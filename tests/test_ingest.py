from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.agent import sha256_json
from kgdistiller.alignment import empty_alignment_set
from kgdistiller.cli import (
    apply_delta,
    ensure_database,
    load_state,
    make_agent_snapshot,
    sha256_file,
    sha256_text,
    synchronize,
)
from kgdistiller.ingest import (
    CAPABILITY,
    IngestError,
    IngestPaths,
    _writer_lock,
    apply_ingest,
    finalize_request,
    plan_ingest,
    recover_ingest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class TransactionalIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-ingest-test-")
        self.repo = Path(self.temporary.name)
        self.authority = self.repo / "notes/demo/chapter.md"
        self.authority.parent.mkdir(parents=True)
        self.authority.write_text(
            "> **Definition: --[[Alpha]]--**\n>\n> Alpha is the baseline concept.\n",
            encoding="utf-8",
        )
        self.registry = self.repo / "knowledge/sources.json"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text(
            json.dumps(
                {
                    "schema": "qlkg-sources-v2",
                    "fields": [
                        {"id": "demo", "label": "Demo", "text": "Fixture field."}
                    ],
                    "sources": [
                        {
                            "id": "notes:demo",
                            "subject": "demo",
                            "course": "demo",
                            "knowledge_origin": "personal-note",
                            "fields": ["demo"],
                            "root": "notes/demo",
                            "files": ["*.md", "*.typ", "*.tex"],
                            "web": "https://example.test/demo",
                            "topics": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.graph = self.repo / "knowledge/graph"
        self.identities = self.repo / "knowledge/identities.json"
        self.alignments = self.repo / "knowledge/alignments.json"
        self.alignments.write_text(
            json.dumps(empty_alignment_set()), encoding="utf-8"
        )
        self.database = self.repo / "knowledge/build/knowledge.sqlite"
        self.typst_registry = self.repo / "knowledge/build/knowledge-registry.typ"
        self.paths = IngestPaths(
            repo_root=self.repo,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
            typst_registry=self.typst_registry,
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.database,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )
        baseline_delta = self.repo / "knowledge/build/baseline.delta.json"
        baseline_delta.write_text(
            json.dumps(
                {
                    "schema": "qlkg-agent-delta-v2",
                    "remove_nodes": [],
                    "nodes": [
                        {
                            "id": "alpha",
                            "text": "Alpha is the baseline concept.",
                        }
                    ],
                    "edges": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(
            self.graph,
            self.database,
            self.typst_registry,
            baseline_delta,
            self.alignments,
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.database,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[self.authority.relative_to(self.repo)],
            course=None,
            subject=None,
            write=True,
        )
        self.candidate = self._candidate_snapshot()
        self.candidate_path = self.repo / "knowledge/build/beta.snapshot.json"
        self.candidate_path.write_text(json.dumps(self.candidate), encoding="utf-8")
        target = make_agent_snapshot(load_state(self.graph))
        self.query_report = {
            "schema": "qlkg-graph-comparison-v1",
            "candidate": {
                "namespace": self.candidate["namespace"],
                "snapshot_sha256": self.candidate["snapshot_sha256"],
                "graph_sha256": self.candidate["graph"]["sha256"],
            },
            "target": {
                "namespace": "personal",
                "snapshot_sha256": target["snapshot_sha256"],
                "graph_sha256": target["graph"]["sha256"],
            },
            "results": [
                {
                    "candidate": {"namespace": "paper:beta", "id": "beta"},
                    "status": "new",
                    "matches": [],
                    "missing": [],
                    "conflicts": [],
                    "evidence": [],
                }
            ],
            "summary": {
                "known": 0,
                "partial": 0,
                "new": 1,
                "conflict": 0,
                "uncertain": 0,
                "total": 1,
            },
            "alignment_report_sha256": "1" * 64,
        }
        self.query_path = self.repo / "knowledge/build/beta.comparison.json"
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _candidate_snapshot(self) -> dict:
        snapshot = {
            "schema": "qlkg-agent-snapshot-v1",
            "namespace": "paper:beta",
            "graph": {
                "schema": "qlkg-v2",
                "sha256": "b" * 64,
                "counts": {"nodes": 1, "edges": 0, "references": 0},
            },
            "nodes": [
                {
                    "id": "beta",
                    "type": "knowledge",
                    "label": "Beta",
                    "text": "Beta is a source-backed candidate concept.",
                    "properties": {"aliases": []},
                    "provenance": {
                        "authority": "paper.md",
                        "line": 7,
                        "source_format": "markdown",
                    },
                }
            ],
            "edges": [],
            "references": [],
            "diagnostics": {"errors": [], "warnings": []},
        }
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        return snapshot

    def request(self, mode: str, *, request_id: str = "add-beta") -> dict:
        content = self.authority.read_text(encoding="utf-8") + (
            "\n> **Definition: --[[Beta]]--**\n>\n"
            "> Beta is a source-backed candidate concept.\n"
        )
        state = load_state(self.graph)
        return finalize_request(
            {
                "schema": "qlkg-ingest-request-v1",
                "request_id": request_id,
                "mode": mode,
                "capabilities": [CAPABILITY],
                "base_graph_sha256": state.manifest["graph_sha256"],
                "base_alignment_sha256": sha256_json(empty_alignment_set()),
                "candidate_snapshot": {
                    "path": self.candidate_path.relative_to(self.repo).as_posix(),
                    "sha256": self.candidate["snapshot_sha256"],
                },
                "query_report": {
                    "path": self.query_path.relative_to(self.repo).as_posix(),
                    "sha256": sha256_json(self.query_report),
                },
                "authority_patches": [
                    {
                        "path": self.authority.relative_to(self.repo).as_posix(),
                        "operation": "write",
                        "expected_sha256": sha256_file(self.authority),
                        "content": content,
                        "content_sha256": sha256_text(content),
                        "expected_markers": {
                            "definitions": ["alpha", "beta"],
                            "references": [],
                        },
                    }
                ],
                "decisions": [
                    {
                        "candidate_id": "beta",
                        "action": "add",
                        "target_id": "beta",
                        "evidence": "The reviewed source explicitly defines Beta.",
                    }
                ],
                "delta": {
                    "schema": "qlkg-agent-delta-v2",
                    "remove_nodes": [],
                    "nodes": [
                        {
                            "id": "beta",
                            "text": "Beta is a source-backed candidate concept.",
                        }
                    ],
                    "edges": [],
                    "remove_edges": [],
                },
                "alignment_decisions": [],
                "review": {
                    "status": "reviewed",
                    "reviewer": "fixture-reviewer",
                    "evidence": ["Beta has an explicit reviewed authority marker."],
                    "provenance": [
                        {
                            "path": "notes/demo/chapter.md",
                            "line": 5,
                            "kind": "authority",
                        }
                    ],
                },
            }
        )

    def material_hashes(self) -> dict[str, str | None]:
        values: dict[str, str | None] = {
            "authority": sha256_file(self.authority) if self.authority.is_file() else None,
            "alignments": sha256_file(self.alignments) if self.alignments.is_file() else None,
            "registry": sha256_file(self.typst_registry) if self.typst_registry.is_file() else None,
        }
        for path in sorted(self.graph.rglob("*")):
            if path.is_file():
                values[f"graph:{path.relative_to(self.graph).as_posix()}"] = sha256_file(path)
        return values

    def test_plan_is_read_only_and_predicts_valid_transaction(self) -> None:
        before = self.material_hashes()

        plan = plan_ingest(self.paths, self.request("plan"))

        self.assertEqual("qlkg-ingest-plan-v1", plan["schema"])
        self.assertEqual("planned", plan["status"])
        self.assertEqual(["beta"], plan["changes"]["nodes"]["added"])
        self.assertNotEqual(plan["before"]["graph_sha256"], plan["after"]["graph_sha256"])
        self.assertEqual(before, self.material_hashes())

    def test_apply_commits_once_and_returns_replayable_receipt(self) -> None:
        request = self.request("apply")

        first = apply_ingest(self.paths, request)
        second = apply_ingest(self.paths, request)

        self.assertEqual(first, second)
        self.assertEqual("qlkg-ingest-receipt-v1", first["schema"])
        self.assertEqual("committed", first["status"])
        self.assertEqual(["beta"], first["changes"]["nodes"]["added"])
        self.assertIn("--[[Beta]]--", self.authority.read_text(encoding="utf-8"))
        self.assertIn("beta", load_state(self.graph).nodes)
        receipt_path = (
            self.database.parent
            / "kgdistiller-ingest/receipts"
            / f"{request['request_sha256']}.json"
        )
        self.assertTrue(receipt_path.is_file())

    def test_stale_source_and_stale_graph_reject_without_writes(self) -> None:
        request = self.request("apply")
        before = self.material_hashes()
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8") + "\nUser changed this.\n",
            encoding="utf-8",
        )
        changed = self.material_hashes()
        with self.assertRaises(IngestError) as caught:
            apply_ingest(self.paths, request)
        self.assertEqual("stale-source", caught.exception.code)
        self.assertEqual(changed, self.material_hashes())

        self.authority.write_text(
            "> **Definition: --[[Alpha]]--**\n>\n> Alpha is the baseline concept.\n",
            encoding="utf-8",
        )
        stale_graph = copy.deepcopy(request)
        stale_graph["base_graph_sha256"] = "f" * 64
        stale_graph = finalize_request(stale_graph)
        with self.assertRaises(IngestError) as graph_error:
            apply_ingest(self.paths, stale_graph)
        self.assertEqual("stale-base-graph", graph_error.exception.code)
        self.assertEqual(before, self.material_hashes())

    def test_every_install_failure_restores_authority_graph_and_alignment_hashes(self) -> None:
        stages = [
            "prepared-install",
            "installed-authorities",
            "installed-alignments",
            "installed-graph",
            "installed-registry",
            "rebuilt-index",
            "receipt-written",
        ]
        baseline = self.material_hashes()
        for index, failure_stage in enumerate(stages):
            request = self.request("apply", request_id=f"failure-{index}")

            def inject(stage: str, expected: str = failure_stage) -> None:
                if stage == expected:
                    raise IngestError("injected-failure", stage, stage=stage)

            with self.assertRaises(IngestError):
                apply_ingest(self.paths, request, failure_injector=inject)
            self.assertEqual(baseline, self.material_hashes(), failure_stage)
            recover_ingest(self.paths)

    def test_lock_conflict_and_request_id_conflict_are_stable(self) -> None:
        request = self.request("apply")
        with _writer_lock(self.paths):
            with self.assertRaises(IngestError) as locked:
                apply_ingest(self.paths, request)
        self.assertEqual("lock-conflict", locked.exception.code)

        apply_ingest(self.paths, request)
        different = copy.deepcopy(request)
        different["review"]["evidence"] = ["Different reviewed evidence."]
        different = finalize_request(different)
        with self.assertRaises(IngestError) as conflict:
            apply_ingest(self.paths, different)
        self.assertEqual("request-id-conflict", conflict.exception.code)

    def test_killed_writer_is_recovered_before_retry(self) -> None:
        request = self.request("apply", request_id="crash-recovery")
        request_path = self.repo / "knowledge/build/crash.request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        environment["KGDISTILLER_INGEST_CRASH_STAGE"] = "installed-graph"

        crashed = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.repo),
                "ingest",
                "apply",
                str(request_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(86, crashed.returncode)

        receipt = apply_ingest(self.paths, request)

        self.assertEqual("committed", receipt["status"])
        self.assertIn("beta", load_state(self.graph).nodes)
        self.assertFalse(
            (self.database.parent / "kgdistiller-ingest/journal.json").exists()
        )

    def test_reader_keeps_last_complete_index_while_generation_is_installing(self) -> None:
        before = self.database.read_bytes()
        journal = self.database.parent / "kgdistiller-ingest/journal.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            json.dumps(
                {
                    "schema": "qlkg-ingest-journal-v1",
                    "request_sha256": "a" * 64,
                    "status": "installing",
                    "backup_root": str(self.repo / "unused"),
                    "targets": [],
                }
            ),
            encoding="utf-8",
        )

        rebuilt = ensure_database(
            self.database, load_state(self.graph), self.alignments
        )

        self.assertFalse(rebuilt)
        self.assertEqual(before, self.database.read_bytes())

    def test_path_traversal_marker_mismatch_and_unreviewed_identity_are_rejected(self) -> None:
        traversal = self.request("plan")
        traversal["authority_patches"][0]["path"] = "../outside.md"
        traversal = finalize_request(traversal)
        with self.assertRaises(IngestError) as unsafe:
            plan_ingest(self.paths, traversal)
        self.assertEqual("unsafe-source-path", unsafe.exception.code)

        markers = self.request("plan")
        markers["authority_patches"][0]["expected_markers"]["definitions"] = ["alpha"]
        markers = finalize_request(markers)
        with self.assertRaises(IngestError) as mismatch:
            plan_ingest(self.paths, markers)
        self.assertEqual("marker-state-mismatch", mismatch.exception.code)

        uncertain_report = copy.deepcopy(self.query_report)
        uncertain_report["results"][0]["status"] = "uncertain"
        uncertain_report["summary"]["new"] = 0
        uncertain_report["summary"]["uncertain"] = 1
        self.query_path.write_text(json.dumps(uncertain_report), encoding="utf-8")
        unresolved = self.request("plan")
        unresolved["query_report"]["sha256"] = sha256_json(uncertain_report)
        unresolved = finalize_request(unresolved)
        with self.assertRaises(IngestError) as identity:
            plan_ingest(self.paths, unresolved)
        self.assertEqual("unresolved-identity", identity.exception.code)

    def test_cli_plan_and_apply_emit_machine_readable_documents(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        plan_request = self.repo / "knowledge/build/plan.request.json"
        plan_request.write_text(json.dumps(self.request("plan")), encoding="utf-8")
        plan_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.repo),
                "ingest",
                "plan",
                str(plan_request),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, plan_result.returncode, plan_result.stderr)
        self.assertEqual("qlkg-ingest-plan-v1", json.loads(plan_result.stdout)["schema"])

        apply_request = self.repo / "knowledge/build/apply.request.json"
        apply_request.write_text(json.dumps(self.request("apply")), encoding="utf-8")
        apply_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.repo),
                "ingest",
                "apply",
                str(apply_request),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, apply_result.returncode, apply_result.stderr)
        self.assertEqual(
            "qlkg-ingest-receipt-v1", json.loads(apply_result.stdout)["schema"]
        )


if __name__ == "__main__":
    unittest.main()
