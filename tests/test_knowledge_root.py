"""The hidden .knowledge/ tree is a base's only knowledge root."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.home import KNOWLEDGE_DIRECTORY, knowledge_root
from tests.knowledge_fixture import make_fixture, use_temporary_home
from tests.test_read_adapters import build_entry_store


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
        self.fixture = make_fixture(self)
        build_entry_store(self.fixture)
        self.root = self.fixture.root

    def test_default_commands_use_the_hidden_tree(self) -> None:
        result = run_cli(self.root.parent, "check", "--base", "kb")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("OK: 2 entries, 1 edges", result.stdout)
        self.assertNotIn("knowledge", {path.name for path in self.root.iterdir()})

    @unittest.skipIf(os.name == "nt", "directory symlinks need extra privileges on Windows")
    def test_cli_rejects_a_symlinked_tree_before_any_command(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "outside"
            (self.root / ".knowledge").rename(target)
            (self.root / ".knowledge").symlink_to(target, target_is_directory=True)
            before = sorted(path.relative_to(target) for path in target.rglob("*"))
            for arguments in (
                ("agent", "status"),
                ("agent", "resolve", "beta"),
                ("check",),
                ("export", "obsidian"),
            ):
                with self.subTest(arguments=arguments):
                    result = run_cli(self.root.parent, *arguments, "--base", "kb")
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("knowledge tree must not be a symlink", result.stderr)
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
