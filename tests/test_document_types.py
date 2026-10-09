from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kgdistiller import cli
from kgdistiller.document_types import load_document_types, validate_node_kind


class DocumentTypesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        (self.repo / "sources").mkdir()
        (self.repo / ".knowledge").mkdir()
        self.registry = self.repo / ".knowledge/sources.json"
        self.graph = self.repo / ".knowledge/graph"
        self.profile = {
            "node_kinds": ["construction", "measurement protocol"],
            "extraction_guidance": "Extract explained constructions. Leave unexplained terms pending.\nObservations are relations.",
        }
        self.payload = {
            "schema": cli.SOURCE_SCHEMA,
            "document_types": {"实验日志": self.profile},
            "sources": [{"id": "reading", "root": "sources", "files": ["*.md", "*.typ", "*.tex"],
                         "document_type": "实验日志"}],
        }
        self.save_registry()

    def save_registry(self) -> None:
        self.registry.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def sync(self):
        return cli.synchronize(self.repo, self.registry, self.graph, files=[], write=True)[0]

    def apply_kind(self, node_id: str, kind: object, *, text: str | None = None) -> None:
        delta = self.repo / "delta.json"
        node = {"id": node_id, "properties": {"kind": kind}}
        if text is not None:
            node["text"] = text
        delta.write_text(json.dumps({"schema": cli.DELTA_SCHEMA,
                                     "nodes": [node]}), encoding="utf-8")
        cli.apply_delta(self.graph, delta, repo_root=self.repo, registry=self.registry)

    def test_user_profile_is_independent_of_format_and_domain(self) -> None:
        sources = {
            "text.md": "--[[Text object]]--\nDefinition body.\n",
            "native.typ": "#definition(title: [#kn[Native object]])[Definition body.]\n",
            "formula.tex": "\\begin{definition}\\kn{Formula object} Definition body.\\end{definition}\n",
        }
        spec = cli.load_sources(self.repo, self.registry)[0]
        self.assertEqual(spec.document_type, "实验日志")
        for name, content in sources.items():
            with self.subTest(source=name):
                path = self.repo / "sources" / name
                path.write_text(content, encoding="utf-8")
                scanned = cli.scan_source(self.repo, spec, path, {})
                self.assertFalse(scanned.errors)
                self.assertEqual(len(scanned.definitions), 1)
                definition = scanned.definitions[0]
                self.assertEqual(definition.document_type, "实验日志")
                node = cli.source_node(definition, None)
                self.assertEqual(node["properties"]["document_type"], "实验日志")
                # Profile kinds guide reviewed extraction, not syntax scanning.
                self.assertEqual(node["properties"]["kind"], definition.kind)
                self.assertNotIn("source_kind", node["properties"])
        self.assertEqual(load_document_types(self.registry), {"实验日志": self.profile})

    def test_scan_exposes_selected_profile_before_any_markers_exist(self) -> None:
        source = self.repo / "sources/new.tex"
        source.write_text("\\section{A source not yet distilled}\nBody.\n", encoding="utf-8")
        self.payload["document_types"]["unrelated"] = {
            "node_kinds": ["object"], "extraction_guidance": "Other source rules.",
        }
        self.save_registry()
        output = io.StringIO()
        args = ["kgdistiller", "--repo-root", str(self.repo), "scan", "--file", str(source)]
        with patch("sys.argv", args), redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["definitions"], [])
        self.assertEqual(result["document_types"], {"实验日志": self.profile})
        self.assertEqual(result["sources"], [{"path": "sources/new.tex", "source_id": "reading",
                                               "source_format": "latex", "document_type": "实验日志"}])
        self.assertFalse(self.graph.exists())

    def test_omitted_profile_preserves_unclassified_legacy_sources(self) -> None:
        self.payload.pop("document_types")
        self.payload["sources"][0].pop("document_type")
        self.save_registry()
        self.assertEqual(load_document_types(self.registry), {})
        spec = cli.load_sources(self.repo, self.registry)[0]
        self.assertEqual(spec.document_type, "")
        self.assertEqual(validate_node_kind("unlisted semantic object", "", {}), "unlisted semantic object")

    def test_rejects_unknown_and_malformed_explicit_classifications(self) -> None:
        for value in ("missing", "", None, 7, ["实验日志"]):
            with self.subTest(value=value):
                self.payload["sources"][0]["document_type"] = value
                self.save_registry()
                with self.assertRaises(cli.KnowledgeError):
                    cli.load_sources(self.repo, self.registry)

    def test_rejects_malformed_profiles(self) -> None:
        variants = [None, [], {"": self.profile}, {"x": {}},
                    {"x": {**self.profile, "node_kinds": []}},
                    {"x": {**self.profile, "node_kinds": ["a", "a"]}},
                    {"x": {**self.profile, "node_kinds": ["a\nb"]}},
                    {"x": {**self.profile, "node_kinds": [3]}},
                    {"x": {**self.profile, "extraction_guidance": " "}},
                    {"x": {**self.profile, "typo": "ignored?"}}]
        for profiles in variants:
            with self.subTest(profiles=profiles):
                self.payload["document_types"] = profiles
                self.save_registry()
                with self.assertRaises(cli.KnowledgeError):
                    load_document_types(self.registry)

    def test_reviewed_semantic_kind_survives_syntax_change_and_sync(self) -> None:
        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        self.sync()
        self.apply_kind("test-object", "construction", text="Reviewed definition.")
        source.write_text("Theorem --[[Test object]]--\nChanged body.\n", encoding="utf-8")
        node = self.sync().nodes["test-object"]
        self.assertEqual(node["properties"]["kind"], "construction")
        self.assertEqual(node["properties"]["kind_origin"], "reviewed")
        self.assertEqual(node["properties"]["source_kind"], "theorem")
        self.assertEqual(node["properties"]["document_type"], "实验日志")
        # Invalid reviewed kinds fail before changing durable graph state.
        previous = copy.deepcopy(cli.load_state(self.graph).nodes)
        with self.assertRaisesRegex(cli.KnowledgeError, "not allowed"):
            self.apply_kind("test-object", "theorem")
        self.assertEqual(cli.load_state(self.graph).nodes, previous)

    def test_kind_only_update_preserves_scientific_review_status_and_fingerprints(self) -> None:
        from kgdistiller.entry_markdown import parse_entry

        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nOriginal definition.\n", encoding="utf-8")
        self.sync()
        self.apply_kind("test-object", "construction", text="The reviewed original definition.")
        entry = self.repo / ".knowledge/entries/test-object.md"
        before = entry.read_text(encoding="utf-8")
        original = parse_entry(entry)
        source.write_text("Definition --[[Test object]]--\nScientifically changed definition.\n", encoding="utf-8")
        self.assertEqual(self.sync().nodes["test-object"]["properties"]["curation_status"], "needs-review")

        self.apply_kind("test-object", "measurement protocol")

        node = cli.load_state(self.graph).nodes["test-object"]
        self.assertEqual(node["properties"]["curation_status"], "needs-review")
        self.assertEqual(node["properties"]["kind"], "measurement protocol")
        expected = copy.deepcopy(original)
        expected["metadata"]["kgd_kind"] = "measurement protocol"
        self.assertEqual(parse_entry(entry), expected)
        self.assertEqual(entry.read_text(encoding="utf-8"), before.replace('kgd_kind: "construction"', 'kgd_kind: "measurement protocol"'))
        self.assertEqual(self.sync().nodes["test-object"]["properties"]["curation_status"], "needs-review")
        self.apply_kind("test-object", "measurement protocol", text="The reviewed changed definition.")
        self.assertEqual(cli.load_state(self.graph).nodes["test-object"]["properties"]["curation_status"], "current")
        self.assertNotEqual(parse_entry(entry)["metadata"]["kgd_definition_sha256"], original["metadata"]["kgd_definition_sha256"])

    def test_unreviewed_kind_still_tracks_source_syntax(self) -> None:
        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        self.sync()
        source.write_text("Theorem --[[Test object]]--\nChanged body.\n", encoding="utf-8")
        node = self.sync().nodes["test-object"]
        self.assertEqual(node["properties"]["kind"], "theorem")
        self.assertNotIn("kind_origin", node["properties"])
        self.assertNotIn("source_kind", node["properties"])

    def test_removing_reviewed_kind_restores_scanner_kind_without_redundant_metadata(self) -> None:
        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        self.sync()
        self.apply_kind("test-object", "construction", text="Reviewed definition.")
        # The original syntax is retained immediately, before any later scan.
        self.assertEqual("definition", cli.load_state(self.graph).nodes["test-object"]["properties"]["source_kind"])
        entry = self.repo / ".knowledge/entries/test-object.md"
        entry.write_text(entry.read_text().replace('kgd_kind: "construction"\n', ""), encoding="utf-8")
        node = self.sync().nodes["test-object"]
        self.assertEqual("definition", node["properties"]["kind"])
        self.assertNotIn("kind_origin", node["properties"])
        self.assertNotIn("source_kind", node["properties"])
        before = {path.name: path.read_bytes() for path in self.graph.iterdir() if path.is_file()}
        self.sync()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.graph.iterdir() if path.is_file()})

    def test_reviewed_kind_equal_to_scanner_kind_still_preserves_reset_semantics(self) -> None:
        self.payload["sources"][0].pop("document_type")
        self.save_registry()
        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        self.sync()
        self.apply_kind("test-object", "definition", text="Reviewed definition.")
        properties = self.sync().nodes["test-object"]["properties"]
        self.assertEqual("reviewed", properties["kind_origin"])
        self.assertEqual("definition", properties["source_kind"])
        source.write_text("Theorem --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        properties = self.sync().nodes["test-object"]["properties"]
        self.assertEqual("definition", properties["kind"])
        self.assertEqual("theorem", properties["source_kind"])
        entry = self.repo / ".knowledge/entries/test-object.md"
        entry.write_text(entry.read_text().replace('kgd_kind: "definition"\n', ""), encoding="utf-8")
        properties = self.sync().nodes["test-object"]["properties"]
        self.assertEqual("theorem", properties["kind"])
        self.assertNotIn("source_kind", properties)
        self.assertNotIn("kind_origin", properties)

    def test_kind_without_accepted_content_cannot_live_only_in_generated_graph(self) -> None:
        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nFirst body.\n", encoding="utf-8")
        self.sync()
        previous = copy.deepcopy(cli.load_state(self.graph).nodes)
        for text in (None, ""):
            with self.subTest(text=text), self.assertRaisesRegex(cli.KnowledgeError, "existing knowledge entry or reviewed content"):
                self.apply_kind("test-object", "construction", text=text)
        self.assertEqual(cli.load_state(self.graph).nodes, previous)
        self.assertFalse((self.repo / ".knowledge/entries/test-object.md").exists())

    def test_kind_only_uses_previous_staged_content_for_repeated_node_updates(self) -> None:
        from kgdistiller.entry_markdown import parse_entry

        source = self.repo / "sources/item.md"
        source.write_text("Definition --[[Test object]]--\nSource definition.\n", encoding="utf-8")
        self.sync()
        entry = self.repo / ".knowledge/entries/test-object.md"
        delta = self.repo / "delta.json"
        for text in ("First accepted entry.", "Updated accepted entry."):
            with self.subTest(text=text):
                delta.write_text(json.dumps({"schema": cli.DELTA_SCHEMA, "nodes": [
                    {"id": "test-object", "text": text, "properties": {"kind": "construction"}},
                    {"id": "test-object", "properties": {"kind": "measurement protocol"}},
                ]}), encoding="utf-8")
                cli.apply_delta(self.graph, delta, repo_root=self.repo, registry=self.registry)
                parsed = parse_entry(entry)
                self.assertEqual(parsed["entry"]["summary"], text)
                self.assertEqual(parsed["metadata"]["kgd_kind"], "measurement protocol")
                node = cli.load_state(self.graph).nodes["test-object"]
                self.assertEqual(node["text"], text)
                self.assertEqual(node["properties"]["kind"], "measurement protocol")


if __name__ == "__main__":
    unittest.main()
