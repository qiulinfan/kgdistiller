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
    MAX_TOOL_RESPONSE_BYTES,
    MCPServer,
    TOOL_DEFINITIONS,
    call_tool,
)
from kgdistiller.query import QueryError, query_status  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
