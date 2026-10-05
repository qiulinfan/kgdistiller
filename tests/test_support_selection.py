from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from kgdistiller.alignment import node_fingerprint
from kgdistiller.contracts import ContractError, sha256_json, validate_contract
from kgdistiller.graph_retrieval import GraphRetrievalPolicy
from kgdistiller.query import GraphView
from kgdistiller.retrieval import RetrievalError, execute_retrieval_plan
from kgdistiller.semantic_retrieval import SemanticRankingService, search_document
from kgdistiller.support_selection import SUPPORT_SELECTION_SCHEMA, make_support_selection, validate_support_selection
from tests.test_graph_retrieval import candidate_plan, graph_fixture
from tests.test_query import fixture_nodes, snapshot_with
from tests.test_semantic_retrieval import FakeEmbedding


def choices() -> list[dict]:
    return [{"node_id": "sigma-algebra", "requirement_ids": ["hq001-r1", "hq001-r2"],
             "reason": "Caller selected a prerequisite candidate from the actual query.",
             "producer_trace_sha256": ["a" * 64]}]


class SupportSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = graph_fixture()
        self.plan = candidate_plan()
        self.execution = execute_retrieval_plan(self.view, self.plan)

    def make(self, items=None):
        return make_support_selection(self.view, self.execution, self.plan, choices() if items is None else items)

    def test_source_binding_and_direct_rows_preserve_choices_without_rank_or_identity(self) -> None:
        before = copy.deepcopy((self.view.snapshot, self.plan, self.execution))
        manifest = self.make()
        self.assertEqual(SUPPORT_SELECTION_SCHEMA, manifest["schema"])
        self.assertFalse(manifest["identity_authority"])
        self.assertEqual("caller-selected-query-support", manifest["kind"])
        self.assertEqual(self.execution["snapshot_sha256"], manifest["snapshot_sha256"])
        self.assertEqual(hashlib.sha256(self.plan["question"].encode()).hexdigest(), manifest["question_sha256"])
        self.assertEqual(sha256_json(self.plan), manifest["plan_sha256"])
        item = manifest["items"][0]
        self.assertEqual(node_fingerprint(self.view.nodes[item["node_id"]]), item["node_sha256"])
        self.assertEqual(hashlib.sha256(search_document(self.view.nodes[item["node_id"]]).encode()).hexdigest(), item["document_sha256"])
        rows = validate_support_selection(self.view, self.execution, self.plan, manifest)
        self.assertEqual([{"node_id": "sigma-algebra", "lanes": {"support": {}}, "path_evidence": [],
                           "selection_origin": "query-support", "requirement_ids": ["hq001-r1", "hq001-r2"]}], rows)
        self.assertEqual(before, (self.view.snapshot, self.plan, self.execution))
        self.assertEqual(manifest, validate_contract(manifest))

    def test_choices_are_independent_of_answer_results_and_producer_trace_is_not_proof(self) -> None:
        self.assertNotIn("sigma-algebra", [row["node_id"] for row in self.execution["result"]["results"]])
        manifest = self.make()
        manifest["items"][0]["reason"] = "A caller hypothesis, not a semantic correctness guarantee."
        manifest["items"][0]["producer_trace_sha256"] = ["f" * 64]
        rows = validate_support_selection(self.view, self.execution, self.plan, manifest)
        self.assertEqual("sigma-algebra", rows[0]["node_id"])
        self.assertNotIn("identity", rows[0]["lanes"])
        self.assertNotIn("rank", rows[0])

    def test_empty_and_bounded_choices_are_valid_without_model_calls(self) -> None:
        self.assertEqual([], validate_support_selection(self.view, self.execution, self.plan, self.make([])))
        nodes = []
        for index in range(32):
            node = copy.deepcopy(fixture_nodes()[0]); node["id"] = f"node-{index:02d}"
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        execution = execute_retrieval_plan(view, self.plan)
        selected = [{"node_id": node["id"], "requirement_ids": ["hq001-r1"], "reason": "Explicit source support."} for node in nodes]
        manifest = make_support_selection(view, execution, self.plan, selected)
        self.assertEqual(32, len(validate_support_selection(view, execution, self.plan, manifest)))
        with self.assertRaisesRegex(RetrievalError, "invalid-support-selection"):
            make_support_selection(view, execution, self.plan, [*selected, selected[0]])

    def test_wrong_query_plan_namespace_and_generation_fail_structurally(self) -> None:
        manifest = self.make()
        for key, expected_code in (("question_sha256", "invalid-support-selection"), ("plan_sha256", "invalid-support-selection"),
                                   ("snapshot_sha256", "stale-generation"), ("graph_sha256", "stale-generation")):
            forged = copy.deepcopy(manifest); forged[key] = "b" * 64
            with self.subTest(key=key), self.assertRaises(RetrievalError) as caught:
                validate_support_selection(self.view, self.execution, self.plan, forged)
            self.assertEqual(expected_code, caught.exception.code)
        forged = copy.deepcopy(manifest); forged["namespace"] = "paper:other"
        with self.assertRaisesRegex(RetrievalError, "namespace-conflict"):
            validate_support_selection(self.view, self.execution, self.plan, forged)
        wrong_plan = copy.deepcopy(self.plan); wrong_plan["question"] += " a different original question"
        with self.assertRaisesRegex(RetrievalError, "invalid-execution"):
            validate_support_selection(self.view, self.execution, wrong_plan, manifest)

    def test_unsupported_versions_unknown_fields_and_spoofed_identity_are_rejected(self) -> None:
        manifest = self.make()
        for value in (True, 0, 0.0, None, "false"):
            forged = copy.deepcopy(manifest); forged["identity_authority"] = value
            with self.subTest(value=value), self.assertRaisesRegex(RetrievalError, "invalid-support-selection"):
                validate_support_selection(self.view, self.execution, self.plan, forged)
        for mutate in (lambda m: m.update(schema="kgdistiller-support-selection-v2"),
                       lambda m: m.update(kind="authoritative-identity"),
                       lambda m: m.update(confidence="high"),
                       lambda m: m["items"][0].update(identity_authority=True)):
            forged = copy.deepcopy(manifest); mutate(forged)
            with self.assertRaisesRegex(RetrievalError, "invalid-support-selection"):
                validate_support_selection(self.view, self.execution, self.plan, forged)

    def test_duplicate_unknown_nodes_and_bad_fingerprints_fail_closed(self) -> None:
        manifest = self.make()
        forged = copy.deepcopy(manifest); forged["items"].append(copy.deepcopy(forged["items"][0]))
        with self.assertRaisesRegex(RetrievalError, "unique"):
            validate_support_selection(self.view, self.execution, self.plan, forged)
        forged = copy.deepcopy(manifest); forged["items"][0]["node_id"] = "not-a-source-node"
        with self.assertRaisesRegex(RetrievalError, "unknown source node"):
            validate_support_selection(self.view, self.execution, self.plan, forged)
        for key in ("node_sha256", "document_sha256"):
            forged = copy.deepcopy(manifest); forged["items"][0][key] = "b" * 64
            with self.subTest(key=key), self.assertRaisesRegex(RetrievalError, "stale-generation"):
                validate_support_selection(self.view, self.execution, self.plan, forged)

    def test_current_filters_control_type_staleness_and_orphaned_sources(self) -> None:
        nodes = fixture_nodes()
        stale = copy.deepcopy(nodes[0]); stale["id"] = "stale"; stale["properties"]["curation_status"] = "needs-review"
        orphan = copy.deepcopy(nodes[0]); orphan["id"] = "orphan"; orphan["properties"]["source_status"] = "orphaned"; orphan["provenance"]["active"] = False
        field = {"id": "field", "type": "field", "label": "Field", "properties": {}}
        view = GraphView.from_snapshot(snapshot_with([*nodes, stale, orphan, field], []))
        execution = execute_retrieval_plan(view, self.plan)
        for node_id in ("stale", "orphan", "field"):
            selected = [{"node_id": node_id, "requirement_ids": ["hq001-r1"], "reason": "Explicit hypothesis."}]
            with self.subTest(node_id=node_id), self.assertRaisesRegex(RetrievalError, "excluded"):
                make_support_selection(view, execution, self.plan, selected)
        plan = copy.deepcopy(self.plan); plan["filters"].update(include_stale=True, include_orphaned=True, node_types=["knowledge", "field"])
        execution = execute_retrieval_plan(view, plan)
        selected = [{"node_id": node_id, "requirement_ids": ["hq001-r1"], "reason": "Explicit inclusive support."} for node_id in ("stale", "orphan", "field")]
        manifest = make_support_selection(view, execution, plan, selected)
        self.assertEqual(3, len(validate_support_selection(view, execution, plan, manifest)))

    def test_altered_source_and_coherent_snapshot_mutation_cannot_claim_old_generation(self) -> None:
        manifest = self.make()
        self.view.nodes["sigma-algebra"]["text"] += " Added without graph refresh."
        with self.assertRaisesRegex(RetrievalError, "stale-generation"):
            validate_support_selection(self.view, self.execution, self.plan, manifest)
        next(node for node in self.view.snapshot["nodes"] if node["id"] == "sigma-algebra")["text"] = self.view.nodes["sigma-algebra"]["text"]
        with self.assertRaisesRegex(RetrievalError, "snapshot digest"):
            validate_support_selection(self.view, self.execution, self.plan, manifest)

    def test_old_selection_cannot_rebind_to_a_valid_new_generation(self) -> None:
        manifest = self.make(); nodes = fixture_nodes()
        nodes[0]["properties"]["conditions"] = ["A source-authored new condition."]
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        execution = execute_retrieval_plan(view, self.plan)
        with self.assertRaisesRegex(RetrievalError, "stale-generation"):
            validate_support_selection(view, execution, self.plan, manifest)
        fresh = make_support_selection(view, execution, self.plan, choices())
        self.assertNotEqual(manifest["items"][0]["node_sha256"], fresh["items"][0]["node_sha256"])
        self.assertEqual(1, len(validate_support_selection(view, execution, self.plan, fresh)))

    def test_requirement_reason_trace_and_manifest_payload_bounds_are_strict(self) -> None:
        manifest = self.make()
        variants = [{"requirement_ids": []}, {"requirement_ids": ["hq001-r1"] * 2}, {"requirement_ids": [" "]},
                    {"requirement_ids": ["r"] * 33}, {"requirement_ids": ["x" * 129]}, {"reason": " "},
                    {"reason": "x" * 2049}, {"producer_trace_sha256": ["not-a-hash"]},
                    {"producer_trace_sha256": ["a" * 64] * 2}, {"producer_trace_sha256": [float("nan")]},
                    {"reason": float("inf")}, {"reason": "\ud800"}]
        for changes in variants:
            forged = copy.deepcopy(manifest); forged["items"][0].update(changes)
            with self.subTest(fields=list(changes)), self.assertRaisesRegex(RetrievalError, "invalid-support-selection"):
                validate_support_selection(self.view, self.execution, self.plan, forged)
        with self.assertRaises(ContractError):
            validate_contract({**manifest, "identity_authority": 0})

    def test_v2_and_v3_executions_bind_without_selection_model_inference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-support-model-") as temporary:
            adapter = FakeEmbedding(); service = SemanticRankingService(adapter, cache_dir=Path(temporary))
            model_execution = execute_retrieval_plan(self.view, self.plan, ranking_service=service)
            before = (adapter.document_calls, copy.deepcopy(adapter.query_calls))
            manifest = make_support_selection(self.view, model_execution, self.plan, choices())
            validate_support_selection(self.view, model_execution, self.plan, manifest)
            self.assertEqual(before, (adapter.document_calls, adapter.query_calls))
            forged = copy.deepcopy(model_execution)
            forged["result"]["ranking"]["embedding"]["query_sha256"] = "f" * 64
            with self.assertRaisesRegex(RetrievalError, "model question"):
                validate_support_selection(self.view, forged, self.plan, manifest)
        graph_execution = execute_retrieval_plan(self.view, self.plan, graph_policy=GraphRetrievalPolicy(candidate_limit=1))
        manifest = make_support_selection(self.view, graph_execution, self.plan, choices())
        self.assertEqual(1, len(validate_support_selection(self.view, graph_execution, self.plan, manifest)))


if __name__ == "__main__":
    unittest.main()
