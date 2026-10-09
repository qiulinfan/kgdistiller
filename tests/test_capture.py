from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.alignment import empty_alignment_set
from kgdistiller.capture import CaptureError, prepare_capture
from kgdistiller.cli import SOURCE_SCHEMA, load_state, sha256_authority_file, synchronize
from kgdistiller.ingest import IngestPaths, apply_ingest, load_request, plan_ingest
from kgdistiller.entry_markdown import EntryMarkdownError, default_derived_relative
from kgdistiller.query import compare


class CaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-capture-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "notes/chapter.md"
        self.source.parent.mkdir(parents=True)
        self.original = "> **Definition: --[[Alpha]]--**\n> Alpha is an unrelated pending definition.\n"
        self.source.write_text(self.original, encoding="utf-8")
        self.registry = self.root / "knowledge/sources.json"
        self.registry.parent.mkdir()
        self.registry.write_text(json.dumps({
            "schema": SOURCE_SCHEMA,
            "fields": [{"id": "demo", "label": "Demo", "text": "Demo field."}],
            "sources": [{
                "id": "notes:demo", "subject": "demo", "course": "demo",
                "knowledge_origin": "personal-note", "fields": ["demo"],
                "root": "notes", "files": ["*.md", "*.typ", "*.tex"],
                "web": "https://example.test/notes", "topics": [],
            }],
        }), encoding="utf-8")
        self.alignments = self.root / "knowledge/alignments.json"
        self.alignments.write_text(json.dumps(empty_alignment_set()), encoding="utf-8")
        self.paths = IngestPaths(
            repo_root=self.root, registry=self.registry,
            graph_dir=self.root / "knowledge/graph",
            identities=self.root / "knowledge/identities.json",
            alignments=self.alignments,
            typst_registry=self.root / "knowledge/build/knowledge-registry.typ",
        )
        synchronize(
            self.root, self.registry, self.paths.graph_dir, self.paths.typst_registry,
            identities=self.paths.identities, alignments=self.alignments,
            files=[], course=None, subject=None, write=True,
        )
        self.output = self.root / "knowledge/build/captures"

    def payload(self) -> dict:
        return {
            "name": "Beta", "source": "notes/chapter.md",
            "text": "Beta transforms an input representation.",
            "entry": {
                "understanding": "not-yet-understood",
                "pending_prerequisites": ["Encoder: used here without an explanation."],
            },
            "source_content": self.original + "\n> **Definition: --[[Beta]]--**\n> Beta transforms an input representation.\n",
            "review": {
                "action": "add", "reviewer": "test-reviewer",
                "evidence": "The selected source explicitly defines Beta; a distinct identity was reviewed.",
            },
        }

    def test_one_capture_plans_and_applies_without_curating_other_definitions(self) -> None:
        before = self.source.read_bytes()
        with patch("kgdistiller.capture.compare", wraps=compare) as comparison:
            result = prepare_capture(self.paths, self.payload(), self.output)
        self.assertEqual(comparison.call_count, 1)
        self.assertEqual(self.source.read_bytes(), before)
        candidate = json.loads(Path(result["artifacts"]["candidate"]).read_text())
        self.assertEqual([node["id"] for node in candidate["nodes"]], ["beta"])
        self.assertEqual(candidate["edges"], [])
        self.assertEqual(candidate["references"], [])
        plan = load_request(Path(result["artifacts"]["plan"]), mode="plan")
        self.assertEqual(plan["authority_patches"][0]["expected_markers"], {
            "definitions": ["alpha", "beta"], "references": [],
        })
        plan_ingest(self.paths, plan)
        self.assertEqual(self.source.read_bytes(), before)
        request = load_request(Path(result["artifacts"]["apply"]), mode="apply")
        receipt = apply_ingest(self.paths, request)
        self.assertEqual(receipt["status"], "committed")
        state = load_state(self.paths.graph_dir)
        self.assertNotIn("encoder", state.nodes)
        self.assertEqual(state.nodes["alpha"]["properties"]["curation_status"], "pending")
        self.assertEqual(state.nodes["beta"]["entry"]["understanding"], "not-yet-understood")
        self.assertEqual(state.nodes["beta"]["entry"]["pending_prerequisites"],
                         ["Encoder: used here without an explanation."])

    def test_update_requires_reviewed_existing_target(self) -> None:
        payload = self.payload()
        payload.update(name="Alpha", text="Alpha is the reviewed definition.")
        payload.pop("source_content")
        payload["review"].update(action="update", target_id="alpha")
        result = prepare_capture(self.paths, payload, self.output)
        request = load_request(Path(result["artifacts"]["apply"]), mode="apply")
        apply_ingest(self.paths, request)
        self.assertEqual(load_state(self.paths.graph_dir).nodes["alpha"]["text"], payload["text"])
        self.assertEqual(self.source.read_text(), self.original)

    def test_update_can_select_existing_source_marker_without_target_id(self) -> None:
        payload = self.payload()
        payload.update(name="Alpha", text="Alpha is the reviewed definition.")
        payload.pop("source_content")
        payload["review"]["action"] = "update"
        result = prepare_capture(self.paths, payload, self.output)
        request = load_request(Path(result["artifacts"]["apply"]), mode="apply")
        self.assertEqual(request["decisions"][0]["target_id"], "alpha")
        apply_ingest(self.paths, request)
        self.assertEqual(load_state(self.paths.graph_dir).nodes["alpha"]["text"], payload["text"])

    def test_update_cannot_take_identity_from_another_source(self) -> None:
        payload = self.payload()
        payload.update(name="Alpha", source="notes/other.md", source_content=self.original)
        payload["review"]["action"] = "update"
        with self.assertRaisesRegex(CaptureError, "existing native identity in this source"):
            prepare_capture(self.paths, payload, self.output)

    def test_capture_cli_prepares_actual_ingest_requests(self) -> None:
        input_path = self.root / "capture.json"
        input_path.write_text(json.dumps(self.payload()), encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root),
             "capture", "prepare", input_path.name, "--output", "knowledge/build/captures"],
            capture_output=True, text=True, check=False, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads(result.stdout)
        self.assertEqual(prepared["status"], "prepared")
        plan_ingest(self.paths, load_request(Path(prepared["artifacts"]["plan"])))
        self.assertEqual(self.source.read_text(), self.original)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_update_preserves_omitted_metadata_and_can_clear_a_direct_gap(self) -> None:
        payload = self.payload()
        payload["entry"]["context"] = "An explanation that must survive a small update."
        prepared = prepare_capture(self.paths, payload, self.output)
        apply_ingest(self.paths, load_request(Path(prepared["artifacts"]["apply"])))
        payload.pop("source_content")
        payload["review"].update(action="update", target_id="beta")
        payload["entry"] = {"pending_prerequisites": []}
        prepared = prepare_capture(self.paths, payload, self.output)
        apply_ingest(self.paths, load_request(Path(prepared["artifacts"]["apply"])))
        entry = load_state(self.paths.graph_dir).nodes["beta"]["entry"]
        self.assertEqual(entry["context"], "An explanation that must survive a small update.")
        self.assertEqual(entry["understanding"], "not-yet-understood")
        self.assertNotIn("pending_prerequisites", entry)

    def test_native_markers_in_all_formats(self) -> None:
        for extension, content in (
            ("md", "--[[Beta]]--\nBeta is defined here.\n"),
            ("typ", "#kn[Beta]\nBeta is defined here.\n"),
            ("tex", "\\kn{Beta}\nBeta is defined here.\n"),
        ):
            with self.subTest(extension=extension):
                payload = self.payload()
                payload.update(source=f"notes/new.{extension}", source_content=content)
                if extension != "md":
                    derived = self.root / default_derived_relative(payload["source"])
                    derived.parent.mkdir(parents=True, exist_ok=True)
                    derived.write_text("Beta is defined here.\n", encoding="utf-8")
                result = prepare_capture(self.paths, payload, self.output)
                request = load_request(Path(result["artifacts"]["plan"]))
                self.assertIsNone(request["authority_patches"][0]["expected_sha256"])
                self.assertEqual(request["authority_patches"][0]["expected_markers"]["definitions"], ["beta"])
                plan_ingest(self.paths, request)

    def test_native_source_requires_existing_derived_evidence(self) -> None:
        payload = self.payload()
        payload.update(source="notes/new.typ", source_content="#kn[Beta]\nBeta is defined here.\n")
        with self.assertRaisesRegex(EntryMarkdownError, "does not exist"):
            prepare_capture(self.paths, payload, self.output)
        self.assertEqual(list(self.output.glob("*.json")), [])

    def test_normalizes_authority_newlines(self) -> None:
        self.source.write_bytes(self.original.replace("\n", "\r\n").encode())
        payload = self.payload()
        payload["source_content"] = payload["source_content"].replace("\n", "\r\n")
        result = prepare_capture(self.paths, payload, self.output)
        request = load_request(Path(result["artifacts"]["plan"]))
        patch_record = request["authority_patches"][0]
        self.assertEqual(patch_record["expected_sha256"], sha256_authority_file(self.source))
        self.assertNotIn("\r", patch_record["content"])
        plan_ingest(self.paths, request)

    def test_duplicate_identity_cannot_be_added(self) -> None:
        payload = self.payload()
        payload.update(name="Alpha", source_content=self.original)
        with self.assertRaisesRegex(CaptureError, "already exists"):
            prepare_capture(self.paths, payload, self.output)
        self.assertEqual(list(self.output.glob("*.json")), [])

    def test_ambiguous_comparison_is_not_written(self) -> None:
        def ambiguous(*args, **kwargs):
            report = compare(*args, **kwargs)
            report["results"][0]["status"] = "ambiguous"
            return report

        with patch("kgdistiller.capture.compare", side_effect=ambiguous):
            with self.assertRaisesRegex(CaptureError, "ambiguous"):
                prepare_capture(self.paths, self.payload(), self.output)
        self.assertEqual(list(self.output.glob("*.json")), [])

    def test_requires_a_review_and_preserves_other_definitions(self) -> None:
        for mutation in ("no-review", "wrong-target", "change-unselected", "new-unselected", "missing-marker"):
            with self.subTest(mutation=mutation):
                payload = self.payload()
                if mutation == "no-review":
                    payload.pop("review")
                elif mutation == "wrong-target":
                    payload["review"].update(action="update", target_id="alpha")
                elif mutation == "change-unselected":
                    payload["source_content"] = payload["source_content"].replace("unrelated pending", "changed")
                elif mutation == "new-unselected":
                    payload["source_content"] += "\n--[[Gamma]]--\nGamma is another concept.\n"
                else:
                    payload["source_content"] = self.original
                with self.assertRaises(CaptureError):
                    prepare_capture(self.paths, payload, self.output)
        self.assertEqual(self.source.read_text(), self.original)

    def test_rejects_paths_outside_capture_scope(self) -> None:
        for location in (self.root.parent / "escape", self.source.parent / "captures", self.paths.graph_dir / "captures"):
            with self.subTest(location=location):
                with self.assertRaises(CaptureError):
                    prepare_capture(self.paths, self.payload(), location)
        payload = copy.deepcopy(self.payload())
        payload["source"] = "../escape.md"
        with self.assertRaises(CaptureError):
            prepare_capture(self.paths, payload, self.output)


if __name__ == "__main__":
    unittest.main()
