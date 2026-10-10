from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import ContractError, validate_contract
from kgdistiller.query import GraphView, search
from kgdistiller.retrieval import (
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
)
from kgdistiller.semantic_retrieval import (
    SemanticRankingService,
    SemanticRetrievalError,
    model_cache_name,
    search_document,
)
from tests.knowledge_fixture import memory_view, node_record
from tests.test_query import fixture_edges, fixture_nodes
from tests.test_retrieval import retrieval_plan

REPO_ROOT = Path(__file__).resolve().parents[1]


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
        self.view = memory_view(fixture_nodes(), fixture_edges())

    def execute(self, plan: dict | None = None, view: GraphView | None = None) -> dict:
        return execute_retrieval_plan(view or self.view, plan or semantic_plan(), ranking_service=self.service)


class SemanticRetrievalTest(RetrievalFixture):
    def test_embedding_recalls_beyond_keywords_but_never_resolves_or_seeds_identity(self) -> None:
        original = copy.deepcopy(self.view.nodes)
        plan = semantic_plan()
        plan["lexical_queries"] = ["image augmentation"]
        self.assertEqual([], search(self.view, plan["lexical_queries"][0]))
        execution = self.execute(plan)
        self.assertEqual("kgdistiller-search-execution-v2", execution["schema"])
        self.assertEqual("kgdistiller-search-result-v2", execution["result"]["schema"])
        self.assertEqual("measure", execution["result"]["results"][0]["node_id"])
        self.assertEqual([], execution["identity_resolutions"])
        self.assertEqual(0, execution["result"]["lanes"]["graph"]["seeds"])
        self.assertEqual(0, execution["result"]["lanes"]["lexical"]["results"])
        self.assertEqual([[plan["question"]]], self.adapter.query_calls)
        for row in execution["result"]["results"]:
            self.assertEqual({"embedding"}, set(row["lanes"]))
            self.assertEqual([], row["seed_evidence"])
            self.assertEqual([], row["path_evidence"])
        provenance = execution["result"]["ranking"]["embedding"]
        self.assertEqual("plan.question", provenance["query_source"])
        self.assertEqual(
            {"model", "projection", "document_count", "dimensions", "cache_status", "query_source"},
            set(provenance),
        )
        self.assertEqual(original, self.view.nodes)
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
                self.assertFalse((self.cache_dir / str(index)).exists())
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

    def test_every_entry_reaches_the_embedding_lane_with_its_evidence(self) -> None:
        execution = self.execute()
        self.assertEqual(
            {"sigma-algebra", "measure", "absolute-continuity"},
            {row["node_id"] for row in execution["result"]["results"]},
        )
        self.assertEqual(3, execution["result"]["ranking"]["embedding"]["document_count"])
        quoted = memory_view([node_record("quoted", "Quoted", evidence="verbatimquote from the source")])
        self.assertIn("verbatimquote from the source", search_document(quoted.nodes["quoted"]))

    def test_unknown_duplicate_ids_are_rejected(self) -> None:
        for ids in (["unknown"], ["measure", "measure"]):
            with self.subTest(ids=ids), self.assertRaisesRegex(SemanticRetrievalError, "invalid-semantic-candidates"):
                self.service.rank(self.view, question="test", eligible_ids=ids)

    def test_empty_graph_does_not_encode_or_write(self) -> None:
        empty = memory_view([])
        execution = self.execute(view=empty)
        provenance = execution["result"]["ranking"]["embedding"]
        self.assertEqual([], execution["result"]["results"])
        self.assertEqual("empty", provenance["cache_status"])
        self.assertEqual(0, provenance["document_count"])
        self.assertEqual(0, provenance["dimensions"])
        self.assertEqual(0, self.adapter.document_calls)
        self.assertEqual([], self.adapter.query_calls)
        self.assertEqual([], list(self.cache_dir.iterdir()))

    def test_atomic_write_failure_leaves_no_partial_cache(self) -> None:
        with (
            patch("kgdistiller.semantic_retrieval.os.replace", side_effect=OSError("disk unavailable")),
            self.assertRaisesRegex(RetrievalError, "vector-cache-unwritable"),
        ):
            self.execute()
        self.assertEqual([], [path for path in self.cache_dir.rglob("*") if path.is_file()])

    def test_cache_cannot_be_written_into_the_entry_directory(self) -> None:
        project = self.cache_dir / "project"
        entries = project / ".knowledge/entries"
        entries.mkdir(parents=True)
        view = replace(self.view, repo_root=project)
        service = SemanticRankingService(self.adapter, cache_dir=entries / "vectors")
        with self.assertRaisesRegex(RetrievalError, "cache-store-conflict"):
            execute_retrieval_plan(view, semantic_plan(), ranking_service=service)
        self.assertFalse((entries / "vectors").exists())
        self.assertEqual(0, self.adapter.document_calls)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_and_symlink_cache_are_rejected_without_blocking(self) -> None:
        self.execute()
        path = self.cache_dir / model_cache_name(self.adapter.descriptor)
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

    def test_v2_context_revalidates_nodes_and_keeps_complete_records(self) -> None:
        execution = self.execute()
        context = build_context_from_execution(self.view, execution, plan=semantic_plan(), token_budget=6000)
        self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
        self.assertEqual("kgdistiller-search-result-v2", context["search_result_schema"])
        self.assertLessEqual(context["budget"]["estimated_tokens"], 6000)
        self.assertEqual(self.view.nodes["measure"], next(node for node in context["nodes"] if node["id"] == "measure"))
        smaller = memory_view([node for node in fixture_nodes() if node["id"] != "measure"])
        with self.assertRaisesRegex(RetrievalError, "stale-execution"):
            build_context_from_execution(smaller, execution, plan=semantic_plan())
        changed = copy.deepcopy(execution)
        changed["result"]["ranking"]["embedding"]["document_count"] = -1
        with self.assertRaisesRegex(RetrievalError, "invalid-execution"):
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


