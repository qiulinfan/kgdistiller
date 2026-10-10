from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from kgdistiller.capture import CaptureError, prepare_capture, prepare_captures
from kgdistiller.ingest import apply_ingest, load_request, plan_ingest
from kgdistiller.knowledge_store import load_state
from tests.knowledge_fixture import make_fixture

SOURCE = "notes/chapter.tex"
TEXT = (
    "\\section{Basics}\n"
    "\\begin{definition}[Encoder]\n"
    "An encoder maps inputs to codes.\n"
    "\\end{definition}\n"
    "Beta transforms an input representation.\n"
    "测度论 studies measures.\n"
)


class CaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.root = self.fixture.root
        self.paths = self.fixture.paths
        self.fixture.write_source(SOURCE, TEXT)
        self.output = self.root / ".knowledge/build/captures"

    def payload(self, **changes):
        value = {
            "label": "Beta", "source": SOURCE, "line_start": 5, "line_end": 5,
            "kind": "definition", "text": "Beta transforms an input representation.",
            "entry": {"understanding": "not-yet-understood",
                      "pending_prerequisites": ["Encoder: used without an explanation."]},
            "review": {"action": "add", "reviewer": "tester",
                       "evidence": "The source states what Beta does; no existing entry matches."},
        }
        value.update(changes)
        return value

    def capture(self, payload, output=None):
        result = prepare_capture(self.paths, payload, output or self.output)
        receipt = apply_ingest(self.paths, load_request(Path(result["artifacts"]["apply"]), mode="apply"))
        return result, receipt

    def test_capture_plans_and_applies_one_entry_with_verbatim_evidence(self) -> None:
        source_before = (self.root / SOURCE).read_bytes()
        result = prepare_capture(self.paths, self.payload(), self.output)
        self.assertEqual(result["request_id"], "capture-beta-1")
        self.assertEqual(result["entries"], [{"id": "beta", "label": "Beta", "action": "add",
                                              "entry": ".knowledge/entries/beta.md"}])
        plan = load_request(Path(result["artifacts"]["plan"]), mode="plan")
        self.assertEqual(plan_ingest(self.paths, plan)["changes"]["entries_created"], ["beta"])
        receipt = apply_ingest(self.paths, load_request(Path(result["artifacts"]["apply"]), mode="apply"))
        self.assertEqual(receipt["status"], "committed")
        self.assertEqual((self.root / SOURCE).read_bytes(), source_before)
        entry = load_state(self.root).entries["beta"]
        self.assertEqual(entry["evidence"], "Beta transforms an input representation.")
        self.assertEqual(entry["understanding"], "not-yet-understood")
        self.assertEqual(entry["pending_prerequisites"], ["Encoder: used without an explanation."])
        self.assertEqual((entry["source"], entry["line_start"], entry["line_end"]), (SOURCE, 5, 5))
        self.assertEqual(prepare_capture(self.paths, self.payload(label="Beta two", id="beta-two"),
                                         self.output)["request_id"], "capture-beta-two-1")

    def test_request_ids_count_up(self) -> None:
        self.capture(self.payload())
        update = self.payload(review={"action": "update", "reviewer": "tester", "evidence": "Re-read.",
                                      "target_id": "beta"})
        first = prepare_capture(self.paths, update, self.output)
        second = prepare_capture(self.paths, update, self.output)
        self.assertEqual((first["request_id"], second["request_id"]), ("capture-beta-2", "capture-beta-3"))
        apply_ingest(self.paths, load_request(Path(second["artifacts"]["apply"])))
        other = self.root / ".knowledge/build/elsewhere"
        self.assertEqual(prepare_capture(self.paths, update, other)["request_id"], "capture-beta-2")

    def test_multi_line_evidence_is_copied_from_the_cited_lines(self) -> None:
        self.capture(self.payload(label="Encoder", line_start=2, line_end=4, text="Maps inputs to codes."))
        self.assertEqual(load_state(self.root).entries["encoder"]["evidence"],
                         "\\begin{definition}[Encoder]\nAn encoder maps inputs to codes.\n\\end{definition}")

    def test_update_merges_omitted_fields_and_rename_keeps_the_old_label(self) -> None:
        self.capture(self.payload(aliases=["B"]))
        update = {
            "label": "Beta transform", "source": SOURCE, "line_start": 5, "line_end": 5,
            "text": "Beta, reviewed again.", "entry": {"open_questions": ["Is it invertible?"],
                                                       "pending_prerequisites": []},
            "review": {"action": "update", "reviewer": "tester", "evidence": "Renamed after review.",
                       "target_id": "beta"},
        }
        _, receipt = self.capture(update)
        self.assertEqual(receipt["changes"]["aliases_changed"], ["beta"])
        entry = load_state(self.root).entries["beta"]
        self.assertEqual(entry["label"], "Beta transform")
        self.assertEqual(entry["aliases"], ["B", "Beta"])
        self.assertEqual(entry["kind"], "definition")
        self.assertEqual(entry["understanding"], "not-yet-understood")
        self.assertEqual(entry["open_questions"], ["Is it invertible?"])
        self.assertNotIn("pending_prerequisites", entry)
        self.assertEqual(entry["summary"], "Beta, reviewed again.")
        update["label"] = "Beta"
        update["review"]["evidence"] = "Renamed back."
        self.capture(update)
        entry = load_state(self.root).entries["beta"]
        self.assertEqual((entry["label"], entry["aliases"]), ("Beta", ["B", "Beta transform"]))

    def test_cjk_label_needs_an_explicit_id(self) -> None:
        payload = self.payload(label="测度论", line_start=6, line_end=6, text="Measure theory.")
        with self.assertRaisesRegex(CaptureError, "explicit id"):
            prepare_capture(self.paths, payload, self.output)
        result, _ = self.capture({**payload, "id": "measure-theory"})
        self.assertEqual(result["request_id"], "capture-measure-theory-1")
        self.assertEqual(load_state(self.root).entries["measure-theory"]["label"], "测度论")

    def test_identity_is_checked_against_labels_and_aliases(self) -> None:
        self.capture(self.payload(aliases=["Beta map"]))
        for payload, message in (
            (self.payload(), "already exists"),
            (self.payload(label="BETA  MAP", id="beta-map"), "already identifies beta"),
            (self.payload(label="Gamma", aliases=["beta"]), "already identifies beta"),
            (self.payload(review={"action": "update", "reviewer": "t", "evidence": "e",
                                  "target_id": "ghost"}), "does not exist"),
            (self.payload(review={"action": "add", "reviewer": "t", "evidence": "e",
                                  "target_id": "beta"}), "no target_id"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(CaptureError, message):
                prepare_capture(self.paths, payload, self.output)

    def test_kind_is_required_for_add_and_checked_against_the_document_type(self) -> None:
        payload = self.payload()
        payload.pop("kind")
        with self.assertRaisesRegex(CaptureError, "kind is required"):
            prepare_capture(self.paths, payload, self.output)
        self.fixture.document_types = {"papers": {"node_kinds": ["concept", "method"],
                                                  "extraction_guidance": "Named methods."}}
        self.fixture.sources[0]["document_type"] = "papers"
        self.fixture.write_registry()
        with self.assertRaisesRegex(CaptureError, "not allowed"):
            prepare_capture(self.paths, self.payload(), self.output)
        self.capture(self.payload(kind="method"))
        self.assertEqual(load_state(self.root).entries["beta"]["kind"], "method")

    def test_source_and_scope_checks(self) -> None:
        (self.root / "loose.txt").write_text(TEXT, encoding="utf-8")
        cases = (
            (self.payload(source="loose.txt"), "not admitted"),
            (self.payload(source="notes/missing.txt"), "does not exist"),
            (self.payload(source="../outside.txt"), "inside the project"),
            (self.payload(line_end=99), "outside the source"),
            (self.payload(line_start=0), "positive integer"),
            (self.payload(line_start=5, line_end=4), "outside the source"),
            ({**self.payload(), "name": "Beta"}, "unknown capture fields"),
            ({**self.payload(), "source_content": "x"}, "unknown capture fields"),
            (self.payload(entry={"sources": []}), "entry may contain only"),
        )
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(CaptureError, message):
                prepare_capture(self.paths, payload, self.output)
        for output in (self.root / "notes/out", self.root / ".knowledge/entries/out"):
            with self.subTest(output=output), self.assertRaisesRegex(CaptureError, "output_dir"):
                prepare_capture(self.paths, self.payload(), output)

    def test_any_text_format_is_captured_identically(self) -> None:
        for index, name in enumerate(("a.md", "b.typ", "c.txt", "d"), 1):
            path = f"notes/{name}"
            self.fixture.write_source(path, f"Concept {index} is plain text here.\n")
            self.capture(self.payload(label=f"Concept {index}", source=path, line_start=1, line_end=1,
                                      text="Plain text."))
        self.assertEqual(len(load_state(self.root).entries), 4)

    def test_batch_is_one_request(self) -> None:
        result = prepare_captures(self.paths, [
            self.payload(),
            self.payload(label="Encoder", line_start=2, line_end=4, text="Maps inputs."),
        ], self.output, request_id="batch-1")
        receipt = apply_ingest(self.paths, load_request(Path(result["artifacts"]["apply"])))
        self.assertEqual(receipt["changes"]["entries_created"], ["beta", "encoder"])
        with self.assertRaisesRegex(CaptureError, "more than once"):
            prepare_captures(self.paths, [self.payload(label="Delta"), self.payload(label="Delta")], self.output)

    def test_cli_prepares_requests(self) -> None:
        (self.root / "capture.json").write_text(json.dumps(self.payload()), encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root), "capture", "prepare",
             "capture.json", "--output", ".knowledge/build/captures"],
            capture_output=True, text=True, env=environment, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        artifacts = json.loads(result.stdout)["artifacts"]
        self.assertTrue(Path(artifacts["plan"]).is_file())
        apply_ingest(self.paths, load_request(Path(artifacts["apply"]), mode="apply"))
        self.assertIn("beta", load_state(self.root).entries)


if __name__ == "__main__":
    unittest.main()
