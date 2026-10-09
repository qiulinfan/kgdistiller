from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.alignment import node_fingerprint
from kgdistiller.cli import (
    GraphState,
    main,
    make_artifacts,
    make_graph_retrieval_policy,
    make_ranking_service,
    parse_args,
    write_artifacts,
)
from kgdistiller.contracts import canonical_json, sha256_json
from kgdistiller.query import load_graph_view
from kgdistiller.retrieval import (
    MAX_RETRIEVAL_PLAN_BYTES,
    SEARCH_EXECUTION_SCHEMA,
    SEARCH_RESULT_SCHEMA,
    RetrievalError,
)
from kgdistiller.semantic_retrieval import (
    SemanticRetrievalError,
    search_document,
)
from kgdistiller.support_selection import make_support_selection
from tests.test_query import (
    fixture_edges,
    fixture_nodes,
    write_fixture_graph,
)
from tests.test_semantic_retrieval import FakeEmbedding


class FakeReranker(FakeEmbedding):
    def __init__(self) -> None:
        super().__init__()
        self.descriptor["model"] = "fixture-reranker-embedding"
        self.pair_calls: list[tuple[str, list[str]]] = []

    def metadata(self, kind: str) -> dict:
        if kind == "reranker":
            return {"provider": "test", "model": "fixture-reranker", "revision": "frozen-v1",
                    "inference": {"mode": "cross-encoder-logit"}}
        return super().metadata(kind)

    def score_pairs(self, question: str, documents: list[str]) -> list[float]:
        self.pair_calls.append((question, list(documents)))
        return [3.0 if "closed under countable union" in document else 0.0 for document in documents]

    def encode_documents(self, documents: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [[1.0, 0.0] if "countably additive" in text else
                [0.8, 0.2] if "closed under countable union" in text else [0.0, 1.0]
                for text in documents]


def retrieval_plan() -> dict:
    return {
        "schema": "kgdistiller-retrieval-plan-v1",
        "question": "How does a measure depend on a sigma algebra?",
        "namespace": "personal",
        "identity_queries": ["西格玛代数"],
        "lexical_queries": ["countably additive"],
        "graph": {
            "seed_ids": [],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 2,
            "strategy": "hybrid",
        },
        "filters": {
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


def repository_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def write_source_evidence_manifest(
    root: Path, documents: dict[str, bytes], *, hash_mode: str = "raw-utf8"
) -> Path:
    sources = root / "raw-sources"
    sources.mkdir(exist_ok=True)
    records = []
    for doc_id, raw in documents.items():
        path = sources / f"{doc_id}.txt"
        path.write_bytes(raw)
        hashed = raw if hash_mode == "raw-utf8" else raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        records.append({"doc_id": doc_id, "path": path.name,
                        "source_url": f"https://example.org/{doc_id}",
                        "source_version": "fixture-v2", "source_type": "paper-text",
                        "expected_sha256": hashlib.sha256(hashed).hexdigest()})
    manifest = root / "evidence-manifest.json"
    manifest.write_text(json.dumps({"schema": "kgdistiller-source-evidence-manifest-v1",
                                   "root": sources.name, "hash_mode": hash_mode,
                                   "documents": records}, ensure_ascii=False), encoding="utf-8")
    return manifest


def write_graph_retrieval_fixture(root: Path) -> Path:
    """Source-backed chain with one deliberately unverified side edge."""
    graph = root / ".knowledge" / "graph"
    nodes = fixture_nodes()
    nodes[1]["properties"]["conditions"] = ["Domain is a sigma algebra."]
    unverified = copy.deepcopy(nodes[2])
    unverified.update(id="unverified-result", label="Unverified destination", text="An unaudited relation destination.")
    nodes.append(unverified)
    edges = fixture_edges()
    for edge in edges:
        edge["confidence"] = "high"
    edges.append({"source": "sigma-algebra", "relation": "implies", "target": "unverified-result", "evidence": "Fixture unverified statement.", "curation_status": "current", "confidence": "unverified"})
    state = GraphState({node["id"]: node for node in nodes}, {(edge["source"], edge["relation"], edge["target"]): edge for edge in edges}, [], {})
    write_artifacts(graph, make_artifacts(state, {}))
    return graph


def assert_compact_source_bundle(test: unittest.TestCase, bundle: dict, graph: Path, budget: int) -> None:
    view = load_graph_view(graph)
    test.assertEqual("kgdistiller-context-bundle-v3", bundle["schema"])
    test.assertEqual(view.snapshot["snapshot_sha256"], bundle["snapshot_sha256"])
    test.assertEqual(view.snapshot["graph"]["sha256"], bundle["graph_sha256"])
    test.assertFalse(bundle["confidence_is_independent_review"])
    for record in bundle["nodes"]:
        original = view.nodes[record["node_id"]]
        content = record["content"]
        test.assertEqual(original.get("text"), content.get("text"))
        test.assertEqual(original.get("properties", {}).get("conditions"), content.get("properties", {}).get("conditions"))
        test.assertEqual(original.get("provenance"), content.get("provenance"))
        test.assertEqual(node_fingerprint(original), record["node_sha256"])
        test.assertEqual(hashlib.sha256(search_document(original).encode("utf-8")).hexdigest(), record["document_sha256"])
        test.assertEqual(sha256_json(content), record["projection_sha256"])
    source_edges = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges}
    for record in bundle["edges"]:
        edge = record["content"]
        test.assertEqual(source_edges[(edge["source"], edge["relation"], edge["target"])], edge)
        test.assertEqual(sha256_json(edge), record["edge_sha256"])
    for packet in bundle["support_packets"]:
        test.assertFalse(packet["logical_entailment"])
        for index, step in enumerate(packet["steps"]):
            edge = bundle["edges"][step["edge_index"]]["content"]
            left, right = (edge["source"], edge["target"]) if step["direction"] == "outgoing" else (edge["target"], edge["source"])
            test.assertEqual((left, right), (packet["nodes"][index], packet["nodes"][index + 1]))
    actual_bytes = len(canonical_json(bundle).encode("utf-8"))
    test.assertEqual(actual_bytes, bundle["budget"]["estimated_tokens"])
    test.assertLessEqual(actual_bytes, budget)


class RetrievalCliParserTest(unittest.TestCase):
    def parse(self, *arguments: str):
        with patch.object(sys, "argv", ["kgdistiller", *arguments]):
            return parse_args()

    def assert_parse_error(self, *arguments: str) -> None:
        with (
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            self.parse(*arguments)
        self.assertEqual(2, raised.exception.code)

    def run_cli(self, root: Path, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            sys,
            "argv",
            ["kgdistiller", "--repo-root", str(root), *arguments],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_agent_search_and_context_require_query_xor_plan(self) -> None:
        legacy = self.parse("agent", "search", "countable closure")
        planned = self.parse("agent", "search", "--plan", "retrieval.json")
        context = self.parse(
            "agent", "context", "why alpha", "--namespace", "paper:fixture"
        )

        self.assertEqual("countable closure", legacy.query)
        self.assertIsNone(legacy.plan)
        self.assertEqual(Path("retrieval.json"), planned.plan)
        self.assertEqual("paper:fixture", context.namespace)
        self.assert_parse_error("agent", "search")
        self.assert_parse_error(
            "agent", "search", "countable closure", "--plan", "retrieval.json"
        )
        self.assert_parse_error(
            "agent", "context", "why alpha", "--plan", "retrieval.json"
        )
        self.assert_parse_error(
            "agent", "search", "--plan", "retrieval.json", "--limit", "5"
        )

    def test_graph_controls_are_explicit_bounded_and_independent_of_models(self) -> None:
        for command in (("agent", "search", "collection"), ("agent", "context", "collection")):
            plain = self.parse(*command)
            self.assertFalse(plain.graph_retrieval)
            self.assertIsNone(make_graph_retrieval_policy(plain))
            opted = self.parse(*command, "--graph-retrieval")
            self.assertFalse(opted.embedding)
            self.assertEqual((5, "high-confidence"), (opted.graph_seed_candidates, opted.graph_edge_policy))
            policy = make_graph_retrieval_policy(opted)
            self.assertEqual((5, "high-confidence"), (policy.candidate_limit, policy.edge_policy))
            for controls in (("--graph-seed-candidates", "5"), ("--graph-edge-policy", "high-confidence"),
                             ("--graph-seed-candidates=5",), ("--graph-edge-policy=high-confidence",),
                             ("--graph-seed-c", "5"), ("--graph-edge-p", "high-confidence")):
                with self.subTest(command=command, controls=controls):
                    self.assert_parse_error(*command, *controls)
            self.assert_parse_error(*command, "--graph-retrieval", "--graph-seed-candidates", "0")
            self.assert_parse_error(*command, "--graph-retrieval", "--graph-seed-candidates", "33")
            self.assert_parse_error(*command, "--graph-retrieval", "--graph-edge-policy", "reviewed")
        literal = self.parse("agent", "search", "--", "--graph-edge-policy=high-confidence")
        self.assertEqual("--graph-edge-policy=high-confidence", literal.query)
        self.assertFalse(literal.graph_retrieval)
        self.assert_parse_error("agent", "resolve", "measure", "--graph-retrieval")
        invalid = self.parse("agent", "search", "collection", "--graph-retrieval")
        invalid.graph_seed_candidates = True
        with self.assertRaisesRegex(RetrievalError, "between 1 and 32"):
            make_graph_retrieval_policy(invalid)

    def test_graph_cli_search_and_context_keep_candidate_and_path_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-graph-cli-") as raw:
            root = Path(raw)
            graph = write_graph_retrieval_fixture(root)
            before = repository_bytes(graph)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter", side_effect=AssertionError("graph-only search must not construct a model")):
                status, output, error = self.run_cli(root, "agent", "search", "collection", "--graph-retrieval", "--graph-seed-candidates", "1", "--depth", "1")
                self.assertEqual(0, status, error)
                execution = json.loads(output)
                self.assertEqual("kgdistiller-search-execution-v3", execution["schema"])
                self.assertEqual("kgdistiller-search-result-v3", execution["result"]["schema"])
                metadata = execution["result"]["graph_retrieval"]
                self.assertEqual([], metadata["seeds"]["identity"])
                self.assertEqual(["sigma-algebra"], [item["node_id"] for item in metadata["seeds"]["candidate"]])
                self.assertFalse(metadata["seeds"]["candidate"][0]["identity_authority"])
                self.assertTrue(metadata["ppr"]["converged"])
                self.assertFalse(metadata["policy"]["confidence_is_independent_review"])
                rows = {row["node_id"]: row for row in execution["result"]["results"]}
                self.assertEqual({"sigma-algebra"}, set(rows))
                self.assertNotIn("graph", rows["sigma-algebra"]["lanes"])
                plain_status, plain_output, plain_error = self.run_cli(root, "agent", "search", "collection")
                self.assertEqual(0, plain_status, plain_error)
                self.assertEqual(json.loads(plain_output)["result"]["results"], execution["result"]["results"])
                neighbors = {row["node_id"]: row for row in metadata["neighbors"]}
                self.assertEqual({"measure"}, set(neighbors))
                self.assertEqual("navigation", neighbors["measure"]["fusion"]["method"])
                self.assertEqual(0.0, neighbors["measure"]["fusion"]["score"])
                path = neighbors["measure"]["path_evidence"][0]
                self.assertEqual(["sigma-algebra", "measure"], path["nodes"])
                self.assertEqual("A measure is defined on a sigma algebra.", path["steps"][0]["evidence"])
                self.assertFalse(path["logical_entailment"])
                self.assertEqual("candidate", neighbors["measure"]["seed_evidence"][0]["origin"])
                plan = retrieval_plan()
                plan["question"] = "collection"
                plan["identity_queries"] = []
                plan["lexical_queries"] = ["collection"]
                plan_path = root / "graph-plan.json"
                plan_path.write_text(json.dumps(plan))
                status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--graph-retrieval", "--graph-seed-candidates", "1", "--budget", "18000")
                self.assertEqual(0, status, error)
                bundle = json.loads(output)
                self.assertEqual("kgdistiller-context-bundle-v2", bundle["schema"])
                self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
                packet = next(item for item in bundle["support_packets"] if item["node_id"] == "absolute-continuity")
                self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"], packet["nodes"])
                self.assertEqual(2, len(packet["path"]["steps"]))
                self.assertEqual(["Domain is a sigma algebra."], next(node for node in bundle["nodes"] if node["id"] == "measure")["properties"]["conditions"])
                self.assertEqual(2, len(bundle["edges"]))
                self.assertLessEqual(bundle["budget"]["estimated_tokens"], 18000)
            self.assertEqual(before, repository_bytes(graph))

    def test_graph_cli_gate_can_be_explicitly_relaxed_without_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-graph-cli-") as raw:
            root = Path(raw)
            write_graph_retrieval_fixture(root)
            status, output, error = self.run_cli(root, "agent", "search", "collection", "--graph-retrieval", "--graph-seed-candidates", "1", "--graph-edge-policy", "current", "--graph-strategy", "bfs")
            self.assertEqual(0, status, error)
            execution = json.loads(output)
            self.assertEqual(["sigma-algebra"], [row["node_id"] for row in execution["result"]["results"]])
            self.assertIn("unverified-result", [row["node_id"] for row in execution["result"]["graph_retrieval"]["neighbors"]])
            self.assertEqual("current", execution["result"]["graph_retrieval"]["policy"]["edge_policy"])
            self.assertEqual([], execution["result"]["graph_retrieval"]["seeds"]["identity"])

    def test_graph_cli_composes_with_embedding_without_promoting_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-graph-cli-") as raw:
            root = Path(raw)
            graph = write_graph_retrieval_fixture(root)
            before = repository_bytes(graph)
            adapter = FakeReranker()
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter", return_value=adapter):
                plain_status, plain_output, plain_error = self.run_cli(root, "agent", "search", "如何给可测集合赋予大小？", "--embedding", "--rerank", "--rerank-candidates", "1")
                self.assertEqual(0, plain_status, plain_error)
                status, output, error = self.run_cli(root, "agent", "search", "如何给可测集合赋予大小？", "--embedding", "--rerank", "--rerank-candidates", "1", "--graph-retrieval", "--graph-seed-candidates", "1", "--depth", "1")
            self.assertEqual(0, status, error)
            execution = json.loads(output)
            self.assertEqual("kgdistiller-search-execution-v3", execution["schema"])
            self.assertIn("embedding", execution["result"]["ranking"])
            plain_execution = json.loads(plain_output)
            self.assertEqual(plain_execution["result"]["results"], execution["result"]["results"])
            self.assertEqual(plain_execution["result"]["ranking"]["reranker"]["candidates"], execution["result"]["ranking"]["reranker"]["candidates"])
            self.assertEqual(1, len(adapter.pair_calls))
            self.assertEqual([search_document(load_graph_view(graph).nodes["measure"])], adapter.pair_calls[0][1])
            self.assertEqual([["如何给可测集合赋予大小？"]], adapter.query_calls)
            self.assertEqual([], execution["result"]["graph_retrieval"]["seeds"]["identity"])
            self.assertEqual(["measure"], [item["node_id"] for item in execution["result"]["graph_retrieval"]["seeds"]["candidate"]])
            self.assertTrue(all(not item["identity_authority"] for item in execution["result"]["graph_retrieval"]["seeds"]["candidate"]))
            self.assertEqual(before, repository_bytes(graph))

    def test_compact_projection_cli_is_explicit_and_rejects_unknown_values(self) -> None:
        self.assertEqual("full", self.parse("agent", "context", "measure").context_projection)
        self.assertEqual("compact", self.parse("agent", "context", "measure", "--context-projection", "compact").context_projection)
        self.assert_parse_error("agent", "context", "measure", "--context-projection", "summary")
        self.assert_parse_error("agent", "context", "measure", "--context-projection=summary")
        self.assert_parse_error("agent", "search", "measure", "--context-projection", "compact")

    def test_compact_graph_cli_context_shares_source_edges_and_complete_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-compact-cli-") as raw:
            root = Path(raw); graph = write_graph_retrieval_fixture(root)
            before = repository_bytes(graph)
            plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"])
            plan_path = root / "graph-plan.json"; plan_path.write_text(json.dumps(plan))
            controls = ("agent", "context", "--plan", str(plan_path), "--graph-retrieval", "--graph-seed-candidates", "1")
            status, output, error = self.run_cli(root, *controls, "--context-projection", "compact", "--budget", "12000")
            self.assertEqual(0, status, error)
            bundle = json.loads(output)
            assert_compact_source_bundle(self, bundle, graph, 12000)
            self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
            records = {record["node_id"]: record for record in bundle["nodes"]}
            self.assertEqual(["Domain is a sigma algebra."], records["measure"]["content"]["properties"]["conditions"])
            self.assertEqual(2, len(bundle["edges"]))
            routes = {packet["node_id"]: packet for packet in bundle["support_packets"] if packet["kind"] == "graph-path"}
            self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"], routes["absolute-continuity"]["nodes"])
            self.assertEqual(routes["measure"]["steps"][0]["edge_index"], routes["absolute-continuity"]["steps"][0]["edge_index"])
            self.assertTrue(all(set(step) == {"edge_index", "direction"} for packet in routes.values() for step in packet["steps"]))
            status, output, error = self.run_cli(root, *controls, "--budget", "12000")
            self.assertEqual(0, status, error)
            self.assertEqual("kgdistiller-context-bundle-v2", json.loads(output)["schema"])
            status, output, error = self.run_cli(root, *controls, "--context-projection", "compact", "--budget", "1800")
            self.assertEqual(0, status, error)
            tight = json.loads(output); assert_compact_source_bundle(self, tight, graph, 1800)
            self.assertGreater(tight["omitted_support_packets"], 0)
            self.assertTrue(tight["gaps"] or tight["diagnostics_truncated"])
            self.assertEqual(before, repository_bytes(graph))

    def test_compact_plain_cli_v1_v2_preserve_full_defaults_and_source_conditions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-compact-cli-") as raw:
            root = Path(raw); graph = write_graph_retrieval_fixture(root)
            before = repository_bytes(graph)
            for models, query, expected_execution in (((), "measure", "kgdistiller-search-execution-v1"), (("--embedding",), "如何给可测集合赋予大小？", "kgdistiller-search-execution-v2")):
                with self.subTest(schema=expected_execution), patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter", return_value=FakeEmbedding()):
                    status, output, error = self.run_cli(root, "agent", "context", query, *models, "--context-projection", "compact", "--budget", "12000")
                    self.assertEqual(0, status, error)
                    bundle = json.loads(output); assert_compact_source_bundle(self, bundle, graph, 12000)
                    self.assertEqual(expected_execution, bundle["search_execution_schema"])
                    self.assertEqual([], bundle["edges"])
                    self.assertTrue(all(packet["kind"] == "direct-source" and not packet["steps"] for packet in bundle["support_packets"]))
                    measure = next(record for record in bundle["nodes"] if record["node_id"] == "measure")
                    self.assertEqual(["Domain is a sigma algebra."], measure["content"]["properties"]["conditions"])
                    status, output, error = self.run_cli(root, "agent", "context", query, *models, "--budget", "12000")
                    self.assertEqual(0, status, error)
                    full = json.loads(output)
                    self.assertEqual("kgdistiller-context-bundle-v1", full["schema"])
                    self.assertEqual(expected_execution, full["search_execution_schema"])
            self.assertEqual(before, repository_bytes(graph))

    def test_support_selection_cli_context_adds_query_support_without_ranking_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-support-cli-") as raw:
            root = Path(raw); graph = write_graph_retrieval_fixture(root); source_before = repository_bytes(graph)
            plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"], limit=1)
            plan_path = root / "support-plan.json"; plan_path.write_text(json.dumps(plan))
            status, output, error = self.run_cli(root, "agent", "search", "--plan", str(plan_path))
            self.assertEqual(0, status, error); execution = json.loads(output)
            self.assertEqual(["sigma-algebra"], [row["node_id"] for row in execution["result"]["results"]])
            view = load_graph_view(graph)
            manifest = make_support_selection(view, execution, plan, [{"node_id": "measure", "requirement_ids": ["query-r2"], "reason": "Explicit caller-selected second requested aspect."}])
            manifest_path = root / "support.json"; manifest_path.write_text(json.dumps(manifest))
            for projection, expected_projection in (("full", "kgdistiller-context-projection-full-v1"), ("compact", "kgdistiller-context-projection-v1")):
                with self.subTest(projection=projection):
                    status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--support-selection", "support.json", "--context-projection", projection, "--budget", "12000")
                    self.assertEqual(0, status, error); bundle = json.loads(output)
                    assert_compact_source_bundle(self, bundle, graph, 12000)
                    self.assertEqual(expected_projection, bundle["projection"])
                    self.assertEqual(sha256_json(manifest), bundle["support_selection_sha256"])
                    self.assertEqual(sha256_json(execution), bundle["execution_sha256"])
                    packet = bundle["support_packets"][0]
                    self.assertEqual(("measure", "query-support", ["query-r2"]), (packet["node_id"], packet["selection_origin"], packet["requirement_ids"]))
                    self.assertEqual("direct-source", packet["kind"]); self.assertFalse(packet["logical_entailment"])
                    record = next(record for record in bundle["nodes"] if record["node_id"] == "measure")
                    self.assertEqual(["Domain is a sigma algebra."], record["content"]["properties"]["conditions"])
                    if projection == "full":self.assertEqual(view.nodes["measure"], record["content"])
            status, output, error = self.run_cli(root, "agent", "search", "--plan", str(plan_path))
            self.assertEqual(0, status, error); self.assertEqual(execution, json.loads(output))
            status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--budget", "12000")
            self.assertEqual(0, status, error); self.assertEqual("kgdistiller-context-bundle-v1", json.loads(output)["schema"])
            self.assertEqual(source_before, repository_bytes(graph))

    def test_support_selection_cli_graph_context_keeps_bound_route_and_support_separate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-support-cli-") as raw:
            root = Path(raw); graph = write_graph_retrieval_fixture(root)
            plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"], limit=1)
            path = root / "plan.json"; path.write_text(json.dumps(plan))
            graph_options = ("--graph-retrieval", "--graph-seed-candidates", "1")
            status, output, error = self.run_cli(root, "agent", "search", "--plan", str(path), *graph_options)
            self.assertEqual(0, status, error); execution = json.loads(output)
            manifest = make_support_selection(load_graph_view(graph), execution, plan, [{"node_id": "measure", "requirement_ids": ["query-r2"], "reason": "Explicit source support, independent of graph navigation."}])
            selection_path = root / "support.json"; selection_path.write_text(json.dumps(manifest))
            status, output, error = self.run_cli(root, "agent", "context", "--plan", str(path), *graph_options, "--support-selection", str(selection_path), "--budget", "12000")
            self.assertEqual(0, status, error); bundle = json.loads(output)
            assert_compact_source_bundle(self, bundle, graph, 12000)
            self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
            self.assertEqual(sha256_json(execution), bundle["execution_sha256"])
            self.assertEqual("query-support", bundle["support_packets"][0]["selection_origin"])
            self.assertTrue(any(packet["kind"] == "graph-path" and packet["node_id"] == "absolute-continuity" for packet in bundle["support_packets"]))
            self.assertEqual(2, len(bundle["edges"]))

    def test_support_selection_cli_rejects_invalid_binding_and_file_inputs(self) -> None:
        self.assert_parse_error("agent", "search", "collection", "--support-selection", "support.json")
        self.assert_parse_error("agent", "resolve", "measure", "--support-selection", "support.json")
        with tempfile.TemporaryDirectory(prefix="kgdistiller-support-cli-") as raw:
            root = Path(raw); graph = write_graph_retrieval_fixture(root)
            plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"], limit=1)
            plan_path = root / "plan.json"; plan_path.write_text(json.dumps(plan))
            status, output, error = self.run_cli(root, "agent", "search", "--plan", str(plan_path))
            self.assertEqual(0, status, error)
            manifest = make_support_selection(load_graph_view(graph), json.loads(output), plan, [{"node_id": "measure", "requirement_ids": ["query-r2"], "reason": "Source support."}])
            selection_path = root / "support.json"
            for key, value in (("snapshot_sha256", "b" * 64), ("identity_authority", True)):
                forged = copy.deepcopy(manifest); forged[key] = value; selection_path.write_text(json.dumps(forged))
                status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--support-selection", str(selection_path))
                self.assertEqual(1, status); self.assertEqual("", output)
                self.assertIn(json.loads(error)["code"], {"stale-generation", "invalid-support-selection"})
            forged = copy.deepcopy(manifest); forged["items"][0]["node_id"] = "unknown-source"
            selection_path.write_text(json.dumps(forged))
            status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--support-selection", str(selection_path))
            self.assertEqual(1, status); self.assertEqual("invalid-support-selection", json.loads(error)["code"])
            cases = [(root / "absent.json", None), (selection_path, b"not-json"), (selection_path, b"[]"), (selection_path, b'{"schema":"kgdistiller-support-selection-v1","items":NaN}'), (root, None)]
            if hasattr(os, "mkfifo"):
                fifo = root / "fifo.json"; os.mkfifo(fifo); cases.append((fifo, None))
            for file_path, content in cases:
                if content is not None:file_path.write_bytes(content)
                with self.subTest(path=file_path.name, content=content):
                    status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--support-selection", str(file_path))
                    self.assertEqual(1, status); self.assertEqual("", output); self.assertIn("code", json.loads(error))
            with selection_path.open("wb") as handle:handle.seek(MAX_RETRIEVAL_PLAN_BYTES); handle.write(b"x")
            status, output, error = self.run_cli(root, "agent", "context", "--plan", str(plan_path), "--support-selection", str(selection_path))
            self.assertEqual(1, status); self.assertEqual("", output); self.assertIn(json.loads(error)["code"], {"plan-too-large", "invalid-support-selection"})

    def test_embedding_is_explicit_and_model_controls_are_bounded(self) -> None:
        plain = self.parse("agent", "search", "measure")
        self.assertFalse(plain.embedding)
        self.assertEqual(("cpu", 4, 8192),
                         (plain.model_device, plain.model_batch_size, plain.model_max_length))
        for arguments in (
            ("agent", "search", "measure"),
            ("agent", "context", "--plan", "retrieval.json"),
            ("mcp",),
        ):
            with self.subTest(arguments=arguments):
                opted_in = self.parse(*arguments, "--embedding", "--models-offline")
                self.assertTrue(opted_in.embedding)
                self.assertTrue(opted_in.models_offline)
        self.assert_parse_error("agent", "search", "measure", "--embedding", "--model-device", "bogus")
        self.assert_parse_error("mcp", "--embedding", "--model-batch-size", "0")
        self.assert_parse_error("mcp", "--embedding", "--model-max-length", "8193")
        self.assert_parse_error("mcp", "--embedding", "--embedding-model", "custom/model")
        self.assert_parse_error("agent", "resolve", "measure", "--embedding")

    def test_reranker_requires_embedding_and_explicit_valid_controls(self) -> None:
        self.assert_parse_error("agent", "search", "measure", "--rerank")
        self.assert_parse_error("mcp", "--embedding", "--rerank", "--rerank-candidates", "0")
        self.assert_parse_error("mcp", "--embedding", "--rerank", "--rerank-candidates", "501")
        self.assert_parse_error("mcp", "--embedding", "--reranker-revision", "a" * 40)
        self.assert_parse_error("mcp", "--embedding", "--rerank-candidates", "20")
        self.assert_parse_error("mcp", "--embedding", "--rerank-candidates", "50")
        self.assert_parse_error("mcp", "--embedding", "--rerank", "--reranker-model", "test/model")
        args = self.parse("mcp", "--embedding", "--rerank")
        self.assertEqual(50, args.rerank_candidates)
        self.assertTrue(args.rerank)
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            args = self.parse("mcp")
            args.rerank = True
            with self.assertRaisesRegex(RetrievalError, "--rerank requires --embedding"):
                make_ranking_service(args, graph_dir=root / "graph", repo_root=root)

    def test_explicit_model_settings_require_embedding_even_at_default_values(self) -> None:
        for command in (("agent", "search", "measure"), ("agent", "context", "measure"), ("mcp",)):
            with self.subTest(command=command):
                self.assertFalse(self.parse(*command).embedding)
            for controls in (
                ("--model-device", "cpu"),
                ("--model-batch-size", "4"),
                ("--model-max-length", "8192"),
                ("--model-cache-dir", "derived/vectors"),
                ("--models-offline",),
                ("--embedding-model", "test/model", "--embedding-revision", "a" * 40),
                ("--embedding-revision", "a" * 40),
                ("--model-device=cpu",),
                ("--model-batch-size=4",),
                ("--model-max-length=8192",),
                ("--model-cache-dir=derived/vectors",),
                ("--embedding-model=test/model", "--embedding-revision=" + "a" * 40),
                ("--model-dev", "cpu"),
                ("--models-off",),
            ):
                with self.subTest(command=command, controls=controls):
                    self.assert_parse_error(*command, *controls)
        literal = self.parse("agent", "search", "--", "--models-offline")
        self.assertEqual("--models-offline", literal.query)
        self.assertFalse(literal.embedding)

    def test_reranker_factory_uses_pinned_or_explicit_model_and_candidate_limit(self) -> None:
        from kgdistiller.adapters.sentence_transformers import (
            DEFAULT_RERANKER_MODEL,
            DEFAULT_RERANKER_REVISION,
        )
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            for controls, expected_model, expected_revision, expected_limit in (
                ((), DEFAULT_RERANKER_MODEL, DEFAULT_RERANKER_REVISION, 50),
                (("--reranker-model", "test/cross-encoder", "--reranker-revision", "b" * 40,
                  "--rerank-candidates", "2"), "test/cross-encoder", "b" * 40, 2),
            ):
                with self.subTest(controls=controls), patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                                                          return_value=FakeReranker()) as constructor, patch("kgdistiller.semantic_retrieval.SemanticRankingService") as service:
                    result = make_ranking_service(
                        self.parse("mcp", "--embedding", "--rerank", *controls), graph_dir=graph, repo_root=root,
                    )
                    self.assertIs(service.return_value, result)
                    self.assertEqual(expected_model, constructor.call_args.kwargs["reranker_model"])
                    self.assertEqual(expected_revision, constructor.call_args.kwargs["reranker_revision"])
                    service.assert_called_once_with(constructor.return_value,
                                                    cache_dir=(graph.parent / "build" / "retrieval").resolve(),
                                                    rerank=True, candidate_limit=expected_limit)

    def test_reranker_search_and_context_keep_question_and_identity_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            authority_before = repository_bytes(graph)
            query = "如何给可测集合赋予大小？"
            adapter = FakeReranker()
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter", return_value=adapter):
                status, output, error = self.run_cli(root, "agent", "search", query,
                                                    "--embedding", "--rerank", "--rerank-candidates", "2")
                self.assertEqual(0, status, error)
                execution = json.loads(output)
                self.assertEqual("kgdistiller-search-execution-v2", execution["schema"])
                rows = execution["result"]["results"]
                # Reversed ranks in this two-item pool have equal RRF scores;
                # the documented stable ID tie breaker retains measure first.
                self.assertEqual(["measure", "sigma-algebra"], [row["node_id"] for row in rows])
                expected = {"measure": (1, 2, 0.0), "sigma-algebra": (2, 1, 3.0)}
                for row in rows:
                    base_rank, model_rank, raw_score = expected[row["node_id"]]
                    self.assertEqual({"rank": model_rank, "score": raw_score}, row["lanes"]["reranker"])
                    self.assertEqual("rrf", row["fusion"]["method"])
                    self.assertAlmostEqual(1 / (60 + base_rank) + 1 / (60 + model_rank), row["fusion"]["score"])
                provenance = execution["result"]["ranking"]["reranker"]
                self.assertEqual("rrf-base-reranker", provenance["fusion"])
                self.assertEqual(2, provenance["candidate_limit"])
                self.assertEqual(execution["snapshot_sha256"], provenance["snapshot_sha256"])
                self.assertEqual(execution["graph_sha256"], provenance["graph_sha256"])
                self.assertEqual(["measure", "sigma-algebra"], [candidate["node_id"] for candidate in provenance["candidates"]])
                self.assertTrue(all(row["status"] == "missing" for row in execution["identity_resolutions"]))
                self.assertEqual(query, adapter.pair_calls[0][0])
                self.assertEqual(2, len(adapter.pair_calls[0][1]))
                self.assertEqual(hashlib.sha256(query.encode("utf-8")).hexdigest(), provenance["query_sha256"])
                for candidate, document in zip(provenance["candidates"], adapter.pair_calls[0][1]):
                    self.assertEqual(hashlib.sha256(document.encode("utf-8")).hexdigest(), candidate["document_sha256"])
                    self.assertEqual(expected[candidate["node_id"]][2], candidate["score"])
                status, output, error = self.run_cli(root, "agent", "context", query,
                                                    "--embedding", "--rerank", "--rerank-candidates", "2", "--budget", "6000")
                self.assertEqual(0, status, error)
                context = json.loads(output)
                self.assertEqual(query, context["question"])
                self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
                self.assertIn("sigma-algebra", [node["id"] for node in context["nodes"]])
                self.assertEqual("measure", context["nodes"][0]["id"])
                self.assertEqual(2, len(adapter.pair_calls[-1][1]))
            self.assertEqual(authority_before, repository_bytes(graph))

    def test_embedding_factory_defaults_and_custom_cache_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            from kgdistiller.adapters.sentence_transformers import (
                DEFAULT_EMBEDDING_MODEL,
                DEFAULT_EMBEDDING_REVISION,
            )
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()) as constructor:
                service = make_ranking_service(
                    self.parse("mcp", "--embedding"), graph_dir=graph, repo_root=root,
                )
                self.assertEqual((graph.parent / "build" / "retrieval").resolve(), service.cache_dir)
                constructor.assert_called_once_with(
                    model=DEFAULT_EMBEDDING_MODEL, revision=DEFAULT_EMBEDDING_REVISION,
                    device="cpu", batch_size=4, max_length=8192, local_files_only=False,
                )
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()) as constructor:
                service = make_ranking_service(
                    self.parse("mcp", "--embedding", "--embedding-model", "test/model",
                               "--embedding-revision", "a" * 40, "--model-device", "mps",
                               "--model-batch-size", "2", "--model-max-length", "4096",
                               "--model-cache-dir", "derived/vectors", "--models-offline"),
                    graph_dir=graph, repo_root=root,
                )
                self.assertEqual((root / "derived" / "vectors").resolve(), service.cache_dir)
                constructor.assert_called_once_with(
                    model="test/model", revision="a" * 40, device="mps", batch_size=2,
                    max_length=4096, local_files_only=True,
                )
            self.assertFalse((graph.parent / "build").exists())

    def test_help_status_and_plain_search_do_not_construct_models(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            before = repository_bytes(root)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       side_effect=AssertionError("model constructor must stay lazy")) as constructor:
                self.assertIsNone(make_ranking_service(
                    self.parse("mcp"), graph_dir=graph, repo_root=root,
                ))
                for arguments in (("agent", "status"), ("agent", "search", "measure")):
                    status, _, error = self.run_cli(root, *arguments)
                    self.assertEqual(0, status, error)
                with (
                    redirect_stdout(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    self.parse("agent", "search", "--help")
                self.assertEqual(0, raised.exception.code)
                constructor.assert_not_called()
            self.assertEqual(before, repository_bytes(root))

    def test_embedding_search_and_context_use_v2_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            authority_before = repository_bytes(graph)
            query = "如何给可测集合赋予大小？"
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()):
                status, output, error = self.run_cli(root, "agent", "search", query, "--embedding")
                self.assertEqual(0, status, error)
                execution = json.loads(output)
                self.assertEqual("kgdistiller-search-execution-v2", execution["schema"])
                self.assertEqual("measure", execution["result"]["results"][0]["node_id"])
                self.assertTrue(all(row["status"] == "missing" for row in execution["identity_resolutions"]))
                status, output, error = self.run_cli(root, "agent", "context", query,
                                                     "--embedding", "--budget", "6000")
                self.assertEqual(0, status, error)
                context = json.loads(output)
                self.assertEqual(query, context["question"])
                self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
                self.assertIn("measure", [node["id"] for node in context["nodes"]])
            self.assertEqual(authority_before, repository_bytes(graph))
            self.assertTrue(list((graph.parent / "build" / "retrieval").glob("*.json")))

    def test_opted_in_dependency_errors_are_structured_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)
            before = repository_bytes(root)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       side_effect=SemanticRetrievalError("model-dependency-missing", "missing fixture dependency")):
                for arguments in (("agent", "search", "measure"), ("agent", "context", "measure"), ("mcp",)):
                    with self.subTest(arguments=arguments):
                        status, output, error = self.run_cli(root, *arguments, "--embedding")
                        self.assertEqual(1, status)
                        self.assertEqual("", output)
                        self.assertEqual("model-dependency-missing", json.loads(error)["code"])
            self.assertEqual(before, repository_bytes(root))

    def test_mcp_launch_reuses_one_explicit_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_graph(root)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()) as constructor, patch("kgdistiller.mcp.serve_stdio") as serve:
                status, _, error = self.run_cli(root, "mcp", "--embedding", "--models-offline")
                self.assertEqual(0, status, error)
                constructor.assert_called_once()
                self.assertEqual(graph.resolve(), serve.call_args.args[0])
                self.assertIs(serve.call_args.kwargs["ranking_service"].adapter, constructor.return_value)

    def test_missing_graph_fails_without_creating_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            before = repository_bytes(root)

            status, _, error = self.run_cli(root, "agent", "search", "alpha")

            self.assertEqual(1, status)
            self.assertIn("authority graph", error)
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_legacy_and_planned_search_read_the_same_json_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(retrieval_plan()), encoding="utf-8")
            before = repository_bytes(root)

            outputs = []
            for arguments in (
                ("agent", "search", "countably additive"),
                ("agent", "search", "--plan", str(plan_path)),
            ):
                status, output, error = self.run_cli(root, *arguments)
                self.assertEqual(0, status, error)
                outputs.append(json.loads(output))

            self.assertEqual("legacy", outputs[0]["plan_mode"])
            self.assertEqual("planned", outputs[1]["plan_mode"])
            self.assertEqual(SEARCH_EXECUTION_SCHEMA, outputs[1]["schema"])
            self.assertEqual(SEARCH_RESULT_SCHEMA, outputs[1]["result"]["schema"])
            self.assertRegex(outputs[1]["result"]["plan_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_context_preserves_original_question_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)
            plan = retrieval_plan()
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            before = repository_bytes(root)
            legacy_question = "why does measure need sigma algebra?"

            legacy_status, legacy_output, legacy_error = self.run_cli(
                root, "agent", "context", legacy_question, "--budget", "2048"
            )
            plan_status, plan_output, plan_error = self.run_cli(
                root,
                "agent",
                "context",
                "--plan",
                str(plan_path),
                "--budget",
                "2048",
            )

            self.assertEqual(0, legacy_status, legacy_error)
            self.assertEqual(0, plan_status, plan_error)
            self.assertEqual(legacy_question, json.loads(legacy_output)["question"])
            self.assertEqual(plan["question"], json.loads(plan_output)["question"])
            self.assertEqual(before, repository_bytes(root))

    def test_status_reports_json_memory_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)

            status, output, error = self.run_cli(root, "agent", "status")

            self.assertEqual(0, status, error)
            payload = json.loads(output)
            self.assertEqual("json-memory", payload["backend"])
            self.assertIn("read-only-query-v3", payload["capabilities"])


class SourceEvidenceCliTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-evidence-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_evidence(self, manifest: Path, *options: str, query: str = "alpha") -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", "--repo-root", str(self.root),
                                        "agent", "evidence", query, "--manifest", str(manifest), *options]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_unicode_table_spans_and_document_scope_need_no_graph(self) -> None:
        raw = ("# --[[Invented identity]]--\r\n\r\n| 模型 | MSE |\r\n|---|---:|\r\n"
               "| μ网络 | 0.79 |\r\n| 基线 | 8.20 |\r\n").encode()
        manifest = write_source_evidence_manifest(self.root, {"paper": raw, "other": b"alpha MSE 99\n"}, hash_mode="normalized-utf8")
        before = repository_bytes(self.root)
        status, output, error = self.run_evidence(Path(manifest.name), "--doc-id", "paper", query="MSE μ网络")
        self.assertEqual(0, status, error)
        result = json.loads(output)
        self.assertEqual("kgdistiller-source-evidence-result-v1", result["schema"])
        self.assertIs(False, result["identity_authority"])
        self.assertIs(False, result["complete_definition"])
        self.assertEqual(["paper"], result["doc_ids"])
        tables = [fragment for fragment in result["fragments"] if fragment["fragment_type"] == "table"]
        self.assertEqual(1, len(tables))
        self.assertIn("| μ网络 | 0.79 |\r\n| 基线 | 8.20 |\r\n", tables[0]["text"])
        for fragment in result["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(raw[span["start"]:span["end"]], fragment["text"].encode("utf-8"))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), fragment["raw_source_sha256"])
            self.assertEqual(fragment["normalized_source_sha256"], fragment["source_sha256"])
            self.assertIs(False, fragment["identity_authority"])
            self.assertNotIn("node_id", fragment)
            self.assertTrue(all(heading["identity_authority"] is False for heading in fragment["heading_context"]))
        self.assertEqual(len(canonical_json(result).encode("utf-8")), result["budget"]["used_bytes"])
        self.assertEqual(before, repository_bytes(self.root))
        self.assertFalse((self.root / ".knowledge").exists())

    def test_budget_omits_whole_fragments_and_reports_too_small(self) -> None:
        raw = ("alpha long " * 300 + "\n\nalpha concise result\n").encode("utf-8")
        manifest = write_source_evidence_manifest(self.root, {"paper": raw})
        status, output, error = self.run_evidence(manifest, "--budget", "2500")
        self.assertEqual(0, status, error)
        result = json.loads(output)
        self.assertGreater(result["omitted_fragments"], 0)
        self.assertTrue(result["omissions"] or result["diagnostics_truncated"])
        self.assertLessEqual(len(canonical_json(result).encode("utf-8")), 2500)
        for fragment in result["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(raw[span["start"]:span["end"]], fragment["text"].encode("utf-8"))
        status, output, error = self.run_evidence(manifest, "--budget", "1")
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("source-budget-too-small", json.loads(error)["code"])

    def test_compact_evidence_keeps_more_exact_fragments_in_the_same_budget(self) -> None:
        from kgdistiller.source_context import validate_source_context
        raw = ("# Protocol\n\n" + "\n\n".join(f"alpha experiment {i}; condition σ>{i}." for i in range(40)) + "\n").encode()
        manifest = write_source_evidence_manifest(self.root, {"paper": raw})
        before = repository_bytes(self.root)
        status, full_output, error = self.run_evidence(manifest, "--limit", "30", "--budget", "200000")
        self.assertEqual(0, status, error)
        source_result = json.loads(full_output)
        status, baseline_output, error = self.run_evidence(manifest, "--limit", "30", "--budget", "15000")
        self.assertEqual(0, status, error)
        status, compact_output, error = self.run_evidence(manifest, "--limit", "30", "--budget", "15000", "--context-projection", "compact")
        self.assertEqual(0, status, error)
        compact = json.loads(compact_output)
        validate_source_context(compact, source_results=[source_result])
        self.assertEqual("kgdistiller-source-evidence-context-v1", compact["schema"])
        self.assertGreater(len(compact["fragments"]), len(json.loads(baseline_output)["fragments"]))
        self.assertLessEqual(len(canonical_json(compact).encode()), 15000)
        for fragment in compact["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(raw[span["start"]:span["end"]], fragment["text"].encode())
        self.assertEqual(before, repository_bytes(self.root))

    def test_compact_evidence_errors_are_structured_and_sources_checked_after_packing(self) -> None:
        from kgdistiller.source_context import build_source_context
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        status, output, error = self.run_evidence(manifest, "--context-projection", "compact", "--budget", "1")
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("source-budget-too-small", json.loads(error)["code"])
        def changed_source(*args, **kwargs):
            result = build_source_context(*args, **kwargs)
            (self.root / "raw-sources" / "paper.txt").write_bytes(b"alpha replaced source\n")
            return result
        with patch("kgdistiller.source_context.build_source_context", side_effect=changed_source):
            status, output, error = self.run_evidence(manifest, "--context-projection", "compact")
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("stale-source", json.loads(error)["code"])
        status, output, error = self.run_evidence(manifest, "--context-projection", "compact", "--budget", "200001")
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("invalid-source-query", json.loads(error)["code"])

    def test_source_reference_cli_preserves_declared_version_and_ambiguity_without_graph(self) -> None:
        from kgdistiller.source_references import validate_source_reference_result
        manifest = write_source_evidence_manifest(self.root, {"adam": b"Adam source\n", "other": b"Other source\n"})
        payload = json.loads(manifest.read_text())
        for doc in payload["documents"]:
            doc["source_version"] = "1412.6980v8"
        manifest.write_text(json.dumps(payload))
        before = repository_bytes(self.root)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", "--repo-root", str(self.root), "agent", "evidence-resolve", "Adam v8", "adam@1412.6980v8", "1412.6980v8", "Adam v9", "AdamW", "--manifest", manifest.name]), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main()
        self.assertEqual(0, status, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        validate_source_reference_result(result)
        self.assertEqual(["matched", "matched", "ambiguous", "unmatched", "unmatched"], [r["status"] for r in result["resolutions"]])
        self.assertEqual(["adam"], result["matched_doc_ids"])
        self.assertIs(False, result["identity_authority"])
        self.assertEqual(before, repository_bytes(self.root))
        self.assertFalse((self.root / ".knowledge").exists())

    def test_invalid_filters_and_result_bounds_fail_as_structured_errors(self) -> None:
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        for options, code in ((("--doc-id", "missing"), "invalid-source-filter"),
                              (("--doc-id", "paper", "--doc-id", "paper"), "invalid-source-filter"),
                              (("--limit", "0"), "invalid-source-query"),
                              (("--budget", "200001"), "invalid-source-query")):
            with self.subTest(options=options):
                status, output, error = self.run_evidence(manifest, *options)
                self.assertEqual(1, status)
                self.assertEqual("", output)
                self.assertEqual(code, json.loads(error)["code"])

    def test_missing_invalid_and_stale_sources_fail_without_writes(self) -> None:
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        valid = manifest.read_bytes()
        cases = ((self.root / "missing.json", None, "source-unavailable"),
                 (manifest, b"{invalid json", "invalid-source-manifest"))
        for path, content, code in cases:
            with self.subTest(code=code):
                if content is not None:
                    manifest.write_bytes(content)
                before = repository_bytes(self.root)
                status, output, error = self.run_evidence(path)
                self.assertEqual(1, status)
                self.assertEqual("", output)
                self.assertEqual(code, json.loads(error)["code"])
                self.assertEqual(before, repository_bytes(self.root))
        manifest.write_bytes(valid)
        (self.root / "raw-sources" / "paper.txt").write_bytes(b"alpha changed source\n")
        status, output, error = self.run_evidence(manifest)
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("stale-source", json.loads(error)["code"])

    def test_duplicate_keys_and_nonfinite_json_are_rejected_before_schema_use(self) -> None:
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        original = manifest.read_text(encoding="utf-8")
        invalid_json = ['{"root":"unapproved",' + original[1:]]
        invalid_json.extend(original.replace('"root": "raw-sources"', f'"root": {constant}')
                            for constant in ("NaN", "Infinity", "-Infinity"))
        for text in invalid_json:
            with self.subTest(manifest=text):
                manifest.write_text(text, encoding="utf-8")
                status, output, error = self.run_evidence(manifest)
                self.assertEqual(1, status)
                self.assertEqual("", output)
                self.assertEqual("invalid-source-manifest", json.loads(error)["code"])

    def test_explicit_manifest_and_root_symlinks_are_rejected(self) -> None:
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        alias = self.root / "linked-manifest.json"
        alias.symlink_to(manifest)
        status, output, error = self.run_evidence(alias)
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertIn(json.loads(error)["code"], {"source-unavailable", "unsafe-source-path"})
        linked_root = self.root / "linked-sources"
        linked_root.symlink_to(self.root / "raw-sources", target_is_directory=True)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["root"] = linked_root.name
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        status, output, error = self.run_evidence(manifest)
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("unsafe-source-path", json.loads(error)["code"])

    def test_manifest_and_document_byte_limits_fail_before_reading_payloads(self) -> None:
        manifest = write_source_evidence_manifest(self.root, {"paper": b"alpha source\n"})
        valid = manifest.read_bytes()
        with manifest.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)
        status, output, error = self.run_evidence(manifest)
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("source-too-large", json.loads(error)["code"])
        manifest.write_bytes(valid)
        with (self.root / "raw-sources" / "paper.txt").open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        status, output, error = self.run_evidence(manifest)
        self.assertEqual(1, status)
        self.assertEqual("", output)
        self.assertEqual("source-too-large", json.loads(error)["code"])


if __name__ == "__main__":
    unittest.main()