class VectorCacheTest(RetrievalFixture):
    def cache_path(self) -> Path:
        return self.cache_dir / model_cache_name(self.adapter.descriptor)

    def cache(self) -> dict:
        return json.loads(self.cache_path().read_text(encoding="utf-8"))

    def test_cache_file_is_readable_and_keyed_by_node_id(self) -> None:
        first = self.execute()["result"]["ranking"]["embedding"]
        self.assertEqual("miss", first["cache_status"])
        self.assertEqual("vectors-multilingual-fixture.json", self.cache_path().name)
        cache = self.cache()
        self.assertEqual({"schema", "model", "records"}, set(cache))
        self.assertEqual("kgdistiller-vector-cache-v1", cache["schema"])
        self.assertEqual(self.adapter.descriptor, cache["model"])
        self.assertEqual(sorted(self.view.nodes), sorted(cache["records"]))
        for node_id, record in cache["records"].items():
            self.assertEqual({"text", "vector"}, set(record))
            self.assertEqual(search_document(self.view.nodes[node_id]), record["text"])
        self.assertEqual([path.name for path in self.cache_dir.iterdir()], [self.cache_path().name])

    def test_unchanged_nodes_reuse_vectors_without_rewriting(self) -> None:
        self.execute()
        before = self.cache_path().stat().st_mtime_ns
        second = self.execute()["result"]["ranking"]["embedding"]
        self.assertEqual("hit", second["cache_status"])
        self.assertEqual(1, self.adapter.document_calls)
        self.assertEqual(0, self.service.last_cache_stats["document_inference_items"])
        self.assertEqual(before, self.cache_path().stat().st_mtime_ns)
        fresh = SemanticRankingService(self.adapter, cache_dir=self.cache_dir)
        execute_retrieval_plan(self.view, semantic_plan(), ranking_service=fresh)
        self.assertEqual(1, self.adapter.document_calls)

    def test_text_change_reembeds_only_that_node(self) -> None:
        self.execute()
        nodes = fixture_nodes()
        nodes[1]["summary"] = "A countably additive set function on a sigma algebra, revised."
        changed = memory_view(nodes)
        provenance = self.execute(view=changed)["result"]["ranking"]["embedding"]
        self.assertEqual("miss", provenance["cache_status"])
        self.assertEqual([search_document(changed.nodes["measure"])], self.adapter.document_inputs[-1])
        self.assertEqual(1, self.service.last_cache_stats["document_inference_items"])
        self.assertEqual(search_document(changed.nodes["measure"]), self.cache()["records"]["measure"]["text"])

    def test_missing_nodes_are_embedded_and_vanished_ids_dropped(self) -> None:
        self.execute()
        nodes = [node for node in fixture_nodes() if node["id"] != "absolute-continuity"]
        extra = node_record("outer-measure", "Outer measure", "Subadditive set function.")
        view = memory_view([*nodes, extra])
        self.execute(view=view)
        self.assertEqual([search_document(view.nodes["outer-measure"])], self.adapter.document_inputs[-1])
        self.assertEqual(sorted(view.nodes), sorted(self.cache()["records"]))
        self.assertEqual(1, self.service.last_cache_stats["dropped_records"])

    def test_model_change_discards_all_records(self) -> None:
        self.execute()
        self.adapter.descriptor["revision"] = "frozen-v2"
        self.execute()
        self.assertEqual(2, self.adapter.document_calls)
        self.assertEqual(3, len(self.adapter.document_inputs[-1]))
        self.assertEqual("frozen-v2", self.cache()["model"]["revision"])
        self.adapter.descriptor["model"] = "other-fixture"
        self.execute()
        self.assertTrue((self.cache_dir / "vectors-other-fixture.json").is_file())
        self.assertEqual(3, self.adapter.document_calls)

    def test_malformed_cache_fails_without_fallback(self) -> None:
        self.execute()
        for mutate in (
            lambda payload: payload["records"]["measure"].update(vector=[0.0, 0.0]),
            lambda payload: payload["records"]["measure"].update(vector=[1.0]),
            lambda payload: payload["records"]["measure"].pop("text"),
            lambda payload: payload.update(extra=True),
        ):
            with self.subTest(mutate=mutate):
                payload = self.cache()
                original = copy.deepcopy(payload)
                mutate(payload)
                self.cache_path().write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(RetrievalError, "invalid-vector-cache"):
                    self.execute()
                self.cache_path().write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(1, self.adapter.document_calls)


