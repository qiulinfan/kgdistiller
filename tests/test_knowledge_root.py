"""The hidden .knowledge/ tree is the project's only knowledge root."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.capture import prepare_capture
from kgdistiller.ingest import apply_ingest, load_request
from kgdistiller.knowledge_paths import KNOWLEDGE_DIRECTORY, knowledge_root
from kgdistiller.query import GraphView, get
from kgdistiller.store import snapshot_store, verify_store
from kgdistiller.vault_registry import ensure_vault_manifest
from tests import test_capture


def run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kgdistiller", "--repo-root", str(root), *arguments],
        capture_output=True,
        text=True,
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

    def test_init_creates_only_the_hidden_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = run_cli(root, "init")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((root / ".knowledge/sources.json").is_file())
            self.assertTrue((root / ".knowledge/vault.json").is_file())
            self.assertNotIn("knowledge", {path.name for path in root.iterdir()})


class KnowledgeRootDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture = test_capture.CaptureTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root
        self.paths = fixture.paths
        ensure_vault_manifest(self.root)
        prepared = prepare_capture(self.paths, fixture.payload(), self.root / ".knowledge/build/capture")
        apply_ingest(self.paths, load_request(Path(prepared["artifacts"]["apply"]), mode="apply"))

    def test_default_commands_use_the_hidden_tree(self) -> None:
        beta = get(GraphView.load(self.paths.graph_dir), "beta")["node"]
        self.assertTrue(beta["properties"]["entry_authority"].startswith(".knowledge/entries/"))
        self.assertTrue((self.root / ".knowledge/entries/beta.md").is_file())
        result = run_cli(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
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
                ("audit",),
                ("export", "obsidian", "--replace"),
                ("init",),
            ):
                with self.subTest(arguments=arguments):
                    result = run_cli(self.root, *arguments)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("knowledge tree must not be a symlink", result.stderr)
            self.assertEqual(sorted(path.relative_to(target) for path in target.rglob("*")), before)

    def test_store_snapshot_verifies_the_hidden_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "snapshot"
            snapshot_store(
                self.root, output, registry=self.paths.registry,
                graph_dir=self.paths.graph_dir, identities=self.paths.identities,
                alignments=self.paths.alignments,
            )
            self.assertEqual(verify_store(output)["status"], "verified")
            self.assertTrue((output / ".knowledge/entries/beta.md").is_file())
            self.assertNotIn("knowledge", {path.name for path in output.iterdir()})


if __name__ == "__main__":
    unittest.main()
