from __future__ import annotations

import contextlib
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

from kgdistiller import cli, source_archive as archive
from kgdistiller.contracts import ContractError, canonical_json, sha256_json, validate_contract
from kgdistiller.source_archive import (
    DERIVATION_SCHEMA,
    DOCUMENT_SCHEMA,
    LEDGER_SCHEMA,
    REPORT_SCHEMA,
    VERSION_SCHEMA,
    SourceArchiveError,
    capture_source,
    diff_source,
    extract_evidence_excerpt,
    load_source_ledger,
    normalize_source_text,
    source_status,
    verify_evidence_span,
)
from kgdistiller.vaults import VAULT_SCHEMA, add_vault, load_vault


class SourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-source-test-")
        # The contract requires canonical paths. Hosted runners may expose the
        # same temporary directory through macOS /var or a Windows 8.3 alias.
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "machine-home"
        self.vault = self.root / "Vault"
        for relative in (
            ".kgdistiller/sources",
            ".kgdistiller/graph",
            ".kgdistiller/build",
            "Knowledge/Concepts",
            "Knowledge/Fields",
            "Knowledge/Topics",
            "notes",
        ):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": VAULT_SCHEMA,
            "id": "test",
            "label": "Test Vault",
            "description": "",
            "concept_root": "Knowledge/Concepts",
            "field_root": "Knowledge/Fields",
            "topic_root": "Knowledge/Topics",
            "source_include": ["**/*.md", "**/*.typ", "**/*.tex"],
            "source_exclude": ["Knowledge/**", ".kgdistiller/**"],
        }
        (self.vault / ".kgdistiller/vault.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        add_vault(self.vault, home=self.home)
        self.source = self.vault / "notes/source.md"
        self.identities = iter(
            (
                uuid.UUID("12345678-1234-4234-8234-123456789abc"),
                uuid.UUID("22345678-1234-4234-8234-123456789abc"),
                uuid.UUID("32345678-1234-4234-8234-123456789abc"),
                uuid.UUID("42345678-1234-4234-8234-123456789abc"),
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _clock() -> str:
        return "2026-08-16T00:00:00Z"

    def _capture(self, path: Path | None = None) -> dict:
        return capture_source(
            path or self.source,
            home=self.home,
            clock=self._clock,
            uuid_factory=lambda: next(self.identities),
        )

    def _ledger(self):
        return load_source_ledger(load_vault(self.vault))

    def _review_current(self, concept_id: str | None = "alpha") -> None:
        resolved = archive._resolve_source(self.source, self.home)
        snapshot = archive._read_source(self.source)
        with archive._vault_writer_lock(resolved.vault):
            ledger = load_source_ledger(resolved.vault)
            documents = [dict(item) for item in ledger.documents]
            versions = [dict(item) for item in ledger.versions]
            current = versions[-1]
            if concept_id is None:
                derivation = {
                    "schema": DERIVATION_SCHEMA,
                    "version_id": current["version_id"],
                    "graph_generation_sha256": "a" * 64,
                    "candidate_dispositions": [],
                    "concept_ids": [],
                    "concept_evidence": [],
                    "relation_evidence": [],
                    "status": "reviewed-empty",
                    "inherited_from_version_id": None,
                    "ingest_receipt_sha256": "b" * 64,
                }
                lifecycle = "reviewed-empty"
            else:
                text = normalize_source_text(snapshot.raw.decode("utf-8"))
                excerpt = text.split("\n")[0]
                span = {
                    "version_id": current["version_id"],
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt_sha256": archive._sha256_bytes(excerpt.encode("utf-8")),
                }
                derivation = {
                    "schema": DERIVATION_SCHEMA,
                    "version_id": current["version_id"],
                    "graph_generation_sha256": "a" * 64,
                    "candidate_dispositions": [],
                    "concept_ids": [concept_id],
                    "concept_evidence": [{"concept_id": concept_id, "spans": [span]}],
                    "relation_evidence": [],
                    "status": "committed",
                    "inherited_from_version_id": None,
                    "ingest_receipt_sha256": "b" * 64,
                }
                lifecycle = "distilled"
            validate_contract(derivation)
            documents[0]["status"] = lifecycle
            archive._publish(
                resolved,
                self.home,
                documents,
                versions,
                [*ledger.derivations, derivation],
                snapshot=snapshot,
                stage_blob=False,
                expected_ledger_generation=ledger.generation_sha256,
            )

    def _cli(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False),
            mock.patch.object(sys, "argv", ["kgdistiller", *arguments]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main()
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else {}, stderr.getvalue()

    def _make_directory_link(self, link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            pass
        if os.name != "nt":
            return False
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0 and os.path.lexists(link)

    @staticmethod
    def _remove_directory_link(link: Path) -> None:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)

    def _require_directory_links(self) -> None:
        target = self.root / "link-probe-target"
        link = self.root / "link-probe"
        target.mkdir()
        if not self._make_directory_link(link, target):
            self.skipTest("directory symlinks or junctions are unavailable")
        self._remove_directory_link(link)
        target.rmdir()

    def test_first_capture_writes_canonical_generation_and_portable_report(self) -> None:
        self.source.write_bytes("α\r\nβ\r\n".encode("utf-8"))
        report = self._capture()

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["result"]["outcome"], "capture")
        self.assertTrue(report["result"]["semantic_changed"])
        self.assertNotIn(str(self.vault), json.dumps(report))
        ledger = self._ledger()
        self.assertEqual(len(ledger.documents), 1)
        self.assertEqual(len(ledger.versions), 1)
        version = ledger.versions[0]
        self.assertEqual(version["version_id"], "doc:12345678-1234-4234-8234-123456789abc:v00000001")
        self.assertEqual(version["captured_path"], "notes/source.md")
        self.assertEqual(version["blob_path"], f"blobs/sha256/{version['raw_sha256'][:2]}/{version['raw_sha256']}")
        manifest_path = self.vault / ".kgdistiller/sources/manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest_bytes, canonical_json(manifest).encode("utf-8"))
        self.assertEqual(manifest["schema"], LEDGER_SCHEMA)
        self.assertEqual(manifest["generation_sha256"], sha256_json(manifest["artifacts"]))
        self.assertEqual(validate_contract(manifest), manifest)

    def test_unchanged_is_verified_noop_without_clock_or_generation(self) -> None:
        self.source.write_bytes(b"same\n")
        first = self._capture()
        manifest = (self.vault / ".kgdistiller/sources/manifest.json").read_bytes()
        with mock.patch.object(archive, "_timestamp", side_effect=AssertionError("clock used")):
            second = capture_source(self.source, home=self.home)
        self.assertEqual(second["result"]["outcome"], "no_op")
        self.assertEqual(second["ledger_generation"], first["ledger_generation"])
        self.assertEqual((self.vault / ".kgdistiller/sources/manifest.json").read_bytes(), manifest)
        self.assertEqual(len(self._ledger().versions), 1)

    def test_a_b_a_has_three_events_and_reuses_first_blob(self) -> None:
        self.source.write_bytes(b"A\n")
        self._capture()
        self.source.write_bytes(b"B\n")
        self._capture()
        self.source.write_bytes(b"A\n")
        self._capture()
        ledger = self._ledger()
        self.assertEqual([item["sequence"] for item in ledger.versions], [1, 2, 3])
        self.assertEqual(ledger.versions[0]["blob_path"], ledger.versions[2]["blob_path"])
        blobs = list((self.vault / ".kgdistiller/sources/blobs/sha256").glob("*/*"))
        self.assertEqual(len(blobs), 2)

    def test_reviewed_newline_changes_carry_forward_across_multiple_hops(self) -> None:
        self.source.write_bytes(b"line\n")
        self._capture()
        self._review_current("alpha")
        self.source.write_bytes(b"line\r\n")
        second = self._capture()
        self.source.write_bytes(b"line\r")
        third = self._capture()
        ledger = self._ledger()
        self.assertFalse(second["result"]["semantic_changed"])
        self.assertFalse(third["result"]["semantic_changed"])
        self.assertEqual(second["result"]["effective_status"], "distilled")
        self.assertEqual([item["status"] for item in ledger.derivations], ["committed", "carried-forward", "carried-forward"])
        versions = {item["version_id"]: item for item in ledger.versions}
        derivations = {item["version_id"]: item for item in ledger.derivations}
        terminal = archive._effective_derivation(ledger.versions[-1]["version_id"], derivations, versions)
        self.assertEqual(terminal["concept_ids"], ["alpha"])

    def test_unreviewed_newline_change_archives_without_becoming_fresh(self) -> None:
        self.source.write_bytes(b"line\n")
        self._capture()
        self.source.write_bytes(b"line\r\n")
        report = self._capture()
        ledger = self._ledger()
        self.assertEqual(report["result"]["effective_status"], "captured")
        self.assertEqual(len(ledger.versions), 2)
        self.assertEqual(ledger.derivations, ())

    def test_semantic_change_reports_predecessor_concepts_and_stale_status(self) -> None:
        self.source.write_bytes(b"old\n")
        self._capture()
        self._review_current("alpha")
        self.source.write_bytes(b"new\n")
        report = self._capture()
        self.assertTrue(report["result"]["semantic_changed"])
        self.assertEqual(report["result"]["affected_concept_ids"], ["alpha"])
        self.assertEqual(report["result"]["effective_status"], "stale")
        self.assertIn("-old", report["result"]["diff"]["text"])
        self.assertIn("+new", report["result"]["diff"]["text"])

    def test_move_identity_second_live_copy_and_ambiguous_absent_matches(self) -> None:
        self.source.write_bytes(b"same\n")
        first = self._capture()
        moved = self.vault / "notes/moved.md"
        self.source.rename(moved)
        moved_report = self._capture(moved)
        self.assertEqual(moved_report["result"]["outcome"], "move")
        self.assertEqual(moved_report["result"]["document_id"], first["result"]["document_id"])
        self.assertEqual(len(self._ledger().versions), 1)

        copy = self.vault / "notes/copy.md"
        copy.write_bytes(moved.read_bytes())
        copy_report = self._capture(copy)
        self.assertNotEqual(copy_report["result"]["document_id"], first["result"]["document_id"])
        moved.unlink()
        copy.unlink()
        ambiguous = self.vault / "notes/ambiguous.md"
        ambiguous.write_bytes(b"same\n")
        with self.assertRaisesRegex(SourceArchiveError, "multiple absent documents"):
            self._capture(ambiguous)

    def test_evidence_coordinates_are_exact_for_crlf_unicode_and_final_lf(self) -> None:
        normalized = normalize_source_text("αβ\r\nx\r\n")
        version_id = "doc:12345678-1234-4234-8234-123456789abc:v00000001"
        full = {
            "version_id": version_id,
            "start_line": 1,
            "end_line": 2,
            "excerpt_sha256": archive._sha256_bytes("αβ\nx".encode("utf-8")),
        }
        self.assertEqual(verify_evidence_span(normalized, full, expected_version_id=version_id), "αβ\nx")
        unicode_column = {
            "version_id": version_id,
            "start_line": 1,
            "end_line": 1,
            "start_column": 1,
            "end_column": 2,
            "excerpt_sha256": archive._sha256_bytes("β".encode("utf-8")),
        }
        self.assertEqual(verify_evidence_span(normalized, unicode_column, expected_version_id=version_id), "β")
        final_empty = {
            "version_id": version_id,
            "start_line": 3,
            "end_line": 3,
            "excerpt_sha256": archive._sha256_bytes(b""),
        }
        with self.assertRaisesRegex(SourceArchiveError, "must not be empty"):
            extract_evidence_excerpt(normalized, final_empty, expected_version_id=version_id)
        with self.assertRaisesRegex(SourceArchiveError, "occur together"):
            extract_evidence_excerpt(normalized, {**full, "start_column": 0}, expected_version_id=version_id)

    def test_corrupt_hash_noncanonical_traversal_and_link_ledgers_fail_closed(self) -> None:
        self.source.write_bytes(b"source\n")
        self._capture()
        ledger = self._ledger()
        manifest_path = self.vault / ".kgdistiller/sources/manifest.json"
        generation = self.vault / ".kgdistiller/sources" / ledger.manifest["generation_path"]
        documents = generation / "documents.jsonl"
        original = documents.read_bytes()
        documents.write_bytes(original + b" ")
        with self.assertRaisesRegex(SourceArchiveError, "inventory does not match"):
            self._ledger()
        documents.write_bytes(original)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["documents"]["path"] = "../documents.jsonl"
        manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
        with self.assertRaises(SourceArchiveError):
            self._ledger()
        manifest_path.write_text(canonical_json(ledger.manifest), encoding="utf-8")

        target = self.root / "artifact-target"
        target.write_bytes(original)
        documents.unlink()
        try:
            documents.symlink_to(target)
        except (OSError, NotImplementedError):
            try:
                os.link(target, documents)
            except OSError:
                self.skipTest("file links are unavailable")
        with self.assertRaises(SourceArchiveError):
            self._ledger()

    def test_late_live_manifest_and_vault_manifest_races_keep_old_pointer(self) -> None:
        self.source.write_bytes(b"one\n")
        self._capture()
        manifest_path = self.vault / ".kgdistiller/sources/manifest.json"
        old_manifest = manifest_path.read_bytes()
        self.source.write_bytes(b"two\n")

        def change_live(label, resolved):
            if label == "before-final-recheck":
                self.source.write_bytes(b"three\n")

        with mock.patch.object(archive, "_capture_test_hook", side_effect=change_live):
            with self.assertRaisesRegex(SourceArchiveError, "live source changed"):
                self._capture()
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

        self.source.write_bytes(b"two\n")
        vault_manifest = self.vault / ".kgdistiller/vault.json"
        original_vault_manifest = vault_manifest.read_bytes()

        def change_vault(label, resolved):
            if label == "before-final-recheck":
                payload = json.loads(vault_manifest.read_text(encoding="utf-8"))
                payload["description"] = "changed"
                vault_manifest.write_text(json.dumps(payload), encoding="utf-8")

        with mock.patch.object(archive, "_capture_test_hook", side_effect=change_vault):
            with self.assertRaisesRegex(SourceArchiveError, "registration token changed"):
                self._capture()
        self.assertEqual(manifest_path.read_bytes(), old_manifest)
        vault_manifest.write_bytes(original_vault_manifest)

        with mock.patch.object(
            archive._PinnedDirectory,
            "replace_leaf",
            side_effect=OSError("injected publication"),
        ):
            with self.assertRaisesRegex(OSError, "injected publication"):
                self._capture()
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

    def test_source_parent_swap_after_pin_fails_without_reading_outside(self) -> None:
        self._require_directory_links()
        self.source.write_bytes(b"inside\n")
        self._capture()
        sources_manifest = self.vault / ".kgdistiller/sources/manifest.json"
        old_manifest = sources_manifest.read_bytes()
        outside = self.root / "outside-source-parent"
        outside.mkdir()
        outside_source = outside / self.source.name
        outside_source.write_bytes(b"outside\n")
        source_parent = self.source.parent
        backup = source_parent.with_name("notes-pinned-backup")
        attempted = False

        def swap(label: str, parent: Path, leaf: str) -> None:
            nonlocal attempted
            if (
                attempted
                or label != "before-leaf-open"
                or parent != source_parent
                or leaf != self.source.name
            ):
                return
            attempted = True
            try:
                source_parent.rename(backup)
            except OSError as error:
                raise SourceArchiveError(
                    "injected-ancestor-swap", "pinned source parent rejected replacement"
                ) from error
            if not self._make_directory_link(source_parent, outside):
                backup.rename(source_parent)
                raise SourceArchiveError(
                    "injected-ancestor-swap", "could not install source-parent test link"
                )

        try:
            with mock.patch.object(archive, "_anchored_test_hook", side_effect=swap):
                with self.assertRaises((SourceArchiveError, OSError)):
                    source_status(self.source, home=self.home)
        finally:
            if os.path.lexists(source_parent) and backup.exists():
                self._remove_directory_link(source_parent)
            if backup.exists():
                backup.rename(source_parent)
        self.assertTrue(attempted)
        self.assertEqual(outside_source.read_bytes(), b"outside\n")
        self.assertEqual(sources_manifest.read_bytes(), old_manifest)

    def test_publication_ancestor_swap_after_pin_keeps_old_manifest_and_outside(self) -> None:
        self._require_directory_links()
        self.source.write_bytes(b"before\n")
        self._capture()
        sources = self.vault / ".kgdistiller/sources"
        manifest = sources / "manifest.json"
        old_manifest = manifest.read_bytes()
        self.source.write_bytes(b"after\n")
        outside = self.root / "outside-publication"
        outside.mkdir()
        outside_manifest = outside / "manifest.json"
        outside_manifest.write_bytes(b"outside-sentinel")
        backup = sources.with_name("sources-pinned-backup")
        attempted = False

        def swap(label: str, parent: Path, leaf: str) -> None:
            nonlocal attempted
            if (
                attempted
                or label != "before-leaf-replace"
                or parent != sources
                or leaf != "manifest.json"
            ):
                return
            attempted = True
            try:
                sources.rename(backup)
            except OSError as error:
                raise SourceArchiveError(
                    "injected-ancestor-swap", "pinned publication parent rejected replacement"
                ) from error
            if not self._make_directory_link(sources, outside):
                backup.rename(sources)
                raise SourceArchiveError(
                    "injected-ancestor-swap", "could not install publication-parent test link"
                )

        try:
            with mock.patch.object(archive, "_anchored_test_hook", side_effect=swap):
                with self.assertRaises((SourceArchiveError, OSError)):
                    self._capture()
        finally:
            if os.path.lexists(sources) and backup.exists():
                self._remove_directory_link(sources)
            if backup.exists():
                backup.rename(sources)
        self.assertTrue(attempted)
        self.assertEqual(outside_manifest.read_bytes(), b"outside-sentinel")
        self.assertEqual(manifest.read_bytes(), old_manifest)

    def test_diff_is_bounded_and_cli_status_is_read_only_from_arbitrary_cwd(self) -> None:
        self.source.write_bytes(("\n".join(f"old-{index}" for index in range(10050)) + "\n").encode("utf-8"))
        self._capture()
        self.source.write_bytes(("\n".join(f"new-{index}" for index in range(10050)) + "\n").encode("utf-8"))
        capture = self._capture()
        self.assertTrue(capture["result"]["diff"]["truncated"])
        self.assertLessEqual(capture["result"]["diff"]["emitted_lines"], 10000)
        self.assertLessEqual(len(capture["result"]["diff"]["text"].encode("utf-8")), 1024 * 1024)
        report = diff_source(self.source, home=self.home)
        self.assertTrue(report["result"]["diff"]["truncated"])

        manifest = self.vault / ".kgdistiller/sources/manifest.json"
        before = manifest.read_bytes()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(unrelated)
            code, output, error = self._cli("source", "status", str(self.source))
        finally:
            os.chdir(previous)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output["result"]["outcome"], "current")
        self.assertEqual(manifest.read_bytes(), before)

    def test_all_f2_contracts_are_closed(self) -> None:
        self.source.write_bytes(b"source\n")
        report = self._capture()
        ledger = self._ledger()
        payloads = [report, ledger.manifest, *ledger.documents, *ledger.versions]
        for payload in payloads:
            with self.subTest(schema=payload["schema"]):
                self.assertEqual(validate_contract(payload), payload)
                with self.assertRaises(ContractError):
                    validate_contract({**payload, "unknown": True})
        self.assertEqual(ledger.documents[0]["schema"], DOCUMENT_SCHEMA)
        self.assertEqual(ledger.versions[0]["schema"], VERSION_SCHEMA)


if __name__ == "__main__":
    unittest.main()
