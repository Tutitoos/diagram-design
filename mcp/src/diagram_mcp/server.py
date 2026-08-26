"""MCP over stdio, spoken the way Atenea's passthrough speaks it.

Three methods and one notification are the whole conversation Atenea has with
a backend: `initialize`, `notifications/initialized`, `tools/list` and
`tools/call` (see internal/passthrough/stdio.go). Nothing else is implemented,
because a method nothing calls is a method nothing tests.

The framing is newline-delimited JSON, one message per line, and the reader on
the other side is a bufio.Scanner capped at 8 MB per line. A response above
that ceiling does not truncate -- it kills the scan and takes the session with
it. So the ceiling is enforced here, where it can still be turned into an
answer the caller can read.

stdout carries protocol and nothing else. Every diagnostic goes to stderr,
which Atenea already captures and folds into its failure text.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import skill
from .tools import BY_NAME, TOOLS

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "diagram-design"

# There are two line ceilings on the way to a client and the tighter one is
# what governs. Atenea's passthrough reads this server with a bufio.Scanner
# capped at 8 MiB (internal/passthrough/stdio.go), but the bridge that relays
# to the client caps at 1 MiB (cmd/atenea/mcp.go). Neither truncates: an
# oversized line ends the scan and takes the session with it. So the refusal
# fires well under the 1 MiB figure, with room left for the JSON-RPC envelope
# and for the escaping that string encoding adds on each of the two hops.
MAX_RESULT_BYTES = 768 << 10

# JSON-RPC codes. Only the ones this server can actually produce.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(message: str) -> None:
    print("[{}] {}".format(SERVER_NAME, message), file=sys.stderr, flush=True)


def _check_arguments(tool: Any, args: Dict[str, Any]) -> Optional[str]:
    """Check arguments against the tool's own schema.

    Deliberately small: required keys, unknown keys, and the handful of types
    the schemas actually use. A full JSON Schema validator would be a
    dependency, and this server has none -- the schemas here are ours, they
    are simple by construction, and the checks below are the ones that catch
    real caller mistakes.
    """
    schema = tool.schema
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args or args[name] in (None, ""):
            return "{} is required".format(name)
    for name, value in args.items():
        if name not in properties:
            return "unknown argument {!r}; this tool takes {}".format(
                name, ", ".join(sorted(properties)) or "no arguments"
            )
        expected = properties[name].get("type")
        if value is None:
            continue
        if expected == "string" and not isinstance(value, str):
            return "{} must be a string".format(name)
        if expected == "boolean" and not isinstance(value, bool):
            return "{} must be a boolean".format(name)
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return "{} must be an integer".format(name)
        if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            return "{} must be a number".format(name)
    return None


def _text_result(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(method: str, params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Answer one request. Returns (result, error); exactly one is not None."""
    if method == "initialize":
        return (
            {
                # Echo the revision the client asked for when we speak it, so a
                # client pinned to this revision is not told a different one.
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": skill.version()},
            },
            None,
        )

    if method == "tools/list":
        return {"tools": [tool.wire() for tool in TOOLS]}, None

    if method == "tools/call":
        name = str(params.get("name", ""))
        tool = BY_NAME.get(name)
        if tool is None:
            return None, {
                "code": INVALID_PARAMS,
                "message": "no tool {!r}; this server offers {}".format(
                    name, ", ".join(sorted(BY_NAME))
                ),
            }
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return None, {"code": INVALID_PARAMS, "message": "arguments must be an object"}
        complaint = _check_arguments(tool, args)
        if complaint is not None:
            return None, {"code": INVALID_PARAMS, "message": "{}: {}".format(name, complaint)}
        try:
            text = tool.handler(args)
        except skill.SkillError as err:
            # A tool that could not do the job is a successful call that
            # answers no. Reported in the result so the model reads the reason
            # and can correct, rather than as a protocol error that only says
            # the call failed.
            return _text_result(str(err), is_error=True), None
        except Exception as err:  # noqa: BLE001 - the far side is scripts and a browser
            log("{} raised {}: {}".format(name, type(err).__name__, err))
            return _text_result("{}: {}".format(type(err).__name__, err), is_error=True), None
        if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
            return (
                _text_result(
                    "{} produced {} bytes, above the {} byte ceiling one MCP response may "
                    "carry. Narrow the request -- for the spec tool, leave `primitives` "
                    "off; for an export, write the file instead of returning it.".format(
                        name, len(text.encode("utf-8")), MAX_RESULT_BYTES
                    ),
                    is_error=True,
                ),
                None,
            )
        return _text_result(text), None

    return None, {"code": METHOD_NOT_FOUND, "message": "unsupported method {!r}".format(method)}


def serve(stdin: Any = None, stdout: Any = None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    def emit(message: Dict[str, Any]) -> None:
        stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        stdout.flush()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as err:
            emit({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": str(err)}})
            continue
        if not isinstance(request, dict):
            emit({"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "not an object"}})
            continue

        identifier = request.get("id")
        method = str(request.get("method", ""))
        params = request.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # A notification carries no id and takes no answer. Replying to one is
        # an unmatched response the client has to discard, and Atenea's reader
        # would be holding it while waiting for the next real reply.
        if identifier is None:
            if method not in ("notifications/initialized", "notifications/cancelled"):
                log("ignoring notification {!r}".format(method))
            continue

        try:
            result, error = handle(method, params)
        except Exception as err:  # noqa: BLE001 - last resort; the loop must not die
            log("{} raised {}: {}".format(method, type(err).__name__, err))
            result, error = None, {"code": INTERNAL_ERROR, "message": str(err)}

        if error is not None:
            emit({"jsonrpc": "2.0", "id": identifier, "error": error})
        else:
            emit({"jsonrpc": "2.0", "id": identifier, "result": result})
    return 0


def main() -> int:
    try:
        return serve()
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The client went away mid-write. That is a closed session, not a
        # fault to report.
        return 0
