from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.contracts import (
    ContractError,
    sha256_json,
    validate_contract,
)
from kgdistiller.query import GraphView, load_graph_view, search
from kgdistiller.retrieval import (
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
)
from kgdistiller.semantic_retrieval import (
    SemanticRankingService,
    SemanticRetrievalError,
    search_document,
)
from tests.test_query import (
    fixture_nodes,
    fixture_snapshot,
    snapshot_with,
    write_fixture_graph,
)
from tests.test_retrieval import retrieval_plan


class FakeEmbedding:
    def __init__(self) -> None:
        self.document_calls = 0
        self.document_inputs: list[list[str]] = []
        self.query_calls: list[list[str]] = []
        self.descriptor = {
            "provider": "test", "model": "multilingual-fixture", "revision": "frozen-v1",
            "inference": {"normalization": False, "dimensions": 2},
        }
        self.bad_documents = None
        self.bad_queries = None

    def metadata(self, kind: str) -> dict:
        if kind != "embedding":
            raise ValueError("unknown model kind")
        return copy.deepcopy(self.descriptor)

    def encode_documents(self, documents: list[str]) -> list[list[float]]:
        self.document_calls += 1
        self.document_inputs.append(list(documents))
        if self.bad_documents is not None:
            return self.bad_documents
        return [[1.0, 0.0] if "countably additive" in text else [0.0, 1.0] for text in documents]

    def encode_queries(self, queries: list[str]) -> list[list[float]]:
        self.query_calls.append(list(queries))
        if self.bad_queries is not None:
            return self.bad_queries
        return [[1.0, 0.0] for _ in queries]


class FakeReranker(FakeEmbedding):
    def __init__(self) -> None:
        super().__init__()
        self.reranker_descriptor = {
            "provider": "test", "model": "pair-fixture", "revision": "pair-v1",
            "inference": {"normalization": False},
        }
        self.pair_calls: list[tuple[str, list[str]]] = []
        self.bad_scores = None

    def metadata(self, kind: str) -> dict:
        return copy.deepcopy(self.reranker_descriptor) if kind == "reranker" else super().metadata(kind)

    def score_pairs(self, question: str, documents: list[str]) -> list[float]:
        self.pair_calls.append((question, list(documents)))
        if self.bad_scores is not None:
            return self.bad_scores
        return [10.0 if "Name: Absolute continuity" in document or "Name: Node 029" in document else -1.0 for document in documents]


def semantic_plan() -> dict:
    plan = retrieval_plan()
    plan["question"] = "如何给可测集合赋予大小？"
    plan["identity_queries"] = []
    plan["lexical_queries"] = [plan["question"]]
    plan["graph"].update({"seed_ids": [], "max_depth": 0, "strategy": "bfs"})
    return plan


class RetrievalFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="kgdistiller-vectors-")
        self.addCleanup(self.directory.cleanup)
        self.cache_dir = Path(self.directory.name)
        self.adapter = FakeEmbedding()
        self.service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir)
        self.view = GraphView.from_snapshot(fixture_snapshot())

    def execute(self, plan: dict | None = None, view: GraphView | None = None) -> dict:
        return execute_retrieval_plan(view or self.view, plan or semantic_plan(), ranking_service=self.service)


