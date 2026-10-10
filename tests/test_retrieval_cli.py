from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kgdistiller.cli import (
    main,
    make_graph_retrieval_policy,
    make_ranking_service,
    parse_args,
)
from kgdistiller.query import load_graph_view
from kgdistiller.retrieval import (
    SEARCH_EXECUTION_SCHEMA,
    SEARCH_RESULT_SCHEMA,
    RetrievalError,
)
from kgdistiller.semantic_retrieval import (
    SemanticRetrievalError,
    search_document,
)
from tests.knowledge_fixture import KnowledgeFixture
from tests.test_query import fixture_edges, fixture_nodes
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
        "identity_queries": ["西格玛代数"],
        "lexical_queries": ["countably additive"],
        "graph": {
            "seed_ids": [],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 2,
            "strategy": "hybrid",
        },
        "limit": 20,
    }


def repository_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def authority_bytes(knowledge: Path) -> dict[str, bytes]:
    """Entries, edges and the registry; the rebuildable build/ tree is excluded."""
    return {path: data for path, data in repository_bytes(knowledge).items() if not path.startswith("build/")}


def write_fixture_store(root: Path, nodes: list[dict] | None = None, edges: list[dict] | None = None) -> Path:
    """Write the query fixture as real entry files whose Evidence quotes one source line each."""
    fixture = KnowledgeFixture(root)
    nodes = fixture_nodes() if nodes is None else nodes
    fixture.write_source("notes/fixture.txt", "".join(f"{node['summary']}\n" for node in nodes))
    for line, node in enumerate(nodes, 1):
        sections = {key: node[key] for key in ("prerequisites",) if key in node}
        fixture.add_entry(node["id"], node["label"], "notes/fixture.txt", line,
                          aliases=node["aliases"], summary=node["summary"], **sections)
    for edge in fixture_edges() if edges is None else edges:
        fixture.add_edge(edge["source"], edge["relation"], edge["target"],
                         confidence=edge["confidence"], evidence=edge["evidence"])
    return root / ".knowledge"


