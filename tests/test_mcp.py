from __future__ import annotations

import unittest
from unittest.mock import patch

from kgdistiller.home import resolve_base
from kgdistiller.mcp import (
    MAX_TOOL_RESPONSE_BYTES,
    TOOL_DEFINITIONS,
    MCPServer,
    call_tool,
)
from kgdistiller.query import QueryError, load_graph_view, query_status
from kgdistiller.semantic_retrieval import (
    SemanticRankingService,
    search_document,
)
from tests.knowledge_fixture import use_temporary_home
from tests.test_retrieval_cli import (
    FakeReranker,
    authority_bytes,
    retrieval_plan,
    write_fixture_store,
    write_graph_retrieval_fixture,
)
from tests.test_semantic_retrieval import FakeEmbedding


class MCPTest(unittest.TestCase):
    def setUp(self) -> None:
        home = use_temporary_home(self)
        self.root = home.parent / "kb"
        self.root.mkdir()
        self.graph = write_fixture_store(self.root)
        self.base = resolve_base("kb", self.root)

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
                "kg_compiled_knowledge",
                "kg_status",
                "kg_resolve_concepts",
                "kg_search",
                "kg_get_node",
                "kg_expand",
                "kg_ppr",
                "kg_build_context",
            },
            names,
        )
        ppr = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_ppr")
        self.assertEqual(
            {
                "ids",
                "edge_types",
                "direction",
                "limit",
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
            call_tool(self.base,
                "kg_resolve_concepts",
                {"concepts": ["x" * 4097]},
            )
        with self.assertRaisesRegex(QueryError, "invalid length"):
            call_tool(self.base, "kg_get_node", {"id": "x" * 257})

    def test_mcp_fails_closed_before_emitting_an_oversized_tool_result(self) -> None:
        server = MCPServer(self.base)
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
        direct = call_tool(self.base, "kg_status", {})
        self.assertEqual(query_status(load_graph_view(self.base)), direct)

        server = MCPServer(self.base)
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
        entry = self.graph / "entries/measure.md"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(original.replace("aliases:\n  - 测度", "aliases:\n  - 测度\n  - Mass function"), encoding="utf-8")
        resolved = call_tool(self.base, "kg_resolve_concepts", {"concepts": ["mass function"]})
        self.assertEqual(["measure"], resolved["results"][0]["candidate_ids"])
        # A malformed manual edit is rejected; no partial view is returned.
        entry.write_text(original.replace("label: Measure", "label: Measure\nstatus: current"), encoding="utf-8")
        with self.assertRaisesRegex(QueryError, "unknown frontmatter key"):
            call_tool(self.base, "kg_status", {})
        entry.write_text(original, encoding="utf-8")
        self.assertEqual({"entries": 3, "edges": 2}, call_tool(self.base, "kg_status", {})["counts"])

    def test_graph_mcp_controls_are_per_request_and_fail_closed_when_unused(self) -> None:
        for name in ("kg_search", "kg_build_context"):
            schema = next(tool["inputSchema"] for tool in TOOL_DEFINITIONS if tool["name"] == name)
            self.assertEqual(False, schema["properties"]["graph_retrieval"]["default"])
            self.assertEqual(5, schema["properties"]["graph_seed_candidates"]["default"])
            self.assertEqual("high-confidence", schema["properties"]["graph_edge_policy"]["default"])
            for controls in ({"graph_seed_candidates": 5}, {"graph_edge_policy": "high-confidence"},
                             {"graph_retrieval": False, "graph_seed_candidates": 5}):
                with self.subTest(name=name, controls=controls), self.assertRaisesRegex(QueryError, "require graph_retrieval"):
                    call_tool(self.base, name, {"query": "collection", **controls})
            for controls in ({"graph_retrieval": 1}, {"graph_retrieval": True, "graph_seed_candidates": True},
                             {"graph_retrieval": True, "graph_seed_candidates": 0},
                             {"graph_retrieval": True, "graph_seed_candidates": 33},
                             {"graph_retrieval": True, "graph_edge_policy": "reviewed"}):
                with self.subTest(name=name, controls=controls), self.assertRaises(QueryError):
                    call_tool(self.base, name, {"query": "collection", **controls})
        with self.assertRaisesRegex(QueryError, "unexpected"):
            call_tool(self.base, "kg_status", {"graph_retrieval": True})
        server = MCPServer(self.base)
        server.initialized = True
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kg_search", "arguments": {"query": "collection", "graph_edge_policy": "high-confidence"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("require graph_retrieval", response["result"]["structuredContent"]["error"]["message"])

    def test_graph_mcp_search_and_context_preserve_complete_bounded_source_paths(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        before = authority_bytes(self.graph)
        server = MCPServer(self.base)
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
        bundle = call_tool(self.base, "kg_build_context", {"plan": plan, "graph_retrieval": True, "graph_seed_candidates": 1, "token_budget": 18000})
        self.assertEqual("kgdistiller-context-bundle-v2", bundle["schema"])
        self.assertEqual("kgdistiller-search-execution-v3", bundle["search_execution_schema"])
        packet = next(item for item in bundle["support_packets"] if item["node_id"] == "absolute-continuity")
        self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"], packet["nodes"])
        self.assertEqual(2, len(packet["path"]["steps"]))
        self.assertTrue(all(step["evidence"] and step["confidence"] == "high" for step in packet["path"]["steps"]))
        self.assertFalse(packet["logical_entailment"])
        self.assertEqual(["Domain is a sigma algebra."], next(node for node in bundle["nodes"] if node["id"] == "measure")["prerequisites"])
        self.assertEqual(2, len(bundle["edges"]))
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 18000)
        plain = call_tool(self.base, "kg_search", {"query": "collection", "graph_retrieval": False})
        self.assertEqual("kgdistiller-search-execution-v1", plain["schema"])
        self.assertEqual(plain["result"]["results"], execution["result"]["results"])
        self.assertEqual(before, authority_bytes(self.graph))

    def test_graph_mcp_policy_relaxation_and_model_composition_are_explicit(self) -> None:
        self.graph = write_graph_retrieval_fixture(self.root)
        relaxed = call_tool(self.base, "kg_search", {"query": "collection", "graph_retrieval": True, "graph_seed_candidates": 1, "graph_edge_policy": "all", "graph_strategy": "bfs"})
        self.assertEqual(["sigma-algebra"], [row["node_id"] for row in relaxed["result"]["results"]])
        self.assertIn("unverified-result", [row["node_id"] for row in relaxed["result"]["graph_retrieval"]["neighbors"]])
        self.assertEqual("all", relaxed["result"]["graph_retrieval"]["policy"]["edge_policy"])
        adapter = FakeReranker()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors", rerank=True, candidate_limit=1)
        embedded = call_tool(self.base, "kg_search", {"query": "如何给可测集合赋予大小？", "graph_retrieval": True, "graph_seed_candidates": 1}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v3", embedded["schema"])
        self.assertIn("embedding", embedded["result"]["ranking"])
        self.assertEqual([], embedded["result"]["graph_retrieval"]["seeds"]["identity"])
        self.assertEqual(["measure"], [item["node_id"] for item in embedded["result"]["graph_retrieval"]["seeds"]["candidate"]])
        self.assertTrue(all(not item["identity_authority"] for item in embedded["result"]["graph_retrieval"]["seeds"]["candidate"]))
        without_graph = call_tool(self.base, "kg_search", {"query": "如何给可测集合赋予大小？"}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", without_graph["schema"])
        self.assertEqual(without_graph["result"]["results"], embedded["result"]["results"])
        self.assertEqual(without_graph["result"]["ranking"]["reranker"]["candidates"], embedded["result"]["ranking"]["reranker"]["candidates"])
        # Reranker scores are not persisted; each request scores its own pool.
        self.assertEqual(2, len(adapter.pair_calls))
        for _, documents in adapter.pair_calls:
            self.assertEqual([search_document(load_graph_view(self.base).nodes["measure"])], documents)
        self.assertEqual(0, service.last_cache_stats["document_inference_items"])

    def test_opted_in_service_applies_to_search_and_context_only(self) -> None:
        adapter = FakeEmbedding()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors")
        authority_before = {path.name: path.read_bytes() for path in self.graph.glob("*") if path.is_file()}
        query = "如何给可测集合赋予大小？"

        plain = call_tool(self.base, "kg_search", {"query": query})
        self.assertEqual("kgdistiller-search-execution-v1", plain["schema"])
        self.assertNotIn("embedding", plain["result"]["lanes"])
        embedded = call_tool(self.base, "kg_search", {"query": query}, ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", embedded["schema"])
        self.assertEqual("measure", embedded["result"]["results"][0]["node_id"])
        context = call_tool(self.base, "kg_build_context", {"query": query, "token_budget": 6000},
                            ranking_service=service)
        self.assertEqual("kgdistiller-search-execution-v2", context["search_execution_schema"])
        self.assertIn("measure", [node["id"] for node in context["nodes"]])
        self.assertEqual(1, adapter.document_calls)
        self.assertEqual([[query], [query]], adapter.query_calls)
        source_view = load_graph_view(self.base)
        self.assertEqual([search_document(source_view.nodes[node_id]) for node_id in sorted(source_view.nodes)], adapter.document_inputs[0])

        self.assertEqual(call_tool(self.base, "kg_status", {}),
                         call_tool(self.base, "kg_status", {}, ranking_service=service))
        resolution = call_tool(self.base, "kg_resolve_concepts", {"concepts": [query]}, ranking_service=service)
        self.assertEqual("missing", resolution["results"][0]["status"])
        self.assertEqual([[query], [query]], adapter.query_calls)
        self.assertEqual(authority_before, {path.name: path.read_bytes() for path in self.graph.glob("*") if path.is_file()})

    def test_server_forwards_same_service_only_to_retrieval_tools(self) -> None:
        service = object()
        server = MCPServer(self.base, ranking_service=service)
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

    def test_mcp_reranker_service_reuses_vectors_and_does_not_resolve_identity(self) -> None:
        adapter = FakeReranker()
        service = SemanticRankingService(adapter, cache_dir=self.root / "vectors", rerank=True, candidate_limit=2)
        server = MCPServer(self.base, ranking_service=service)
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
            else:
                self.assertEqual("kgdistiller-search-execution-v2", content["search_execution_schema"])
                self.assertEqual("measure", content["nodes"][0]["id"])
        self.assertEqual(1, adapter.document_calls)
        self.assertEqual([query, query], [question for question, _ in adapter.pair_calls])
        source_view = load_graph_view(self.base)
        self.assertEqual([search_document(source_view.nodes[node_id]) for node_id in ("measure", "sigma-algebra")], adapter.pair_calls[0][1])
        self.assertEqual(0, service.last_cache_stats["document_inference_items"])
        before = len(adapter.pair_calls)
        resolution = call_tool(self.base, "kg_resolve_concepts", {"concepts": [query]}, ranking_service=service)
        self.assertEqual("missing", resolution["results"][0]["status"])
        self.assertEqual(before, len(adapter.pair_calls))

    def test_mcp_context_and_get_node_read_entries(self) -> None:
        context = call_tool(self.base,
            "kg_build_context",
            {"query": "measure", "token_budget": 5000},
        )
        self.assertEqual("kgdistiller-context-bundle-v1", context["schema"])
        self.assertNotIn("references", context)
        node = call_tool(self.base, "kg_get_node", {"id": "measure"})
        self.assertEqual({"node", "incoming", "outgoing"}, set(node))
        self.assertEqual(".knowledge/entries/measure.md", node["node"]["entry"])
        self.assertEqual("A countably additive set function.", node["node"]["evidence"])


if __name__ == "__main__":
    unittest.main()
