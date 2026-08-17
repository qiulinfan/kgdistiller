from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src/kgdistiller/cli.py"
SPEC = importlib.util.spec_from_file_location("ql_knowledge", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
knowledge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = knowledge
SPEC.loader.exec_module(knowledge)

class KnowledgeGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-graph-v1-test-")
        self.repo = Path(self.temporary.name)
        self.source_root = self.repo / "notes/math/demo"
        self.chapter = self.source_root / "chapters/01-foundations.typ"
        self.chapter.parent.mkdir(parents=True)
        shutil.copyfile(
            REPO_ROOT / "tests/fixtures/roundtrip.typ",
            self.chapter,
        )
        self.derived_chapter = (
            self.repo
            / "knowledge/derived/by-source/notes/math/demo/chapters/01-foundations.typ.md"
        )
        self.derived_chapter.parent.mkdir(parents=True)
        self.derived_chapter.write_text(
            "# Derived fixture\n\nConverted from the Typst authority for entry evidence.\n",
            encoding="utf-8",
        )
        self.registry = self.repo / "knowledge/sources.json"
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-sources-v1",
                    "fields": [
                        {
                            "id": "analysis",
                            "label": "Analysis",
                            "text": "A broad analytic field.",
                        },
                        {
                            "id": "demo-methods",
                            "label": "Demo Methods",
                            "text": "A fixture-specific field.",
                        },
                        {
                            "id": "geometry",
                            "label": "Geometry",
                            "text": "",
                        },
                    ],
                    "sources": [
                        {
                            "id": "math:demo",
                            "subject": "math",
                            "course": "demo",
                            "knowledge_origin": "personal-note",
                            "fields": ["analysis"],
                            "root": "notes/math/demo",
                            "files": [
                                "chapters/*.typ",
                                "chapters/*.md",
                                "chapters/*.tex",
                                "appendix/*.md",
                            ],
                            "web": "https://example.test/demo",
                            "topics": [
                                {
                                    "glob": "chapters/*.typ",
                                    "id": "demo-foundations",
                                    "label": "Demo Foundations",
                                    "fields": ["demo-methods"],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.graph = self.repo / "knowledge/graph"
        self.identities = self.repo / "knowledge/identities.json"
        (self.repo / "knowledge/build").mkdir(parents=True, exist_ok=True)
        self.typst_registry = self.repo / "notes/math/toolchain/generated/knowledge-registry.typ"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self, *, files: list[Path] | None = None):
        return knowledge.synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            files=files or [],
            course=None,
            subject=None,
            write=True,
        )

    def test_only_explicit_kn_becomes_knowledge_and_ref_is_backlink(self) -> None:
        state, _, report = self.sync()
        self.assertEqual(2, report["definitions"])
        self.assertEqual(1, report["references"])
        self.assertEqual({"sigma-algebra", "measure-space"}, {
            node_id for node_id, node in state.nodes.items() if node["type"] == "knowledge"
        })
        self.assertNotIn("worked-example", state.nodes)
        self.assertNotIn("mathematics", state.nodes)
        self.assertEqual("field", state.nodes["analysis"]["type"])
        self.assertIn(("analysis", "contains", "demo-foundations"), state.edges)
        self.assertIn(("demo-methods", "contains", "demo-foundations"), state.edges)
        self.assertEqual("measure-space", state.references[0]["target"])
        self.assertIn(("demo-foundations", "contains", "sigma-algebra"), state.edges)
        self.assertEqual("sigma-algebra", knowledge.show_node(state, "σ-algebra")["node"]["id"])
        registry = self.typst_registry.read_text(encoding="utf-8")
        self.assertIn("name: [$sigma$-algebra]", registry)
        self.assertIn('id: "sigma-algebra"', registry)
        self.assertIn("<math>", state.nodes["sigma-algebra"]["properties"]["label_html"])

    def test_graph_entry_url_is_derived_from_registered_note_web(self) -> None:
        state = knowledge.GraphState(
            nodes={
                "known": {
                    "id": "known",
                    "provenance": {
                        "web": "https://example.test/custom/notes/math/demo/#kn-known"
                    },
                }
            },
            edges={},
            references=[],
            manifest={},
        )
        self.assertEqual(
            "https://example.test/custom/knowledge/#node=orphan",
            knowledge.graph_entry_url(state, "orphan"),
        )

    def test_artifacts_are_deterministic(self) -> None:
        _, first, _ = self.sync()
        _, second, report = self.sync()
        self.assertEqual(first, second)
        self.assertEqual({"nodes": 0, "edges": 0, "references": 0}, report["delta"])
        manifest = json.loads(first["manifest.json"])
        self.assertEqual("kgdistiller-graph-v1", manifest["schema"])
        self.assertRegex(manifest["graph_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            knowledge.source_registry_sha256(self.registry),
            manifest["registry_sha256"],
        )
        self.assertNotIn("generated_at", manifest)

    def test_graph_loader_rejects_existing_unknown_or_non_object_manifest(self) -> None:
        self.sync()
        manifest_path = self.graph / "manifest.json"
        original = manifest_path.read_bytes()

        legacy = json.loads(original)
        legacy["schema"] = "legacy-graph-v0"
        manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
        legacy_bytes = manifest_path.read_bytes()
        with self.assertRaisesRegex(knowledge.KnowledgeError, "expected kgdistiller-graph-v1"):
            self.sync()
        self.assertEqual(legacy_bytes, manifest_path.read_bytes())

        manifest_path.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "JSON object"):
            knowledge.load_state(self.graph)

        manifest_path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(
            knowledge.KnowledgeError, "invalid graph manifest JSON"
        ):
            knowledge.load_state(self.graph)

        manifest_path.unlink()
        empty = knowledge.load_state(self.graph)
        self.assertEqual({}, empty.manifest)
        self.assertEqual({}, empty.nodes)

        manifest_path.write_bytes(original)

    def test_unknown_source_identity_and_delta_contracts_are_rejected(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["schema"] = "legacy-sources-v0"
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "kgdistiller-sources-v1"):
            self.sync()

        registry["schema"] = "kgdistiller-sources-v1"
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        self.sync()

        self.identities.write_text(
            json.dumps({"schema": "legacy-identities-v0", "identities": []}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(knowledge.KnowledgeError, "kgdistiller-identities-v1"):
            self.sync()
        self.identities.unlink()

        legacy_delta = self.repo / "knowledge/build/legacy-delta.json"
        legacy_delta.write_text(
            json.dumps({"schema": "legacy-agent-delta-v0", "nodes": [], "edges": []}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(knowledge.KnowledgeError, "kgdistiller-agent-delta-v1"):
            knowledge.apply_delta(self.graph, self.typst_registry, legacy_delta)

    def test_source_hash_and_graph_check_are_crlf_portable(self) -> None:
        state, _, _ = self.sync()
        authority = self.chapter.relative_to(self.repo).as_posix()
        expected = state.manifest["source_hashes"][authority]
        registry_sha = state.manifest["registry_sha256"]

        registry_payload = json.loads(self.registry.read_text(encoding="utf-8"))
        registry_text = json.dumps(
            registry_payload, ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"
        self.registry.write_bytes(registry_text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(registry_sha, knowledge.source_registry_sha256(self.registry))

        canonical_text = self.chapter.read_text(encoding="utf-8")
        self.chapter.write_bytes(
            canonical_text.replace("\n", "\r\n").encode("utf-8")
        )
        self.assertEqual(expected, knowledge.sha256_authority_file(self.chapter))
        self.assertNotEqual(expected, knowledge.sha256_file(self.chapter))

        for path in sorted(self.graph.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        hydrated = knowledge.load_state(self.graph)
        self.assertEqual(
            state.manifest["graph_sha256"],
            knowledge.make_agent_snapshot(hydrated)["graph"]["sha256"],
        )
        _, artifacts, report = knowledge.synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            files=[],
            course=None,
            subject=None,
            write=False,
        )
        self.assertEqual([], report["source_changes"]["modified"])
        stale = [
            name
            for name, content in artifacts.items()
            if not (self.graph / name).is_file()
            or (self.graph / name).read_text(encoding="utf-8") != content
        ]
        self.assertEqual([], stale)

    def test_registry_generation_binding_survives_delta_and_snapshot_recomputation(self) -> None:
        state, _, _ = self.sync()
        registry_sha = knowledge.source_registry_sha256(self.registry)
        self.assertEqual(registry_sha, state.manifest["registry_sha256"])

        delta = self.repo / "knowledge/build/no-op-generation.json"
        delta.write_text(
            json.dumps({"schema": "kgdistiller-agent-delta-v1", "nodes": [], "edges": []}),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)

        hydrated = knowledge.load_state(self.graph)
        self.assertEqual(registry_sha, hydrated.manifest["registry_sha256"])
        self.assertEqual(
            hydrated.manifest["graph_sha256"],
            knowledge.make_agent_snapshot(hydrated)["graph"]["sha256"],
        )

    def test_explicit_file_scope_must_match_a_bounded_source_pattern(self) -> None:
        excluded = self.source_root / "README.typ"
        excluded.write_text("#kn[Must not be ingested]", encoding="utf-8")

        with self.assertRaisesRegex(
            knowledge.KnowledgeError,
            "not admitted by its file patterns",
        ):
            self.sync(files=[excluded.relative_to(self.repo)])

    def test_overlapping_source_patterns_are_rejected(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        duplicate = dict(payload["sources"][0])
        duplicate["id"] = "math:duplicate"
        payload["sources"].append(duplicate)
        self.registry.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(
            knowledge.KnowledgeError,
            "matches multiple registry sources",
        ):
            self.sync()

    def test_source_root_must_remain_inside_repository_after_resolution(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        outside = self.repo.parent / f"{self.repo.name}-outside"
        outside.mkdir()
        try:
            payload["sources"][0]["root"] = "../" + outside.name
            self.registry.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(knowledge.KnowledgeError, "portable relative path"):
                self.sync()
        finally:
            outside.rmdir()

    def test_source_root_must_be_a_portable_lexical_relative_path(self) -> None:
        original = json.loads(self.registry.read_text(encoding="utf-8"))
        for root in (str(self.source_root), "notes/../notes"):
            with self.subTest(root=root):
                payload = copy.deepcopy(original)
                payload["sources"][0]["root"] = root
                self.registry.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(
                    knowledge.KnowledgeError, "portable relative path"
                ):
                    self.sync()

    def test_symlinked_source_root_inside_repository_is_not_portable(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        link = self.repo / "linked-notes"
        try:
            try:
                link.symlink_to(self.source_root, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("directory symlink creation is unavailable")
            payload["sources"][0]["root"] = "linked-notes"
            self.registry.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(knowledge.KnowledgeError, "must not traverse"):
                self.sync()
        finally:
            if link.is_symlink():
                link.unlink()

    def test_symlinked_source_root_cannot_escape_repository(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        outside = self.repo.parent / f"{self.repo.name}-symlink-target"
        outside.mkdir()
        link = self.repo / "notes/escaped-root"
        try:
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("directory symlink creation is unavailable")
            payload["sources"][0]["root"] = "notes/escaped-root"
            self.registry.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(knowledge.KnowledgeError, "escapes repository"):
                self.sync()
        finally:
            if link.is_symlink():
                link.unlink()
            outside.rmdir()

    def test_glob_matching_uses_segments_and_double_star(self) -> None:
        self.assertTrue(
            knowledge.glob_matches_path(Path("chapters/a.typ"), "chapters/*.typ")
        )
        self.assertFalse(
            knowledge.glob_matches_path(
                Path("chapters/nested/a.typ"), "chapters/*.typ"
            )
        )
        self.assertTrue(knowledge.glob_matches_path(Path("a.md"), "**/*.md"))
        self.assertTrue(
            knowledge.glob_matches_path(Path("nested/a.md"), "**/*.md")
        )

    def test_registry_node_id_over_output_limit_is_rejected(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        payload["fields"][0]["id"] = "a" * 257
        self.registry.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(knowledge.KnowledgeError, "invalid field id"):
            self.sync()

    def test_scanned_node_label_over_output_limit_is_rejected(self) -> None:
        oversized = self.source_root / "chapters/oversized.md"
        oversized.write_text(
            "> **Definition: --[[" + ("L" * 1025) + "]]--**\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(knowledge.KnowledgeError, "at most 1024"):
            self.sync()

    def test_delta_node_output_bounds_are_rejected(self) -> None:
        self.sync()
        cases = (
            ({"id": "a" * 257, "label": "Too long an ID"}, "invalid delta node id"),
            ({"id": "sigma-algebra", "label": "L" * 1025}, "at most 1024"),
        )
        for index, (node, message) in enumerate(cases):
            with self.subTest(message=message):
                delta = self.repo / f"knowledge/build/output-bound-{index}.json"
                delta.write_text(
                    json.dumps({"schema": "kgdistiller-agent-delta-v1", "nodes": [node]}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(knowledge.KnowledgeError, message):
                    knowledge.apply_delta(self.graph, self.typst_registry, delta)

    def test_agent_snapshot_is_self_contained_and_deterministic(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/snapshot-entry.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "sigma-algebra",
                            "entry": {
                                "summary": "A collection closed under complements and countable unions.",
                                "sources": ["01-foundations.typ:3"],
                            },
                        }
                    ],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure-space",
                            "confidence": "high",
                            "evidence": "The measure-space definition uses a sigma-algebra.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        state = knowledge.load_state(self.graph)

        first = knowledge.make_agent_snapshot(state, "paper:fixture")
        second = knowledge.make_agent_snapshot(state, "paper:fixture")

        self.assertEqual(first, second)
        self.assertEqual("kgdistiller-agent-snapshot-v1", first["schema"])
        self.assertEqual("paper:fixture", first["namespace"])
        self.assertEqual(state.manifest["graph_sha256"], first["graph"]["sha256"])
        self.assertEqual(len(state.nodes), first["graph"]["counts"]["nodes"])
        self.assertEqual(len(state.edges), first["graph"]["counts"]["edges"])
        self.assertEqual(len(state.references), first["graph"]["counts"]["references"])
        self.assertEqual([], first["diagnostics"]["errors"])
        sigma = next(node for node in first["nodes"] if node["id"] == "sigma-algebra")
        self.assertEqual(
            "A collection closed under complements and countable unions.",
            sigma["entry"]["summary"],
        )
        semantic_edge = next(
            edge
            for edge in first["edges"]
            if edge["relation"] == "prerequisite-for"
        )
        self.assertEqual(
            "The measure-space definition uses a sigma-algebra.",
            semantic_edge["evidence"],
        )
        digest_payload = dict(first)
        digest = digest_payload.pop("snapshot_sha256")
        self.assertEqual(
            knowledge.sha256_text(knowledge.json_text(digest_payload)),
            digest,
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_agent_snapshot_rejects_invalid_namespace_counts_and_graph(self) -> None:
        self.sync()
        state = knowledge.load_state(self.graph)
        with self.assertRaisesRegex(knowledge.KnowledgeError, "invalid Agent snapshot namespace"):
            knowledge.make_agent_snapshot(state, "Paper With Spaces")

        state.manifest["counts"]["nodes"] += 1
        with self.assertRaisesRegex(knowledge.KnowledgeError, "manifest counts"):
            knowledge.make_agent_snapshot(state)
        state.manifest["counts"]["nodes"] -= 1

        original_label = state.nodes["sigma-algebra"]["label"]
        state.nodes["sigma-algebra"]["label"] = "Uncommitted label"
        with self.assertRaisesRegex(knowledge.KnowledgeError, "graph digest"):
            knowledge.make_agent_snapshot(state)
        state.nodes["sigma-algebra"]["label"] = original_label

        state.edges[("missing", "implies", "measure-space")] = {
            "source": "missing",
            "relation": "implies",
            "target": "measure-space",
            "origin": "agent",
            "confidence": "high",
            "evidence": "Invalid fixture edge.",
        }
        with self.assertRaisesRegex(knowledge.KnowledgeError, "cannot export invalid authority graph"):
            knowledge.make_agent_snapshot(state)

    def test_snapshot_command_writes_machine_readable_file(self) -> None:
        self.sync()
        output = self.repo / "knowledge/build/agent/snapshot.json"
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--repo-root",
                str(self.repo),
                "snapshot",
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        snapshot = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("kgdistiller-agent-snapshot-v1", report["schema"])
        self.assertEqual(snapshot["snapshot_sha256"], report["snapshot_sha256"])
        self.assertEqual("personal", snapshot["namespace"])

    def test_structured_cli_json_is_safe_on_an_ascii_console(self) -> None:
        self.sync()
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "ascii:strict"
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--repo-root",
                str(self.repo),
                "show",
                "sigma-algebra",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(r"\u03c3", result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("σ-algebra", payload["node"]["label"])
        self.assertIn("𝜎", payload["node"]["properties"]["label_html"])

    def test_entries_are_sharded_by_authority_and_hydrated_on_load(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/structured-entry.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "sigma-algebra",
                            "entry": {
                                "summary": "A family of sets closed under the defining operations.",
                                "context": "Used as the measurable event system.",
                                "sources": ["01-foundations.typ:3"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        knowledge.apply_delta(self.graph, self.typst_registry, delta)

        manifest = json.loads((self.graph / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("kgdistiller-entry-shards-v1", manifest["entry_store"]["schema"])
        self.assertEqual(3, manifest["entry_store"]["entries"])
        shard = next(
            item
            for item in manifest["entry_store"]["shards"]
            if item["path"].startswith("entries/by-source/")
        )
        self.assertIn("entries/by-source/notes/math/demo/chapters/01-foundations.typ.jsonl", shard["path"])
        serialized = next(
            json.loads(line)
            for line in (self.graph / "nodes.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["id"] == "sigma-algebra"
        )
        self.assertNotIn("text", serialized)
        self.assertEqual(shard["path"], serialized["properties"]["entry_path"])
        hydrated = knowledge.load_state(self.graph).nodes["sigma-algebra"]
        self.assertEqual("A family of sets closed under the defining operations.", hydrated["text"])
        self.assertEqual("Used as the measurable event system.", hydrated["entry"]["context"])

    def test_source_marks_research_nodes_for_square_rendering(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        payload["sources"][0]["knowledge_origin"] = "research"
        self.registry.write_text(json.dumps(payload), encoding="utf-8")

        state, _, _ = self.sync()

        self.assertEqual("research", state.nodes["sigma-algebra"]["properties"]["knowledge_origin"])

    def test_agent_can_add_node_specific_cross_field_membership(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/cross-field.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "sigma-algebra",
                            "properties": {"additional_fields": ["geometry"]},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)

        state, _, _ = self.sync(
            files=[Path("notes/math/demo/chapters/01-foundations.typ")]
        )

        self.assertEqual(
            ["analysis", "demo-methods", "geometry"],
            state.nodes["sigma-algebra"]["properties"]["fields"],
        )
        self.assertIn(("geometry", "contains", "sigma-algebra"), state.edges)

    def test_changed_file_orphans_without_erasing_meta_or_edges_then_rehomes(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/delta.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "sigma-algebra",
                            "type": "knowledge",
                            "text": "Durable agent summary.",
                            "properties": {"reviewed": True},
                        }
                    ],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure-space",
                            "evidence": "measure spaces are built on sigma-algebras",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)

        self.chapter.write_text(
            self.chapter.read_text(encoding="utf-8").replace(
                "#kn[$sigma$-algebra]", "σ-algebra"
            ),
            encoding="utf-8",
        )
        state, _, report = self.sync(files=[Path("notes/math/demo/chapters/01-foundations.typ")])
        node = state.nodes["sigma-algebra"]
        self.assertEqual(["sigma-algebra"], report["orphaned"])
        self.assertEqual("orphaned", node["properties"]["source_status"])
        self.assertEqual("Durable agent summary.", node["text"])
        self.assertTrue(node["properties"]["reviewed"])
        self.assertIn(("sigma-algebra", "prerequisite-for", "measure-space"), state.edges)

        new_chapter = self.source_root / "chapters/02-rehomed.typ"
        new_chapter.write_text(
            "= Rehomed\n#definition(title: [#kn[$sigma$-algebra]])[New authority.]\n",
            encoding="utf-8",
        )
        state, _, _ = self.sync(files=[Path("notes/math/demo/chapters/02-rehomed.typ")])
        node = state.nodes["sigma-algebra"]
        self.assertEqual("active", node["properties"]["source_status"])
        self.assertEqual("notes/math/demo/chapters/02-rehomed.typ", node["provenance"]["authority"])
        self.assertEqual("Durable agent summary.", node["text"])
        self.assertIn(("sigma-algebra", "prerequisite-for", "measure-space"), state.edges)

    def test_git_rename_in_incremental_scope_rehomes_nodes_and_references(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "kgdistiller tests"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "notes", "knowledge/sources.json"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial authority"], cwd=self.repo, check=True)
        self.sync()

        entry = self.repo / "knowledge/build/rename-entry.json"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [{"id": "sigma-algebra", "text": "Durable entry."}],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, entry)

        renamed = self.chapter.with_name("02-重定位.typ")
        subprocess.run(
            ["git", "mv", str(self.chapter.relative_to(self.repo)), str(renamed.relative_to(self.repo))],
            cwd=self.repo,
            check=True,
        )
        state, _, report = self.sync(
            files=[Path("notes/math/demo/chapters/02-重定位.typ")]
        )

        self.assertEqual(
            "notes/math/demo/chapters/02-重定位.typ",
            state.nodes["sigma-algebra"]["provenance"]["authority"],
        )
        self.assertEqual("active", state.nodes["sigma-algebra"]["properties"]["source_status"])
        self.assertEqual("Durable entry.", state.nodes["sigma-algebra"]["text"])
        self.assertEqual(
            "notes/math/demo/chapters/02-重定位.typ",
            state.references[0]["authority"],
        )
        self.assertEqual(
            ["notes/math/demo/chapters/01-foundations.typ"],
            report["source_changes"]["deleted"],
        )
        self.assertEqual(
            ["notes/math/demo/chapters/02-重定位.typ"],
            report["source_changes"]["added"],
        )
        self.assertFalse(
            self.graph.joinpath(
                "entries/by-source/notes/math/demo/chapters/01-foundations.typ.jsonl"
            ).exists()
        )

    def test_git_machine_output_decode_failures_are_structured(self) -> None:
        specs = knowledge.load_sources(self.repo, self.registry)
        for invalid in (b"\xff", "not-bytes"):
            completed = subprocess.CompletedProcess([], 0, invalid, b"")
            stderr = io.StringIO()
            with (
                patch.object(knowledge.subprocess, "run", return_value=completed) as run,
                redirect_stderr(stderr),
                self.assertRaisesRegex(
                    knowledge.KnowledgeError, "machine output is not"
                ),
            ):
                knowledge.git_source_context(self.repo, None, specs)
            self.assertEqual("", stderr.getvalue())
            self.assertIs(run.call_args.kwargs["text"], False)

    def test_git_name_status_truncation_fails_closed(self) -> None:
        specs = knowledge.load_sources(self.repo, self.registry)
        head = subprocess.CompletedProcess([], 0, b"a" * 40 + b"\n", b"")
        status = subprocess.CompletedProcess([], 0, b"", b"")
        for malformed in (b"R100\0old\0", b"M\0"):
            diff = subprocess.CompletedProcess([], 0, malformed, b"")
            with (
                patch.object(
                    knowledge.subprocess,
                    "run",
                    side_effect=[head, status, diff],
                ),
                self.assertRaisesRegex(knowledge.KnowledgeError, "truncated"),
            ):
                knowledge.git_source_context(self.repo, "b" * 40, specs)

    def test_typst_success_with_missing_or_invalid_output_is_structured(self) -> None:
        def state() -> object:
            return knowledge.GraphState(
                nodes={
                    "measurable-space": {
                        "id": "measurable-space",
                        "type": "knowledge",
                        "properties": {"typst_name": "$cal(M)$"},
                    }
                },
                edges={},
                references=[],
                manifest={},
            )

        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with (
            patch.object(knowledge.subprocess, "run", return_value=completed),
            self.assertRaisesRegex(
                knowledge.KnowledgeError, "cannot read rendered knowledge labels"
            ),
        ):
            knowledge.render_typst_labels(state())

        def write_invalid(
            arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            Path(arguments[-1]).write_bytes(b"\xff")
            return completed

        with (
            patch.object(knowledge.subprocess, "run", side_effect=write_invalid),
            self.assertRaisesRegex(knowledge.KnowledgeError, "not valid UTF-8"),
        ):
            knowledge.render_typst_labels(state())

    def test_definition_change_marks_entries_and_edges_for_review(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/reviewed.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "sigma-algebra", "text": "Reviewed sigma entry."},
                        {"id": "measure-space", "text": "Reviewed measure entry."},
                    ],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure-space",
                            "evidence": "A measure space uses a sigma-algebra.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        self.chapter.write_text(
            self.chapter.read_text(encoding="utf-8").replace(
                "A family of sets closed under complement and countable union.",
                "A revised family of sets closed under complements and countable unions.",
            ),
            encoding="utf-8",
        )

        state, _, report = self.sync(
            files=[Path("notes/math/demo/chapters/01-foundations.typ")]
        )

        self.assertEqual("needs-review", state.nodes["sigma-algebra"]["properties"]["curation_status"])
        self.assertEqual("current", state.nodes["measure-space"]["properties"]["curation_status"])
        edge = state.edges[("sigma-algebra", "prerequisite-for", "measure-space")]
        self.assertEqual("needs-review", edge["curation_status"])
        self.assertEqual(["sigma-algebra"], edge["stale_endpoints"])
        self.assertEqual({"nodes": 1, "edges": 1}, report["needs_review"])

        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        refreshed = knowledge.load_state(self.graph)
        self.assertEqual("current", refreshed.nodes["sigma-algebra"]["properties"]["curation_status"])
        self.assertEqual(
            "current",
            refreshed.edges[("sigma-algebra", "prerequisite-for", "measure-space")]["curation_status"],
        )

    def test_ref_only_change_does_not_stale_node_curation(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/ref-only.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "sigma-algebra", "text": "Reviewed sigma entry."},
                        {"id": "measure-space", "text": "Reviewed measure entry."},
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        self.chapter.write_text(
            self.chapter.read_text(encoding="utf-8").replace(
                "Later we use #ref[measure space].\n", ""
            ),
            encoding="utf-8",
        )

        state, _, _ = self.sync(
            files=[Path("notes/math/demo/chapters/01-foundations.typ")]
        )

        self.assertEqual([], state.references)
        self.assertEqual("current", state.nodes["sigma-algebra"]["properties"]["curation_status"])
        self.assertEqual("current", state.nodes["measure-space"]["properties"]["curation_status"])

    def test_explicit_name_reconciliation_preserves_stable_node_id(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/name-entry.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [{"id": "sigma-algebra", "text": "Reviewed identity entry."}],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        state = knowledge.load_state(self.graph)
        result = knowledge.reconcile_node_name(
            state, self.identities, "sigma-algebra", "sigma field"
        )
        self.assertEqual("sigma-algebra", result["id"])
        self.chapter.write_text(
            self.chapter.read_text(encoding="utf-8").replace(
                "#kn[$sigma$-algebra]", "#kn[sigma field]"
            ),
            encoding="utf-8",
        )

        state, _, _ = self.sync(
            files=[Path("notes/math/demo/chapters/01-foundations.typ")]
        )

        self.assertIn("sigma-algebra", state.nodes)
        self.assertNotIn("sigma-field", state.nodes)
        self.assertEqual("σ field", state.nodes["sigma-algebra"]["label"])
        self.assertIn("σ-algebra", state.nodes["sigma-algebra"]["properties"]["aliases"])
        self.assertEqual("Reviewed identity entry.", state.nodes["sigma-algebra"]["text"])
        self.assertEqual("needs-review", state.nodes["sigma-algebra"]["properties"]["curation_status"])

    def test_multiple_explicit_kn_markers_in_one_title_create_multiple_nodes(self) -> None:
        self.chapter.write_text(
            "= Foundations\n"
            "#definition(title: [#kn[norm] and #kn[seminorm]])[Two related definitions.]\n",
            encoding="utf-8",
        )

        state, _, report = self.sync()

        self.assertEqual(2, report["definitions"])
        self.assertEqual(
            {"norm", "seminorm"},
            {node_id for node_id, node in state.nodes.items() if node["type"] == "knowledge"},
        )
        self.assertEqual(
            state.nodes["norm"]["provenance"]["line"],
            state.nodes["seminorm"]["provenance"]["line"],
        )

    def test_typst_registry_includes_authored_reference_spellings(self) -> None:
        self.chapter.write_text(
            "#definition(title: [#kn[concept #strong[one,\ntwo]]])[Authority.]\n"
            "By #ref[concept #strong[one, two]], continue.\n",
            encoding="utf-8",
        )

        self.sync()

        registry = self.typst_registry.read_text(encoding="utf-8")
        self.assertIn("names: (", registry)
        self.assertIn("[concept #strong[one,\ntwo]]", registry)
        self.assertIn("[concept #strong[one, two]]", registry)

    def test_mixed_markdown_and_latex_sources_share_one_graph(self) -> None:
        markdown = self.source_root / "chapters/02-cache.md"
        markdown.write_text(
            "# Cache\n\n"
            "> **Definition: --[[cache line]]--**\n>\n"
            "> A cache line is the transfer unit. It may depend on [[σ-algebra]].\n\n"
            "An ordinary [[cache line|line]] occurrence is a backlink, not a definition.\n",
            encoding="utf-8",
        )
        latex = self.source_root / "chapters/03-cache.tex"
        latex.write_text(
            "\\begin{theorem}\n"
            "\\kn{cache locality theorem}\n"
            "By \\knref{cache line}, nearby accesses are cheaper.\n"
            "\\end{theorem}\n",
            encoding="utf-8",
        )

        state, _, report = self.sync()

        self.assertEqual(4, report["definitions"])
        self.assertEqual(4, report["references"])
        self.assertEqual("markdown", state.nodes["cache-line"]["properties"]["source_format"])
        self.assertEqual("latex", state.nodes["cache-locality-theorem"]["properties"]["source_format"])
        self.assertNotIn("typst_name", state.nodes["cache-line"]["properties"])
        self.assertEqual(
            "https://example.test/demo/chapters/02-cache/#kn-cache-line",
            state.nodes["cache-line"]["provenance"]["web"],
        )
        cache_refs = [item for item in state.references if item["target"] == "cache-line"]
        self.assertEqual(2, len(cache_refs))
        self.assertEqual({"markdown", "latex"}, {item["source_format"] for item in cache_refs})
        registry = self.typst_registry.read_text(encoding="utf-8")
        self.assertIn('name: [#text("cache line")]', registry)

    def test_same_stem_mixed_sources_use_distinct_entry_shards(self) -> None:
        markdown = self.source_root / "chapters/same.md"
        markdown.write_text("--[[markdown authority]]--\n", encoding="utf-8")
        latex = self.source_root / "chapters/same.tex"
        latex.write_text("\\kn{latex authority}\n", encoding="utf-8")
        derived_latex = (
            self.repo / "knowledge/derived/by-source/notes/math/demo/chapters/same.tex.md"
        )
        derived_latex.parent.mkdir(parents=True, exist_ok=True)
        derived_latex.write_text("# Derived LaTeX fixture\n", encoding="utf-8")
        self.sync()
        delta = self.repo / "knowledge/build/same-stem-entries.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "markdown-authority", "text": "Markdown entry."},
                        {"id": "latex-authority", "text": "LaTeX entry."},
                    ],
                }
            ),
            encoding="utf-8",
        )

        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        state = knowledge.load_state(self.graph)

        markdown_path = state.nodes["markdown-authority"]["properties"]["entry_path"]
        latex_path = state.nodes["latex-authority"]["properties"]["entry_path"]
        self.assertTrue(markdown_path.endswith("same.md.jsonl"))
        self.assertTrue(latex_path.endswith("same.tex.jsonl"))
        self.assertNotEqual(markdown_path, latex_path)

    def test_markdown_requires_explicit_authority_dashes(self) -> None:
        markdown = self.source_root / "chapters/02-links.md"
        markdown.write_text(
            "# Links\n\n[[new reference]] and --[[canonical concept]]--.\n",
            encoding="utf-8",
        )

        state, _, report = self.sync()

        self.assertEqual(3, report["definitions"])
        self.assertNotIn("new-reference", state.nodes)
        self.assertIn("canonical-concept", state.nodes)
        self.assertEqual("new-reference", next(
            item["target"] for item in state.references if item["label"] == "new reference"
        ))

    def test_markdown_publish_syncs_then_blocks_missing_agent_entry(self) -> None:
        self.sync()
        markdown = self.source_root / "chapters/02-publication.md"
        markdown.write_text(
            "# Publication\n\n> **Definition: --[[publication concept]]--**\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(MODULE_PATH),
            "--repo-root",
            str(self.repo),
            "publish",
            "--format",
            "markdown",
        ]

        blocked = subprocess.run(command, check=False, capture_output=True, text=True)

        self.assertEqual(1, blocked.returncode)
        report = json.loads(blocked.stdout)
        self.assertEqual(1, report["synchronized_files"])
        self.assertEqual(["missing-node-entry"], [item["code"] for item in report["errors"]])
        self.assertIn("publication-concept", knowledge.load_state(self.graph).nodes)

        delta = self.repo / "knowledge/build/publication-entry.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "publication-concept",
                            "text": "A source-grounded entry prepared before publication.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)

        ready = subprocess.run(command, check=False, capture_output=True, text=True)

        self.assertEqual(0, ready.returncode, ready.stderr or ready.stdout)
        self.assertEqual([], json.loads(ready.stdout)["errors"])

    def test_markdown_escaped_double_brackets_are_literal_text(self) -> None:
        markdown = self.source_root / "chapters/02-escaped.md"
        markdown.write_text(
            "# Literal syntax\n\n"
            "\\--[[not an authority]]-- and \\[[not a reference]].\n",
            encoding="utf-8",
        )

        state, _, report = self.sync()

        self.assertEqual(2, report["definitions"])
        self.assertEqual(1, report["references"])
        self.assertNotIn("not-an-authority", state.nodes)
        self.assertFalse(any(item["label"].startswith("not a") for item in state.references))

    def test_directory_scope_expands_configured_mixed_sources_only(self) -> None:
        markdown = self.source_root / "chapters/02-cache.md"
        markdown.write_text("--[[cache line]]--\n", encoding="utf-8")
        latex = self.source_root / "chapters/03-locality.tex"
        latex.write_text("\\kn{locality theorem}\n", encoding="utf-8")
        outside = self.source_root / "appendix/01-outside.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("--[[outside concept]]--\n", encoding="utf-8")
        self.sync()

        outside.write_text("The authority marker was removed.\n", encoding="utf-8")
        state, _, report = self.sync(files=[Path("notes/math/demo/chapters")])

        self.assertEqual(4, report["definitions"])
        self.assertEqual("active", state.nodes["outside-concept"]["properties"]["source_status"])
        self.assertIn("cache-line", state.nodes)
        self.assertIn("locality-theorem", state.nodes)

    def test_global_audit_reports_topology_and_file_curation_coverage(self) -> None:
        state, _, _ = self.sync()
        report = knowledge.audit_report(state)

        self.assertEqual("kgdistiller-audit-v1", report["schema"])
        self.assertEqual(2, report["counts"]["active_knowledge"])
        self.assertEqual(0, report["counts"]["entries"])
        self.assertEqual(2, report["topology"]["isolated_nodes"])
        self.assertEqual(
            ["notes/math/demo/chapters/01-foundations.typ"],
            report["curation"]["pending_authorities"],
        )
        self.assertEqual([], report["quality"]["knowledge_nodes_without_field"])
        self.assertEqual(2, report["quality"]["knowledge_nodes_with_multiple_fields"])
        self.assertEqual({"2": 2}, report["topology"]["field_membership_histogram"])

        delta = self.repo / "knowledge/build/audit.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "sigma-algebra", "text": "Closed under the defining operations."},
                        {"id": "measure-space", "text": "A measurable space with a measure."},
                    ],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure-space",
                            "evidence": "a measure space is built on a sigma-algebra",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, delta)
        report = knowledge.audit_report(knowledge.load_state(self.graph))

        self.assertEqual(2, report["counts"]["entries"])
        self.assertEqual(0, report["topology"]["isolated_nodes"])
        self.assertEqual(2, report["topology"]["largest_component"])
        self.assertEqual(
            ["notes/math/demo/chapters/01-foundations.typ"],
            report["curation"]["complete_authorities"],
        )

    def test_duplicate_active_kn_is_rejected(self) -> None:
        duplicate = self.source_root / "chapters/02-duplicate.typ"
        duplicate.write_text(
            "= Duplicate\n#theorem(title: [#kn[$sigma$-algebra]])[No.]\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(knowledge.KnowledgeError, "global knowledge name"):
            self.sync()

    def test_semantic_cycle_is_rejected(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/cycle.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "edges": [
                        {"source": "sigma-algebra", "relation": "prerequisite-for", "target": "measure-space"},
                        {"source": "measure-space", "relation": "prerequisite-for", "target": "sigma-algebra"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(knowledge.KnowledgeError, "prerequisite-for cycle"):
            knowledge.apply_delta(self.graph, self.typst_registry, delta)

    def test_explicit_delta_can_remove_obsolete_meta_root(self) -> None:
        self.sync()
        state = knowledge.load_state(self.graph)
        state.nodes["obsolete-discipline"] = {
            "id": "obsolete-discipline",
            "type": "field",
            "label": "Obsolete Discipline",
            "text": "A removable meta root.",
            "properties": {"kind": "field", "source_status": "meta"},
        }
        state.edges[("obsolete-discipline", "contains", "demo-foundations")] = {
            "source": "obsolete-discipline",
            "relation": "contains",
            "target": "demo-foundations",
            "origin": "agent-taxonomy",
            "confidence": "high",
            "evidence": "legacy root",
        }
        knowledge.write_artifacts(
            self.graph,
            knowledge.make_artifacts(
                state,
                dict(state.manifest.get("source_hashes") or {}),
            ),
        )
        delta = self.repo / "knowledge/build/remove-root.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "remove_nodes": ["obsolete-discipline"],
                }
            ),
            encoding="utf-8",
        )

        report = knowledge.apply_delta(
            self.graph,
            self.typst_registry,
            delta,
        )

        self.assertEqual(1, report["nodes_removed"])
        state = knowledge.load_state(self.graph)
        self.assertNotIn("obsolete-discipline", state.nodes)
        self.assertFalse(any(
            edge.get("source") == "obsolete-discipline"
            or edge.get("target") == "obsolete-discipline"
            for edge in state.edges.values()
        ))

    def test_discipline_node_type_is_rejected(self) -> None:
        self.sync()
        delta = self.repo / "knowledge/build/discipline.json"
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "mathematics",
                            "type": "discipline",
                            "label": "Mathematics",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(knowledge.KnowledgeError, "unsupported node type"):
            knowledge.apply_delta(
                self.graph,
                self.typst_registry,
                delta,
            )

    def test_file_curation_requires_entries_and_cross_file_refs(self) -> None:
        state, _, _ = self.sync()
        authority = "notes/math/demo/chapters/01-foundations.typ"
        report = knowledge.curation_report(state, {authority})
        self.assertEqual(2, report["nodes"])
        self.assertEqual(0, report["entries"])
        self.assertEqual(
            ["missing-node-entry", "missing-node-entry"],
            [item["code"] for item in report["errors"]],
        )

        entries = self.repo / "knowledge/build/entries.json"
        entries.parent.mkdir(parents=True, exist_ok=True)
        entries.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "sigma-algebra", "text": "A family of sets closed under the required operations."},
                        {"id": "measure-space", "text": "A measurable space equipped with a measure."},
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, entries)

        application = self.source_root / "chapters/02-application.typ"
        application.write_text(
            "= Application\n#theorem(title: [#kn[completion theorem]])[A completion exists.]\n",
            encoding="utf-8",
        )
        derived_application = (
            self.repo
            / "knowledge/derived/by-source/notes/math/demo/chapters/02-application.typ.md"
        )
        derived_application.write_text(
            "# Derived application fixture\n", encoding="utf-8"
        )
        self.sync(files=[Path("notes/math/demo/chapters/02-application.typ")])
        relation = self.repo / "knowledge/build/relation.json"
        relation.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {"id": "completion-theorem", "text": "Every object in scope admits a completion."},
                    ],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "completion-theorem",
                            "evidence": "the completion is constructed from the sigma-algebra",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        knowledge.apply_delta(self.graph, self.typst_registry, relation)

        application_authority = "notes/math/demo/chapters/02-application.typ"
        state = knowledge.load_state(self.graph)
        report = knowledge.curation_report(state, {application_authority})
        self.assertEqual(
            ["missing-cross-file-ref"],
            [item["code"] for item in report["errors"]],
        )
        self.assertFalse(report["required_refs"][0]["covered"])

        application.write_text(
            "= Application\n#theorem(title: [#kn[completion theorem]])[A completion exists.]\n"
            "By #ref[$sigma$-algebra], the construction can proceed.\n",
            encoding="utf-8",
        )
        state, _, _ = self.sync(files=[Path(application_authority)])
        report = knowledge.curation_report(state, {application_authority})
        self.assertEqual([], report["errors"])
        self.assertTrue(report["required_refs"][0]["covered"])


if __name__ == "__main__":
    unittest.main()
