from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.web import load_graph_payload, source_excerpt  # noqa: E402


class WebPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-web-")
        self.root = Path(self.temporary.name)
        self.graph = self.root / "knowledge/graph"
        self.graph.mkdir(parents=True)
        shard = "entries/by-source/notes/demo.md.jsonl"
        (self.graph / shard).parent.mkdir(parents=True)
        (self.graph / shard).write_text(
            json.dumps({"id": "demo", "text": "Hydrated entry"}) + "\n",
            encoding="utf-8",
        )
        (self.graph / "manifest.json").write_text(
            json.dumps({"entry_store": {"shards": [{"path": shard}]}}),
            encoding="utf-8",
        )
        (self.graph / "nodes.jsonl").write_text(
            json.dumps({"id": "demo", "type": "knowledge", "label": "Demo"}) + "\n",
            encoding="utf-8",
        )
        for name in ("edges.jsonl", "references.jsonl"):
            (self.graph / name).write_text("", encoding="utf-8")
        (self.graph / "diagnostics.json").write_text(
            json.dumps({"errors": [], "warnings": []}), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_entry_shards_are_hydrated(self) -> None:
        payload = load_graph_payload(self.graph)
        self.assertEqual("Hydrated entry", payload["nodes"][0]["text"])

    def test_source_excerpt_is_bounded_to_project(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text("one\ntwo\nthree\n", encoding="utf-8")
        excerpt = source_excerpt(self.root, "notes/demo.md", 2, radius=1)
        self.assertEqual([1, 2, 3], [line["number"] for line in excerpt["lines"]])
        with self.assertRaisesRegex(ValueError, "outside"):
            source_excerpt(self.root, "../private.md", 1)


if __name__ == "__main__":
    unittest.main()
