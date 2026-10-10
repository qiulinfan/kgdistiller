from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller import harvest
from kgdistiller.capture import prepare_captures
from kgdistiller.harvest import HarvestError, apply_harvest, prepare_harvest
from kgdistiller.ingest import apply_ingest, load_request
from kgdistiller.knowledge_store import load_state
from tests.knowledge_fixture import make_fixture

SOURCE = "notes/chapter.md"
TEXT = (
    "# Chapter\n"
    "Alpha is an unrelated idea.\n"
    "Beta transforms an input representation.\n"
    "Gamma is a distinct transformation.\n"
)


class HarvestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.root = self.fixture.root
        self.paths = self.fixture.paths
        self.fixture.write_source(SOURCE, TEXT)
        self.sheet = self.root / "sheets/chapter-defs.md"
        self.reviews = self.root / ".knowledge/build/reviews"
        self.runs = self.root / ".knowledge/build/harvest-runs"

    def payload(self, label="Beta", line=3, **changes):
        value = {
            "label": label, "source": SOURCE, "line_start": line, "line_end": line,
            "kind": "definition", "text": f"{label} as reviewed.",
            "entry": {"understanding": "not-yet-understood", "pending_prerequisites": ["Encoder: unexplained."]},
            "review": {"action": "add", "reviewer": "tester", "evidence": f"The source states {label}."},
        }
        value.update(changes)
        return value

    def gamma(self):
        return self.payload("Gamma", 4)

    def prepare(self, captures=None):
        return prepare_harvest(self.paths, {"captures": captures or [self.payload()]}, self.sheet, self.reviews)

    def check(self, name="Beta"):
        text = self.sheet.read_text(encoding="utf-8")
        self.sheet.write_text(text.replace(f"- [ ] [{name} (draft)]", f"- [x] [{name} (draft)]"), encoding="utf-8")

    def entries(self):
        return load_state(self.root).entries

    def manifest(self):
        return json.loads((self.reviews / "review.json").read_text(encoding="utf-8"))

    def test_only_checked_tasks_are_ingested_and_linked(self) -> None:
        self.prepare([self.payload(), self.gamma()])
        self.check()
        self.sheet.write_text(self.sheet.read_text(encoding="utf-8") + "\nPersonal annotation.\n- [x] Ordinary task\n",
                              encoding="utf-8")
        result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["receipt"]["request_id"], "harvest-reviews-1")
        self.assertEqual(sorted(self.entries()), ["beta"])
        self.assertEqual(self.entries()["beta"]["pending_prerequisites"], ["Encoder: unexplained."])
        text = self.sheet.read_text(encoding="utf-8")
        self.assertIn("- [x] [Beta](../.knowledge/entries/beta.md)", text)
        self.assertIn("Personal annotation.\n- [x] Ordinary task", text)
        self.assertIn("- [ ] [Gamma (draft)]", text)
        self.assertEqual((self.root / SOURCE).read_text(encoding="utf-8"), TEXT)
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")
        self.check("Gamma")
        result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(result["receipt"]["request_id"], "harvest-reviews-2")
        self.assertEqual(sorted(self.entries()), ["beta", "gamma"])
        manifest = self.manifest()
        self.assertEqual(sorted(manifest), ["items", "last_request_id", "next_item", "runs", "schema", "sheet"])
        self.assertEqual(sorted(manifest["items"]), ["1-beta", "2-gamma"])
        self.assertEqual(sorted(manifest["items"]["1-beta"]), [
            "committed", "draft", "draft_text", "entry_id", "entry_text", "payload", "revision", "source_text"])

    def test_multiple_selected_tasks_are_one_transaction(self) -> None:
        self.prepare([self.payload(), self.gamma()])
        self.check()
        self.check("Gamma")
        result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(result["receipt"]["changes"]["entries_created"], ["beta", "gamma"])
        self.assertEqual(result["counts"], {"selected": 2, "committed": 2})

    def test_draft_shows_fields_cited_lines_and_evidence(self) -> None:
        draft = Path(self.prepare()["artifacts"]["drafts"][0]).read_text(encoding="utf-8")
        self.assertIn("- Kind: （无） → \"definition\"", draft)
        self.assertIn(f"- Source: `{SOURCE}` lines 3–3", draft)
        self.assertIn("## Evidence\n\n```\nBeta transforms an input representation.\n```", draft)
        self.check()
        apply_harvest(self.paths, self.sheet, self.runs)
        update = self.payload(kind="method", review={"action": "update", "reviewer": "tester",
                                                     "evidence": "Re-read.", "target_id": "beta"})
        draft = Path(self.prepare([update])["artifacts"]["drafts"][0]).read_text(encoding="utf-8")
        self.assertIn('- Kind: "definition" → "method"', draft)
        self.assertIn("## Entry before\n\n### Summary\n\nBeta as reviewed.", draft)

    def test_recovery_after_ingest_before_sheet_refresh(self) -> None:
        self.prepare()
        self.check()
        with patch("kgdistiller.harvest._refresh_sheet", side_effect=OSError("interrupted")):
            self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "committed-sheet-pending")
        self.assertEqual(self.manifest()["active_run"]["request_id"], "harvest-reviews-1")
        with patch("kgdistiller.harvest.apply_ingest") as ingest:
            result = apply_harvest(self.paths, self.sheet, self.root / "a-different-output")
        ingest.assert_not_called()
        self.assertEqual(result["status"], "committed")
        self.assertIn("../.knowledge/entries/beta.md", self.sheet.read_text(encoding="utf-8"))
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")

    def test_recovery_after_sheet_refresh_before_review_update(self) -> None:
        self.prepare()
        self.check()
        save = harvest._save_manifest
        calls = 0

        def interrupted(path, manifest):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("interrupted after sheet refresh")
            save(path, manifest)

        with patch("kgdistiller.harvest._save_manifest", side_effect=interrupted):
            result = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(result["status"], "committed-sheet-pending")
        self.assertIn("../.knowledge/entries/beta.md", self.sheet.read_text(encoding="utf-8"))
        retried = apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual((retried["status"], retried["receipt"]), ("committed", result["receipt"]))

    def test_precommit_interruption_reuses_request_and_selection(self) -> None:
        self.prepare()
        self.check()
        with patch("kgdistiller.harvest.apply_ingest", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            apply_harvest(self.paths, self.sheet, self.runs)
        request = self.manifest()["active_run"]["request"]
        self.assertNotIn("beta", self.entries())
        with patch("kgdistiller.harvest.prepare_captures") as prepare:
            result = apply_harvest(self.paths, self.sheet, self.root / "another-run-directory")
        prepare.assert_not_called()
        self.assertEqual(result["receipt"]["request_id"], load_request(self.root / request)["request_id"])

    def test_edited_draft_requires_a_new_prepare(self) -> None:
        draft = Path(self.prepare()["artifacts"]["drafts"][0])
        self.check()
        draft.write_text(draft.read_text(encoding="utf-8").replace("Beta as", "Beta was"), encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "draft changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("beta", self.entries())

    def test_source_changes_are_detected_by_text(self) -> None:
        self.prepare()
        self.check()
        self.fixture.write_source(SOURCE, TEXT + "An appended unrelated line.\n")
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertIn("beta", self.entries())
        self.prepare([self.gamma()])
        self.check("Gamma")
        self.fixture.write_source(SOURCE, TEXT.replace("Gamma is a distinct", "Gamma is a different"))
        with self.assertRaisesRegex(HarvestError, "source changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertNotIn("gamma", self.entries())

    def test_entry_file_edit_after_review_is_not_overwritten(self) -> None:
        prepared = prepare_captures(self.paths, [self.payload()], self.root / ".knowledge/build/captures")
        apply_ingest(self.paths, load_request(Path(prepared["artifacts"]["apply"])))
        self.prepare([self.payload(review={"action": "update", "reviewer": "tester", "evidence": "Again.",
                                           "target_id": "beta"}, text="A reviewed new explanation.")])
        self.check()
        entry = self.root / ".knowledge/entries/beta.md"
        entry.write_text(entry.read_text(encoding="utf-8").replace("Beta as reviewed.", "My own words."),
                         encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "entry changed"):
            apply_harvest(self.paths, self.sheet, self.runs)
        self.assertIn("My own words.", entry.read_text(encoding="utf-8"))

    def test_targeted_re_review_replaces_one_pending_row_and_resets_selection(self) -> None:
        prepared = self.prepare([self.payload(), self.gamma()])
        self.check()
        text = self.sheet.read_text(encoding="utf-8")
        selected = next(line for line in text.splitlines() if "[Beta (draft)]" in line)
        self.sheet.write_text(text.replace(selected, selected + " My annotation."), encoding="utf-8")
        gamma_row = next(line for line in self.sheet.read_text(encoding="utf-8").splitlines() if "[Gamma (draft)]" in line)
        corrected = self.payload(text="Beta maps an input representation.")
        self.prepare([corrected])
        after = self.sheet.read_text(encoding="utf-8")
        self.assertEqual(after.count("[Beta (draft)]"), 1)
        self.assertIn("- [ ] [Beta (draft)](../.knowledge/build/reviews/1-beta.r2.md)", after)
        self.assertIn("My annotation.", after)
        self.assertIn(gamma_row, after)
        self.assertTrue(Path(prepared["artifacts"]["drafts"][0]).exists())
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")
        self.check()
        apply_harvest(self.paths, self.sheet, self.runs)
        self.assertEqual(self.entries()["beta"]["summary"], corrected["text"])

    def test_fenced_examples_and_ordinary_tasks_are_ignored(self) -> None:
        self.prepare()
        text = self.sheet.read_text(encoding="utf-8")
        task = next(line for line in text.splitlines() if "- [ ] [Beta (draft)]" in line).replace("[ ]", "[x]", 1)
        self.sheet.write_text(text + f"\n```markdown\n{task}\n```\n\n    {task}\n\n- [x] ordinary task\n",
                              encoding="utf-8")
        self.assertEqual(apply_harvest(self.paths, self.sheet, self.runs)["status"], "nothing-selected")

    def test_binding_and_link_cannot_be_repurposed(self) -> None:
        self.prepare()
        self.check()
        self.sheet.write_text(self.sheet.read_text(encoding="utf-8").replace("[Beta (draft)]", "[Other (draft)]"),
                              encoding="utf-8")
        with self.assertRaisesRegex(HarvestError, "label or link"):
            apply_harvest(self.paths, self.sheet, self.runs)

    def test_sheet_crlf_and_unchecked_annotations_are_preserved(self) -> None:
        self.sheet.parent.mkdir(parents=True)
        self.sheet.write_bytes(b"---\r\ntitle: Reading\r\n---\r\n\r\n# Definitions\r\n\r\nMy annotation.  \r\n")
        self.prepare([self.payload(), self.gamma()])
        text = self.sheet.read_bytes().decode()
        self.assertTrue(text.startswith("---\r\ntitle: Reading\r\n---\r\n"))
        text = text.replace("- [ ] [Beta (draft)]", "- [x] [Beta (draft)]")
        self.sheet.write_bytes(text.encode())
        unchecked = next(line for line in text.splitlines(keepends=True) if "[Gamma (draft)]" in line)
        apply_harvest(self.paths, self.sheet, self.runs)
        after = self.sheet.read_bytes().decode()
        self.assertIn(unchecked, after)
        self.assertIn("My annotation.  \r\n", after)
        self.assertNotIn("\n", after.replace("\r\n", ""))

    def test_sheet_location_rules(self) -> None:
        for sheet in (self.root / ".knowledge/entries/beta.md", self.root / "sheets/defs.txt",
                      self.root / SOURCE):
            with self.subTest(sheet=sheet), self.assertRaises(HarvestError):
                prepare_harvest(self.paths, {"captures": [self.payload()]}, sheet, self.reviews)
        self.assertEqual(self.entries(), {})

    def test_cli_prepares_and_harvests(self) -> None:
        (self.root / "capture.json").write_text(json.dumps({"captures": [self.payload()]}), encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        prefix = [sys.executable, "-m", "kgdistiller", "harvest"]
        prepared = subprocess.run(prefix + ["prepare", "capture.json", "--sheet", "sheets/defs.md",
                                            "--output", ".knowledge/build/reviews", "--base", "kb"],
                                  check=False, capture_output=True, text=True, env=environment, cwd=self.root)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        sheet = Path(json.loads(prepared.stdout)["sheet"])
        sheet.write_text(sheet.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
        # Inside the base root the base is found without --base.
        applied = subprocess.run(prefix + ["apply", "sheets/defs.md", "--output", ".knowledge/build/runs"],
                                 check=False, capture_output=True, text=True, env=environment, cwd=self.root)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["status"], "committed")
        self.assertIn("beta", self.entries())


if __name__ == "__main__":
    unittest.main()
