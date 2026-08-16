from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import kgdistiller.vault_ingest as vault_ingest_module
import kgdistiller.native_compiler as native_compiler_module
import kgdistiller.cli as cli_module
import kgdistiller.source_archive as source_archive_module
from kgdistiller.contracts import (
    canonical_json,
    finalize_self_digest,
    sha256_json,
    validate_contract,
)
from kgdistiller.native_compiler import check_knowledge, sync_knowledge
from kgdistiller.native_notes import NativeNoteError, merge_native_note_bytes, parse_native_markdown
from kgdistiller.query import GraphView
from kgdistiller.recall import recall_status
from kgdistiller.source_archive import (
    SourceArchiveError,
    capture_source,
    current_evidence_view,
    diff_source,
    load_source_ledger,
    source_status,
)
from kgdistiller.vault_ingest import (
    CAPABILITY,
    REQUEST_SCHEMA,
    VaultIngestError,
    apply_vault_ingest,
    apply_vault_ingest_report,
    plan_vault_ingest,
    plan_vault_ingest_report,
    recover_vault_ingest,
    receipt_relative_path,
    write_ingest_artifact,
)
from kgdistiller.vaults import (
    init_vault,
    load_registry,
    load_vault,
    managed_markdown_token,
    snapshot_managed_markdown,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _concept_note(node_id: str, label: str, body: str) -> str:
    return "\n".join(
        [
            "---",
            "# user preamble remains",
            "kgd_schema: qlkg-concept-v1",
            f"kgd_id: {node_id}",
            "aliases: []",
            "tags: [kgdistiller/concept] # user inline remains",
            'kgd_fields: ["[[Knowledge/Fields/Test]]"]',
            "kgd_topics: []",
            "# user relation comment remains",
            "kgd_prerequisites: []",
            "kgd_implies: []",
            "kgd_generalizes: []",
            "kgd_contrasts_with: []",
            "kgd_derived_from: []",
            'user_setting: "yes"',
            "user_flow: [a, b]",
            "---",
            "",
            f"# {label}",
            "",
            body,
            "",
        ]
    )


class _Crash(BaseException):
    pass


class VaultIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-vault-ingest-")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "machine-home"
        self.vault_root = self.root / "Vault"
        init_vault(
            self.vault_root,
            vault_id="test",
            label="Test Vault",
            home=self.home,
        )
        self.note = self.vault_root / "Knowledge/Concepts/Alpha.md"
        (self.vault_root / "Knowledge/Fields/Test.md").write_text(
            "\n".join(
                [
                    "---",
                    "kgd_schema: qlkg-taxonomy-v1",
                    "kgd_id: test-field",
                    "kgd_kind: field",
                    "aliases: []",
                    "kgd_parents: []",
                    "---",
                    "",
                    "# Test field",
                    "",
                    "A test field.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.note.write_text(
            _concept_note("alpha", "Alpha", "Old reviewed body."), encoding="utf-8"
        )
        sync_knowledge(home=self.home)
        self.query = self.root / "query.json"
        self._refresh_query_report()

    def _refresh_query_report(self) -> None:
        self.query.write_bytes(
            canonical_json(
                recall_status(home=self.home, vault_ids=("test",))
            ).encode("utf-8")
            + b"\n"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request(
        self,
        patches: list[dict],
        *,
        updates: list[dict] | None = None,
        request_id: str = "request-1",
        refresh_query: bool = True,
    ) -> dict:
        if refresh_query:
            self._refresh_query_report()
        registry = load_registry(self.home, validate_vaults=False)
        vault = load_vault(self.vault_root, expected_id="test")
        ledger = load_source_ledger(vault)
        graph_manifest = json.loads(
            (self.vault_root / ".kgdistiller/graph/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        note_token = sha256_json(
            [list(item) for item in managed_markdown_token(snapshot_managed_markdown(vault))]
        )
        return finalize_self_digest(
            {
                "schema": REQUEST_SCHEMA,
                "request_id": request_id,
                "request_sha256": "0" * 64,
                "capabilities": [CAPABILITY],
                "vault_id": "test",
                "registry_generation": registry.generation,
                "vault_manifest_sha256": sha256_json(vault.manifest),
                "base": {
                    "source_ledger_generation_sha256": ledger.generation_sha256,
                    "graph_generation_sha256": graph_manifest["graph_sha256"],
                    "note_inventory_sha256": note_token,
                },
                "query_report": {
                    "path": "query.json",
                    "sha256": _sha256(self.query.read_bytes()),
                },
                "note_patches": patches,
                "derivation_updates": updates or [],
                "alignment_mutations": [],
                "review": {
                    "status": "reviewed",
                    "reviewer": "unit-test",
                    "evidence": "human reviewed the exact native note update",
                    "provenance": "tests/test_vault_ingest.py",
                },
            },
            "request_sha256",
        )

    def _query_payload(self) -> dict:
        return json.loads(self.query.read_text(encoding="utf-8"))

    def _write_query_payload(self, payload: dict) -> None:
        validate_contract(payload)
        self.query.write_bytes(canonical_json(payload).encode("utf-8") + b"\n")

    @staticmethod
    def _set_report_generation(payload: dict) -> None:
        payload["generation"] = sha256_json(
            {
                "registry_generation": payload["registry_generation"],
                "vaults": [
                    {
                        "vault_id": item["vault_id"],
                        "generation": item["generation"],
                    }
                    for item in payload["vaults"]
                ],
                "incomplete_vaults": [
                    {"vault_id": item["vault_id"], "code": item["code"]}
                    for item in payload["incomplete_vaults"]
                ],
            }
        )

    def _write_patch(self, content: str, *, path: str = "Knowledge/Concepts/Alpha.md") -> dict:
        data = content.encode("utf-8")
        current = self.vault_root / Path(path)
        return {
            "path": path,
            "operation": "write",
            "expected_raw_sha256": _sha256(current.read_bytes()) if current.exists() else None,
            "content": content,
            "content_sha256": _sha256(data),
        }

    def _tree(self) -> tuple[set[str], dict[str, bytes]]:
        directories: set[str] = set()
        files: dict[str, bytes] = {}
        for path in self.vault_root.rglob("*"):
            relative = path.relative_to(self.vault_root).as_posix()
            if path.is_dir():
                directories.add(relative)
            elif path.is_file():
                files[relative] = path.read_bytes()
        return directories, files

    def _visible_tree(self) -> tuple[set[str], dict[str, bytes]]:
        directories, files = self._tree()
        immutable_prefix = ".kgdistiller/sources/generations/"
        return (
            {item for item in directories if not item.startswith(immutable_prefix)},
            {
                path: data
                for path, data in files.items()
                if not path.startswith(immutable_prefix)
            },
        )

    @staticmethod
    def _subtree(root: Path) -> tuple[set[str], dict[str, bytes]]:
        directories: set[str] = set()
        files: dict[str, bytes] = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                directories.add(relative)
            elif path.is_file():
                files[relative] = path.read_bytes()
        return directories, files

    def _request_file(self, request: dict, *, name: str = "request.json") -> Path:
        path = self.root / name
        path.write_bytes(canonical_json(request).encode("utf-8") + b"\n")
        return path

    def _reviewed_empty_request(self, *, request_id: str) -> dict:
        source = self.vault_root / "notes/empty-review.md"
        source.parent.mkdir(exist_ok=True)
        source.write_text("No retained knowledge.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("87654321-4321-4321-8321-cba987654321"),
        )
        return self._request(
            [],
            updates=[
                {
                    "version_id": captured["result"]["current_version_id"],
                    "status": "reviewed-empty",
                    "candidate_dispositions": [],
                    "concept_ids": [],
                    "concept_evidence": [],
                    "relation_evidence": [],
                }
            ],
            request_id=request_id,
        )

    def _crash_during_live_temporary(
        self, request: dict, *, occurrence: int, name: str
    ) -> subprocess.CompletedProcess[str]:
        request_path = self._request_file(request, name=name)
        program = "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "from kgdistiller.vault_ingest import apply_vault_ingest",
                "seen = 0",
                "def die(label):",
                "    global seen",
                "    if label == 'after-live-temp-fsync':",
                "        seen += 1",
                "        if seen == int(sys.argv[3]):",
                "            os._exit(91)",
                "apply_vault_ingest(Path(sys.argv[1]), home=Path(sys.argv[2]), failure_injector=die)",
            ]
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                os.fspath(request_path),
                os.fspath(self.home),
                str(occurrence),
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(91, result.returncode, result.stderr)
        return result

    def test_query_report_must_be_a_closed_recall_report(self) -> None:
        self.query.write_bytes(b'{"schema":"test-query-report"}\n')
        request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Invalid query report body.")
                )
            ],
            request_id="invalid-query-report",
            refresh_query=False,
        )
        before = self._tree()
        with self.assertRaises(VaultIngestError) as rejected:
            plan_vault_ingest(
                self._request_file(request, name="invalid-query-request.json"),
                home=self.home,
            )
        self.assertEqual("invalid-query-report", rejected.exception.code)
        self.assertEqual("request", rejected.exception.stage)
        self.assertNotIn(
            os.fspath(self.root),
            canonical_json(rejected.exception.payload()),
        )
        self.assertEqual(before, self._tree())

    def test_query_report_binds_registry_vault_manifest_and_base_generation(
        self,
    ) -> None:
        baseline = self._query_payload()
        vault_manifest_sha256 = sha256_json(
            load_vault(self.vault_root, expected_id="test").manifest
        )

        def set_card_generation(report: dict, manifest_sha256: str) -> None:
            card = report["vaults"][0]
            card["generation"] = sha256_json(
                {
                    "vault_manifest_sha256": manifest_sha256,
                    "graph_manifest_sha256": card["graph_manifest_sha256"],
                    "graph_sha256": card["graph_sha256"],
                    "source_ledger_generation_sha256": card[
                        "source_ledger_generation_sha256"
                    ],
                    "authority_generation_sha256": card[
                        "authority_generation_sha256"
                    ],
                }
            )
            self._set_report_generation(report)

        def wrong_registry(report: dict) -> None:
            report["registry_generation"] = "0" * 64
            self._set_report_generation(report)

        def wrong_graph(report: dict) -> None:
            report["vaults"][0]["graph_sha256"] = "1" * 64
            set_card_generation(report, vault_manifest_sha256)

        def wrong_source(report: dict) -> None:
            report["vaults"][0]["source_ledger_generation_sha256"] = "2" * 64
            set_card_generation(report, vault_manifest_sha256)

        def wrong_authority(report: dict) -> None:
            report["vaults"][0]["authority_generation_sha256"] = "3" * 64
            set_card_generation(report, vault_manifest_sha256)

        def wrong_manifest(report: dict) -> None:
            set_card_generation(report, "4" * 64)

        def incomplete_target(report: dict) -> None:
            report["vaults"] = []
            report["incomplete_vaults"] = [
                {
                    "vault_id": "test",
                    "code": "invalid-native-graph",
                    "message": "registered Vault could not provide a coherent recall generation",
                }
            ]
            report["status"] = "partial"
            self._set_report_generation(report)

        for name, mutate in (
            ("registry", wrong_registry),
            ("graph", wrong_graph),
            ("source", wrong_source),
            ("authority", wrong_authority),
            ("vault-manifest", wrong_manifest),
            ("incomplete-target", incomplete_target),
        ):
            with self.subTest(binding=name):
                report = json.loads(canonical_json(baseline))
                mutate(report)
                self._write_query_payload(report)
                request = self._request(
                    [
                        self._write_patch(
                            _concept_note("alpha", "Alpha", f"Stale {name} body.")
                        )
                    ],
                    request_id=f"stale-query-{name}",
                    refresh_query=False,
                )
                with self.assertRaises(VaultIngestError) as rejected:
                    plan_vault_ingest(
                        self._request_file(
                            request, name=f"stale-query-{name}.json"
                        ),
                        home=self.home,
                    )
                self.assertEqual("stale-query-report", rejected.exception.code)
                self.assertEqual("request", rejected.exception.stage)
                self.assertNotIn(
                    os.fspath(self.root),
                    canonical_json(rejected.exception.payload()),
                )

    def test_query_report_allows_the_exact_first_graph_bootstrap_state(self) -> None:
        report = self._query_payload()
        report["vaults"] = []
        report["incomplete_vaults"] = [
            {
                "vault_id": "test",
                "code": "invalid-native-graph",
                "message": "registered Vault could not provide a coherent recall generation",
            }
        ]
        report["status"] = "partial"
        self._set_report_generation(report)
        self._write_query_payload(report)
        request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Bootstrap query report body.")
                )
            ],
            request_id="bootstrap-query-report",
            refresh_query=False,
        )
        request["base"]["graph_generation_sha256"] = None
        request = finalize_self_digest(request, "request_sha256")
        validate_contract(request)
        checked, digest = vault_ingest_module._query_report(
            vault_ingest_module._RequestInput(request, self.root, None, None)
        )
        self.assertEqual("qlkg-recall-report-v1", checked["schema"])
        self.assertEqual(request["query_report"]["sha256"], digest)

    def test_same_request_plans_applies_and_retries_portable_receipt(self) -> None:
        before = self.note.read_bytes()
        desired = _concept_note("alpha", "Alpha", "New reviewed body.").replace(
            "kgd_topics: []", 'kgd_topics: ["[[Knowledge/Topics/Future]]"]'
        )
        # Keep the graph valid: this baseline test changes only the body.
        desired = desired.replace(
            'kgd_topics: ["[[Knowledge/Topics/Future]]"]', "kgd_topics: []"
        )
        request = self._request([self._write_patch(desired)])

        plan = plan_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual(request["request_sha256"], plan["request_sha256"])
        self.assertEqual("ready", plan["status"])
        self.assertEqual(before, self.note.read_bytes())

        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual(request["request_sha256"], receipt["request_sha256"])
        self.assertEqual(receipt, validate_contract(receipt))
        receipt_path = self.vault_root / Path(
            receipt_relative_path(receipt["receipt_sha256"])
        )
        self.assertTrue(receipt_path.is_file())
        self.assertNotIn(str(self.root), receipt_path.read_text(encoding="utf-8"))
        self.assertIn(b"# user preamble remains", self.note.read_bytes())
        self.assertIn(b"# user relation comment remains", self.note.read_bytes())
        self.assertIn(b"New reviewed body.", self.note.read_bytes())

        retried = apply_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual(receipt, retried)

    def test_historical_retry_report_reconstructs_its_own_ledger_generation(self) -> None:
        first_request = self._reviewed_empty_request(request_id="historical-r1")
        first_report, first_receipt = apply_vault_ingest_report(
            first_request, request_root=self.root, home=self.home
        )
        first_generation = first_report["source_ledger_generation_sha256"]

        second_source = self.vault_root / "notes/second-review.md"
        second_source.write_text("Another empty review.\n", encoding="utf-8")
        captured = capture_source(
            second_source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:01Z",
            uuid_factory=lambda: uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        )
        second_request = self._request(
            [],
            updates=[
                {
                    "version_id": captured["result"]["current_version_id"],
                    "status": "reviewed-empty",
                    "candidate_dispositions": [],
                    "concept_ids": [],
                    "concept_evidence": [],
                    "relation_evidence": [],
                }
            ],
            request_id="historical-r2",
        )
        second_report, _ = apply_vault_ingest_report(
            second_request, request_root=self.root, home=self.home
        )
        self.assertNotEqual(
            first_generation, second_report["source_ledger_generation_sha256"]
        )

        retried_report, retried_receipt = apply_vault_ingest_report(
            first_request, request_root=self.root, home=self.home
        )
        self.assertEqual("already-committed", retried_report["outcome"])
        self.assertEqual(first_receipt, retried_receipt)
        self.assertEqual(
            first_generation, retried_report["source_ledger_generation_sha256"]
        )
        self.assertEqual(
            first_report["graph_generation_sha256"],
            retried_report["graph_generation_sha256"],
        )
        self.assertEqual(
            first_report["note_inventory_sha256"],
            retried_report["note_inventory_sha256"],
        )

    def test_receipt_inventory_rejects_a_foreign_vault_receipt(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Foreign receipt setup.")
        request = self._request(
            [self._write_patch(desired)], request_id="foreign-receipt-setup"
        )
        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)
        foreign = finalize_self_digest(
            {**receipt, "vault_id": "other", "receipt_sha256": "0" * 64},
            "receipt_sha256",
        )
        relative = receipt_relative_path(foreign["receipt_sha256"])
        target = self.vault_root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_json(foreign).encode("utf-8") + b"\n")

        with self.assertRaises(VaultIngestError) as rejected:
            vault_ingest_module._receipt_inventory(
                load_vault(self.vault_root, expected_id="test")
            )
        self.assertEqual("invalid-receipt-store", rejected.exception.code)

    def test_receipt_inventory_retains_only_matches_and_bounds_total_bytes(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Receipt inventory bounds.")
        request = self._request(
            [self._write_patch(desired)], request_id="inventory-match"
        )
        receipt = apply_vault_ingest(
            request, request_root=self.root, home=self.home
        )
        unrelated = finalize_self_digest(
            {
                **receipt,
                "request_id": "inventory-unrelated",
                "request_sha256": "f" * 64,
                "receipt_sha256": "0" * 64,
            },
            "receipt_sha256",
        )
        unrelated_relative = receipt_relative_path(unrelated["receipt_sha256"])
        unrelated_path = self.vault_root.joinpath(*unrelated_relative.split("/"))
        unrelated_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_path.write_bytes(
            canonical_json(unrelated).encode("utf-8") + b"\n"
        )

        vault = load_vault(self.vault_root, expected_id="test")
        retained = vault_ingest_module._receipt_inventory(
            vault, matching_request_id="inventory-match"
        )
        self.assertEqual([receipt["receipt_sha256"]], [item["receipt_sha256"] for item in retained])

        total = sum(
            path.stat().st_size
            for path in (self.vault_root / ".kgdistiller/receipts/sha256").rglob("*.json")
        )
        with mock.patch.object(
            vault_ingest_module, "MAX_RECEIPT_INVENTORY_BYTES", total - 1
        ):
            with self.assertRaises(VaultIngestError) as bounded:
                vault_ingest_module._receipt_inventory(
                    vault, matching_request_id="inventory-match"
                )
        self.assertEqual("receipt-store-too-large", bounded.exception.code)

    def test_receipt_inventory_verifies_each_pinned_shard_after_enumeration(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Shard verification setup.")
        request = self._request(
            [self._write_patch(desired)], request_id="receipt-shard-verification"
        )
        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)
        shard = (
            self.vault_root
            / ".kgdistiller/receipts/sha256"
            / receipt["receipt_sha256"][:2]
        ).resolve()
        original = vault_ingest_module._PinnedDirectory.verify_current
        state: dict[str, int | None] = {"outer": None, "calls": 0}

        def swap_after_enumeration(pinned: object) -> None:
            if Path(pinned.path) == shard:
                if state["outer"] is None:
                    state["outer"] = id(pinned)
                if state["outer"] == id(pinned):
                    state["calls"] = int(state["calls"] or 0) + 1
                    if state["calls"] == 2:
                        raise SourceArchiveError(
                            "unsafe-ledger-path",
                            "simulated receipt shard ancestor replacement",
                        )
            original(pinned)

        with mock.patch.object(
            vault_ingest_module._PinnedDirectory,
            "verify_current",
            autospec=True,
            side_effect=swap_after_enumeration,
        ):
            with self.assertRaises(VaultIngestError) as rejected:
                apply_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual("unsafe-ledger-path", rejected.exception.code)
        self.assertEqual(2, state["calls"])

    def test_receipt_inventory_double_capture_detects_inserted_conflict(self) -> None:
        setup_request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Receipt race setup.")
                )
            ],
            request_id="receipt-race-setup",
        )
        template = apply_vault_ingest(
            setup_request, request_root=self.root, home=self.home
        )
        racing_request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Receipt race target.")
                )
            ],
            request_id="receipt-race-target",
        )
        conflict = finalize_self_digest(
            {
                **template,
                "request_id": racing_request["request_id"],
                "request_sha256": "f" * 64,
                "receipt_sha256": "0" * 64,
            },
            "receipt_sha256",
        )
        relative = receipt_relative_path(conflict["receipt_sha256"])
        conflict_path = self.vault_root.joinpath(*relative.split("/"))
        inserted = False

        def insert_between_scans(label: str, path: str) -> None:
            nonlocal inserted
            if label == "between-receipt-inventory-scans" and not inserted:
                inserted = True
                conflict_path.parent.mkdir(parents=True, exist_ok=True)
                conflict_path.write_bytes(
                    canonical_json(conflict).encode("utf-8") + b"\n"
                )

        with mock.patch.object(
            vault_ingest_module,
            "_vault_ingest_hook",
            side_effect=insert_between_scans,
        ):
            with self.assertRaises(VaultIngestError) as changing:
                apply_vault_ingest(
                    racing_request, request_root=self.root, home=self.home
                )
        self.assertEqual("stale-receipt-store", changing.exception.code)
        self.assertTrue(conflict_path.is_file())

        with self.assertRaises(VaultIngestError) as stable_conflict:
            apply_vault_ingest(
                racing_request, request_root=self.root, home=self.home
            )
        self.assertEqual("request-id-conflict", stable_conflict.exception.code)

    def test_late_receipt_rechecks_output_and_cleans_stage_before_failure(self) -> None:
        request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Late receipt target.")
                )
            ],
            request_id="late-receipt-output",
        )
        request_path = self._request_file(request, name="late-receipt.json")
        input_value = vault_ingest_module._load_request(request_path)
        prepared = vault_ingest_module._prepare(input_value, home=self.home)
        expected, _, _ = vault_ingest_module._complete_preparation(prepared)
        late = finalize_self_digest(
            {
                **expected,
                "warnings": ["canonical receipt inserted by concurrent actor"],
                "receipt_sha256": "0" * 64,
            },
            "receipt_sha256",
        )
        output = self.root / "late-receipt-output.json"
        output.write_bytes(canonical_json(expected).encode("utf-8") + b"\n")
        late_path = self.vault_root.joinpath(
            *receipt_relative_path(late["receipt_sha256"]).split("/")
        )
        original_existing = vault_ingest_module._existing_receipt
        calls = 0

        def insert_before_late_scan(vault: object, current_request: dict):
            nonlocal calls
            calls += 1
            if calls == 2:
                late_path.parent.mkdir(parents=True, exist_ok=True)
                late_path.write_bytes(canonical_json(late).encode("utf-8") + b"\n")
            return original_existing(vault, current_request)

        before_note = self.note.read_bytes()
        with mock.patch.object(
            vault_ingest_module,
            "_existing_receipt",
            side_effect=insert_before_late_scan,
        ):
            with self.assertRaises(VaultIngestError) as rejected:
                apply_vault_ingest(
                    request_path,
                    home=self.home,
                    receipt_precondition=lambda receipt: (
                        vault_ingest_module.preflight_ingest_output(
                            output,
                            receipt,
                            request=request_path,
                            home=self.home,
                        )
                    ),
                )
        self.assertEqual("output-exists", rejected.exception.code)
        self.assertEqual(2, calls)
        self.assertEqual(before_note, self.note.read_bytes())
        self.assertEqual(
            canonical_json(expected).encode("utf-8") + b"\n",
            output.read_bytes(),
        )
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )
        self.assertEqual(
            [],
            list(
                (self.vault_root / ".kgdistiller/build").glob(
                    ".stage-vault-ingest-*"
                )
            ),
        )

    def test_self_consistent_receipt_cannot_assign_note_role_to_source_manifest(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Role binding setup.")
        request = self._request(
            [self._write_patch(desired)], request_id="receipt-role-binding"
        )
        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)
        malicious = json.loads(canonical_json(receipt))
        malicious["changes"]["notes"][0][
            "path"
        ] = ".kgdistiller/sources/manifest.json"
        malicious = finalize_self_digest(malicious, "receipt_sha256")
        self.assertEqual(malicious, validate_contract(malicious))

        with self.assertRaises(VaultIngestError) as rejected:
            vault_ingest_module._receipt_note_paths(
                load_vault(self.vault_root, expected_id="test"), malicious
            )
        self.assertEqual("invalid-journal", rejected.exception.code)

    def test_unsorted_reviewed_update_uses_one_canonical_receipt_and_ledger_order(self) -> None:
        beta = self.vault_root / "Knowledge/Concepts/Beta.md"
        beta.write_text(
            _concept_note("beta", "Beta", "Beta authority."), encoding="utf-8"
        )
        sync_knowledge(home=self.home)
        source = self.vault_root / "notes/unsorted-review.md"
        source.parent.mkdir(exist_ok=True)
        source.write_text("Alpha evidence.\nBeta evidence.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        version_id = captured["result"]["current_version_id"]
        alpha_span = {
            "version_id": version_id,
            "start_line": 1,
            "end_line": 1,
            "excerpt_sha256": _sha256(b"Alpha evidence."),
        }
        beta_span = {
            "version_id": version_id,
            "start_line": 2,
            "end_line": 2,
            "excerpt_sha256": _sha256(b"Beta evidence."),
        }
        update = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [
                {"candidate_id": "z-candidate", "disposition": "defer"},
                {"candidate_id": "a-candidate", "disposition": "reuse"},
            ],
            "concept_ids": ["beta", "alpha"],
            "concept_evidence": [
                {"concept_id": "beta", "spans": [beta_span]},
                {"concept_id": "alpha", "spans": [alpha_span]},
            ],
            "relation_evidence": [],
        }
        request = self._request(
            [], updates=[update], request_id="unsorted-reviewed-update"
        )
        receipt = apply_vault_ingest(
            request, request_root=self.root, home=self.home
        )
        summary = receipt["after"]["derivations"][0]
        self.assertEqual(["alpha", "beta"], summary["concept_ids"])
        self.assertEqual(
            ["a-candidate", "z-candidate"],
            [item["candidate_id"] for item in summary["candidate_dispositions"]],
        )
        self.assertEqual(
            ["alpha", "beta"],
            [item["concept_id"] for item in summary["concept_evidence"]],
        )
        self.assertEqual(
            receipt,
            apply_vault_ingest(request, request_root=self.root, home=self.home),
        )

    def test_same_id_cross_path_delete_create_is_rejected_without_writes(self) -> None:
        destination = "Knowledge/Concepts/Nested/Alpha.md"
        desired = _concept_note("alpha", "Alpha", "Moved body.")
        patches = [
            {
                "path": "Knowledge/Concepts/Alpha.md",
                "operation": "delete",
                "expected_raw_sha256": _sha256(self.note.read_bytes()),
                "content": None,
                "content_sha256": None,
            },
            self._write_patch(desired, path=destination),
        ]
        request = self._request(patches, request_id="move-request")
        before = self.note.read_bytes()

        with self.assertRaises(VaultIngestError) as rejected:
            plan_vault_ingest(request, request_root=self.root, home=self.home)

        self.assertEqual("unsupported-native-note-move", rejected.exception.code)
        self.assertEqual(before, self.note.read_bytes())
        self.assertFalse((self.vault_root / Path(destination)).exists())

    def test_nfd_note_authority_fails_before_any_vault_write(self) -> None:
        authority = "Knowledge/Concepts/Cafe\u0301.md"
        request = self._request(
            [
                self._write_patch(
                    _concept_note("cafe", "Cafe", "NFD authority."),
                    path=authority,
                )
            ],
            request_id="nfd-authority",
        )
        before = self._visible_tree()

        with self.assertRaises(VaultIngestError) as rejected:
            plan_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual("invalid-request", rejected.exception.code)
        self.assertEqual(before, self._visible_tree())
        self.assertFalse((self.vault_root / Path(authority)).exists())

    def test_failure_rolls_back_first_receipt_and_new_nested_note_directories(self) -> None:
        path = "Knowledge/Concepts/New/Nested/Beta.md"
        desired = _concept_note("beta", "Beta", "A new reviewed concept.")
        request = self._request(
            [self._write_patch(desired, path=path)], request_id="rollback-request"
        )
        before = self._tree()

        for fail_after_target in (1, 2):
            seen = 0

            def inject(label: str) -> None:
                nonlocal seen
                if label == "after-target":
                    seen += 1
                    if seen == fail_after_target:
                        raise RuntimeError("injected publication failure")

            with self.assertRaisesRegex(RuntimeError, "injected publication failure"):
                apply_vault_ingest(
                    request,
                    request_root=self.root,
                    home=self.home,
                    failure_injector=inject,
                )
            self.assertEqual(before, self._tree())
            self.assertFalse(
                (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
            )

    def test_third_party_planned_directory_is_never_claimed_or_removed(self) -> None:
        authority = "Knowledge/Concepts/RaceWinner/Beta.md"
        race_directory = self.vault_root / "Knowledge/Concepts/RaceWinner"
        request = self._request(
            [
                self._write_patch(
                    _concept_note("race-beta", "Race Beta", "Race body."),
                    path=authority,
                )
            ],
            request_id="directory-race-winner",
        )
        before_files = self._tree()[1]

        def create_race_winner(label: str) -> None:
            if label == "after-final-preconditions":
                race_directory.mkdir()

        with self.assertRaises(VaultIngestError) as rejected:
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=create_race_winner,
            )
        self.assertEqual("concurrent-directory-change", rejected.exception.code)
        self.assertTrue(race_directory.is_dir())
        self.assertEqual([], list(race_directory.iterdir()))
        self.assertEqual(before_files, self._tree()[1])
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )

    def test_unrecorded_empty_scaffold_does_not_wedge_recovery_or_retry(self) -> None:
        authority = "Knowledge/Concepts/CrashOne/CrashTwo/Beta.md"
        request = self._request(
            [
                self._write_patch(
                    _concept_note("crash-beta", "Crash Beta", "Crash body."),
                    path=authority,
                )
            ],
            request_id="directory-ownership-crash",
        )
        before_directories, before_files = self._tree()

        def crash_after_second_scaffold(label: str, path: str) -> None:
            if (
                label == "after-directory-create-before-journal"
                and path == "Knowledge/Concepts/CrashOne/CrashTwo"
            ):
                raise _Crash()

        with mock.patch.object(
            vault_ingest_module,
            "_vault_ingest_hook",
            side_effect=crash_after_second_scaffold,
        ):
            with self.assertRaises(_Crash):
                apply_vault_ingest(
                    request, request_root=self.root, home=self.home
                )

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        after_directories, after_files = self._tree()
        self.assertEqual(before_files, after_files)
        self.assertLessEqual(
            after_directories - before_directories,
            {
                "Knowledge/Concepts/CrashOne",
                "Knowledge/Concepts/CrashOne/CrashTwo",
            },
        )
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )
        self.assertFalse(
            any(
                path.name.startswith(".kgd-live-")
                for path in self.vault_root.rglob("*")
            )
        )

        apply_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertTrue((self.vault_root / Path(authority)).is_file())

    def test_recorded_scaffold_replacement_is_never_deleted_by_recovery(self) -> None:
        authority = "Knowledge/Concepts/IdentitySwap/Beta.md"
        directory = self.vault_root / "Knowledge/Concepts/IdentitySwap"
        request = self._request(
            [
                self._write_patch(
                    _concept_note("identity-beta", "Identity Beta", "Identity body."),
                    path=authority,
                )
            ],
            request_id="directory-identity-swap",
        )

        def crash(label: str) -> None:
            if label == "before-target":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        recorded = next(
            item
            for item in journal["created_directories"]
            if item["path"] == "Knowledge/Concepts/IdentitySwap"
        )
        original = directory.stat()
        self.assertEqual(str(original.st_dev), recorded["device"])
        self.assertEqual(str(original.st_ino), recorded["inode"])
        displaced = directory.with_name("IdentitySwap-displaced")
        directory.rename(displaced)
        directory.mkdir()
        replacement = directory.stat()
        self.assertFalse(os.path.samestat(original, replacement))

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertTrue(directory.is_dir())
        self.assertEqual([], list(directory.iterdir()))
        self.assertTrue(displaced.is_dir())
        self.assertFalse(journal_path.exists())

        displaced.rmdir()
        apply_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertTrue((self.vault_root / Path(authority)).is_file())

    def test_apply_and_recovery_share_target_image_bounds(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Bounded replacement body.")
        request = self._request(
            [self._write_patch(desired)], request_id="target-image-bounds"
        )
        before = self._tree()
        for constant in ("MAX_TRANSACTION_IMAGE_BYTES", "MAX_JOURNAL_BYTES"):
            with self.subTest(constant=constant), mock.patch.object(
                vault_ingest_module, constant, 1
            ):
                with self.assertRaises(VaultIngestError):
                    apply_vault_ingest(
                        request, request_root=self.root, home=self.home
                    )
                self.assertEqual(before, self._tree())
                self.assertFalse(
                    (
                        self.vault_root
                        / ".kgdistiller/build/vault-ingest-journal.json"
                    ).exists()
                )

    def test_created_directory_allowlist_uses_one_parent_set_lookup_each(self) -> None:
        class CountingParents(set):
            def __init__(self, values: set[str]) -> None:
                super().__init__(values)
                self.lookups = 0

            def __contains__(self, value: object) -> bool:
                self.lookups += 1
                return super().__contains__(value)

        directories = {
            f"Knowledge/Concepts/Bounded/{index:04d}" for index in range(8192)
        }
        parents = CountingParents(directories)
        vault = load_vault(self.vault_root)
        self.assertTrue(
            all(
                vault_ingest_module._created_directory_allowed(
                    vault, directory, parents
                )
                for directory in sorted(directories)
            )
        )
        self.assertEqual(len(directories), parents.lookups)

    def test_hard_crash_live_temporaries_recover_first_receipt_and_nested_note(self) -> None:
        receipt_request = self._reviewed_empty_request(
            request_id="crash-first-receipt"
        )
        cases = (
            (
                receipt_request,
                "receipt-crash.json",
                ".kgdistiller/receipts/sha256/",
            ),
            (
                self._request(
                    [
                        self._write_patch(
                            _concept_note("beta", "Beta", "Nested crash body."),
                            path="Knowledge/Concepts/New/Nested/Beta.md",
                        )
                    ],
                    request_id="crash-nested-note",
                ),
                "nested-crash.json",
                "Knowledge/Concepts/New/Nested/",
            ),
        )
        for request, name, expected_parent in cases:
            with self.subTest(name=name):
                before = self._visible_tree()
                self._crash_during_live_temporary(
                    request, occurrence=1, name=name
                )
                temporaries = list(
                    self.vault_root.rglob(".kgd-vault-ingest-*.tmp")
                )
                self.assertEqual(1, len(temporaries))
                self.assertTrue(
                    temporaries[0]
                    .relative_to(self.vault_root)
                    .as_posix()
                    .startswith(expected_parent)
                )
                self.assertTrue(
                    (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").is_file()
                )
                self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
            self.assertEqual(before, self._visible_tree())

    @unittest.skipIf(os.name == "nt", "POSIX hard-link no-clobber state")
    def test_posix_no_clobber_link_crash_recovers_exact_old_tree(self) -> None:
        authority = "Knowledge/Concepts/NewLinked.md"
        request = self._request(
            [
                self._write_patch(
                    _concept_note("new-linked", "New Linked", "No-clobber body."),
                    path=authority,
                )
            ],
            request_id="no-clobber-link-crash",
        )
        request_path = self._request_file(
            request, name="no-clobber-link-crash.json"
        )
        before = self._visible_tree()
        program = "\n".join(
            [
                "import os, sys",
                "import kgdistiller.source_archive as source_archive",
                "from kgdistiller.vault_ingest import apply_vault_ingest",
                "def hook(label, parent, leaf):",
                "    if label == 'after-leaf-noreplace-link':",
                "        os._exit(91)",
                "source_archive._anchored_test_hook = hook",
                "apply_vault_ingest(sys.argv[1], home=sys.argv[2])",
            ]
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                os.fspath(request_path),
                os.fspath(self.home),
            ],
            cwd=self.root,
            check=False,
        )
        self.assertEqual(91, result.returncode)

        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        record = next(item for item in journal["targets"] if item["path"] == authority)
        final = self.vault_root / Path(authority)
        temporary = self.vault_root / Path(record["temporary_path"])
        self.assertTrue(final.is_file())
        self.assertTrue(temporary.is_file())
        self.assertTrue(os.path.samestat(final.stat(), temporary.stat()))
        self.assertEqual(2, final.stat().st_nlink)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(before, self._visible_tree())
        self.assertFalse(final.exists())
        self.assertFalse(temporary.exists())

    def test_linked_no_clobber_state_is_normalized_before_recovery(self) -> None:
        authority = "Knowledge/Concepts/LinkedRecovery.md"
        request = self._request(
            [
                self._write_patch(
                    _concept_note(
                        "linked-recovery", "Linked Recovery", "Linked state."
                    ),
                    path=authority,
                )
            ],
            request_id="linked-state-recovery",
        )
        before = self._visible_tree()

        def crash(label: str) -> None:
            if label == "after-journal":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        record = next(item for item in journal["targets"] if item["path"] == authority)
        staged = self.vault_root / Path(record["staged_path"])
        temporary = self.vault_root / Path(record["temporary_path"])
        final = self.vault_root / Path(authority)
        temporary.write_bytes(staged.read_bytes())
        os.link(temporary, final)
        self.assertTrue(os.path.samestat(temporary.stat(), final.stat()))
        self.assertEqual(2, temporary.stat().st_nlink)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(before, self._visible_tree())
        self.assertFalse(final.exists())
        self.assertFalse(temporary.exists())

    def test_linked_rollback_restore_state_is_normalized_before_recovery(self) -> None:
        authority = "Knowledge/Concepts/Alpha.md"
        before = self._visible_tree()
        old_note = self.note.read_bytes()
        request = self._request(
            [
                {
                    "path": authority,
                    "operation": "delete",
                    "expected_raw_sha256": _sha256(old_note),
                    "content": None,
                    "content_sha256": None,
                }
            ],
            request_id="linked-rollback-state",
        )

        def crash(label: str) -> None:
            if label == "after-journal":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        record = next(item for item in journal["targets"] if item["path"] == authority)
        journal = finalize_self_digest(
            {**journal, "state": "rolling-back"}, "journal_sha256"
        )
        journal_path.write_bytes(canonical_json(journal).encode("utf-8"))
        backup = self.vault_root / Path(record["backup_path"])
        temporary = self.vault_root / Path(record["temporary_path"])
        self.note.unlink()
        temporary.write_bytes(backup.read_bytes())
        os.link(temporary, self.note)
        self.assertTrue(os.path.samestat(temporary.stat(), self.note.stat()))
        self.assertEqual(2, temporary.stat().st_nlink)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(before, self._visible_tree())
        self.assertEqual(old_note, self.note.read_bytes())
        self.assertFalse(temporary.exists())

    @unittest.skipIf(os.name == "nt", "POSIX hard-link rollback crash state")
    def test_posix_rollback_no_clobber_link_crash_recovers_exact_old_tree(self) -> None:
        authority = "Knowledge/Concepts/Alpha.md"
        old_note = self.note.read_bytes()
        request = self._request(
            [
                {
                    "path": authority,
                    "operation": "delete",
                    "expected_raw_sha256": _sha256(old_note),
                    "content": None,
                    "content_sha256": None,
                }
            ],
            request_id="rollback-no-clobber-link-crash",
        )
        request_path = self._request_file(
            request, name="rollback-no-clobber-link-crash.json"
        )
        before = self._visible_tree()
        program = "\n".join(
            [
                "import json, os, sys",
                "from pathlib import Path",
                "import kgdistiller.source_archive as source_archive",
                "from kgdistiller.vault_ingest import apply_vault_ingest",
                "journal = Path(sys.argv[3])",
                "def hook(label, parent, leaf):",
                "    if label == 'after-leaf-noreplace-link' and journal.exists():",
                "        if json.loads(journal.read_text(encoding='utf-8'))['state'] == 'rolling-back':",
                "            os._exit(92)",
                "def fail(label):",
                "    if label == 'after-target':",
                "        raise RuntimeError('begin rollback')",
                "source_archive._anchored_test_hook = hook",
                "apply_vault_ingest(sys.argv[1], home=sys.argv[2], failure_injector=fail)",
            ]
        )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                os.fspath(request_path),
                os.fspath(self.home),
                os.fspath(journal_path),
            ],
            cwd=self.root,
            check=False,
        )
        self.assertEqual(92, result.returncode)
        journal = json.loads(journal_path.read_bytes())
        self.assertEqual("rolling-back", journal["state"])
        record = next(item for item in journal["targets"] if item["path"] == authority)
        temporary = self.vault_root / Path(record["temporary_path"])
        self.assertTrue(os.path.samestat(temporary.stat(), self.note.stat()))
        self.assertEqual(2, temporary.stat().st_nlink)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(before, self._visible_tree())
        self.assertEqual(old_note, self.note.read_bytes())

    def test_prepared_recovery_accepts_only_exact_new_prefix_temporary(self) -> None:
        request = self._reviewed_empty_request(request_id="partial-prefix-request")
        before = self._visible_tree()

        def crash(label: str) -> None:
            if label == "before-target":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        record = next(
            item
            for item in journal["targets"]
            if item["path"].startswith(".kgdistiller/receipts/")
        )
        staged = self.vault_root / Path(record["staged_path"])
        temporary = self.vault_root / Path(record["temporary_path"])
        temporary.parent.mkdir(parents=True, exist_ok=True)
        content = staged.read_bytes()
        temporary.write_bytes(content[: len(content) // 2])

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(before, self._visible_tree())

    def test_partial_first_immutable_source_artifact_is_retryable(self) -> None:
        request = self._reviewed_empty_request(request_id="partial-source-first")
        prepared = vault_ingest_module._prepare(
            vault_ingest_module._load_request(request, request_root=self.root),
            home=self.home,
        )
        _, source, _ = vault_ingest_module._complete_preparation(prepared)
        self.assertIsNotNone(source)
        assert source is not None
        live_manifest = self.vault_root / ".kgdistiller/sources/manifest.json"
        before_manifest = live_manifest.read_bytes()
        generation = (
            self.vault_root
            / ".kgdistiller/sources/generations"
            / source.ledger.generation_sha256
        )
        generation.mkdir()
        name = "documents"
        filename = source.manifest["artifacts"][name]["path"]
        content = source.contents[name]
        temporary = generation / f".{filename}-{_sha256(content)[:16]}.install"
        temporary.write_bytes(content[: max(1, len(content) // 2)])
        self.assertEqual(before_manifest, live_manifest.read_bytes())

        apply_vault_ingest(request, request_root=self.root, home=self.home)

        self.assertEqual(
            source.ledger.generation_sha256,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )
        self.assertFalse(tuple(generation.glob("*.install")))
        for artifact, record in source.manifest["artifacts"].items():
            self.assertEqual(
                source.contents[artifact],
                (generation / record["path"]).read_bytes(),
            )

    def test_partial_later_immutable_source_generation_is_completed(self) -> None:
        request = self._reviewed_empty_request(request_id="partial-source-later")
        prepared = vault_ingest_module._prepare(
            vault_ingest_module._load_request(request, request_root=self.root),
            home=self.home,
        )
        _, source, _ = vault_ingest_module._complete_preparation(prepared)
        self.assertIsNotNone(source)
        assert source is not None
        live_manifest = self.vault_root / ".kgdistiller/sources/manifest.json"
        before_manifest = live_manifest.read_bytes()
        generation = (
            self.vault_root
            / ".kgdistiller/sources/generations"
            / source.ledger.generation_sha256
        )
        generation.mkdir()
        artifacts = list(source.manifest["artifacts"].items())
        first_name, first_record = artifacts[0]
        (generation / first_record["path"]).write_bytes(source.contents[first_name])
        later_name, later_record = artifacts[1]
        later_content = source.contents[later_name]
        temporary = generation / (
            f".{later_record['path']}-{_sha256(later_content)[:16]}.install"
        )
        temporary.write_bytes(later_content[: max(1, len(later_content) // 2)])
        self.assertEqual(before_manifest, live_manifest.read_bytes())

        apply_vault_ingest(request, request_root=self.root, home=self.home)

        self.assertEqual(
            source.ledger.generation_sha256,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )
        self.assertFalse(tuple(generation.glob("*.install")))
        for artifact, record in source.manifest["artifacts"].items():
            self.assertEqual(
                source.contents[artifact],
                (generation / record["path"]).read_bytes(),
            )

    def test_prepared_recovery_rejects_tampered_and_globally_unreachable_state(self) -> None:
        request = self._request(
            [self._write_patch(_concept_note("alpha", "Alpha", "Tamper body."))],
            request_id="prepared-tamper",
        )
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"

        def leave_prepared() -> dict:
            def crash(label: str) -> None:
                if label == "after-journal":
                    raise _Crash()

            with self.assertRaises(_Crash):
                apply_vault_ingest(
                    request,
                    request_root=self.root,
                    home=self.home,
                    failure_injector=crash,
                )
            return json.loads(journal_path.read_text(encoding="utf-8"))

        def note_record(journal: dict) -> dict:
            return next(
                item
                for item in journal["targets"]
                if item["path"] == "Knowledge/Concepts/Alpha.md"
            )

        journal = leave_prepared()
        record = note_record(journal)
        temporary = self.vault_root / Path(record["temporary_path"])
        temporary.write_bytes(b"not-a-new-prefix")
        with self.assertRaises(VaultIngestError) as wrong_bytes:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-temporary", wrong_bytes.exception.code
        )
        self.assertTrue(temporary.is_file())
        self.assertTrue(journal_path.is_file())
        temporary.unlink()
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        journal = leave_prepared()
        record = note_record(journal)
        temporary = self.vault_root / Path(record["temporary_path"])
        temporary.mkdir()
        with self.assertRaises(VaultIngestError) as wrong_type:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-temporary", wrong_type.exception.code
        )
        self.assertTrue(temporary.is_dir())
        temporary.rmdir()
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        journal = leave_prepared()
        record = note_record(journal)
        temporary = self.vault_root / Path(record["temporary_path"])
        staged = self.vault_root / Path(record["staged_path"])
        temporary.write_bytes(staged.read_bytes()[:1])
        alias = self.root / "temporary-hardlink-alias"
        os.link(temporary, alias)
        with self.assertRaises(VaultIngestError) as hardlink:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-temporary", hardlink.exception.code
        )
        self.assertTrue(temporary.is_file())
        alias.unlink()
        temporary.unlink()
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        journal = leave_prepared()
        targets = sorted(
            journal["targets"],
            key=lambda item: (
                10
                if not item["path"].startswith(".kgdistiller/")
                else 20
                if item["path"].startswith(".kgdistiller/receipts/")
                else 30
                if item["path"] == ".kgdistiller/sources/manifest.json"
                else 50
                if item["path"] == ".kgdistiller/graph/manifest.json"
                else 40,
                item["path"],
            ),
        )
        multiple: list[Path] = []
        for item in targets[:2]:
            path = self.vault_root / Path(item["temporary_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            multiple.append(path)
        with self.assertRaises(VaultIngestError) as multiple_temps:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-temporary", multiple_temps.exception.code
        )
        for path in multiple:
            path.unlink()
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        journal = leave_prepared()
        targets = sorted(
            journal["targets"],
            key=lambda item: (
                10
                if not item["path"].startswith(".kgdistiller/")
                else 20
                if item["path"].startswith(".kgdistiller/receipts/")
                else 30
                if item["path"] == ".kgdistiller/sources/manifest.json"
                else 50
                if item["path"] == ".kgdistiller/graph/manifest.json"
                else 40,
                item["path"],
            ),
        )
        later = targets[1]
        later_live = self.vault_root / Path(later["path"])
        later_live.parent.mkdir(parents=True, exist_ok=True)
        later_live.write_bytes(
            (self.vault_root / Path(later["staged_path"])).read_bytes()
        )
        with self.assertRaises(VaultIngestError) as scrambled:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("unreachable-transaction-state", scrambled.exception.code)
        self.assertTrue(journal_path.is_file())
        later_live.unlink()
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

    def test_recovery_binds_source_generation_to_receipt_and_keeps_missing_evidence(self) -> None:
        source = self.vault_root / "notes/recovery-history.md"
        source.parent.mkdir(exist_ok=True)
        source.write_text("First archived text.\n", encoding="utf-8")
        capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        )
        earlier_generation = load_source_ledger(
            load_vault(self.vault_root)
        ).generation_sha256
        source.write_text("Second archived text.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:01Z",
        )
        request = self._request(
            [],
            updates=[
                {
                    "version_id": captured["result"]["current_version_id"],
                    "status": "reviewed-empty",
                    "candidate_dispositions": [],
                    "concept_ids": [],
                    "concept_evidence": [],
                    "relation_evidence": [],
                }
            ],
            request_id="recovery-source-binding",
        )

        def crash(label: str) -> None:
            if label == "after-journal":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        original_bytes = journal_path.read_bytes()
        original = json.loads(original_bytes)
        final_generation = original["after"]["source_ledger_generation_sha256"]
        self.assertNotEqual(earlier_generation, final_generation)

        tampered = json.loads(original_bytes)
        tampered["after"]["source_ledger_generation_sha256"] = earlier_generation
        tampered = finalize_self_digest(tampered, "journal_sha256")
        journal_path.write_bytes(canonical_json(tampered).encode("utf-8"))
        with self.assertRaises(VaultIngestError) as wrong_projection:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-journal", wrong_projection.exception.code)
        self.assertTrue(journal_path.is_file())

        journal_path.write_bytes(original_bytes)
        derivations = (
            self.vault_root
            / ".kgdistiller/sources/generations"
            / final_generation
            / "derivations.jsonl"
        )
        derivation_bytes = derivations.read_bytes()
        derivations.unlink()
        with self.assertRaises(VaultIngestError) as missing_generation:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-receipt-history", missing_generation.exception.code)
        self.assertTrue(journal_path.is_file())
        derivations.write_bytes(derivation_bytes)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertFalse(journal_path.exists())

    def test_recovery_binds_changed_note_images_to_the_immutable_receipt(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Receipt-bound after image.")
        request = self._request(
            [self._write_patch(desired)], request_id="receipt-note-images"
        )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )

        def crash_at(label: str):
            def crash(current: str) -> None:
                if current == label:
                    raise _Crash()

            return crash

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash_at("after-journal"),
            )
        original_journal = journal_path.read_bytes()
        journal = json.loads(original_journal)
        note_record = next(
            item
            for item in journal["targets"]
            if item["path"] == "Knowledge/Concepts/Alpha.md"
        )
        backup_path = self.vault_root / Path(note_record["backup_path"])
        original_backup = backup_path.read_bytes()
        forged_before = _concept_note(
            "alpha", "Alpha", "Forged but parseable before image."
        ).encode("utf-8")
        backup_path.write_bytes(forged_before)
        note_record["old_bytes"] = len(forged_before)
        note_record["old_sha256"] = _sha256(forged_before)
        journal = finalize_self_digest(journal, "journal_sha256")
        journal_path.write_bytes(canonical_json(journal).encode("utf-8"))

        with self.assertRaises(VaultIngestError) as forged_backup:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-journal", forged_backup.exception.code)
        self.assertTrue(journal_path.is_file())

        backup_path.write_bytes(original_backup)
        journal_path.write_bytes(original_journal)
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash_at("after-commit"),
            )
        committed = json.loads(journal_path.read_bytes())
        note_record = next(
            item
            for item in committed["targets"]
            if item["path"] == "Knowledge/Concepts/Alpha.md"
        )
        forged_after = _concept_note(
            "alpha", "Alpha", "Forged but parseable after image."
        ).encode("utf-8")
        (self.vault_root / Path(note_record["staged_path"])).write_bytes(
            forged_after
        )
        self.note.write_bytes(forged_after)
        note_record["new_bytes"] = len(forged_after)
        note_record["new_sha256"] = _sha256(forged_after)
        committed = finalize_self_digest(committed, "journal_sha256")
        journal_path.write_bytes(canonical_json(committed).encode("utf-8"))

        with self.assertRaises(VaultIngestError) as forged_after_image:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-journal", forged_after_image.exception.code)
        self.assertTrue(journal_path.is_file())
        self.assertEqual(forged_after, self.note.read_bytes())

    def test_recovery_rejects_an_nfd_forged_journal_path(self) -> None:
        request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "NFC journal setup.")
                )
            ],
            request_id="nfd-journal",
        )

        def crash(label: str) -> None:
            if label == "after-journal":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        journal["targets"][0]["path"] = "Knowledge/Concepts/Cafe\u0301.md"
        journal = finalize_self_digest(journal, "journal_sha256")
        journal_path.write_bytes(canonical_json(journal).encode("utf-8"))

        with self.assertRaises(VaultIngestError) as rejected:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-journal", rejected.exception.code)
        self.assertTrue(journal_path.is_file())

    def test_source_manifest_live_temporary_crash_is_recovered_before_status(self) -> None:
        request = self._reviewed_empty_request(request_id="source-temp-crash")
        source = self.vault_root / "notes/empty-review.md"
        manifest_path = self.vault_root / ".kgdistiller/sources/manifest.json"
        before_manifest = manifest_path.read_bytes()
        before_generation = load_source_ledger(
            load_vault(self.vault_root)
        ).generation_sha256

        self._crash_during_live_temporary(
            request, occurrence=2, name="source-temp-crash.json"
        )
        journal = json.loads(
            (
                self.vault_root
                / ".kgdistiller/build/vault-ingest-journal.json"
            ).read_text(encoding="utf-8")
        )
        source_record = next(
            item
            for item in journal["targets"]
            if item["path"] == ".kgdistiller/sources/manifest.json"
        )
        self.assertTrue(
            (self.vault_root / Path(source_record["temporary_path"])).is_file()
        )

        report = source_status(source, home=self.home)
        self.assertEqual("captured", report["result"]["effective_status"])
        self.assertEqual(before_manifest, manifest_path.read_bytes())
        self.assertEqual(
            before_generation,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )
        self.assertEqual([], list(self.vault_root.rglob(".kgd-vault-ingest-*.tmp")))

    def test_source_readers_and_capture_recover_partial_new_pointer(self) -> None:
        request = self._reviewed_empty_request(request_id="source-entry-recovery")
        source = self.vault_root / "notes/empty-review.md"
        before_generation = load_source_ledger(load_vault(self.vault_root)).generation_sha256

        def leave_after_source() -> None:
            seen = 0

            def crash(label: str) -> None:
                nonlocal seen
                if label == "after-target":
                    seen += 1
                    if seen == 2:
                        raise _Crash()

            with self.assertRaises(_Crash):
                apply_vault_ingest(
                    request,
                    request_root=self.root,
                    home=self.home,
                    failure_injector=crash,
                )

        leave_after_source()
        status = source_status(source, home=self.home)
        self.assertEqual("captured", status["result"]["effective_status"])
        self.assertEqual(
            before_generation,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )

        leave_after_source()
        difference = diff_source(source, home=self.home)
        self.assertEqual("diff", difference["action"])
        self.assertEqual(
            before_generation,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )

        leave_after_source()
        captured = capture_source(source, home=self.home)
        self.assertEqual("no_op", captured["result"]["outcome"])
        self.assertEqual(
            before_generation,
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
        )
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )

    def test_check_and_sync_recover_partial_note_before_compilation(self) -> None:
        old_note = self.note.read_bytes()
        request = self._request(
            [self._write_patch(_concept_note("alpha", "Alpha", "Partial note body."))],
            request_id="compiler-entry-recovery",
        )

        def leave_after_note() -> None:
            def crash(label: str) -> None:
                if label == "after-target":
                    raise _Crash()

            with self.assertRaises(_Crash):
                apply_vault_ingest(
                    request,
                    request_root=self.root,
                    home=self.home,
                    failure_injector=crash,
                )

        leave_after_note()
        checked = check_knowledge("test", home=self.home)
        self.assertEqual("ok", checked["status"])
        self.assertEqual(old_note, self.note.read_bytes())

        leave_after_note()
        synced = sync_knowledge("test", home=self.home)
        self.assertEqual("ok", synced["status"])
        self.assertEqual(old_note, self.note.read_bytes())
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )

    def test_graph_view_recovers_every_partial_target_and_committed_cleanup(self) -> None:
        old_note = self.note.read_bytes()
        old_view = GraphView.load(self.vault_root / ".kgdistiller/graph")
        old_generation = old_view.snapshot["graph"]["sha256"]
        desired = _concept_note("alpha", "Alpha", "Crash matrix body.")
        request = self._request(
            [self._write_patch(desired)], request_id="graph-crash-matrix"
        )
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"

        def crash_after_journal(label: str) -> None:
            if label == "after-journal":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash_after_journal,
            )
        target_count = len(json.loads(journal_path.read_text())["targets"])
        self.assertGreater(target_count, 5)
        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))

        for stop_after in range(1, target_count + 1):
            seen = 0

            def crash_after_target(label: str) -> None:
                nonlocal seen
                if label == "after-target":
                    seen += 1
                    if seen == stop_after:
                        raise _Crash()

            with self.subTest(stop_after=stop_after):
                with self.assertRaises(_Crash):
                    apply_vault_ingest(
                        request,
                        request_root=self.root,
                        home=self.home,
                        failure_injector=crash_after_target,
                    )
                self.assertTrue(journal_path.is_file())
                view = GraphView.load(self.vault_root / ".kgdistiller/graph")
                self.assertEqual(old_generation, view.snapshot["graph"]["sha256"])
                self.assertEqual(old_note, self.note.read_bytes())
                self.assertFalse(journal_path.exists())

        def crash_after_commit(label: str) -> None:
            if label == "after-commit":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash_after_commit,
            )
        committed = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual("committed", committed["state"])
        note_record = next(
            item
            for item in committed["targets"]
            if item["path"] == "Knowledge/Concepts/Alpha.md"
        )
        committed_temp = self.vault_root / Path(note_record["temporary_path"])
        committed_temp.write_bytes(
            (self.vault_root / Path(note_record["staged_path"])).read_bytes()[:1]
        )
        with self.assertRaises(VaultIngestError) as impossible_committed:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-state", impossible_committed.exception.code
        )
        self.assertTrue(journal_path.is_file())
        self.assertTrue(committed_temp.is_file())
        committed_temp.unlink()
        view = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertNotEqual(old_generation, view.snapshot["graph"]["sha256"])
        self.assertIn(b"Crash matrix body.", self.note.read_bytes())
        self.assertFalse(journal_path.exists())

    def test_commit_journal_uncertainty_requires_a_durable_retry(self) -> None:
        first = self._request(
            [self._write_patch(_concept_note("alpha", "Alpha", "Retry commit body."))],
            request_id="commit-retry-once",
        )
        calls = 0

        def fail_once(label: str, path: str) -> None:
            nonlocal calls
            if label == "after-committed-journal-replace":
                calls += 1
                if calls == 1:
                    raise OSError("injected post-replace durability failure")

        with mock.patch.object(
            vault_ingest_module, "_vault_ingest_hook", side_effect=fail_once
        ):
            report, _ = apply_vault_ingest_report(
                first, request_root=self.root, home=self.home
            )
        self.assertEqual(2, calls)
        self.assertEqual("committed", report["outcome"])
        self.assertEqual("complete", report["cleanup_status"])
        self.assertFalse(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").exists()
        )

        second = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Persistent commit fault body.")
                )
            ],
            request_id="commit-retry-always",
        )

        def fail_always(label: str, path: str) -> None:
            if label == "after-committed-journal-replace":
                raise OSError("persistent post-replace durability failure")

        with mock.patch.object(
            vault_ingest_module, "_vault_ingest_hook", side_effect=fail_always
        ):
            with self.assertRaises(VaultIngestError) as rejected:
                apply_vault_ingest(
                    second, request_root=self.root, home=self.home
                )
        self.assertEqual("commit-journal-uncertain", rejected.exception.code)
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        self.assertEqual(
            "committed", json.loads(journal_path.read_text(encoding="utf-8"))["state"]
        )
        self.assertIn(b"Persistent commit fault body.", self.note.read_bytes())
        view = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertIn("graph", view.snapshot)
        self.assertFalse(journal_path.exists())
        self.assertIn(b"Persistent commit fault body.", self.note.read_bytes())

    def test_commit_failure_then_rolling_restore_temp_crash_recovers_old(self) -> None:
        old_visible = self._visible_tree()
        request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Rolling crash body.")
                )
            ],
            request_id="rolling-temp-crash",
        )
        request_path = self._request_file(request, name="rolling-temp-crash.json")
        program = "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "import kgdistiller.source_archive as source_archive",
                "import kgdistiller.vault_ingest as vault_ingest",
                "original_write_journal = vault_ingest._write_journal",
                "commit_failed = False",
                "rolling = False",
                "def write_journal(vault, payload):",
                "    global commit_failed, rolling",
                "    if payload['state'] == 'committed' and not commit_failed:",
                "        commit_failed = True",
                "        raise OSError('commit replace did not happen')",
                "    original_write_journal(vault, payload)",
                "    if payload['state'] == 'rolling-back':",
                "        rolling = True",
                "def hook(label, parent, leaf):",
                "    global rolling",
                "    if rolling and label == 'after-vault-file-temp-fsync' and leaf.startswith('.kgd-vault-ingest-'):",
                "        os._exit(92)",
                "vault_ingest._write_journal = write_journal",
                "source_archive._anchored_test_hook = hook",
                "vault_ingest.apply_vault_ingest(Path(sys.argv[1]), home=Path(sys.argv[2]))",
            ]
        )
        crashed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                os.fspath(request_path),
                os.fspath(self.home),
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(92, crashed.returncode, crashed.stderr)
        journal_path = self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        self.assertEqual(
            "rolling-back",
            json.loads(journal_path.read_text(encoding="utf-8"))["state"],
        )
        temporaries = list(self.vault_root.rglob(".kgd-vault-ingest-*.tmp"))
        self.assertEqual(1, len(temporaries))
        rolling = json.loads(journal_path.read_text(encoding="utf-8"))
        rolling_record = next(
            item
            for item in rolling["targets"]
            if self.vault_root / Path(item["temporary_path"]) == temporaries[0]
        )
        old_image = (
            self.vault_root / Path(rolling_record["backup_path"])
        ).read_bytes()
        temporaries[0].write_bytes(b"not-an-old-prefix")
        with self.assertRaises(VaultIngestError) as wrong_prefix:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual(
            "unreachable-transaction-temporary", wrong_prefix.exception.code
        )
        self.assertTrue(journal_path.is_file())
        temporaries[0].write_bytes(old_image)

        self.assertTrue(recover_vault_ingest(load_vault(self.vault_root)))
        self.assertEqual(old_visible, self._visible_tree())

    def test_before_target_third_party_change_is_never_overwritten(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Transaction body.")
        request = self._request(
            [self._write_patch(desired)], request_id="concurrent-note-request"
        )
        outside = _concept_note("alpha", "Alpha", "Third-party body.").encode("utf-8")
        changed = False

        def inject(label: str) -> None:
            nonlocal changed
            if label == "before-target" and not changed:
                changed = True
                self.note.write_bytes(outside)

        with self.assertRaises(VaultIngestError) as rejected:
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=inject,
            )

        self.assertEqual("rollback-conflict", rejected.exception.code)
        self.assertEqual(outside, self.note.read_bytes())
        self.assertTrue(
            (self.vault_root / ".kgdistiller/build/vault-ingest-journal.json").is_file()
        )

    def test_after_temp_fsync_third_party_images_are_never_overwritten(self) -> None:
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )

        def exercise(request: dict, target: Path, third_party: bytes) -> None:
            before = target.read_bytes() if target.exists() else None
            changed = False

            def inject(label: str) -> None:
                nonlocal changed
                if label == "after-live-temp-fsync" and not changed:
                    changed = True
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(third_party)

            with self.assertRaises(VaultIngestError) as rejected:
                apply_vault_ingest(
                    request,
                    request_root=self.root,
                    home=self.home,
                    failure_injector=inject,
                )
            self.assertTrue(changed)
            self.assertEqual("rollback-conflict", rejected.exception.code)
            self.assertEqual(third_party, target.read_bytes())
            self.assertTrue(journal_path.is_file())

            if before is None:
                target.unlink()
            else:
                target.write_bytes(before)
            self.assertTrue(
                recover_vault_ingest(load_vault(self.vault_root))
            )
            self.assertFalse(journal_path.exists())

        exercise(
            self._request(
                [
                    self._write_patch(
                        _concept_note("alpha", "Alpha", "Existing race target.")
                    )
                ],
                request_id="after-temp-existing",
            ),
            self.note,
            b"third-party existing bytes",
        )

        new_path = self.vault_root / "Knowledge/Concepts/Gamma.md"
        exercise(
            self._request(
                [
                    self._write_patch(
                        _concept_note("gamma", "Gamma", "New race target."),
                        path="Knowledge/Concepts/Gamma.md",
                    )
                ],
                request_id="after-temp-new",
            ),
            new_path,
            b"third-party new bytes",
        )

        alpha_before = self.note.read_bytes()
        exercise(
            self._request(
                [
                    {
                        "path": "Knowledge/Concepts/Alpha.md",
                        "operation": "delete",
                        "expected_raw_sha256": _sha256(alpha_before),
                        "content": None,
                        "content_sha256": None,
                    }
                ],
                request_id="after-temp-delete",
            ),
            self.note,
            b"third-party delete bytes",
        )

    @unittest.skipIf(os.name == "nt", "open Windows temporary denies rename")
    def test_live_temporary_name_swap_never_deletes_the_race_winner(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Temporary swap body.")
        request = self._request(
            [self._write_patch(desired)], request_id="temporary-name-swap"
        )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        third_party = b"third-party temporary name winner"
        moved: Path | None = None
        temporary: Path | None = None

        def swap_temporary(label: str) -> None:
            nonlocal moved, temporary
            if label != "after-live-temp-fsync" or temporary is not None:
                return
            journal = json.loads(journal_path.read_bytes())
            record = next(
                item
                for item in journal["targets"]
                if item["path"] == "Knowledge/Concepts/Alpha.md"
            )
            temporary = self.vault_root / Path(record["temporary_path"])
            moved = temporary.with_name(temporary.name + ".owned")
            temporary.replace(moved)
            temporary.write_bytes(third_party)
            raise RuntimeError("temporary name was swapped")

        with self.assertRaises(VaultIngestError):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=swap_temporary,
            )
        assert temporary is not None and moved is not None
        self.assertEqual(third_party, temporary.read_bytes())
        self.assertTrue(moved.is_file())
        self.assertTrue(journal_path.is_file())

    def test_recovery_cleanup_is_bound_to_the_validated_temporary_inode(self) -> None:
        request = self._reviewed_empty_request(
            request_id="recovery-temporary-name-swap"
        )

        def crash(label: str) -> None:
            if label == "before-target":
                raise _Crash()

        with self.assertRaises(_Crash):
            apply_vault_ingest(
                request,
                request_root=self.root,
                home=self.home,
                failure_injector=crash,
            )
        journal_path = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal = json.loads(journal_path.read_bytes())
        record = next(
            item
            for item in journal["targets"]
            if item["path"].startswith(".kgdistiller/receipts/")
        )
        staged = self.vault_root / Path(record["staged_path"])
        temporary = self.vault_root / Path(record["temporary_path"])
        temporary.write_bytes(staged.read_bytes()[:17])
        moved = temporary.with_name(temporary.name + ".owned")
        third_party = b"third-party cleanup race winner"
        original = source_archive_module._PinnedDirectory.cleanup_owned_leaf_raw
        swapped = False

        def swap_before_cleanup(
            pinned: object, leaf: str, expected: os.stat_result
        ) -> bool:
            nonlocal swapped
            if not swapped and Path(getattr(pinned, "path")) == temporary.parent and leaf == temporary.name:
                swapped = True
                temporary.replace(moved)
                temporary.write_bytes(third_party)
            return original(pinned, leaf, expected)

        with mock.patch.object(
            source_archive_module._PinnedDirectory,
            "cleanup_owned_leaf_raw",
            new=swap_before_cleanup,
        ):
            with self.assertRaises(VaultIngestError):
                recover_vault_ingest(load_vault(self.vault_root))
        self.assertTrue(swapped)
        self.assertEqual(third_party, temporary.read_bytes())
        self.assertTrue(moved.is_file())
        self.assertTrue(journal_path.is_file())

    def test_reviewed_derivation_closes_evidence_graph_receipt_ledger_cycle(self) -> None:
        source = self.vault_root / "notes/source.md"
        source.parent.mkdir()
        source.write_text("Alpha evidence.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("12345678-1234-4234-8234-123456789abc"),
        )
        version_id = captured["result"]["current_version_id"]
        span = {
            "version_id": version_id,
            "start_line": 1,
            "end_line": 1,
            "excerpt_sha256": _sha256(b"Alpha evidence."),
        }
        update = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [
                {"candidate_id": "alpha", "disposition": "reuse"}
            ],
            "concept_ids": ["alpha"],
            "concept_evidence": [{"concept_id": "alpha", "spans": [span]}],
            "relation_evidence": [],
        }
        request = self._request(
            [], updates=[update], request_id="review-alpha-request"
        )
        before = self._tree()

        plan = plan_vault_ingest(request, request_root=self.root, home=self.home)
        self.assertEqual(before, self._tree())
        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)

        ledger = load_source_ledger(load_vault(self.vault_root, expected_id="test"))
        evidence = current_evidence_view(ledger)
        self.assertEqual(frozenset({"alpha"}), evidence.concept_ids)
        committed = [row for row in ledger.derivations if row["status"] == "committed"]
        self.assertEqual(1, len(committed))
        self.assertEqual(receipt["receipt_sha256"], committed[0]["ingest_receipt_sha256"])
        self.assertEqual(
            receipt["after"]["graph_generation_sha256"],
            committed[0]["graph_generation_sha256"],
        )
        self.assertNotIn("source_ledger_generation_sha256", receipt["after"])
        self.assertEqual([update], receipt["after"]["derivations"])
        view = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertEqual(
            receipt["after"]["graph_generation_sha256"], view.snapshot["graph"]["sha256"]
        )
        self.assertEqual(
            plan["after"]["graph_generation_sha256"],
            view.snapshot["graph"]["sha256"],
        )

    def test_plan_rejects_missing_concepts_relations_and_wrong_direction(self) -> None:
        beta = self.vault_root / "Knowledge/Concepts/Beta.md"
        beta.write_text(
            _concept_note("beta", "Beta", "Beta authority."), encoding="utf-8"
        )
        sync_knowledge(home=self.home)
        source = self.vault_root / "notes/closure.md"
        source.parent.mkdir(exist_ok=True)
        source.write_text("Closure evidence.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )
        version_id = captured["result"]["current_version_id"]
        span = {
            "version_id": version_id,
            "start_line": 1,
            "end_line": 1,
            "excerpt_sha256": _sha256(b"Closure evidence."),
        }

        ghost = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [],
            "concept_ids": ["ghost"],
            "concept_evidence": [{"concept_id": "ghost", "spans": [span]}],
            "relation_evidence": [],
        }
        with self.assertRaises(VaultIngestError) as missing_concept:
            plan_vault_ingest(
                self._request([], updates=[ghost], request_id="ghost-concept"),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("missing-reviewed-concept", missing_concept.exception.code)

        concepts = ["alpha", "beta"]
        concept_evidence = [
            {"concept_id": concept_id, "spans": [span]}
            for concept_id in concepts
        ]
        missing = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [],
            "concept_ids": concepts,
            "concept_evidence": concept_evidence,
            "relation_evidence": [
                {
                    "source": "alpha",
                    "relation": "implies",
                    "target": "beta",
                    "spans": [span],
                }
            ],
        }
        with self.assertRaises(VaultIngestError) as missing_relation:
            plan_vault_ingest(
                self._request([], updates=[missing], request_id="missing-relation"),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("missing-reviewed-relation", missing_relation.exception.code)

        duplicate = {
            **missing,
            "relation_evidence": [
                missing["relation_evidence"][0],
                missing["relation_evidence"][0],
            ],
        }
        with self.assertRaises(VaultIngestError) as duplicate_relation:
            plan_vault_ingest(
                self._request(
                    [], updates=[duplicate], request_id="duplicate-relation-evidence"
                ),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("invalid-source-ledger", duplicate_relation.exception.code)

        reverse_contrast = {
            **missing,
            "relation_evidence": [
                {
                    "source": "alpha",
                    "relation": "contrasts-with",
                    "target": "beta",
                    "spans": [span],
                },
                {
                    "source": "beta",
                    "relation": "contrasts-with",
                    "target": "alpha",
                    "spans": [span],
                },
            ],
        }
        with self.assertRaises(VaultIngestError) as reverse_duplicate:
            plan_vault_ingest(
                self._request(
                    [],
                    updates=[reverse_contrast],
                    request_id="reverse-duplicate-contrast-evidence",
                ),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("invalid-source-ledger", reverse_duplicate.exception.code)

        alpha_with_prerequisite = _concept_note(
            "alpha", "Alpha", "Old reviewed body."
        ).replace(
            "kgd_prerequisites: []",
            'kgd_prerequisites: ["[[Knowledge/Concepts/Beta]]"]',
        )
        wrong = {
            **missing,
            "relation_evidence": [
                {
                    "source": "alpha",
                    "relation": "prerequisite-for",
                    "target": "beta",
                    "spans": [span],
                }
            ],
        }
        with self.assertRaises(VaultIngestError) as wrong_direction:
            plan_vault_ingest(
                self._request(
                    [self._write_patch(alpha_with_prerequisite)],
                    updates=[wrong],
                    request_id="wrong-direction",
                ),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("missing-reviewed-relation", wrong_direction.exception.code)

    def test_plan_validates_untouched_effective_evidence_against_final_authority(self) -> None:
        source = self.vault_root / "notes/untouched-closure.md"
        source.parent.mkdir(exist_ok=True)
        source.write_text("Alpha survives only with authority.\n", encoding="utf-8")
        captured = capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        )
        version_id = captured["result"]["current_version_id"]
        update = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [],
            "concept_ids": ["alpha"],
            "concept_evidence": [
                {
                    "concept_id": "alpha",
                    "spans": [
                        {
                            "version_id": version_id,
                            "start_line": 1,
                            "end_line": 1,
                            "excerpt_sha256": _sha256(
                                b"Alpha survives only with authority."
                            ),
                        }
                    ],
                }
            ],
            "relation_evidence": [],
        }
        apply_vault_ingest(
            self._request([], updates=[update], request_id="commit-alpha-evidence"),
            request_root=self.root,
            home=self.home,
        )
        delete = {
            "path": "Knowledge/Concepts/Alpha.md",
            "operation": "delete",
            "expected_raw_sha256": _sha256(self.note.read_bytes()),
            "content": None,
            "content_sha256": None,
        }

        with self.assertRaises(VaultIngestError) as rejected:
            plan_vault_ingest(
                self._request([delete], request_id="delete-reviewed-alpha"),
                request_root=self.root,
                home=self.home,
            )
        self.assertEqual("missing-reviewed-concept", rejected.exception.code)
        self.assertTrue(self.note.is_file())

    def test_existing_frontmatter_merge_preserves_every_unowned_byte(self) -> None:
        existing = (
            "\ufeff---\r\n"
            "# before first key\r\n"
            "kgd_schema: qlkg-concept-v1\r\n"
            "kgd_id: alpha\r\n"
            'aliases: [" alpha "] # keep inline\r\n'
            "tags: [kgdistiller/concept]\r\n"
            'kgd_fields: ["[[Knowledge/Fields/Test]]"]\r\n'
            "kgd_topics:\r\n"
            '  - "[[Knowledge/Topics/Old]]"\r\n'
            "# immediately after owned value\r\n"
            "\r\n"
            "kgd_prerequisites: []\r\n"
            "kgd_implies: []\r\n"
            "kgd_generalizes: []\r\n"
            "kgd_contrasts_with: []\r\n"
            "kgd_derived_from: []\r\n"
            'user_quoted: "leave: exactly"\r\n'
            "user_optional: # keep implicit null\r\n"
            "user_block: |-\r\n"
            "  first\r\n"
            "  second\r\n"
            "user_flow: [one, two]\r\n"
            "---\r\n\r\n# Alpha\r\n\r\nOld body.\r\n"
        ).encode("utf-8")
        desired = _concept_note("alpha", "Alpha", "New body.").replace(
            "aliases: []", 'aliases: ["alpha"]'
        ).replace('user_setting: "yes"\n', "").replace(
            "user_flow: [a, b]\n", ""
        ).encode("utf-8")

        merged = merge_native_note_bytes(
            existing, desired, authority="Knowledge/Concepts/Alpha.md"
        )

        self.assertTrue(merged.startswith(b"\xef\xbb\xbf---\r\n"))
        for exact in (
            b"# before first key\r\n",
            b'aliases: [" alpha "] # keep inline\r\n',
            b"# immediately after owned value\r\n\r\n",
            b'user_quoted: "leave: exactly"\r\n',
            b"user_optional: # keep implicit null\r\n",
            b"user_block: |-\r\n  first\r\n  second\r\n",
            b"user_flow: [one, two]\r\n",
        ):
            self.assertIn(exact, merged)
        self.assertIn(b"kgd_topics:\r\n  []\r\n", merged)
        self.assertTrue(merged.endswith(b"# Alpha\r\n\r\nNew body.\r\n"))
        parsed = parse_native_markdown(
            merged, authority="Knowledge/Concepts/Alpha.md"
        )
        self.assertEqual((), parsed.topics)

        incomplete = desired.replace(b"kgd_topics: []\n", b"")
        with self.assertRaises(NativeNoteError) as rejected:
            merge_native_note_bytes(
                existing,
                incomplete,
                authority="Knowledge/Concepts/Alpha.md",
            )
        self.assertEqual("incomplete-owned-frontmatter", rejected.exception.code)

    def test_flow_and_block_owned_values_round_trip_both_directions(self) -> None:
        empty = _concept_note("alpha", "Alpha", "Body.").encode("utf-8")
        block = _concept_note("alpha", "Alpha", "Body.").replace(
            "kgd_topics: []",
            'kgd_topics:\n  - "[[Knowledge/Topics/Test]]"',
        ).encode("utf-8")
        to_block = merge_native_note_bytes(
            empty, block, authority="Knowledge/Concepts/Alpha.md"
        )
        to_empty = merge_native_note_bytes(
            block, empty, authority="Knowledge/Concepts/Alpha.md"
        )
        self.assertEqual(1, len(parse_native_markdown(
            to_block, authority="Knowledge/Concepts/Alpha.md"
        ).topics))
        self.assertEqual((), parse_native_markdown(
            to_empty, authority="Knowledge/Concepts/Alpha.md"
        ).topics)

    def test_pure_compilation_validation_enforces_publication_leaf_bounds(self) -> None:
        for name, limit_name in (
            ("nodes.jsonl", "MAX_NATIVE_ARTIFACT_BYTES"),
            ("entries/00.json", "ENTRY_SHARD_LIMIT"),
            ("manifest.json", "MAX_NATIVE_MANIFEST_BYTES"),
        ):
            with self.subTest(name=name), mock.patch.object(
                native_compiler_module, limit_name, 3
            ), mock.patch.object(
                native_compiler_module,
                "_expected_bytes",
                return_value={name: b"1234"},
            ):
                with self.assertRaises(
                    native_compiler_module.NativeCompilerError
                ) as rejected:
                    native_compiler_module.validate_native_compilation(
                        mock.Mock()
                    )
                self.assertEqual("native-artifact-too-large", rejected.exception.code)
                self.assertEqual(
                    3,
                    vault_ingest_module._target_limit(
                        f".kgdistiller/graph/{name}"
                    ),
                )

    def test_external_artifact_writer_is_material_free_guarded_and_exact(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Output writer body.")
        request = self._request(
            [self._write_patch(desired)], request_id="output-writer-request"
        )
        request_path = self._request_file(request)
        report, plan = plan_vault_ingest_report(request_path, home=self.home)
        self.assertEqual("planned", report["outcome"])
        vault_before = self._tree()
        registry_before = self._subtree(self.home)
        request_before = request_path.read_bytes()
        query_before = self.query.read_bytes()
        output = self.root / "plan.json"

        write_ingest_artifact(
            output, plan, request=request_path, home=self.home
        )
        self.assertEqual(
            canonical_json(plan).encode("utf-8") + b"\n", output.read_bytes()
        )
        write_ingest_artifact(
            output, plan, request=request_path, home=self.home
        )
        output.write_bytes(b"replace me")
        with self.assertRaises(VaultIngestError) as different_output:
            write_ingest_artifact(
                output, plan, request=request_path, home=self.home
            )
        self.assertEqual("output-exists", different_output.exception.code)
        self.assertEqual(b"replace me", output.read_bytes())
        self.assertEqual(vault_before, self._tree())
        self.assertEqual(registry_before, self._subtree(self.home))
        self.assertEqual(request_before, request_path.read_bytes())
        self.assertEqual(query_before, self.query.read_bytes())

        swap_output = self.root / "plan-temp-swap.json"
        plan_bytes = canonical_json(plan).encode("utf-8") + b"\n"
        temporary = self.root / (
            ".kgd-ingest-"
            + _sha256(swap_output.name.encode("utf-8"))[:16]
            + "-"
            + _sha256(plan_bytes)[:16]
            + ".tmp"
        )
        temporary.write_bytes(plan_bytes[:19])
        moved = temporary.with_name(temporary.name + ".owned")
        third_party = b"third-party external temporary"
        original_cleanup = (
            source_archive_module._PinnedDirectory.cleanup_owned_leaf_raw
        )
        swapped = False

        def swap_external_temporary(
            pinned: object, leaf: str, expected: os.stat_result
        ) -> bool:
            nonlocal swapped
            if not swapped and Path(getattr(pinned, "path")) == self.root and leaf == temporary.name:
                swapped = True
                temporary.replace(moved)
                temporary.write_bytes(third_party)
            return original_cleanup(pinned, leaf, expected)

        with mock.patch.object(
            source_archive_module._PinnedDirectory,
            "cleanup_owned_leaf_raw",
            new=swap_external_temporary,
        ):
            with self.assertRaises(VaultIngestError) as temporary_race:
                write_ingest_artifact(
                    swap_output, plan, request=request_path, home=self.home
                )
        self.assertEqual("unsafe-output-artifact", temporary_race.exception.code)
        self.assertTrue(swapped)
        self.assertEqual(third_party, temporary.read_bytes())
        self.assertTrue(moved.is_file())
        self.assertFalse(swap_output.exists())

        race_output = self.root / "plan-hardlink-race.json"
        hardlink_alias = self.root / "output-hardlink-alias.json"

        def add_hardlink(label: str, path: str) -> None:
            if label == "after-output-replace":
                self.assertEqual(race_output, Path(path))
                os.link(Path(path), hardlink_alias)

        with mock.patch.object(
            vault_ingest_module, "_vault_ingest_hook", side_effect=add_hardlink
        ):
            with self.assertRaises(VaultIngestError) as hardlink_race:
                write_ingest_artifact(
                    race_output, plan, request=request_path, home=self.home
                )
        self.assertEqual("unsafe-output-artifact", hardlink_race.exception.code)
        self.assertTrue(hardlink_alias.is_file())
        hardlink_alias.unlink()
        if race_output.exists():
            race_output.unlink()

        for hostile in (
            self.vault_root / "Knowledge/Concepts/Alpha.md",
            self.home / "hostile-plan.json",
            request_path,
            self.query,
        ):
            before = (
                self._tree(),
                self._subtree(self.home),
                request_path.read_bytes(),
                self.query.read_bytes(),
            )
            with self.assertRaises(VaultIngestError) as rejected:
                write_ingest_artifact(
                    hostile, plan, request=request_path, home=self.home
                )
            self.assertEqual("unsafe-output-artifact", rejected.exception.code)
            self.assertEqual(
                before,
                (
                    self._tree(),
                    self._subtree(self.home),
                    request_path.read_bytes(),
                    self.query.read_bytes(),
                ),
            )

    def test_cli_plan_and_apply_work_from_arbitrary_cwd(self) -> None:
        desired = _concept_note("alpha", "Alpha", "CLI body.")
        request = self._request(
            [self._write_patch(desired)], request_id="cli-request"
        )
        request_path = self._request_file(request)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        plan_path = self.root / "cli-plan.json"
        receipt_path = self.root / "cli-receipt.json"
        environment = os.environ.copy()
        environment["KGDISTILLER_HOME"] = os.fspath(self.home)

        planned = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "knowledge",
                "ingest",
                "plan",
                os.fspath(request_path),
                "--output",
                os.fspath(plan_path),
            ],
            cwd=elsewhere,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, planned.returncode, planned.stderr)
        plan_report = json.loads(planned.stdout)
        self.assertEqual("qlkg-vault-ingest-report-v1", plan_report["schema"])
        self.assertNotIn(os.fspath(self.root), planned.stdout)
        self.assertEqual("qlkg-vault-ingest-plan-v1", json.loads(plan_path.read_text())["schema"])

        applied = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "knowledge",
                "ingest",
                "apply",
                os.fspath(request_path),
                "--receipt",
                os.fspath(receipt_path),
            ],
            cwd=elsewhere,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, applied.returncode, applied.stderr)
        apply_report = json.loads(applied.stdout)
        self.assertEqual("committed", apply_report["outcome"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(apply_report["receipt_sha256"], receipt["receipt_sha256"])
        self.assertEqual(
            load_source_ledger(load_vault(self.vault_root)).generation_sha256,
            apply_report["source_ledger_generation_sha256"],
        )

    def test_cli_apply_closes_corrupt_f3_and_f4_recovery_errors(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Never installed.")
        request = self._request(
            [self._write_patch(desired)], request_id="cli-corrupt-journal"
        )
        request_path = self._request_file(request)
        elsewhere = self.root / "closed-error-cwd"
        elsewhere.mkdir()
        environment = os.environ.copy()
        environment["KGDISTILLER_HOME"] = os.fspath(self.home)
        before = self.note.read_bytes()
        build = self.vault_root / ".kgdistiller/build"

        for index, relative in enumerate(
            ("graph-transaction.json", "vault-ingest-journal.json")
        ):
            with self.subTest(journal=relative):
                journal = build / relative
                journal.write_bytes(b'{"broken":')
                output = self.root / f"closed-error-{index}.json"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "kgdistiller",
                        "knowledge",
                        "ingest",
                        "apply",
                        os.fspath(request_path),
                        "--receipt",
                        os.fspath(output),
                    ],
                    cwd=elsewhere,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(1, result.returncode)
                self.assertEqual("", result.stdout)
                failure = json.loads(result.stderr)
                self.assertEqual("qlkg-vault-ingest-error-v1", failure["schema"])
                self.assertEqual("recovery", failure["error"]["stage"])
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(os.fspath(self.root), result.stderr)
                self.assertFalse(output.exists())
                self.assertEqual(before, self.note.read_bytes())
                journal.unlink()

    def test_deep_json_request_query_and_journal_fail_as_closed_errors(self) -> None:
        nested = ("{\"x\":" * 1200 + "0" + "}" * 1200).encode("utf-8")
        environment = os.environ.copy()
        environment["KGDISTILLER_HOME"] = os.fspath(self.home)

        def invoke(action: str, request_path: Path) -> dict:
            output = self.root / f"deep-{action}-output.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kgdistiller",
                    "knowledge",
                    "ingest",
                    action,
                    os.fspath(request_path),
                    "--output" if action == "plan" else "--receipt",
                    os.fspath(output),
                ],
                cwd=self.root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            payload = json.loads(result.stderr)
            self.assertEqual("qlkg-vault-ingest-error-v1", payload["schema"])
            return payload

        deep_request = self.root / "deep-request.json"
        deep_request.write_bytes(nested)
        self.assertEqual(
            "request", invoke("plan", deep_request)["error"]["stage"]
        )

        original_query = self.query.read_bytes()
        self.query.write_bytes(nested)
        query_request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Deep query body.")
                )
            ],
            request_id="deep-query",
            refresh_query=False,
        )
        query_request_path = self._request_file(
            query_request, name="deep-query-request.json"
        )
        self.assertEqual(
            "request", invoke("plan", query_request_path)["error"]["stage"]
        )
        self.query.write_bytes(original_query)

        journal_request = self._request(
            [
                self._write_patch(
                    _concept_note("alpha", "Alpha", "Deep journal body.")
                )
            ],
            request_id="deep-journal",
        )
        journal_request_path = self._request_file(
            journal_request, name="deep-journal-request.json"
        )
        journal = (
            self.vault_root / ".kgdistiller/build/vault-ingest-journal.json"
        )
        journal.write_bytes(nested)
        with self.assertRaises(VaultIngestError) as recovery:
            recover_vault_ingest(load_vault(self.vault_root))
        self.assertEqual("invalid-vault-ingest-journal", recovery.exception.code)
        self.assertEqual(
            "recovery", invoke("apply", journal_request_path)["error"]["stage"]
        )

    def test_cli_apply_preflights_output_and_reports_postcommit_copy_failure(self) -> None:
        desired = _concept_note("alpha", "Alpha", "Truthful CLI commit body.")
        request = self._request(
            [self._write_patch(desired)], request_id="cli-output-truthfulness"
        )
        request_path = self._request_file(request)
        occupied = self.root / "occupied-receipt.json"
        occupied.write_bytes(b"do not replace")
        vault_before = self._visible_tree()
        environment = os.environ.copy()
        environment["KGDISTILLER_HOME"] = os.fspath(self.home)

        rejected = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "knowledge",
                "ingest",
                "apply",
                os.fspath(request_path),
                "--receipt",
                os.fspath(occupied),
            ],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, rejected.returncode)
        self.assertEqual("output-exists", json.loads(rejected.stderr)["error"]["code"])
        self.assertEqual(b"do not replace", occupied.read_bytes())
        before_directories, before_files = vault_before
        after_directories, after_files = self._visible_tree()
        before_files = dict(before_files)
        after_files = dict(after_files)
        before_files.pop(".kgdistiller/build/writer.lock", None)
        after_files.pop(".kgdistiller/build/writer.lock", None)
        self.assertEqual(
            (before_directories, before_files),
            (after_directories, after_files),
        )

        failed_copy = self.root / "failed-copy.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            [
                "kgdistiller",
                "knowledge",
                "ingest",
                "apply",
                os.fspath(request_path),
                "--receipt",
                os.fspath(failed_copy),
            ],
        ), mock.patch.dict(
            os.environ, {"KGDISTILLER_HOME": os.fspath(self.home)}
        ), mock.patch.object(
            vault_ingest_module,
            "write_ingest_artifact",
            side_effect=VaultIngestError(
                "injected-output-failure",
                "simulated external copy failure",
                stage="publication",
            ),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return_code = cli_module.main()

        self.assertEqual(0, return_code)
        self.assertEqual("", stderr.getvalue())
        report = json.loads(stdout.getvalue())
        self.assertEqual("committed", report["outcome"])
        self.assertIn(
            "external-receipt-not-written:injected-output-failure",
            report["warnings"],
        )
        self.assertFalse(failed_copy.exists())
        stored = self.vault_root.joinpath(*report["receipt_path"].split("/"))
        self.assertTrue(stored.is_file())

        retry_output = self.root / "retried-receipt.json"
        retried = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "knowledge",
                "ingest",
                "apply",
                os.fspath(request_path),
                "--receipt",
                os.fspath(retry_output),
            ],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertEqual("already-committed", json.loads(retried.stdout)["outcome"])
        self.assertTrue(retry_output.is_file())


if __name__ == "__main__":
    unittest.main()
