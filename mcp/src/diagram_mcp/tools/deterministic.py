"""The upstream Python scripts, wrapped as tools.

Nothing here parses draw.io, Mermaid or HTML. The three scripts in the skill
already do, they are the authority on their own formats, and reimplementing
any of them here would be a second parser to keep in step with the first. This
module resolves paths, runs the script, and turns its exit code and output
into a result a client can act on.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .. import skill
from .registry import Tool, obj


def _resolve(raw: str, label: str) -> Path:
    """Turn a client-supplied path into an absolute one that exists.

    A relative path is resolved against the caller's declared `cwd` when there
    is one and against this process's cwd otherwise. The server is spawned by
    Atenea and inherits Atenea's working directory, which is nobody's project,
    so a bare relative path is far more likely to be a mistake than a hit --
    hence the failure names what it tried.
    """
    text = str(raw or "").strip()
    if not text:
        raise skill.SkillError("{} is required".format(label))
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        raise skill.SkillError("no file at {} (resolved from {!r})".format(path, text))
    return path


def _ir(script: str, path: Path, args: Dict[str, Any], selector: str) -> str:
    """Run one of the two extractors and hand back its intermediate representation.

    `as_json` picks between the two output modes the scripts already have: the
    Markdown digest is meant to be read into context and is what an agent
    usually wants; the full JSON IR is for a caller that is going to compute
    over it. Defaulting to the digest keeps a client's context from filling
    with every node's style attributes.
    """
    argv: List[str] = [str(path)]
    if args.get("as_json"):
        argv.append("--json")
    chosen = str(args.get(selector, "")).strip()
    if chosen:
        argv += ["--" + selector, chosen]
    rows = args.get("max_rows")
    if rows is not None:
        argv += ["--max-rows", str(int(rows))]
    code, out, err = skill.run_script(script, argv)
    if code != 0:
        raise skill.SkillError(
            "{} could not read {}: {}".format(script, path.name, (err or out).strip() or "exit {}".format(code))
        )
    return out


def _import_drawio(args: Dict[str, Any]) -> str:
    return _ir("drawio_extract.py", _resolve(args.get("path", ""), "path"), args, "page")


def _import_mermaid(args: Dict[str, Any]) -> str:
    """Extract from a Mermaid file, or from Mermaid text handed in directly.

    The upstream script takes a file and only a file. Text is therefore staged
    through a private temporary file that is removed on the way out: that is
    an implementation detail of reading, not a write the caller asked for, so
    the tool still declares only read and process.
    """
    text = str(args.get("text", "") or "")
    if not text.strip():
        return _ir("mermaid_extract.py", _resolve(args.get("path", ""), "path or text"), args, "diagram")
    handle, staged = tempfile.mkstemp(suffix=".mmd", prefix="diagram-mcp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        out = _ir("mermaid_extract.py", Path(staged), args, "diagram")
        # The script reports the file it read, which here is a name the caller
        # never chose and cannot look at. Saying where the source actually came
        # from is more use than leaking a path that is already unlinked.
        return out.replace(Path(staged).name, "(inline text)")
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass


def _validate(args: Dict[str, Any]) -> str:
    """Check a generated diagram against the skill's own contract.

    self_check.py exits 1 on a failed file and prints the reasons, so the exit
    code is a verdict rather than an error: a diagram that fails its checks is
    a successful call that returns "no". Turning it into a tool error would
    hide the reasons behind a failure banner.
    """
    path = _resolve(args.get("path", ""), "path")
    code, out, err = skill.run_script("self_check.py", [str(path)])
    findings = [
        line.strip().lstrip("- ").strip()
        for line in out.splitlines()
        if line.startswith("  -")
    ]
    return json.dumps(
        {
            "path": str(path),
            "ok": code == 0,
            "findings": findings,
            "raw": (out + err).strip(),
        },
        indent=2,
        ensure_ascii=False,
    )


_MAX_ROWS = {
    "type": "integer",
    "minimum": 1,
    "description": "Cap on rows in the digest, passed straight to the script.",
}
_AS_JSON = {
    "type": "boolean",
    "description": "Return the full JSON IR instead of the Markdown digest. Off by default: the digest is what an agent reads.",
}

TOOLS = [
    Tool(
        "import_drawio",
        "Read a .drawio / .xml / .drawio.png / .drawio.svg file and return its "
        "normalized structure: nodes, edges, containers, depth, cycles, and the "
        "diagram types the shape of it suggests. The first step in redrawing a "
        "draw.io source in this design system.",
        obj(
            {
                "path": {"type": "string", "description": "Absolute path to the draw.io file."},
                "page": {"type": "string", "description": "Page index or name, when the file has several."},
                "as_json": _AS_JSON,
                "max_rows": _MAX_ROWS,
            },
            ["path"],
        ),
        _import_drawio,
    ),
    Tool(
        "import_mermaid",
        "Read Mermaid source -- from a .mmd/.mermaid/Markdown file or handed in "
        "as text -- and return its normalized structure. Supports flowchart/"
        "graph, sequenceDiagram, stateDiagram-v2 and erDiagram. The first step "
        "in redrawing a Mermaid block in this design system.",
        obj(
            {
                "path": {"type": "string", "description": "Absolute path to the Mermaid or Markdown file."},
                "text": {"type": "string", "description": "Mermaid source directly. Takes precedence over path."},
                "diagram": {"type": "string", "description": "Which diagram to take when the source holds several: an index, or 'all'."},
                "as_json": _AS_JSON,
                "max_rows": _MAX_ROWS,
            },
            [],
        ),
        _import_mermaid,
    ),
    Tool(
        "validate",
        "Check a generated diagram HTML against the skill's own contract: the "
        "accessible-SVG rules, the single-file safety rules (no remote assets, "
        "no executable attributes, no stray scripts) and the motion contract "
        "when motion markup is present. Returns a verdict and the reasons; a "
        "failing diagram is a successful call that answers no.",
        obj({"path": {"type": "string", "description": "Absolute path to the diagram HTML."}}, ["path"]),
        _validate,
    ),
]
