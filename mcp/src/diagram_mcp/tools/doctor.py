"""What this installation can and cannot do, and why.

`doctor` exists because the two things most likely to be wrong here are
invisible from a client: the server is running outside a plugin tree, or
Playwright is absent so PNG export cannot work. Both fail later with errors that read as
bugs in the tool. Reporting them up front, with the command that fixes each,
turns a confusing failure into a task.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

from .. import skill
from .registry import NO_ARGS, Tool


def _doctor(_: Dict[str, Any]) -> str:
    report: Dict[str, Any] = {
        "python": {
            "version": "{}.{}.{}".format(*sys.version_info[:3]),
            "executable": sys.executable,
        },
        "skill": {"root": str(skill.plugin_root())},
    }
    try:
        directory = skill.require_skill()
    except skill.SkillError as err:
        report["skill"]["ready"] = False
        report["skill"]["hint"] = str(err)
        report["ok"] = False
        return json.dumps(report, indent=2)

    report["skill"].update(
        {
            "ready": True,
            "directory": str(directory),
            "version": skill.version(),
            "types": len(skill.types()),
            "templates": skill.templates(),
        }
    )
    scripts: Dict[str, bool] = {}
    for name in ("drawio_extract.py", "mermaid_extract.py", "self_check.py"):
        scripts[name] = (skill.scripts_dir() / name).is_file()
    report["skill"]["scripts"] = scripts

    playwright = skill.playwright_state()
    report["png_export"] = {
        "available": bool(playwright["chromium"]),
        "playwright_installed": bool(playwright["module"]),
        "hint": playwright["hint"],
    }

    profiles = skill.profiles_dir()
    report["profiles"] = {"directory": str(profiles), "exists": profiles.is_dir()}

    # `ok` is about the deterministic core only. PNG export is an extra: a
    # machine without Chromium is a healthy installation missing one optional
    # tool, not a broken one, and reporting it as broken would send somebody
    # to fix what is not wrong.
    report["ok"] = all(scripts.values())
    return json.dumps(report, indent=2, ensure_ascii=False)


TOOLS = [
    Tool(
        "doctor",
        "Report what this installation can do: the Python running it, whether "
        "the skill is present and at what version, which scripts "
        "are present, whether PNG export is possible, and where brand profiles "
        "live. Each failure carries the command that fixes it.",
        NO_ARGS,
        _doctor,
    ),
]
