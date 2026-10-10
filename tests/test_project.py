from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.project import (
    ensure_knowledge_gitignore,
    initialize_project,
)
from kgdistiller.vault_registry import VAULT_SCHEMA


class ProjectInitializationTest(unittest.TestCase):
    def test_init_creates_minimal_registry_and_empty_store(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            root = Path(temporary)
            registry = root / ".knowledge/sources.json"

            result = initialize_project(root, registry, source_root=Path("notes"))

            sources = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual("kgdistiller-sources-v1", sources["schema"])
            self.assertEqual({"schema", "sources"}, set(sources))
            self.assertEqual(
                [{"id": "local:notes", "root": "notes", "files": ["**/*"]}],
                sources["sources"],
            )
            self.assertEqual(["**/*"], result["files"])
            self.assertTrue((root / "notes").is_dir())
            self.assertTrue((root / ".knowledge/entries").is_dir())
            self.assertEqual("", (root / ".knowledge/edges.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                {".gitignore", "edges.jsonl", "entries", "sources.json", "vault.json"},
                {path.name for path in (root / ".knowledge").iterdir()},
            )
            vault_manifest = json.loads(
                (root / ".knowledge/vault.json").read_text(encoding="utf-8")
            )
            self.assertEqual(VAULT_SCHEMA, vault_manifest["schema"])
            self.assertRegex(
                vault_manifest["vault_id"],
                r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$",
            )
            self.assertEqual(
                "build/\n",
                (root / ".knowledge/.gitignore").read_text(encoding="utf-8"),
            )

    def test_init_refuses_to_replace_a_registry_without_force(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            root = Path(temporary)
            registry = root / ".knowledge/sources.json"
            initialize_project(root, registry, source_root=Path("notes"))
            edges = root / ".knowledge/edges.jsonl"
            edges.write_text('{"kept": true}\n', encoding="utf-8")
            before = registry.read_bytes()
            with self.assertRaises(FileExistsError):
                initialize_project(root, registry, source_root=Path("replacement"))
            self.assertEqual(before, registry.read_bytes())
            gitignore = root / ".knowledge/.gitignore"
            gitignore.write_text("build/\nlocal-secret/\n", encoding="utf-8")
            initialize_project(
                root,
                registry,
                source_root=Path("replacement"),
                files=["**/*.tex", "*.txt"],
                force=True,
            )
            source = json.loads(registry.read_text(encoding="utf-8"))["sources"][0]
            self.assertEqual("replacement", source["root"])
            self.assertEqual(["**/*.tex", "*.txt"], source["files"])
            self.assertEqual('{"kept": true}\n', edges.read_text(encoding="utf-8"))
            self.assertEqual(
                "build/\nlocal-secret/\n",
                gitignore.read_text(encoding="utf-8"),
            )

    def test_existing_gitignore_is_extended_atomically_and_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            gitignore = Path(temporary) / ".knowledge/.gitignore"
            gitignore.parent.mkdir(parents=True)
            gitignore.write_bytes(b"local-secret/\r\n")
            if os.name != "nt":
                gitignore.chmod(0o640)

            self.assertTrue(ensure_knowledge_gitignore(gitignore))
            expected = b"local-secret/\r\nbuild/\n"
            self.assertEqual(expected, gitignore.read_bytes())
            self.assertFalse(ensure_knowledge_gitignore(gitignore))
            self.assertEqual(expected, gitignore.read_bytes())
            if os.name != "nt":
                self.assertEqual(0o640, stat.S_IMODE(gitignore.stat().st_mode))

            gitignore.write_bytes(b"build/\n!build/\n")
            self.assertTrue(ensure_knowledge_gitignore(gitignore))
            self.assertEqual(b"build/\n!build/\nbuild/\n", gitignore.read_bytes())
            self.assertFalse(ensure_knowledge_gitignore(gitignore))

            gitignore.write_bytes(b"!build/\rbuild/\n")
            self.assertTrue(ensure_knowledge_gitignore(gitignore))
            self.assertEqual(b"!build/\rbuild/\nbuild/\n", gitignore.read_bytes())
            self.assertFalse(ensure_knowledge_gitignore(gitignore))

    def test_custom_registry_still_ignores_default_build_projections(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            root = Path(temporary)
            registry = root / "config/sources.json"

            initialize_project(
                root,
                registry,
                source_root=Path("notes"),
            )

            self.assertEqual(
                "build/\n",
                (root / ".knowledge/.gitignore").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "build/\n",
                (root / "config/.gitignore").read_text(encoding="utf-8"),
            )

    def test_gitignore_atomic_failure_preserves_existing_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            gitignore = Path(temporary) / ".knowledge/.gitignore"
            gitignore.parent.mkdir(parents=True)
            gitignore.write_bytes(b"local-secret/\n")

            with (
                mock.patch("kgdistiller.project.os.replace", side_effect=OSError("injected")),
                self.assertRaisesRegex(OSError, "injected"),
            ):
                ensure_knowledge_gitignore(gitignore)

            self.assertEqual(b"local-secret/\n", gitignore.read_bytes())
            self.assertEqual(
                [".gitignore"],
                sorted(path.name for path in gitignore.parent.iterdir()),
            )


if __name__ == "__main__":
    unittest.main()
