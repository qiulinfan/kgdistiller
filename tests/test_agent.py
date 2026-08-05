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
    align_graph,
    build_context_bundle,
    compare_graph,
    create_proposal,
    estimate_tokens,
    expand_index,
    get_index_node,
    index_status,
    index_embeddings,
    personalized_pagerank,
    resolve_concepts,
    retrieve_index,
    search_index,
    semantic_search,
    sha256_json,
    write_agent_index,
)
from kgdistiller.alignment import (  # noqa: E402
    ALIGNMENT_SCHEMA,
    extract_scoped_aliases,
    make_reviewed_mapping,
    upsert_mapping,
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


def alignment_fixture_snapshot() -> dict:
    payload = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "personal",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": "c" * 64,
            "counts": {"nodes": 3, "edges": 1, "references": 0},
        },
        "nodes": [
            {
                "id": "absolutely-continuous",
                "type": "knowledge",
                "label": "Absolutely continuous",
                "text": "Absolutely continuous (AC) regularity rules out singular mass.",
                "properties": {"aliases": [], "curation_status": "current"},
            },
            {
                "id": "alternating-current",
                "type": "knowledge",
                "label": "Alternating current",
                "text": "An electric current whose direction changes periodically.",
                "properties": {"aliases": [], "curation_status": "current"},
            },
            {
                "id": "measure",
                "type": "knowledge",
                "label": "Measure",
                "text": "A countably additive set function.",
                "properties": {"aliases": [], "curation_status": "current"},
            },
        ],
        "edges": [
            {
                "source": "absolutely-continuous",
                "relation": "derived-from",
                "target": "measure",
                "origin": "agent",
                "confidence": "high",
                "evidence": "Absolute continuity is defined relative to a measure.",
            }
        ],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def ac_candidate_snapshot() -> dict:
    payload = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "paper:ac",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": "d" * 64,
            "counts": {"nodes": 2, "edges": 1, "references": 0},
        },
        "nodes": [
            {
                "id": "ac",
                "type": "knowledge",
                "label": "AC",
                "text": "AC denotes absolutely continuous.",
                "properties": {"aliases": []},
                "provenance": {"authority": "paper.tex", "line": 12},
            },
            {
                "id": "measure",
                "type": "knowledge",
                "label": "Measure",
                "text": "The underlying measure.",
                "properties": {"aliases": []},
            },
        ],
        "edges": [
            {
                "source": "ac",
                "relation": "derived-from",
                "target": "measure",
                "evidence": "The paper defines AC relative to the measure.",
            }
        ],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


