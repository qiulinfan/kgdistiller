"""The read-only MCP server: tool surface, input bounds, response cap and fresh read-only connections."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller.index import index
from kgdistiller.mcp import (
    MAX_TOOL_RESPONSE_BYTES,
    TOOL_DEFINITIONS,
    MCPServer,
    ToolError,
    call_tool,
    serve_stdio,
)
from kgdistiller.retrieve import browse, neighbors, pack, search
from tests.knowledge_fixture import (
    fake_encoder,
    make_record_home,
    missing_retrieval_extra,
)

SOURCE = "Title\nA measure space is a triple.\nA measure is countably additive.\n测度论研究可测空间。\n"


def node(label: str, lines: str, extra: str = "") -> str:
    return f"label: {label}\nkind: definition\nsource: notes/a.txt\nlines: {lines}\n{extra}".rstrip("\n")


class MCPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self)
        self.kb.write_source("notes/a.txt", SOURCE)
        self.kb.write_record("measure-space", node("Measure space", "2", 'requires: ["[[measure]]"]'),
                             "A triple.", ["A measure space"])
        self.kb.write_record("measure", node("Measure", "3", "aliases: [测度]"), "A set function.", ["A measure"])
        index()

    def server(self) -> MCPServer:
        server = MCPServer()
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return server

    def call(self, server: MCPServer, name: str, arguments: Any) -> dict[str, Any]:
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}})
        return response["result"]

    def snapshot(self) -> dict[str, tuple[bytes, int]]:
        """Every file under the home and the base, except SQLite's own WAL coordination files."""
        found = {}
        for directory in (self.kb.home, self.kb.root):
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.name.endswith(("-wal", "-shm")):
                    found[str(path)] = (path.read_bytes(), path.stat().st_mtime_ns)
        return found

    def test_tool_surface_is_exactly_the_eight_read_tools(self) -> None:
        self.assertEqual(["kg_search", "kg_resolve", "kg_get", "kg_neighbors", "kg_browse", "kg_pack", "kg_drafts", "kg_find"],
                         [tool["name"] for tool in TOOL_DEFINITIONS])
        filters = {"base", "kind", "class", "source", "understanding"}
        properties = {tool["name"]: set(tool["inputSchema"]["properties"]) for tool in TOOL_DEFINITIONS}
        self.assertEqual(properties, {
            "kg_search": {"query", "limit", "no_dense", *filters},
            "kg_resolve": {"terms", *filters},
            "kg_get": {"uids", "source_lines"},
            "kg_neighbors": {"uids", "role", "dir", "depth", *filters},
            "kg_browse": {"handle", *filters},
            "kg_pack": {"uids", "budget", "requires_depth", *filters},
            "kg_drafts": {"base", "source", "names"},
            "kg_find": {"text", "base", "source", "limit"},
        })
        for tool in TOOL_DEFINITIONS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertTrue(tool["annotations"]["readOnlyHint"])
        listed = self.server().handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(TOOL_DEFINITIONS, listed["result"]["tools"])

    def test_initialize_describes_a_read_only_whole_home_server(self) -> None:
        server = MCPServer()
        self.assertEqual(-32002, server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["error"]["code"])
        result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
        self.assertEqual({"tools": {"listChanged": False}}, result["capabilities"])
        self.assertIn("Read-only", result["instructions"])
        self.assertIn("run `kgd index --no-embed`", result["instructions"])
        self.assertEqual({}, server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"])

    def test_inputs_are_bounded_and_unknown_arguments_rejected(self) -> None:
        cases = {
            "unexpected tool arguments: base_path": ("kg_search", {"query": "q", "base_path": "/x"}),
            "missing required tool argument: query": ("kg_search", {}),
            "query has an invalid length": ("kg_search", {"query": "x" * 4097}),
            "limit is outside": ("kg_search", {"query": "q", "limit": 0}),
            "limit must be integer": ("kg_search", {"query": "q", "limit": True}),
            "no_dense must be boolean": ("kg_search", {"query": "q", "no_dense": "yes"}),
            "class contains unsupported items": ("kg_search", {"query": "q", "class": ["edge"]}),
            "base contains invalid items": ("kg_search", {"query": "q", "base": [1]}),
            "terms has an invalid item count": ("kg_resolve", {"terms": []}),
            "terms contains an invalid string length": ("kg_resolve", {"terms": ["x" * 4097]}),
            "uids has an invalid item count": ("kg_get", {"uids": ["kb:x"] * 129}),
            "source_lines is outside": ("kg_get", {"uids": ["kb:x"], "source_lines": -1}),
            "arguments must be an object": ("kg_get", ["kb:x"]),
            "dir must be one of out, in, both": ("kg_neighbors", {"uids": ["kb:x"], "dir": "sideways"}),
            "depth is outside": ("kg_neighbors", {"uids": ["kb:x"], "depth": 0}),
            "depth is outside its allowed range": ("kg_neighbors", {"uids": ["kb:x"], "depth": 33}),
            "role contains invalid items": ("kg_neighbors", {"uids": ["kb:x"], "role": [1]}),
            "budget is outside": ("kg_pack", {"uids": ["kb:x"], "budget": 0}),
            "budget is outside its allowed range": ("kg_pack", {"uids": ["kb:x"], "budget": 4194305}),
            "requires_depth is outside": ("kg_pack", {"uids": ["kb:x"], "requires_depth": -1}),
            "argument uids has an invalid item count": ("kg_pack", {"uids": []}),
            "handle has an invalid length": ("kg_browse", {"handle": ""}),
            "unknown tool: kg_no_such_tool": ("kg_no_such_tool", {}),
        }
        server = self.server()
        for message, (name, arguments) in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ToolError, message):
                    call_tool(name, arguments)
                result = self.call(server, name, arguments)
                self.assertTrue(result["isError"])
                self.assertIn(message, result["structuredContent"]["error"]["message"])

    def test_response_size_is_capped(self) -> None:
        server = self.server()
        with patch("kgdistiller.mcp.call_tool", return_value={"blob": "x" * (MAX_TOOL_RESPONSE_BYTES + 1)}):
            result = self.call(server, "kg_search", {"query": "q"})
        self.assertTrue(result["isError"])
        self.assertIn("tool response exceeds", result["structuredContent"]["error"]["message"])

    def test_calls_match_the_core_and_write_nothing(self) -> None:
        before = self.snapshot()
        server = self.server()
        found = self.call(server, "kg_search", {"query": "measure space", "class": ["node"], "limit": 5})
        self.assertFalse(found["isError"])
        self.assertEqual(search("measure space", limit=5), found["structuredContent"])
        self.assertEqual(json.loads(found["content"][0]["text"]), found["structuredContent"])
        self.assertEqual("kb:measure-space", found["structuredContent"]["results"][0]["uid"])
        resolved = self.call(server, "kg_resolve", {"terms": ["测度"]})["structuredContent"]
        self.assertEqual(["kb:measure"], [sense["uid"] for sense in resolved["terms"][0]["senses"]])
        got = self.call(server, "kg_get", {"uids": ["measure-space"], "source_lines": 0})["structuredContent"]
        self.assertEqual("2\tA measure space is a triple.", got["records"][0]["source_text"])
        linked = self.call(server, "kg_neighbors", {"uids": ["measure-space"], "dir": "out"})["structuredContent"]
        self.assertEqual(neighbors(["measure-space"]), linked)
        self.assertEqual(["kb:measure"], [edge["to"] for edge in linked["edges"]])
        self.assertEqual(browse(), self.call(server, "kg_browse", {})["structuredContent"])
        self.assertEqual(browse("kb:notes/a.txt"),
                         self.call(server, "kg_browse", {"handle": "kb:notes/a.txt"})["structuredContent"])
        packet = self.call(server, "kg_pack", {"uids": ["measure-space"]})["structuredContent"]
        self.assertEqual(pack(["measure-space"]), packet)
        self.assertEqual(["kb:measure-space", "kb:measure"], [record["uid"] for record in packet["records"]])
        unknown = self.call(server, "kg_browse", {"handle": "nope"})
        self.assertTrue(unknown["isError"])
        self.assertIn("unknown base 'nope'", unknown["structuredContent"]["error"]["message"])
        self.assertEqual(before, self.snapshot())

    def embed(self, model: str = "fake/model") -> None:
        self.kb.embedding = model
        self.kb.write_config()

    def test_kg_search_runs_the_dense_lane_unless_no_dense(self) -> None:
        self.embed()
        server = self.server()
        with fake_encoder() as encoders:
            index()
            dense = self.call(server, "kg_search", {"query": "measure space"})
            sparse = self.call(server, "kg_search", {"query": "measure space", "no_dense": True})
        self.assertFalse(dense["isError"])
        self.assertEqual(["lexical", "dense", "name"], dense["structuredContent"]["lanes"])
        self.assertEqual(["lexical", "name"], sparse["structuredContent"]["lanes"])
        self.assertEqual(["fake/model", "fake/model"], encoders.loads)

    def test_missing_retrieval_extra_is_a_tool_error(self) -> None:
        self.embed()
        with fake_encoder():
            index()
        server = self.server()
        with missing_retrieval_extra():
            result = self.call(server, "kg_search", {"query": "measure space"})
            sparse = self.call(server, "kg_search", {"query": "measure space", "no_dense": True})
        self.assertTrue(result["isError"])
        self.assertIn("install kgdistiller[retrieval]", result["structuredContent"]["error"]["message"])
        self.assertFalse(sparse["isError"])

    def test_kg_get_reads_no_file_outside_the_registered_sources(self) -> None:
        outside = self.kb.home.parent / "private.txt"
        outside.write_text("secret line\n", encoding="utf-8")
        (self.kb.root / "notes/link.txt").symlink_to(outside)
        sources = {"traversal": "../private.txt", "absolute": str(outside), "escaping-link": "notes/link.txt"}
        for identifier, source in sources.items():
            self.kb.write_record(identifier, node(identifier, "1").replace("notes/a.txt", source), "Text.", ["Title line"])
        index()
        server = self.server()
        got = self.call(server, "kg_get", {"uids": [f"kb:{name}" for name in sources], "source_lines": 2})
        self.assertFalse(got["isError"])
        self.assertEqual([None] * 3, [record["source_text"] for record in got["structuredContent"]["records"]])
        self.assertNotIn("secret", json.dumps(got))

    def test_each_call_opens_a_fresh_connection(self) -> None:
        server = self.server()
        self.assertEqual([], self.call(server, "kg_resolve", {"terms": ["mass"]})["structuredContent"]["terms"][0]["senses"])
        self.kb.write_record("mass", node("Mass", "3"), "A total.", ["A measure"])
        lagging = self.call(server, "kg_resolve", {"terms": ["mass"]})["structuredContent"]
        self.assertEqual((lagging["terms"][0]["senses"], lagging["lag"]["changed_files"]), ([], 1))
        index()
        fresh = self.call(server, "kg_resolve", {"terms": ["mass"]})["structuredContent"]
        self.assertEqual((["kb:mass"], 0), ([sense["uid"] for sense in fresh["terms"][0]["senses"]], fresh["lag"]["changed_files"]))

    def test_missing_database_is_a_tool_error(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.kb.home / 'index.sqlite'}{suffix}").unlink(missing_ok=True)
        result = self.call(self.server(), "kg_search", {"query": "measure"})
        self.assertTrue(result["isError"])
        self.assertIn("run `kgd index`", result["structuredContent"]["error"]["message"])
        self.assertFalse((self.kb.home / "index.sqlite").exists())

    def test_stdio_framing(self) -> None:
        lines = [
            "not json",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "kg_get", "arguments": {"uids": ["kb:measure"]}}}),
        ]
        output = io.StringIO()
        serve_stdio(input_stream=io.StringIO("\n".join(lines) + "\n"), output_stream=output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(-32700, responses[0]["error"]["code"])
        self.assertEqual(1, responses[1]["id"])
        self.assertEqual("kb:measure", responses[2]["result"]["structuredContent"]["records"][0]["uid"])


if __name__ == "__main__":
    unittest.main()
