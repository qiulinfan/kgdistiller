"""The command-line surface: commands, JSON output, exit codes and an end-to-end write/read flow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.knowledge_fixture import make_record_home, type_document, use_temporary_home

COMMANDS = {
    "base", "check", "sheet", "accept", "harvest", "index", "search", "resolve", "get",
    "obsidian", "claude", "codex", "mcp",
}
SOURCE = "Chapter one\nA measure space is a triple (X, F, mu).\nA measure is countably additive.\n测度论研究可测空间。\n"


def kgdistiller(*arguments: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI in ``cwd``; the patched KGDISTILLER_HOME is inherited."""
    return subprocess.run(
        [sys.executable, "-m", "kgdistiller", *arguments],
        capture_output=True, text=True, encoding="utf-8", cwd=cwd, env=env, check=False,
    )


def output(result: subprocess.CompletedProcess[str], status: int = 0) -> Any:
    if result.returncode != status:
        raise AssertionError(f"exit {result.returncode}, expected {status}:\n{result.stdout}{result.stderr}")
    return json.loads(result.stdout)


def node(label: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: notes/a.txt\nlines: {lines}\n{extra}".rstrip("\n")


class BaseAddTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = use_temporary_home(self)
        self.tmp = self.home.parent

    def register_papers(self) -> None:
        config_path = self.home / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["bases"]["papers"]["sources"] = {"papers/*.tex": "papers"}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (self.home / "types/papers.md").write_text(
            type_document({"node_kinds": ["definition"], "guidance": "Named statements."}), encoding="utf-8")

    def test_base_add_unicode_root_round_trip(self) -> None:
        root = self.tmp / "测度 project"
        (root / "papers").mkdir(parents=True)
        (root / "papers/paper.tex").write_text("\\kn{Ignored}\nA definition.\n", encoding="utf-8")
        added = output(kgdistiller("base", "add", str(root), "--name", "papers", cwd=self.tmp))
        self.assertEqual(added["added"]["name"], "papers")
        self.assertEqual(added["added"]["root"], str(root))
        self.assertEqual(list((root / ".knowledge/entries").iterdir()), [])
        self.assertEqual((self.home / ".gitignore").read_text(encoding="utf-8"), "index.sqlite*\nlock\n")
        self.register_papers()
        self.assertEqual(output(kgdistiller("check", cwd=root / "papers")), {"errors": [], "stale": [], "moved": []})
        profile = output(kgdistiller("sheet", "paper.tex", "--json", cwd=root / "papers"))
        self.assertEqual((profile["base"], profile["source"], profile["type"], profile["line_count"]),
                         ("papers", "papers/paper.tex", "papers", 2))
        written = output(kgdistiller("sheet", str(root / "papers/paper.tex"), cwd=self.tmp))
        self.assertEqual(written, {"sheet": str(root / ".knowledge/sheets/papers/paper.tex.md")})
        again = kgdistiller("base", "add", str(root), "--name", "other", cwd=self.tmp)
        self.assertEqual(again.returncode, 1)
        self.assertIn("already registered", again.stderr)
        listed = output(kgdistiller("base", "list", cwd=self.tmp))
        self.assertEqual([(item["name"], item["root"], item["records"]) for item in listed["bases"]],
                         [("papers", str(root), 0)])
        self.assertEqual(output(kgdistiller("base", "rm", "papers", cwd=self.tmp))["dangling"], [])
        self.assertTrue((root / ".knowledge/entries").is_dir())
        orphan = kgdistiller("sheet", "papers/paper.tex", cwd=root)
        self.assertEqual(orphan.returncode, 1)
        self.assertIn("not inside a registered base", orphan.stderr)

    def test_commands_refuse_without_a_home_and_create_nothing(self) -> None:
        for arguments in (("index",), ("search", "measure"), ("resolve", "measure"), ("get", "kb:x"),
                          ("sheet", "a.txt"), ("accept", "x.md"), ("harvest", "s.md"), ("base", "list")):
            with self.subTest(arguments=arguments):
                result = kgdistiller(*arguments, cwd=self.tmp)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("kgd base add", json.loads(result.stderr)["message"])
        checked = output(kgdistiller("check", cwd=self.tmp), status=1)
        self.assertEqual([error["rule"] for error in checked["errors"]], ["config"])
        self.assertIn("kgd base add", checked["errors"][0]["message"])
        self.assertFalse(self.home.exists())


class ConsoleTest(unittest.TestCase):
    def test_json_output_escapes_text_an_ascii_console_cannot_encode(self) -> None:
        kb = make_record_home(self)
        kb.write_source("notes/a.txt", SOURCE)
        kb.write_record("measure", node("测度 \U0001d4dc", "3"), "A set function.", ["A measure"])
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "sheet", "notes/a.txt", "--json"],
            capture_output=True, check=False, env={**os.environ, "PYTHONIOENCODING": "ascii:strict"}, cwd=kb.root,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stdout = result.stdout.decode("ascii")
        self.assertIn("\\u6d4b\\u5ea6 \\ud835\\udcdc", stdout)
        self.assertEqual(json.loads(stdout)["records"][0]["label"], "测度 \U0001d4dc")