class KeywordEmbeddingProvider:
    name = "fixture"
    model = "keyword-v1"
    dimensions = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.casefold()
            vectors.append(
                [
                    1.0 if "countable" in lowered else 0.05,
                    1.0 if "target" in lowered else 0.05,
                    1.0 if "electric" in lowered else 0.05,
                ]
            )
        return vectors


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
        self.assertEqual("qlkg-agent-index-v2", status["schema"])
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

    def test_explicit_abbreviations_are_scoped_and_evidence_backed(self) -> None:
        snapshot = alignment_fixture_snapshot()
        extracted = extract_scoped_aliases(snapshot)
        write_agent_index(self.database, snapshot)

        self.assertEqual(1, extracted["count"])
        alias = extracted["aliases"][0]
        self.assertEqual("AC", alias["surface"])
        self.assertEqual("Absolutely continuous", alias["expansion"])
        self.assertEqual("parenthetical-abbreviation", alias["evidence"]["kind"])
        resolution = resolve_concepts(self.database, ["AC"])[0]
        self.assertEqual("scoped-alias", resolution["status"])
        self.assertEqual("absolutely-continuous", resolution["matches"][0]["id"])
        self.assertEqual("scoped-alias", resolution["match_kind"])
        self.assertEqual(1, len(resolution["evidence"]))

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM node_names WHERE normalized_name = 'ac'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

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

    def test_ppr_and_embedding_lanes_remain_disposable_retrieval_evidence(self) -> None:
        write_agent_index(self.database, fixture_snapshot())
        provider = KeywordEmbeddingProvider()

        first = index_embeddings(self.database, provider, similarity_threshold=0.0)
        second = index_embeddings(self.database, provider, similarity_threshold=0.0)
        semantic = semantic_search(self.database, "countable closure", provider, limit=2)
        ppr = personalized_pagerank(self.database, {"alpha": 1.0}, limit=2)
        retrieved = retrieve_index(
            self.database,
            "countable closure",
            graph_strategy="hybrid",
            max_depth=1,
        )

        self.assertEqual(2, first["embedded"])
        self.assertEqual(0, second["embedded"])
        self.assertEqual(2, second["cached"])
        self.assertEqual("alpha", semantic[0]["node"]["id"])
        self.assertFalse(semantic[0]["reasons"][0]["identity_authority"])
        self.assertEqual("qlkg-ppr-result-v1", ppr["schema"])
        self.assertEqual("namespace", ppr["policy"]["scope"])
        self.assertGreater(
            next(item["score"] for item in ppr["results"] if item["node"]["id"] == "beta"),
            0.0,
        )
        bounded = personalized_pagerank(
            self.database,
            {"alpha": 1.0},
            limit=2,
            _candidate_ids={"alpha", "beta"},
        )
        self.assertEqual("bounded-neighborhood", bounded["policy"]["scope"])
        self.assertEqual(2, bounded["policy"]["scope_nodes"])
        beta = next(item for item in retrieved if item["node"]["id"] == "beta")
        methods = {reason["method"] for reason in beta["reasons"]}
        self.assertEqual({"graph", "ppr"}, methods)

    def test_index_rebuild_preserves_only_embeddings_with_current_input_digest(self) -> None:
        snapshot = fixture_snapshot()
        write_agent_index(self.database, snapshot)
        index_embeddings(
            self.database,
            KeywordEmbeddingProvider(),
            build_similarity_edges=False,
        )
        connection = sqlite3.connect(self.database)
        try:
            before = connection.execute(
                "SELECT node_id, vector FROM embeddings ORDER BY node_id"
            ).fetchall()
        finally:
            connection.close()

        write_agent_index(self.database, snapshot)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                before,
                connection.execute(
                    "SELECT node_id, vector FROM embeddings ORDER BY node_id"
                ).fetchall(),
            )
        finally:
            connection.close()

        snapshot["nodes"][0]["label"] = "Changed embedding input"
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                ["beta"],
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT node_id FROM embeddings ORDER BY node_id"
                    ).fetchall()
                ],
            )
        finally:
            connection.close()

    def test_ppr_does_not_rank_unreachable_zero_mass_nodes(self) -> None:
        write_agent_index(self.database, alignment_fixture_snapshot())

        ppr = personalized_pagerank(self.database, {"measure": 1.0}, limit=10)

        ranked_ids = {item["node"]["id"] for item in ppr["results"]}
        self.assertEqual({"measure", "absolutely-continuous"}, ranked_ids)
        self.assertNotIn("alternating-current", ranked_ids)

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

    def test_alignment_combines_scoped_alias_acronym_and_graph_consistency(self) -> None:
        write_agent_index(self.database, alignment_fixture_snapshot())

        report = align_graph(self.database, ac_candidate_snapshot())
        by_id = {item["candidate"]["id"]: item for item in report["results"]}
        ac = by_id["ac"]

        self.assertEqual("qlkg-alignment-report-v1", report["schema"])
        self.assertEqual("ambiguous", ac["status"])
        self.assertIsNone(ac["identity_target_id"])
        self.assertEqual(
            ["absolutely-continuous", "alternating-current"],
            [item["target"]["id"] for item in ac["candidates"]],
        )
        top_signals = {signal["kind"] for signal in ac["candidates"][0]["signals"]}
        self.assertIn("explicit-scoped-alias", top_signals)
        self.assertIn("acronym-candidate", top_signals)
        graph_signal = next(
            signal
            for signal in ac["candidates"][0]["signals"]
            if signal["kind"] == "graph-consistency"
        )
        self.assertEqual({"matched": 1, "checked": 1}, {
            "matched": graph_signal["matched"],
            "checked": graph_signal["checked"],
        })
        comparison = compare_graph(self.database, ac_candidate_snapshot())
        compared_ac = next(
            item for item in comparison["results"] if item["candidate"]["id"] == "ac"
        )
        self.assertEqual("uncertain", compared_ac["status"])
        self.assertEqual(report["report_sha256"], comparison["alignment_report_sha256"])

        dangling = json.loads(json.dumps(ac_candidate_snapshot()))
        dangling["edges"][0]["target"] = "missing"
        dangling["snapshot_sha256"] = sha256_json(
            {key: value for key, value in dangling.items() if key != "snapshot_sha256"}
        )
        with self.assertRaisesRegex(AgentIndexError, "dangling edge"):
            align_graph(self.database, dangling)

    def test_reviewed_alignment_is_hard_only_while_endpoint_fingerprints_are_fresh(self) -> None:
        target_snapshot = alignment_fixture_snapshot()
        candidate = ac_candidate_snapshot()
        target_node = next(
            node for node in target_snapshot["nodes"] if node["id"] == "absolutely-continuous"
        )
        candidate_node = next(node for node in candidate["nodes"] if node["id"] == "ac")
        mapping = make_reviewed_mapping(
            subject_namespace="paper:ac",
            subject_node=candidate_node,
            predicate="exact-match",
            object_namespace="personal",
            object_node=target_node,
            status="reviewed",
            justification="paper-defines-the-same-mathematical-concept",
            evidence="The paper explicitly expands AC and uses the same measure relation.",
        )
        write_agent_index(
            self.database,
            target_snapshot,
            {"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]},
        )

        reviewed = align_graph(self.database, candidate)
        reviewed_ac = next(
            item for item in reviewed["results"] if item["candidate"]["id"] == "ac"
        )
        self.assertEqual("exact", reviewed_ac["status"])
        self.assertEqual("absolutely-continuous", reviewed_ac["identity_target_id"])
        comparison = compare_graph(self.database, candidate)
        compared_ac = next(
            item for item in comparison["results"] if item["candidate"]["id"] == "ac"
        )
        self.assertEqual("known", compared_ac["status"])
        proposal = create_proposal(
            self.database,
            candidate,
            target_authority="notes/research/ac-paper.md",
        )
        self.assertTrue(proposal["fully_resolved"])
        self.assertEqual([], proposal["blockers"])
        self.assertEqual([], proposal["delta_preview"]["nodes"])
        self.assertEqual([], proposal["delta_preview"]["edges"])

        changed = json.loads(json.dumps(candidate))
        changed["nodes"][0]["text"] += " Updated by a later paper revision."
        changed["snapshot_sha256"] = sha256_json(
            {key: value for key, value in changed.items() if key != "snapshot_sha256"}
        )
        stale = align_graph(self.database, changed)
        stale_ac = next(item for item in stale["results"] if item["candidate"]["id"] == "ac")
        self.assertNotEqual("exact", stale_ac["status"])
        registry = stale_ac["registry_evidence"][0]
        self.assertFalse(registry["freshness"]["subject_fresh"])
        self.assertFalse(registry["identity_authority"])

        refreshed_mapping = make_reviewed_mapping(
            subject_namespace="paper:ac",
            subject_node=changed["nodes"][0],
            predicate="exact-match",
            object_namespace="personal",
            object_node=target_node,
            status="reviewed",
            justification="reviewed-again-after-paper-revision",
            evidence="The revised paper still defines the same mathematical concept.",
        )
        refreshed_registry = upsert_mapping(
            {"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]},
            refreshed_mapping,
        )
        self.assertEqual(mapping["id"], refreshed_mapping["id"])
        self.assertEqual(1, len(refreshed_registry["mappings"]))
        self.assertEqual(
            refreshed_mapping["subject"]["node_sha256"],
            refreshed_registry["mappings"][0]["subject"]["node_sha256"],
        )

    def test_rejected_alignment_removes_that_target_from_candidates(self) -> None:
        target_snapshot = alignment_fixture_snapshot()
        target_snapshot["nodes"][0]["properties"]["aliases"] = ["AC"]
        target_snapshot["snapshot_sha256"] = sha256_json(
            {
                key: value
                for key, value in target_snapshot.items()
                if key != "snapshot_sha256"
            }
        )
        candidate = ac_candidate_snapshot()
        target_node = next(
            node for node in target_snapshot["nodes"] if node["id"] == "absolutely-continuous"
        )
        candidate_node = next(node for node in candidate["nodes"] if node["id"] == "ac")
        mapping = make_reviewed_mapping(
            subject_namespace="paper:ac",
            subject_node=candidate_node,
            predicate="exact-match",
            object_namespace="personal",
            object_node=target_node,
            status="rejected",
            justification="domain-review-rejected-this-sense",
            evidence="The reviewer determined that AC denotes a different local construct.",
        )
        write_agent_index(
            self.database,
            target_snapshot,
            {"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]},
        )

        report = align_graph(self.database, candidate)
        ac = next(item for item in report["results"] if item["candidate"]["id"] == "ac")
        self.assertIn("absolutely-continuous", ac["rejected_target_ids"])
        self.assertNotIn(
            "absolutely-continuous",
            [item["target"]["id"] for item in ac["candidates"]],
        )

        changed = json.loads(json.dumps(candidate))
        changed["nodes"][0]["text"] += " This revision changes the local construct."
        changed["snapshot_sha256"] = sha256_json(
            {key: value for key, value in changed.items() if key != "snapshot_sha256"}
        )
        reconsidered = align_graph(self.database, changed)
        reconsidered_ac = next(
            item for item in reconsidered["results"] if item["candidate"]["id"] == "ac"
        )
        self.assertNotIn("absolutely-continuous", reconsidered_ac["rejected_target_ids"])
        self.assertIn(
            "absolutely-continuous",
            [item["target"]["id"] for item in reconsidered_ac["candidates"]],
        )

    def test_alignment_registry_rejects_multiple_reviewed_exact_targets(self) -> None:
        target_snapshot = alignment_fixture_snapshot()
        candidate_node = ac_candidate_snapshot()["nodes"][0]
        first = make_reviewed_mapping(
            subject_namespace="paper:ac",
            subject_node=candidate_node,
            predicate="exact-match",
            object_namespace="personal",
            object_node=target_snapshot["nodes"][0],
            status="reviewed",
            justification="first-review",
            evidence="First target review evidence.",
        )
        second = make_reviewed_mapping(
            subject_namespace="paper:ac",
            subject_node=candidate_node,
            predicate="exact-match",
            object_namespace="personal",
            object_node=target_snapshot["nodes"][1],
            status="reviewed",
            justification="conflicting-second-review",
            evidence="Second target review evidence.",
        )

        with self.assertRaisesRegex(AgentIndexError, "multiple reviewed exact targets"):
            write_agent_index(
                self.database,
                target_snapshot,
                {"schema": ALIGNMENT_SCHEMA, "mappings": [first, second]},
            )

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

    def test_alignment_cli_writes_reviewable_report(self) -> None:
        write_agent_index(self.database, alignment_fixture_snapshot())
        candidate = self.root / "paper.snapshot.json"
        candidate.write_text(json.dumps(ac_candidate_snapshot()), encoding="utf-8")
        output = self.root / "reviews/paper.alignment.json"
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
                "align",
                str(candidate),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        summary = json.loads(result.stdout)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("qlkg-alignment-report-v1", summary["schema"])
        self.assertEqual("qlkg-alignment-report-v1", report["schema"])
        self.assertEqual(1, report["summary"]["ambiguous"])

    def test_ppr_cli_returns_ranked_graph_result(self) -> None:
        write_agent_index(self.database, fixture_snapshot())
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
                "ppr",
                "alpha",
                "--limit",
                "2",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("qlkg-ppr-result-v1", report["schema"])
        self.assertEqual(
            {"alpha", "beta"},
            {item["node"]["id"] for item in report["results"]},
        )


if __name__ == "__main__":
    unittest.main()
