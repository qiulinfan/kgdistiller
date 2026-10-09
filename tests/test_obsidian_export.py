from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller import obsidian_export
from kgdistiller.cli import apply_delta, synchronize
from kgdistiller.contracts import validate_contract
from kgdistiller.obsidian_export import ObsidianExportError, export_obsidian_graph
from kgdistiller.project import initialize_project


class ObsidianGraphFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-obsidian-")
        self.repo = Path(self.temporary.name)
        self.registry = self.repo / ".knowledge/sources.json"
        self.graph = self.repo / ".knowledge/graph"
        self.identities = self.repo / ".knowledge/identities.json"
        self.alignments = self.repo / ".knowledge/alignments.json"
        self.output = self.repo / ".knowledge/build/obsidian/semantic-graph.json"
        initialize_project(
            self.repo,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )
        self.authority = self.repo / "notes/chapter.md"
        self.authority.write_text(
            "> **Definition: --[[Sigma algebra]]--**\n>\n"
            "> A collection closed under complement and countable union.\n\n"
            "> **Definition: --[[Measure]]--**\n>\n"
            "> A countably additive function on a [[Sigma algebra]].\n",
            encoding="utf-8",
        )
        self.sync()
        delta = self.repo / "relation.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure",
                            "evidence": "A measure is defined on a sigma algebra.",
                        }
                    ],
                    "remove_nodes": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(self.graph, delta)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self) -> None:
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            write=True,
        )

    def export(self, output: Path | None = None) -> dict:
        return export_obsidian_graph(
            self.repo,
            output or self.output,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
        )

    def feed(self) -> dict:
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_feed_carries_typed_concepts_sources_edges_and_references(self) -> None:
        result = self.export()

        feed = self.feed()
        self.assertEqual(
            {"status": "exported", "output": str(self.output.resolve()), "counts": feed["counts"]},
            result,
        )
        self.assertEqual(feed, validate_contract(feed))
        self.assertEqual(
            {"concepts": 2, "sources": 1, "semantic_edges": 1, "definitions": 2, "references": 1},
            feed["counts"],
        )
        self.assertEqual(
            {"id", "label", "authority", "curation_status", "aliases"},
            set(feed["concepts"][0]),
        )
        self.assertEqual([{"authority": "notes/chapter.md"}], feed["sources"])
        self.assertEqual(
            {
                "source": "sigma-algebra",
                "relation": "prerequisite-for",
                "target": "measure",
                "evidence": "A measure is defined on a sigma algebra.",
            },
            feed["semantic_edges"][0],
        )
        self.assertEqual("sigma-algebra", feed["references"][0]["target"])
        self.assertEqual(
            [path.name for path in self.output.parent.iterdir()],
            ["semantic-graph.json"],
        )

    def test_needs_review_edges_are_left_out(self) -> None:
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8").replace(
                "complement and countable union", "complements and countable unions"
            ),
            encoding="utf-8",
        )
        self.sync()

        self.export()

        self.assertEqual([], self.feed()["semantic_edges"])
        self.assertEqual(2, self.feed()["counts"]["concepts"])

    def test_output_must_be_a_json_file_outside_authorities_and_obsidian(self) -> None:
        cases = (
            (self.repo / ".knowledge/build/obsidian/graph.txt", "must be a .json file"),
            (self.repo / "notes/graph.json", "overlaps registered authority"),
            (self.repo / ".obsidian/graph.json", "cannot be the project root or .obsidian"),
        )
        for output, message in cases:
            with self.subTest(output=output), self.assertRaisesRegex(ObsidianExportError, message):
                self.export(output)
            self.assertFalse(output.exists())

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

    def test_unsynchronized_authorities_and_registries_keep_the_previous_feed(self) -> None:
        self.export()
        baseline = self.output.read_bytes()
        original_authority = self.authority.read_bytes()
        original_registry = self.registry.read_bytes()

        def assert_rejected(message: str) -> None:
            with self.assertRaisesRegex(ObsidianExportError, message):
                self.export()
            self.assertEqual(baseline, self.output.read_bytes())

        with self.subTest(change="modified authority"):
            self.authority.write_text(
                self.authority.read_text(encoding="utf-8") + "\nunsynchronized\n",
                encoding="utf-8",
            )
            assert_rejected("out of sync")
            self.authority.write_bytes(original_authority)

        with self.subTest(change="added authority"):
            added = self.repo / "notes/added.md"
            added.write_text("> **Definition: --[[Added]]--**\n", encoding="utf-8")
            assert_rejected("out of sync")
            added.unlink()

        with self.subTest(change="source registry"):
            registry = json.loads(original_registry)
            registry["sources"][0]["files"] = ["**/*.md"]
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            assert_rejected("source registry is out of sync")
            self.registry.write_bytes(original_registry)

        with self.subTest(change="identity registry"):
            self.identities.write_text(
                json.dumps(
                    {
                        "schema": "kgdistiller-identities-v1",
                        "identities": [
                            {"id": "unsynchronized", "canonical_name": "Unsynchronized", "aliases": []}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            assert_rejected("identity registry is out of sync")
            self.identities.unlink()

    def test_authority_change_during_build_fails_before_writing(self) -> None:
        self.export()
        baseline = self.output.read_bytes()
        original_build = obsidian_export._build_plugin_graph

        def build_then_change(**kwargs: object) -> dict:
            graph = original_build(**kwargs)
            self.authority.write_text(
                self.authority.read_text(encoding="utf-8") + "\nchanged mid-build\n",
                encoding="utf-8",
            )
            return graph

        with (
            patch.object(obsidian_export, "_build_plugin_graph", build_then_change),
            self.assertRaisesRegex(ObsidianExportError, "out of sync"),
        ):
            self.export()
        self.assertEqual(baseline, self.output.read_bytes())

    def test_interrupted_write_keeps_the_previous_feed(self) -> None:
        self.export()
        baseline = self.output.read_bytes()
        with (
            patch("kgdistiller.cli.os.replace", side_effect=OSError("interrupted")),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            self.export()
        self.assertEqual(baseline, self.output.read_bytes())
        self.assertEqual(["semantic-graph.json"], os.listdir(self.output.parent))


if __name__ == "__main__":
    unittest.main()
