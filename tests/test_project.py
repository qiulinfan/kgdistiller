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

from kgdistiller.project import ensure_knowledge_gitignore, initialize_project  # noqa: E402


class ProjectInitializationTest(unittest.TestCase):
    def test_init_creates_empty_alignment_registry_without_erasing_review_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            root = Path(temporary)
            registry = root / "knowledge/sources.json"
            alignments = root / "knowledge/alignments.json"

            initialize_project(
                root,
                registry,
                source_root=Path("notes"),
                alignments=alignments,
            )

            self.assertEqual(
                {"schema": "qlkg-alignments-v1", "mappings": []},
                json.loads(alignments.read_text(encoding="utf-8")),
            )
            self.assertEqual(
                "build/\n",
                (root / "knowledge/.gitignore").read_text(encoding="utf-8"),
            )
            reviewed = {
                "schema": "qlkg-alignments-v1",
                "mappings": [{"preserved": True}],
            }
            alignments.write_text(json.dumps(reviewed), encoding="utf-8")
            gitignore = root / "knowledge/.gitignore"
            gitignore.write_text("build/\nlocal-secret/\n", encoding="utf-8")
            initialize_project(
                root,
                registry,
                source_root=Path("notes"),
                alignments=alignments,
                force=True,
            )
            self.assertEqual(reviewed, json.loads(alignments.read_text(encoding="utf-8")))
            self.assertEqual(
                "build/\nlocal-secret/\n",
                gitignore.read_text(encoding="utf-8"),
            )

    def test_existing_gitignore_is_extended_atomically_and_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            gitignore = Path(temporary) / "knowledge/.gitignore"
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

    def test_custom_registry_still_ignores_the_default_local_profile(self) -> None:
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
                (root / "knowledge/.gitignore").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "build/\n",
                (root / "config/.gitignore").read_text(encoding="utf-8"),
            )

    def test_gitignore_atomic_failure_preserves_existing_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-project-test-") as temporary:
            gitignore = Path(temporary) / "knowledge/.gitignore"
            gitignore.parent.mkdir(parents=True)
            gitignore.write_bytes(b"local-secret/\n")

            with mock.patch("kgdistiller.project.os.replace", side_effect=OSError("injected")):
                with self.assertRaisesRegex(OSError, "injected"):
                    ensure_knowledge_gitignore(gitignore)

            self.assertEqual(b"local-secret/\n", gitignore.read_bytes())
            self.assertEqual(
                [".gitignore"],
                sorted(path.name for path in gitignore.parent.iterdir()),
            )


if __name__ == "__main__":
    unittest.main()
