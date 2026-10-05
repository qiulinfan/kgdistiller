from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kgdistiller.alignment import node_fingerprint
from kgdistiller.context_projection import (
    build_compact_context, node_record, project_node, validate_compact_context,
)
from kgdistiller.contracts import ContractError, canonical_json, sha256_json, validate_contract
from kgdistiller.graph_retrieval import GraphRetrievalPolicy, view_content_sha256
from kgdistiller.retrieval import RetrievalError, build_context_from_execution, execute_retrieval_plan
from kgdistiller.query import GraphView
from kgdistiller.semantic_retrieval import SemanticRankingService, search_document
from tests.test_graph_retrieval import candidate_plan, graph_fixture
from tests.test_query import fixture_nodes, snapshot_with
from tests.test_semantic_retrieval import FakeReranker


def repeated_fixture() -> tuple[GraphView, dict]:
    nodes = fixture_nodes()[1:]
    for index, node in enumerate(nodes):
        node["text"] = ("完整定义保持每个限定条件。 " if index == 0 else "Complete definition preserves every qualification. ") * 16
        node["properties"].update({
            "conditions": ["Only on source-defined objects.", "训练和推理阶段不能混合。"],
            "unknown_semantic_contract": {"input": "selected target", "output": "conditional probability", "exception": ["finite probe accuracy does not imply population independence"]},
            "entry_sha256": "a" * 64, "entry_source_sha256": "b" * 64,
            "entry_source_current_sha256": "c" * 64,
            "curated_definition_sha256": "d" * 64,
            "model_declared_source_sha256": "e" * 64, "source_sha256": "e" * 64,
            "entry_authority": "knowledge/entries/original.md",
        })
        node["provenance"]["definition_sha256"] = "d" * 64
        node["entry"] = {"summary": node["text"], "context": copy.deepcopy(node["properties"]["conditions"]), "body": "A source-authored claim with its own wording.", "sources": ["https://example.org/original-v2"], "unknown_entry_semantics": {"phase": "train", "operator": "conditional expectation"}}
    edge = {"source": "measure", "relation": "contrasts-with", "target": "absolute-continuity", "confidence": "high", "curation_status": "current", "evidence": "Original evidence, retained in full exactly once. " * 30}
    view = GraphView.from_snapshot(snapshot_with(nodes, [edge]))
    plan = candidate_plan(); plan["identity_queries"] = []
    plan["lexical_queries"] = ["完整定义保持每个限定条件", "Complete definition"]
    plan["graph"]["edge_types"] = ["contrasts-with"]
    return view, plan


class ContextProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view, self.plan = repeated_fixture()
        self.execution = execute_retrieval_plan(self.view, self.plan, graph_policy=GraphRetrievalPolicy(candidate_limit=2))

    def build(self, budget=12000) -> dict:
        return build_compact_context(self.view, self.execution, self.plan, budget)

    def test_selected_support_is_prioritized_without_rewriting_answer_execution(self) -> None:
        from kgdistiller.support_selection import make_support_selection
        plan = copy.deepcopy(self.plan); plan["limit"] = 1
        execution = execute_retrieval_plan(self.view, plan, graph_policy=GraphRetrievalPolicy(candidate_limit=2))
        answer_id = execution["result"]["results"][0]["node_id"]
        support_id = next(node_id for node_id in self.view.nodes if node_id != answer_id)
        manifest = make_support_selection(self.view, execution, plan, [{"node_id": support_id, "requirement_ids": ["query-r2"], "reason": "caller-selected evidence for the second requested aspect"}])
        original = copy.deepcopy(execution)
        for projection in ("full", "compact"):
            with self.subTest(projection=projection):
                context = build_context_from_execution(self.view, execution, plan=plan, token_budget=20000, context_projection=projection, support_selection=manifest)
                self.assertEqual("kgdistiller-context-bundle-v3", context["schema"])
                self.assertEqual(sha256_json(manifest), context["support_selection_sha256"])
                packet = context["support_packets"][0]
                self.assertEqual(support_id, packet["node_id"])
                self.assertEqual("query-support", packet["selection_origin"])
                self.assertEqual(["query-r2"], packet["requirement_ids"])
                record = next(record for record in context["nodes"] if record["node_id"] == support_id)
                expected = self.view.nodes[support_id] if projection == "full" else project_node(self.view.nodes[support_id])
                self.assertEqual(expected, record["content"])
                validate_compact_context(context, self.view)
                self.assertEqual(original, execution)

    def test_support_request_mutation_during_packing_is_rejected(self) -> None:
        from kgdistiller.support_selection import make_support_selection
        node_id = next(iter(self.view.nodes))
        manifest = make_support_selection(self.view, self.execution, self.plan, [{"node_id": node_id, "requirement_ids": ["query-r1"], "reason": "explicit source support"}])
        original_record = node_record
        def mutate(node, **kwargs):
            manifest["items"][0]["requirement_ids"] = ["query-r2"]
            return original_record(node, **kwargs)
        with mock.patch("kgdistiller.context_projection.node_record", side_effect=mutate):
            with self.assertRaises(RetrievalError) as error:
                build_context_from_execution(self.view, self.execution, plan=self.plan, token_budget=20000, support_selection=manifest)
            self.assertEqual("stale-generation", error.exception.code)

    def test_selected_support_gap_and_stale_source_are_explicit(self) -> None:
        from kgdistiller.support_selection import make_support_selection
        node_id = next(iter(self.view.nodes))
        manifest = make_support_selection(self.view, self.execution, self.plan, [{"node_id": node_id, "requirement_ids": ["query-r1"], "reason": "explicit source support"}])
        context = build_context_from_execution(self.view, self.execution, plan=self.plan, token_budget=1200, context_projection="compact", support_selection=manifest)
        self.assertFalse(context["nodes"])
        self.assertGreater(context["omitted_support_packets"], 0)
        self.assertLessEqual(context["budget"]["estimated_tokens"], 1200)
        corrupted = copy.deepcopy(manifest); corrupted["items"][0]["node_sha256"] = "0" * 64
        with self.assertRaises(RetrievalError):
            build_context_from_execution(self.view, self.execution, plan=self.plan, token_budget=20000, support_selection=corrupted)
        context = build_context_from_execution(self.view, self.execution, plan=self.plan, token_budget=20000, support_selection=manifest)
        context.pop("support_selection_sha256")
        with self.assertRaises(ContractError):
            validate_contract(context)

    def test_complete_semantics_unknown_fields_and_source_fingerprints_survive(self) -> None:
        before = copy.deepcopy(self.view.snapshot)
        context = self.build()
        self.assertEqual("kgdistiller-context-bundle-v3", context["schema"])
        self.assertEqual(context, validate_contract(context))
        validate_compact_context(context, self.view)
        for record in context["nodes"]:
            original, content = self.view.nodes[record["node_id"]], record["content"]
            self.assertEqual(original["text"], content["text"])
            self.assertEqual(original["properties"]["conditions"], content["properties"]["conditions"])
            self.assertEqual(original["properties"]["unknown_semantic_contract"], content["properties"]["unknown_semantic_contract"])
            self.assertEqual(original["entry"]["body"], content["entry"]["body"])
            self.assertEqual(original["entry"]["unknown_entry_semantics"], content["entry"]["unknown_entry_semantics"])
            self.assertEqual(original["provenance"], content["provenance"])
            self.assertNotIn("summary", content["entry"])
            self.assertNotIn("context", content["entry"])
            self.assertNotIn("entry_sha256", content["properties"])
            self.assertEqual(node_fingerprint(original), record["node_sha256"])
            self.assertEqual(hashlib.sha256(search_document(original).encode()).hexdigest(), record["document_sha256"])
            self.assertEqual(sha256_json(content), record["projection_sha256"])
        self.assertEqual(before, self.view.snapshot)

    def test_only_exact_duplicate_content_and_known_hashes_are_removed(self) -> None:
        original = copy.deepcopy(self.view.nodes["measure"])
        original["entry"]["summary"] = original["text"] + " "
        original["entry"]["context"] = "An independent restriction."
        original["properties"]["entry_sha256"] = {"unexpected_semantic_value": "retain"}
        original["properties"]["curated_definition_sha256"] = "f" * 64
        projected = project_node(original)
        self.assertEqual(original["entry"]["summary"], projected["entry"]["summary"])
        self.assertEqual(original["entry"]["context"], projected["entry"]["context"])
        self.assertEqual(original["properties"]["entry_sha256"], projected["properties"]["entry_sha256"])
        self.assertEqual("f" * 64, projected["properties"]["curated_definition_sha256"])
        self.assertEqual(projected, node_record(original)["content"])

    def test_shared_edge_evidence_is_serialized_once_and_routes_use_indexes(self) -> None:
        context = self.build()
        evidence = self.view.edges[0]["evidence"]
        self.assertEqual(1, len(context["edges"]))
        self.assertEqual(evidence, context["edges"][0]["content"]["evidence"])
        self.assertEqual(1, canonical_json(context).count(evidence))
        routes = [packet for packet in context["support_packets"] if packet["kind"] == "graph-path"]
        self.assertEqual(2, len(routes))
        for route in routes:
            self.assertEqual(0, route["steps"][0]["edge_index"])
            self.assertEqual({"edge_index", "direction"}, set(route["steps"][0]))
            self.assertFalse(route["logical_entailment"])

    def test_compact_budget_includes_more_complete_support_than_full_projection(self) -> None:
        full = build_context_from_execution(self.view, self.execution, plan=self.plan, token_budget=8000)
        compact = self.build(8000)
        full_routes = sum(packet["kind"] == "graph-path" for packet in full["support_packets"])
        compact_routes = sum(packet["kind"] == "graph-path" for packet in compact["support_packets"])
        self.assertGreater(compact_routes, full_routes)
        self.assertEqual(2, compact_routes)
        self.assertLessEqual(len(canonical_json(compact).encode()), 8000)

    def test_tight_budgets_keep_atomic_routes_or_report_missing_packets(self) -> None:
        for budget in range(1000, 6500, 31):
            try:
                context = self.build(budget)
            except RetrievalError as error:
                self.assertEqual("context-failed", error.code)
                self.assertIn("metadata", error.message)
                continue
            validate_compact_context(context, self.view)
            self.assertLessEqual(len(canonical_json(context).encode()), budget)
            for packet in context["support_packets"]:
                self.assertTrue(all(node_id in {record["node_id"] for record in context["nodes"]} for node_id in packet["nodes"]))
                self.assertTrue(all(step["edge_index"] < len(context["edges"]) for step in packet["steps"]))
            if context["omitted_support_packets"]:
                self.assertTrue(context["gaps"] or context["diagnostics_truncated"])
        with self.assertRaisesRegex(RetrievalError, "metadata"):
            self.build(10)

    def test_source_changes_stale_nodes_and_forged_routes_fail_before_packing(self) -> None:
        forged = copy.deepcopy(self.execution)
        forged["result"]["graph_retrieval"]["neighbors"][0]["path_evidence"][0]["steps"][0]["evidence"] = "Forged scientific evidence."
        with self.assertRaisesRegex(RetrievalError, "evidence does not match"):
            build_compact_context(self.view, forged, self.plan, 1000)
        forged = copy.deepcopy(self.execution)
        step = forged["result"]["graph_retrieval"]["neighbors"][0]["path_evidence"][0]["steps"][0]
        step["direction"] = "outgoing" if step["direction"] == "incoming" else "incoming"
        with self.assertRaises(RetrievalError):
            build_compact_context(self.view, forged, self.plan, 12000)
        self.view.nodes["measure"]["properties"]["conditions"].append("New source condition.")
        with self.assertRaisesRegex(RetrievalError, "stale-generation"):
            self.build()

    def test_projection_tampering_and_unknown_edge_indexes_are_rejected(self) -> None:
        context = self.build()
        forged = copy.deepcopy(context)
        forged["nodes"][0]["content"]["properties"]["conditions"] = []
        with self.assertRaisesRegex(ContractError, "projection digest"):
            validate_contract(forged)
        # A self-updated projection digest still cannot attest omitted source
        # fields: resolving the bound original graph detects the alteration.
        forged["nodes"][0]["projection_sha256"] = sha256_json(forged["nodes"][0]["content"])
        with self.assertRaisesRegex(ContractError, "original source-bound"):
            validate_compact_context(forged, self.view)
        forged = copy.deepcopy(context)
        forged["support_packets"][0]["steps"][0]["edge_index"] = 3999
        with self.assertRaisesRegex(ContractError, "unknown edge index"):
            validate_contract(forged)

    def test_plain_v1_v2_are_direct_sources_without_legacy_graph_proof(self) -> None:
        view = graph_fixture()
        plan = candidate_plan()
        plan["identity_queries"] = ["Sigma algebra"]
        plan["graph"].update(strategy="hybrid", max_depth=2)
        plain = execute_retrieval_plan(view, plan)
        self.assertTrue(any(row["path_evidence"] for row in plain["result"]["results"]))
        compact = build_compact_context(view, plain, plan, 10000)
        self.assertEqual([], compact["edges"])
        self.assertTrue(all(packet["kind"] == "direct-source" and not packet["steps"] for packet in compact["support_packets"]))
        with tempfile.TemporaryDirectory() as temporary:
            service = SemanticRankingService(FakeReranker(), cache_dir=Path(temporary), rerank=True)
            model = execute_retrieval_plan(view, plan, ranking_service=service)
            compact = build_compact_context(view, model, plan, 10000)
        self.assertEqual("kgdistiller-search-execution-v2", compact["search_execution_schema"])
        self.assertEqual([], compact["edges"])
        self.assertEqual(compact, validate_contract(compact))

    def test_plain_generation_and_query_binding_are_checked_by_direct_api(self) -> None:
        plain = execute_retrieval_plan(self.view, self.plan)
        wrong_plan = copy.deepcopy(self.plan); wrong_plan["question"] += " other"
        with self.assertRaisesRegex(RetrievalError, "supplied plan"):
            build_compact_context(self.view, plain, wrong_plan, 12000)
        self.view.nodes["measure"]["text"] += " Mutated."
        with self.assertRaisesRegex(RetrievalError, "changed without"):
            build_compact_context(self.view, plain, self.plan, 12000)

    def test_coherent_plain_view_and_snapshot_mutation_cannot_claim_old_generation(self) -> None:
        view = graph_fixture(); plan = candidate_plan()
        execution = execute_retrieval_plan(view, plan)
        forged = "FORGED definition replacing the original source content."
        view.nodes["measure"]["text"] = forged
        next(node for node in view.snapshot["nodes"] if node["id"] == "measure")["text"] = forged
        self.assertEqual(view.nodes, {node["id"]: node for node in view.snapshot["nodes"]})
        self.assertEqual(execution["snapshot_sha256"], view.snapshot["snapshot_sha256"])
        with self.assertRaisesRegex(RetrievalError, "snapshot digest does not match"):
            build_compact_context(view, execution, plan, 12000)

    def test_rebound_graph_execution_cannot_accept_an_invalid_original_snapshot(self) -> None:
        view = graph_fixture(); plan = candidate_plan()
        execution = execute_retrieval_plan(view, plan, graph_policy=GraphRetrievalPolicy(candidate_limit=1))
        forged = "FORGED definition replacing the original source content."
        view.nodes["measure"]["text"] = forged
        next(node for node in view.snapshot["nodes"] if node["id"] == "measure")["text"] = forged
        metadata = execution["result"]["graph_retrieval"]
        metadata["binding"]["view_content_sha256"] = view_content_sha256(view)
        binding = {"node_id": "measure", "node_sha256": node_fingerprint(view.nodes["measure"]),
                   "document_sha256": hashlib.sha256(search_document(view.nodes["measure"]).encode()).hexdigest()}
        metadata["seeds"]["candidate"][0].update(binding)
        for row in [*execution["result"]["results"], *metadata["neighbors"]]:
            for path in row["path_evidence"]:
                for record in path["node_bindings"]:
                    if record["node_id"] == "measure":
                        record.update(binding)
        self.assertEqual(execution, validate_contract(execution))
        for projection in ("full", "compact"):
            with self.subTest(projection=projection), self.assertRaisesRegex(RetrievalError, "snapshot digest does not match"):
                build_context_from_execution(view, execution, plan=plan, token_budget=12000, context_projection=projection)


if __name__ == "__main__":
    unittest.main()
