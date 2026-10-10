"""The entry store is the node source for every public read adapter."""
from __future__ import annotations

import unittest

from kgdistiller.mcp import call_tool
from kgdistiller.query import GraphView, QueryError, get
from kgdistiller.retrieval import execute_retrieval_plan, query_retrieval_plan
from tests.knowledge_fixture import KnowledgeFixture, make_fixture

BETA_TEXT = "Beta transforms an input representation."


def build_entry_store(fixture: KnowledgeFixture) -> None:
    """Write two entries and one edge through the real entry writer."""
    fixture.write_source(
        "notes/chapter.txt",
        "Alpha is an unrelated pending definition.\n\n" f"{BETA_TEXT}\n",
    )
    fixture.add_entry("alpha", "Alpha", "notes/chapter.txt", 1)
    fixture.add_entry(
        "beta", "Beta", "notes/chapter.txt", 3,
        understanding="not-yet-understood",
        pending_prerequisites=["Encoder: used here without an explanation."],
    )
    fixture.add_edge("alpha", "prerequisite-for", "beta")


class EntryStoreCallersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        build_entry_store(self.fixture)

    def view(self) -> GraphView:
        return GraphView.load(self.fixture.base)

    def test_query_mcp_and_retrieval_read_the_same_entry(self) -> None:
        result = get(self.view(), "beta")
        node = result["node"]
        self.assertEqual(node["evidence"], BETA_TEXT)
        self.assertEqual(node["entry"], ".knowledge/entries/beta.md")
        self.assertEqual(node["understanding"], "not-yet-understood")
        self.assertEqual([edge["source"] for edge in result["incoming"]], ["alpha"])
        self.assertNotIn("backlinks", result)
        self.assertEqual(
            call_tool(self.fixture.base, "kg_get_node", {"id": "beta"}),
            result,
        )
        execution = execute_retrieval_plan(self.view(), query_retrieval_plan("Beta"))
        self.assertTrue(any(row["node_id"] == "beta" for row in execution["result"]["results"]))

    def test_edited_entry_is_read_without_regeneration(self) -> None:
        path = self.fixture.root / ".knowledge/entries/beta.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "Beta as stated in the source.", "Beta, reworded by hand."), encoding="utf-8")
        self.assertEqual(get(self.view(), "beta")["node"]["summary"], "Beta, reworded by hand.")

    def test_stale_evidence_never_hides_an_entry(self) -> None:
        self.fixture.write_source("notes/chapter.txt", "Alpha is an unrelated pending definition.\n")
        self.assertIn("beta", self.view().nodes)

    def test_dangling_edge_and_pending_journal_reject_readers(self) -> None:
        (self.fixture.root / ".knowledge/entries/alpha.md").unlink()
        with self.assertRaisesRegex(QueryError, "has no entry for alpha"):
            self.view()
        self.fixture.add_entry("alpha", "Alpha", "notes/chapter.txt", 1)
        journal = self.fixture.root / ".knowledge/build/kgdistiller-ingest/journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(QueryError, "ingest install"):
            self.view()


if __name__ == "__main__":
    unittest.main()
