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

from kgdistiller.agent import write_agent_index  # noqa: E402
from kgdistiller.mcp import MCPServer, TOOL_DEFINITIONS, serve_stdio  # noqa: E402
from tests.test_agent import (  # noqa: E402
    ac_candidate_snapshot,
    candidate_snapshot,
    fixture_snapshot,
)


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
        self.assertEqual("alpha", result["structuredContent"]["results"][0]["node"]["id"])
        self.assertEqual(
            result["structuredContent"],
            json.loads(result["content"][0]["text"]),
        )

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


if __name__ == "__main__":
    unittest.main()
