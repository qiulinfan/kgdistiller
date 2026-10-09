from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.cli import (
    GraphState,
    KnowledgeError,
    apply_delta,
    jsonl,
    load_state,
    make_agent_snapshot,
    make_artifacts,
    pretty_json,
    sha256_text,
    synchronize,
    write_artifacts,
)
from kgdistiller.project import initialize_project


class CompactGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgd-compact-")
        self.root = Path(self.temporary.name).resolve()
        self.registry = self.root / "knowledge/sources.json"
        self.graph = self.root / "knowledge/graph"
        self.typst = self.root / "knowledge/build/registry.typ"
        initialize_project(self.root, self.registry, source_root=Path("notes"))
        (self.root / "notes/chapter.md").write_text(
            "--[[Alpha]]--\nAlpha is the first object.\n\n--[[Beta]]--\nBeta uses [[Alpha]].\n",
            encoding="utf-8",
        )
        self.sync()
        self.delta({
            "nodes": [{"id": "alpha", "entry": {
                "summary": "The curated first object.",
                "context": "An independently reviewed explanation.",
                "understanding": "not-yet-understood",
                "pending_prerequisites": ["A direct prerequisite"],
            }}, {"id": "beta", "text": "The curated second object."}],
            "edges": [{"source": "alpha", "relation": "prerequisite-for", "target": "beta",
                       "evidence": "The Beta definition uses Alpha."}],
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self):
        return synchronize(
            self.root, self.registry, self.graph, self.typst,
            files=[], course=None, subject=None, write=True,
        )

    def delta(self, payload: dict) -> None:
        path = self.root / "delta.json"
        path.write_text(json.dumps({"schema": "kgdistiller-agent-delta-v1", **payload}), encoding="utf-8")
        apply_delta(self.graph, self.typst, path, repo_root=self.root)

    def test_only_markdown_contains_reviewed_body_and_read_preserves_status(self) -> None:
        state = load_state(self.graph)
        body = "The curated first object."
        self.assertEqual(body, state.nodes["alpha"]["text"])
        self.assertEqual(body, state.nodes["alpha"]["entry"]["summary"])
        for path in self.graph.rglob("*"):
            if path.is_file():
                self.assertNotIn(body, path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"manifest.json", "nodes.jsonl", "edges.jsonl", "references.jsonl"},
            {path.name for path in self.graph.iterdir()},
        )
        for key in ("entry_store", "node_types", "relations", "statuses", "curation_statuses", "knowledge_origins"):
            self.assertNotIn(key, state.manifest)
        old_properties = copy.deepcopy(state.nodes["alpha"]["properties"])
        source = self.root / "notes/chapter.md"
        source.write_text(source.read_text().replace("first object.", "a changed object."), encoding="utf-8")
        self.assertEqual(old_properties, load_state(self.graph).nodes["alpha"]["properties"])
        self.sync()
        self.assertEqual("needs-review", load_state(self.graph).nodes["alpha"]["properties"]["curation_status"])

    def test_readonly_edit_rejected_then_sync_accepts_it(self) -> None:
        entry = self.root / "knowledge/entries/alpha.md"
        entry.write_text(entry.read_text().replace("The curated first object.", "An edited first object."), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "out of sync"):
            load_state(self.graph)
        self.sync()
        state = load_state(self.graph)
        self.assertEqual("An edited first object.", state.nodes["alpha"]["text"])
        self.assertEqual("not-yet-understood", state.nodes["alpha"]["entry"]["understanding"])
        make_agent_snapshot(state)
        state.nodes["alpha"]["text"] = "Unaccepted in-memory edit"
        with self.assertRaisesRegex(KnowledgeError, "graph digest"):
            make_agent_snapshot(state)

    def test_partial_delta_preserves_edited_entry_fields(self) -> None:
        entry = self.root / "knowledge/entries/alpha.md"
        entry.write_text(entry.read_text().replace("An independently reviewed explanation.", "My revised context."), encoding="utf-8")
        self.delta({"nodes": [{"id": "alpha", "text": "Updated summary."}]})
        state = load_state(self.graph)
        self.assertEqual("My revised context.", state.nodes["alpha"]["entry"]["context"])
        self.assertEqual("not-yet-understood", state.nodes["alpha"]["entry"]["understanding"])

    def test_custom_graph_requires_explicit_root_and_rejects_path_escape(self) -> None:
        custom = self.root / "custom/compiled"
        shutil.copytree(self.graph, custom)
        with self.assertRaisesRegex(KnowledgeError, "explicit repo_root"):
            load_state(custom)
        self.assertEqual(load_state(self.graph).nodes, load_state(custom, repo_root=self.root).nodes)
        for arguments in (["get", "alpha"], ["search", "first"], ["context", "first"]):
            result = subprocess.run(
                [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root),
                 "--graph", str(custom), "agent", *arguments],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("alpha", result.stdout)
        manifest = json.loads((custom / "manifest.json").read_text())
        manifest["entry_authorities"]["entries"][0]["path"] = "../outside.md"
        (custom / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "unsafe entry authority"):
            load_state(custom, repo_root=self.root)

    def test_compact_reader_rejects_a_second_scientific_body(self) -> None:
        path = self.graph / "nodes.jsonl"
        nodes = [json.loads(line) for line in path.read_text().splitlines()]
        next(node for node in nodes if node["id"] == "alpha")["text"] = "An unwanted second body"
        path.write_text(jsonl(nodes), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "duplicates Markdown content"):
            load_state(self.graph)

    def test_sync_accepts_explicit_entry_deletion(self) -> None:
        (self.root / "knowledge/entries/alpha.md").unlink()
        with self.assertRaisesRegex(KnowledgeError, "missing entry authority"):
            load_state(self.graph)
        self.sync()
        node = load_state(self.graph).nodes["alpha"]
        self.assertEqual("", node["text"])
        self.assertEqual("pending", node["properties"]["curation_status"])
        self.assertNotIn("entry", node)

    def test_non_entry_backed_content_remains_self_contained(self) -> None:
        graph = self.root / "standalone"
        state = GraphState({
            "field": {"id": "field", "type": "field", "label": "Field", "text": "An optional field.", "properties": {}},
            "concept": {"id": "concept", "type": "knowledge", "label": "Concept", "text": "Inline summary.",
                        "entry": {"summary": "Inline summary.", "context": "Inline context."},
                        "properties": {"source_status": "meta", "curation_status": "current"}},
        }, {}, [], {})
        write_artifacts(graph, make_artifacts(state, {}))
        serialized = (graph / "nodes.jsonl").read_text()
        self.assertEqual(1, serialized.count("Inline summary."))
        loaded = load_state(graph)
        self.assertEqual("Inline summary.", loaded.nodes["concept"]["text"])
        self.assertEqual("An optional field.", loaded.nodes["field"]["text"])
        make_agent_snapshot(loaded)

    def test_v1_read_and_explicit_rewrite_preserve_knowledge(self) -> None:
        expected = load_state(self.graph)
        # Historical layout fixture: two Markdown-backed bodies share one shard.
        old_nodes = []
        records = []
        shard_path = "entries/by-source/notes/chapter.md.jsonl"
        for original in sorted(expected.nodes.values(), key=lambda node: node["id"]):
            node = copy.deepcopy(original)
            text = node.pop("text", "")
            entry = node.pop("entry", None)
            if text or entry:
                node["properties"]["entry_path"] = shard_path
                record = {"id": node["id"], "text": text}
                if entry:
                    record["entry"] = entry
                records.append(record)
            old_nodes.append(node)
        shard = jsonl(records)
        nodes = jsonl(old_nodes)
        edges = jsonl(sorted(expected.edges.values(), key=lambda edge: (edge["source"], edge["relation"], edge["target"])))
        refs = jsonl(sorted(expected.references, key=lambda ref: (ref["authority"], ref["line"], ref["target"])))
        manifest = copy.deepcopy(expected.manifest)
        manifest["schema"] = "kgdistiller-graph-v1"
        manifest["entry_store"] = {"schema": "kgdistiller-entry-shards-v1", "entries": len(records), "shards": [
            {"path": shard_path, "count": len(records), "bytes": len(shard.encode()), "sha256": sha256_text(shard)}
        ]}
        suffix = "".join(record["path"] + record["sha256"]
                         for key in ("entry_authorities", "entry_sources")
                         for record in sorted(manifest[key]["entries"], key=lambda record: record["path"]))
        manifest["graph_sha256"] = sha256_text(nodes + edges + refs + shard_path + shard + suffix)
        for name, content in {"manifest.json": pretty_json(manifest), "nodes.jsonl": nodes, "edges.jsonl": edges,
                              "references.jsonl": refs, "diagnostics.json": "{}\n", shard_path: shard}.items():
            path = self.graph / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        unmanaged = self.graph / "entries/user-file.txt"
        unmanaged.write_text("Unmanaged content stays.", encoding="utf-8")
        legacy = load_state(self.graph)
        self.assertEqual("kgdistiller-graph-v1", make_agent_snapshot(legacy)["graph"]["schema"])
        self.sync()
        actual = load_state(self.graph)
        self.assertEqual(expected.nodes, actual.nodes)
        self.assertEqual(expected.edges, actual.edges)
        self.assertEqual(expected.references, actual.references)
        self.assertEqual("kgdistiller-graph-v2", actual.manifest["schema"])
        self.assertFalse((self.graph / shard_path).exists())
        self.assertFalse((self.graph / "diagnostics.json").exists())
        self.assertTrue(unmanaged.is_file())
        for node in actual.nodes.values():
            self.assertNotIn("source_kind", node["properties"])
        _, checked_artifacts, _ = synchronize(
            self.root, self.registry, self.graph, self.typst,
            files=[], course=None, subject=None, write=False,
        )
        for name, content in checked_artifacts.items():
            self.assertEqual(content, (self.graph / name).read_text(encoding="utf-8"))
        make_agent_snapshot(actual)
