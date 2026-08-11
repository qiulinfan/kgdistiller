from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import kgdistiller.agent as agent
import kgdistiller.ingest as ingest_module
from kgdistiller.agent import resolve_agent_index_path, sha256_json
from kgdistiller.alignment import empty_alignment_set
from kgdistiller.cli import (
    apply_delta,
    ensure_database,
    load_state,
    make_agent_snapshot,
    sha256_authority_file,
    sha256_file,
    sha256_text,
    synchronize,
)
from kgdistiller.ingest import (
    CAPABILITY,
    IngestError,
    IngestPaths,
    _backup_target,
    _filesystem_path,
    _remove_target,
    _restore_journal,
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
                        "expected_sha256": sha256_authority_file(self.authority),
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

    def test_plan_accepts_reviewed_hash_across_crlf_checkout(self) -> None:
        request = self.request("plan")
        expected = request["authority_patches"][0]["expected_sha256"]
        text = self.authority.read_text(encoding="utf-8")
        self.authority.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        self.assertEqual(expected, sha256_authority_file(self.authority))
        self.assertNotEqual(expected, sha256_file(self.authority))
        plan = plan_ingest(self.paths, request)
        self.assertEqual(
            expected,
            plan["before"]["source_hashes"]["notes/demo/chapter.md"],
        )

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

    def test_first_ingest_succeeds_without_an_existing_agent_index(self) -> None:
        _remove_target(self.database)
        _remove_target(agent._index_generation_root(self.database))
        self.assertFalse(agent.agent_index_exists(self.database))

        receipt = apply_ingest(
            self.paths,
            self.request("apply", request_id="first-index"),
        )

        self.assertEqual("committed", receipt["status"])
        self.assertTrue(agent.agent_index_exists(self.database))

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

    def test_long_transaction_backup_restores_with_canonical_journal_paths(self) -> None:
        backup_container = (
            self.database.parent
            / ("long-backup-" + "x" * 180)
        )
        backup_root = backup_container / ("a" * 64)
        try:
            record = _backup_target(self.repo, self.graph, backup_root)
            backed_up_graph = backup_root / record["path"]
            self.assertGreater(len(str(backed_up_graph)), 260)
            self.assertEqual("knowledge/graph", record["path"])
            self.assertNotIn("\\\\?\\", str(backup_root))
            if os.name == "nt":
                self.assertTrue(
                    str(_filesystem_path(backup_root)).startswith("\\\\?\\")
                )
            else:
                self.assertEqual(backup_root, _filesystem_path(backup_root))

            journal = {
                "backup_root": str(backup_root),
                "targets": [record],
            }
            _remove_target(self.graph)
            self.assertFalse(_filesystem_path(self.graph).exists())
            _restore_journal(self.paths, journal)
            self.assertIn("alpha", load_state(self.graph).nodes)
        finally:
            shutil.rmtree(_filesystem_path(backup_container), ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows extended-length I/O is required")
    def test_writer_lock_supports_a_long_agent_index_state_path(self) -> None:
        long_root = self.repo / ("long-writer-lock-" + "x" * 180)
        database = long_root / ("nested-" + "y" * 80) / "knowledge.sqlite"
        paths = IngestPaths(
            repo_root=self.paths.repo_root,
            registry=self.paths.registry,
            graph_dir=self.paths.graph_dir,
            identities=self.paths.identities,
            alignments=self.paths.alignments,
            database=database,
            typst_registry=self.paths.typst_registry,
        )
        self.assertGreater(len(str(database)), 260)
        try:
            with _writer_lock(paths):
                pass
        finally:
            shutil.rmtree(_filesystem_path(long_root), ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows extended-length I/O is required")
    def test_long_agent_index_backup_remove_and_restore_is_readable(self) -> None:
        long_root = self.repo / ("long-index-recovery-" + "x" * 180)
        database = long_root / ("nested-" + "y" * 80) / "knowledge.sqlite"
        paths = IngestPaths(
            repo_root=self.paths.repo_root,
            registry=self.paths.registry,
            graph_dir=self.paths.graph_dir,
            identities=self.paths.identities,
            alignments=self.paths.alignments,
            database=database,
            typst_registry=self.paths.typst_registry,
        )
        self.assertGreater(len(str(database)), 260)
        filesystem_database = _filesystem_path(database)
        filesystem_database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(filesystem_database))
        try:
            connection.execute(
                "CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO index_meta VALUES (?, ?)",
                [
                    ("schema", json.dumps(agent.INDEX_SCHEMA)),
                    ("sentinel", json.dumps("long-last-good")),
                ],
            )
            connection.commit()
        finally:
            connection.close()

        backup_root = database.parent / "kgdistiller-ingest/backups/long-restore"
        try:
            record = _backup_target(
                self.repo,
                database,
                backup_root,
                kind="agent-index",
            )
            self.assertEqual(database.relative_to(self.repo).as_posix(), record["path"])
            self.assertNotIn("\\\\?\\", record["path"])
            self.assertNotIn("\\\\?\\", str(backup_root))

            agent.remove_agent_index(database)
            self.assertFalse(agent.agent_index_exists(database))
            try:
                _restore_journal(
                    paths,
                    {
                        "backup_root": str(backup_root),
                        "request_sha256": "long-restore",
                        "targets": [record],
                    },
                )
            except IngestError as error:
                self.fail(f"long-path restore failed: {error.diagnostics}")

            self.assertEqual("long-last-good", agent.index_status(database)["sentinel"])
        finally:
            shutil.rmtree(_filesystem_path(long_root), ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows extended-length I/O is required")
    def test_apply_ingest_supports_a_long_absent_index_end_to_end(self) -> None:
        long_root = self.repo / ("long-apply-" + "x" * 180)
        database = long_root / ("nested-" + "y" * 80) / "knowledge.sqlite"
        paths = IngestPaths(
            repo_root=self.paths.repo_root,
            registry=self.paths.registry,
            graph_dir=self.paths.graph_dir,
            identities=self.paths.identities,
            alignments=self.paths.alignments,
            database=database,
            typst_registry=self.paths.typst_registry,
        )
        request = self.request("apply", request_id="long-absent-apply")
        self.assertGreater(len(str(database)), 260)
        self.assertFalse(agent.agent_index_exists(database))

        try:
            try:
                receipt = apply_ingest(paths, request)
            except Exception as error:
                self.fail(
                    "long-path apply failed before committing a readable index: "
                    f"{type(error).__name__}: {error}"
                )

            self.assertEqual("committed", receipt["status"])
            connection = agent.open_agent_index(database)
            try:
                self.assertEqual(
                    ("beta",),
                    tuple(
                        connection.execute(
                            "SELECT id FROM nodes WHERE id = ?", ("beta",)
                        ).fetchone()
                    ),
                )
            finally:
                connection.close()
            receipt_path = (
                database.parent
                / "kgdistiller-ingest/receipts"
                / f"{request['request_sha256']}.json"
            )
            self.assertTrue(_filesystem_path(receipt_path).is_file())
            persisted = _filesystem_path(receipt_path).read_text(encoding="utf-8")
            self.assertNotIn("\\\\?\\", persisted)
            self.assertNotIn("\\\\?\\", json.dumps(receipt))
            self.assertFalse(
                _filesystem_path(
                    database.parent / "kgdistiller-ingest/journal.json"
                ).exists()
            )
        finally:
            shutil.rmtree(_filesystem_path(long_root), ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows extended-length I/O is required")
    def test_long_apply_failure_after_rebuild_recovers_existing_index(self) -> None:
        long_root = self.repo / ("long-apply-recovery-" + "x" * 170)
        database = long_root / ("nested-" + "y" * 80) / "knowledge.sqlite"
        paths = IngestPaths(
            repo_root=self.paths.repo_root,
            registry=self.paths.registry,
            graph_dir=self.paths.graph_dir,
            identities=self.paths.identities,
            alignments=self.paths.alignments,
            database=database,
            typst_registry=self.paths.typst_registry,
        )
        self.assertGreater(len(str(database)), 260)
        agent.backup_agent_index(self.database, database)
        before_status = agent.index_status(database)
        baseline = self.material_hashes()
        request = self.request("apply", request_id="long-rebuild-rollback")

        def inject(stage: str) -> None:
            if stage == "rebuilt-index":
                raise IngestError("injected-failure", stage, stage=stage)

        try:
            with self.assertRaises(IngestError) as caught:
                apply_ingest(paths, request, failure_injector=inject)
            self.assertEqual("injected-failure", caught.exception.code)
            journal_path = database.parent / "kgdistiller-ingest/journal.json"
            self.assertTrue(_filesystem_path(journal_path).is_file())
            journal_text = _filesystem_path(journal_path).read_text(encoding="utf-8")
            self.assertNotIn("\\\\?\\", journal_text)

            recovered = recover_ingest(paths)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual("rolled-back", recovered["status"])
            self.assertFalse(_filesystem_path(journal_path).exists())
            self.assertEqual(baseline, self.material_hashes())
            self.assertEqual(
                before_status["graph_sha256"],
                agent.index_status(database)["graph_sha256"],
            )
            connection = agent.open_agent_index(database)
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT id FROM nodes WHERE id = ?", ("beta",)
                    ).fetchone()
                )
            finally:
                connection.close()
        finally:
            shutil.rmtree(_filesystem_path(long_root), ignore_errors=True)

    def test_restore_journal_continues_after_agent_index_error(self) -> None:
        backup_root = self.database.parent / "kgdistiller-ingest/backups/mixed-error"
        records = [
            _backup_target(self.repo, self.authority, backup_root),
            _backup_target(self.repo, self.graph, backup_root),
            _backup_target(self.repo, self.alignments, backup_root),
            _backup_target(
                self.repo,
                self.database,
                backup_root,
                kind="agent-index",
            ),
        ]
        baseline = self.material_hashes()
        self.authority.write_text("mutated authority\n", encoding="utf-8")
        _remove_target(self.graph)
        self.graph.mkdir(parents=True)
        (self.graph / "mutated.json").write_text("{}", encoding="utf-8")
        self.alignments.write_text("{}", encoding="utf-8")
        real_atomic_copy = ingest_module._atomic_copy

        def fail_only_agent_index(
            source: Path, target: Path, *, agent_index: bool = False
        ) -> None:
            if agent_index:
                raise agent.AgentIndexError("injected Agent index restore failure")
            real_atomic_copy(source, target, agent_index=agent_index)

        try:
            with patch.object(
                ingest_module,
                "_atomic_copy",
                side_effect=fail_only_agent_index,
            ):
                with self.assertRaises(IngestError) as caught:
                    _restore_journal(
                        self.paths,
                        {
                            "backup_root": str(backup_root),
                            "request_sha256": "mixed-agent-index-error",
                            "targets": records,
                        },
                    )
            self.assertEqual("rollback-failed", caught.exception.code)
            self.assertEqual(baseline, self.material_hashes())
            self.assertTrue(
                any(
                    "injected Agent index restore failure"
                    in str(item.get("message", ""))
                    for item in caught.exception.diagnostics
                )
            )
        finally:
            shutil.rmtree(_filesystem_path(backup_root), ignore_errors=True)

    def test_restore_journal_collects_receipt_unlink_error(self) -> None:
        request_sha = "receipt-unlink-error"
        receipt = (
            self.database.parent
            / "kgdistiller-ingest/receipts"
            / f"{request_sha}.json"
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("{}", encoding="utf-8")
        real_unlink = Path.unlink

        def deny_receipt_unlink(
            candidate: Path, *args: object, **kwargs: object
        ) -> None:
            if candidate.name == receipt.name and candidate.parent.name == "receipts":
                raise PermissionError("injected receipt unlink denial")
            real_unlink(candidate, *args, **kwargs)

        with patch.object(Path, "unlink", new=deny_receipt_unlink):
            with self.assertRaises(IngestError) as caught:
                _restore_journal(
                    self.paths,
                    {
                        "backup_root": str(self.repo / "unused-backup"),
                        "request_sha256": request_sha,
                        "targets": [],
                    },
                )

        self.assertEqual("rollback-failed", caught.exception.code)
        self.assertTrue(_filesystem_path(receipt).is_file())
        self.assertTrue(
            any(
                "injected receipt unlink denial" in str(item.get("message", ""))
                for item in caught.exception.diagnostics
            )
        )

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

    def test_writer_lock_conflicts_across_processes(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        ready_path = self.repo / "knowledge/build/writer-lock.ready"
        release_path = self.repo / "knowledge/build/writer-lock.release"
        holder_code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from kgdistiller.ingest import IngestPaths, _writer_lock\n"
            "repo = Path(sys.argv[1])\n"
            "ready = Path(sys.argv[2])\n"
            "release = Path(sys.argv[3])\n"
            "paths = IngestPaths(\n"
            "    repo_root=repo,\n"
            "    registry=repo / 'knowledge/sources.json',\n"
            "    graph_dir=repo / 'knowledge/graph',\n"
            "    identities=repo / 'knowledge/identities.json',\n"
            "    alignments=repo / 'knowledge/alignments.json',\n"
            "    database=repo / 'knowledge/build/knowledge.sqlite',\n"
            "    typst_registry=repo / 'knowledge/build/knowledge-registry.typ',\n"
            ")\n"
            "with _writer_lock(paths):\n"
            "    ready.touch()\n"
            "    deadline = time.monotonic() + 20.0\n"
            "    while not release.exists():\n"
            "        if time.monotonic() >= deadline:\n"
            "            raise RuntimeError('release deadline expired')\n"
            "        time.sleep(0.01)\n"
        )
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                holder_code,
                str(self.repo),
                str(ready_path),
                str(release_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        holder_stdout = ""
        holder_error = ""
        try:
            deadline = time.monotonic() + 10.0
            while not ready_path.exists() and time.monotonic() < deadline:
                if holder.poll() is not None:
                    break
                time.sleep(0.01)
            if not ready_path.exists():
                if holder.poll() is None:
                    holder.kill()
                holder_stdout, holder_error = holder.communicate(timeout=5)
                self.fail(
                    "lock holder did not become ready: "
                    f"exit={holder.returncode} stdout={holder_stdout} "
                    f"stderr={holder_error}"
                )

            with self.assertRaises(IngestError) as locked:
                apply_ingest(self.paths, self.request("apply"))
            self.assertEqual("lock-conflict", locked.exception.code)
        finally:
            release_path.touch(exist_ok=True)
            try:
                holder_stdout, holder_error = holder.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder_stdout, holder_error = holder.communicate(timeout=5)
        self.assertEqual(
            0,
            holder.returncode,
            f"stdout={holder_stdout} stderr={holder_error}",
        )

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
            timeout=30,
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

    def test_open_reader_survives_transaction_generation_swap(self) -> None:
        reader = sqlite3.connect(resolve_agent_index_path(self.database))
        transaction_error: Exception | None = None
        try:
            self.assertIsNone(
                reader.execute(
                    "SELECT id FROM nodes WHERE id = ?", ("beta",)
                ).fetchone()
            )

            receipt = apply_ingest(self.paths, self.request("apply"))

            self.assertEqual("committed", receipt["status"])
            self.assertIsNone(
                reader.execute(
                    "SELECT id FROM nodes WHERE id = ?", ("beta",)
                ).fetchone()
            )
        except Exception as error:
            transaction_error = error
        finally:
            reader.close()

        if transaction_error is not None:
            recover_ingest(self.paths)
            self.fail(
                "an open reader blocked the transaction generation swap: "
                f"{type(transaction_error).__name__}: {transaction_error}"
            )

        current_reader = sqlite3.connect(resolve_agent_index_path(self.database))
        try:
            self.assertEqual(
                ("beta",),
                current_reader.execute(
                    "SELECT id FROM nodes WHERE id = ?", ("beta",)
                ).fetchone(),
            )
        finally:
            current_reader.close()

    def test_open_reader_survives_transaction_generation_rollback(self) -> None:
        reader = sqlite3.connect(resolve_agent_index_path(self.database))
        try:
            self.assertIsNone(
                reader.execute(
                    "SELECT id FROM nodes WHERE id = ?", ("beta",)
                ).fetchone()
            )

            def inject(stage: str) -> None:
                if stage == "rebuilt-index":
                    raise IngestError("injected-failure", stage, stage=stage)

            with self.assertRaises(IngestError) as caught:
                apply_ingest(
                    self.paths,
                    self.request("apply"),
                    failure_injector=inject,
                )
            self.assertEqual("injected-failure", caught.exception.code)
            journal = json.loads(
                (
                    self.database.parent
                    / "kgdistiller-ingest/journal.json"
                ).read_text(encoding="utf-8")
            )
            database_record = next(
                record
                for record in journal["targets"]
                if record["kind"] == "agent-index"
            )
            self.assertEqual("knowledge/build/knowledge.sqlite", database_record["path"])
            self.assertNotIn(".generations", database_record["path"])
            self.assertIsNone(
                reader.execute(
                    "SELECT id FROM nodes WHERE id = ?", ("beta",)
                ).fetchone()
            )

            current_reader = sqlite3.connect(resolve_agent_index_path(self.database))
            try:
                self.assertIsNone(
                    current_reader.execute(
                        "SELECT id FROM nodes WHERE id = ?", ("beta",)
                    ).fetchone()
                )
            finally:
                current_reader.close()
        finally:
            reader.close()
            recover_ingest(self.paths)

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
            timeout=30,
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
            timeout=30,
        )
        self.assertEqual(0, apply_result.returncode, apply_result.stderr)
        self.assertEqual(
            "qlkg-ingest-receipt-v1", json.loads(apply_result.stdout)["schema"]
        )


if __name__ == "__main__":
    unittest.main()
