"""The tools themselves: knowledge, the wrapped scripts, and export."""

from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import tempfile

from diagram_mcp import skill
from diagram_mcp.tools import BY_NAME

FIXTURE_DRAWIO = skill.plugin_root() / "scripts" / "fixtures" / "sample-architecture.drawio"


# Positional-only: `template` and `profiles` both take an argument called
# `name`, which would otherwise collide with this helper's own parameter.
def run(tool, /, **arguments):
    return BY_NAME[tool].handler(arguments)


class Scratch(unittest.TestCase):
    """A TestCase that can make a throwaway directory on 3.9.

    unittest.TestCase.enterContext arrived in 3.11 and this project runs
    on whatever python3 the machine resolves -- 3.9.6 on stock macOS.
    """

    def scratch(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return Path(holder.name)


class Knowledge(unittest.TestCase):
    def test_the_catalogue_comes_from_disk(self):
        # Upstream adds and removes types between releases. Whatever is in
        # references/ is the catalogue, not a number written down anywhere.
        on_disk = sorted(p.stem[len("type-"):] for p in skill.references_dir().glob("type-*.md"))
        served = json.loads(run("types"))
        self.assertEqual(sorted(entry["id"] for entry in served["types"]), on_disk)
        self.assertEqual(served["count"], len(on_disk))

    def test_every_type_carries_the_line_that_decides_it(self):
        for entry in json.loads(run("types"))["types"]:
            self.assertTrue(entry["best_for"], "{} has no Best for line".format(entry["id"]))

    def test_spec_carries_the_type_the_palette_and_the_file_contract(self):
        text = run("spec", type="flowchart")
        self.assertIn("type-flowchart.md", text)
        self.assertIn("style-guide.md", text)
        self.assertIn("output-spec.md", text)

    def test_primitives_are_opt_in(self):
        lean = run("spec", type="flowchart")
        fat = run("spec", type="flowchart", primitives=True)
        self.assertNotIn("primitive-icons.md", lean)
        self.assertIn("primitive-icons.md", fat)
        self.assertGreater(len(fat), len(lean) * 2)

    def test_an_unknown_type_names_the_catalogue(self):
        with self.assertRaises(skill.SkillError) as caught:
            run("spec", type="octopus")
        self.assertIn("flowchart", str(caught.exception))

    def test_templates_are_globbed_not_listed(self):
        self.assertEqual(
            json.loads(run("templates"))["templates"],
            sorted(p.name for p in skill.assets_dir().glob("template*.html")),
        )

    def test_template_suffix_is_optional(self):
        self.assertEqual(run("template", name="template-motion"), run("template", name="template-motion.html"))

    def test_a_template_name_cannot_escape_assets(self):
        with self.assertRaises(skill.SkillError):
            run("template", name="../../../../etc/passwd")

    def test_a_profile_name_cannot_escape_the_profiles_directory(self):
        with self.assertRaises(skill.SkillError):
            run("profiles", name="../../.ssh/id_rsa")


class Deterministic(Scratch):
    def test_mermaid_from_inline_text(self):
        out = run("import_mermaid", text="flowchart TD\n  A[One] --> B[Two]\n")
        self.assertIn("flowchart", out)
        self.assertIn("(inline text)", out)

    def test_the_staging_file_never_reaches_the_caller(self):
        # Text is staged through a temp file the caller never named. Leaking
        # its path would report a source that is already unlinked.
        out = run("import_mermaid", text="flowchart TD\n  A --> B\n")
        self.assertNotIn("diagram-mcp-", out)

    def test_mermaid_json_mode_is_json(self):
        out = run("import_mermaid", text="flowchart TD\n  A --> B\n", as_json=True)
        self.assertEqual(json.loads(out)["diagrams_total"], 1)

    def test_drawio_reads_the_upstream_fixture(self):
        self.assertTrue(FIXTURE_DRAWIO.is_file(), "the plugin tree is incomplete")
        out = run("import_drawio", path=str(FIXTURE_DRAWIO))
        self.assertIn("Platform", out)

    def test_a_missing_file_says_what_it_looked_for(self):
        with self.assertRaises(skill.SkillError) as caught:
            run("validate", path="/no/such/diagram.html")
        self.assertIn("/no/such/diagram.html", str(caught.exception))

    def test_validate_passes_a_shipped_example(self):
        example = skill.assets_dir() / "example-architecture.html"
        verdict = json.loads(run("validate", path=str(example)))
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(verdict["findings"], [])

    def test_a_failing_diagram_is_an_answer_not_an_error(self):
        # self_check exits 1 on a bad file. That is a verdict, and turning it
        # into a tool error would hide the reasons behind a failure banner.
        bad = self.scratch() / "bad.html"
        bad.write_text("<html><body><script>alert(1)</script></body></html>", encoding="utf-8")
        verdict = json.loads(run("validate", path=str(bad)))
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["findings"])


