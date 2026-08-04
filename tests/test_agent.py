from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import (  # noqa: E402
    AgentIndexError,
    build_context_bundle,
    compare_graph,
    create_proposal,
    estimate_tokens,
    expand_index,
    get_index_node,
    index_status,
    resolve_concepts,
    retrieve_index,
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
                    "claims": {"dimension": 1},
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


def candidate_snapshot() -> dict:
    payload = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "paper:fixture",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": "b" * 64,
            "counts": {"nodes": 5, "edges": 2, "references": 0},
        },
        "nodes": [
            {
                "id": "alpha",
                "type": "knowledge",
                "label": "Alpha in the paper",
                "text": "A conflicting alpha claim.",
                "entry": {"claims": {"dimension": 2}},
                "properties": {"aliases": []},
            },
            {
                "id": "beta",
                "type": "knowledge",
                "label": "Beta in the paper",
                "text": "The paper's beta entry.",
                "properties": {"aliases": []},
            },
            {
                "id": "novel",
                "type": "knowledge",
                "label": "Novel paper concept",
                "text": "Not present in the personal graph.",
                "properties": {"aliases": []},
            },
            {
                "id": "ambiguous-paper-node",
                "type": "knowledge",
                "label": "Shared concept",
                "text": "The label maps to two personal nodes.",
                "properties": {"aliases": []},
            },
            {
                "id": "paper-alpha",
                "type": "knowledge",
                "label": "First concept",
                "text": "An alias-backed known concept.",
                "properties": {"aliases": []},
            },
        ],
        "edges": [
            {
                "source": "paper-alpha",
                "relation": "prerequisite-for",
                "target": "beta",
                "evidence": "Matches the personal relation.",
            },
            {
                "source": "beta",
                "relation": "derived-from",
                "target": "alpha",
                "evidence": "Missing from the personal graph.",
            },
        ],
        "references": [],
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
            self.assertEqual(0, connection.execute("SELECT count(*) FROM embeddings").fetchone()[0])
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

    def test_typed_expansion_returns_paths_edges_and_backlinks(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        expansion = expand_index(
            self.database,
            ["alpha"],
            direction="outgoing",
            edge_types=["prerequisite-for"],
            max_depth=1,
        )
        self.assertEqual(["alpha", "beta"], [item["node"]["id"] for item in expansion["nodes"]])
        self.assertEqual("prerequisite-for", expansion["nodes"][1]["path"][0]["relation"])
        self.assertEqual(1, len(expansion["edges"]))
        node = get_index_node(self.database, "alpha")
        self.assertEqual(1, len(node["outgoing"]))
        self.assertEqual(1, len(node["backlinks"]))

    def test_retrieval_fuses_fts_with_graph_expansion(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        results = retrieve_index(
            self.database,
            "countable closure",
            max_depth=1,
        )

        self.assertEqual("alpha", results[0]["node"]["id"])
        beta = next(result for result in results if result["node"]["id"] == "beta")
        self.assertTrue(any(reason["method"] == "graph" for reason in beta["reasons"]))

    def test_stale_nodes_are_excluded_unless_policy_allows_them(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["nodes"][1]["properties"]["curation_status"] = "needs-review"
        snapshot["snapshot_sha256"] = sha256_json(
            {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
        )
        write_agent_index(self.database, snapshot)

        default = expand_index(self.database, ["alpha"], max_depth=1)
        allowed = expand_index(
            self.database,
            ["alpha"],
            max_depth=1,
            include_stale=True,
        )

        self.assertEqual(["alpha"], [item["node"]["id"] for item in default["nodes"]])
        self.assertEqual(["alpha", "beta"], [item["node"]["id"] for item in allowed["nodes"]])

    def test_context_bundle_obeys_budget_and_keeps_edge_endpoints(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        bundle = build_context_bundle(
            self.database,
            "countable closure",
            token_budget=5000,
            max_depth=1,
        )

        self.assertEqual("qlkg-context-bundle-v1", bundle["schema"])
        self.assertLessEqual(estimate_tokens(bundle), 5000)
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        node_ids = {node["id"] for node in bundle["nodes"]}
        for edge in bundle["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)
        with self.assertRaisesRegex(AgentIndexError, "budget-too-small"):
            build_context_bundle(self.database, "alpha", token_budget=10)

    def test_candidate_graph_comparison_is_isolated_and_explainable(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        comparison = compare_graph(self.database, candidate_snapshot())

        self.assertEqual("qlkg-graph-comparison-v1", comparison["schema"])
        self.assertEqual(
            {
                "known": 1,
                "partial": 1,
                "new": 1,
                "conflict": 1,
                "uncertain": 1,
                "total": 5,
            },
            comparison["summary"],
        )
        by_id = {item["candidate"]["id"]: item for item in comparison["results"]}
        self.assertEqual("conflict", by_id["alpha"]["status"])
        self.assertEqual("claim", by_id["alpha"]["conflicts"][0]["kind"])
        self.assertEqual("partial", by_id["beta"]["status"])
        self.assertEqual("edge", by_id["beta"]["missing"][0]["kind"])
        self.assertEqual("new", by_id["novel"]["status"])
        self.assertEqual("uncertain", by_id["ambiguous-paper-node"]["status"])
        self.assertEqual("known", by_id["paper-alpha"]["status"])
        with self.assertRaisesRegex(AgentIndexError, "must be distinct"):
            same_namespace = candidate_snapshot()
            same_namespace["namespace"] = "personal"
            same_namespace["snapshot_sha256"] = sha256_json(
                {key: value for key, value in same_namespace.items() if key != "snapshot_sha256"}
            )
            compare_graph(self.database, same_namespace)

    def test_proposal_separates_safe_delta_from_review_blockers(self) -> None:
        write_agent_index(self.database, fixture_snapshot())

        first = create_proposal(
            self.database,
            candidate_snapshot(),
            target_authority="notes/research/paper.md",
        )
        second = create_proposal(
            self.database,
            candidate_snapshot(),
            target_authority="notes/research/paper.md",
        )

        self.assertEqual(first, second)
        self.assertEqual("qlkg-agent-proposal-v1", first["schema"])
        self.assertTrue(first["delta_ready"])
        self.assertFalse(first["fully_resolved"])
        self.assertEqual(
            ["conflict-review-required", "identity-review-required", "source-marker-required"],
            sorted(blocker["code"] for blocker in first["blockers"]),
        )
        operation_names = {operation["op"] for operation in first["operations"]}
        self.assertEqual(
            {"propose-edge", "propose-node", "review-conflict", "review-identity"},
            operation_names,
        )
        new_operation = next(
            operation for operation in first["operations"] if operation["op"] == "propose-node"
        )
        self.assertEqual("notes/research/paper.md", new_operation["target_authority"])
        self.assertEqual("--[[Novel paper concept]]--", new_operation["markers"]["markdown"])
        delta = first["delta_preview"]
        self.assertEqual("qlkg-agent-delta-v2", delta["schema"])
        self.assertEqual([], delta["nodes"])
        self.assertEqual("derived-from", delta["edges"][0]["relation"])
        digest_payload = dict(first)
        digest = digest_payload.pop("proposal_sha256")
        self.assertEqual(sha256_json(digest_payload), digest)

    def test_proposal_cli_writes_review_and_delta_files(self) -> None:
        write_agent_index(self.database, fixture_snapshot())
        candidate = self.root / "paper.snapshot.json"
        candidate.write_text(
            json.dumps(candidate_snapshot(), ensure_ascii=False),
            encoding="utf-8",
        )
        proposal = self.root / "reviews/paper.proposal.json"
        delta = self.root / "reviews/paper.delta.json"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.root),
                "--database",
                str(self.database.relative_to(self.root)),
                "agent",
                "propose",
                str(candidate),
                "--output",
                str(proposal),
                "--delta-output",
                str(delta),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("qlkg-agent-proposal-v1", report["schema"])
        self.assertEqual("qlkg-agent-proposal-v1", json.loads(proposal.read_text())["schema"])
        self.assertEqual("qlkg-agent-delta-v2", json.loads(delta.read_text())["schema"])


if __name__ == "__main__":
    unittest.main()
