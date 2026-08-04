from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import (  # noqa: E402
    AgentIndexError,
    index_status,
    resolve_concepts,
    search_index,
    sha256_json,
    write_agent_index,
)


def fixture_snapshot() -> dict:
    payload = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "personal",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": "a" * 64,
            "counts": {"nodes": 2, "edges": 1, "references": 1},
        },
        "nodes": [
            {
                "id": "alpha",
                "type": "knowledge",
                "label": "Shared concept",
                "text": "A measurable foundation with countable closure.",
                "entry": {
                    "summary": "Alpha summary.",
                    "common_confusions": ["Not the coefficient alpha."],
                },
                "properties": {
                    "aliases": ["First concept", "Α"],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {"authority": "notes/alpha.md", "line": 3},
            },
            {
                "id": "beta",
                "type": "knowledge",
                "label": "Shared concept",
                "text": "A target that depends on alpha.",
                "properties": {
                    "aliases": ["Second concept"],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {"authority": "notes/beta.typ", "line": 8},
            },
        ],
        "edges": [
            {
                "source": "alpha",
                "relation": "prerequisite-for",
                "target": "beta",
                "origin": "agent",
                "confidence": "high",
                "evidence": "Beta explicitly requires alpha.",
                "curation_status": "current",
            }
        ],
        "references": [
            {
                "id": "ref-alpha",
                "target": "alpha",
                "authority": "notes/beta.typ",
                "line": 9,
                "context": "beta",
            }
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


class AgentIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-agent-test-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "nested/knowledge.sqlite"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_index_contains_versioned_nodes_edges_refs_and_fts(self) -> None:
        snapshot = fixture_snapshot()
        write_agent_index(self.database, snapshot)

        status = index_status(self.database)
        self.assertEqual("qlkg-agent-index-v1", status["schema"])
        self.assertEqual(snapshot["snapshot_sha256"], status["snapshot_sha256"])
        self.assertEqual({"nodes": 2, "edges": 1, "references": 1}, status["counts"])
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM edges").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT count(*) FROM refs").fetchone()[0])
            self.assertEqual(
                "Beta explicitly requires alpha.",
                connection.execute("SELECT evidence FROM edges").fetchone()[0],
            )
        finally:
            connection.close()

    def test_batch_resolution_refuses_ambiguity_and_preserves_aliases(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        results = resolve_concepts(
            self.database,
            ["alpha", "First concept", "Shared concept", "unknown"],
        )

        self.assertEqual("exact", results[0]["status"])
        self.assertEqual("alpha", results[0]["matches"][0]["id"])
        self.assertEqual("alias", results[1]["status"])
        self.assertEqual("ambiguous", results[2]["status"])
        self.assertEqual(["alpha", "beta"], [node["id"] for node in results[2]["matches"]])
        self.assertEqual("missing", results[3]["status"])

    def test_fts_searches_structured_entries_and_quotes_user_input(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        results = search_index(self.database, "coefficient alpha")
        self.assertEqual("alpha", results[0]["node"]["id"])
        self.assertEqual("fts", results[0]["reasons"][0]["method"])
        self.assertEqual([], search_index(self.database, 'unknown" OR *'))
        self.assertEqual(2, index_status(self.database)["counts"]["nodes"])

    def test_corrupt_snapshot_is_rejected_before_replacing_index(self) -> None:
        snapshot = fixture_snapshot()
        write_agent_index(self.database, snapshot)
        original = self.database.read_bytes()
        snapshot["nodes"][0]["label"] = "tampered"

        with self.assertRaisesRegex(AgentIndexError, "digest"):
            write_agent_index(self.database, snapshot)

        self.assertEqual(original, self.database.read_bytes())


if __name__ == "__main__":
    unittest.main()
