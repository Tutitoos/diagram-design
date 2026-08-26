"""The design knowledge the skill carries, served as tools.

This is the half of the wrapper that has no equivalent upstream. In Claude
Code the agent reads SKILL.md and the reference files off disk because the
plugin put them there; every other client has no such directory. Serving the
catalogue, the type spec and the templates as tools is what lets omp, Codex or
OpenCode author a diagram in the same house style.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .. import skill
from .registry import NO_ARGS, Tool, obj


def _types(_: Dict[str, Any]) -> str:
    return json.dumps(
        {"count": len(skill.types()), "types": skill.types()},
        indent=2,
        ensure_ascii=False,
    )


def _spec(args: Dict[str, Any]) -> str:
    """The full brief for one diagram type.

    A type spec alone is not enough to draw with: it names shapes and layout
    but takes the palette, the type scale and the file contract from two other
    files. Returning the three together is what makes one call sufficient --
    the alternative is three round trips and an agent that forgets the third.
    """
    wanted = str(args.get("type", "")).strip()
    known = {entry["id"] for entry in skill.types()}
    if wanted not in known:
        raise skill.SkillError(
            "no diagram type {!r}. Call `types` for the catalogue; it holds {}.".format(
                wanted, ", ".join(sorted(known))
            )
        )
    parts: List[str] = [
        "<!-- type-{}.md -->\n{}".format(wanted, skill.reference("type-{}.md".format(wanted))),
        "<!-- style-guide.md -->\n{}".format(skill.reference("style-guide.md")),
        "<!-- output-spec.md -->\n{}".format(skill.reference("output-spec.md")),
    ]
    # Opt-in, not default. primitive-icons.md alone is 106 KB of inlined SVG
    # paths; folding it into every spec call would spend a client's context on
    # an icon set most diagrams never reference.
    if args.get("primitives"):
        for name in ("primitive-annotation.md", "primitive-sketchy.md", "primitive-terminal.md", "primitive-icons.md"):
            parts.append("<!-- {} -->\n{}".format(name, skill.reference(name)))
    if args.get("animation"):
        parts.append("<!-- animation.md -->\n{}".format(skill.reference("animation.md")))
    if args.get("semantic_patterns"):
        parts.append("<!-- semantic-patterns.md -->\n{}".format(skill.reference("semantic-patterns.md")))
    return "\n\n".join(parts)


def _template(args: Dict[str, Any]) -> str:
    available = skill.templates()
    name = str(args.get("name", "template.html")).strip()
    if not name.endswith(".html"):
        name += ".html"
    if name not in available:
        raise skill.SkillError(
            "no template {!r}; this checkout ships {}".format(name, ", ".join(available))
        )
    return skill.asset(name)


def _templates(_: Dict[str, Any]) -> str:
    return json.dumps({"templates": skill.templates()}, indent=2)


def _profiles(args: Dict[str, Any]) -> str:
    """List the operator's saved brand profiles, or read one.

    Read-only by design. Creating a profile means fetching a website and
    writing tokens to disk -- `external` and `write` -- which is a different
    permission conversation and stays with the upstream `/profile` command.
    """
    directory = skill.profiles_dir()
    name = str(args.get("name", "")).strip()
    if not name:
        if not directory.is_dir():
            return json.dumps(
                {"directory": str(directory), "exists": False, "profiles": []}, indent=2
            )
        found = sorted(p.stem for p in directory.glob("*.md"))
        return json.dumps(
            {"directory": str(directory), "exists": True, "profiles": found}, indent=2
        )
    target = (directory / (name + ".md")).resolve()
    if directory.resolve() not in target.parents:
        raise skill.SkillError("{!r} is not a profile in {}".format(name, directory))
    if not target.is_file():
        raise skill.SkillError("no profile {!r} in {}".format(name, directory))
    return target.read_text(encoding="utf-8")


TOOLS = [
    Tool(
        "types",
        "The catalogue of diagram types this checkout ships. Each entry carries "
        "the id `spec` takes, the upstream title, and the 'Best for' line that "
        "says when to reach for it. Start here when the diagram type is not "
        "already decided.",
        NO_ARGS,
        _types,
    ),
    Tool(
        "spec",
        "Everything needed to draw one diagram type: its layout spec, the style "
        "guide (palette, type scale, tokens) and the single-file output "
        "contract. Optionally the primitives, the motion guide and the semantic "
        "patterns. This is the brief an agent authors the HTML from.",
        obj(
            {
                "type": {
                    "type": "string",
                    "description": "A type id from `types`, e.g. flowchart, architecture, sankey.",
                },
                "primitives": {
                    "type": "boolean",
                    "description": "Include annotation, sketchy, terminal and icon primitives. Large (~120 KB); off by default.",
                },
                "animation": {
                    "type": "boolean",
                    "description": "Include the accessible-motion guide.",
                },
                "semantic_patterns": {
                    "type": "boolean",
                    "description": "Include the semantic pattern catalogue.",
                },
            },
            ["type"],
        ),
        _spec,
    ),
    Tool(
        "templates",
        "The starting HTML templates this checkout ships, by name.",
        NO_ARGS,
        _templates,
    ),
    Tool(
        "template",
        "One starting HTML template, whole. Use `templates` for the names.",
        obj(
            {
                "name": {
                    "type": "string",
                    "description": "Template file name; the .html suffix is optional. Defaults to template.html.",
                }
            },
            [],
        ),
        _template,
    ),
    Tool(
        "profiles",
        "The operator's saved brand profiles. With no argument, lists them; "
        "with a name, returns that profile's tokens. Read-only.",
        obj(
            {"name": {"type": "string", "description": "Profile slug to read. Omit to list."}},
            [],
        ),
        _profiles,
    ),
]