class Export(Scratch):
    example = None

    @classmethod
    def setUpClass(cls):
        cls.example = skill.assets_dir() / "example-architecture.html"

    def test_the_svg_is_well_formed_xml(self):
        # The whole reason the font URL is re-escaped: a standalone .svg is
        # parsed as strict XML, where a bare & opens an entity reference.
        ET.fromstring(run("export_svg", path=str(self.example)))

    def test_the_font_import_is_xml_escaped(self):
        svg = run("export_svg", path=str(self.example))
        self.assertIn("&amp;family=", svg)
        self.assertNotIn("?family=Instrument+Serif:ital@0;1&family", svg)

    def test_defs_are_merged_not_duplicated(self):
        self.assertEqual(run("export_svg", path=str(self.example)).count("<defs"), 1)

    def test_it_starts_with_an_xml_declaration(self):
        self.assertTrue(run("export_svg", path=str(self.example)).startswith('<?xml version="1.0"'))

    def test_a_file_with_no_svg_is_refused(self):
        page = self.scratch() / "plain.html"
        page.write_text("<html><body><p>no diagram here</p></body></html>", encoding="utf-8")
        with self.assertRaises(skill.SkillError) as caught:
            run("export_svg", path=str(page))
        self.assertIn("no <svg>", str(caught.exception))

    def test_png_refuses_a_scale_that_would_soften_or_upscale(self):
        for scale in (0.5, 5):
            with self.assertRaises(skill.SkillError):
                run("export_png", path=str(self.example), scale=scale)

    def test_png_refuses_the_gallery(self):
        gallery = skill.assets_dir() / "index.html"
        if not gallery.is_file():
            self.skipTest("this checkout ships no gallery")
        with self.assertRaises(skill.SkillError) as caught:
            run("export_png", path=str(gallery))
        self.assertIn("gallery", str(caught.exception))


class Doctor(unittest.TestCase):
    def test_it_reports_the_plugin_version_and_the_scripts(self):
        report = json.loads(run("doctor"))
        self.assertTrue(report["skill"]["ready"])
        self.assertEqual(report["skill"]["version"], skill.version())
        self.assertTrue(all(report["skill"]["scripts"].values()), report["skill"]["scripts"])
        self.assertTrue(report["ok"])

    def test_a_machine_without_chromium_is_still_healthy(self):
        # PNG export is an extra. Reporting its absence as a broken install
        # would send somebody to fix what is not wrong.
        report = json.loads(run("doctor"))
        self.assertTrue(report["ok"])
        self.assertIn("available", report["png_export"])


if __name__ == "__main__":
    unittest.main()


class Ceiling(unittest.TestCase):
    """No reachable response may exceed what the transport carries.

    An upstream sync is the way this breaks: they add a reference file or
    grow an existing one, `spec` quietly crosses the line, and the failure
    shows up as a dead MCP session rather than as an oversized file. The
    biggest response this server can be asked for is `spec` with every
    optional section on, so that is what gets measured.
    """

    def test_the_fattest_spec_fits(self):
        from diagram_mcp.server import MAX_RESULT_BYTES

        worst = 0
        culprit = ""
        for entry in json.loads(run("types"))["types"]:
            size = len(
                run(
                    "spec",
                    type=entry["id"],
                    primitives=True,
                    animation=True,
                    semantic_patterns=True,
                ).encode("utf-8")
            )
            if size > worst:
                worst, culprit = size, entry["id"]
        self.assertLess(
            worst,
            MAX_RESULT_BYTES,
            "spec({}) is {} bytes, at or above the {} byte ceiling".format(
                culprit, worst, MAX_RESULT_BYTES
            ),
        )

    def test_the_ceiling_stays_under_the_bridge_line_cap(self):
        # cmd/atenea/mcp.go relays with a 1 MiB scanner cap, and that is the
        # tighter of the two hops. A ceiling raised past it would let this
        # server emit a line that kills the client's session.
        from diagram_mcp.server import MAX_RESULT_BYTES

        self.assertLess(MAX_RESULT_BYTES, 1 << 20)
