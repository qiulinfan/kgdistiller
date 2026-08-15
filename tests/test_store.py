from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import synchronize  # noqa: E402
from kgdistiller.contracts import canonical_json, sha256_json  # noqa: E402
from kgdistiller.project import initialize_project  # noqa: E402
from kgdistiller.store import StoreError, snapshot_store, verify_store  # noqa: E402


class JsonStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-store-v2-")
        root = Path(self.temporary.name)
        self.source = root / "source"
        self.output = root / "portable"
        self.registry = self.source / "knowledge/sources.json"
        self.graph = self.source / "knowledge/graph"
        self.identities = self.source / "knowledge/identities.json"
        self.alignments = self.source / "knowledge/alignments.json"
        self.typst_registry = self.source / "knowledge/build/knowledge-registry.typ"
        initialize_project(
            self.source,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )
        self.identities.write_text(
            json.dumps(
                {"schema": "qlkg-identities-v2", "identities": []},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.source / "notes/concepts.md").write_text(
            "> **Definition: --[[Sigma algebra]]--**\n>\n"
            "> A collection closed under complement and countable union.\n\n"
            "A measure is defined on [[Sigma algebra]].\n",
            encoding="utf-8",
        )
        synchronize(
            self.source,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def snapshot(self, output: Path | None = None) -> dict[str, object]:
        return snapshot_store(
            self.source,
            output or self.output,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
        )

    def test_snapshot_copy_is_self_contained_and_query_ready(self) -> None:
        created = self.snapshot()
        verified = verify_store(self.output)

        self.assertEqual("qlkg-store-report-v1", created["schema"])
        self.assertEqual("qlkg-store-v2", created["artifact_schema"])
        self.assertEqual("snapshot-copy", created["layout"])
        self.assertEqual("json-memory", verified["query_backend"])
        self.assertEqual(created["store_generation_sha256"], verified["store_generation_sha256"])
        self.assertTrue((self.output / "notes/concepts.md").is_file())
        self.assertTrue((self.output / "knowledge/graph/manifest.json").is_file())
        self.assertTrue((self.output / "knowledge/documents.jsonl").is_file())
        self.assertFalse(any(self.output.rglob("*.sqlite")))
        self.assertFalse((self.output / "knowledge/embeddings").exists())
        store_manifest = json.loads(
            (self.output / "knowledge/store.json").read_text(encoding="utf-8")
        )
        graph_manifest = json.loads(
            (self.output / "knowledge/graph/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(store_manifest["registry_sha256"], graph_manifest["registry_sha256"])
        self.assertEqual(store_manifest["identity_sha256"], graph_manifest["identity_sha256"])

    def test_cloned_snapshot_remains_self_contained_and_verifiable(self) -> None:
        self.snapshot()
        clone = Path(self.temporary.name) / "portable-clone"
        shutil.copytree(self.output, clone)

        verified = verify_store(clone)

        self.assertEqual("verified", verified["status"])
        self.assertEqual("snapshot-copy", verified["layout"])

    def test_in_place_snapshot_is_idempotent(self) -> None:
        first = self.snapshot(self.source)
        manifest_before = (self.source / "knowledge/store.json").read_bytes()
        documents_before = (self.source / "knowledge/documents.jsonl").read_bytes()
        second = self.snapshot(self.source)

        self.assertEqual("in-place", first["layout"])
        self.assertEqual(first["store_generation_sha256"], second["store_generation_sha256"])
        self.assertEqual(manifest_before, (self.source / "knowledge/store.json").read_bytes())
        self.assertEqual(documents_before, (self.source / "knowledge/documents.jsonl").read_bytes())

    def test_verify_rejects_tampering(self) -> None:
        self.snapshot()
        authority = self.output / "notes/concepts.md"
        authority.write_text(authority.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "authority digest mismatch"):
            verify_store(self.output)

    def test_verify_recomputes_document_inventory_semantics(self) -> None:
        self.snapshot()
        documents_path = self.output / "knowledge/documents.jsonl"
        documents = [
            json.loads(line)
            for line in documents_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        documents[0]["reference_count"] += 1
        documents_text = "".join(canonical_json(record) + "\n" for record in documents)
        documents_path.write_text(documents_text, encoding="utf-8")

        manifest_path = self.output / "knowledge/store.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["documents"]["sha256"] = hashlib.sha256(
            documents_text.encode("utf-8")
        ).hexdigest()
        manifest["documents"]["source_snapshot_sha256"] = sha256_json(documents)
        manifest["store_generation_sha256"] = sha256_json(
            {
                "registry_sha256": manifest["registry_sha256"],
                "identity_sha256": manifest["identity_sha256"],
                "alignment_sha256": manifest["alignment_sha256"],
                "graph_sha256": manifest["graph_sha256"],
                "source_snapshot_sha256": manifest["documents"][
                    "source_snapshot_sha256"
                ],
            }
        )
        manifest.pop("store_sha256")
        manifest["store_sha256"] = sha256_json(manifest)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StoreError, "registry and graph semantics"):
            verify_store(self.output)

    def test_verify_rejects_tampered_graph_diagnostics(self) -> None:
        self.snapshot()
        diagnostics = self.output / "knowledge/graph/diagnostics.json"
        diagnostics.write_text('{"forged": true}\n', encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "graph artifact digest mismatch"):
            verify_store(self.output)

    def test_verify_accepts_git_metadata_but_refresh_refuses_to_delete_it(self) -> None:
        self.snapshot()
        head = self.output / ".git/HEAD"
        head.parent.mkdir(parents=True)
        head.write_text("ref: refs/heads/main\n", encoding="utf-8")

        self.assertEqual("verified", verify_store(self.output)["status"])
        with self.assertRaisesRegex(StoreError, "repository metadata or unmanaged"):
            self.snapshot()
        self.assertEqual("ref: refs/heads/main\n", head.read_text(encoding="utf-8"))

    def test_verify_uses_canonical_json_hashes_across_crlf_checkouts(self) -> None:
        self.snapshot()
        manifest = json.loads(
            (self.output / "knowledge/store.json").read_text(encoding="utf-8")
        )
        portable_text_paths = [
            "knowledge/sources.json",
            "knowledge/identities.json",
            *(artifact["path"] for artifact in manifest["graph_artifacts"]),
        ]
        for relative in portable_text_paths:
            path = self.output / relative
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        self.assertEqual("verified", verify_store(self.output)["status"])

    def test_verify_rejects_broken_symlink_as_unmanaged_entry(self) -> None:
        self.snapshot()
        link = self.output / "broken-link"
        try:
            link.symlink_to("missing-target")
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(StoreError, "managed-file mismatch"):
            verify_store(self.output)

    def test_verify_rejects_symlinked_manifest_before_reading_it(self) -> None:
        self.snapshot()
        manifest = self.output / "knowledge/store.json"
        external = Path(self.temporary.name) / "external-store.json"
        manifest.replace(external)
        try:
            manifest.symlink_to(external)
        except (NotImplementedError, OSError) as error:
            external.replace(manifest)
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(StoreError, "manifest must be an ordinary file"):
            verify_store(self.output)

    def test_store_v1_is_rejected_without_deleting_old_assets(self) -> None:
        (self.output / "knowledge/embeddings").mkdir(parents=True)
        sentinel = self.output / "knowledge/embeddings/keep.f32"
        sentinel.write_bytes(b"old-vector")
        (self.output / "knowledge/store.json").write_text(
            json.dumps({"schema": "qlkg-store-v1"}), encoding="utf-8"
        )

        with self.assertRaisesRegex(StoreError, "unsupported-store-schema"):
            self.snapshot()
        self.assertEqual(b"old-vector", sentinel.read_bytes())

    def test_snapshot_copy_refuses_unverified_existing_output(self) -> None:
        self.output.mkdir(parents=True)
        (self.output / "unmanaged.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises((StoreError, FileNotFoundError, json.JSONDecodeError)):
            self.snapshot()
        self.assertEqual("keep", (self.output / "unmanaged.txt").read_text(encoding="utf-8"))

    def test_snapshot_rejects_new_registered_authority_until_sync(self) -> None:
        self.snapshot()
        before = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        (self.source / "notes/unsynced.md").write_text(
            "> **Definition: --[[Unsynced concept]]--**\n>\n> Not in the graph yet.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StoreError, "out of sync with the graph"):
            self.snapshot()

        after = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_snapshot_rejects_unsynchronized_registry_generations_without_replacing_output(self) -> None:
        self.snapshot()
        baseline = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        original_registry = self.registry.read_bytes()
        original_identities = self.identities.read_bytes()

        def assert_unchanged() -> None:
            current = {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(baseline, current)

        try:
            registry = json.loads(original_registry)
            registry["sources"][0]["subject"] = "changed-without-sync"
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(StoreError, "source registry is out of sync"):
                self.snapshot()
            assert_unchanged()
            self.registry.write_bytes(original_registry)

            identities = json.loads(original_identities)
            identities["identities"].append(
                {
                    "id": "unsynchronized-identity",
                    "canonical_name": "Unsynchronized identity",
                    "aliases": [],
                }
            )
            self.identities.write_text(json.dumps(identities), encoding="utf-8")
            with self.assertRaisesRegex(StoreError, "identity registry is out of sync"):
                self.snapshot()
            assert_unchanged()
        finally:
            self.registry.write_bytes(original_registry)
            self.identities.write_bytes(original_identities)

    def test_verify_rejects_store_registry_that_differs_from_its_graph_generation(self) -> None:
        self.snapshot()
        portable_registry = self.output / "knowledge/sources.json"
        payload = json.loads(portable_registry.read_text(encoding="utf-8"))
        payload["sources"][0]["subject"] = "tampered-generation"
        portable_registry.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "source registry is out of sync"):
            verify_store(self.output)

    def test_verify_rejects_identity_registry_that_differs_from_graph_generation(self) -> None:
        self.snapshot()
        portable_identities = self.output / "knowledge/identities.json"
        payload = json.loads(portable_identities.read_text(encoding="utf-8"))
        payload["identities"].append(
            {
                "id": "tampered-identity",
                "canonical_name": "Tampered identity",
                "aliases": [],
            }
        )
        portable_identities.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "identity registry is out of sync"):
            verify_store(self.output)

    def test_snapshot_copy_never_replaces_an_in_place_project(self) -> None:
        victim = Path(self.temporary.name) / "victim"
        victim_registry = victim / "knowledge/sources.json"
        victim_graph = victim / "knowledge/graph"
        victim_identities = victim / "knowledge/identities.json"
        victim_alignments = victim / "knowledge/alignments.json"
        victim_typst = victim / "knowledge/build/knowledge-registry.typ"
        initialize_project(
            victim,
            victim_registry,
            source_root=Path("notes"),
            alignments=victim_alignments,
        )
        (victim / "notes/private.md").write_text(
            "> **Definition: --[[Private concept]]--**\n>\n> Must survive.\n",
            encoding="utf-8",
        )
        synchronize(
            victim,
            victim_registry,
            victim_graph,
            victim_typst,
            identities=victim_identities,
            alignments=victim_alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )
        snapshot_store(
            victim,
            victim,
            registry=victim_registry,
            graph_dir=victim_graph,
            identities=victim_identities,
            alignments=victim_alignments,
        )
        sentinel = victim / "private-unmanaged.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "in-place project"):
            self.snapshot(victim)

        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertEqual("in-place", json.loads((victim / "knowledge/store.json").read_text(encoding="utf-8"))["layout"])

    def test_copy_removes_stale_managed_generation_only_after_verification(self) -> None:
        self.snapshot()
        stale = self.output / "knowledge/graph/stale.json"
        stale.write_text("{}", encoding="utf-8")
        # A generation containing unmanaged files is not silently replaced.
        with self.assertRaises(StoreError):
            self.snapshot()
        stale.unlink()
        (self.source / "notes/second.md").write_text(
            "> **Definition: --[[Outer measure]]--**\n>\n> A covering construction.\n",
            encoding="utf-8",
        )
        synchronize(
            self.source,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )
        refreshed = self.snapshot()
        self.assertEqual(2, refreshed["documents"])


if __name__ == "__main__":
    unittest.main()
