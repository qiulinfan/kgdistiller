from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import ContractError, validate_contract
from kgdistiller.obsidian_export import ObsidianExportError, export_obsidian_graph
from tests.knowledge_fixture import make_fixture

CHAPTER = (
    "\\begin{definition}[Sigma algebra]\n"
    "A collection closed under complement and countable union.\n"
    "\\end{definition}\n"
    "\\begin{definition}[Measure]\n"
    "A countably additive function on a sigma algebra.\n"
    "\\end{definition}\n"
)


class ObsidianGraphFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.repo = self.fixture.root
        self.output = self.repo / ".knowledge/build/obsidian/semantic-graph.json"
        self.authority = self.fixture.write_source("notes/chapter.tex", CHAPTER)
        self.fixture.add_entry("sigma-algebra", "Sigma algebra", "notes/chapter.tex", 1, 3,
                               kind="definition", aliases=["σ-algebra"])
        self.fixture.add_entry("measure", "Measure", "notes/chapter.tex", 4, 6,
                               kind="definition", understanding="understood")
        self.fixture.add_edge("sigma-algebra", "prerequisite-for", "measure",
                              evidence="A measure is defined on a sigma algebra.")

    def export(self, output: Path | None = None) -> dict:
        return export_obsidian_graph(self.fixture.base, output or self.output)

    def feed(self) -> dict:
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_feed_carries_entries_sources_edges_and_definitions(self) -> None:
        result = self.export()
        feed = self.feed()
        self.assertEqual(
            {"status": "exported", "output": str(self.output.resolve()), "counts": feed["counts"]},
            result,
        )
        self.assertEqual(feed, validate_contract(feed))
        self.assertEqual({"concepts": 2, "sources": 1, "semantic_edges": 1, "definitions": 2}, feed["counts"])
        self.assertEqual(
            {"id": "sigma-algebra", "label": "Sigma algebra", "kind": "definition", "aliases": ["σ-algebra"],
             "authority": ".knowledge/entries/sigma-algebra.md", "understanding": "unknown"},
            feed["concepts"][1],
        )
        self.assertEqual("understood", feed["concepts"][0]["understanding"])
        self.assertEqual([{"authority": "notes/chapter.tex"}], feed["sources"])
        self.assertEqual(
            [{"source_authority": "notes/chapter.tex", "target": "measure", "line_start": 4, "line_end": 6},
             {"source_authority": "notes/chapter.tex", "target": "sigma-algebra", "line_start": 1, "line_end": 3}],
            feed["definitions"],
        )
        self.assertEqual(
            [{"source": "sigma-algebra", "relation": "prerequisite-for", "target": "measure",
              "evidence": "A measure is defined on a sigma algebra."}],
            feed["semantic_edges"],
        )
        self.assertEqual({"schema", "counts", "concepts", "sources", "semantic_edges", "definitions"}, set(feed))
        self.assertEqual(["semantic-graph.json"], [path.name for path in self.output.parent.iterdir()])

    def test_stale_evidence_and_low_confidence_edges_stay_in_the_feed(self) -> None:
        self.authority.write_text(CHAPTER.replace("complement and countable union", "complements"), encoding="utf-8")
        edges = self.repo / ".knowledge/edges.jsonl"
        edges.write_text(edges.read_text(encoding="utf-8").replace('"high"', '"unverified"'), encoding="utf-8")
        self.export()
        self.assertEqual(1, len(self.feed()["semantic_edges"]))
        self.assertEqual(2, self.feed()["counts"]["concepts"])

    def test_any_text_source_is_accepted(self) -> None:
        self.fixture.write_source("notes/log.unusual", "A plain fact.\n")
        self.fixture.add_entry("plain-fact", "Plain fact", "notes/log.unusual", 1)
        self.export()
        self.assertIn({"authority": "notes/log.unusual"}, self.feed()["sources"])

    def test_contract_rejects_inconsistent_feeds(self) -> None:
        self.export()
        feed = self.feed()
        broken = []
        changed = copy.deepcopy(feed)
        changed["definitions"].pop()
        broken.append(changed)
        changed = copy.deepcopy(feed)
        changed["semantic_edges"][0]["target"] = "missing"
        broken.append(changed)
        changed = copy.deepcopy(feed)
        changed["concepts"][0]["authority"] = "../outside.md"
        broken.append(changed)
        changed = copy.deepcopy(feed)
        changed["sources"].append({"authority": "notes/unused.tex"})
        changed["counts"]["sources"] += 1
        broken.append(changed)
        changed = copy.deepcopy(feed)
        changed["concepts"][0]["status"] = "current"
        broken.append(changed)
        for index, payload in enumerate(broken):
            with self.subTest(index=index), self.assertRaises(ContractError):
                validate_contract(payload)

    def test_output_must_be_a_json_file_outside_sources_and_obsidian(self) -> None:
        outside = "must lie under .knowledge/, outside entries/"
        cases = (
            (self.repo / ".knowledge/build/obsidian/graph.txt", "must be a .json file"),
            (self.repo / ".obsidian/graph.json", "cannot be inside .obsidian"),
            (self.repo / "notes/graph.json", outside),
            (self.repo / "graph.json", outside),
            (self.repo / ".knowledge/entries/graph.json", outside),
        )
        for output, message in cases:
            with self.subTest(output=output), self.assertRaisesRegex(ObsidianExportError, message):
                self.export(output)
            self.assertFalse(output.exists())

    def test_output_outside_the_base_root_is_allowed(self) -> None:
        output = self.repo.parent / "feeds/graph.json"
        self.export(output)
        self.assertTrue(output.is_file())

    def test_symlinked_output_is_rejected(self) -> None:
        target = self.repo / "elsewhere.json"
        target.write_text("{}\n", encoding="utf-8")
        self.output.parent.mkdir(parents=True)
        try:
            self.output.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("file symlink creation is unavailable")
        with self.assertRaisesRegex(ObsidianExportError, "must not be a symlink"):
            self.export()
        self.assertEqual("{}\n", target.read_text(encoding="utf-8"))

    def test_unreadable_store_keeps_the_previous_feed(self) -> None:
        self.export()
        baseline = self.output.read_bytes()
        (self.repo / ".knowledge/entries/measure.md").unlink()
        with self.assertRaisesRegex(ObsidianExportError, "has no entry for measure"):
            self.export()
        self.assertEqual(baseline, self.output.read_bytes())

    def test_interrupted_write_keeps_the_previous_feed(self) -> None:
        self.export()
        baseline = self.output.read_bytes()
        with (
            patch("kgdistiller.home.os.replace", side_effect=OSError("interrupted")),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            self.export()
        self.assertEqual(baseline, self.output.read_bytes())
        self.assertEqual(["semantic-graph.json"], os.listdir(self.output.parent))


if __name__ == "__main__":
    unittest.main()