def _choices(help_text: str) -> set[str]:
    match = re.search(r"\{([a-z,-]+)\}", help_text)
    assert match is not None, help_text
    return set(match[1].split(","))


class CommandSurfaceTest(unittest.TestCase):
    def test_exactly_the_s2_commands_are_offered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            help_text = kgdistiller("--help", cwd=root).stdout
            self.assertEqual(_choices(help_text), COMMANDS)
            usage = help_text.split("{", 1)[0]
            self.assertEqual(set(re.findall(r"--[a-z-]+", usage)) - {"--help"}, set())

    def test_removed_commands_and_flags_are_usage_errors(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for arguments in (("agent", "status"), ("scan", "--file", "a.txt"), ("ingest", "plan", "r.json"),
                              ("capture", "prepare", "c.json"), ("export", "obsidian"), ("mcp", "--base", "kb"),
                              ("mcp", "--embedding"), ("search", "q", "--embedding"), ("search", "q", "--rerank"),
                              ("harvest", "prepare", "x"),
                              ("base", "list", "--base", "kb"), ("--base", "kb", "check")):
                with self.subTest(arguments=arguments):
                    result = kgdistiller(*arguments, cwd=root)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")


class ExitCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self)
        self.root = self.kb.root
        self.kb.write_source("notes/a.txt", SOURCE)
        self.kb.write_record("measure", node("Measure", "3"), "A set function.", ["A measure"])

    def test_zero_one_and_two(self) -> None:
        self.assertEqual(kgdistiller("check", cwd=self.root).returncode, 0)
        self.kb.write_record("broken", node("Broken", "3", kind="lemma"), "No such kind.", ["A measure"])
        self.assertEqual(kgdistiller("check", cwd=self.root).returncode, 1)
        for arguments in (("search",), ("search", "q", "--limit", "0"), ("get", "x", "--source-lines", "-1"),
                          ("accept",), ("search", "q", "--class", "edge"), ("check", "--force"), ()):
            with self.subTest(arguments=arguments):
                self.assertEqual(kgdistiller(*arguments, cwd=self.root).returncode, 2)

    def test_reads_without_an_index_say_run_kgd_index(self) -> None:
        for arguments in (("search", "measure"), ("resolve", "measure"), ("get", "measure")):
            with self.subTest(arguments=arguments):
                result = kgdistiller(*arguments, cwd=self.root)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("run `kgd index`", json.loads(result.stderr)["message"])
        self.assertFalse((self.kb.home / "index.sqlite").exists())


class CheckJsonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self)
        self.root = self.kb.root
        self.kb.write_source("notes/a.txt", SOURCE)

    def test_findings_and_fix_lines(self) -> None:
        moved = self.kb.write_record("measure", node("Measure", "2"), "A set function.", ["A measure is countably"])
        stale = self.kb.write_record("space", node("Space", "2"), "A triple.", ["no longer in the source"])
        broken = self.kb.write_record("broken", node("Broken", "2", extra='requires: ["[[missing]]"]'),
                                      "Dangling.", ["A measure space"])
        report = output(kgdistiller("check", "--base", "kb", cwd=self.root.parent), status=1)
        self.assertEqual(set(report), {"errors", "stale", "moved"})
        self.assertEqual([(error["path"], error["rule"]) for error in report["errors"]], [(str(broken), "link")])
        self.assertIn("dangling link [[missing]]", report["errors"][0]["message"])
        self.assertEqual(report["stale"], [str(stale)])
        self.assertEqual(report["moved"], [{"path": str(moved), "lines": "3-3"}])
        fixed = output(kgdistiller("check", "--fix-lines", cwd=self.root), status=1)
        self.assertEqual((fixed["fixed"], fixed["skipped"], fixed["moved"]), ([str(moved)], [], []))
        self.assertIn("\nlines: 3\n", moved.read_text(encoding="utf-8"))
        broken.unlink()
        stale.unlink()
        self.assertEqual(output(kgdistiller("check", cwd=self.root)), {"errors": [], "stale": [], "moved": []})

    def test_unknown_base_is_a_config_error(self) -> None:
        report = output(kgdistiller("check", "--base", "nope", cwd=self.root), status=1)
        self.assertEqual([error["rule"] for error in report["errors"]], ["config"])
        self.assertIn("unknown base 'nope'", report["errors"][0]["message"])


