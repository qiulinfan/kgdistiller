from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.project import initialize_project  # noqa: E402


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
            reviewed = {
                "schema": "qlkg-alignments-v1",
                "mappings": [{"preserved": True}],
            }
            alignments.write_text(json.dumps(reviewed), encoding="utf-8")
            initialize_project(
                root,
                registry,
                source_root=Path("notes"),
                alignments=alignments,
                force=True,
            )
            self.assertEqual(reviewed, json.loads(alignments.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
