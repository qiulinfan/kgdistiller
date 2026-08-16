from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import kgdistiller.vault_store as vault_store_module
import kgdistiller.contracts as contracts_module
import kgdistiller.cli as cli_module
from kgdistiller.contracts import ContractError, canonical_json, validate_contract
from kgdistiller.native_compiler import sync_knowledge
from kgdistiller.recall import execute_recall_request, make_recall_request, recall_status
from kgdistiller.source_archive import capture_source, load_source_ledger
from kgdistiller.vault_ingest import CAPABILITY, REQUEST_SCHEMA, apply_vault_ingest
from kgdistiller.vault_store import snapshot_vault_store, verify_vault_store
from kgdistiller.vaults import (
    init_vault,
    add_vault,
    load_registry,
    load_vault,
    managed_markdown_token,
    snapshot_managed_markdown,
)


def _concept(node_id: str, label: str) -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-concept-v1",
            f"kgd_id: {node_id}",
            "aliases: []",
            "tags: [kgdistiller/concept]",
            "kgd_fields: [\"[[Knowledge/Fields/Test]]\"]",
            "kgd_topics: []",
            "kgd_prerequisites: []",
            "kgd_implies: []",
            "kgd_generalizes: []",
            "kgd_contrasts_with: []",
            "kgd_derived_from: []",
            "---",
            "",
            f"# {label}",
            "",
            "Portable native authority.",
            "",
        ]
    )


def _field() -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-taxonomy-v1",
            "kgd_id: test-field",
            "kgd_kind: field",
            "aliases: []",
            "kgd_parents: []",
            "---",
            "",
            "# Test",
            "",
            "Portable field authority.",
            "",
        ]
    )


def _topic() -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-taxonomy-v1",
            "kgd_id: test-topic",
            "kgd_kind: topic",
            "aliases: []",
            "kgd_parents: [\"[[Knowledge/Fields/Test]]\"]",
            "---",
            "",
            "# Test Topic",
            "",
            "Portable topic authority.",
            "",
        ]
    )


class VaultStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-vault-store-")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.vault = self.root / "Vault"
        init_vault(
            self.vault,
            vault_id="portable",
            label="Portable Vault",
            home=self.home,
        )
        (self.vault / "Knowledge/Concepts/Alpha.md").write_text(
            _concept("alpha", "Alpha"), encoding="utf-8"
        )
        (self.vault / "Knowledge/Fields/Test.md").write_text(
            _field(), encoding="utf-8"
        )
        sync_knowledge(home=self.home)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_cli(
        self, *arguments: str, registry_home: Path | None = None
    ) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        arbitrary = self.root / "arbitrary-cwd"
        arbitrary.mkdir(exist_ok=True)
        previous = Path.cwd()
        try:
            os.chdir(arbitrary)
            with mock.patch.dict(
                os.environ,
                {
                    "KGDISTILLER_HOME": str(
                        self.home if registry_home is None else registry_home
                    )
                },
            ), mock.patch.object(
                sys, "argv", ["kgdistiller", *arguments]
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = cli_module.main()
        finally:
            os.chdir(previous)
        payload_text = stdout.getvalue() if status == 0 else stderr.getvalue()
        return status, json.loads(payload_text), stderr.getvalue()

    def test_in_place_snapshot_and_pure_verify_close_empty_layout(self) -> None:
        report = snapshot_vault_store("portable", home=self.home)
        self.assertEqual("snapshot", report["action"])
        self.assertEqual("verified", report["status"])
        self.assertEqual("in-place", report["layout"])
        self.assertIsNone(report["source_generation_sha256"])

        store_path = self.vault / ".kgdistiller/store.json"
        manifest = validate_contract(
            __import__("json").loads(store_path.read_text(encoding="utf-8"))
        )
        self.assertEqual("qlkg-vault-store-v3", manifest["schema"])
        self.assertEqual(report["store_sha256"], manifest["store_sha256"])
        self.assertEqual(
            [
                ".kgdistiller/.gitattributes",
                ".kgdistiller/build/.gitignore",
                ".kgdistiller/sources/.gitkeep",
                "Knowledge/Topics/.gitkeep",
            ],
            [item["path"] for item in manifest["scaffolds"]],
        )
        self.assertNotIn(str(self.root), canonical_json(manifest))

        verified = verify_vault_store(self.vault)
        self.assertEqual("verify", verified["action"])
        self.assertEqual(report["store_sha256"], verified["store_sha256"])

    def test_native_v3_verify_is_closed_and_does_not_mutate_legacy_v2_bytes(self) -> None:
        store_path = self.vault / ".kgdistiller/store.json"
        before_missing = {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in sorted(self.vault.rglob("*"))
            if path.is_file()
        }
        with self.assertRaises(vault_store_module.VaultStoreError) as missing:
            verify_vault_store(self.vault)
        self.assertEqual("missing-vault-store", missing.exception.code)
        self.assertEqual(
            before_missing,
            {
                path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in sorted(self.vault.rglob("*"))
                if path.is_file()
            },
        )

        digest = "a" * 64
        legacy = contracts_module.finalize_self_digest(
            {
                "schema": "qlkg-store-v2",
                "generator": "kgdistiller",
                "layout": "in-place",
                "paths": {
                    "registry": "knowledge/sources.json",
                    "identities": None,
                    "alignments": "knowledge/alignments.json",
                    "graph": "knowledge/graph",
                    "documents": "knowledge/documents.jsonl",
                },
                "documents": {
                    "count": 0,
                    "sha256": digest,
                    "source_snapshot_sha256": digest,
                },
                "graph_artifacts": [
                    {
                        "path": "knowledge/graph/manifest.json",
                        "bytes": 1,
                        "sha256": digest,
                    },
                    {
                        "path": "knowledge/graph/nodes.jsonl",
                        "bytes": 0,
                        "sha256": digest,
                    },
                    {
                        "path": "knowledge/graph/edges.jsonl",
                        "bytes": 0,
                        "sha256": digest,
                    },
                    {
                        "path": "knowledge/graph/references.jsonl",
                        "bytes": 0,
                        "sha256": digest,
                    },
                    {
                        "path": "knowledge/graph/diagnostics.json",
                        "bytes": 1,
                        "sha256": digest,
                    },
                ],
                "registry_sha256": digest,
                "identity_sha256": None,
                "alignment_sha256": digest,
                "graph_sha256": digest,
                "store_generation_sha256": digest,
                "managed_paths": [
                    "knowledge/documents.jsonl",
                    "knowledge/store.json",
                ],
                "store_sha256": "0" * 64,
            },
            "store_sha256",
        )
        self.assertEqual(legacy, validate_contract(legacy))
        legacy_bytes = canonical_json(legacy).encode("utf-8") + b"\n"
        store_path.write_bytes(legacy_bytes)
        with self.assertRaises(vault_store_module.VaultStoreError) as legacy_error:
            verify_vault_store(self.vault)
        self.assertEqual("invalid-vault-store", legacy_error.exception.code)
        self.assertEqual(legacy_bytes, store_path.read_bytes())
        self.assertEqual(legacy, validate_contract(json.loads(store_path.read_text("utf-8"))))

    def test_snapshot_copy_is_exact_verified_and_no_clobber(self) -> None:
        output = self.root / "PortableCopy"
        report = snapshot_vault_store("portable", home=self.home, output=output)
        self.assertEqual("snapshot-copy", report["layout"])
        self.assertEqual("snapshot", report["action"])
        self.assertFalse((self.vault / ".kgdistiller/store.json").exists())
        self.assertEqual(
            report["store_sha256"], verify_vault_store(output)["store_sha256"]
        )
        self.assertFalse(
            any(
                path.name.startswith(".kgdistiller-store-")
                for path in self.root.iterdir()
            )
        )

        marker = output / "third-party"
        marker.write_bytes(b"preserve")
        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "output already exists"
        ):
            snapshot_vault_store("portable", home=self.home, output=output)
        self.assertEqual(b"preserve", marker.read_bytes())

    def test_cli_snapshot_and_registry_independent_verify_from_arbitrary_cwd(self) -> None:
        output = self.root / "CliCopy"
        status, snapshot, error = self._run_cli(
            "vault", "snapshot", "portable", "--output", str(output)
        )
        self.assertEqual(0, status)
        self.assertEqual("snapshot", snapshot["action"])
        self.assertEqual("", error)

        status, verified, error = self._run_cli(
            "vault",
            "verify",
            str(output),
            registry_home=self.root / "missing-home",
        )
        self.assertEqual(0, status)
        self.assertEqual("verify", verified["action"])
        self.assertEqual(snapshot["store_sha256"], verified["store_sha256"])
        self.assertEqual("", error)

        status, failure, _ = self._run_cli("vault", "snapshot", "unknown-vault")
        self.assertEqual(1, status)
        self.assertEqual("kgdistiller-vault-store-error", failure["kind"])
        self.assertNotIn(str(self.root), canonical_json(failure))

    def test_snapshot_copy_preserves_racing_output_and_heals_postinstall_error(self) -> None:
        raced = self.root / "RacedCopy"

        def create_racing_output(label: str, path: str) -> None:
            if label == "before-snapshot-directory-install":
                raced.mkdir()
                (raced / "third-party").write_bytes(b"preserve")

        with mock.patch.object(
            vault_store_module,
            "_vault_store_hook",
            side_effect=create_racing_output,
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home, output=raced)
        self.assertEqual(b"preserve", (raced / "third-party").read_bytes())
        preserved_stages = [
            path
            for path in self.root.iterdir()
            if path.name.startswith(".kgdistiller-store-")
        ]
        self.assertEqual(1, len(preserved_stages))
        self.assertEqual(
            "snapshot-copy", verify_vault_store(preserved_stages[0])["layout"]
        )

        healed = self.root / "HealedCopy"
        fired = False

        def fail_after_install(label: str, path: str) -> None:
            nonlocal fired
            if label == "after-snapshot-directory-install" and not fired:
                fired = True
                raise OSError("injected")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=fail_after_install
        ):
            report = snapshot_vault_store("portable", home=self.home, output=healed)
        self.assertEqual(report["store_sha256"], verify_vault_store(healed)["store_sha256"])

    def test_snapshot_copy_rejects_protected_output_overlap_without_writes(self) -> None:
        for output in (
            self.vault / "Copy",
            self.home / "Copy",
            self.root,
        ):
            before = set(self.root.rglob("*"))
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home, output=output)
            self.assertEqual(before, set(self.root.rglob("*")))

    def test_snapshot_copy_retains_output_parent_across_every_stage_write(self) -> None:
        parent = self.root / "DestinationParent"
        parent.mkdir()
        moved = self.root / "MovedDestinationParent"
        output = parent / "Copy"
        fired = False
        blocked = False

        def swap_parent(label: str, path: str) -> None:
            nonlocal fired, blocked
            if label != "after-snapshot-stage-file" or fired:
                return
            fired = True
            if __import__("os").name == "nt":
                try:
                    parent.rename(moved)
                except OSError:
                    blocked = True
                return
            try:
                parent.rename(moved)
                parent.mkdir()
            except OSError:
                blocked = True

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=swap_parent
        ):
            try:
                report = snapshot_vault_store("portable", home=self.home, output=output)
            except vault_store_module.VaultStoreError:
                if __import__("os").name != "nt" and parent.exists():
                    self.assertEqual([], list(parent.iterdir()))
                self.assertFalse(output.exists())
                self.assertFalse((moved / output.name).exists())
            else:
                self.assertTrue(blocked)
                self.assertEqual("snapshot-copy", report["layout"])
            if __import__("os").name != "nt":
                with self.assertRaises(vault_store_module.VaultStoreError):
                    verify_vault_store(output)

    def test_snapshot_copy_resumes_exact_bootstrap_reachable_states(self) -> None:
        vault = load_vault(self.vault)
        captured = vault_store_module._capture_store(vault, layout="snapshot-copy")
        states = [("empty", "empty"), ("temp", "temp")]
        if __import__("os").name != "nt":
            states.append(("linked", "linked"))
        for suffix, state in states:
            output = self.root / f"Bootstrap-{suffix}"
            stage_leaf = vault_store_module._snapshot_stage_name(output)
            bootstrap = self.root / vault_store_module._snapshot_bootstrap_name(stage_leaf)
            bootstrap.mkdir()
            marker = vault_store_module._snapshot_stage_marker_bytes(
                output, captured.manifest
            )
            temporary = bootstrap / f".{vault_store_module.STAGE_MARKER_LEAF}.write"
            final = bootstrap / vault_store_module.STAGE_MARKER_LEAF
            if state == "temp":
                temporary.write_bytes(marker)
            elif state == "linked":
                temporary.write_bytes(marker)
                __import__("os").link(temporary, final)
            report = snapshot_vault_store("portable", home=self.home, output=output)
            self.assertEqual("snapshot-copy", report["layout"])
            self.assertFalse(bootstrap.exists())

    def test_snapshot_copy_resumes_authority_newline_variants_from_temp_and_final(self) -> None:
        relative = "Knowledge/Concepts/Alpha.md"
        live = self.vault / relative
        for state in ("temporary", "final"):
            with self.subTest(state=state):
                destination = self.root / f"Newline-{state}"
                captured = vault_store_module._capture_store(
                    load_vault(self.vault), layout="snapshot-copy"
                )
                record = next(
                    item
                    for item in captured.manifest["authority"]["artifacts"]
                    if item["path"] == relative
                )
                staged_raw = live.read_bytes()
                with vault_store_module._PinnedDirectory(self.root) as output_parent:
                    stage_path, stage, stage_metadata, complete = (
                        vault_store_module._prepare_snapshot_stage(
                            captured, destination, output_parent
                        )
                    )
                    self.assertFalse(complete)
                    identities = {"": stage_metadata}
                    try:
                        for directory in sorted(
                            vault_store_module._parent_directories([relative]),
                            key=lambda item: (len(Path(item).parts), item),
                        ):
                            vault_store_module._ensure_retained_stage_directory(
                                stage, directory, identities
                            )
                        stack, parent = (
                            vault_store_module._open_retained_stage_directory(
                                stage, Path(relative).parent.as_posix(), identities
                            )
                        )
                        with stack:
                            leaf = (
                                Path(relative).name
                                if state == "final"
                                else vault_store_module._authority_stage_temporary(
                                    relative, record
                                )
                            )
                            descriptor = parent.create_file(leaf, readable=True)
                            try:
                                os.write(descriptor, staged_raw)
                                os.fsync(descriptor)
                            finally:
                                os.close(descriptor)
                            parent.verify_current()
                    finally:
                        stage.close()
                    self.assertTrue(stage_path.is_dir())

                if b"\r\n" in staged_raw:
                    flipped = staged_raw.replace(b"\r\n", b"\n")
                else:
                    flipped = staged_raw.replace(b"\n", b"\r\n")
                self.assertNotEqual(staged_raw, flipped)
                live.write_bytes(flipped)
                report = snapshot_vault_store(
                    "portable", home=self.home, output=destination
                )
                self.assertEqual("snapshot-copy", report["layout"])
                self.assertEqual(staged_raw, (destination / relative).read_bytes())

    def test_snapshot_copy_rejects_registered_stage_collision_before_writes(self) -> None:
        output = self.root / "CollisionOutput"
        stage = self.root / vault_store_module._snapshot_stage_name(output)
        init_vault(
            stage,
            vault_id="stage-collision",
            label="Stage Collision",
            home=self.home,
        )
        before = {
            path.relative_to(stage).as_posix(): path.read_bytes()
            for path in stage.rglob("*")
            if path.is_file()
        }
        with self.assertRaises(vault_store_module.VaultStoreError):
            snapshot_vault_store("portable", home=self.home, output=output)
        after = {
            path.relative_to(stage).as_posix(): path.read_bytes()
            for path in stage.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse(output.exists())

    def test_snapshot_copy_never_moves_unowned_bootstrap_content(self) -> None:
        output = self.root / "BootstrapThirdParty"
        stage_leaf = vault_store_module._snapshot_stage_name(output)
        bootstrap = self.root / vault_store_module._snapshot_bootstrap_name(stage_leaf)
        bootstrap.mkdir()
        third_party = bootstrap / "third-party"
        third_party.write_bytes(b"preserve")

        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "unowned entry"
        ):
            snapshot_vault_store("portable", home=self.home, output=output)
        self.assertEqual(b"preserve", third_party.read_bytes())
        self.assertFalse((self.root / stage_leaf).exists())
        self.assertFalse(output.exists())

    def test_snapshot_copy_revalidates_complete_stage_after_install_hook(self) -> None:
        output = self.root / "MutatedStage"
        stage = self.root / vault_store_module._snapshot_stage_name(output)

        def mutate_stage(label: str, path: str) -> None:
            if label == "before-snapshot-directory-install":
                (stage / "third-party").write_bytes(b"preserve")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=mutate_stage
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home, output=output)
        self.assertFalse(output.exists())
        self.assertEqual(b"preserve", (stage / "third-party").read_bytes())

    def test_store_pointer_rechecks_third_party_destination_without_hook_error(self) -> None:
        snapshot_vault_store("portable", home=self.home)
        concept = self.vault / "Knowledge/Concepts/Alpha.md"
        concept.write_text(_concept("alpha", "Alpha Updated"), encoding="utf-8")
        sync_knowledge(home=self.home)

        def replace_destination(label: str, path: str) -> None:
            if label == "before-store-replace":
                (self.vault / ".kgdistiller/store.json").write_bytes(b"third-party")

        with mock.patch.object(
            vault_store_module,
            "_vault_store_hook",
            side_effect=replace_destination,
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home)
        self.assertEqual(
            b"third-party", (self.vault / ".kgdistiller/store.json").read_bytes()
        )

    def test_store_pointer_binds_staged_inode_across_replace_hook(self) -> None:
        first = snapshot_vault_store("portable", home=self.home)
        old_store = (self.vault / ".kgdistiller/store.json").read_bytes()
        concept = self.vault / "Knowledge/Concepts/Alpha.md"
        concept.write_text(_concept("alpha", "Alpha Updated"), encoding="utf-8")
        sync_knowledge(home=self.home)
        swapped = False
        blocked = False

        def swap_stage(label: str, path: str) -> None:
            nonlocal swapped, blocked
            if label != "before-store-replace":
                return
            staged = next(
                item
                for item in (self.vault / ".kgdistiller/build").iterdir()
                if item.name.startswith(".store-") and item.name.endswith(".json")
            )
            moved = staged.with_name(".swapped-store-stage")
            try:
                staged.rename(moved)
            except OSError:
                blocked = True
                return
            swapped = True
            staged.write_bytes(b"third-party")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=swap_stage
        ):
            if os.name == "nt":
                report = snapshot_vault_store("portable", home=self.home)
                self.assertTrue(blocked)
                self.assertNotEqual(first["store_sha256"], report["store_sha256"])
            else:
                with self.assertRaises(vault_store_module.VaultStoreError):
                    snapshot_vault_store("portable", home=self.home)
                self.assertTrue(swapped)
                self.assertEqual(
                    old_store, (self.vault / ".kgdistiller/store.json").read_bytes()
                )

    def test_public_verify_allows_only_explicit_excluded_local_state(self) -> None:
        output = self.root / "LocalStateClone"
        report = snapshot_vault_store("portable", home=self.home, output=output)
        (output / ".git").mkdir()
        (output / ".git/config").write_bytes(b"local")
        (output / ".obsidian").mkdir()
        (output / ".obsidian/workspace.json").write_bytes(b"local")
        (output / "Unmanaged").mkdir()
        (output / "Unmanaged/source.typ").write_bytes(b"local")
        (output / ".kgdistiller/build/cache.bin").write_bytes(b"local")
        orphan_generation = output / ".kgdistiller/sources/generations" / ("a" * 64)
        orphan_generation.mkdir(parents=True)
        (orphan_generation / "documents.jsonl").write_bytes(b"local")
        orphan_blob = (
            output
            / ".kgdistiller/sources/blobs/sha256/aa"
            / ("a" * 64)
        )
        orphan_blob.parent.mkdir(parents=True)
        orphan_blob.write_bytes(b"local")
        (output / "Knowledge/Concepts/Empty/Nested").mkdir(parents=True)
        (output / ".kgdistiller/receipts/sha256/ab").mkdir(parents=True)

        verified = verify_vault_store(output)
        self.assertEqual(report["store_sha256"], verified["store_sha256"])

        escaped = output / ".kgdistiller/escape"
        escaped.write_bytes(b"not-excluded")
        with self.assertRaises(vault_store_module.VaultStoreError):
            verify_vault_store(output)
        escaped.unlink()
        receipt_escape = output / ".kgdistiller/receipts/escape"
        receipt_escape.write_bytes(b"not-a-receipt-namespace")
        with self.assertRaises(vault_store_module.VaultStoreError):
            verify_vault_store(output)
        receipt_escape.unlink()
        unmanaged_authority = output / "Knowledge/Concepts/unmanaged.bin"
        unmanaged_authority.write_bytes(b"not-authority")
        with self.assertRaises(vault_store_module.VaultStoreError):
            verify_vault_store(output)

    def test_empty_local_directories_do_not_consume_managed_file_cap(self) -> None:
        first = snapshot_vault_store("portable", home=self.home)
        manifest = validate_contract(
            json.loads(
                (self.vault / ".kgdistiller/store.json").read_text(encoding="utf-8")
            )
        )
        for relative in ("Empty/A", "Empty/B", "Empty/C/Nested"):
            (self.vault / "Knowledge/Concepts" / relative).mkdir(parents=True)
        with mock.patch.object(
            vault_store_module, "MAX_STORE_FILES", len(manifest["managed_paths"])
        ):
            refreshed = snapshot_vault_store("portable", home=self.home)
            verified = verify_vault_store(self.vault)
        self.assertEqual(first["store_sha256"], refreshed["store_sha256"])
        self.assertEqual(refreshed["store_sha256"], verified["store_sha256"])

    def test_stale_empty_scaffolds_survive_live_transition_but_are_not_copied(self) -> None:
        snapshot_vault_store("portable", home=self.home)
        topic_gitkeep = self.vault / "Knowledge/Topics/.gitkeep"
        source_gitkeep = self.vault / ".kgdistiller/sources/.gitkeep"
        self.assertTrue(topic_gitkeep.is_file())
        self.assertTrue(source_gitkeep.is_file())

        (self.vault / "Knowledge/Topics/Test.md").write_text(
            _topic(), encoding="utf-8"
        )
        live_source = self.vault / "Sources/Reference.md"
        live_source.parent.mkdir()
        live_source.write_text("# Reference\n\nPortable source.\n", encoding="utf-8")
        capture_source(live_source, home=self.home)
        sync_knowledge(home=self.home)

        report = snapshot_vault_store("portable", home=self.home)
        self.assertTrue(topic_gitkeep.is_file())
        self.assertTrue(source_gitkeep.is_file())
        self.assertEqual(report["store_sha256"], verify_vault_store(self.vault)["store_sha256"])

        output = self.root / "TransitionClone"
        copied = snapshot_vault_store("portable", home=self.home, output=output)
        self.assertEqual(report["content_generation_sha256"], copied["content_generation_sha256"])
        self.assertFalse((output / "Knowledge/Topics/.gitkeep").exists())
        self.assertFalse((output / ".kgdistiller/sources/.gitkeep").exists())
        self.assertEqual(copied["store_sha256"], verify_vault_store(output)["store_sha256"])

    def test_snapshot_copy_retains_nonempty_ledger_blobs_and_durable_receipts(self) -> None:
        source = self.vault / "Sources/Evidence.md"
        source.parent.mkdir()
        source.write_text("Alpha evidence.\n", encoding="utf-8")
        captured = capture_source(source, home=self.home)
        version_id = captured["result"]["current_version_id"]
        query = self.root / "query.json"
        query.write_bytes(
            canonical_json(
                recall_status(home=self.home, vault_ids=("portable",))
            ).encode("utf-8")
            + b"\n"
        )
        registry = load_registry(self.home, validate_vaults=False)
        vault = load_vault(self.vault, expected_id="portable")
        ledger = load_source_ledger(vault)
        graph_manifest = json.loads(
            (self.vault / ".kgdistiller/graph/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        note_token = contracts_module.sha256_json(
            [
                list(item)
                for item in managed_markdown_token(snapshot_managed_markdown(vault))
            ]
        )
        request = contracts_module.finalize_self_digest(
            {
                "schema": REQUEST_SCHEMA,
                "request_id": "portable-store-receipt",
                "request_sha256": "0" * 64,
                "capabilities": [CAPABILITY],
                "vault_id": "portable",
                "registry_generation": registry.generation,
                "vault_manifest_sha256": contracts_module.sha256_json(vault.manifest),
                "base": {
                    "source_ledger_generation_sha256": ledger.generation_sha256,
                    "graph_generation_sha256": graph_manifest["graph_sha256"],
                    "note_inventory_sha256": note_token,
                },
                "query_report": {
                    "path": "query.json",
                    "sha256": vault_store_module._sha256(query.read_bytes()),
                },
                "note_patches": [],
                "derivation_updates": [
                    {
                        "version_id": version_id,
                        "status": "committed",
                        "candidate_dispositions": [
                            {"candidate_id": "alpha", "disposition": "reuse"}
                        ],
                        "concept_ids": ["alpha"],
                        "concept_evidence": [
                            {
                                "concept_id": "alpha",
                                "spans": [
                                    {
                                        "version_id": version_id,
                                        "start_line": 1,
                                        "end_line": 1,
                                        "excerpt_sha256": vault_store_module._sha256(
                                            b"Alpha evidence."
                                        ),
                                    }
                                ],
                            }
                        ],
                        "relation_evidence": [
                            {
                                "source": "test-field",
                                "relation": "contains",
                                "target": "alpha",
                                "spans": [
                                    {
                                        "version_id": version_id,
                                        "start_line": 1,
                                        "end_line": 1,
                                        "excerpt_sha256": vault_store_module._sha256(
                                            b"Alpha evidence."
                                        ),
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "alignment_mutations": [],
                "review": {
                    "status": "reviewed",
                    "reviewer": "vault-store-test",
                    "evidence": "portable store receipt integration was reviewed",
                    "provenance": "tests/test_vault_store.py",
                },
            },
            "request_sha256",
        )
        receipt = apply_vault_ingest(request, request_root=self.root, home=self.home)

        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian/workspace.json").write_bytes(b"machine-local")
        (self.vault / ".kgdistiller/build/cache.bin").write_bytes(b"machine-local")
        old_generation = (
            self.vault
            / ".kgdistiller/sources/generations"
            / ("f" * 64)
        )
        old_generation.mkdir()
        (old_generation / "old.jsonl").write_bytes(b"unreferenced\n")
        unreferenced_blob = (
            self.vault
            / ".kgdistiller/sources/blobs/sha256/ee"
            / ("e" * 64)
        )
        unreferenced_blob.parent.mkdir(parents=True, exist_ok=True)
        unreferenced_blob.write_bytes(b"unreferenced")

        output = self.root / "EvidenceClone"
        report = snapshot_vault_store("portable", home=self.home, output=output)
        verified = verify_vault_store(output)
        manifest = validate_contract(
            json.loads(
                (output / ".kgdistiller/store.json").read_text(encoding="utf-8")
            )
        )
        self.assertEqual(report["store_sha256"], verified["store_sha256"])
        self.assertIsNotNone(manifest["source"]["generation_sha256"])
        self.assertEqual(1, manifest["receipts"]["count"])
        for section in (
            manifest["source"]["artifacts"],
            manifest["source"]["blobs"],
            manifest["receipts"]["artifacts"],
            manifest["graph"]["artifacts"],
        ):
            for record in section:
                self.assertTrue(
                    output.joinpath(*Path(record["path"]).parts).is_file()
                )
        receipt_record = manifest["receipts"]["artifacts"][0]
        self.assertEqual(receipt["receipt_sha256"], receipt_record["receipt_sha256"])
        receipt_path = output.joinpath(*Path(receipt_record["path"]).parts)
        self.assertEqual(
            receipt_record["sha256"],
            vault_store_module._sha256(receipt_path.read_bytes()),
        )
        cloned_ledger = load_source_ledger(load_vault(output, expected_id="portable"))
        committed = [
            item for item in cloned_ledger.derivations if item["status"] == "committed"
        ]
        self.assertEqual(receipt["receipt_sha256"], committed[0]["ingest_receipt_sha256"])
        self.assertFalse((output / "Sources/Evidence.md").exists())
        self.assertFalse((output / ".obsidian").exists())
        self.assertFalse((output / ".kgdistiller/build/cache.bin").exists())
        self.assertFalse(
            output.joinpath(
                ".kgdistiller", "sources", "generations", "f" * 64
            ).exists()
        )
        self.assertFalse(
            output.joinpath(
                ".kgdistiller", "sources", "blobs", "sha256", "ee", "e" * 64
            ).exists()
        )
        self.assertFalse((output / ".kgdistiller/registry.json").exists())

        clone_home = self.root / "clone-home"
        added = add_vault(output, home=clone_home)
        self.assertEqual("ok", added["status"])
        recalled = execute_recall_request(
            make_recall_request(
                "search", vault_ids=["portable"], query="Alpha", limit=10
            ),
            home=clone_home,
        )
        self.assertIn(
            "portable:alpha",
            [item["handle"] for item in recalled["result"]["nodes"]],
        )

        live_vault = load_vault(self.vault, expected_id="portable")
        live_ledger = load_source_ledger(live_vault)
        authority = vault_store_module._authority_inventory(live_vault)[1]
        baseline = copy.deepcopy(live_ledger.derivations[0])
        span = copy.deepcopy(baseline["concept_evidence"][0]["spans"][0])
        forged_concept = copy.deepcopy(baseline)
        forged_concept["concept_evidence"].append(
            {"concept_id": "orphan-concept", "spans": [span]}
        )
        forged_relation = copy.deepcopy(baseline)
        forged_relation["relation_evidence"].append(
            {
                "source": "alpha",
                "relation": "implies",
                "target": "alpha",
                "spans": [span],
            }
        )
        vault_store_module._graph_inventory(live_vault, authority, live_ledger)
        for derivation in (forged_concept, forged_relation):
            forged_ledger = replace(live_ledger, derivations=(derivation,))
            with self.assertRaisesRegex(
                vault_store_module.VaultStoreError, "closed over effective source evidence"
            ):
                vault_store_module._graph_inventory(
                    live_vault, authority, forged_ledger
                )

        wrong_graph = copy.deepcopy(live_ledger.derivations[0])
        wrong_graph["graph_generation_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "does not match its durable receipt"
        ):
            vault_store_module._receipt_inventory(
                live_vault, replace(live_ledger, derivations=(wrong_graph,))
            )

        unrelated = copy.deepcopy(receipt)
        unrelated["request_id"] = "unrelated-receipt"
        unrelated["request_sha256"] = "f" * 64
        unrelated["after"]["derivations"] = []
        unrelated["changes"]["derivation_version_ids"] = []
        unrelated = contracts_module.finalize_self_digest(
            unrelated, "receipt_sha256"
        )
        validate_contract(unrelated)
        unrelated_path = self.vault.joinpath(
            ".kgdistiller",
            "receipts",
            "sha256",
            unrelated["receipt_sha256"][:2],
            f"{unrelated['receipt_sha256']}.json",
        )
        unrelated_path.parent.mkdir(exist_ok=True)
        unrelated_path.write_bytes(
            canonical_json(unrelated).encode("utf-8") + b"\n"
        )
        wrong_receipt = copy.deepcopy(live_ledger.derivations[0])
        wrong_receipt["ingest_receipt_sha256"] = unrelated["receipt_sha256"]
        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "does not match its durable receipt"
        ):
            vault_store_module._receipt_inventory(
                live_vault, replace(live_ledger, derivations=(wrong_receipt,))
            )

    def test_authority_roots_are_portable_store_paths_before_any_write(self) -> None:
        vault = load_vault(self.vault)
        captured = vault_store_module._capture_store(vault, layout="snapshot-copy")
        forbidden = copy.deepcopy(captured.manifest)
        forbidden["authority"]["roots"][0]["path"] = "Knowledge/.GiT/Concepts"
        with self.assertRaises(ContractError):
            validate_contract(forbidden)
        overlapping = copy.deepcopy(captured.manifest)
        overlapping["authority"]["roots"][1]["path"] = "knowledge/concepts/Fields"
        with self.assertRaises(ContractError):
            validate_contract(overlapping)

        manifest_path = self.vault / ".kgdistiller/vault.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement = self.vault / "Portable/.ObSiDiAn/Concepts"
        replacement.parent.mkdir(parents=True)
        shutil.move(str(self.vault / manifest["concept_root"]), replacement)
        manifest["concept_root"] = "Portable/.ObSiDiAn/Concepts"
        manifest_path.write_bytes(
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
        )
        output = self.root / "ForbiddenRootCopy"
        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "excluded local-state"
        ):
            snapshot_vault_store("portable", home=self.home, output=output)
        self.assertFalse(output.exists())
        self.assertFalse((self.vault / ".kgdistiller/store.json").exists())

    def test_store_pointer_recaptures_all_controlled_content_after_hook(self) -> None:
        concept = self.vault / "Knowledge/Concepts/Alpha.md"
        original = concept.read_bytes()

        def mutate_authority(label: str, path: str) -> None:
            if label == "before-store-replace":
                concept.write_bytes(original + b"Editor change.\n")

        with mock.patch.object(
            vault_store_module,
            "_vault_store_hook",
            side_effect=mutate_authority,
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home)

        self.assertEqual(original + b"Editor change.\n", concept.read_bytes())
        self.assertFalse((self.vault / ".kgdistiller/store.json").exists())
        self.assertFalse((self.vault / ".kgdistiller/.gitattributes").exists())
        self.assertFalse(
            any(path.name.startswith(".kgdistiller-store-") for path in self.root.iterdir())
        )

    def test_in_place_prepublish_rejects_untracked_physical_content(self) -> None:
        for extra in (
            self.vault / ".kgdistiller/escape",
            self.vault / "Knowledge/Concepts/unmanaged.bin",
        ):
            extra.write_bytes(b"third-party")
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home)
            self.assertEqual(b"third-party", extra.read_bytes())
            self.assertFalse((self.vault / ".kgdistiller/store.json").exists())
            self.assertFalse((self.vault / ".kgdistiller/.gitattributes").exists())
            self.assertFalse((self.vault / ".kgdistiller/build/.gitignore").exists())
            extra.unlink()

    def test_snapshot_copy_rejects_untracked_source_namespace_before_stage(self) -> None:
        for index, extra in enumerate(
            (
                self.vault / ".kgdistiller/escape",
                self.vault / "Knowledge/Concepts/unmanaged.bin",
            )
        ):
            output = self.root / f"RejectedPhysicalCopy{index}"
            extra.write_bytes(b"third-party")
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home, output=output)
            self.assertFalse(output.exists())
            self.assertFalse(
                any(path.name.startswith(".kgdistiller-store-") for path in self.root.iterdir())
            )
            self.assertEqual(b"third-party", extra.read_bytes())
            extra.unlink()

    def test_external_overlap_is_nfc_casefold_conservative_and_zero_write(self) -> None:
        before = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        with self.assertRaises(vault_store_module.VaultStoreError):
            snapshot_vault_store(
                "portable", home=self.home, output=self.root / "vault/Copy"
            )
        after = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

        composed = self.root / "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
        decomposed = self.root / "Cafe\N{COMBINING ACUTE ACCENT}" / "Copy"
        registry = SimpleNamespace(home=composed, registrations=())
        with self.assertRaises(vault_store_module.VaultStoreError):
            vault_store_module._external_destination(
                decomposed,
                registry=registry,
                vault=load_vault(self.vault),
                require_absent=True,
            )
        self.assertFalse(decomposed.parent.exists())
        self.assertEqual(
            vault_store_module._snapshot_stage_name(Path("R\N{LATIN SMALL LETTER E WITH ACUTE}sum\N{LATIN SMALL LETTER E WITH ACUTE}")),
            vault_store_module._snapshot_stage_name(Path("Re\N{COMBINING ACUTE ACCENT}sume\N{COMBINING ACUTE ACCENT}")),
        )

    def test_public_verify_maps_declared_file_races_to_closed_error(self) -> None:
        snapshot_vault_store("portable", home=self.home)
        underlying = vault_store_module.SourceArchiveError(
            "unsafe-ledger-path", f"absolute path {self.vault}"
        )
        with mock.patch.object(
            vault_store_module, "_read_record", side_effect=underlying
        ):
            with self.assertRaises(vault_store_module.VaultStoreError) as raised:
                verify_vault_store(self.vault)
        payload = raised.exception.payload()
        self.assertEqual("vault-store-verify-failed", payload["code"])
        self.assertNotIn(str(self.root), canonical_json(payload))

    def test_snapshot_stage_marker_does_not_consume_final_store_limits(self) -> None:
        vault = load_vault(self.vault)
        captured = vault_store_module._capture_store(vault, layout="snapshot-copy")
        _, scaffolds = vault_store_module._scaffold_inventory(
            captured.manifest["authority"]["roots"],
            captured.manifest["authority"]["artifacts"],
            source_present=captured.manifest["source"]["manifest"] is not None,
        )
        actual = len(vault_store_module._store_bytes(captured.manifest))
        for record in vault_store_module._manifest_records(captured.manifest):
            actual += len(
                vault_store_module._snapshot_file_bytes(vault, record, scaffolds)
            )
        output = self.root / "ExactStageLimit"
        with mock.patch.object(
            vault_store_module, "MAX_VAULT_STORE_BYTES", actual
        ), mock.patch.object(
            vault_store_module,
            "MAX_STORE_FILES",
            len(captured.manifest["managed_paths"]),
        ):
            report = snapshot_vault_store("portable", home=self.home, output=output)
        self.assertEqual("snapshot-copy", report["layout"])

    def test_vault_manifest_must_be_lf_only_and_store_counts_itself(self) -> None:
        snapshot_vault_store("portable", home=self.home)
        store = validate_contract(
            __import__("json").loads(
                (self.vault / ".kgdistiller/store.json").read_text(encoding="utf-8")
            )
        )
        records_only = sum(
            vault_store_module._record_size(record)
            for record in vault_store_module._manifest_records(store)
        )
        with mock.patch.object(contracts_module, "MAX_VAULT_STORE_BYTES", records_only):
            with self.assertRaises(ContractError):
                validate_contract(store)

        manifest_path = self.vault / ".kgdistiller/vault.json"
        manifest_path.write_bytes(manifest_path.read_bytes().replace(b"\n", b"\r\n"))
        output = self.root / "CrLfCopy"
        with self.assertRaisesRegex(
            vault_store_module.VaultStoreError, "LF-only"
        ):
            snapshot_vault_store("portable", home=self.home, output=output)
        self.assertFalse(output.exists())

    def test_selected_vault_and_source_pointer_bytes_cannot_mix_generations(self) -> None:
        selected = load_vault(self.vault, expected_id="portable")
        vault_manifest_path = self.vault / ".kgdistiller/vault.json"
        original_vault_manifest = vault_manifest_path.read_bytes()
        replacement = json.loads(original_vault_manifest.decode("utf-8"))
        replacement["label"] = "Changed after selection"
        vault_manifest_path.write_bytes(
            (json.dumps(replacement, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
        )
        try:
            with self.assertRaisesRegex(
                vault_store_module.VaultStoreError, "changed after the Vault generation"
            ):
                vault_store_module._vault_manifest_record(selected)
        finally:
            vault_manifest_path.write_bytes(original_vault_manifest)

        source = self.vault / "Sources/Pointer.md"
        source.parent.mkdir()
        source.write_text("Pointer generation.\n", encoding="utf-8")
        capture_source(source, home=self.home)
        selected = load_vault(self.vault, expected_id="portable")
        ledger = load_source_ledger(selected)
        source_manifest_path = self.vault / ".kgdistiller/sources/manifest.json"
        original_source_manifest = source_manifest_path.read_bytes()
        changed_source_manifest = copy.deepcopy(ledger.manifest)
        changed_source_manifest["generation_sha256"] = "0" * 64
        source_manifest_path.write_bytes(
            canonical_json(changed_source_manifest).encode("utf-8")
        )
        try:
            with self.assertRaisesRegex(
                vault_store_module.VaultStoreError, "source manifest changed"
            ):
                vault_store_module._source_inventory(selected, ledger)
        finally:
            source_manifest_path.write_bytes(original_source_manifest)

    def test_contract_rejects_self_consistent_forged_scaffold_content(self) -> None:
        manifest = copy.deepcopy(
            vault_store_module._capture_store(
                load_vault(self.vault), layout="snapshot-copy"
            ).manifest
        )
        scaffold = next(
            item
            for item in manifest["scaffolds"]
            if item["path"] == ".kgdistiller/.gitattributes"
        )
        forged = b"forged\n"
        scaffold["bytes"] = len(forged)
        scaffold["sha256"] = vault_store_module._sha256(forged)
        manifest["content_generation_sha256"] = contracts_module.sha256_json(
            {
                "vault_manifest_sha256": manifest["vault"]["manifest_sha256"],
                "authority_generation_sha256": manifest["authority"][
                    "generation_sha256"
                ],
                "source_inventory_sha256": manifest["source"]["inventory_sha256"],
                "graph_inventory_sha256": manifest["graph"]["inventory_sha256"],
                "receipt_inventory_sha256": manifest["receipts"][
                    "inventory_sha256"
                ],
                "scaffold_inventory_sha256": contracts_module.sha256_json(
                    manifest["scaffolds"]
                ),
            }
        )
        manifest = contracts_module.finalize_self_digest(manifest, "store_sha256")
        with self.assertRaisesRegex(ContractError, "scaffold content"):
            validate_contract(manifest)

    def test_prepublish_failure_rolls_back_only_owned_exact_scaffolds(self) -> None:
        def fail_after_scaffolds(label: str, path: str) -> None:
            if label == "after-scaffolds":
                raise OSError("injected")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=fail_after_scaffolds
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home)
        for relative in (
            ".kgdistiller/.gitattributes",
            ".kgdistiller/build/.gitignore",
            ".kgdistiller/sources/.gitkeep",
            "Knowledge/Topics/.gitkeep",
            ".kgdistiller/store.json",
        ):
            self.assertFalse(self.vault.joinpath(*Path(relative).parts).exists())
        self.assertFalse(
            any(
                path.name.startswith(".store-")
                for path in (self.vault / ".kgdistiller/build").iterdir()
            )
        )

    def test_postreplace_failure_classifies_new_and_preserves_scaffolds(self) -> None:
        fired = False

        def fail_after_replace(label: str, path: str) -> None:
            nonlocal fired
            if label == "after-store-replace" and not fired:
                fired = True
                raise OSError("injected")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=fail_after_replace
        ):
            report = snapshot_vault_store("portable", home=self.home)
        self.assertEqual("verified", report["status"])
        self.assertTrue((self.vault / ".kgdistiller/.gitattributes").is_file())
        self.assertEqual(report["store_sha256"], verify_vault_store(self.vault)["store_sha256"])

    def test_store_third_state_preserves_evidence_and_scaffolds(self) -> None:
        def write_third(label: str, path: str) -> None:
            if label == "before-store-replace":
                (self.vault / ".kgdistiller/store.json").write_bytes(b"third-party")
                raise OSError("injected")

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=write_third
        ):
            with self.assertRaisesRegex(
                vault_store_module.VaultStoreError, "unrecognized publication state"
            ):
                snapshot_vault_store("portable", home=self.home)
        self.assertEqual(
            b"third-party", (self.vault / ".kgdistiller/store.json").read_bytes()
        )
        self.assertTrue((self.vault / ".kgdistiller/.gitattributes").is_file())
        self.assertTrue(
            any(
                path.name.startswith(".store-")
                for path in (self.vault / ".kgdistiller/build").iterdir()
            )
        )

    def test_changed_owned_scaffold_is_preserved_on_rollback(self) -> None:
        def mutate_after_scaffolds(label: str, path: str) -> None:
            if label == "after-scaffolds":
                (self.vault / ".kgdistiller/.gitattributes").write_bytes(b"third-party\n")

        with mock.patch.object(
            vault_store_module,
            "_vault_store_hook",
            side_effect=mutate_after_scaffolds,
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                snapshot_vault_store("portable", home=self.home)
        self.assertEqual(
            b"third-party\n",
            (self.vault / ".kgdistiller/.gitattributes").read_bytes(),
        )
        self.assertFalse((self.vault / ".kgdistiller/store.json").exists())

    def test_verify_rechecks_pending_and_empty_directory_inventory(self) -> None:
        snapshot_vault_store("portable", home=self.home)

        def add_pending(label: str, path: str) -> None:
            if label == "between-verify-passes":
                (self.vault / ".kgdistiller/build/graph-transaction.json").write_bytes(
                    b"{}"
                )

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=add_pending
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                verify_vault_store(self.vault)
        pending = self.vault / ".kgdistiller/build/graph-transaction.json"
        self.assertEqual(b"{}", pending.read_bytes())
        pending.unlink()

        def add_empty_directory(label: str, path: str) -> None:
            if label == "between-verify-passes":
                (self.vault / ".kgdistiller/graph/untracked-empty").mkdir()

        with mock.patch.object(
            vault_store_module, "_vault_store_hook", side_effect=add_empty_directory
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                verify_vault_store(self.vault)

    def test_authority_inventory_is_portable_across_newline_checkout_policy(self) -> None:
        for path in sorted((self.vault / "Knowledge").rglob("*.md")):
            path.write_bytes(
                path.read_bytes()
                .decode("utf-8", errors="strict")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .encode("utf-8")
            )
        report = snapshot_vault_store("portable", home=self.home)
        source_manifest = validate_contract(
            __import__("json").loads(
                (self.vault / ".kgdistiller/store.json").read_text(encoding="utf-8")
            )
        )
        declared_bytes = len(vault_store_module._store_bytes(source_manifest)) + sum(
            vault_store_module._record_size(record)
            for record in vault_store_module._manifest_records(source_manifest)
        )
        with mock.patch.object(
            vault_store_module, "MAX_VAULT_STORE_BYTES", declared_bytes
        ):
            vault_store_module._capture_store(
                vault_store_module.load_vault(self.vault), layout="in-place"
            )

        clone = self.root / "Clone"
        shutil.copytree(self.vault, clone)
        for path in sorted((clone / "Knowledge").rglob("*.md")):
            normalized = (
                path.read_bytes()
                .decode("utf-8", errors="strict")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
            path.write_bytes(normalized.replace("\n", "\r\n").encode("utf-8"))

        with mock.patch.object(
            vault_store_module, "MAX_VAULT_STORE_BYTES", declared_bytes
        ):
            with self.assertRaises(vault_store_module.VaultStoreError):
                vault_store_module._capture_store(
                    vault_store_module.load_vault(clone), layout="in-place"
                )

        verified = verify_vault_store(clone)
        self.assertEqual(report["content_generation_sha256"], verified["content_generation_sha256"])
        self.assertEqual(report["store_sha256"], verified["store_sha256"])

        concept = clone / "Knowledge/Concepts/Alpha.md"
        concept.write_bytes(concept.read_bytes().replace(b"Portable", b"Changed", 1))
        with self.assertRaises(vault_store_module.VaultStoreError):
            verify_vault_store(clone)


if __name__ == "__main__":
    unittest.main()
