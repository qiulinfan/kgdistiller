from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.mcp import (  # noqa: E402
    MAX_TOOL_RESPONSE_BYTES,
    MCPServer,
    TOOL_DEFINITIONS,
    call_tool,
)
from kgdistiller.query import QueryError, load_graph_view, query_status  # noqa: E402
from tests.test_query import candidate_snapshot_with, fixture_nodes, write_fixture_graph  # noqa: E402
from tests.test_semantic_retrieval import FakeEmbedding  # noqa: E402
from kgdistiller.semantic_retrieval import SemanticRankingService, search_document  # noqa: E402
from kgdistiller.support_selection import make_support_selection  # noqa: E402
from kgdistiller.contracts import canonical_json, sha256_json  # noqa: E402
from kgdistiller.retrieval import RetrievalError  # noqa: E402
from tests.test_retrieval_cli import FakeReranker, assert_compact_source_bundle, repository_bytes, retrieval_plan, write_graph_retrieval_fixture, write_source_evidence_manifest  # noqa: E402


class MCPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-mcp-")
        self.root = Path(self.temporary.name)
        self.graph = write_fixture_graph(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _files(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_tool_surface_is_exactly_the_json_memory_surface(self) -> None:
        names = {tool["name"] for tool in TOOL_DEFINITIONS}
        self.assertEqual(
            {
                "kg_status",
                "kg_resolve_concepts",
                "kg_search",
                "kg_search_source_evidence",
                "kg_resolve_source_references",
                "kg_get_node",
                "kg_expand",
                "kg_ppr",
                "kg_build_context",
                "kg_align_graph",
                "kg_compare_graph",
                "kg_create_proposal",
            },
            names,
        )
        ppr = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_ppr")
        self.assertEqual(
            {
                "ids",
                "namespace",
                "node_types",
                "edge_types",
                "direction",
                "limit",
                "include_taxonomy",
                "include_stale",
                "include_orphaned",
            },
            set(ppr["inputSchema"]["properties"]),
        )
        resolve = next(
            tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_resolve_concepts"
        )
        self.assertEqual(
            4096,
            resolve["inputSchema"]["properties"]["concepts"]["items"]["maxLength"],
        )

    def test_identity_and_node_id_inputs_are_bounded(self) -> None:
        with self.assertRaisesRegex(QueryError, "invalid string length"):
            call_tool(
                self.graph,
                "kg_resolve_concepts",
                {"concepts": ["x" * 4097]},
            )
        with self.assertRaisesRegex(QueryError, "invalid length"):
            call_tool(self.graph, "kg_get_node", {"id": "x" * 257})

    def test_mcp_fails_closed_before_emitting_an_oversized_tool_result(self) -> None:
        server = MCPServer(self.graph)
        server.initialized = True
        with patch(
            "kgdistiller.mcp.call_tool",
            return_value={"blob": "x" * (MAX_TOOL_RESPONSE_BYTES + 1)},
        ):
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "kg_status", "arguments": {}},
                }
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn(
            "tool response exceeds",
            response["result"]["structuredContent"]["error"]["message"],
        )

    def test_mcp_and_python_core_are_equivalent_and_do_not_write(self) -> None:
        before = self._files()
        direct = call_tool(self.graph, "kg_status", {})
        self.assertEqual(query_status(self.graph), direct)

        server = MCPServer(self.graph)
        initialized = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        self.assertEqual("json-memory", initialized["result"]["capabilities"]["experimental"]["queryBackend"])
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "kg_status", "arguments": {}},
            }
        )
        self.assertEqual(direct, response["result"]["structuredContent"])
        self.assertEqual(before, self._files())

    def test_each_tool_call_loads_a_complete_fresh_view(self) -> None:
        first = call_tool(self.graph, "kg_status", {})
        nodes_path = self.graph / "nodes.jsonl"
        original = nodes_path.read_text(encoding="utf-8")
        # A mixed manual edit is rejected; no partial new view is returned.
        tampered = original.replace("Sigma algebra", "Tampered sigma algebra", 1)
        nodes_path.write_text(tampered, encoding="utf-8")
        with self.assertRaisesRegex(Exception, "digest|duplicate|counts"):
            call_tool(self.graph, "kg_status", {})
        nodes_path.write_text(original, encoding="utf-8")
        self.assertEqual(first["snapshot_sha256"], call_tool(self.graph, "kg_status", {})["snapshot_sha256"])

    def test_graph_mcp_controls_are_per_request_and_fail_closed_when_unused(self) -> None:
        for name in ("kg_search", "kg_build_context"):
            schema = next(tool["inputSchema"] for tool in TOOL_DEFINITIONS if tool["name"] == name)
            self.assertEqual(False, schema["properties"]["graph_retrieval"]["default"])
            self.assertEqual(5, schema["properties"]["graph_seed_candidates"]["default"])
            self.assertEqual("high-confidence", schema["properties"]["graph_edge_policy"]["default"])
            for controls in ({"graph_seed_candidates": 5}, {"graph_edge_policy": "high-confidence"},
                             {"graph_retrieval": False, "graph_seed_candidates": 5}):
                with self.subTest(name=name, controls=controls), self.assertRaisesRegex(QueryError, "require graph_retrieval"):
                    call_tool(self.graph, name, {"query": "collection", **controls})
            for controls in ({"graph_retrieval": 1}, {"graph_retrieval": True, "graph_seed_candidates": True},
                             {"graph_retrieval": True, "graph_seed_candidates": 0},
                             {"graph_retrieval": True, "graph_seed_candidates": 33},
                             {"graph_retrieval": True, "graph_edge_policy": "reviewed"}):
                with self.subTest(name=name, controls=controls), self.assertRaises(QueryError):
                    call_tool(self.graph, name, {"query": "collection", **controls})
        with self.assertRaisesRegex(QueryError, "unexpected"):
            call_tool(self.graph, "kg_status", {"graph_retrieval": True})
        server = MCPServer(self.graph)
        server.initialized = True
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kg_search", "arguments": {"query": "collection", "graph_edge_policy": "high-confidence"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("require graph_retrieval", response["result"]["structuredContent"]["error"]["message"])

    def test_graph_mcp_search_and_context_preserve_complete_bounded_source_paths(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        before = repository_bytes(self.graph)
        server = MCPServer(self.graph)
        server.initialized = True
        arguments = {"query": "collection", "graph_retrieval": True, "graph_seed_candidates": 1, "max_depth": 1}
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kg_search", "arguments": arguments}})
        self.assertFalse(response["result"]["isError"])
        execution = response["result"]["structuredContent"]
        self.assertEqual("kgdistiller-search-execution-v3", execution["schema"])
        rows = {row["node_id"]: row for row in execution["result"]["results"]}
        self.assertEqual({"sigma-algebra"}, set(rows))
        metadata = execution["result"]["graph_retrieval"]
        self.assertEqual([], metadata["seeds"]["identity"])
        self.assertFalse(metadata["seeds"]["candidate"][0]["identity_authority"])
        self.assertFalse(metadata["policy"]["confidence_is_independent_review"])
        self.assertTrue(metadata["ppr"]["converged"])
        neighbors = {row["node_id"]: row for row in metadata["neighbors"]}
        self.assertEqual({"measure"}, set(neighbors))
        self.assertEqual("navigation", neighbors["measure"]["fusion"]["method"])
        self.assertEqual(0.0, neighbors["measure"]["fusion"]["score"])
        self.assertEqual(["sigma-algebra", "measure"], neighbors["measure"]["path_evidence"][0]["nodes"])
        plan = retrieval_plan()
        plan.update(question="collection", identity_queries=[], lexical_queries=["collection"])
        bundle = call_tool(self.graph, "kg_build_context", {"plan": plan, "graph_retrieval": True, "graph_seed_candidates": 1, "token_budget": 18000})
        self.assertEqual("kgdistiller-context-bundle-v2", bundle["schema"])
        self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
        packet = next(item for item in bundle["support_packets"] if item["node_id"] == "absolute-continuity")
        self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"], packet["nodes"])
        self.assertEqual(2, len(packet["path"]["steps"]))
        self.assertTrue(all(step["evidence"] and step["confidence"] == "high" for step in packet["path"]["steps"]))
        self.assertFalse(packet["logical_entailment"])
        self.assertEqual(["Domain is a sigma algebra."], next(node for node in bundle["nodes"] if node["id"] == "measure")["properties"]["conditions"])
        self.assertEqual(2, len(bundle["edges"]))
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 18000)
        plain = call_tool(self.graph, "kg_search", {"query": "collection", "graph_retrieval": False})
        self.assertEqual("kgdistiller-search-execution-v1", plain["schema"])
        self.assertEqual(plain["result"]["results"], execution["result"]["results"])
        self.assertEqual(before, repository_bytes(self.graph))

    def test_graph_mcp_policy_relaxation_and_model_composition_are_explicit(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        relaxed = call_tool(self.graph, "kg_search", {"query": "collection", "graph_retrieval": True, "graph_seed_candidates": 1, "graph_edge_policy": "current", "graph_strategy": "bfs"})
        self.assertEqual(["sigma-algebra"], [row["node_id"] for row in relaxed["result"]["results"]])
        self.assertIn("unverified-result", [row["node_id"] for row in relaxed["result"]["graph_retrieval"]["neighbors"]])
        self.assertEqual("current", relaxed["result"]["graph_retrieval"]["policy"]["edge_policy"])
        adapter = FakeReranker()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors", rerank=True, candidate_limit=1)
        embedded = call_tool(self.graph, "kg_search", {"query": "如何给可测集合赋予大小？", "graph_retrieval": True, "graph_seed_candidates": 1}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v3", embedded["schema"])
        self.assertIn("embedding", embedded["result"]["ranking"])
        self.assertEqual([], embedded["result"]["graph_retrieval"]["seeds"]["identity"])
        self.assertEqual(["measure"], [item["node_id"] for item in embedded["result"]["graph_retrieval"]["seeds"]["candidate"]])
        self.assertTrue(all(not item["identity_authority"] for item in embedded["result"]["graph_retrieval"]["seeds"]["candidate"]))
        without_graph = call_tool(self.graph, "kg_search", {"query": "如何给可测集合赋予大小？"}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", without_graph["schema"])
        self.assertEqual(without_graph["result"]["results"], embedded["result"]["results"])
        self.assertEqual(without_graph["result"]["ranking"]["reranker"]["candidates"], embedded["result"]["ranking"]["reranker"]["candidates"])
        self.assertEqual(1, len(adapter.pair_calls))
        self.assertEqual([search_document(load_graph_view(self.graph).nodes["measure"])], adapter.pair_calls[0][1])
        self.assertEqual(1, service.last_cache_stats["rerank"]["pair_cache_hits"])
        self.assertEqual(0, service.last_cache_stats["rerank"]["pair_inference_items"])

    def test_compact_projection_mcp_rejects_malformed_or_misplaced_controls(self) -> None:
        schema = next(tool["inputSchema"] for tool in TOOL_DEFINITIONS if tool["name"] == "kg_build_context")
        self.assertEqual("full", schema["properties"]["context_projection"]["default"])
        for value in ("summary", True, None):
            with self.subTest(value=value), self.assertRaises(QueryError):
                call_tool(self.graph, "kg_build_context", {"query": "measure", "context_projection": value})
        with self.assertRaisesRegex(QueryError, "unexpected"):
            call_tool(self.graph, "kg_search", {"query": "measure", "context_projection": "compact"})

    def test_compact_graph_mcp_context_keeps_shared_source_proof_and_full_default(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        before = repository_bytes(self.graph)
        plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"])
        arguments = {"plan": plan, "graph_retrieval": True, "graph_seed_candidates": 1, "context_projection": "compact", "token_budget": 12000}
        server = MCPServer(self.graph); server.initialized = True
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kg_build_context", "arguments": arguments}})
        self.assertFalse(response["result"]["isError"])
        bundle = response["result"]["structuredContent"]
        assert_compact_source_bundle(self, bundle, self.graph, 12000)
        self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
        measure = next(record for record in bundle["nodes"] if record["node_id"] == "measure")
        self.assertEqual(["Domain is a sigma algebra."], measure["content"]["properties"]["conditions"])
        self.assertEqual(2, len(bundle["edges"]))
        routes = {packet["node_id"]: packet for packet in bundle["support_packets"] if packet["kind"] == "graph-path"}
        self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"], routes["absolute-continuity"]["nodes"])
        self.assertEqual(routes["measure"]["steps"][0]["edge_index"], routes["absolute-continuity"]["steps"][0]["edge_index"])
        full = call_tool(self.graph, "kg_build_context", {key: value for key, value in arguments.items() if key != "context_projection"})
        self.assertEqual("kgdistiller-context-bundle-v2", full["schema"])
        tight = call_tool(self.graph, "kg_build_context", {**arguments, "token_budget": 1800})
        assert_compact_source_bundle(self, tight, self.graph, 1800)
        self.assertGreater(tight["omitted_support_packets"], 0)
        self.assertTrue(tight["gaps"] or tight["diagnostics_truncated"])
        self.assertEqual(before, repository_bytes(self.graph))

    def test_compact_plain_mcp_v1_v2_preserve_full_conditions_and_default_schema(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        before = repository_bytes(self.graph)
        for query, service, expected_execution in (("measure", None, "kgdistiller-search-execution-v1"), ("如何给可测集合赋予大小？", SemanticRankingService(FakeEmbedding(), cache_dir=self.root / "vectors"), "kgdistiller-search-execution-v2")):
            with self.subTest(schema=expected_execution):
                bundle = call_tool(self.graph, "kg_build_context", {"query": query, "context_projection": "compact", "token_budget": 12000}, ranking_service=service)
                assert_compact_source_bundle(self, bundle, self.graph, 12000)
                self.assertEqual(expected_execution, bundle["search_execution_schema"])
                self.assertEqual([], bundle["edges"])
                self.assertTrue(all(packet["kind"] == "direct-source" and not packet["steps"] for packet in bundle["support_packets"]))
                measure = next(record for record in bundle["nodes"] if record["node_id"] == "measure")
                self.assertEqual(["Domain is a sigma algebra."], measure["content"]["properties"]["conditions"])
                full = call_tool(self.graph, "kg_build_context", {"query": query, "token_budget": 12000}, ranking_service=service)
                self.assertEqual("kgdistiller-context-bundle-v1", full["schema"])
                self.assertEqual(expected_execution, full["search_execution_schema"])
        self.assertEqual(before, repository_bytes(self.graph))

    def test_support_selection_mcp_plain_model_and_graph_preserve_answer_rankings(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        source_before = repository_bytes(self.graph)
        plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"], limit=1)
        service = SemanticRankingService(FakeEmbedding(), cache_dir=self.root / "vectors")
        for graph_options, ranking_service, expected_execution in (({}, None, "kgdistiller-search-execution-v1"), ({}, service, "kgdistiller-search-execution-v2"), ({"graph_retrieval": True, "graph_seed_candidates": 1}, None, "kgdistiller-search-execution-v3")):
            with self.subTest(schema=expected_execution):
                arguments = {"plan": plan, **graph_options}
                if ranking_service is not None:call_tool(self.graph, "kg_search", arguments, ranking_service=ranking_service)
                execution = call_tool(self.graph, "kg_search", arguments, ranking_service=ranking_service)
                self.assertEqual(["sigma-algebra"], [row["node_id"] for row in execution["result"]["results"]])
                view = load_graph_view(self.graph)
                manifest = make_support_selection(view, execution, plan, [{"node_id": "measure", "requirement_ids": ["query-r2"], "reason": "Caller-selected support for a separate requested aspect."}])
                for projection, expected_projection in (("full", "kgdistiller-context-projection-full-v1"), ("compact", "kgdistiller-context-projection-v1")):
                    bundle = call_tool(self.graph, "kg_build_context", {**arguments, "support_selection": manifest, "context_projection": projection, "token_budget": 12000}, ranking_service=ranking_service)
                    assert_compact_source_bundle(self, bundle, self.graph, 12000)
                    self.assertEqual(expected_projection, bundle["projection"])
                    self.assertEqual(expected_execution, bundle["search_execution_schema"])
                    self.assertEqual(sha256_json(manifest), bundle["support_selection_sha256"])
                    self.assertEqual(sha256_json(execution), bundle["execution_sha256"])
                    packet = bundle["support_packets"][0]
                    self.assertEqual(("measure", "query-support", ["query-r2"]), (packet["node_id"], packet["selection_origin"], packet["requirement_ids"]))
                    self.assertEqual("direct-source", packet["kind"]); self.assertFalse(packet["logical_entailment"])
                    record = next(record for record in bundle["nodes"] if record["node_id"] == "measure")
                    self.assertEqual(["Domain is a sigma algebra."], record["content"]["properties"]["conditions"])
                    if projection == "full":self.assertEqual(view.nodes["measure"], record["content"])
                self.assertEqual(execution, call_tool(self.graph, "kg_search", arguments, ranking_service=ranking_service))
                without_selection = call_tool(self.graph, "kg_build_context", {**arguments, "token_budget": 12000}, ranking_service=ranking_service)
                self.assertEqual("kgdistiller-context-bundle-v2" if graph_options else "kgdistiller-context-bundle-v1", without_selection["schema"])
        self.assertEqual(source_before, repository_bytes(self.graph))

    def test_support_selection_mcp_rejects_wrong_bindings_missing_nodes_and_identity_spoofs(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        plan = retrieval_plan(); plan.update(question="collection", identity_queries=[], lexical_queries=["collection"], limit=1)
        execution = call_tool(self.graph, "kg_search", {"plan": plan})
        manifest = make_support_selection(load_graph_view(self.graph), execution, plan, [{"node_id": "measure", "requirement_ids": ["query-r2"], "reason": "Caller-selected source."}])
        variants = []
        for key, value in (("snapshot_sha256", "b" * 64), ("graph_sha256", "b" * 64), ("question_sha256", "b" * 64), ("plan_sha256", "b" * 64), ("identity_authority", True), ("identity_authority", 0)):
            forged = copy.deepcopy(manifest); forged[key] = value; variants.append(forged)
        forged = copy.deepcopy(manifest); forged["items"][0]["node_id"] = "unknown-source"; variants.append(forged)
        forged = copy.deepcopy(manifest); forged["items"][0]["node_sha256"] = "f" * 64; variants.append(forged)
        for forged in variants:
            with self.assertRaises(RetrievalError):
                call_tool(self.graph, "kg_build_context", {"plan": plan, "support_selection": forged})
        with self.assertRaises(QueryError):
            call_tool(self.graph, "kg_build_context", {"plan": plan, "support_selection": []})
        with self.assertRaisesRegex(QueryError, "unexpected"):
            call_tool(self.graph, "kg_search", {"plan": plan, "support_selection": manifest})
        server = MCPServer(self.graph); server.initialized = True
        forged = copy.deepcopy(manifest); forged["identity_authority"] = True
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kg_build_context", "arguments": {"plan": plan, "support_selection": forged}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual("invalid-support-selection", response["result"]["structuredContent"]["error"]["code"])

    def test_opted_in_service_applies_to_search_and_context_only(self) -> None:
        adapter = FakeEmbedding()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors")
        authority_before = {path.name: path.read_bytes() for path in self.graph.glob("*") if path.is_file()}
        query = "如何给可测集合赋予大小？"

        plain = call_tool(self.graph, "kg_search", {"query": query})
        self.assertEqual("kgdistiller-search-execution-v1", plain["schema"])
        self.assertEqual([], plain["result"]["results"])
        embedded = call_tool(self.graph, "kg_search", {"query": query}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", embedded["schema"])
        self.assertEqual("measure", embedded["result"]["results"][0]["node_id"])
        context = call_tool(self.graph, "kg_build_context", {"query": query, "token_budget": 6000},
                            ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
        self.assertIn("measure", [node["id"] for node in context["nodes"]])
        self.assertEqual(1, adapter.document_calls)
        self.assertEqual([[query]], adapter.query_calls)
        self.assertEqual(1, service.last_cache_stats["rank"]["query_cache_hits"])
        source_view = load_graph_view(self.graph)
        self.assertEqual([search_document(source_view.nodes[node_id]) for node_id in sorted(source_view.nodes)], adapter.document_inputs[0])

        self.assertEqual(call_tool(self.graph, "kg_status", {}),
                         call_tool(self.graph, "kg_status", {}, ranking_service=service))
        resolution = call_tool(self.graph, "kg_resolve_concepts", {"concepts": [query]}, ranking_service=service)
        self.assertEqual("missing", resolution["results"][0]["status"])
        self.assertEqual([[query]], adapter.query_calls)
        self.assertEqual(authority_before, {path.name: path.read_bytes() for path in self.graph.glob("*") if path.is_file()})

    def test_server_forwards_same_service_only_to_retrieval_tools(self) -> None:
        service = object()
        server = MCPServer(self.graph, ranking_service=service)
        server.initialized = True
        with patch("kgdistiller.mcp.call_tool", return_value={"ok": True}) as operation:
            for index, name in enumerate(("kg_search", "kg_build_context", "kg_status", "kg_resolve_concepts")):
                response = server.handle({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                                          "params": {"name": name, "arguments": {}}})
                self.assertFalse(response["result"]["isError"])
                if name in {"kg_search", "kg_build_context"}:
                    self.assertIs(service, operation.call_args.kwargs["ranking_service"])
                else:
                    self.assertNotIn("ranking_service", operation.call_args.kwargs)

    def test_mcp_reranker_service_reuses_cache_and_does_not_resolve_identity(self) -> None:
        adapter = FakeReranker()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors", rerank=True, candidate_limit=2)
        server = MCPServer(self.graph, ranking_service=service)
        server.initialized = True
        query = "如何给可测集合赋予大小？"
        for index, name in enumerate(("kg_search", "kg_build_context")):
            response = server.handle({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                                      "params": {"name": name, "arguments": {"query": query}}})
            self.assertFalse(response["result"]["isError"])
            content = response["result"]["structuredContent"]
            if name == "kg_search":
                rows = content["result"]["results"]
                self.assertEqual(["measure", "sigma-algebra"], [row["node_id"] for row in rows])
                self.assertEqual([{"rank": 2, "score": 0.0}, {"rank": 1, "score": 3.0}],
                                 [row["lanes"]["reranker"] for row in rows])
                self.assertTrue(all(row["fusion"]["method"] == "rrf" for row in rows))
                self.assertAlmostEqual(rows[0]["fusion"]["score"], rows[1]["fusion"]["score"])
                provenance = content["result"]["ranking"]["reranker"]
                self.assertEqual("rrf-base-reranker", provenance["fusion"])
                self.assertEqual(2, provenance["candidate_limit"])
                self.assertEqual(["measure", "sigma-algebra"], [candidate["node_id"] for candidate in provenance["candidates"]])
                self.assertEqual(content["graph_sha256"], provenance["graph_sha256"])
                self.assertEqual(content["snapshot_sha256"], provenance["snapshot_sha256"])
            else:
                self.assertEqual("kgdistiller-search-execution-v2", content["search_execution_schema"])
                self.assertEqual("measure", content["nodes"][0]["id"])
        self.assertEqual(1, adapter.document_calls)
        self.assertEqual([query], [question for question, _ in adapter.pair_calls])
        source_view = load_graph_view(self.graph)
        self.assertEqual([search_document(source_view.nodes[node_id]) for node_id in ("measure", "sigma-algebra")], adapter.pair_calls[0][1])
        self.assertEqual(2, service.last_cache_stats["rerank"]["pair_cache_hits"])
        self.assertEqual(0, service.last_cache_stats["rerank"]["pair_inference_items"])
        before = len(adapter.pair_calls)
        resolution = call_tool(self.graph, "kg_resolve_concepts", {"concepts": [query]}, ranking_service=service)
        self.assertEqual("missing", resolution["results"][0]["status"])
        self.assertEqual(before, len(adapter.pair_calls))

    def test_mcp_context_and_alignment_outputs_use_bound_v1_contracts(self) -> None:
        status = call_tool(self.graph, "kg_status", {})
        context = call_tool(
            self.graph,
            "kg_build_context",
            {"query": "measure", "token_budget": 5000},
        )
        candidate = copy.deepcopy(fixture_nodes()[1])
        candidate.update({"id": "paper-measure", "label": "Paper measure"})
        candidate["properties"]["aliases"] = []
        candidate_snapshot = candidate_snapshot_with([candidate])
        alignment = call_tool(
            self.graph,
            "kg_align_graph",
            {"candidate_snapshot": candidate_snapshot},
        )
        comparison = call_tool(
            self.graph,
            "kg_compare_graph",
            {"candidate_snapshot": candidate_snapshot},
        )
        proposal = call_tool(
            self.graph,
            "kg_create_proposal",
            {"candidate_snapshot": candidate_snapshot},
        )

        self.assertEqual("kgdistiller-context-bundle-v1", context["schema"])
        self.assertEqual("kgdistiller-alignment-report-v1", alignment["schema"])
        self.assertEqual("kgdistiller-graph-comparison-v1", comparison["schema"])
        self.assertEqual("kgdistiller-agent-proposal-v1", proposal["schema"])
        for report in (alignment, comparison, proposal):
            self.assertEqual(status["alignment_sha256"], report["alignment_sha256"])


class SourceEvidenceMCPTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-evidence-mcp-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.graph = self.root / "missing-graph"
        self.raw = ("# 数据\r\n\r\n| Model | MSE |\r\n|---|---:|\r\n"
                    "| μ网络 | 0.79 |\r\n| Base | 8.20 |\r\n").encode("utf-8")
        self.manifest = write_source_evidence_manifest(self.root, {"paper": self.raw, "other": b"MSE alpha other\n"}, hash_mode="normalized-utf8")
        self.server = MCPServer(self.graph)
        self.server.initialized = True

    def request(self, **options) -> dict:
        arguments = {"query": "MSE μ网络", "manifest_path": str(self.manifest), **options}
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "kg_search_source_evidence", "arguments": arguments}})
        return response["result"]

    def test_raw_source_tool_has_an_explicit_independent_input_surface(self) -> None:
        tool = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_search_source_evidence")
        schema = tool["inputSchema"]
        self.assertEqual({"query", "manifest_path"}, set(schema["required"]))
        self.assertEqual({"query", "manifest_path", "doc_ids", "limit", "byte_budget", "context_projection"}, set(schema["properties"]))
        self.assertIs(False, schema["additionalProperties"])

    def test_source_reference_tool_resolves_only_document_declarations_without_graph(self) -> None:
        from kgdistiller.source_references import validate_source_reference_result
        manifest = json.loads(self.manifest.read_text())
        manifest["documents"][0]["source_version"] = "1234.5678v2"
        self.manifest.write_text(json.dumps(manifest))
        before = repository_bytes(self.root)
        result = call_tool(self.graph, "kg_resolve_source_references", {"manifest_path": str(self.manifest), "references": ["paper v2", "paper v3", "1234.5678v2"]})
        validate_source_reference_result(result)
        self.assertEqual(["matched", "unmatched", "matched"], [r["status"] for r in result["resolutions"]])
        self.assertIs(False, result["identity_authority"])
        self.assertEqual(before, repository_bytes(self.root))
        self.assertFalse(self.graph.exists())
        for options in ({"manifest_path": self.manifest.name, "references": ["paper"]}, {"manifest_path": str(self.manifest), "references": []}, {"manifest_path": str(self.manifest), "references": [False]}):
            with self.subTest(options=options), self.assertRaises(QueryError):
                call_tool(self.graph, "kg_resolve_source_references", options)

    def test_server_preserves_exact_unicode_table_bytes_without_a_graph_or_writes(self) -> None:
        before = repository_bytes(self.root)
        response = self.request(doc_ids=["paper"])
        self.assertFalse(response["isError"])
        result = response["structuredContent"]
        self.assertEqual(result, json.loads(response["content"][0]["text"]))
        self.assertEqual(result, call_tool(self.graph, "kg_search_source_evidence",
                                          {"query": "MSE μ网络", "manifest_path": str(self.manifest), "doc_ids": ["paper"]}))
        self.assertIs(False, result["identity_authority"])
        self.assertIs(False, result["complete_definition"])
        self.assertTrue(result["fragments"])
        table = next(fragment for fragment in result["fragments"] if fragment["fragment_type"] == "table")
        self.assertIn("| μ网络 | 0.79 |\r\n| Base | 8.20 |\r\n", table["text"])
        for fragment in result["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(self.raw[span["start"]:span["end"]], fragment["text"].encode("utf-8"))
            self.assertEqual(hashlib.sha256(self.raw).hexdigest(), fragment["raw_source_sha256"])
            self.assertEqual("paper", fragment["doc_id"])
            self.assertIs(False, fragment["identity_authority"])
            self.assertIs(False, fragment["complete_definition"])
        self.assertEqual(len(canonical_json(result).encode("utf-8")), result["budget"]["used_bytes"])
        self.assertEqual(before, repository_bytes(self.root))
        self.assertFalse(self.graph.exists())

    def test_manifest_path_argument_types_and_exact_filters_fail_closed(self) -> None:
        for options in ({"manifest_path": self.manifest.name}, {"limit": True},
                        {"byte_budget": True}, {"limit": 0}, {"limit": 101},
                        {"byte_budget": 200001}, {"doc_ids": ["missing"]},
                        {"doc_ids": ["paper", "paper"]}, {"doc_ids": [0]},
                        {"namespace": "personal"}):
            with self.subTest(options=options):
                response = self.request(**options)
                self.assertTrue(response["isError"])
                self.assertIn("error", response["structuredContent"])
                self.assertNotIn("fragments", response["structuredContent"])
        with self.assertRaisesRegex(QueryError, "missing required tool argument: manifest_path"):
            call_tool(self.graph, "kg_search_source_evidence", {"query": "MSE"})
        empty = self.request(doc_ids=[])
        self.assertFalse(empty["isError"])
        self.assertEqual([], empty["structuredContent"]["fragments"])

    def test_byte_budget_returns_omissions_and_explicit_metadata_failure(self) -> None:
        self.manifest = write_source_evidence_manifest(self.root, {"paper": ("alpha long " * 300 + "\n\nalpha concise result\n").encode()})
        response = self.request(query="alpha", byte_budget=2500)
        self.assertFalse(response["isError"])
        result = response["structuredContent"]
        self.assertGreater(result["omitted_fragments"], 0)
        self.assertTrue(result["omissions"] or result["diagnostics_truncated"])
        self.assertLessEqual(len(canonical_json(result).encode("utf-8")), 2500)
        failed = self.request(query="alpha", byte_budget=1)
        self.assertTrue(failed["isError"])
        self.assertEqual("source-budget-too-small", failed["structuredContent"]["error"]["code"])

    def test_compact_source_evidence_server_validates_exact_raw_spans_without_graph(self) -> None:
        from kgdistiller.source_context import validate_source_context
        full = self.request(doc_ids=["paper"], byte_budget=200000)["structuredContent"]
        before = repository_bytes(self.root)
        response = self.request(doc_ids=["paper"], context_projection="compact")
        self.assertFalse(response["isError"])
        compact = response["structuredContent"]
        validate_source_context(compact, source_results=[full])
        self.assertEqual("kgdistiller-source-evidence-context-v1", compact["schema"])
        self.assertIs(False, compact["identity_authority"])
        for fragment in compact["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(self.raw[span["start"]:span["end"]], fragment["text"].encode())
        self.assertEqual(before, repository_bytes(self.root))
        self.assertFalse(self.graph.exists())

    def test_compact_source_evidence_rejects_bad_projection_budget_and_stale_packing(self) -> None:
        from kgdistiller.source_context import build_source_context
        for options in ({"context_projection": "summary"}, {"context_projection": "compact", "byte_budget": 1}):
            response = self.request(**options)
            self.assertTrue(response["isError"])
        def changed_source(*args, **kwargs):
            result = build_source_context(*args, **kwargs)
            (self.root / "raw-sources" / "paper.txt").write_bytes(b"changed source\n")
            return result
        with patch("kgdistiller.source_context.build_source_context", side_effect=changed_source):
            failed = self.request(context_projection="compact")
        self.assertTrue(failed["isError"])
        self.assertEqual("stale-source", failed["structuredContent"]["error"]["code"])

    def test_traversal_final_and_intermediate_symlinks_cannot_read_sources(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        source = self.root / "raw-sources" / "paper.txt"
        nested = source.parent / "linked"
        nested.symlink_to(source.parent, target_is_directory=True)
        final = source.parent / "linked.txt"
        final.symlink_to(source)
        for path in ("../outside.txt", "paper.txt/../paper.txt", "./paper.txt",
                     "/outside.txt", "C:/outside.txt", "linked.txt", "linked/paper.txt",
                     "knowledge/graph/nodes.jsonl"):
            with self.subTest(path=path):
                invalid = copy.deepcopy(manifest)
                invalid["documents"][0]["path"] = path
                self.manifest.write_text(json.dumps(invalid), encoding="utf-8")
                response = self.request()
                self.assertTrue(response["isError"])
                self.assertEqual("unsafe-source-path", response["structuredContent"]["error"]["code"])

    def test_missing_stale_invalid_utf8_and_nonregular_sources_are_tool_errors(self) -> None:
        source = self.root / "raw-sources" / "paper.txt"
        response = self.request(manifest_path=str(self.root / "missing-manifest.json"))
        self.assertTrue(response["isError"])
        self.assertEqual("source-unavailable", response["structuredContent"]["error"]["code"])
        for raw, code in ((b"changed MSE source\n", "stale-source"),
                          (b"\xff", "invalid-source-encoding")):
            with self.subTest(code=code):
                source.write_bytes(raw)
                response = self.request()
                self.assertTrue(response["isError"])
                self.assertEqual(code, response["structuredContent"]["error"]["code"])
        source.unlink()
        response = self.request()
        self.assertTrue(response["isError"])
        self.assertEqual("unsafe-source-path", response["structuredContent"]["error"]["code"])
        source.mkdir()
        response = self.request()
        self.assertTrue(response["isError"])
        self.assertEqual("invalid-source-file", response["structuredContent"]["error"]["code"])


if __name__ == "__main__":
    unittest.main()
