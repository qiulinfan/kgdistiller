from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import ContractError, canonical_json, validate_contract
from kgdistiller.graph_retrieval import GraphRetrievalPolicy
from kgdistiller.query import GraphView, personalized_pagerank
from kgdistiller.retrieval import RetrievalError, build_context_from_execution, execute_retrieval_plan
from kgdistiller.semantic_retrieval import SemanticRankingService
from tests.test_query import fixture_edges, fixture_nodes, snapshot_with
from tests.test_retrieval import retrieval_plan
from tests.test_semantic_retrieval import FakeReranker


def graph_fixture() -> GraphView:
    edges = fixture_edges()
    for edge in edges:
        edge["confidence"] = "high"
    return GraphView.from_snapshot(snapshot_with(fixture_nodes(), edges))


def candidate_plan() -> dict:
    plan = retrieval_plan()
    plan["identity_queries"] = ["countably additive"]
    plan["lexical_queries"] = ["countably additive"]
    plan["graph"].update(strategy="bfs", max_depth=1)
    return plan


class GraphRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = graph_fixture()
        self.policy = GraphRetrievalPolicy(candidate_limit=1)

    def execute(self, plan: dict | None = None, **kwargs) -> dict:
        return execute_retrieval_plan(self.view, plan or candidate_plan(), graph_policy=self.policy, **kwargs)

    def test_candidates_find_neighbors_without_promoting_identity(self) -> None:
        original_plan, original_snapshot = candidate_plan(), copy.deepcopy(self.view.snapshot)
        execution = self.execute(original_plan)
        self.assertEqual("kgdistiller-search-execution-v3", execution["schema"])
        self.assertEqual("missing", execution["identity_resolutions"][0]["status"])
        graph = execution["result"]["graph_retrieval"]
        self.assertEqual([], graph["seeds"]["explicit"])
        self.assertEqual([], graph["seeds"]["identity"])
        candidate = graph["seeds"]["candidate"][0]
        self.assertEqual("measure", candidate["node_id"])
        self.assertFalse(candidate["identity_authority"])
        self.assertEqual({"lexical"}, set(candidate["lanes"]))
        self.assertEqual(original_plan, candidate_plan())
        self.assertEqual(original_snapshot, self.view.snapshot)
        rows = {row["node_id"]: row for row in execution["result"]["results"]}
        self.assertEqual({"lexical"}, set(rows["measure"]["lanes"]))
        neighbor = execution["result"]["graph_retrieval"]["neighbors"][0]
        self.assertEqual({"graph"}, set(neighbor["lanes"]))
        self.assertEqual("candidate", neighbor["seed_evidence"][0]["origin"])
        step = neighbor["path_evidence"][0]["steps"][0]
        self.assertEqual("learning-prerequisite", step["purpose"])
        self.assertEqual("outgoing", step["direction"])
        self.assertEqual("high", step["confidence"])
        self.assertIn("compares measures", step["evidence"])
        self.assertFalse(step["logical_entailment"])
        self.assertEqual(execution, validate_contract(execution))

    def test_explicit_identity_candidate_origins_remain_separate(self) -> None:
        plan = candidate_plan()
        plan["identity_queries"] = ["Sigma algebra"]
        plan["graph"]["seed_ids"] = ["absolute-continuity"]
        execution = self.execute(plan)
        seeds = execution["result"]["graph_retrieval"]["seeds"]
        self.assertEqual(["absolute-continuity"], seeds["explicit"])
        self.assertEqual(["sigma-algebra"], seeds["identity"])
        self.assertEqual(["measure"], [row["node_id"] for row in seeds["candidate"]])
        self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
        self.assertTrue(all(not (set(row["lanes"]) & {"graph", "ppr"}) for row in execution["result"]["results"]))

    def test_high_confidence_gate_blocks_unverified_and_stale_edges(self) -> None:
        edges = fixture_edges()
        edges[1]["confidence"] = "unverified"
        self.view = GraphView.from_snapshot(snapshot_with(fixture_nodes(), edges))
        strict = self.execute()
        self.assertEqual(0, strict["result"]["lanes"]["graph"]["results"])
        exploratory = execute_retrieval_plan(self.view, candidate_plan(), graph_policy=GraphRetrievalPolicy(1, "current"))
        neighbor = next(row for row in exploratory["result"]["graph_retrieval"]["neighbors"] if row["node_id"] == "absolute-continuity")
        self.assertEqual("unverified", neighbor["path_evidence"][0]["steps"][0]["confidence"])
        edges[1].update(confidence="high", curation_status="needs-review")
        self.view = GraphView.from_snapshot(snapshot_with(fixture_nodes(), edges))
        plan = candidate_plan(); plan["filters"]["include_stale"] = True
        self.assertEqual(0, self.execute(plan)["result"]["lanes"]["graph"]["results"])

    def test_candidate_universe_filters_before_seed_selection(self) -> None:
        nodes = fixture_nodes()
        nodes[1]["properties"]["curation_status"] = "needs-review"
        self.view = GraphView.from_snapshot(snapshot_with(nodes, []))
        execution = self.execute()
        self.assertEqual([], execution["result"]["graph_retrieval"]["seeds"]["candidate"])

    def test_ppr_diagnostics_and_no_root_self_boost(self) -> None:
        plan = candidate_plan(); plan["graph"]["strategy"] = "hybrid"
        execution = self.execute(plan)
        ppr = execution["result"]["graph_retrieval"]["ppr"]
        self.assertTrue(ppr["converged"])
        self.assertLessEqual(ppr["final_l1_residual"], ppr["tolerance"])
        self.assertAlmostEqual(1, ppr["probability_mass"])
        for row in execution["result"]["results"]:
            if row["node_id"] == "measure":
                self.assertEqual({"lexical"}, set(row["lanes"]))
        forced = personalized_pagerank(self.view, {"measure": 1}, direction="out", max_depth=1, max_iterations=1)
        with patch("kgdistiller.graph_retrieval.personalized_pagerank", return_value=forced):
            degraded = self.execute(plan)
        self.assertEqual("degraded", degraded["result"]["lanes"]["ppr"]["status"])
        self.assertEqual(0, degraded["result"]["lanes"]["ppr"]["results"])
        self.assertTrue(all("ppr" not in row["lanes"] for row in degraded["result"]["results"]))

    def test_plan_depth_and_incoming_direction_are_preserved_in_paths(self) -> None:
        plan = candidate_plan(); plan["graph"].update(seed_ids=["absolute-continuity"], direction="in", max_depth=1)
        plan["identity_queries"] = []; plan["lexical_queries"] = []
        execution = self.execute(plan)
        self.assertEqual(["measure"], [row["node_id"] for row in execution["result"]["results"]])
        path = execution["result"]["results"][0]["path_evidence"][0]
        self.assertEqual(["absolute-continuity", "measure"], path["nodes"])
        self.assertEqual("incoming", path["steps"][0]["direction"])

    def test_model_reranks_base_pool_alongside_graph_support_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeReranker()
            service = SemanticRankingService(adapter, cache_dir=Path(temp), rerank=True)
            execution = self.execute(ranking_service=service)
        self.assertEqual({"embedding", "reranker"}, set(execution["result"]["ranking"]))
        self.assertTrue(any("Absolute continuity" in text for _, docs in adapter.pair_calls for text in docs))
        candidate = execution["result"]["graph_retrieval"]["seeds"]["candidate"][0]
        self.assertEqual({"lexical", "embedding"}, set(candidate["lanes"]))
        self.assertEqual(execution, validate_contract(execution))

    def test_graph_support_never_boosts_relevance_or_changes_base_reranker_pool(self) -> None:
        plan = candidate_plan()
        base = execute_retrieval_plan(self.view, plan)
        graph = self.execute(plan)
        self.assertEqual(base["result"]["results"], graph["result"]["results"])
        neighbors = graph["result"]["graph_retrieval"]["neighbors"]
        self.assertEqual(["absolute-continuity"], [row["node_id"] for row in neighbors])
        self.assertTrue(all(row["fusion"]["method"] == "navigation" and row["fusion"]["score"] == 0 for row in neighbors))
        plan["limit"] = 1
        with tempfile.TemporaryDirectory() as temp:
            base_adapter, graph_adapter = FakeReranker(), FakeReranker()
            base_service = SemanticRankingService(base_adapter, cache_dir=Path(temp) / "base", rerank=True, candidate_limit=1)
            graph_service = SemanticRankingService(graph_adapter, cache_dir=Path(temp) / "graph", rerank=True, candidate_limit=1)
            base = execute_retrieval_plan(self.view, plan, ranking_service=base_service)
            graph = self.execute(plan, ranking_service=graph_service)
        self.assertEqual(base["result"]["results"], graph["result"]["results"])
        self.assertEqual(base_adapter.pair_calls, graph_adapter.pair_calls)
        self.assertEqual(base["result"]["ranking"]["reranker"], graph["result"]["ranking"]["reranker"])
        self.assertEqual(1, len(graph["result"]["graph_retrieval"]["neighbors"]))
        context = build_context_from_execution(self.view, graph, plan=plan, token_budget=10000)
        self.assertTrue(any(packet["node_id"] == "absolute-continuity" and packet["kind"] == "graph-path" for packet in context["support_packets"]))

    def test_two_recalled_roots_preserve_comparison_support_without_rank_boost(self) -> None:
        nodes = fixture_nodes()[1:]
        nodes[0]["properties"]["conditions"] = ["A measure is defined on measurable sets."]
        nodes[1]["properties"]["conditions"] = ["Absolute continuity is relative to a second measure."]
        edge = {"source": "measure", "relation": "contrasts-with", "target": "absolute-continuity", "confidence": "high", "curation_status": "current", "evidence": "These are distinct source-defined objects with different conditions."}
        self.view = GraphView.from_snapshot(snapshot_with(nodes, [edge]))
        plan = candidate_plan()
        plan["identity_queries"] = []
        plan["lexical_queries"] = ["countably additive", "relative to a measure"]
        plan["graph"]["edge_types"] = ["contrasts-with"]
        policy = GraphRetrievalPolicy(candidate_limit=2)
        base = execute_retrieval_plan(self.view, plan)
        graph = execute_retrieval_plan(self.view, plan, graph_policy=policy)
        self.assertEqual(base["result"]["results"], graph["result"]["results"])
        neighbors = graph["result"]["graph_retrieval"]["neighbors"]
        self.assertEqual({"measure", "absolute-continuity"}, {row["node_id"] for row in neighbors})
        self.assertTrue(all(path["nodes"][0] != row["node_id"] and len(path["steps"]) == 1 for row in neighbors for path in row["path_evidence"]))
        context = build_context_from_execution(self.view, graph, plan=plan, token_budget=10000)
        self.assertEqual([edge], context["edges"])
        self.assertTrue(all(node["properties"]["conditions"] for node in context["nodes"]))
        self.assertEqual(2, sum(packet["kind"] == "graph-path" for packet in context["support_packets"]))
        with tempfile.TemporaryDirectory() as temp:
            base_adapter, graph_adapter = FakeReranker(), FakeReranker()
            base_service = SemanticRankingService(base_adapter, cache_dir=Path(temp) / "base", rerank=True, candidate_limit=1)
            graph_service = SemanticRankingService(graph_adapter, cache_dir=Path(temp) / "graph", rerank=True, candidate_limit=1)
            base = execute_retrieval_plan(self.view, plan, ranking_service=base_service)
            graph = execute_retrieval_plan(self.view, plan, ranking_service=graph_service, graph_policy=policy)
        self.assertEqual(base["result"]["results"], graph["result"]["results"])
        self.assertEqual(base_adapter.pair_calls, graph_adapter.pair_calls)
        self.assertEqual(base["result"]["ranking"]["reranker"], graph["result"]["ranking"]["reranker"])

    def test_context_keeps_complete_path_or_reports_a_gap(self) -> None:
        execution = self.execute()
        full = build_context_from_execution(self.view, execution, plan=candidate_plan(), token_budget=10000)
        self.assertEqual("kgdistiller-context-bundle-v2", full["schema"])
        packet = next(item for item in full["support_packets"] if item["kind"] == "graph-path")
        self.assertEqual(["measure", "absolute-continuity"], packet["nodes"])
        self.assertEqual(1, len(full["edges"]))
        self.assertEqual(full, validate_contract(full))
        tight = build_context_from_execution(self.view, execution, plan=candidate_plan(), token_budget=1400)
        self.assertFalse(any(item["kind"] == "graph-path" for item in tight["support_packets"]))
        self.assertEqual([], tight["edges"])
        self.assertGreater(tight["omitted_support_packets"], 0)
        self.assertTrue(tight["gaps"] or tight["diagnostics_truncated"])
        self.assertTrue(all(node["id"] != "absolute-continuity" for node in tight["nodes"]))
        for budget in range(750, 3000, 11):
            try:
                context = build_context_from_execution(self.view, execution, plan=candidate_plan(), token_budget=budget)
            except RetrievalError as error:
                self.assertIn("metadata", error.message)
                continue
            self.assertLessEqual(len(canonical_json(context).encode()), budget)
            self.assertEqual(context, validate_contract(context))

    def test_stale_generation_and_tampered_paths_fail_closed(self) -> None:
        execution = self.execute()
        invalid = copy.deepcopy(execution)
        invalid["result"]["graph_retrieval"]["neighbors"][0]["path_evidence"][0]["steps"][0]["direction"] = "incoming"
        with self.assertRaisesRegex(ContractError, "direction"):
            validate_contract(invalid)
        invalid = copy.deepcopy(execution)
        invalid["result"]["graph_retrieval"]["seeds"]["candidate"][0]["identity_authority"] = True
        with self.assertRaises(ContractError):
            validate_contract(invalid)
        self.view.nodes["measure"]["text"] += " changed"
        with self.assertRaisesRegex(RetrievalError, "stale-generation"):
            build_context_from_execution(self.view, execution, plan=candidate_plan(), token_budget=10000)

    def test_no_policy_preserves_v1_v2_schema_families(self) -> None:
        plain = execute_retrieval_plan(self.view, candidate_plan())
        self.assertEqual("kgdistiller-search-execution-v1", plain["schema"])
        self.assertNotIn("graph_retrieval", plain["result"])
        with tempfile.TemporaryDirectory() as temp:
            service = SemanticRankingService(FakeReranker(), cache_dir=Path(temp))
            model = execute_retrieval_plan(self.view, candidate_plan(), ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", model["schema"])
        self.assertNotIn("graph_retrieval", model["result"])

    def test_legacy_ppr_respects_the_supplied_depth_bound(self) -> None:
        plan = candidate_plan()
        plan["identity_queries"] = []; plan["lexical_queries"] = []
        plan["graph"].update(seed_ids=["sigma-algebra"], strategy="ppr", max_depth=1)
        one_hop = execute_retrieval_plan(self.view, plan)
        self.assertEqual({"sigma-algebra", "measure"}, {row["node_id"] for row in one_hop["result"]["results"]})
        plan["graph"]["max_depth"] = 0
        zero_hops = execute_retrieval_plan(self.view, plan)
        self.assertEqual(["sigma-algebra"], [row["node_id"] for row in zero_hops["result"]["results"]])

    def test_forged_origin_edge_evidence_and_plan_policy_fail_closed(self) -> None:
        execution = self.execute()
        forged = copy.deepcopy(execution)
        seeds = forged["result"]["graph_retrieval"]["seeds"]
        seeds["identity"] = [seeds["candidate"][0]["node_id"]]
        for row in forged["result"]["graph_retrieval"]["neighbors"]:
            for evidence in row["seed_evidence"]:
                evidence.update(origin="identity", identity_authority=True)
        with self.assertRaisesRegex(ContractError, "authoritative exact"):
            validate_contract(forged)
        forged["identity_resolutions"][0].update(status="exact", match_kind="label", candidate_ids=["measure"], identity_authority=True)
        with self.assertRaisesRegex(RetrievalError, "source names and plan"):
            build_context_from_execution(self.view, forged, plan=candidate_plan(), token_budget=10000)
        forged = copy.deepcopy(execution)
        forged["result"]["graph_retrieval"]["neighbors"][0]["path_evidence"][0]["steps"][0]["evidence"] = "Forged scientific claim."
        with self.assertRaisesRegex(RetrievalError, "evidence does not match"):
            build_context_from_execution(self.view, forged, plan=candidate_plan(), token_budget=10000)
        forged = copy.deepcopy(execution)
        forged["result"]["graph_retrieval"]["policy"]["max_depth"] = 8
        with self.assertRaisesRegex(RetrievalError, "depth does not match"):
            build_context_from_execution(self.view, forged, plan=candidate_plan(), token_budget=10000)
        forged = copy.deepcopy(execution)
        forged["result"]["graph_retrieval"]["seeds"]["candidate"][0]["document_sha256"] = "0" * 64
        with self.assertRaisesRegex(RetrievalError, "candidate graph root"):
            build_context_from_execution(self.view, forged, plan=candidate_plan(), token_budget=10000)
        full = build_context_from_execution(self.view, execution, plan=candidate_plan(), token_budget=10000)
        full["support_packets"][0]["path"]["steps"][0]["evidence"] = "Forged scientific claim."
        with self.assertRaisesRegex(ContractError, "do not match the source edge"):
            validate_contract(full)

    def test_reverse_contrast_navigation_has_actual_incoming_proof(self) -> None:
        edges = fixture_edges()
        edges[1].update(relation="contrasts-with", confidence="high")
        self.view = GraphView.from_snapshot(snapshot_with(fixture_nodes(), edges))
        plan = candidate_plan()
        plan["graph"].update(seed_ids=["absolute-continuity"], edge_types=["contrasts-with"], direction="out", strategy="hybrid")
        plan["identity_queries"] = []; plan["lexical_queries"] = []
        execution = self.execute(plan)
        row = execution["result"]["results"][0]
        self.assertEqual("measure", row["node_id"])
        self.assertEqual("incoming", row["path_evidence"][0]["steps"][0]["direction"])
        bundle = build_context_from_execution(self.view, execution, plan=plan, token_budget=10000)
        self.assertEqual(bundle, validate_contract(bundle))

    def test_policy_bounds_and_zero_depth_are_explicit(self) -> None:
        for value in (0, 33, True, 1.5):
            with self.assertRaises(ValueError):
                GraphRetrievalPolicy(candidate_limit=value)
        with self.assertRaises(ValueError):
            GraphRetrievalPolicy(edge_policy="reviewed")
        plan = candidate_plan(); plan["graph"]["max_depth"] = 0
        execution = self.execute(plan)
        self.assertEqual(0, execution["result"]["lanes"]["graph"]["results"])
        self.assertTrue(all(not row["path_evidence"] for row in execution["result"]["results"]))


if __name__ == "__main__":
    unittest.main()
