"""The protocol half: framing, dispatch, and the line ceiling."""

from __future__ import annotations

import io
import json
import sys
import unittest

from diagram_mcp import server


def converse(*messages):
    """Run the server over a canned conversation and return the replies."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def call(name, arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


HELLO = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "atenea", "version": "1"},
    },
}


class Handshake(unittest.TestCase):
    def test_initialize_answers_the_revision_atenea_speaks(self):
        (reply,) = converse(HELLO)
        self.assertEqual(reply["id"], 1)
        self.assertEqual(reply["result"]["protocolVersion"], server.PROTOCOL_VERSION)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "diagram-design")

    def test_a_notification_is_not_answered(self):
        # Atenea sends notifications/initialized right after initialize and
        # waits for nothing. A reply to it would sit in the reader as an
        # unmatched response while the next real call waits behind it.
        replies = converse(
            HELLO, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["id"], 1)


class Dispatch(unittest.TestCase):
    def test_tools_list_carries_the_three_fields_the_passthrough_decodes(self):
        (reply,) = converse({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = reply["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            self.assertEqual({"name", "description", "inputSchema"}, set(tool))
            self.assertTrue(tool["description"], tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method_is_a_protocol_error(self):
        (reply,) = converse({"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        self.assertEqual(reply["error"]["code"], server.METHOD_NOT_FOUND)

    def test_unparseable_line_does_not_end_the_session(self):
        stdin = io.StringIO("not json\n" + json.dumps(HELLO) + "\n")
        stdout = io.StringIO()
        server.serve(stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], server.PARSE_ERROR)
        self.assertEqual(replies[1]["id"], 1)

    def test_unknown_tool_names_what_is_offered(self):
        (reply,) = converse(call("nope"))
        self.assertEqual(reply["error"]["code"], server.INVALID_PARAMS)
        self.assertIn("doctor", reply["error"]["message"])


class ArgumentChecking(unittest.TestCase):
    def test_missing_required_argument_is_refused(self):
        (reply,) = converse(call("spec"))
        self.assertEqual(reply["error"]["code"], server.INVALID_PARAMS)
        self.assertIn("type is required", reply["error"]["message"])

    def test_misspelled_argument_is_refused_rather_than_ignored(self):
        # The schemas are closed. An argument silently dropped gives the
        # caller default behaviour with no sign the argument did nothing.
        (reply,) = converse(call("spec", {"type": "flowchart", "primitve": True}))
        self.assertEqual(reply["error"]["code"], server.INVALID_PARAMS)
        self.assertIn("primitve", reply["error"]["message"])

    def test_wrong_type_is_refused(self):
        (reply,) = converse(call("spec", {"type": 12}))
        self.assertIn("must be a string", reply["error"]["message"])

    def test_a_boolean_is_not_an_integer(self):
        # True is an int in Python. A schema that took it would accept
        # max_rows=true and pass 1 to the script.
        (reply,) = converse(call("import_drawio", {"path": "/x", "max_rows": True}))
        self.assertIn("must be an integer", reply["error"]["message"])


class Ceiling(unittest.TestCase):
    def test_an_oversized_result_is_refused_not_emitted(self):
        # Atenea reads with a bufio.Scanner capped at 8 MB. A longer line does
        # not truncate, it ends the scan and takes the session down, so the
        # refusal has to happen here.
        original = server.BY_NAME["types"].handler
        server.BY_NAME["types"].handler = lambda args: "x" * (server.MAX_RESULT_BYTES + 1)
        try:
            (reply,) = converse(call("types"))
        finally:
            server.BY_NAME["types"].handler = original
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("ceiling", reply["result"]["content"][0]["text"])

    def test_every_reply_is_one_line(self):
        stdin = io.StringIO(json.dumps(HELLO) + "\n" + json.dumps(call("types")) + "\n")
        stdout = io.StringIO()
        server.serve(stdin, stdout)
        written = stdout.getvalue()
        self.assertEqual(written.count("\n"), 2, "one newline per message and no more")


if __name__ == "__main__":
    unittest.main()


class Stderr(unittest.TestCase):
    """stdout carries protocol; stderr carries diagnostics and nothing else.

    Atenea captures a backend's stderr and folds it into the text of a later
    failure. Anything written there that is not a fact about this machine ends
    up attached to an unrelated error report, and whoever reads it debugs the
    wrong thing. `doctor` is where this bit: asking Playwright where chromium
    lives starts its node driver, and tearing that down makes asyncio complain.

    This runs the real entrypoint as a subprocess, because the whole point is
    what reaches fd 2 -- which an in-process call cannot observe.
    """

    def test_doctor_says_nothing_on_stderr(self):
        import os
        import subprocess
        from pathlib import Path

        entry = Path(__file__).resolve().parents[1] / "bin" / "diagram-design-mcp"
        conversation = "".join(
            json.dumps(m) + "\n"
            for m in (HELLO, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "doctor", "arguments": {}}})
        )
        # The child must run under the interpreter running this test. The
        # shim would otherwise pick the newest python3 on PATH, which is not
        # the venv Playwright lives in -- and the test would skip itself into
        # permanent uselessness on exactly the machine it exists to protect.
        environment = dict(os.environ, DIAGRAM_MCP_PYTHON=sys.executable)
        done = subprocess.run(
            [str(entry)], input=conversation, capture_output=True, text=True,
            timeout=180, check=False, env=environment,
        )
        self.assertEqual(done.returncode, 0)
        report = {}
        for line in done.stdout.splitlines():
            message = json.loads(line)
            if message.get("id") == 9:
                report = json.loads(message["result"]["content"][0]["text"])
        if not report.get("png_export", {}).get("playwright_installed"):
            # The probe that writes the noise never ran, so a pass here would
            # mean nothing. Say so rather than bank a green nobody earned.
            self.skipTest("playwright is not reachable from the interpreter under test")
        self.assertEqual(
            done.stderr.strip(), "",
            "the server wrote to stderr:\n{}".format(done.stderr),
        )