class EndToEndTest(unittest.TestCase):
    def test_check_accept_index_search_resolve_get(self) -> None:
        kb = make_record_home(self)
        root = kb.root
        kb.write_source("notes/a.txt", SOURCE)
        kb.write_record("measure", node("Measure", "3", extra="aliases: [测度]"), "A countably additive set function.",
                        ["A measure is countably additive."])
        kb.write_record("measure-space", node("Measure space", "2", extra='requires: ["[[measure]]", sigma-algebra]'),
                        "A triple (X, F, mu).", ["A measure space is a triple"], folder="drafts")
        kb.write_record(
            "space-implies-measure",
            node("Measure space implies measure", "2-3", kind="implies",
                 extra='premise: ["[[measure-space]]"]\nconclusion: ["[[measure]]"]\nepistemic: stated'),
            "Every measure space carries a measure.", ["A measure space", "A measure is"], folder="drafts",
        )
        self.assertEqual(output(kgdistiller("check", cwd=root)), {"errors": [], "stale": [], "moved": []})

        lone = kgdistiller("accept", ".knowledge/drafts/space-implies-measure.md", cwd=root)
        refused = output(lone, status=1)["refused"]
        self.assertTrue(any("select [[measure-space]] too" in item["message"] for item in refused), refused)
        drafts = (".knowledge/drafts/measure-space.md", ".knowledge/drafts/space-implies-measure.md")
        self.assertEqual(output(kgdistiller("accept", *drafts, "--dry-run", cwd=root)),
                         {"would_create": ["kb:measure-space", "kb:space-implies-measure"]})
        self.assertEqual(output(kgdistiller("accept", *drafts, cwd=root)),
                         {"created": ["kb:measure-space", "kb:space-implies-measure"], "understanding_set": []})
        self.assertFalse((root / ".knowledge/drafts/measure-space.md").exists())
        self.assertEqual(output(kgdistiller("check", cwd=root)), {"errors": [], "stale": [], "moved": []})

        report = output(kgdistiller("index", "--no-embed", cwd=root))
        self.assertEqual((report["created"], report["bases"]["kb"]["parsed"], report["unembedded"]), (True, 3, 0))
        self.assertEqual(output(kgdistiller("index", cwd=root))["bases"]["kb"]["parsed"], 0)
        self.assertEqual(output(kgdistiller("index", "--rebuild", cwd=root))["bases"]["kb"]["parsed"], 3)

        found = output(kgdistiller("search", "measure space", cwd=root.parent))
        self.assertEqual((found["lanes"], found["results"][0]["uid"]), (["lexical", "name"], "kb:measure-space"))
        self.assertEqual(found["lag"]["changed_files"], 0)
        sparse = output(kgdistiller("search", "measure space", "--no-dense", cwd=root))
        self.assertEqual((sparse["lanes"], sparse["results"][0]["uid"]), (["lexical", "name"], "kb:measure-space"))
        relations = output(kgdistiller("search", "measure", "--class", "relation", "--limit", "5", cwd=root))
        self.assertEqual([item["uid"] for item in relations["results"]], ["kb:space-implies-measure"])
        self.assertTrue(output(kgdistiller("search", "测度", cwd=root))["results"])

        resolved = output(kgdistiller("resolve", "measure", "sigma-algebra", cwd=root))
        self.assertEqual([sense["uid"] for sense in resolved["terms"][0]["senses"]], ["kb:measure"])
        self.assertEqual(resolved["terms"][1]["pending"],
                         [{"owner": "kb:measure-space", "role": "requires", "term": "sigma-algebra"}])

        got = output(kgdistiller("get", "space-implies-measure", "kb:nope", "--source-lines", "1", cwd=root))
        record = got["records"][0]
        self.assertEqual(got["missing"], ["kb:nope"])
        self.assertEqual([(link["role"], link["uid"]) for link in record["out"]],
                         [("conclusion", "kb:measure"), ("premise", "kb:measure-space")])
        self.assertEqual(record["source_text"].split("\n")[0], "1\tChapter one")
        self.assertEqual(len(record["source_text"].split("\n")), 4)

        kb.write_record("measure", node("Measure", "3", extra="aliases: [测度, mass]"), "Edited in place.",
                        ["A measure is countably additive."])
        self.assertEqual(output(kgdistiller("search", "mass", cwd=root))["lag"]["changed_files"], 1)
        self.assertNotIn(".sqlite", "".join(path.name for path in root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
