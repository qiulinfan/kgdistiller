"""The hidden .knowledge/ tree is a base's only knowledge root."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.home import KNOWLEDGE_DIRECTORY, knowledge_root
from tests.knowledge_fixture import make_record_home, use_temporary_home


def run_cli(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kgdistiller", *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


class KnowledgeRootTest(unittest.TestCase):
    def test_knowledge_root_is_the_hidden_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(KNOWLEDGE_DIRECTORY, ".knowledge")
            self.assertEqual(knowledge_root(root), root / ".knowledge")

    @unittest.skipIf(os.name == "nt", "directory symlinks need extra privileges on Windows")
    def test_symlinked_knowledge_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "elsewhere").mkdir()
            (root / ".knowledge").symlink_to(root / "elsewhere", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                knowledge_root(root)

    def test_base_add_creates_only_the_hidden_entries_tree(self) -> None:
        home = use_temporary_home(self)
        root = home.parent / "kb"
        root.mkdir()
        result = run_cli(home.parent, "base", "add", str(root))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual([path.name for path in root.iterdir()], [".knowledge"])
        self.assertEqual([path.name for path in (root / ".knowledge").iterdir()], ["entries"])
        self.assertEqual(list((root / ".knowledge/entries").iterdir()), [])


class KnowledgeRootDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self)
        self.root = self.kb.root
        self.kb.write_source("notes/a.txt", "Title\nA measure space is a triple.\n")
        frontmatter = "label: Measure space\nkind: definition\nsource: notes/a.txt\nlines: 2"
        self.kb.write_record("measure-space", frontmatter, "A triple.", ["A measure space"])
        self.kb.write_record("measure", frontmatter.replace("Measure space", "Measure"), "A set function.",
                             ["A measure"], folder="drafts")

    def test_default_commands_use_the_hidden_tree(self) -> None:
        result = run_cli(self.root.parent, "check", "--base", "kb")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout), {"errors": [], "stale": [], "moved": []})
        self.assertNotIn("knowledge", {path.name for path in self.root.iterdir()})

    @unittest.skipIf(os.name == "nt", "directory symlinks need extra privileges on Windows")
    def test_cli_rejects_a_symlinked_tree_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "outside"
            (self.root / ".knowledge").rename(target)
            (self.root / ".knowledge").symlink_to(target, target_is_directory=True)
            before = sorted(path.relative_to(target) for path in target.rglob("*"))
            for arguments, names_the_symlink in (
                (("check", "--base", "kb"), True),
                (("check", "--base", "kb", "--fix-lines"), True),
                (("sheet", "notes/a.txt"), True),
                (("sheet", "notes/a.txt", "--json"), True),
                (("accept", ".knowledge/drafts/measure.md"), False),
                (("index",), True),
            ):
                with self.subTest(arguments=arguments):
                    result = run_cli(self.root, *arguments)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    if names_the_symlink:
                        self.assertIn("must not be a symlink", result.stdout + result.stderr)
                    else:
                        self.assertIn("refused", json.loads(result.stdout))
            self.assertFalse((self.kb.home / "index.sqlite").exists())
            other = self.root.parent / "other"
            other.mkdir()
            (other / ".knowledge").symlink_to(target, target_is_directory=True)
            added = run_cli(self.root.parent, "base", "add", str(other))
            self.assertEqual(added.returncode, 1, added.stdout)
            self.assertIn("must not be a symlink", added.stderr)
            self.assertNotIn("other", run_cli(self.root.parent, "base", "list").stdout)
            self.assertEqual(sorted(path.relative_to(target) for path in target.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