class RerankerTest(RetrievalFixture):
    def setUp(self) -> None:
        super().setUp()
        self.adapter = FakeReranker()
        self.service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir)

    def enable(self, *, candidate_limit: int = 50) -> None:
        self.service = SemanticRankingService(self.adapter, cache_dir=self.cache_dir, rerank=True, candidate_limit=candidate_limit)

    def test_reranker_optin_changes_order_with_bound_scores_without_identity(self) -> None:
        original = copy.deepcopy(self.view.nodes)
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
        for candidate, document in zip(provenance["candidates"], self.adapter.pair_calls[0][1]):
            self.assertEqual({"node_id", "score"}, set(candidate))
            self.assertEqual(search_document(self.view.nodes[candidate["node_id"]]), document)
        self.assertEqual(original, self.view.nodes)
        self.assertEqual(execution, validate_contract(execution))

    def test_candidate_pool_is_truncated_before_final_limit_not_after(self) -> None:
        nodes = [
            node_record(f"node-{index:03d}", f"Node {index:03d}", "A collection closed under countable union and complement.")
            for index in range(40)
        ]
        view = memory_view(nodes)
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
        nodes = [
            node_record(
                f"node-{index:03d}", f"Node {index:03d}",
                "Background about image augmentation, not measurable-set size." if index
                else "A countably additive set function.",
            )
            for index in range(40)
        ]
        view = memory_view(nodes)
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

    def test_reranker_candidate_ids_and_constructor_are_bounded(self) -> None:
        self.enable(candidate_limit=2)
        for ids in (["unknown"], ["measure", "measure"], list(self.view.nodes)):
            with self.subTest(ids=ids), self.assertRaisesRegex(SemanticRetrievalError, "invalid-reranker-candidates"):
                self.service.rerank(self.view, question="test", candidate_ids=ids)
        for rerank, limit in (("yes", 50), (True, 0), (True, 501), (True, True)):
            with self.subTest(rerank=rerank, limit=limit), self.assertRaisesRegex(SemanticRetrievalError, "invalid-ranking-configuration"):
                SemanticRankingService(self.adapter, cache_dir=self.cache_dir, rerank=rerank, candidate_limit=limit)

    def test_reranker_query_mode_keeps_original_question_and_candidates(self) -> None:
        nodes = [*fixture_nodes(), node_record("other", "Other", "A countably additive set function.")]
        view = memory_view(nodes)
        self.enable()
        execution = execute_retrieval_plan(view, semantic_plan(), plan_mode="query", ranking_service=self.service)
        self.assertEqual("query", execution["plan_mode"])
        candidates = execution["result"]["ranking"]["reranker"]["candidates"]
        self.assertEqual({"sigma-algebra", "measure", "absolute-continuity", "other"}, {item["node_id"] for item in candidates})
        self.assertEqual(semantic_plan()["question"], self.adapter.pair_calls[-1][0])

    def test_reranker_empty_pool_skips_pair_inference(self) -> None:
        self.enable()
        execution = self.execute(view=memory_view([]))
        self.assertEqual([], execution["result"]["ranking"]["reranker"]["candidates"])
        self.assertEqual([], self.adapter.pair_calls)
        self.assertEqual(0, execution["result"]["lanes"]["reranker"]["results"])
        self.assertEqual(execution, validate_contract(execution))

    def test_reranker_context_revalidates_candidates(self) -> None:
        self.enable()
        execution = self.execute()
        context = build_context_from_execution(self.view, execution, plan=semantic_plan())
        self.assertEqual("absolute-continuity", context["nodes"][0]["id"])
        smaller = memory_view([node for node in fixture_nodes() if node["id"] != "sigma-algebra"])
        with self.assertRaisesRegex(RetrievalError, "stale-execution"):
            build_context_from_execution(smaller, execution, plan=semantic_plan())

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
        changed["result"]["ranking"]["reranker"]["projection"] = "other-projection"
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
