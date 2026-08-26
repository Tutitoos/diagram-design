"""Locates and reads the diagram-design skill this server ships inside.

Everything this server knows about diagrams comes from the plugin tree this
package sits inside, never from constants written here. The upstream skill
gains and loses diagram types between releases; a catalogue transcribed into
Python would be a second copy that drifts silently and nothing would notice.
So every list in this module is derived from what is actually on disk.

This repository is a fork of the upstream plugin with the server added under
`mcp/`, so the plugin root is this file's fourth parent: skill.py -> diagram_mcp
-> src -> mcp -> the plugin itself.

Python 3.9 is the floor: `/usr/bin/python3` on macOS is 3.9.6 and it is the
interpreter a bare `python3` resolves to on this machine. Nothing here needs a
newer one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# mcp/src/diagram_mcp/skill.py -> diagram_mcp -> src -> mcp -> the plugin root.
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# One line of a type reference: `**Best for:** decision logic, ...`.
_BEST_FOR = re.compile(r"^\*\*Best for:\*\*\s*(.+?)\s*$", re.MULTILINE)
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# A script may not run forever. The three upstream scripts parse bounded text
# and finish in well under a second on real input; a minute is generous enough
# that a slow machine is not a failure and short enough that a wedged child
# does not hold a chat open.
SCRIPT_TIMEOUT = 60


class SkillError(Exception):
    """The plugin tree is missing or does not look like a skill."""


def plugin_root() -> Path:
    """The plugin tree: the root of this fork.

    `DIAGRAM_SKILL_ROOT` overrides it so a developer can point at another
    checkout without moving this one, and so a test can point at a fixture.
    """
    override = os.environ.get("DIAGRAM_SKILL_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _PLUGIN_ROOT


def skill_dir() -> Path:
    """The skill itself: where SKILL.md, references/, assets/ and scripts/ live."""
    return plugin_root() / "skills" / "diagram-design"


def require_skill() -> Path:
    """Resolve the skill directory or explain what is missing.

    A tree with no `skills/` in it is the ordinary failure — the server was
    copied somewhere out of the plugin, or DIAGRAM_SKILL_ROOT points at the
    wrong place. Saying so is worth more than a FileNotFoundError on a path
    the reader has to interpret.
    """
    root = plugin_root()
    if not root.is_dir():
        raise SkillError("no plugin tree at {}".format(root))
    skill = skill_dir()
    if not (skill / "SKILL.md").is_file():
        raise SkillError(
            "{} holds no skills/diagram-design/SKILL.md, so it is not a "
            "diagram-design checkout. Set DIAGRAM_SKILL_ROOT to one, or run "
            "this server from inside the plugin.".format(root)
        )
    return skill


def version() -> str:
    """The plugin version, as the checkout declares it.

    Reported by `doctor` so a tree that has drifted from upstream is visible
    from the client rather than inferred from a diagram that came out wrong.
    """
    manifest = plugin_root() / ".claude-plugin" / "plugin.json"
    try:
        with manifest.open("r", encoding="utf-8") as handle:
            return str(json.load(handle).get("version", "")) or "unknown"
    except (OSError, ValueError):
        return "unknown"


def references_dir() -> Path:
    return require_skill() / "references"


def assets_dir() -> Path:
    return require_skill() / "assets"


def scripts_dir() -> Path:
    return require_skill() / "scripts"


def reference(name: str) -> str:
    """Read one file out of references/, refusing anything that leaves it.

    The name arrives from a client. Resolving it and checking the parent is
    what keeps `../../../etc/passwd` from being a readable reference.
    """
    base = references_dir()
    target = (base / name).resolve()
    if base.resolve() not in target.parents:
        raise SkillError("{!r} is not a reference in {}".format(name, base))
    if not target.is_file():
        raise SkillError("no reference {!r} in {}".format(name, base))
    return target.read_text(encoding="utf-8")


def types() -> List[Dict[str, str]]:
    """The diagram catalogue, derived from `references/type-*.md`.

    Each entry carries the id a caller passes to `spec`, the upstream title,
    and the "Best for" line -- which is the sentence that actually decides
    which type a task wants.
    """
    out: List[Dict[str, str]] = []
    for path in sorted(references_dir().glob("type-*.md")):
        text = path.read_text(encoding="utf-8")
        heading = _HEADING.search(text)
        best = _BEST_FOR.search(text)
        out.append(
            {
                "id": path.stem[len("type-") :],
                "title": heading.group(1) if heading else path.stem,
                "best_for": best.group(1) if best else "",
            }
        )
    return out


def templates() -> List[str]:
    """The template names in assets/, derived from disk.

    Upstream ships five today (plain, dark, full, motion, terminal) and has
    shipped fewer; globbing is what keeps this honest across an upstream sync.
    """
    return sorted(p.name for p in assets_dir().glob("template*.html"))


def asset(name: str) -> str:
    """Read one file out of assets/, with the same containment check as references."""
    base = assets_dir()
    target = (base / name).resolve()
    if base.resolve() not in target.parents:
        raise SkillError("{!r} is not an asset in {}".format(name, base))
    if not target.is_file():
        raise SkillError("no asset {!r} in {}".format(name, base))
    return target.read_text(encoding="utf-8")


def profiles_dir() -> Path:
    """Where the upstream skill keeps named brand profiles."""
    return Path(os.environ.get("DIAGRAM_PROFILES_DIR", "~/.diagram-design/profiles")).expanduser()


def interpreter() -> str:
    """The interpreter the upstream scripts are run with.

    `sys.executable` and not a bare `python3`: this process already resolved a
    working interpreter, and re-resolving through PATH in the child is how a
    server that started fine ends up running its scripts under a different
    Python than the one that imported this module.
    """
    return sys.executable or "python3"


def run_script(name: str, args: List[str]) -> Tuple[int, str, str]:
    """Run one upstream script by absolute path and hand back what it said.

    The path is built from the skill directory rather than from the caller's
    cwd, because a client's working directory is not ours and a relative
    script path would resolve somewhere nobody chose.
    """
    script = scripts_dir() / name
    if not script.is_file():
        raise SkillError("the skill has no script {!r} at {}".format(name, script))
    try:
        done = subprocess.run(
            [interpreter(), str(script)] + args,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SkillError(
            "{} did not finish within {}s".format(name, SCRIPT_TIMEOUT)
        )
    return done.returncode, done.stdout, done.stderr


def have(binary: str) -> Optional[str]:
    """Absolute path of a binary on PATH, or None. Used only by doctor."""
    return shutil.which(binary)


# Asking Playwright where chromium is means starting its node driver, and
# tearing that driver down again immediately makes asyncio complain on stderr
# -- "Task was destroyed but it is pending", and a TargetClosedError nobody
# retrieved. None of it is a fact about this machine.
#
# It matters because of where that stderr goes. Atenea captures a backend's
# stderr and folds it into the text of a later failure, so a genuine error
# would arrive with this chatter attached to it, and whoever read the report
# would be debugging a teardown race that never happened. Running the probe in
# a child contains the noise by construction: the child's stderr is captured
# here and only consulted when the probe actually failed.
_CHROMIUM_PROBE = (
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as driver:\n"
    "    print(driver.chromium.executable_path)\n"
)


def playwright_state() -> Dict[str, Any]:
    """Whether PNG export can work here, and if not, what is missing.

    Import and browser availability are two separate failures with two
    different fixes, so they are reported separately rather than collapsed
    into one boolean.
    """
    state: Dict[str, Any] = {"module": False, "chromium": False, "hint": ""}
    try:
        import playwright  # noqa: F401
    except ImportError:
        state["hint"] = (
            "playwright is not installed for {}: create a virtualenv, "
            "`pip install playwright && playwright install chromium` into it, "
            "and point DIAGRAM_MCP_PYTHON at its interpreter".format(interpreter())
        )
        return state
    state["module"] = True
    try:
        done = subprocess.run(
            [interpreter(), "-c", _CHROMIUM_PROBE],
            capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        state["hint"] = "the playwright driver did not answer within {}s".format(SCRIPT_TIMEOUT)
        return state
    reported = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    if done.returncode != 0 or not reported:
        state["hint"] = "playwright is installed but chromium is not usable: {}".format(
            done.stderr.strip()[:300] or "the probe exited {}".format(done.returncode)
        )
        return state
    state["chromium"] = Path(reported).exists()
    if not state["chromium"]:
        state["hint"] = (
            "playwright names chromium at {} but nothing is there: "
            "playwright install chromium".format(reported)
        )
    return state