class SemanticRetrievalTest(RetrievalFixture):
    def test_embedding_recalls_beyond_keywords_but_never_resolves_or_seeds_identity(self) -> None:
        original = copy.deepcopy(self.view.snapshot)
        self.assertEqual([], search(self.view, semantic_plan()["question"]))
        execution = self.execute()
        self.assertEqual("kgdistiller-search-execution-v2", execution["schema"])
        self.assertEqual("kgdistiller-search-result-v2", execution["result"]["schema"])
        self.assertEqual("measure", execution["result"]["results"][0]["node_id"])
        self.assertEqual([], execution["identity_resolutions"])
        self.assertEqual(0, execution["result"]["lanes"]["graph"]["seeds"])
        self.assertEqual(0, execution["result"]["lanes"]["lexical"]["results"])
        self.assertEqual([[semantic_plan()["question"]]], self.adapter.query_calls)
        for row in execution["result"]["results"]:
            self.assertEqual({"embedding"}, set(row["lanes"]))
            self.assertEqual([], row["seed_evidence"])
            self.assertEqual([], row["path_evidence"])
        provenance = execution["result"]["ranking"]["embedding"]
        self.assertEqual("plan.question", provenance["query_source"])
        self.assertEqual(hashlib.sha256(semantic_plan()["question"].encode()).hexdigest(), provenance["query_sha256"])
        self.assertEqual(original, self.view.snapshot)
        self.assertEqual(execution, validate_contract(execution))

    def test_exact_and_authoritative_alias_remain_before_embedding_candidates(self) -> None:
        plan = semantic_plan()
        plan["identity_queries"] = ["Sigma algebra"]
        execution = self.execute(plan)
        self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
        self.assertEqual("exact", execution["identity_resolutions"][0]["status"])
        plan["identity_queries"] = ["西格玛代数"]
        execution = self.execute(plan)
        self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
        self.assertEqual("alias", execution["identity_resolutions"][0]["status"])

    def test_plain_runtime_contract_and_dependencies_are_unchanged(self) -> None:
        execution = execute_retrieval_plan(self.view, semantic_plan())
        self.assertEqual("kgdistiller-search-execution-v1", execution["schema"])
        self.assertEqual("kgdistiller-search-result-v1", execution["result"]["schema"])
        self.assertNotIn("ranking", execution["result"])
        self.assertNotIn("embedding", execution["result"]["lanes"])
        self.assertEqual([], list(self.cache_dir.iterdir()))
        self.assertEqual(0, self.adapter.document_calls)
        # Optional model modules are imported only by the outer adapter.
        source = (REPO_ROOT / "src/kgdistiller/semantic_retrieval.py").read_text()
        self.assertNotIn("import torch", source)
        self.assertNotIn("import sentence_transformers", source)

    def test_cache_reuse_and_source_model_inference_generation_invalidation(self) -> None:
        first = self.execute()["result"]["ranking"]["embedding"]
        second = self.execute()["result"]["ranking"]["embedding"]
        self.assertEqual(("miss", "hit"), (first["cache_status"], second["cache_status"]))
        self.assertEqual(first["index_sha256"], second["index_sha256"])
        self.assertEqual(1, self.adapter.document_calls)
        nodes = fixture_nodes()
        nodes[1]["properties"]["conditions"] = ["Only for measurable sets."]
        changed = self.execute(view=GraphView.from_snapshot(snapshot_with(nodes, [])))["result"]["ranking"]["embedding"]
        self.assertNotEqual(first["index_sha256"], changed["index_sha256"])
        self.assertEqual(2, self.adapter.document_calls)
        self.adapter.descriptor["revision"] = "frozen-v2"
        self.execute()
        self.adapter.descriptor["inference"]["dimensions"] = 3
        self.execute()
        self.assertEqual(4, self.adapter.document_calls)
        graph_changed = fixture_snapshot()
        graph_changed["graph"]["sha256"] = "b" * 64
        graph_changed.pop("snapshot_sha256")
        graph_changed["snapshot_sha256"] = sha256_json(graph_changed)
        self.execute(view=GraphView.from_snapshot(graph_changed))
        self.assertEqual(4, self.adapter.document_calls)
        self.assertEqual(3, self.service.last_cache_stats["rank"]["document_cache_hits"])
        self.assertEqual(0, self.service.last_cache_stats["rank"]["document_inference_items"])

    def test_cache_contains_bound_ordered_fingerprints_and_document_hashes(self) -> None:
        self.execute()
        cache = json.loads(next(self.cache_dir.glob("*.json")).read_text())
        manifest = cache["manifest"]
        self.assertEqual(self.view.snapshot["namespace"], manifest["namespace"])
        self.assertEqual(self.view.snapshot["snapshot_sha256"], manifest["snapshot_sha256"])
        self.assertEqual(self.view.snapshot["graph"]["sha256"], manifest["graph_sha256"])
        self.assertEqual(sorted(self.view.nodes), [record["node_id"] for record in manifest["documents"]])
        for record in manifest["documents"]:
            document = search_document(self.view.nodes[record["node_id"]])
            self.assertEqual(hashlib.sha256(document.encode()).hexdigest(), record["document_sha256"])
            self.assertEqual(64, len(record["node_sha256"]))

    def test_tampered_cache_fails_without_model_retry_or_lexical_fallback(self) -> None:
        self.execute()
        path = next(self.cache_dir.glob("*.json"))
        payload = json.loads(path.read_text())
        payload["vectors"][0][0] += 1.0
        path.write_text(json.dumps(payload))
        plan = semantic_plan()
        plan["lexical_queries"] = ["measure"]
        with self.assertRaisesRegex(RetrievalError, "invalid-vector-cache"):
            self.execute(plan)
        self.assertEqual(1, self.adapter.document_calls)

    def test_resigned_cache_still_checks_shape_and_manifest(self) -> None:
        self.execute()
        path = next(self.cache_dir.glob("*.json"))
        payload = json.loads(path.read_text())
        payload["vectors"].pop()
        payload.pop("cache_sha256")
        payload["cache_sha256"] = sha256_json(payload)
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(RetrievalError, "invalid-vector-cache"):
            self.execute()

    def test_invalid_protocol_vector_shapes_numbers_and_zero_vectors_fail_explicitly(self) -> None:
        bad = [[], [[1.0]], [[1, 0], [1], [1, 0]], [[True, 0]] * 3,
               [[float("nan"), 0]] * 3, [[float("inf"), 0]] * 3,
               [[0, 0]] * 3, [[10 ** 1000, 0]] * 3, [["one", 0]] * 3]
        for index, value in enumerate(bad):
            with self.subTest(index=index):
                adapter = FakeEmbedding()
                adapter.bad_documents = value
                service = SemanticRankingService(adapter, cache_dir=self.cache_dir / str(index))
                with self.assertRaisesRegex(RetrievalError, "invalid-embedding-vectors"):
                    execute_retrieval_plan(self.view, semantic_plan(), ranking_service=service)
        self.adapter.bad_queries = [[1.0, 0.0, 0.0]]
        with self.assertRaisesRegex(RetrievalError, "invalid-embedding-vectors"):
            self.execute()

    def test_model_errors_and_unbound_metadata_do_not_silently_fall_back(self) -> None:
        with (
            patch.object(self.adapter, "encode_documents", side_effect=RuntimeError("provider private diagnostic")),
            self.assertRaisesRegex(RetrievalError, "embedding-model-failed") as caught,
        ):
            self.execute()
        self.assertNotIn("provider private diagnostic", str(caught.exception))
        self.adapter.descriptor["revision"] = ""
        with self.assertRaisesRegex(RetrievalError, "invalid-model-descriptor"):
            self.execute()
        self.adapter.descriptor["revision"] = "valid"
        self.adapter.descriptor["inference"]["oversized"] = "x" * 4096
        with self.assertRaisesRegex(RetrievalError, "invalid-model-descriptor"):
            self.execute()

    def test_descriptor_change_during_inference_is_not_cached(self) -> None:
        encode = self.adapter.encode_documents
        def changing(documents: list[str]) -> list[list[float]]:
            result = encode(documents)
            self.adapter.descriptor["revision"] = "changed"
            return result
        with (
            patch.object(self.adapter, "encode_documents", side_effect=changing),
            self.assertRaisesRegex(RetrievalError, "model-descriptor-changed"),
        ):
            self.execute()
        self.assertEqual([], list(self.cache_dir.iterdir()))

    def test_filters_apply_before_fusion_and_namespace_does_not_cross(self) -> None:
        nodes = fixture_nodes()
        for node_id, properties, type_ in (
            ("stale", {"curation_status": "needs-review"}, "knowledge"),
            ("orphan", {"source_status": "orphaned"}, "knowledge"),
        ):
            node = copy.deepcopy(nodes[1])
            node.update({"id": node_id, "label": node_id, "type": type_})
            node["properties"].update(properties)
            if node_id == "orphan":
                node["provenance"]["active"] = False
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        execution = self.execute(view=view)
        self.assertEqual({"sigma-algebra", "measure", "absolute-continuity"}, {row["node_id"] for row in execution["result"]["results"]})
        first = execution["result"]["ranking"]["embedding"]
        self.assertEqual(3, first["document_count"])
        self.assertEqual([search_document(view.nodes[node_id]) for node_id in sorted({"sigma-algebra", "measure", "absolute-continuity"})], self.adapter.document_inputs[0])
        plan = semantic_plan()
        plan["filters"].update({"include_stale": True, "include_orphaned": True})
        execution = self.execute(plan, view)
        self.assertEqual({"sigma-algebra", "measure", "absolute-continuity", "stale", "orphan"}, {row["node_id"] for row in execution["result"]["results"]})
        second = execution["result"]["ranking"]["embedding"]
        self.assertEqual(5, second["document_count"])
        self.assertNotEqual(first["index_sha256"], second["index_sha256"])
        self.assertEqual(2, self.adapter.document_calls)
        self.execute(view=view)
        self.assertEqual(2, self.adapter.document_calls)
        manifests = [json.loads(path.read_text())["manifest"] for path in self.cache_dir.glob("*.json")]
        self.assertEqual({3, 5}, {len(manifest["documents"]) for manifest in manifests})
        with self.assertRaisesRegex(SemanticRetrievalError, "namespace-conflict"):
            self.service.rank(view, namespace="paper:other", question="test", eligible_ids=["measure"])

    def test_unknown_duplicate_ids_are_rejected(self) -> None:
        for ids in (["unknown"], ["measure", "measure"]):
            with self.subTest(ids=ids), self.assertRaisesRegex(SemanticRetrievalError, "invalid-semantic-candidates"):
                self.service.rank(self.view, namespace="personal", question="test", eligible_ids=ids)

    def test_empty_graph_and_zero_eligible_nodes_do_not_encode_empty_queries(self) -> None:
        empty = GraphView.from_snapshot(snapshot_with([], []))
        execution = self.execute(view=empty)
        provenance = execution["result"]["ranking"]["embedding"]
        self.assertEqual([], execution["result"]["results"])
        self.assertEqual("empty", provenance["cache_status"])
        self.assertEqual(0, provenance["document_count"])
        self.assertEqual(0, provenance["dimensions"])
        self.assertEqual(0, self.adapter.document_calls)
        self.assertEqual([], self.adapter.query_calls)
        stale = copy.deepcopy(fixture_nodes()[1])
        stale["properties"]["curation_status"] = "needs-review"
        execution = self.execute(view=GraphView.from_snapshot(snapshot_with([stale], [])))
        self.assertEqual([], execution["result"]["results"])
        self.assertEqual(0, self.adapter.document_calls)
        self.assertEqual([], self.adapter.query_calls)
        self.assertEqual("empty", execution["result"]["ranking"]["embedding"]["cache_status"])
        self.assertEqual([], list(self.cache_dir.iterdir()))

    def test_excluded_source_text_never_reaches_embedding_adapter(self) -> None:
        nodes = fixture_nodes()
        for node_id, type_ in (("stale", "knowledge"), ("orphan", "knowledge")):
            node = copy.deepcopy(nodes[1])
            node.update({"id": node_id, "label": node_id, "type": type_, "text": "FORBIDDEN MODEL INPUT " + "large " * 1000})
            if node_id == "stale":
                node["properties"]["curation_status"] = "needs-review"
            elif node_id == "orphan":
                node["properties"]["source_status"] = "orphaned"
                node["provenance"]["active"] = False
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        encode = self.adapter.encode_documents
        def reject_excluded(documents: list[str]) -> list[list[float]]:
            if any("FORBIDDEN MODEL INPUT" in document for document in documents):
                raise ValueError("excluded oversized source would fail model inference")
            return encode(documents)
        with patch.object(self.adapter, "encode_documents", side_effect=reject_excluded):
            execution = self.execute(view=view)
            self.assertEqual("measure", execution["result"]["results"][0]["node_id"])
            self.assertEqual(3, len(self.adapter.document_inputs[0]))
            plan = semantic_plan()
            plan["filters"]["include_stale"] = True
            with self.assertRaisesRegex(RetrievalError, "embedding-model-failed"):
                self.execute(plan, view)

    def test_atomic_write_failure_leaves_no_partial_cache(self) -> None:
        with (
            patch("kgdistiller.semantic_retrieval.os.replace", side_effect=OSError("disk unavailable")),
            self.assertRaisesRegex(RetrievalError, "vector-cache-unwritable"),
        ):
            self.execute()
        self.assertEqual([], [path for path in self.cache_dir.rglob("*") if path.is_file()])

    def test_cache_cannot_be_written_into_authority_graph_directory(self) -> None:
        graph = self.cache_dir / "authority"
        graph.mkdir()
        (graph / "manifest.json").write_text("{}")
        view = replace(self.view, graph_dir=graph)
        service = SemanticRankingService(self.adapter, cache_dir=graph / "derived")
        with self.assertRaisesRegex(RetrievalError, "cache-graph-conflict"):
            execute_retrieval_plan(view, semantic_plan(), ranking_service=service)
        self.assertFalse((graph / "derived").exists())
        self.assertEqual(0, self.adapter.document_calls)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_and_symlink_cache_are_rejected_without_blocking(self) -> None:
        self.execute()
        path = next(self.cache_dir.glob("*.json"))
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(RetrievalError, "invalid-vector-cache"):
            self.execute()
        path.unlink()
        target = self.cache_dir / "target"
        target.write_text("{}")
        path.symlink_to(target)
        with self.assertRaisesRegex(RetrievalError, "invalid-vector-cache"):
            self.execute()

    def test_v2_context_preserves_binding_budget_and_complete_conditions(self) -> None:
        execution = self.execute()
        context = build_context_from_execution(self.view, execution, plan=semantic_plan(), token_budget=6000)
        self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
        self.assertEqual("kgdistiller-search-result-v2", context["search_result_schema"])
        self.assertLessEqual(context["budget"]["estimated_tokens"], 6000)
        self.assertEqual(self.view.nodes["measure"], next(node for node in context["nodes"] if node["id"] == "measure"))
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["embedding"]["snapshot_sha256"] = "b" * 64
        with self.assertRaisesRegex(RetrievalError, "invalid-execution"):
            build_context_from_execution(self.view, changed, plan=semantic_plan())
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["embedding"]["query_sha256"] = "b" * 64
        with self.assertRaisesRegex(RetrievalError, "embedding query"):
            build_context_from_execution(self.view, changed, plan=semantic_plan())

    def test_v2_contract_rejects_recursive_bad_payloads_and_duplicate_nodes(self) -> None:
        execution = self.execute()
        changed = copy.deepcopy(execution)
        changed["result"]["results"][0]["lanes"]["embedding"]["score"] = float("nan")
        with self.assertRaises(ContractError):
            validate_contract(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["results"].append(copy.deepcopy(changed["result"]["results"][0]))
        with self.assertRaisesRegex(ContractError, "duplicate"):
            validate_contract(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["schema"] = "kgdistiller-search-result-v1"
        with self.assertRaises(ContractError):
            validate_contract(changed)


class ExactInputReuseTest(RetrievalFixture):
    def rank(self, view: GraphView | None = None, *, ids: list[str] | None = None, question: str = "question"):
        target = view or self.view
        return self.service.rank(target, namespace="personal", question=question, eligible_ids=ids if ids is not None else list(target.nodes))

    def edge_changed(self) -> GraphView:
        payload = fixture_snapshot()
        payload["edges"][0]["evidence"] += " Updated source evidence."
        payload.pop("snapshot_sha256")
        payload["snapshot_sha256"] = sha256_json(payload)
        return GraphView.from_snapshot(payload)

    def test_edge_change_reuses_all_inputs_and_rebinds_generation(self) -> None:
        first, first_provenance = self.rank()
        changed = self.edge_changed()
        second, second_provenance = self.rank(changed)
        self.assertEqual(first, second)
        self.assertEqual(1, self.adapter.document_calls)
        self.assertEqual([["question"]], self.adapter.query_calls)
        self.assertNotEqual(first_provenance["snapshot_sha256"], second_provenance["snapshot_sha256"])
        self.assertEqual(changed.snapshot["snapshot_sha256"], second_provenance["snapshot_sha256"])
        self.assertEqual("miss", second_provenance["cache_status"])
        self.assertEqual(3, self.service.last_cache_stats["rank"]["document_cache_hits"])
        self.assertEqual(0, self.service.last_cache_stats["rank"]["document_inference_items"])
        self.assertEqual(1, self.service.last_cache_stats["rank"]["query_cache_hits"])

    def test_one_edited_document_infers_only_that_document(self) -> None:
        self.rank()
        nodes = fixture_nodes()
        nodes[1]["properties"]["conditions"] = ["A newly authored condition."]
        changed = GraphView.from_snapshot(snapshot_with(nodes, []))
        _, provenance = self.rank(changed)
        self.assertEqual(2, self.adapter.document_calls)
        self.assertEqual([search_document(changed.nodes["measure"])], self.adapter.document_inputs[-1])
        self.assertEqual(1, self.service.last_cache_stats["rank"]["document_inference_items"])
        self.assertEqual(2, self.service.last_cache_stats["rank"]["document_cache_hits"])
        self.assertEqual([["question"]], self.adapter.query_calls)
        manifest = json.loads((self.cache_dir / (provenance["index_sha256"] + ".json")).read_text())["manifest"]
        self.assertEqual(changed.snapshot["snapshot_sha256"], manifest["snapshot_sha256"])
        self.assertEqual(hashlib.sha256(search_document(changed.nodes["measure"]).encode()).hexdigest(), next(row for row in manifest["documents"] if row["node_id"] == "measure")["document_sha256"])

    def test_filter_subset_and_rebound_node_ids_reuse_content(self) -> None:
        self.rank()
        rows, provenance = self.rank(ids=["measure"])
        self.assertEqual(["measure"], [row[0] for row in rows])
        self.assertEqual(1, provenance["document_count"])
        self.assertEqual(1, self.service.last_cache_stats["rank"]["document_cache_hits"])
        node = copy.deepcopy(fixture_nodes()[1]); node["id"] = "another-id"
        changed = GraphView.from_snapshot(snapshot_with([node], []))
        rows, provenance = self.rank(changed)
        self.assertEqual(["another-id"], [row[0] for row in rows])
        self.assertEqual(1, self.adapter.document_calls)
        self.assertEqual([["question"]], self.adapter.query_calls)
        self.assertEqual(changed.snapshot["snapshot_sha256"], provenance["snapshot_sha256"])

    def test_identical_documents_are_encoded_once_and_expand_back_to_nodes(self) -> None:
        nodes = fixture_nodes(); duplicate = copy.deepcopy(nodes[1]); duplicate["id"] = "same-content"
        view = GraphView.from_snapshot(snapshot_with([*nodes, duplicate], []))
        rows, provenance = self.rank(view)
        self.assertEqual(4, len(rows)); self.assertEqual(4, provenance["document_count"])
        self.assertEqual(3, len(self.adapter.document_inputs[0]))
        self.assertEqual(1, self.service.last_cache_stats["rank"]["document_deduplicated"])
        scores = dict(rows)
        self.assertEqual(scores["measure"], scores["same-content"])

    def test_new_service_persists_query_and_document_reuse(self) -> None:
        first, _ = self.rank()
        adapter = FakeEmbedding()
        service = SemanticRankingService(adapter, cache_dir=self.cache_dir)
        rows, _ = service.rank(self.edge_changed(), namespace="personal", question="question", eligible_ids=list(self.view.nodes))
        self.assertEqual(first, rows)
        self.assertEqual(0, adapter.document_calls); self.assertEqual([], adapter.query_calls)
        self.assertEqual(3, service.last_cache_stats["rank"]["document_cache_hits"])

    def test_query_key_uses_exact_text_and_selected_model(self) -> None:
        self.rank(question="same")
        self.rank(question="same ")
        self.rank(question="same")
        self.assertEqual([["same"], ["same "]], self.adapter.query_calls)
        self.adapter.descriptor["revision"] = "new-revision"
        self.rank(question="same")
        self.assertEqual([["same"], ["same "], ["same"]], self.adapter.query_calls)
        self.assertEqual(2, self.adapter.document_calls)

    def test_valid_legacy_generation_cache_bootstraps_content_without_inference(self) -> None:
        self.rank()
        for path in (self.cache_dir / "exact-inputs-v1").glob("*.json"):
            path.unlink()
        adapter = FakeEmbedding(); self.service = SemanticRankingService(adapter, cache_dir=self.cache_dir)
        self.rank()
        self.assertEqual(0, adapter.document_calls)
        self.assertEqual(3, self.service.last_cache_stats["rank"]["document_cache_seeded"])
        self.rank(self.edge_changed())
        self.assertEqual(0, adapter.document_calls)
        self.assertEqual(3, self.service.last_cache_stats["rank"]["document_cache_hits"])

    def test_corrupted_query_and_content_cache_never_reinfer(self) -> None:
        self.rank()
        query_path = next((self.cache_dir / "exact-inputs-v1").glob("query-vector-*.json"))
        original = query_path.read_bytes()
        payload = json.loads(original); payload["value"] = [True, 0]
        payload.pop("cache_sha256"); payload["cache_sha256"] = sha256_json(payload)
        query_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-exact-input-cache"):
            self.rank()
        self.assertEqual([["question"]], self.adapter.query_calls)
        query_path.write_bytes(original)
        document_path = next((self.cache_dir / "exact-inputs-v1").glob("document-vector-*.json"))
        payload = json.loads(document_path.read_text()); payload["value"][0] += 2
        document_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-exact-input-cache"):
            self.rank(self.edge_changed())
        self.assertEqual(1, self.adapter.document_calls)

    def test_cached_query_checks_descriptor_and_source_after_lookup(self) -> None:
        self.rank()
        getter = self.service._exact_cache.get
        def model_changes(binding):
            value = getter(binding)
            self.adapter.descriptor["revision"] = "changed-during-cache-hit"
            return value
        with (
            patch.object(self.service._exact_cache, "get", side_effect=model_changes),
            self.assertRaisesRegex(SemanticRetrievalError, "model-descriptor-changed"),
        ):
            self.rank()
        self.adapter.descriptor["revision"] = "frozen-v1"
        def source_changes(binding):
            value = getter(binding)
            self.view.nodes["measure"]["text"] += " Changed while using cache."
            return value
        with (
            patch.object(self.service._exact_cache, "get", side_effect=source_changes),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            self.rank()
        self.assertEqual([["question"]], self.adapter.query_calls)

    def test_stale_loaded_source_is_rejected_before_any_cache_or_inference(self) -> None:
        self.rank()
        self.view.nodes["measure"]["text"] += " Unbound source change."
        with (
            patch.object(self.service._exact_cache, "get", side_effect=AssertionError("must not read cache")),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            self.rank()
        self.assertEqual(1, self.adapter.document_calls)

    def test_source_change_during_document_or_query_inference_is_rejected(self) -> None:
        encode = self.adapter.encode_documents
        def changes_documents(documents):
            values = encode(documents)
            self.view.nodes["measure"]["text"] += " Changed during document inference."
            return values
        with (
            patch.object(self.adapter, "encode_documents", side_effect=changes_documents),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            self.rank()
        self.assertEqual([], list(self.cache_dir.iterdir()))
        self.view = GraphView.from_snapshot(fixture_snapshot())
        encode_query = self.adapter.encode_queries
        def changes_query(queries):
            values = encode_query(queries)
            self.view.edges[0]["evidence"] += " Changed during query inference."
            return values
        with (
            patch.object(self.adapter, "encode_queries", side_effect=changes_query),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            self.rank()
        self.assertEqual([], list((self.cache_dir / "exact-inputs-v1").glob("query-vector-*.json")))

    def test_generation_memory_reuses_validation_but_detects_same_size_restored_mtime_edit(self) -> None:
        self.rank()
        from kgdistiller.semantic_retrieval import _vectors
        validations = []
        def validate(value, count, **kwargs):
            validations.append(count)
            return _vectors(value, count, **kwargs)
        with patch("kgdistiller.semantic_retrieval._vectors", side_effect=validate):
            self.rank()
        self.assertEqual([1], validations)  # Query width still checked; full matrix is already validated.
        path = next(self.cache_dir.glob("*.json")); info = path.stat()
        raw = path.read_text(); payload = json.loads(raw)
        payload["vectors"][0][0] = 2.0 if payload["vectors"][0][0] == 1.0 else 1.0
        from kgdistiller.contracts import canonical_json
        changed = canonical_json(payload)
        self.assertEqual(len(raw), len(changed))
        path.write_text(changed)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-vector-cache"):
            self.rank()
        self.assertEqual(1, self.adapter.document_calls)

    def test_generation_and_source_memory_have_explicit_lru_bounds(self) -> None:
        with patch("kgdistiller.semantic_retrieval.MAX_GENERATION_MEMORY_RECORDS", 2):
            self.rank(ids=["measure"])
            self.rank(ids=["sigma-algebra"])
            self.rank(ids=["absolute-continuity"])
        self.assertEqual(2, len(self.service._generation_vectors))
        self.assertGreater(self.service._generation_vector_bytes, 0)
        with patch("kgdistiller.semantic_retrieval.MAX_GENERATION_MEMORY_BYTES", 1):
            self.service._remember_generation("not-stored", (1, 2, 3, 4, 5), [[1.0]], 1, "a" * 64, 10)
        self.assertNotIn("not-stored", self.service._generation_vectors)

    def test_unchanged_filesystem_is_not_reloaded_between_hot_cache_operations(self) -> None:
        graph = write_fixture_graph(self.cache_dir / "vault")
        view = load_graph_view(graph)
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir / "vectors", rerank=True)
        kwargs = {"namespace": "personal", "question": "question", "eligible_ids": list(view.nodes)}
        with patch("kgdistiller.semantic_retrieval.load_graph_view", wraps=load_graph_view) as loader:
            service.rank(view, **kwargs)
            service.rank(view, **kwargs)
            service.rerank(view, namespace="personal", question="question", candidate_ids=["measure"])
            service.rerank(view, namespace="personal", question="question", candidate_ids=["measure"])
        self.assertEqual(1, loader.call_count)
        self.assertEqual(1, adapter.document_calls)
        self.assertEqual([["question"]], adapter.query_calls)
        self.assertEqual(1, len(adapter.pair_calls))

    def test_filesystem_source_edits_before_and_during_cached_calls_fail_closed(self) -> None:
        graph = write_fixture_graph(self.cache_dir / "vault")
        view = load_graph_view(graph)
        service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir / "vectors")
        kwargs = {"namespace": "personal", "question": "question", "eligible_ids": list(view.nodes)}
        service.rank(view, **kwargs)
        path = graph / "edges.jsonl"; original = path.read_bytes()
        getter = service._exact_cache.get
        def changes_source(binding):
            value = getter(binding)
            path.write_bytes(original.replace(b"A measure is defined on a sigma algebra.", b"A measure is redefined on a sigma algebra."))
            return value
        with (
            patch.object(service._exact_cache, "get", side_effect=changes_source),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            service.rank(view, **kwargs)
        self.assertEqual(1, self.adapter.document_calls)
        self.assertEqual([["question"]], self.adapter.query_calls)
        with (
            patch.object(service._exact_cache, "get", side_effect=AssertionError("must not read stale-source cache")),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            service.rank(view, **kwargs)
        path.write_bytes(original)
        service.rank(view, **kwargs)
        self.assertEqual(1, self.adapter.document_calls)

    def test_pair_source_change_on_cache_hit_is_rejected_without_scoring(self) -> None:
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir, rerank=True)
        kwargs = {"namespace": "personal", "question": "question", "candidate_ids": ["measure"]}
        service.rerank(self.view, **kwargs)
        getter = service._exact_cache.get
        def changes_source(binding):
            value = getter(binding); self.view.nodes["measure"]["text"] += " Changed while using pair cache."; return value
        with (
            patch.object(service._exact_cache, "get", side_effect=changes_source),
            self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"),
        ):
            service.rerank(self.view, **kwargs)
        self.assertEqual(1, len(adapter.pair_calls))

    def test_pair_scores_persist_reorder_and_rebind_without_model_calls(self) -> None:
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir, rerank=True)
        ids = ["measure", "absolute-continuity"]
        first, _ = service.rerank(self.view, namespace="personal", question="question", candidate_ids=ids)
        second, provenance = service.rerank(self.edge_changed(), namespace="personal", question="question", candidate_ids=list(reversed(ids)))
        self.assertEqual(first, second)
        self.assertEqual(1, len(adapter.pair_calls))
        self.assertEqual(list(reversed(ids)), [item["node_id"] for item in provenance["candidates"]])
        self.assertEqual(2, service.last_cache_stats["rerank"]["pair_cache_hits"])
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir, rerank=True)
        self.assertEqual(first, service.rerank(self.view, namespace="personal", question="question", candidate_ids=ids)[0])
        self.assertEqual([], adapter.pair_calls)

    def test_pair_subset_duplicate_input_and_one_edit_only_infer_new_pair(self) -> None:
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir, rerank=True)
        nodes = fixture_nodes(); duplicate = copy.deepcopy(nodes[1]); duplicate["id"] = "same-content"
        view = GraphView.from_snapshot(snapshot_with([*nodes, duplicate], []))
        service.rerank(view, namespace="personal", question="question", candidate_ids=["measure", "same-content", "absolute-continuity"])
        self.assertEqual(2, len(adapter.pair_calls[0][1])); self.assertEqual(1, service.last_cache_stats["rerank"]["pair_deduplicated"])
        service.rerank(view, namespace="personal", question="question", candidate_ids=["same-content"])
        self.assertEqual(1, len(adapter.pair_calls))
        nodes[1]["properties"]["conditions"] = ["Edited condition."]
        changed = GraphView.from_snapshot(snapshot_with([*nodes, duplicate], []))
        service.rerank(changed, namespace="personal", question="question", candidate_ids=["measure", "same-content"])
        self.assertEqual(2, len(adapter.pair_calls)); self.assertEqual([search_document(changed.nodes["measure"])], adapter.pair_calls[-1][1])
        self.assertEqual(1, service.last_cache_stats["rerank"]["pair_cache_hits"])
        self.assertEqual(1, service.last_cache_stats["rerank"]["pair_inference_items"])

    def test_pair_corruption_descriptor_and_source_changes_fail_without_fallback(self) -> None:
        adapter = FakeReranker(); service = SemanticRankingService(adapter, cache_dir=self.cache_dir, rerank=True)
        ids = ["measure"]
        service.rerank(self.view, namespace="personal", question="question", candidate_ids=ids)
        path = next((self.cache_dir / "exact-inputs-v1").glob("pair-score-*.json")); original = path.read_bytes()
        payload = json.loads(original); payload["value"] = True; payload.pop("cache_sha256"); payload["cache_sha256"] = sha256_json(payload)
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-exact-input-cache"):
            service.rerank(self.view, namespace="personal", question="question", candidate_ids=ids)
        self.assertEqual(1, len(adapter.pair_calls)); path.write_bytes(original)
        getter = service._exact_cache.get
        def change(binding):
            value = getter(binding); adapter.reranker_descriptor["revision"] = "changed"; return value
        with (
            patch.object(service._exact_cache, "get", side_effect=change),
            self.assertRaisesRegex(SemanticRetrievalError, "model-descriptor-changed"),
        ):
            service.rerank(self.view, namespace="personal", question="question", candidate_ids=ids)
        self.assertEqual(1, len(adapter.pair_calls))


class RerankerTest(RetrievalFixture):
    def setUp(self) -> None:
        super().setUp()
        self.adapter = FakeReranker()
        self.service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir)

    def enable(self, *, candidate_limit: int = 50) -> None:
        self.service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir, rerank=True, candidate_limit=candidate_limit)

    def test_reranker_optin_changes_order_with_bound_scores_without_identity(self) -> None:
        original = copy.deepcopy(self.view.snapshot)
        embedding = self.execute()
        self.assertEqual("measure", embedding["result"]["results"][0]["node_id"])
        self.assertEqual([], self.adapter.pair_calls)
        self.enable()
        execution = self.execute()
        self.assertEqual("absolute-continuity", execution["result"]["results"][0]["node_id"])
        self.assertEqual([], execution["identity_resolutions"])
        self.assertEqual(0, execution["result"]["lanes"]["graph"]["seeds"])
        self.assertEqual("hit", execution["result"]["ranking"]["embedding"]["cache_status"])
        self.assertEqual(1, self.adapter.document_calls)
        self.assertEqual("rrf", execution["result"]["results"][0]["fusion"]["method"])
        provenance = execution["result"]["ranking"]["reranker"]
        self.assertEqual("rrf-base-reranker", provenance["fusion"])
        self.assertEqual(50, provenance["candidate_limit"])
        self.assertEqual([row["node_id"] for row in embedding["result"]["results"]], [item["node_id"] for item in provenance["candidates"]])
        self.assertEqual(semantic_plan()["question"], self.adapter.pair_calls[0][0])
        self.assertEqual(self.view.snapshot["snapshot_sha256"], provenance["snapshot_sha256"])
        for candidate, document in zip(provenance["candidates"], self.adapter.pair_calls[0][1]):
            self.assertEqual(hashlib.sha256(document.encode()).hexdigest(), candidate["document_sha256"])
            self.assertEqual(64, len(candidate["node_sha256"]))
        self.assertEqual(original, self.view.snapshot)
        self.assertEqual(execution, validate_contract(execution))

    def test_candidate_pool_is_truncated_before_final_limit_not_after(self) -> None:
        nodes = []
        for index in range(40):
            node = copy.deepcopy(fixture_nodes()[0])
            node.update({"id": f"node-{index:03d}", "label": f"Node {index:03d}"})
            node["properties"]["aliases"] = []
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        self.enable(candidate_limit=30)
        plan = semantic_plan()
        plan["lexical_queries"] = []
        plan["limit"] = 20
        execution = self.execute(plan, view)
        self.assertEqual(20, len(execution["result"]["results"]))
        self.assertIn("node-029", [row["node_id"] for row in execution["result"]["results"]])
        candidates = execution["result"]["ranking"]["reranker"]["candidates"]
        self.assertEqual([f"node-{index:03d}" for index in range(30)], [item["node_id"] for item in candidates])
        self.assertEqual(30, len(self.adapter.pair_calls[0][1]))
        self.assertNotIn("node-030", [item["node_id"] for item in candidates])

    def test_rank_fusion_preserves_strong_base_evidence_against_low_base_model_winner(self) -> None:
        nodes = []
        for index in range(40):
            node = copy.deepcopy(fixture_nodes()[1])
            node.update({"id": f"node-{index:03d}", "label": f"Node {index:03d}"})
            node["properties"]["aliases"] = []
            if index:
                node["text"] = "Background about image augmentation, not measurable-set size."
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        plan = semantic_plan()
        plan["lexical_queries"] = []
        plan["limit"] = 20
        base = self.execute(plan, view)
        self.assertEqual("node-000", base["result"]["results"][0]["node_id"])
        self.enable(candidate_limit=40)
        execution = self.execute(plan, view)
        by_id = {row["node_id"]: row for row in execution["result"]["results"]}
        self.assertEqual("node-000", execution["result"]["results"][0]["node_id"])
        # Raw model rank alone selects the less relevant base-rank-30 item.
        # Fusion records that disagreement rather than dropping the base signal.
        self.assertEqual(1, by_id["node-029"]["lanes"]["reranker"]["rank"])
        self.assertEqual(2, by_id["node-000"]["lanes"]["reranker"]["rank"])
        self.assertEqual(1.0 / 61 + 1.0 / 62, by_id["node-000"]["fusion"]["score"])
        self.assertGreater(by_id["node-000"]["fusion"]["score"], by_id["node-029"]["fusion"]["score"])
        self.assertEqual([], execution["identity_resolutions"])
        self.assertEqual(0, execution["result"]["lanes"]["graph"]["seeds"])

    def test_reranker_cannot_override_exact_alias_priority(self) -> None:
        self.enable()
        plan = semantic_plan()
        for query, expected_status in (("Sigma algebra", "exact"), ("西格玛代数", "alias")):
            with self.subTest(status=expected_status):
                plan["identity_queries"] = [query]
                execution = self.execute(plan)
                self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
                self.assertEqual(expected_status, execution["identity_resolutions"][0]["status"])
                self.assertEqual(-1.0, execution["result"]["results"][0]["lanes"]["reranker"]["score"])
                self.assertIn("authoritative", execution["result"]["results"][0]["fusion"]["explanation"][0])

    def test_bad_score_counts_shapes_numbers_are_explicit_failures(self) -> None:
        self.enable()
        for scores in ([], [1.0], [1.0, 2.0, 3.0, 4.0], [True] * 3, [float("nan")] * 3,
                       [float("inf")] * 3, [[1.0]] * 3, ["one"] * 3, [10 ** 1000] * 3):
            with self.subTest(scores_type=type(scores[0]).__name__ if scores else "empty"):
                self.adapter.bad_scores = scores
                with self.assertRaisesRegex(RetrievalError, "invalid-reranker-scores"):
                    self.execute()
        self.adapter.bad_scores = None
        with patch.object(self.adapter, "score_pairs", side_effect=RuntimeError("private diagnostic")):
            with self.assertRaisesRegex(RetrievalError, "reranker-model-failed") as caught:
                self.execute()
            self.assertNotIn("private diagnostic", str(caught.exception))

    def test_reranker_descriptor_drift_and_bad_descriptors_fail_closed(self) -> None:
        self.enable()
        self.adapter.reranker_descriptor["revision"] = ""
        with self.assertRaisesRegex(RetrievalError, "invalid-model-descriptor"):
            self.execute()
        self.adapter.reranker_descriptor["revision"] = "pair-v1"
        score = self.adapter.score_pairs
        def changing(question: str, documents: list[str]) -> list[float]:
            values = score(question, documents)
            self.adapter.reranker_descriptor["revision"] = "pair-v2"
            return values
        with (
            patch.object(self.adapter, "score_pairs", side_effect=changing),
            self.assertRaisesRegex(RetrievalError, "model-descriptor-changed"),
        ):
            self.execute()

    def test_reranker_candidate_ids_namespace_generation_and_constructor_are_bounded(self) -> None:
        self.enable(candidate_limit=2)
        for ids in (["unknown"], ["measure", "measure"], list(self.view.nodes)):
            with self.subTest(ids=ids), self.assertRaisesRegex(SemanticRetrievalError, "invalid-reranker-candidates"):
                self.service.rerank(self.view, namespace="personal", question="test", candidate_ids=ids)
        with self.assertRaisesRegex(SemanticRetrievalError, "namespace-conflict"):
            self.service.rerank(self.view, namespace="other", question="test", candidate_ids=["measure"])
        with self.assertRaisesRegex(SemanticRetrievalError, "stale-generation"):
            self.service.rerank(self.view, namespace="personal", question="test", candidate_ids=["measure"], expected_snapshot_sha256="b" * 64)
        for rerank, limit in (("yes", 50), (True, 0), (True, 501), (True, True)):
            with self.subTest(rerank=rerank, limit=limit), self.assertRaisesRegex(SemanticRetrievalError, "invalid-ranking-configuration"):
                SemanticRankingService(self.adapter, cache_dir=self.cache_dir, rerank=rerank, candidate_limit=limit)

    def test_source_change_during_pair_inference_fails_closed(self) -> None:
        self.enable()
        score = self.adapter.score_pairs
        def changing(question: str, documents: list[str]) -> list[float]:
            values = score(question, documents)
            self.view.nodes["measure"]["text"] = "Changed source."
            return values
        with (
            patch.object(self.adapter, "score_pairs", side_effect=changing),
            self.assertRaisesRegex(RetrievalError, "stale-generation"),
        ):
            self.execute()

    def test_reranker_filters_and_legacy_mode_keep_original_question_and_candidates(self) -> None:
        nodes = fixture_nodes()
        for node_id, type_ in (("stale", "knowledge"), ("orphan", "knowledge")):
            node = copy.deepcopy(nodes[1])
            node.update({"id": node_id, "label": node_id, "type": type_})
            if node_id == "stale":
                node["properties"]["curation_status"] = "needs-review"
            if node_id == "orphan":
                node["properties"]["source_status"] = "orphaned"
                node["provenance"]["active"] = False
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
        self.enable()
        execution = execute_retrieval_plan(view, semantic_plan(), plan_mode="legacy", ranking_service=self.service)
        self.assertEqual("legacy", execution["plan_mode"])
        candidates = execution["result"]["ranking"]["reranker"]["candidates"]
        self.assertEqual({"sigma-algebra", "measure", "absolute-continuity"}, {item["node_id"] for item in candidates})
        self.assertEqual(semantic_plan()["question"], self.adapter.pair_calls[-1][0])

    def test_reranker_empty_pool_skips_pair_inference(self) -> None:
        self.enable()
        execution = self.execute(view=GraphView.from_snapshot(snapshot_with([], [])))
        self.assertEqual([], execution["result"]["ranking"]["reranker"]["candidates"])
        self.assertEqual([], self.adapter.pair_calls)
        self.assertEqual(0, execution["result"]["lanes"]["reranker"]["results"])
        self.assertEqual(execution, validate_contract(execution))

    def test_reranker_context_validates_query_and_source_bindings(self) -> None:
        self.enable()
        execution = self.execute()
        context = build_context_from_execution(self.view, execution, plan=semantic_plan())
        self.assertEqual("absolute-continuity", context["nodes"][0]["id"])
        for key in ("node_sha256", "document_sha256"):
            changed = copy.deepcopy(execution)
            changed["result"]["ranking"]["reranker"]["candidates"][0][key] = "b" * 64
            with self.subTest(key=key), self.assertRaisesRegex(RetrievalError, "stale-generation"):
                build_context_from_execution(self.view, changed, plan=semantic_plan())

    def test_reranker_contract_requires_matching_lane_source_pool_and_scores(self) -> None:
        self.enable()
        execution = self.execute()
        changes = []
        changed = copy.deepcopy(execution)
        changed["result"]["lanes"].pop("reranker")
        changes.append(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["reranker"]["candidates"].append(copy.deepcopy(changed["result"]["ranking"]["reranker"]["candidates"][0]))
        changes.append(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["reranker"]["candidates"][0]["score"] += 1.0
        changes.append(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["reranker"]["query_sha256"] = "b" * 64
        changes.append(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["results"][0]["fusion"]["score"] = changed["result"]["results"][0]["lanes"]["reranker"]["score"]
        changes.append(changed)
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["reranker"].pop("fusion")
        changes.append(changed)
        for index, changed in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(ContractError):
                validate_contract(changed)


if __name__ == "__main__":
    unittest.main()
