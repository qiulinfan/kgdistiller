from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.mcp import (  # noqa: E402
    FEDERATED_TOOL_DEFINITIONS,
    MAX_TOOL_RESPONSE_BYTES,
    MCPServer,
    RECALL_TOOL_DEFINITION,
    TOOL_DEFINITIONS,
    call_tool,
)
from kgdistiller.contracts import canonical_json  # noqa: E402
from kgdistiller.recall import make_recall_request  # noqa: E402
from kgdistiller.query import QueryError, query_status  # noqa: E402
import tests.test_federation as federation_fixture  # noqa: E402
from tests.test_query import candidate_snapshot_with, fixture_nodes, write_fixture_graph  # noqa: E402


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

    def test_mcp_context_and_alignment_outputs_use_bound_v2_contracts(self) -> None:
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

        self.assertEqual("qlkg-context-bundle-v2", context["schema"])
        self.assertEqual("qlkg-alignment-report-v2", alignment["schema"])
        self.assertEqual("qlkg-graph-comparison-v2", comparison["schema"])
        self.assertEqual("qlkg-agent-proposal-v2", proposal["schema"])
        for report in (alignment, comparison, proposal):
            self.assertEqual(status["alignment_sha256"], report["alignment_sha256"])


class FederatedMCPTest(unittest.TestCase):
    def setUp(self) -> None:
        federation_fixture.FederationFixture.setUp(self)

    def tearDown(self) -> None:
        federation_fixture.FederationFixture.tearDown(self)

    @staticmethod
    def _initialize(server: MCPServer) -> None:
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_federated_discovery_is_closed_and_only_advertises_callable_tools(self) -> None:
        self.assertEqual("kg_recall", RECALL_TOOL_DEFINITION["name"])
        self.assertIn("outputSchema", RECALL_TOOL_DEFINITION)
        self.assertEqual(
            [*TOOL_DEFINITIONS, RECALL_TOOL_DEFINITION],
            FEDERATED_TOOL_DEFINITIONS,
        )
        recall_only = MCPServer(None, federated=True, home=self.home)
        self._initialize(recall_only)
        listed = recall_only.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        self.assertEqual(
            ["kg_recall"],
            [row["name"] for row in listed["result"]["tools"]],
        )

        combined = MCPServer(
            self.analysis / ".kgdistiller/graph",
            federated=True,
            home=self.home,
        )
        self._initialize(combined)
        combined_list = combined.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
        )
        self.assertEqual(
            [row["name"] for row in FEDERATED_TOOL_DEFINITIONS],
            [row["name"] for row in combined_list["result"]["tools"]],
        )

    def test_recall_tool_captures_once_without_loading_a_legacy_graph(self) -> None:
        server = MCPServer(None, federated=True, home=self.home)
        self._initialize(server)
        request = make_recall_request("status")
        import kgdistiller.recall as recall_module

        with patch(
            "kgdistiller.mcp.load_graph_view",
            side_effect=AssertionError("legacy graph loaded"),
        ), patch(
            "kgdistiller.recall.capture_federation",
            wraps=recall_module.capture_federation,
        ) as capture:
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "kg_recall", "arguments": request},
                }
            )
        self.assertEqual(1, capture.call_count)
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual("qlkg-recall-report-v1", result["structuredContent"]["schema"])
        self.assertEqual(
            canonical_json(result["structuredContent"]), result["content"][0]["text"]
        )

    def test_invalid_recall_arguments_fail_closed_before_capture(self) -> None:
        server = MCPServer(None, federated=True, home=self.home)
        self._initialize(server)
        request = make_recall_request("status")
        request["unknown"] = True
        with patch("kgdistiller.recall.capture_federation") as capture:
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "kg_recall", "arguments": request},
                }
            )
        capture.assert_not_called()
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual("qlkg-recall-error-v1", result["structuredContent"]["schema"])
        self.assertEqual(
            canonical_json(result["structuredContent"]), result["content"][0]["text"]
        )


if __name__ == "__main__":
    unittest.main()
