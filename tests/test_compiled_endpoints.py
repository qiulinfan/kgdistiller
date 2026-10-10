from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kgdistiller.cli import main
from kgdistiller.mcp import TOOL_DEFINITIONS, MCPServer, call_tool
from kgdistiller.query import QueryError
from tests.test_compiled_retrieval import library_payload


class CompiledEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = self.root / "library.json"
        self.library.write_text(json.dumps(library_payload(), ensure_ascii=False), encoding="utf-8")

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", "agent", "compiled", "--library", str(self.library), *arguments]), \
                redirect_stdout(stdout), redirect_stderr(stderr), \
                patch("kgdistiller.vault_registry.resolve_repo_root", side_effect=AssertionError("compiled input must not load a vault")):
            code = main()
        return code, stdout.getvalue(), stderr.getvalue()

    def mcp(self, operation, **arguments):
        with patch("kgdistiller.mcp.load_graph_view", side_effect=AssertionError("compiled input must not load a graph")):
            return call_tool(self.root / "nonexistent-vault", self.root / "nonexistent-vault/.knowledge/sources.json", "kg_compiled_knowledge", {"library_path": str(self.library), "operation": operation, **arguments})

    def test_cli_search_and_full_get_work_without_vault_registration(self):
        code, stdout, stderr = self.cli("search", "有界线性算子如何判定连续", "--limit", "1")
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual("map-continuous", json.loads(stdout)["candidates"][0]["reference"])
        code, stdout, stderr = self.cli("get", "map-continuous", "norm")
        self.assertEqual((0, ""), (code, stderr))
        entries = json.loads(stdout)["entries"]
        self.assertEqual(library_payload()["nodes"]["map-continuous"]["conditions"], entries[0]["conditions"])
        self.assertEqual(3, len(entries[1]["notation"]))

    def test_cli_browse_and_budgeted_pack_retain_conditions(self):
        code, stdout, stderr = self.cli("browse", "bounded map")
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(2, len(json.loads(stdout)["senses"]))
        code, stdout, stderr = self.cli("pack", "map-continuous", "norm", "--budget", "12000")
        self.assertEqual((0, ""), (code, stderr))
        result = json.loads(stdout)
        self.assertEqual(["map-continuous", "norm"], [entry["reference"] for entry in result["entries"]])
        self.assertLessEqual(result["bytes_used"], result["byte_budget"])
        self.assertEqual(result["bytes_used"], len(stdout.encode("utf-8")))

    def test_cli_invalid_input_is_a_normal_error(self):
        self.library.write_text("not JSON", encoding="utf-8")
        code, stdout, stderr = self.cli("browse")
        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        self.assertIn("error", json.loads(stderr))

    def test_mcp_search_get_browse_and_pack_share_the_same_library(self):
        self.assertEqual("map-continuous", self.mcp("search", query="continuous linear", limit=1)["candidates"][0]["reference"])
        self.assertEqual(library_payload()["nodes"]["map-continuous"]["evidence"], self.mcp("get", reference="map-continuous")["evidence"])
        self.assertEqual(2, len(self.mcp("browse", reference="bounded map")["senses"]))
        packed = self.mcp("pack", references=["map-continuous", "norm"], byte_budget=12000)
        self.assertEqual(2, len(packed["entries"]))
        self.assertFalse(any(gap["reason"] == "dependency-not-packed" for gap in packed["gaps"]))

    def test_mcp_rejects_missing_operation_inputs_and_relative_paths(self):
        for operation in ("search", "get", "inventory", "pack"):
            with self.subTest(operation=operation), self.assertRaises(QueryError):
                self.mcp(operation)
        with self.assertRaisesRegex(QueryError, "must be absolute"):
            call_tool(self.root, self.root / ".knowledge/sources.json", "kg_compiled_knowledge", {"library_path": "library.json", "operation": "browse"})

    def test_inventory_cli_and_mcp_preserve_every_declaration_without_writes(self):
        payload = library_payload()
        payload["terms"]["bounded map"]["senses"].append({"id": "unregistered-use", "context": "Declared tail"})
        for index in range(57):
            payload["nodes"][f"use-{index}"] = {
                "name": "An authored use", "paper": "source-analysis", "statement": str(index),
                "surfaces": {"head_terms": ["Bounded map"]},
            }
        self.library.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        code, stdout, stderr = self.cli("inventory", "Ｂｏｕｎｄｅｄ　ｍａｐ")
        self.assertEqual((0, ""), (code, stderr))
        result = json.loads(stdout)
        self.assertEqual(57, len(result["uses"]))
        self.assertEqual("use-56", result["uses"][-1]["reference"])
        self.assertFalse(result["groups"][0]["senses"][-1]["available"])
        self.assertEqual("not-certified", result["source_corpus_completeness"])
        self.assertEqual(result, self.mcp("inventory", term="Ｂｏｕｎｄｅｄ　ｍａｐ"))
        server = MCPServer(self.root / "nonexistent-vault", self.root / "nonexistent-vault/.knowledge/sources.json", ranking_service=object())
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        with patch("kgdistiller.mcp.load_graph_view", side_effect=AssertionError("inventory must not load a graph")):
            response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "kg_compiled_knowledge", "arguments": {
                    "library_path": str(self.library), "operation": "inventory", "term": "Ｂｏｕｎｄｅｄ　ｍａｐ",
                },
            }})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(result, response["result"]["structuredContent"])
        self.assertEqual(before, {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_inventory_unknown_terms_are_explicit_without_source_absence_claims(self):
        code, stdout, stderr = self.cli("inventory", "unregistered term")
        self.assertEqual((0, ""), (code, stderr))
        result = json.loads(stdout)
        self.assertFalse(result["matched"])
        self.assertEqual([{"term": "unregistered term", "reason": "unmatched-term"}], result["gaps"])
        self.assertEqual("not-certified", result["source_corpus_completeness"])
        self.assertEqual(result, self.mcp("inventory", term="unregistered term"))

    def test_inventory_rejects_missing_terms_invalid_values_and_ranking_controls(self):
        for invalid in (None, False, [], {}, "", "  "):
            with self.subTest(term=invalid), self.assertRaises(QueryError):
                self.mcp("inventory", term=invalid)
        for options in ({"limit": 1}, {"byte_budget": 1}):
            with self.subTest(options=options), self.assertRaisesRegex(QueryError, "does not rank or truncate"):
                self.mcp("inventory", term="Bounded map", **options)
        code, stdout, stderr = self.cli("inventory", "  ")
        self.assertEqual((1, ""), (code, stdout))
        self.assertIn("nonempty text", json.loads(stderr)["error"])

    def test_compiled_tool_is_read_only(self):
        tool = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_compiled_knowledge")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])


if __name__ == "__main__":
    unittest.main()
