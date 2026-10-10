"""CLI behaviour of base add, check, check --fix-lines and scan over the entry store."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.ingest import writer_lock
from kgdistiller.knowledge_store import load_state
from tests.knowledge_fixture import make_fixture, type_document, use_temporary_home

SOURCE = "notes/measure.txt"
TEXT = "Chapter one\nA measure space is a triple\nwith countable additivity.\n"
COMMANDS = {
    "base", "scan", "check", "obsidian", "agent", "harvest", "capture", "ingest",
    "export", "codex", "claude", "mcp",
}
AGENT_COMMANDS = {"status", "compiled", "resolve", "search", "get", "expand", "ppr", "context"}


def kgdistiller(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI in ``cwd``; the patched KGDISTILLER_HOME is inherited."""
    return subprocess.run(
        [sys.executable, "-m", "kgdistiller", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        check=False,
    )


def run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a base command for base ``kb`` from a directory outside its root."""
    return kgdistiller(*arguments, "--base", "kb", cwd=root.parent)


class BaseAddTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = use_temporary_home(self)
        self.tmp = self.home.parent

    def register_papers(self, root: Path) -> None:
        config_path = self.home / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        name = next(iter(config["bases"]))
        config["bases"][name]["sources"] = {"papers/*.tex": "papers"}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (self.home / "types/papers.md").write_text(
            type_document({"node_kinds": ["definition"], "guidance": "Named statements."}), encoding="utf-8")

    def test_base_add_round_trip(self) -> None:
        root = self.tmp / "测度 project"
        (root / "papers").mkdir(parents=True)
        (root / "papers/paper.tex").write_text("\\kn{Ignored}\n", encoding="utf-8")
        added = kgdistiller("base", "add", str(root), "--name", "papers", cwd=self.tmp)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["added"]["name"], "papers")
        self.assertEqual(list((root / ".knowledge/entries").iterdir()), [])
        self.assertEqual((self.home / ".gitignore").read_text(encoding="utf-8"), "index.sqlite*\nlock\n")
        self.assertEqual([path.name for path in (root / ".knowledge").iterdir()], ["entries"])
        self.register_papers(root)
        check = kgdistiller("check", cwd=root / "papers")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertEqual(check.stdout.strip(), "OK: 0 entries, 0 edges")
        scan = kgdistiller("scan", "--file", "paper.tex", cwd=root / "papers")
        self.assertEqual(scan.returncode, 0, scan.stderr)
        self.assertEqual(json.loads(scan.stdout)["files"][0]["type"], "papers")
        elsewhere = kgdistiller("check", "--base", "papers", cwd=self.tmp)
        self.assertEqual(elsewhere.returncode, 0, elsewhere.stderr)
        again = kgdistiller("base", "add", str(root), "--name", "other", cwd=self.tmp)
        self.assertEqual(again.returncode, 1)
        self.assertIn("already registered", again.stderr)
        listed = json.loads(kgdistiller("base", "list", cwd=self.tmp).stdout)
        self.assertEqual([item["name"] for item in listed["bases"]], ["papers"])
        removed = kgdistiller("base", "rm", "papers", cwd=self.tmp)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue((root / ".knowledge/entries").is_dir())
        orphan = kgdistiller("check", cwd=root)
        self.assertEqual(orphan.returncode, 1)
        self.assertIn("not inside any registered base root", orphan.stderr)

    def test_commands_refuse_without_a_home_or_outside_every_root(self) -> None:
        missing = kgdistiller("check", cwd=self.tmp)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("kgd base add", missing.stderr)
        self.assertFalse(self.home.exists())
        root = self.tmp / "kb"
        root.mkdir()
        self.assertEqual(kgdistiller("base", "add", str(root), cwd=self.tmp).returncode, 0)
        outside = kgdistiller("agent", "status", cwd=self.tmp)
        self.assertEqual(outside.returncode, 1)
        self.assertIn("registered bases: kb", outside.stderr)
        self.assertIn("--base", outside.stderr)
        unknown = kgdistiller("agent", "status", "--base", "nope", cwd=root)
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("unknown base 'nope'", unknown.stderr)
        for cwd, arguments in ((self.tmp, ("--base", "kb")), (root, ()), (root / ".knowledge/entries", ())):
            with self.subTest(cwd=cwd):
                status = kgdistiller("agent", "status", *arguments, cwd=cwd)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertEqual(json.loads(status.stdout)["counts"], {"entries": 0, "edges": 0})


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

    def test_check_and_agent_treat_a_missing_entries_directory_as_empty(self) -> None:
        knowledge = self.root / ".knowledge"
        shutil.rmtree(knowledge / "entries")
        (knowledge / "edges.jsonl").unlink()
        result = run_cli(self.root, "check")
        self.assertEqual((result.returncode, result.stdout), (0, "OK: 0 entries, 0 edges\n"), result.stderr)
        status = run_cli(self.root, "agent", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["counts"], {"entries": 0, "edges": 0})

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
        self.assertIn("run `kgd check --base kb`", search.stderr)

    def test_fix_lines_holds_the_home_lock_and_a_journal_blocks_check(self) -> None:
        self.fixture.write_source(SOURCE, "Preface\n" + TEXT)
        with writer_lock(self.fixture.paths):
            locked = run_cli(self.root, "check", "--fix-lines")
        self.assertEqual(locked.returncode, 1)
        self.assertIn("lock", locked.stderr)
        self.assertEqual(load_state(self.root).entries["measure-space"]["line_start"], 2)
        journal = self.root / ".knowledge/build/kgdistiller-ingest/journal.json"
        journal.parent.mkdir(parents=True)
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
            sources={"math/**/*.tex": "math-notes", "logs/*": "logs"},
            types={
                "math-notes": {"node_kinds": ["definition"], "relation_kinds": {"implies": ["premise", "conclusion"]},
                               "epistemic": ["proved", "stated"], "guidance": "Named statements."},
                "logs": {"node_kinds": ["note"], "guidance": "Log lines."},
            },
        )
        self.root = self.fixture.root

    def test_scan_reports_profile_and_numbered_lines(self) -> None:
        self.fixture.write_source("math/ch/one.tex", "\\begin{definition}\r\nBody\r\n")
        self.fixture.write_source("logs/run.log", "only line")
        result = kgdistiller("scan", "--file", "ch/one.tex", "--file", str(self.root / "logs/run.log"),
                             "--base", "kb", cwd=self.root / "math")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"files": [
            {"path": "math/ch/one.tex", "base": "kb", "type": "math-notes",
             "profile": {"node_kinds": ["definition"], "relation_kinds": {"implies": ["premise", "conclusion"]},
                         "epistemic": ["proved", "stated"], "guidance": "Named statements."},
             "lines": [{"line": 1, "text": "\\begin{definition}"}, {"line": 2, "text": "Body"}]},
            {"path": "logs/run.log", "base": "kb", "type": "logs",
             "profile": {"node_kinds": ["note"], "relation_kinds": {}, "epistemic": [], "guidance": "Log lines."},
             "lines": [{"line": 1, "text": "only line"}]},
        ]})

    def test_json_output_escapes_text_an_ascii_console_cannot_encode(self) -> None:
        self.fixture.write_source("logs/cjk.txt", "测度 \U0001d4dc\n")
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict"}
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "scan", "--file", "logs/cjk.txt"],
            capture_output=True, check=False, env=env, cwd=self.root,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stdout = result.stdout.decode("ascii")
        self.assertIn("\\u6d4b\\u5ea6 \\ud835\\udcdc", stdout)
        self.assertEqual(json.loads(stdout)["files"][0]["lines"][0]["text"], "测度 \U0001d4dc")

    def test_unregistered_or_missing_files_are_clear_errors(self) -> None:
        self.fixture.write_source("math/notes.typ", "Typst companion\n")
        outside = self.root.parent / "outside.tex"
        outside.write_text("Elsewhere\n", encoding="utf-8")
        for path, message in (("math/notes.typ", "not a registered source of base kb"),
                              ("math/missing.tex", "does not exist"),
                              (str(outside), "scan file lies outside base kb")):
            with self.subTest(path=path):
                result = kgdistiller("scan", "--file", path, cwd=self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertIn("bases.kb.sources", kgdistiller("scan", "--file", "math/notes.typ", cwd=self.root).stderr)
        self.assertNotEqual(run_cli(self.root, "scan").returncode, 0)


def _choices(help_text: str) -> set[str]:
    match = re.search(r"\{([a-z,-]+)\}", help_text)
    assert match is not None, help_text
    return set(match[1].split(","))


class CommandSurfaceTest(unittest.TestCase):
    def test_only_the_entry_store_commands_are_offered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(_choices(kgdistiller("--help", cwd=root).stdout), COMMANDS)
            self.assertEqual(_choices(kgdistiller("agent", "--help", cwd=root).stdout), AGENT_COMMANDS)
            usage = kgdistiller("--help", cwd=root).stdout.split("{", 1)[0]
            self.assertEqual(set(re.findall(r"--[a-z-]+", usage)) - {"--help"}, set())

    def test_base_is_a_leaf_option(self) -> None:
        from unittest.mock import patch

        from kgdistiller.cli import parse_args

        for arguments in (("agent", "search", "Q", "--base", "kb"), ("ingest", "plan", "R", "--base", "kb"),
                          ("check", "--base", "kb"), ("export", "obsidian", "--base", "kb"),
                          ("mcp", "--base", "kb"), ("obsidian", "install", "--base", "kb")):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["kgd", *arguments]):
                self.assertEqual(parse_args().base, "kb")
        with tempfile.TemporaryDirectory() as folder:
            for arguments in (("--base", "kb", "check"), ("base", "list", "--base", "kb"),
                              ("agent", "compiled", "--library", "x", "--base", "kb", "search", "q")):
                with self.subTest(arguments=arguments):
                    self.assertEqual(kgdistiller(*arguments, cwd=Path(folder)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
