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
from kgdistiller.mcp import TOOL_DEFINITIONS, call_tool
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
            return call_tool(self.root / "nonexistent-graph", "kg_compiled_knowledge", {"library_path": str(self.library), "operation": operation, **arguments})

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
        for operation in ("search", "get", "pack"):
            with self.subTest(operation=operation), self.assertRaises(QueryError):
                self.mcp(operation)
        with self.assertRaisesRegex(QueryError, "must be absolute"):
            call_tool(self.root, "kg_compiled_knowledge", {"library_path": "library.json", "operation": "browse"})

    def test_compiled_tool_is_read_only(self):
        tool = next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "kg_compiled_knowledge")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])


if __name__ == "__main__":
    unittest.main()
