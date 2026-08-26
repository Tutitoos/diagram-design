"""The `diagrammer` agent: authors a diagram and validates it.

Atenea's agent wire, as worked out in internal/agent/filereader: read one
JSON object on stdin, write one on stdout, and exit 0 on every path this
program controls. The exit status is not the channel -- the verdict is -- and
an agent that signalled failure twice would leave Atenea deciding which to
believe.

What it does between the two is the part that needs a model. The skill's own
routing is a prompt, so this reproduces it in the only honest way: hand the
catalogue to a model, take the type back, hand it that type's full brief, take
HTML back, and put the result through the same self_check the skill uses on
itself. A diagram that fails its checks gets one repair round with the
findings; a diagram that fails twice is reported as incomplete, with the
findings, rather than presented as finished.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import skill

FENCE = re.compile(r"^\s*```(?:html)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
SLUG = re.compile(r"[^a-z0-9]+")

DEFAULT_MODEL_TIMEOUT = 300


class AgentError(Exception):
    """Something the report should carry as a reason, with a failure kind."""

    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind
        self.text = text


def _model_timeout() -> int:
    try:
        return int(os.environ.get("DIAGRAM_MODEL_TIMEOUT", DEFAULT_MODEL_TIMEOUT))
    except ValueError:
        return DEFAULT_MODEL_TIMEOUT


def ask(prompt: str) -> str:
    """One model turn, through whichever CLI the operator named.

    The prompt goes in on stdin and never on argv: a brief carrying a type
    spec and a template is tens of kilobytes, which is the same reason
    Atenea puts its own assignments on stdin.
    """
    binary = os.environ.get("DIAGRAM_MODEL_BIN", "claude")
    resolved = skill.have(binary)
    if resolved is None:
        raise AgentError(
            "unavailable",
            "no model CLI at {!r}: set DIAGRAM_MODEL_BIN to one on PATH".format(binary),
        )
    argv = [resolved, "-p"]
    model = os.environ.get("DIAGRAM_MODEL", "").strip()
    if model:
        argv += ["--model", model]
    try:
        done = subprocess.run(
            argv, input=prompt, capture_output=True, text=True,
            timeout=_model_timeout(), check=False,
        )
    except subprocess.TimeoutExpired:
        raise AgentError("timeout", "{} did not answer within {}s".format(binary, _model_timeout()))
    if done.returncode != 0:
        raise AgentError(
            "unavailable",
            "{} exited {}: {}".format(binary, done.returncode, (done.stderr or done.stdout).strip()[:400]),
        )
    if not done.stdout.strip():
        raise AgentError("unavailable", "{} answered with nothing".format(binary))
    return done.stdout


def unfence(text: str) -> str:
    """Take the HTML out of a fenced block when the model wrapped it."""
    found = FENCE.match(text.strip())
    return found.group(1) if found else text.strip()


def choose_type(objective: str, hints: str) -> str:
    """Pick a diagram type from the catalogue.

    A model call and not keyword matching against the 'Best for' lines: the
    whole reason the skill routes with a prompt is that the mapping from a
    task in prose to a diagram type is a judgement, and a substring match
    would confidently pick 'process' for anything with the word process in it.
    """
    catalogue = "\n".join(
        "- {}: {}".format(entry["id"], entry["best_for"]) for entry in skill.types()
    )
    known = {entry["id"] for entry in skill.types()}
    prompt = (
        "Pick the single best diagram type for this task.\n\n"
        "TASK: {}\n\n{}"
        "CATALOGUE:\n{}\n\n"
        "Answer with the type id alone, nothing else."
    ).format(objective, hints, catalogue)
    answer = ask(prompt).strip().lower()
    # The model may add a sentence around it despite the instruction.
    for candidate in sorted(known, key=len, reverse=True):
        if re.search(r"\b{}\b".format(re.escape(candidate)), answer):
            return candidate
    raise AgentError(
        "invalid_input",
        "the model answered {!r}, which names no type in the catalogue".format(answer[:120]),
    )


def brief(kind: str, objective: str, hints: str, repair: str = "") -> str:
    parts = [
        "Author one self-contained HTML diagram. Output ONLY the HTML file, "
        "with no commentary and no code fence.",
        "TASK: {}".format(objective),
    ]
    if hints:
        parts.append(hints.rstrip())
    if repair:
        parts.append(
            "The previous attempt failed these checks. Fix every one:\n{}".format(repair)
        )
    parts += [
        "Follow the type spec, the style guide and the output contract below exactly.",
        skill.reference("type-{}.md".format(kind)),
        skill.reference("style-guide.md"),
        skill.reference("output-spec.md"),
        "Start from this template:\n{}".format(skill.asset("template.html")),
    ]
    return "\n\n---\n\n".join(parts)


def read_sources(files: List[str], root: str) -> Tuple[str, Optional[str]]:
    """Turn any named draw.io / Mermaid source into a digest for the prompt.

    Redrawing an existing diagram is the case where this agent beats writing
    one from scratch, and the extractors already do the reading. Anything else
    named in the task is left alone: the model is told about it, not handed it.
    """
    hints: List[str] = []
    suggested: Optional[str] = None
    for name in files:
        path = Path(name)
        if not path.is_absolute() and root:
            path = Path(root) / path
        if not path.is_file():
            continue
        suffix = "".join(path.suffixes).lower()
        script = None
        if suffix.endswith((".drawio", ".drawio.png", ".drawio.svg", ".xml")):
            script = "drawio_extract.py"
        elif suffix.endswith((".mmd", ".mermaid")):
            script = "mermaid_extract.py"
        if script is None:
            continue
        code, out, _ = skill.run_script(script, [str(path)])
        if code != 0:
            continue
        hints.append("SOURCE ({}), already parsed:\n{}".format(path.name, out))
        found = re.search(r"type candidates: ([a-z0-9, -]+)", out)
        if found and suggested is None:
            suggested = found.group(1)
    text = "\n\n".join(hints)
    if suggested:
        text += "\n\nThe extractor suggests these types: {}\n".format(suggested)
    return (text + "\n" if text else ""), suggested


def target_path(objective: str, root: str) -> Path:
    """Where the diagram goes: the repository root Atenea named, and nowhere else.

    An empty root is refused rather than defaulted to the working directory.
    A child spawned by the daemon inherits the daemon's cwd, which is nobody's
    project, so that fallback does not write "here" -- it writes somewhere the
    caller never chose and will not think to look. Saying so is cheap; the
    alternative is a file loose in an unrelated checkout.
    """
    if not str(root).strip():
        raise AgentError(
            "invalid_input",
            "the assignment named no repository root, so there is nowhere to "
            "put the diagram. Pass --repository, or run from a registered "
            "repository.",
        )
    slug = SLUG.sub("-", objective.lower()).strip("-")[:48] or "diagram"
    return Path(root) / "{}.html".format(slug)


def check(path: Path) -> List[str]:
    code, out, _ = skill.run_script("self_check.py", [str(path)])
    if code == 0:
        return []
    return [line.strip().lstrip("- ").strip() for line in out.splitlines() if line.startswith("  -")]


def answer(assignment: Dict[str, Any]) -> Dict[str, Any]:
    task = assignment.get("task") or {}
    objective = str(task.get("objective", "")).strip()
    if not objective:
        return {
            "verdict": "failed",
            "reason": {"kind": "invalid_input", "text": "the task named no objective"},
        }
    root = ""
    repository = (assignment.get("context") or {}).get("repository")
    if isinstance(repository, dict):
        root = str(repository.get("root", "") or "")

    try:
        skill.require_skill()
        hints, _ = read_sources(list(task.get("files") or []), root)
        kind = choose_type(objective, hints)
        html = unfence(ask(brief(kind, objective, hints)))
        path = target_path(objective, root)
        path.write_text(html, encoding="utf-8")

        findings = check(path)
        if findings:
            # One repair round. A second would be a loop with a model in it
            # and no new information between turns.
            html = unfence(ask(brief(kind, objective, hints, repair="\n".join("- " + f for f in findings))))
            path.write_text(html, encoding="utf-8")
            findings = check(path)
    except AgentError as err:
        return {"verdict": "failed", "reason": {"kind": err.kind, "text": err.text}}
    except skill.SkillError as err:
        return {"verdict": "failed", "reason": {"kind": "unavailable", "text": str(err)}}
    except OSError as err:
        return {"verdict": "failed", "reason": {"kind": "permission_denied", "text": str(err)}}

    result = {
        "path": str(path),
        "type": kind,
        "checks": "ok" if not findings else "; ".join(findings),
        "bytes": path.stat().st_size,
    }
    if findings:
        # The file exists and is worth looking at, but it does not meet the
        # skill's own contract. Reporting that as ok would be the one lie this
        # agent is in a position to tell.
        return {
            "verdict": "incomplete",
            "result": result,
            "reason": {
                "kind": "invalid_input",
                "text": "the diagram still fails {} check(s) after one repair round".format(len(findings)),
            },
        }
    return {"verdict": "ok", "result": result}


def main(stdin: Any = None, stdout: Any = None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    raw = stdin.read()
    try:
        assignment = json.loads(raw) if raw.strip() else {}
    except ValueError as err:
        report = {
            "verdict": "failed",
            "reason": {"kind": "invalid_input", "text": "the assignment is not readable: {}".format(err)},
        }
    else:
        report = answer(assignment if isinstance(assignment, dict) else {})
    stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    stdout.flush()
    return 0
