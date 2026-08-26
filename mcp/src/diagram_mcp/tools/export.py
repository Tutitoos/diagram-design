"""Export a generated diagram to SVG or PNG.

The procedure is upstream's, in `references/export.md`, and it is followed
rather than reinvented: diagram only (just the `<svg>` node, editorial
wrappers dropped), fonts injected as an XML-safe `@import`, PNG rasterized
from the ORIGINAL HTML so font loading stays reliable, transparent background.

One deliberate departure. Upstream writes both files next to the source;
here `export_svg` RETURNS the SVG and only `export_png` writes. Two reasons,
and they point the same way: a returned PNG would have to be base64 and the
passthrough caps one response at 8 MB, and writing is a declared effect that
Atenea gates separately -- so keeping SVG read-only leaves the common case
working on the permissions a chat already holds.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .. import skill
from .deterministic import _resolve
from .registry import Tool, obj

_SVG = re.compile(r"<svg\b.*?</svg>", re.DOTALL | re.IGNORECASE)
_VIEWBOX = re.compile(r"\bviewBox\s*=", re.IGNORECASE)
_XMLNS = re.compile(r"\bxmlns\s*=", re.IGNORECASE)
_OPEN_TAG = re.compile(r"<svg\b", re.IGNORECASE)
_DEFS_OPEN = re.compile(r"<defs\b[^>]*>", re.IGNORECASE)
_FONT_LINK = re.compile(r"<link[^>]+href=[\"']([^\"']*fonts\.googleapis\.com[^\"']*)[\"']", re.IGNORECASE)

# The font set the current templates carry, used only when the source HTML
# names none. Kept as a fallback rather than the primary path: reading the
# link out of the file is what keeps a font added upstream from silently
# not reaching the export.
_FALLBACK_FONTS = (
    "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
    "&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap"
)


def _font_url(source_html: str) -> str:
    found = _FONT_LINK.search(source_html)
    raw = htmllib.unescape(found.group(1)) if found else _FALLBACK_FONTS
    # A standalone .svg is parsed as strict XML, where a bare `&` opens an
    # entity reference and the whole file fails to parse. This is the one
    # escape the whole procedure turns on.
    return raw.replace("&", "&amp;")


def _standalone_svg(source_html: str, label: str) -> Dict[str, Any]:
    blocks = _SVG.findall(source_html)
    if not blocks:
        raise skill.SkillError(
            "{} holds no <svg> block, so it is not a diagram file; nothing was written".format(label)
        )
    svg = blocks[0]
    warnings = []
    if len(blocks) > 1:
        warnings.append(
            "the source holds {} <svg> blocks; the first was taken, which is the "
            "diagram in every file except the gallery".format(len(blocks))
        )
    if not _XMLNS.search(svg.split(">", 1)[0]):
        svg = _OPEN_TAG.sub('<svg xmlns="http://www.w3.org/2000/svg"', svg, count=1)
    if not _VIEWBOX.search(svg.split(">", 1)[0]):
        # Guessing one would silently change the drawing's proportions, so it
        # is reported rather than invented.
        warnings.append("the <svg> has no viewBox: it will not scale predictably in an importer")

    style = "<style>@import url('{}');</style>".format(_font_url(source_html))
    opened = _DEFS_OPEN.search(svg)
    if opened:
        # Merge, never add a second <defs>.
        at = opened.end()
        svg = svg[:at] + style + svg[at:]
    else:
        at = svg.index(">") + 1
        svg = svg[:at] + "<defs>" + style + "</defs>" + svg[at:]
    return {"svg": '<?xml version="1.0" encoding="UTF-8"?>\n' + svg, "warnings": warnings}


def _export_svg(args: Dict[str, Any]) -> str:
    path = _resolve(args.get("path", ""), "path")
    built = _standalone_svg(path.read_text(encoding="utf-8"), path.name)
    header = ""
    if built["warnings"]:
        header = "".join("<!-- warning: {} -->\n".format(w) for w in built["warnings"])
    return header + built["svg"]


def _png_target(path: Path, out: Optional[str]) -> Path:
    if out and str(out).strip():
        target = Path(str(out).strip()).expanduser()
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        return target
    return path.with_suffix(".png")


def _export_png(args: Dict[str, Any]) -> str:
    """Rasterize the original HTML and screenshot the diagram's bounding box."""
    path = _resolve(args.get("path", ""), "path")
    if path.name == "index.html":
        raise skill.SkillError(
            "{} is the gallery: it holds many diagrams and there is no way to tell "
            "which one was meant. Name the diagram file instead.".format(path)
        )
    scale = float(args.get("scale", 2))
    if scale < 1 or scale > 4:
        raise skill.SkillError(
            "scale {} is outside 1..4: below 1 soft-focuses the type and above 4 "
            "upscales a layout drawn for a smaller canvas. Redraw at a different "
            "preset instead.".format(scale)
        )
    state = skill.playwright_state()
    if not state["chromium"]:
        raise skill.SkillError(
            state["hint"] or "chromium is not available for PNG export"
        )

    source = path.read_text(encoding="utf-8")
    if not _SVG.search(source):
        raise skill.SkillError(
            "{} holds no <svg> block, so it is not a diagram file; nothing was written".format(path.name)
        )
    motion = "data-motion-root" in source
    target = _png_target(path, args.get("out"))

    from playwright.sync_api import sync_playwright

    url = path.resolve().as_uri()
    if motion:
        # Never capture at an arbitrary wall-clock delay: the static frame is
        # a state the page can be asked for and then asserted.
        url += "?motion=static"
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page(device_scale_factor=scale)
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.evaluate("() => document.fonts.ready")
            if motion:
                frame = page.evaluate(
                    "() => { const r = document.querySelector('[data-motion-root]');"
                    " return r ? r.getAttribute('data-frame') : null; }"
                )
                if frame != "static":
                    raise skill.SkillError(
                        "the motion root reported data-frame={!r} instead of 'static'; "
                        "capturing now would freeze an arbitrary frame".format(frame)
                    )
            page.locator("svg").first.screenshot(path=str(target), omit_background=True)
        finally:
            browser.close()

    return json.dumps(
        {
            "path": str(target),
            "bytes": target.stat().st_size if target.exists() else 0,
            "scale": scale,
            "motion_source": motion,
        },
        indent=2,
    )


TOOLS = [
    Tool(
        "export_svg",
        "Turn a generated diagram HTML into a standalone SVG and return it as "
        "text. Diagram only: the first <svg> node, with the editorial wrapper "
        "dropped, xmlns and an XML-safe Google Fonts @import injected, and an "
        "XML declaration prepended. Writes nothing -- save the returned text "
        "yourself. Note that importers which do not fetch remote fonts will "
        "substitute typography; use export_png when that matters.",
        obj({"path": {"type": "string", "description": "Absolute path to the diagram HTML."}}, ["path"]),
        _export_svg,
    ),
    Tool(
        "export_png",
        "Rasterize a generated diagram to PNG with a transparent background, "
        "writing it next to the source unless another path is given. Requires "
        "Playwright and Chromium -- check with `doctor` first. This tool writes "
        "a file, so it needs the write effect.",
        obj(
            {
                "path": {"type": "string", "description": "Absolute path to the diagram HTML."},
                "out": {"type": "string", "description": "Where to write the PNG. Defaults to the source path with a .png suffix."},
                "scale": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 4,
                    "description": "Device scale factor. 2 (default) for docs and slides, 3 for print, 1 for thumbnails.",
                },
            },
            ["path"],
        ),
        _export_png,
    ),
]
