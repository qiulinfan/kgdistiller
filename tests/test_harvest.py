from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_capture

from kgdistiller.capture import CaptureError, prepare_captures
from kgdistiller.cli import load_state, synchronize
from kgdistiller.entry_markdown import DERIVED_SOURCE_ROOT, default_derived_relative
from kgdistiller.harvest import HarvestError, apply_harvest, prepare_harvest
from kgdistiller.ingest import apply_ingest, load_request
from kgdistiller.query import compare


class HarvestTest(unittest.TestCase):
    setUp = test_capture.CaptureTest.setUp
    payload = test_capture.CaptureTest.payload

    def prepare(self, captures=None):
        self.sheet = self.root / "notes/chapter-defs.md"
        self.reviews = self.root / ".knowledge/build/reviews"
        self.runs = self.root / ".knowledge/build/harvest-runs"
        return prepare_harvest(self.paths, {"captures": captures or [self.payload()]}, self.sheet, self.reviews)

    def check(self, name="Beta"):
        text = self.sheet.read_text(encoding="utf-8")
        self.sheet.write_text(text.replace(f"- [ ] [{name} (draft)]", f"- [x] [{name} (draft)]"), encoding="utf-8")

    def gamma(self):
        payload = self.payload()
        payload["name"] = "Gamma"
        payload["text"] = "Gamma is a distinct transformation."
        payload["source_content"] = self.original + "\n> **Definition: --[[Gamma]]--**\n> Gamma is a distinct transformation.\n"
        return payload

    def test_checked_only_real_ingest_preserves_pending_and_annotations(self):
        self.prepare([self.payload(), self.gamma()])
        self.check()
        self.sheet.write_text(self.sheet.read_text(encoding="utf-8") + "\nPersonal annotation.\n- [x] Ordinary task\n", encoding="utf-8")
        with patch("kgdistiller.capture.compare", wraps=compare) as comparison:
            result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(comparison.call_count, 1)
        self.assertEqual(result["counts"]["committed"], 1)
        state = load_state(self.paths.graph_dir)
        self.assertIn("beta", state.nodes)
        self.assertNotIn("gamma", state.nodes)
        self.assertNotIn("--[[Gamma]]--", self.source.read_text(encoding="utf-8"))
        self.assertEqual(state.nodes["beta"]["entry"]["understanding"], "not-yet-understood")
        self.assertEqual(state.nodes["beta"]["entry"]["pending_prerequisites"], self.payload()["entry"]["pending_prerequisites"])
        self.assertIn("../.knowledge/entries/beta.md", self.sheet.read_text(encoding="utf-8"))
        self.assertIn("Personal annotation.\n- [x] Ordinary task", self.sheet.read_text(encoding="utf-8"))
        self.assertIn("- [ ] [Gamma (draft)]", self.sheet.read_text(encoding="utf-8"))
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")
        # A later checkbox from the same source rebases only over this harvest's
        # own accepted source update, retaining the still-reviewed Gamma edit.
        self.check("Gamma")
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertIn("gamma", load_state(self.paths.graph_dir).nodes)

    def test_multiple_selected_same_source_are_one_batch(self):
        self.prepare([self.payload(), self.gamma()])
        self.check()
        self.check("Gamma")
        with patch("kgdistiller.capture.compare", wraps=compare) as comparison:
            result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(comparison.call_count, 1)
        self.assertEqual(len(result["receipt"]["changes"]["nodes"]["added"]), 2)
        self.assertEqual(result["counts"]["committed"], 2)
        self.assertIn("beta", load_state(self.paths.graph_dir).nodes)
        self.assertIn("gamma", load_state(self.paths.graph_dir).nodes)

    def test_review_draft_shows_explicit_kind_and_omitted_update_preserves_it(self):
        payload = self.payload()
        payload["kind"] = "construction"
        prepared = self.prepare([payload])
        draft = Path(prepared["artifacts"]["drafts"][0]).read_text(encoding="utf-8")
        self.assertIn("## Knowledge type", draft)
        self.assertIn('- After: "construction"', draft)
        self.check()
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(load_state(self.paths.graph_dir).nodes["beta"]["properties"]["kind"], "construction")

        payload.pop("kind")
        payload.pop("source_content")
        payload["review"].update(action="update", target_id="beta")
        prepared = self.prepare([payload])
        draft = Path(prepared["artifacts"]["drafts"][0]).read_text(encoding="utf-8")
        self.assertIn('- Before: "construction"', draft)
        self.assertIn('- After: "construction"（保持现有类型）', draft)

        payload["kind"] = "procedure"
        prepared = self.prepare([payload])
        draft = Path(prepared["artifacts"]["drafts"][0]).read_text(encoding="utf-8")
        self.assertIn('- Before: "construction"', draft)
        self.assertIn('- After: "procedure"', draft)

    def test_recovery_after_ingest_before_sheet_refresh(self):
        self.prepare()
        self.check()
        with patch("kgdistiller.harvest._refresh_sheet", side_effect=OSError("simulated interruption")):
            interrupted = apply_harvest(self.paths, self.sheet, self.runs)
            self.assertEqual(interrupted["status"], "committed-sheet-pending")
        committed = load_state(self.paths.graph_dir).manifest["graph_sha256"]
        manifest = json.loads((self.reviews / "review.json").read_text(encoding="utf-8"))
        request = load_request(self.root / manifest["active_run"]["request"])
        result = apply_harvest(self.paths, self.sheet, self.root / "a-different-output")
        self.assertEqual(result["receipt"]["request_sha256"], request["request_sha256"])
        self.assertEqual(load_state(self.paths.graph_dir).manifest["graph_sha256"], committed)
        self.assertIn("../.knowledge/entries/beta.md", self.sheet.read_text(encoding="utf-8"))
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")

    def test_recovery_after_sheet_refresh_before_review_receipt(self):
        from kgdistiller.cli import atomic_write
        self.prepare()
        self.check()
        manifest_path = self.reviews / "review.json"
        writes = 0
        def interrupted_write(path, content):
            nonlocal writes
            if path.resolve() == manifest_path.resolve():
                writes += 1
                if writes == 2:
                    raise OSError("interrupted after sheet refresh")
            atomic_write(path, content)
        with patch("kgdistiller.harvest.atomic_write", side_effect=interrupted_write):
            result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(result["status"], "committed-sheet-pending")
        self.assertIn("../.knowledge/entries/beta.md", self.sheet.read_text(encoding="utf-8"))
        retried = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(retried["receipt"], result["receipt"])
        self.assertEqual(retried["status"], "committed")

    def test_edited_draft_is_rejected_without_writes(self):
        result = self.prepare()
        self.check()
        draft = Path(result["artifacts"]["drafts"][0])
        draft.write_text(draft.read_text(encoding="utf-8").replace("Beta transforms", "Beta alters"), encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "draft changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.original)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_targeted_re_review_replaces_one_pending_row_and_resets_selection(self):
        prepared = self.prepare([self.payload(), self.gamma()])
        self.check()
        text = self.sheet.read_text(encoding="utf-8")
        selected = next(line for line in text.splitlines() if "[Beta (draft)]" in line)
        self.sheet.write_text(text.replace(selected, selected + " My annotation."), encoding="utf-8")
        old_draft = Path(prepared["artifacts"]["drafts"][0])
        old_draft.write_text(old_draft.read_text(encoding="utf-8").replace("Beta transforms", "Beta maps"), encoding="utf-8")
        corrected = self.payload()
        corrected["text"] = "Beta maps an input representation."
        corrected["source_content"] = corrected["source_content"].replace("Beta transforms", "Beta maps")
        before_gamma = next(line for line in self.sheet.read_text(encoding="utf-8").splitlines() if "[Gamma (draft)]" in line)
        prepared = prepare_harvest(self.paths, {"captures": [corrected]}, self.sheet, self.reviews)
        after = self.sheet.read_text(encoding="utf-8")
        self.assertEqual(after.count("[Beta (draft)]"), 1)
        self.assertIn("- [ ] [Beta (draft)]", after)
        self.assertIn("My annotation.", after)
        self.assertIn(before_gamma, after)
        self.assertTrue(old_draft.exists())
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")
        self.check()
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(load_state(self.paths.graph_dir).nodes["beta"]["text"], corrected["text"])

    def test_source_changes_require_re_review(self):
        self.prepare()
        self.check()
        self.source.write_text(self.original + "\nExternal annotation.\n", encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "source changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_precommit_interruption_reuses_request_and_selection(self):
        self.prepare()
        self.check()
        with (
            patch("kgdistiller.harvest.apply_ingest", side_effect=OSError("interrupted before commit")),
            self.assertRaises(OSError),
        ):
            apply_harvest(self.paths, self.sheet, self.runs)
        first = json.loads((self.reviews / "review.json").read_text(encoding="utf-8"))["active_run"]["request"]
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)
        with patch("kgdistiller.harvest.prepare_captures") as prepare:
            result = apply_harvest(self.paths, self.sheet, self.root / "another-run-directory")
        prepare.assert_not_called()
        self.assertEqual(result["receipt"]["request_id"], load_request(self.root / first)["request_id"])

    def test_cli_prepares_and_harvests_selected_metadata(self):
        capture = self.root / "capture.json"
        capture.write_text(json.dumps({"captures": [self.payload()]}), encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        prefix = [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root), "harvest"]
        prepared = subprocess.run(prefix + ["prepare", "capture.json", "--sheet", "notes/defs.md",
                                  "--output", ".knowledge/build/reviews"],
                                  check=False, capture_output=True, text=True, env=environment)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        sheet = Path(json.loads(prepared.stdout)["sheet"])
        sheet.write_text(sheet.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
        applied = subprocess.run(prefix + ["apply", "notes/defs.md", "--output", ".knowledge/build/runs"],
                                 check=False, capture_output=True, text=True, env=environment)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["status"], "committed")
        self.assertIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_existing_entry_file_edit_cannot_be_overwritten(self):
        payload = self.payload()
        prepared = prepare_captures(self.paths, [payload], self.output)
        apply_ingest(self.paths, load_request(Path(prepared["artifacts"]["apply"])))
        payload.pop("source_content")
        payload["review"]["action"] = "update"
        payload["text"] = "A reviewed new explanation."
        self.prepare([payload])
        self.check()
        entry = self.root / ".knowledge/entries/beta.md"
        entry.write_text(entry.read_text(encoding="utf-8") + "\nAn external user note.\n", encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "entry changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertIn("An external user note", entry.read_text(encoding="utf-8"))

    def test_fenced_task_examples_and_ordinary_tasks_are_ignored(self):
        self.prepare()
        text = self.sheet.read_text(encoding="utf-8")
        task = next(line for line in text.splitlines() if "- [ ] [Beta (draft)]" in line).replace("[ ]", "[x]", 1)
        self.sheet.write_text(text + f"\n```markdown\n{task}\n```\n\n    {task}\n\n- [x] ordinary task\n", encoding="utf-8")
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_binding_and_link_cannot_be_repurposed(self):
        self.prepare()
        self.check()
        self.sheet.write_text(self.sheet.read_text(encoding="utf-8").replace("[Beta (draft)]", "[Different (draft)]"), encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "label or link"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_sheet_crlf_and_unchecked_annotations_are_preserved(self):
        self.prepare([self.payload(), self.gamma()])
        self.check()
        text = self.sheet.read_text(encoding="utf-8").replace("\n", "\r\n")
        text += "\r\nUnrelated annotation with trailing spaces.  \r\n"
        self.sheet.write_bytes(text.encode())
        unchecked = next(line for line in text.splitlines(keepends=True) if "[Gamma (draft)]" in line)
        apply_harvest(self.paths, self.sheet, self.runs)
        after = self.sheet.read_bytes().decode()
        self.assertIn(unchecked, after)
        self.assertTrue(after.endswith("Unrelated annotation with trailing spaces.  \r\n"))
        self.assertNotIn("\n", after.replace("\r\n", ""))

    def test_new_native_sources_support_markdown_typst_and_latex(self):
        for extension, content in (("md", "--[[Beta]]--\nBeta is defined here.\n"),
                                   ("typ", "#kn[Beta]\nBeta is defined here.\n"),
                                   ("tex", "\\kn{Beta}\nBeta is defined here.\n")):
            with self.subTest(extension=extension):
                payload = self.payload()
                name = "Concept" + extension
                payload.update(name=name, source=f"notes/concept-{extension}.{extension}", source_content=content.replace("Beta", name))
                if extension != "md":
                    derived = self.root / default_derived_relative(payload["source"])
                    derived.parent.mkdir(parents=True, exist_ok=True)
                    derived.write_text(f"{name} is defined here.\n", encoding="utf-8")
                sheet = self.root / f"notes/{extension}-defs.md"
                prepare_harvest(self.paths, {"captures": [payload]}, sheet, self.root / f".knowledge/build/{extension}-reviews")
                sheet.write_text(sheet.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
                apply_harvest(self.paths, sheet, self.root / f".knowledge/build/{extension}-runs")
                self.assertIn(name.lower(), load_state(self.paths.graph_dir).nodes)

    def test_existing_sheet_frontmatter_and_legacy_tracking_convert_safely(self):
        sheet = self.root / "notes/chapter-defs.md"
        original = "---\r\ntitle: Reading\r\n---\r\n\r\n# Definitions\r\n\r\nMy annotation.  \r\n"
        sheet.write_bytes(original.encode())
        synchronize(self.root, self.registry, self.paths.graph_dir,
                    identities=self.paths.identities, alignments=self.alignments,
                    files=[], write=True)
        self.assertIn("notes/chapter-defs.md", load_state(self.paths.graph_dir).manifest["source_hashes"])
        self.prepare()
        self.assertTrue(sheet.read_bytes().startswith(b"---\r\ntitle: Reading\r\n---\r\n"))
        self.assertIn(b"My annotation.  \r\n", sheet.read_bytes())
        self.check()
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("notes/chapter-defs.md", load_state(self.paths.graph_dir).manifest["source_hashes"])

    def test_sheet_cannot_overwrite_committed_metadata(self):
        for location in (".knowledge/entries/beta.md", ".knowledge/graph/manifest.json", str(DERIVED_SOURCE_ROOT / "beta.md")):
            with (
                self.subTest(location=location),
                self.assertRaises(HarvestError),
            ):
                prepare_harvest(self.paths, {"captures": [self.payload()]}, self.root / location, self.output)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_overlapping_source_changes_fail_before_knowledge_writes(self):
        beta, gamma = self.payload(), self.gamma()
        beta["source_content"] = "# Beta heading\n\n" + beta["source_content"]
        gamma["source_content"] = "# Gamma heading\n\n" + gamma["source_content"]
        self.prepare([beta, gamma])
        self.check()
        self.check("Gamma")
        with self.assertRaisesRegex(CaptureError, "overlap"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.original)
        self.assertNotIn("beta", load_state(self.paths.graph_dir).nodes)

    def test_external_edit_after_commit_does_not_become_an_owned_source_version(self):
        from kgdistiller.harvest import _refresh_sheet
        self.prepare([self.payload(), self.gamma()])
        self.check()
        def refresh_then_external_edit(*args):
            _refresh_sheet(*args)
            self.source.write_text(self.source.read_text(encoding="utf-8") + "\nExternal annotation.\n", encoding="utf-8")
        with patch("kgdistiller.harvest._refresh_sheet", side_effect=refresh_then_external_edit):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.check("Gamma")
        with self.assertRaisesRegex(HarvestError, "source changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("gamma", load_state(self.paths.graph_dir).nodes)


if __name__ == "__main__":
    unittest.main()