def write_graph_retrieval_fixture(root: Path) -> Path:
    """Source-backed chain with one deliberately unverified side edge."""
    nodes = fixture_nodes()
    nodes[1]["prerequisites"] = ["Domain is a sigma algebra."]
    nodes.append({**nodes[2], "id": "unverified-result", "label": "Unverified destination",
                  "aliases": [], "summary": "An unaudited relation destination."})
    edges = fixture_edges()
    edges.append({"source": "sigma-algebra", "relation": "implies", "target": "unverified-result",
                  "evidence": "Fixture unverified statement.", "confidence": "unverified"})
    return write_fixture_store(root, nodes, edges)


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
        plain = self.parse("agent", "search", "countable closure")
        planned = self.parse("agent", "search", "--plan", "retrieval.json")
        context = self.parse("agent", "context", "why alpha", "--limit", "7")

        self.assertEqual("countable closure", plain.query)
        self.assertIsNone(plain.plan)
        self.assertEqual(Path("retrieval.json"), planned.plan)
        self.assertEqual(7, context.limit)
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
            before = authority_bytes(graph)
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
                self.assertEqual(["Domain is a sigma algebra."], next(node for node in bundle["nodes"] if node["id"] == "measure")["prerequisites"])
                self.assertEqual(2, len(bundle["edges"]))
                self.assertLessEqual(bundle["budget"]["estimated_tokens"], 18000)
            self.assertEqual(before, authority_bytes(graph))

    def test_graph_cli_gate_can_be_explicitly_relaxed_without_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-graph-cli-") as raw:
            root = Path(raw)
            write_graph_retrieval_fixture(root)
            status, output, error = self.run_cli(root, "agent", "search", "collection", "--graph-retrieval", "--graph-seed-candidates", "1", "--graph-edge-policy", "all", "--graph-strategy", "bfs")
            self.assertEqual(0, status, error)
            execution = json.loads(output)
            self.assertEqual(["sigma-algebra"], [row["node_id"] for row in execution["result"]["results"]])
            self.assertIn("unverified-result", [row["node_id"] for row in execution["result"]["graph_retrieval"]["neighbors"]])
            self.assertEqual("all", execution["result"]["graph_retrieval"]["policy"]["edge_policy"])
            self.assertEqual([], execution["result"]["graph_retrieval"]["seeds"]["identity"])

    def test_graph_cli_composes_with_embedding_without_promoting_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-graph-cli-") as raw:
            root = Path(raw)
            graph = write_graph_retrieval_fixture(root)
            before = authority_bytes(graph)
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
            # Reranker scores and query vectors are not persisted; each run infers them.
            self.assertEqual(2, len(adapter.pair_calls))
            for _, documents in adapter.pair_calls:
                self.assertEqual([search_document(load_graph_view(root, graph / "sources.json").nodes["measure"])], documents)
            self.assertEqual([["如何给可测集合赋予大小？"]] * 2, adapter.query_calls)
            self.assertEqual([], execution["result"]["graph_retrieval"]["seeds"]["identity"])
            self.assertEqual(["measure"], [item["node_id"] for item in execution["result"]["graph_retrieval"]["seeds"]["candidate"]])
            self.assertTrue(all(not item["identity_authority"] for item in execution["result"]["graph_retrieval"]["seeds"]["candidate"]))
            self.assertEqual(before, authority_bytes(graph))

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
                make_ranking_service(args, repo_root=root)

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
            graph = write_fixture_store(root)
            for controls, expected_model, expected_revision, expected_limit in (
                ((), DEFAULT_RERANKER_MODEL, DEFAULT_RERANKER_REVISION, 50),
                (("--reranker-model", "test/cross-encoder", "--reranker-revision", "b" * 40,
                  "--rerank-candidates", "2"), "test/cross-encoder", "b" * 40, 2),
            ):
                with self.subTest(controls=controls), patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                                                          return_value=FakeReranker()) as constructor, patch("kgdistiller.semantic_retrieval.SemanticRankingService") as service:
                    result = make_ranking_service(
                        self.parse("mcp", "--embedding", "--rerank", *controls), repo_root=root,
                    )
                    self.assertIs(service.return_value, result)
                    self.assertEqual(expected_model, constructor.call_args.kwargs["reranker_model"])
                    self.assertEqual(expected_revision, constructor.call_args.kwargs["reranker_revision"])
                    service.assert_called_once_with(constructor.return_value,
                                                    cache_dir=(graph / "build" / "retrieval").resolve(),
                                                    rerank=True, candidate_limit=expected_limit)

    def test_reranker_search_and_context_keep_question_and_identity_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_store(root)
            authority_before = authority_bytes(graph)
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
                self.assertEqual(["measure", "sigma-algebra"], [candidate["node_id"] for candidate in provenance["candidates"]])
                self.assertTrue(all(row["status"] == "missing" for row in execution["identity_resolutions"]))
                self.assertEqual(query, adapter.pair_calls[0][0])
                self.assertEqual(2, len(adapter.pair_calls[0][1]))
                for candidate in provenance["candidates"]:
                    self.assertEqual({"node_id", "score"}, set(candidate))
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
            self.assertEqual(authority_before, authority_bytes(graph))

    def test_embedding_factory_defaults_and_custom_cache_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            graph = write_fixture_store(root)
            from kgdistiller.adapters.sentence_transformers import (
                DEFAULT_EMBEDDING_MODEL,
                DEFAULT_EMBEDDING_REVISION,
            )
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()) as constructor:
                service = make_ranking_service(
                    self.parse("mcp", "--embedding"), repo_root=root,
                )
                self.assertEqual((graph / "build" / "retrieval").resolve(), service.cache_dir)
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
                    repo_root=root,
                )
                self.assertEqual((root / "derived" / "vectors").resolve(), service.cache_dir)
                constructor.assert_called_once_with(
                    model="test/model", revision="a" * 40, device="mps", batch_size=2,
                    max_length=4096, local_files_only=True,
                )
            self.assertFalse((graph / "build").exists())

    def test_help_status_and_plain_search_do_not_construct_models(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_store(root)
            before = repository_bytes(root)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       side_effect=AssertionError("model constructor must stay lazy")) as constructor:
                self.assertIsNone(make_ranking_service(self.parse("mcp"), repo_root=root))
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
            graph = write_fixture_store(root)
            authority_before = authority_bytes(graph)
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
            self.assertEqual(authority_before, authority_bytes(graph))
            self.assertTrue(list((graph / "build" / "retrieval").glob("*.json")))

    def test_opted_in_dependency_errors_are_structured_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_store(root)
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
            graph = write_fixture_store(root)
            with patch("kgdistiller.adapters.sentence_transformers.SentenceTransformersAdapter",
                       return_value=FakeEmbedding()) as constructor, patch("kgdistiller.mcp.serve_stdio") as serve:
                status, _, error = self.run_cli(root, "mcp", "--embedding", "--models-offline")
                self.assertEqual(0, status, error)
                constructor.assert_called_once()
                self.assertEqual(root.resolve(), serve.call_args.args[0])
                self.assertEqual((graph / "sources.json").resolve(), serve.call_args.args[1])
                self.assertIs(serve.call_args.kwargs["ranking_service"].adapter, constructor.return_value)

    def test_missing_graph_fails_without_creating_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            before = repository_bytes(root)

            status, _, error = self.run_cli(root, "agent", "search", "alpha")

            self.assertEqual(1, status)
            self.assertIn("run kgdistiller init", error)
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_query_and_planned_search_read_the_same_json_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_store(root)
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

            self.assertEqual("query", outputs[0]["plan_mode"])
            self.assertEqual("planned", outputs[1]["plan_mode"])
            self.assertEqual(SEARCH_EXECUTION_SCHEMA, outputs[1]["schema"])
            self.assertEqual(SEARCH_RESULT_SCHEMA, outputs[1]["result"]["schema"])
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_context_preserves_original_question_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_store(root)
            plan = retrieval_plan()
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            before = repository_bytes(root)
            plain_question = "why does measure need sigma algebra?"

            plain_status, plain_output, plain_error = self.run_cli(
                root, "agent", "context", plain_question, "--budget", "2048"
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

            self.assertEqual(0, plain_status, plain_error)
            self.assertEqual(0, plan_status, plan_error)
            self.assertEqual(plain_question, json.loads(plain_output)["question"])
            self.assertEqual(plan["question"], json.loads(plan_output)["question"])
            self.assertEqual(before, repository_bytes(root))

    def test_status_reports_entry_and_edge_counts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_store(root)

            status, output, error = self.run_cli(root, "agent", "status")

            self.assertEqual(0, status, error)
            payload = json.loads(output)
            self.assertEqual({"entries": 3, "edges": 2}, payload["counts"])
            self.assertEqual({"prerequisite-for": 2}, payload["relations"])


if __name__ == "__main__":
    unittest.main()
