from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import (  # noqa: E402
    embedding_inventory,
    index_generation_token,
    install_embedding_records,
    write_agent_index,
)
from kgdistiller.mcp import (  # noqa: E402
    MAX_MESSAGE_BYTES,
    MCPServer,
    TOOL_DEFINITIONS,
    serve_stdio,
)
from kgdistiller.providers import (  # noqa: E402
    ProviderAdapterRegistry,
    provider_config_sha256,
)
from tests.test_agent import (  # noqa: E402
    ac_candidate_snapshot,
    candidate_snapshot,
    fixture_snapshot,
)


def retrieval_plan() -> dict:
    return {
        "schema": "qlkg-retrieval-plan-v1",
        "question": "How does beta depend on alpha?",
        "namespace": "personal",
        "identity_queries": ["alpha"],
        "lexical_queries": ["countable closure"],
        "semantic_queries": [],
        "graph": {
            "seed_ids": ["alpha"],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 1,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


class MCPServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-mcp-test-")
        self.database = Path(self.temporary.name) / "knowledge.sqlite"
        write_agent_index(self.database, fixture_snapshot())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self, server: MCPServer) -> dict:
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        assert response is not None
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def test_lifecycle_and_tools_are_read_only(self) -> None:
        server = MCPServer(self.database)
        before = server.handle(
            {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}}
        )
        self.assertEqual(-32002, before["error"]["code"])

        initialized = self.initialize(server)
        self.assertEqual("2025-06-18", initialized["result"]["protocolVersion"])
        self.assertEqual("0.3.0", initialized["result"]["serverInfo"]["version"])
        listed = server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(
            [
                "kg_status",
                "kg_resolve_concepts",
                "kg_search",
                "kg_get_node",
                "kg_expand",
                "kg_ppr",
                "kg_build_context",
                "kg_align_graph",
                "kg_compare_graph",
                "kg_create_proposal",
            ],
            names,
        )
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in TOOL_DEFINITIONS))
        self.assertTrue(all(not tool["annotations"]["destructiveHint"] for tool in TOOL_DEFINITIONS))

    def test_tool_results_include_structured_and_text_content(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"query": "countable closure", "max_depth": 1},
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        execution = result["structuredContent"]
        self.assertEqual("qlkg-search-execution-v1", execution["schema"])
        self.assertEqual("legacy", execution["plan_mode"])
        self.assertEqual("alpha", execution["result"]["results"][0]["node_id"])
        self.assertEqual(
            result["structuredContent"],
            json.loads(result["content"][0]["text"]),
        )

    def test_search_accepts_a_bounded_plan_without_legacy_controls(self) -> None:
        server = MCPServer(self.database, expected_graph_sha256="a" * 64)
        self.initialize(server)
        before = self.database.read_bytes()

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"plan": retrieval_plan()},
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        execution = result["structuredContent"]
        self.assertEqual("planned", execution["plan_mode"])
        self.assertEqual("qlkg-search-result-v2", execution["result"]["schema"])
        self.assertEqual(before, self.database.read_bytes())

    def test_semantic_plan_uses_one_query_only_batch_and_never_embeds_documents(self) -> None:
        config = {
            "adapter": "mcp-query-fixture",
            "model": "mcp-query-v1",
            "dimensions": 2,
            "base_url": "http://127.0.0.1",
            "credential_env": "UNUSED_MCP_QUERY_KEY",
        }
        digest = provider_config_sha256(config)
        inventory = embedding_inventory(self.database)
        install_embedding_records(
            self.database,
            [
                {
                    "namespace": "personal",
                    "node_id": node["node_id"],
                    "provider": "mcp-query-fixture",
                    "model": "mcp-query-v1",
                    "dimensions": 2,
                    "embedding_input_schema": "qlkg-node-embedding-text-v1",
                    "provider_config_sha256": digest,
                    "content_sha256": node["content_sha256"],
                    "vector": [1.0, 0.0]
                    if node["node_id"] == "alpha"
                    else [0.0, 1.0],
                }
                for node in inventory["nodes"]
            ],
            expected_snapshot_sha256=inventory["snapshot_sha256"],
            expected_graph_sha256=inventory["graph_sha256"],
        )

        class QueryOnlyProvider:
            name = "mcp-query-fixture"
            model = "mcp-query-v1"
            dimensions = 2
            provider_config_sha256 = digest

            def __init__(self) -> None:
                self.query_batches: list[list[str]] = []

            def embed_queries(self, texts: list[str]) -> list[list[float]]:
                self.query_batches.append(list(texts))
                return [[1.0, 0.0] for _ in texts]

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("document embedding is forbidden during query")

            def embed(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("generic embedding is forbidden during query")

        provider = QueryOnlyProvider()
        creates: list[str] = []
        registry = ProviderAdapterRegistry()

        def factory(profile_name: str, raw_config: dict, credential: str):
            creates.append(profile_name)
            return provider

        registry.register("mcp-query-fixture", factory, requires_credential=False)
        plan = retrieval_plan()
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["semantic_queries"] = [f"query {index}" for index in range(32)]
        plan["graph"]["seed_ids"] = []
        before_token = index_generation_token(self.database)
        before_bytes = self.database.read_bytes()
        server = MCPServer(
            self.database,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=registry,
            environ={},
            expected_graph_sha256="a" * 64,
        )
        self.initialize(server)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {"name": "kg_search", "arguments": {"plan": plan}},
            }
        )

        self.assertFalse(response["result"]["isError"])
        lane = response["result"]["structuredContent"]["result"]["lanes"]["semantic"]
        self.assertEqual({"status": "enabled", "queries": 32, "results": 2}, lane)
        self.assertEqual(["fixture"], creates)
        self.assertEqual([plan["semantic_queries"]], provider.query_batches)
        self.assertEqual(before_token, index_generation_token(self.database))
        self.assertEqual(before_bytes, self.database.read_bytes())

        unavailable = MCPServer(
            self.database,
            expected_graph_sha256="a" * 64,
        )
        self.initialize(unavailable)
        degraded = unavailable.handle(
            {
                "jsonrpc": "2.0",
                "id": 32,
                "method": "tools/call",
                "params": {"name": "kg_search", "arguments": {"plan": plan}},
            }
        )
        degraded_lane = degraded["result"]["structuredContent"]["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", degraded_lane["status"])
        self.assertEqual("provider-unavailable", degraded_lane["reason"])

    def test_legacy_search_keeps_the_public_4096_character_input_bound(self) -> None:
        server = MCPServer(self.database, expected_graph_sha256="a" * 64)
        self.initialize(server)
        query = "alpha " + ("q" * 2044)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 32,
                "method": "tools/call",
                "params": {"name": "kg_search", "arguments": {"query": query}},
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual("legacy", result["structuredContent"]["plan_mode"])

    def test_legacy_search_accepts_depth_eight_and_advertises_bounded_node_types(
        self,
    ) -> None:
        server = MCPServer(self.database, expected_graph_sha256="a" * 64)
        self.initialize(server)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 33,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {
                        "query": "alpha",
                        "max_depth": 8,
                        "node_types": ["knowledge"],
                    },
                },
            }
        )

        self.assertFalse(response["result"]["isError"])
        search_schema = next(
            tool["inputSchema"]
            for tool in TOOL_DEFINITIONS
            if tool["name"] == "kg_search"
        )
        properties = search_schema["properties"]
        self.assertEqual(8, properties["max_depth"]["maximum"])
        self.assertEqual(
            ["knowledge", "field", "topic"],
            properties["node_types"]["items"]["enum"],
        )

        invalid = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 34,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"query": "alpha", "node_types": ["arbitrary"]},
                },
            }
        )
        self.assertTrue(invalid["result"]["isError"])

    def test_context_accepts_the_same_bounded_plan(self) -> None:
        server = MCPServer(self.database, expected_graph_sha256="a" * 64)
        self.initialize(server)
        before = self.database.read_bytes()

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {
                    "name": "kg_build_context",
                    "arguments": {"plan": retrieval_plan(), "token_budget": 5000},
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(
            "qlkg-context-bundle-v1",
            result["structuredContent"]["schema"],
        )
        self.assertEqual(
            retrieval_plan()["question"],
            result["structuredContent"]["query"],
        )
        self.assertEqual(before, self.database.read_bytes())

    def test_legacy_context_preserves_the_full_question(self) -> None:
        server = MCPServer(self.database, expected_graph_sha256="a" * 64)
        self.initialize(server)
        query = "alpha " + ("q" * 2044)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 35,
                "method": "tools/call",
                "params": {
                    "name": "kg_build_context",
                    "arguments": {"query": query},
                },
            }
        )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(query, response["result"]["structuredContent"]["query"])

    def test_search_and_context_require_exactly_one_query_or_plan(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)
        cases = (
            ("kg_search", {}),
            ("kg_search", {"query": "alpha", "plan": retrieval_plan()}),
            ("kg_build_context", {}),
            (
                "kg_build_context",
                {"query": "alpha", "plan": retrieval_plan()},
            ),
            ("kg_search", {"plan": retrieval_plan(), "namespace": "personal"}),
        )

        for request_id, (name, arguments) in enumerate(cases, start=40):
            with self.subTest(name=name, arguments=sorted(arguments)):
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                )
                self.assertTrue(response["result"]["isError"])

    def test_retrieval_tool_inputs_never_accept_provider_configuration_or_secrets(self) -> None:
        by_name = {tool["name"]: tool for tool in TOOL_DEFINITIONS}
        for name in ("kg_search", "kg_build_context"):
            schema = by_name[name]["inputSchema"]
            properties = schema["properties"]
            rendered = json.dumps(schema, sort_keys=True).casefold()
            with self.subTest(name=name):
                self.assertIn("query", properties)
                self.assertIn("plan", properties)
                self.assertNotIn("provider_config", rendered)
                self.assertNotIn("credential_env", rendered)
                self.assertNotIn("api_key", rendered)
                self.assertNotIn("secret", rendered)

    def test_tool_validation_errors_are_tool_results(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"query": "alpha", "unexpected": True},
                },
            }
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("unexpected tool arguments", response["result"]["structuredContent"]["error"]["message"])

    def test_missing_or_stale_index_search_fails_without_publishing_files(self) -> None:
        missing = Path(self.temporary.name) / "missing.sqlite"
        missing_server = MCPServer(missing, expected_graph_sha256="a" * 64)
        self.initialize(missing_server)
        missing_response = missing_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"plan": retrieval_plan()},
                },
            }
        )

        self.assertTrue(missing_response["result"]["isError"])
        self.assertFalse(missing.exists())
        self.assertEqual([], list(missing.parent.glob("missing.sqlite*")))

        stale_server = MCPServer(self.database, expected_graph_sha256="b" * 64)
        self.initialize(stale_server)
        before = {
            path.name: path.read_bytes()
            for path in self.database.parent.glob(f"{self.database.name}*")
            if path.is_file()
        }
        stale_response = stale_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 51,
                "method": "tools/call",
                "params": {
                    "name": "kg_search",
                    "arguments": {"plan": retrieval_plan()},
                },
            }
        )
        after = {
            path.name: path.read_bytes()
            for path in self.database.parent.glob(f"{self.database.name}*")
            if path.is_file()
        }

        self.assertTrue(stale_response["result"]["isError"])
        self.assertEqual(before, after)

    def test_long_lived_server_rechecks_authority_digest_for_each_retrieval(self) -> None:
        current = ["a" * 64]
        calls: list[str] = []

        def resolve_digest() -> str:
            calls.append(current[0])
            return current[0]

        server = MCPServer(
            self.database,
            expected_graph_sha256_resolver=resolve_digest,
        )
        self.initialize(server)

        def search(request_id: int) -> dict:
            return server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "kg_search",
                        "arguments": {"plan": retrieval_plan()},
                    },
                }
            )

        self.assertFalse(search(60)["result"]["isError"])
        current[0] = "b" * 64
        stale = search(61)
        self.assertTrue(stale["result"]["isError"])
        self.assertEqual(
            "stale-index",
            stale["result"]["structuredContent"]["error"]["code"],
        )
        current[0] = "a" * 64
        self.assertFalse(search(62)["result"]["isError"])
        self.assertEqual(["a" * 64, "b" * 64, "a" * 64], calls)

    def test_compare_tool_keeps_candidate_namespace_isolated(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "kg_compare_graph",
                    "arguments": {"candidate_snapshot": candidate_snapshot()},
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(1, result["structuredContent"]["summary"]["new"])
        self.assertEqual("paper:fixture", result["structuredContent"]["candidate"]["namespace"])

    def test_ppr_and_alignment_tools_are_explainable_and_read_only(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)
        before = self.database.read_bytes()

        ppr_response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "kg_ppr",
                    "arguments": {"ids": ["alpha"], "limit": 2},
                },
            }
        )
        alignment_response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "kg_align_graph",
                    "arguments": {"candidate_snapshot": ac_candidate_snapshot()},
                },
            }
        )

        ppr = ppr_response["result"]
        alignment = alignment_response["result"]
        self.assertFalse(ppr["isError"])
        self.assertEqual("qlkg-ppr-result-v1", ppr["structuredContent"]["schema"])
        self.assertFalse(alignment["isError"])
        self.assertEqual(
            "qlkg-alignment-report-v1", alignment["structuredContent"]["schema"]
        )
        self.assertEqual(before, self.database.read_bytes())

    def test_proposal_tool_generates_review_data_without_writing(self) -> None:
        server = MCPServer(self.database)
        self.initialize(server)
        before = self.database.read_bytes()

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "kg_create_proposal",
                    "arguments": {
                        "candidate_snapshot": candidate_snapshot(),
                        "target_authority": "notes/research/paper.md",
                    },
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual("qlkg-agent-proposal-v1", result["structuredContent"]["schema"])
        self.assertEqual(before, self.database.read_bytes())

    def test_stdio_is_newline_delimited_json_rpc_without_notification_output(self) -> None:
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "kg_status", "arguments": {}},
            },
        ]
        source = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
        destination = io.StringIO()

        serve_stdio(self.database, input_stream=source, output_stream=destination)

        lines = destination.getvalue().splitlines()
        self.assertEqual(2, len(lines))
        responses = [json.loads(line) for line in lines]
        self.assertEqual("2025-11-25", responses[0]["result"]["protocolVersion"])
        self.assertEqual("qlkg-agent-index-v2", responses[1]["result"]["structuredContent"]["schema"])

    def test_malformed_stdio_messages_are_bounded_and_do_not_stop_the_server(self) -> None:
        deep = "[" * 2000 + "0" + "]" * 2000
        huge_integer = (
            '{"jsonrpc":"2.0","id":'
            + "9" * 10_000
            + ',"method":"ping"}'
        )
        non_finite = '{"jsonrpc":"2.0","id":NaN,"method":"ping"}'
        raw_surrogate = '"\ud800"'
        escaped_surrogate = '{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}'
        surrogate_key = '{"jsonrpc":"2.0","id":1,"method":"ping","\\ud800":1}'
        oversized = "x" * (MAX_MESSAGE_BYTES + 10)
        initialize = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        source = io.StringIO(
            "\n".join(
                [
                    deep,
                    huge_integer,
                    non_finite,
                    raw_surrogate,
                    escaped_surrogate,
                    surrogate_key,
                    oversized,
                    initialize,
                ]
            )
            + "\n"
        )
        destination = io.StringIO()

        serve_stdio(self.database, input_stream=source, output_stream=destination)

        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual(
            [-32700, -32700, -32700, -32700, -32700, -32700, -32600],
            [response["error"]["code"] for response in responses[:7]],
        )
        self.assertEqual("2025-11-25", responses[7]["result"]["protocolVersion"])

    def test_request_ids_and_method_params_have_protocol_safe_shapes(self) -> None:
        server = MCPServer(self.database)
        invalid_id = server.handle(
            {"jsonrpc": "2.0", "id": ["nested"], "method": "ping"}
        )
        invalid_key = server.handle(
            {"jsonrpc": "2.0", "id": 7, "method": "ping", "\ud800": 1}
        )
        invalid_initialize = server.handle(
            {"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": [1]}
        )
        self.initialize(server)
        invalid_call = server.handle(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": [1]}
        )

        self.assertEqual(-32600, invalid_id["error"]["code"])
        self.assertEqual(-32600, invalid_key["error"]["code"])
        self.assertEqual(-32602, invalid_initialize["error"]["code"])
        self.assertEqual(-32602, invalid_call["error"]["code"])


if __name__ == "__main__":
    unittest.main()
