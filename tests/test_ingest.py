from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from kgdistiller.ingest import (
    CAPABILITY,
    REQUEST_SCHEMA,
    IngestError,
    apply_ingest,
    journal_path,
    load_receipt,
    plan_ingest,
    receipt_path,
    recover_ingest,
    validate_request,
    writer_lock,
)
from kgdistiller.knowledge_store import DELTA_SCHEMA, load_state
from tests.knowledge_fixture import make_fixture

SOURCE = "notes/chapter.txt"
TEXT = "Alpha is a first idea.\nBeta builds on alpha.\nGamma contrasts with beta.\n"


def request(request_id="req-1", mode="apply", **lists):
    delta = {"schema": DELTA_SCHEMA, "create_entries": [], "update_entries": [],
             "remove_entries": [], "add_edges": [], "remove_edges": []}
    delta.update(lists)
    return {
        "schema": REQUEST_SCHEMA, "request_id": request_id, "mode": mode,
        "capabilities": [CAPABILITY], "delta": delta,
        "review": {"status": "reviewed", "reviewer": "tester", "evidence": ["Read the source."],
                   "provenance": [{"source": SOURCE}]},
    }


def snapshot(root: Path) -> dict[str, bytes]:
    knowledge = root / ".knowledge"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(knowledge.rglob("*"))
        if path.is_file() and "build" not in path.relative_to(knowledge).parts
    }


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.root = self.fixture.root
        self.paths = self.fixture.paths
        self.fixture.write_source(SOURCE, TEXT)
        self.alpha = self.fixture.add_entry("alpha", "Alpha", SOURCE, 1)
        self.beta = self.fixture.entry("beta", "Beta", SOURCE, 2, kind="definition")
        self.edge = self.fixture.edge("alpha", "prerequisite-for", "beta")

    def create_beta(self, request_id="req-1", mode="apply"):
        return request(request_id, mode, create_entries=[self.beta], add_edges=[self.edge])

    def test_plan_is_read_only_and_reports_changes(self) -> None:
        before = snapshot(self.root)
        plan = plan_ingest(self.paths, self.create_beta(mode="plan"))
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(plan, {
            "schema": "kgdistiller-ingest-plan-v1", "request_id": "req-1", "status": "planned",
            "changes": {
                "entries_created": ["beta"], "entries_updated": [], "entries_removed": [],
                "aliases_changed": [],
                "edges_added": [{"source": "alpha", "relation": "prerequisite-for", "target": "beta"}],
                "edges_removed": [],
            },
            "counts": {"before": {"entries": 1, "edges": 0}, "after": {"entries": 2, "edges": 1}},
        })
        with self.assertRaisesRegex(IngestError, "does not match"):
            plan_ingest(self.paths, self.create_beta(mode="apply"))

    def test_apply_installs_entries_and_edges_with_a_readable_receipt(self) -> None:
        receipt = apply_ingest(self.paths, self.create_beta())
        state = load_state(self.root)
        self.assertEqual(state.entries["beta"], self.beta)
        self.assertEqual(list(state.edges), [("alpha", "prerequisite-for", "beta")])
        stored = receipt_path(self.paths, "req-1")
        self.assertEqual(stored.name, "req-1.json")
        self.assertEqual(json.loads(stored.read_text(encoding="utf-8")), receipt)
        self.assertEqual(set(receipt), {"schema", "request_id", "status", "request", "changes", "counts"})
        self.assertEqual(receipt["counts"], {"entries": 2, "edges": 1})
        self.assertEqual(receipt["request"], self.create_beta())
        self.assertFalse(journal_path(self.paths).exists())
        self.assertFalse((self.root / ".knowledge/build/kgdistiller-ingest/backups/req-1").exists())
        self.assertEqual(set(receipt["changes"]), {
            "entries_created", "entries_updated", "entries_removed", "aliases_changed",
            "edges_added", "edges_removed"})

    def test_replay_and_request_conflict(self) -> None:
        receipt = apply_ingest(self.paths, self.create_beta())
        state = snapshot(self.root)
        self.assertEqual(apply_ingest(self.paths, self.create_beta()), receipt)
        self.assertEqual(snapshot(self.root), state)
        changed = self.create_beta()
        changed["review"]["reviewer"] = "someone else"
        with self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, changed)
        self.assertEqual(caught.exception.code, "request-conflict")

    def test_lock_conflict(self) -> None:
        with writer_lock(self.paths), self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, self.create_beta())
        self.assertEqual(caught.exception.code, "lock-conflict")
        self.assertNotIn("beta", load_state(self.root).entries)

    def test_apply_revalidates_against_the_current_store(self) -> None:
        apply_ingest(self.paths, self.create_beta("seed"))
        beta = load_state(self.root).entries["beta"]
        gamma = self.fixture.entry("gamma", "Gamma", SOURCE, 3)
        cases = [
            ("invalid-request", lambda: {**request("bad"), "delta": {"schema": DELTA_SCHEMA}}),
            ("missing-entry", lambda: request(update_entries=[{"expected_label": "Ghost",
                                                               "entry": {**gamma, "id": "ghost"}}])),
            ("label-mismatch", lambda: request(update_entries=[{"expected_label": "Old beta", "entry": beta}])),
            ("label-mismatch", lambda: request(remove_entries=[{"id": "beta", "expected_label": "Old beta"}])),
            ("entry-exists", lambda: request(create_entries=[beta])),
            ("identity-collision", lambda: request(create_entries=[{**gamma, "aliases": ["BETA"]}])),
            ("missing-source", lambda: request(create_entries=[{**gamma, "source": "notes/gone.txt"}])),
            ("source-not-registered", lambda: request(create_entries=[{**gamma, "source": "loose.txt"}])),
            ("line-range", lambda: request(create_entries=[{**gamma, "line_start": 9, "line_end": 9}])),
            ("stale-evidence", lambda: request(create_entries=[{**gamma, "evidence": "Gamma was rewritten."}])),
            ("dangling-edge", lambda: request(remove_entries=[{"id": "beta", "expected_label": "Beta"}])),
            ("cycle", lambda: request(add_edges=[self.fixture.edge("beta", "prerequisite-for", "alpha")])),
        ]
        (self.root / "loose.txt").write_text(TEXT, encoding="utf-8")
        for code, build in cases:
            with self.subTest(code=code):
                before = snapshot(self.root)
                with self.assertRaises(IngestError) as caught:
                    apply_ingest(self.paths, build())
                self.assertEqual(caught.exception.code, code, str(caught.exception))
                self.assertEqual(snapshot(self.root), before)
                self.assertIsNone(load_receipt(self.paths, "req-1"))

    def test_kind_not_allowed_and_source_edits_between_plan_and_apply(self) -> None:
        plan_ingest(self.paths, self.create_beta(mode="plan"))
        self.fixture.write_source(SOURCE, TEXT.replace("Beta builds", "Beta now builds"))
        with self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, self.create_beta())
        self.assertEqual(caught.exception.code, "stale-evidence")
        self.fixture.write_source(SOURCE, TEXT)
        self.fixture.document_types = {"notes": {"node_kinds": ["concept"], "extraction_guidance": "Ideas."}}
        self.fixture.sources[0]["document_type"] = "notes"
        self.fixture.write_registry()
        with self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, self.create_beta())
        self.assertEqual(caught.exception.code, "kind-not-allowed")

    def test_update_keeps_other_entries_and_remove_deletes_the_file(self) -> None:
        apply_ingest(self.paths, self.create_beta("seed"))
        alpha_file = (self.root / ".knowledge/entries/alpha.md").read_bytes()
        updated = {**load_state(self.root).entries["beta"], "summary": "Beta, reviewed again.",
                   "understanding": "understood"}
        receipt = apply_ingest(self.paths, request("update", update_entries=[{"expected_label": "Beta", "entry": updated}]))
        self.assertEqual(receipt["changes"]["entries_updated"], ["beta"])
        self.assertEqual((self.root / ".knowledge/entries/alpha.md").read_bytes(), alpha_file)
        apply_ingest(self.paths, request(
            "remove", remove_entries=[{"id": "beta", "expected_label": "Beta"}],
            remove_edges=[{"source": "alpha", "relation": "prerequisite-for", "target": "beta"}]))
        self.assertFalse((self.root / ".knowledge/entries/beta.md").exists())
        self.assertEqual((self.root / ".knowledge/edges.jsonl").read_text(encoding="utf-8"), "")

    def test_install_failure_rolls_back(self) -> None:
        apply_ingest(self.paths, self.create_beta("seed"))
        before = snapshot(self.root)
        updated = {**load_state(self.root).entries["beta"], "summary": "Changed."}
        gamma = self.fixture.entry("gamma", "Gamma", SOURCE, 3)

        def fail(stage: str) -> None:
            if stage == "installed-entry":
                raise OSError("disk full")

        with self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, request(update_entries=[{"expected_label": "Beta", "entry": updated}],
                                             create_entries=[gamma]), failure_injector=fail)
        self.assertEqual(caught.exception.code, "install-failed")
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(journal_path(self.paths).exists())
        self.assertIsNone(load_receipt(self.paths, "req-1"))

    def test_receipt_failure_rolls_back(self) -> None:
        before = snapshot(self.root)

        def fail(stage: str) -> None:
            if stage == "receipt-written":
                raise OSError("interrupted")

        with self.assertRaises(IngestError):
            apply_ingest(self.paths, self.create_beta(), failure_injector=fail)
        self.assertEqual(snapshot(self.root), before)
        self.assertIsNone(load_receipt(self.paths, "req-1"))

    def test_crash_is_recovered_before_the_next_write(self) -> None:
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(self.create_beta()), encoding="utf-8")
        environment = dict(os.environ, KGDISTILLER_INGEST_CRASH_STAGE="installed-entry",
                           PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        crashed = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root), "ingest", "apply", "request.json"],
            capture_output=True, text=True, env=environment, check=False)
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        self.assertTrue(journal_path(self.paths).exists())
        self.assertTrue((self.root / ".knowledge/entries/beta.md").exists())
        with self.assertRaises(IngestError) as caught:
            plan_ingest(self.paths, self.create_beta(mode="plan"))
        self.assertEqual(caught.exception.code, "lock-conflict")
        with writer_lock(self.paths):
            self.assertEqual(recover_ingest(self.paths), {"request_id": "req-1", "status": "rolled-back"})
        self.assertFalse((self.root / ".knowledge/entries/beta.md").exists())
        receipt = apply_ingest(self.paths, self.create_beta())
        self.assertEqual(receipt["changes"]["entries_created"], ["beta"])

    def test_recovery_rejects_unmanaged_journal_targets(self) -> None:
        notes = self.root / SOURCE
        before = notes.read_bytes()
        for target in (SOURCE, ".knowledge/sources.json", ".knowledge/entries/../sources.json",
                       ".knowledge/entries/Bad.md"):
            with self.subTest(target=target):
                journal_path(self.paths).parent.mkdir(parents=True, exist_ok=True)
                journal_path(self.paths).write_text(json.dumps({
                    "schema": "kgdistiller-ingest-journal-v1", "request_id": "evil",
                    "status": "installing", "targets": [{"path": target, "existed": False}],
                }), encoding="utf-8")
                with self.assertRaises(IngestError) as caught:
                    apply_ingest(self.paths, self.create_beta())
                self.assertEqual(caught.exception.code, "install-failed")
                self.assertEqual(notes.read_bytes(), before)
                self.assertTrue((self.root / ".knowledge/sources.json").is_file())
        journal_path(self.paths).unlink()

    def test_request_validation(self) -> None:
        valid = self.create_beta()
        validate_request(valid)
        broken = []
        for mutate in (
            lambda r: r.update(request_id="bad:id"),
            lambda r: r.update(request_id="con"),
            lambda r: r.update(capabilities=[]),
            lambda r: r["review"].update(status="draft"),
            lambda r: r.update(decisions=[]),
            lambda r: r["delta"].update(nodes=[]),
            lambda r: r["delta"]["create_entries"][0].update(entry_origin="agent"),
        ):
            value = copy.deepcopy(valid)
            mutate(value)
            broken.append(value)
        for value in broken:
            with self.subTest(value=value), self.assertRaises(IngestError) as caught:
                validate_request(value)
            self.assertEqual(caught.exception.code, "invalid-request")

    def test_cli_plan_and_apply(self) -> None:
        for mode in ("plan", "apply"):
            (self.root / f"{mode}.json").write_text(json.dumps(self.create_beta(mode=mode)), encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        base = [sys.executable, "-m", "kgdistiller", "--repo-root", str(self.root), "ingest"]
        planned = subprocess.run(base + ["plan", "plan.json"], capture_output=True, text=True,
                                 env=environment, check=False)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(planned.stdout)["status"], "planned")
        applied = subprocess.run(base + ["apply", "apply.json", "--receipt", "receipt.json"],
                                 capture_output=True, text=True, env=environment, check=False)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["request_id"], "req-1")
        self.assertEqual(json.loads((self.root / "receipt.json").read_text())["status"], "committed")
        failed = subprocess.run(base + ["apply", "plan.json"], capture_output=True, text=True,
                                env=environment, check=False)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(failed.stderr)["error"]["code"], "invalid-request")


if __name__ == "__main__":
    unittest.main()
