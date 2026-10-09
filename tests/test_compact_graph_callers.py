"""Entry-backed storage stays usable through public read and write adapters."""
from __future__ import annotations

import shutil
import unittest
from dataclasses import replace
from pathlib import Path

from kgdistiller.capture import prepare_capture
from kgdistiller.ingest import apply_ingest, load_request
from kgdistiller.mcp import call_tool
from kgdistiller.query import GraphView, QueryError, get
from kgdistiller.retrieval import (
    RetrievalError,
    execute_retrieval_plan,
    legacy_retrieval_plan,
)
from kgdistiller.semantic_retrieval import SemanticRankingService
from kgdistiller.web import load_graph_payload
from tests import test_capture, test_semantic_retrieval


class CompactGraphCallersTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture = test_capture.CaptureTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        result = prepare_capture(fixture.paths, fixture.payload(), fixture.output)
        apply_ingest(fixture.paths, load_request(Path(result["artifacts"]["apply"]), mode="apply"))
        self.root = fixture.root
        self.graph = self.root / "custom/state"
        self.graph.parent.mkdir()
        shutil.move(fixture.paths.graph_dir, self.graph)
        self.paths = replace(fixture.paths, graph_dir=self.graph)

    def test_custom_graph_root_reaches_query_web_mcp_and_retrieval(self) -> None:
        with self.assertRaisesRegex(QueryError, "explicit repo_root"):
            GraphView.load(self.graph)
        view = GraphView.load(self.graph, repo_root=self.root)
        result = get(view, "beta")
        node = result["node"]
        self.assertEqual(node["text"], self.fixture.payload()["text"])
        self.assertEqual(node["entry"]["understanding"], "not-yet-understood")
        self.assertEqual(
            call_tool(self.graph, "kg_get_node", {"id": "beta"}, repo_root=self.root),
            result,
        )
        self.assertIn(node, load_graph_payload(self.graph, repo_root=self.root)["nodes"])
        execution = execute_retrieval_plan(self.graph, legacy_retrieval_plan("Beta"), repo_root=self.root)
        self.assertTrue(any(row["node_id"] == "beta" for row in execution["result"]["results"]))
        self.assertEqual({path.name for path in self.graph.iterdir()}, {"manifest.json", "nodes.jsonl", "edges.jsonl", "references.jsonl"})

    def test_custom_graph_capture_keeps_entry_authority_root(self) -> None:
        payload = self.fixture.payload()
        payload["review"].update(action="update", target_id="beta")
        payload["text"] = "Beta is the reviewed updated definition."
        payload.pop("source_content")
        result = prepare_capture(self.paths, payload, self.fixture.output / "updated")
        apply_ingest(self.paths, load_request(Path(result["artifacts"]["apply"]), mode="apply"))
        self.assertEqual(get(GraphView.load(self.graph, repo_root=self.root), "beta")["node"]["text"], payload["text"])

    def test_changed_markdown_rejects_readers_and_cached_semantic_view(self) -> None:
        view = GraphView.load(self.graph, repo_root=self.root)
        service = SemanticRankingService(
            test_semantic_retrieval.FakeEmbedding(), cache_dir=self.root / "vector-cache",
        )
        plan = legacy_retrieval_plan("Beta")
        execute_retrieval_plan(view, plan, ranking_service=service)
        authority = self.root / view.nodes["beta"]["properties"]["entry_authority"]
        authority.write_text(authority.read_text().replace("transforms an input", "changes an input"))
        for read in (
            lambda: GraphView.load(self.graph, repo_root=self.root),
            lambda: load_graph_payload(self.graph, repo_root=self.root),
            lambda: call_tool(self.graph, "kg_get_node", {"id": "beta"}, repo_root=self.root),
        ):
            with self.assertRaisesRegex(QueryError, "out of sync"):
                read()
        with self.assertRaises(RetrievalError) as caught:
            execute_retrieval_plan(view, plan, ranking_service=service)
        self.assertEqual(caught.exception.code, "stale-generation")


if __name__ == "__main__":
    unittest.main()
