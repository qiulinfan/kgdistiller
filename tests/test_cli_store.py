"""CLI behaviour of init, check, check --fix-lines and scan over the entry store."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.ingest import IngestPaths, writer_lock
from kgdistiller.knowledge_store import load_state
from tests.knowledge_fixture import make_fixture

SOURCE = "notes/measure.txt"
TEXT = "Chapter one\nA measure space is a triple\nwith countable additivity.\n"
COMMANDS = {
    "vault", "init", "scan", "check", "obsidian", "agent", "harvest", "capture", "ingest",
    "export", "codex", "claude", "mcp",
}
AGENT_COMMANDS = {"status", "compiled", "resolve", "search", "get", "expand", "ppr", "context"}


def run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kgdistiller", "--repo-root", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class InitTest(unittest.TestCase):
    def test_init_registers_one_source_without_scanning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-cli-init-") as folder:
            root = Path(folder)
            (root / "papers").mkdir()
            (root / "papers/paper.tex").write_text("\\kn{Ignored}\n", encoding="utf-8")
            result = run_cli(root, "init", "--source-root", "papers", "--files", "*.tex", "--files", "*.md")
            self.assertEqual(result.returncode, 0, result.stderr)
            registry = json.loads((root / ".knowledge/sources.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["sources"], [
                {"id": "local:notes", "root": "papers", "files": ["*.tex", "*.md"]}])
            self.assertEqual(list((root / ".knowledge/entries").iterdir()), [])
            check = run_cli(root, "check")
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(check.stdout.strip(), "OK: 0 entries, 0 edges")
            again = run_cli(root, "init")
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already exists", again.stderr)


class CheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.root = self.fixture.root
        self.fixture.write_source(SOURCE, TEXT)
        self.fixture.add_entry("measure-space", "Measure space", SOURCE, 2, 3)
        self.fixture.add_entry("chapter", "Chapter", SOURCE, 1)
        self.fixture.add_edge("chapter", "prerequisite-for", "measure-space")

    def test_current_store_prints_ok(self) -> None:
        result = run_cli(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "OK: 2 entries, 1 edges\n")

    def test_moved_evidence_is_reported_then_fixed(self) -> None:
        self.fixture.write_source(SOURCE, "Preface\n\n" + TEXT)
        result = run_cli(self.root, "check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("moved measure-space: notes/measure.txt:2-3 -> 4-5", result.stdout)
        self.assertIn("moved chapter: notes/measure.txt:1-1 -> 3-3", result.stdout)
        self.assertIn("FAILED: 0 errors, 2 stale entries", result.stdout)
        fixed = run_cli(self.root, "check", "--fix-lines")
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        self.assertIn("fixed measure-space: notes/measure.txt:2-3 -> 4-5", fixed.stdout)
        self.assertTrue(fixed.stdout.endswith("OK: 2 entries, 1 edges\n"))
        entry = load_state(self.root).entries["measure-space"]
        self.assertEqual((entry["line_start"], entry["line_end"]), (4, 5))

    def test_tokens_added_around_the_cited_lines_make_the_entry_stale_not_moved(self) -> None:
        for text in (
            "Chapter one\nNew\nNOTE: A measure space is a triple\nwith countable additivity.\n",
            "Chapter one\nNew\nA measure space is a triple\nwith countable additivity. (see)\n",
        ):
            with self.subTest(text=text):
                self.fixture.write_source(SOURCE, text)
                result = run_cli(self.root, "check", "--fix-lines")
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("moved measure-space", result.stdout)
                self.assertNotIn("fixed measure-space", result.stdout)
                self.assertIn("stale measure-space: notes/measure.txt:2-3; Evidence no longer occurs",
                              result.stdout)
                entry = load_state(self.root).entries["measure-space"]
                self.assertEqual((entry["line_start"], entry["line_end"]), (2, 3))

    def test_stale_evidence_and_errors_fail_without_fix(self) -> None:
        self.fixture.write_source(SOURCE, "Chapter one\nA measure space is a pair.\nwith nothing else.\n")
        (self.root / ".knowledge/edges.jsonl").write_text(json.dumps(
            self.fixture.edge("chapter", "implies", "missing")) + "\n", encoding="utf-8")
        result = run_cli(self.root, "check", "--fix-lines")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error dangling-edge: edge endpoint 'missing' has no entry", result.stdout)
        self.assertIn("stale measure-space: notes/measure.txt:2-3; Evidence no longer occurs", result.stdout)
        self.assertIn("FAILED: 1 errors, 1 stale entries", result.stdout)
        self.assertEqual(load_state(self.root).entries["measure-space"]["line_start"], 2)

    def test_every_malformed_entry_and_edge_is_reported(self) -> None:
        entries = self.root / ".knowledge/entries"
        text = (entries / "measure-space.md").read_text(encoding="utf-8")
        (entries / "measure-space.md").write_text(text.replace("understanding: unknown", "understanding: maybe"),
                                                  encoding="utf-8")
        (entries / "chapter.md").rename(entries / "renamed.md")
        result = run_cli(self.root, "check")
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual([line.split(":", 1)[0] for line in lines[:3]],
                         ["error invalid-entry", "error invalid-entry", "error dangling-edge"])
        self.assertIn("measure-space.md", lines[0])
        self.assertIn("renamed.md: file name must be chapter.md", lines[1])
        self.assertTrue(lines[-1].startswith("FAILED: 4 errors"), result.stdout)
        search = run_cli(self.root, "agent", "search", "measure")
        self.assertEqual(search.returncode, 1)
        self.assertIn("run kgdistiller check", search.stderr)

    def test_fix_lines_holds_the_writer_lock_and_a_journal_blocks_check(self) -> None:
        self.fixture.write_source(SOURCE, "Preface\n" + TEXT)
        with writer_lock(IngestPaths(repo_root=self.root, registry=self.fixture.registry)):
            locked = run_cli(self.root, "check", "--fix-lines")
        self.assertEqual(locked.returncode, 1)
        self.assertIn("lock", locked.stderr)
        self.assertEqual(load_state(self.root).entries["measure-space"]["line_start"], 2)
        journal = self.root / ".knowledge/build/kgdistiller-ingest/journal.json"
        journal.write_text("{}", encoding="utf-8")
        for arguments in (("check",), ("check", "--fix-lines")):
            with self.subTest(arguments=arguments):
                result = run_cli(self.root, *arguments)
                self.assertEqual(result.returncode, 1)
                self.assertIn("ingest install is in progress or was interrupted", result.stderr)


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(
            self,
            sources=[
                {"id": "math", "root": "math", "files": ["**/*.tex"], "document_type": "math-notes"},
                {"id": "logs", "root": "logs", "files": ["*"]},
            ],
            document_types={"math-notes": {"node_kinds": ["definition"],
                                           "extraction_guidance": "Named statements."}},
        )
        self.root = self.fixture.root

    def test_scan_reports_profile_and_numbered_lines(self) -> None:
        self.fixture.write_source("math/ch/one.tex", "\\begin{definition}\r\nBody\r\n")
        self.fixture.write_source("logs/run.log", "only line")
        result = run_cli(self.root, "scan", "--file", "math/ch/one.tex",
                         "--file", str(self.root / "logs/run.log"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"files": [
            {"path": "math/ch/one.tex", "source_id": "math", "document_type": "math-notes",
             "profile": {"node_kinds": ["definition"], "extraction_guidance": "Named statements."},
             "lines": [{"line": 1, "text": "\\begin{definition}"}, {"line": 2, "text": "Body"}]},
            {"path": "logs/run.log", "source_id": "logs", "document_type": None, "profile": None,
             "lines": [{"line": 1, "text": "only line"}]},
        ]})

    def test_json_output_escapes_text_an_ascii_console_cannot_encode(self) -> None:
        self.fixture.write_source("logs/cjk.txt", "测度 \U0001d4dc\n")
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict"}
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root), "scan", "--file", "logs/cjk.txt"],
            capture_output=True, check=False, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stdout = result.stdout.decode("ascii")
        self.assertIn("\\u6d4b\\u5ea6 \\ud835\\udcdc", stdout)
        self.assertEqual(json.loads(stdout)["files"][0]["lines"][0]["text"], "测度 \U0001d4dc")

    def test_unregistered_or_missing_files_are_clear_errors(self) -> None:
        self.fixture.write_source("math/notes.typ", "Typst companion\n")
        for path, message in (("math/notes.typ", "not admitted by any registered source"),
                              ("math/missing.tex", "does not exist")):
            with self.subTest(path=path):
                result = run_cli(self.root, "scan", "--file", path)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertNotEqual(run_cli(self.root, "scan").returncode, 0)


def _choices(help_text: str) -> set[str]:
    match = re.search(r"\{([a-z,-]+)\}", help_text)
    assert match is not None, help_text
    return set(match[1].split(","))


class CommandSurfaceTest(unittest.TestCase):
    def test_only_the_entry_store_commands_are_offered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(_choices(run_cli(root, "--help").stdout), COMMANDS)
            self.assertEqual(_choices(run_cli(root, "agent", "--help").stdout), AGENT_COMMANDS)
            usage = run_cli(root, "--help").stdout.split("{", 1)[0]
            self.assertEqual(set(re.findall(r"--[a-z-]+", usage)) - {"--help"},
                             {"--repo-root", "--vault", "--kgdistiller-home", "--registry"})


if __name__ == "__main__":
    unittest.main()
