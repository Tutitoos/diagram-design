"""The diagrammer agent: the wire, the routing and the repair round.

The model is stubbed with a script on disk rather than by patching, because
what is being checked is the spawn as much as the logic: the agent shells out
to a CLI, hands it a prompt on stdin, and reads one answer back.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from diagram_mcp import agent, skill

GOOD = skill.assets_dir() / "example-architecture.html"
BAD_HTML = "<html><body><p>not a diagram</p></body></html>"


class AgentCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))

    def stub_model(self, author_script):
        """Write a fake model CLI that answers the catalogue and then authors.

        `author_script` is shell that prints the HTML for one attempt; a
        counter file lets a stub answer differently the second time, which is
        how the repair round gets exercised.
        """
        path = self.root / "fake-model"
        path.write_text(
            "#!/bin/sh\nprompt=$(cat)\n"
            'case "$prompt" in\n'
            "  *CATALOGUE:*) echo architecture ;;\n"
            "  *) {} ;;\n"
            "esac\n".format(author_script),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["DIAGRAM_MODEL_BIN"] = str(path)
        # PATH matters: the agent resolves the binary with shutil.which.
        os.environ["PATH"] = str(self.root) + os.pathsep + os.environ.get("PATH", "")
        return path

    def run_agent(self, assignment):
        stdin = io.StringIO(json.dumps(assignment))
        stdout = io.StringIO()
        code = agent.main(stdin, stdout)
        self.assertEqual(code, 0, "the verdict is the channel; the exit status is always 0")
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "one report, one line")
        return json.loads(lines[0])

    def assignment(self, objective="Mapa de la plataforma", files=None):
        return {
            "task": {"objective": objective, "files": files or []},
            "context": {"repository": {"root": str(self.root)}},
        }


class Wire(AgentCase):
    def test_an_unreadable_assignment_is_a_failed_report_not_a_crash(self):
        stdout = io.StringIO()
        self.assertEqual(agent.main(io.StringIO("not json"), stdout), 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(report["reason"]["kind"], "invalid_input")

    def test_no_objective_is_refused_before_any_model_is_called(self):
        report = self.run_agent({"task": {}})
        self.assertEqual(report["verdict"], "failed")
        self.assertIn("objective", report["reason"]["text"])

    def test_a_missing_model_cli_names_the_variable_that_fixes_it(self):
        os.environ["DIAGRAM_MODEL_BIN"] = "no-such-model-cli"
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(report["reason"]["kind"], "unavailable")
        self.assertIn("DIAGRAM_MODEL_BIN", report["reason"]["text"])

    def test_a_failed_report_carries_no_result(self):
        # Atenea refuses a non-ok verdict with no reason, and a claim only
        # stands on ok. An empty result with a reason is the shape it wants.
        os.environ["DIAGRAM_MODEL_BIN"] = "no-such-model-cli"
        report = self.run_agent(self.assignment())
        self.assertNotIn("result", report)
        self.assertIn("reason", report)


class Authoring(AgentCase):
    def test_a_clean_diagram_reports_ok_and_the_four_declared_fields(self):
        self.stub_model('cat "{}"'.format(GOOD))
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "ok")
        self.assertEqual(set(report["result"]), {"path", "type", "checks", "bytes"})
        self.assertEqual(report["result"]["type"], "architecture")
        self.assertEqual(report["result"]["checks"], "ok")
        self.assertTrue(Path(report["result"]["path"]).is_file())
        self.assertEqual(report["result"]["bytes"], Path(report["result"]["path"]).stat().st_size)

    def test_the_file_lands_in_the_repository_root(self):
        self.stub_model('cat "{}"'.format(GOOD))
        report = self.run_agent(self.assignment())
        self.assertEqual(Path(report["result"]["path"]).parent, self.root)

    def test_a_fenced_answer_is_unfenced(self):
        self.stub_model('printf "\\`\\`\\`html\\n"; cat "{}"; printf "\\n\\`\\`\\`\\n"'.format(GOOD))
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "ok")
        self.assertFalse(Path(report["result"]["path"]).read_text().lstrip().startswith("`"))

    def test_a_failed_check_gets_one_repair_round_and_then_passes(self):
        counter = self.root / "n"
        self.stub_model(
            'if [ -f "{c}" ]; then cat "{good}"; else : > "{c}"; printf "%s" \'{bad}\'; fi'.format(
                c=counter, good=GOOD, bad=BAD_HTML
            )
        )
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "ok", report)
        self.assertTrue(counter.is_file(), "the first attempt must actually have run")

    def test_a_diagram_that_never_passes_is_incomplete_not_ok(self):
        # The file exists and is worth looking at, but presenting it as ok
        # would be the one lie this agent is in a position to tell.
        self.stub_model("printf '%s' '{}'".format(BAD_HTML))
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "incomplete")
        self.assertIn("reason", report)
        self.assertNotEqual(report["result"]["checks"], "ok")
        self.assertTrue(Path(report["result"]["path"]).is_file())

    def test_an_answer_naming_no_type_is_refused(self):
        path = self.root / "fake-model"
        path.write_text("#!/bin/sh\ncat >/dev/null\necho 'no idea'\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        os.environ["DIAGRAM_MODEL_BIN"] = str(path)
        report = self.run_agent(self.assignment())
        self.assertEqual(report["verdict"], "failed")
        self.assertIn("names no type", report["reason"]["text"])


class Sources(AgentCase):
    def test_a_mermaid_source_is_parsed_into_the_brief(self):
        source = self.root / "flow.mmd"
        source.write_text("flowchart TD\n  A[Cliente] --> B[Atenea]\n", encoding="utf-8")
        hints, _ = agent.read_sources([str(source)], str(self.root))
        self.assertIn("flow.mmd", hints)
        self.assertIn("flowchart", hints)

    def test_an_unparseable_name_is_skipped_rather_than_fatal(self):
        hints, _ = agent.read_sources(["notes.txt", "/no/such/file.mmd"], str(self.root))
        self.assertEqual(hints, "")

    def test_a_drawio_source_offers_its_type_candidates(self):
        fixture = skill.plugin_root() / "scripts" / "fixtures" / "sample-architecture.drawio"
        hints, suggested = agent.read_sources([str(fixture)], "")
        self.assertIn("already parsed", hints)
        self.assertTrue(suggested)


class Naming(unittest.TestCase):
    def test_the_slug_comes_from_the_objective(self):
        self.assertEqual(agent.target_path("Mapa de la Plataforma", "/tmp").name, "mapa-de-la-plataforma.html")

    def test_it_lands_in_the_root_it_was_given(self):
        self.assertEqual(agent.target_path("x", "/tmp/somewhere").parent, Path("/tmp/somewhere"))

    def test_no_root_is_refused_rather_than_written_to_the_working_directory(self):
        # A child spawned by the daemon inherits the daemon's cwd. Defaulting
        # to it does not write "here" -- it drops a file into an unrelated
        # checkout, which is exactly what happened the first time this ran
        # through `atenea agent` without --repository.
        with self.assertRaises(agent.AgentError) as caught:
            agent.target_path("x", "")
        self.assertEqual(caught.exception.kind, "invalid_input")
        self.assertIn("--repository", caught.exception.text)

    def test_an_objective_with_no_usable_characters_still_names_a_file(self):
        self.assertEqual(agent.target_path("!!!", "/tmp").name, "diagram.html")

    def test_a_long_objective_is_bounded(self):
        name = agent.target_path("x" * 300, "/tmp").name
        self.assertLessEqual(len(name), len(".html") + 48)


if __name__ == "__main__":
    unittest.main()
