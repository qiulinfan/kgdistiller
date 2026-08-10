from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import (  # noqa: E402
    agent_index_exists,
    index_embeddings,
    index_status,
    resolve_agent_index_path,
)
from kgdistiller.cli import make_artifacts, synchronize, write_artifacts  # noqa: E402
from kgdistiller.project import initialize_project  # noqa: E402
import kgdistiller.store as store_module  # noqa: E402
from kgdistiller.store import (  # noqa: E402
    StoreError,
    materialize_store,
    snapshot_store,
    verify_store,
)


class FixtureEmbeddingProvider:
    name = "fixture"
    model = "portable-v1"
    dimensions = 4
    provider_config_sha256 = "a" * 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index + 1), float(len(text) + 1), 0.25, -0.5]
            for index, text in enumerate(texts)
        ]


class ReplacementEmbeddingProvider(FixtureEmbeddingProvider):
    name = "replacement-fixture"
    model = "replacement-v1"


class PortableStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-store-test-")
        temporary = Path(self.temporary.name)
        self.source = temporary / "source"
        self.store = temporary / "portable"
        self.registry = self.source / "knowledge/sources.json"
        self.graph = self.source / "knowledge/graph"
        self.identities = self.source / "knowledge/identities.json"
        self.alignments = self.source / "knowledge/alignments.json"
        self.database = self.source / "knowledge/build/knowledge.sqlite"
        self.typst_registry = self.source / "knowledge/build/knowledge-registry.typ"
        initialize_project(
            self.source,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )
        shutil.copyfile(
            REPO_ROOT / "tests/fixtures/roundtrip.typ",
            self.source / "notes/roundtrip.typ",
        )
        synchronize(
            self.source,
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
        index_embeddings(
            self.database,
            FixtureEmbeddingProvider(),
            build_similarity_edges=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def embedding_rows(database: Path) -> list[tuple[object, ...]]:
        connection = sqlite3.connect(resolve_agent_index_path(database))
        try:
            return connection.execute(
                """
                SELECT namespace, node_id, provider, model, dimensions,
                       embedding_input_schema, content_sha256,
                       provider_config_sha256, vector
                FROM embeddings
                ORDER BY namespace, node_id, provider, model,
                         provider_config_sha256
                """
            ).fetchall()
        finally:
            connection.close()

    def test_snapshot_copy_round_trips_exact_vectors_without_provider(self) -> None:
        expected_embeddings = self.embedding_rows(self.database)

        created = snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )
        verified = verify_store(self.store)

        self.assertEqual("snapshot-copy", created["mode"])
        self.assertEqual(created["store_generation_sha256"], verified["store_generation_sha256"])
        self.assertEqual(len(expected_embeddings), verified["embeddings"])
        portable_manifest = json.loads(
            (self.store / "knowledge/embeddings/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        portable_records = self.embedding_records()
        self.assertEqual("qlkg-embedding-bundle-v2", portable_manifest["schema"])
        self.assertTrue(portable_records)
        self.assertEqual(
            {"qlkg-embedding-record-v2"},
            {record["schema"] for record in portable_records},
        )
        self.assertEqual(
            {"a" * 64},
            {record["provider_config_sha256"] for record in portable_records},
        )
        self.assertTrue((self.store / "notes/roundtrip.typ").is_file())
        self.assertTrue((self.store / "knowledge/graph/manifest.json").is_file())
        self.assertEqual(
            "build/\n",
            (self.store / "knowledge/.gitignore").read_text(encoding="utf-8"),
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.store),
                "store",
                "verify",
            ],
            cwd=self.store,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            verified["store_generation_sha256"],
            json.loads(completed.stdout)["store_generation_sha256"],
        )

        restored_database = self.store / "knowledge/build/knowledge.sqlite"
        restored_database.parent.mkdir(parents=True)
        shutil.copyfile(self.database, restored_database)
        first = materialize_store(self.store, restored_database)
        second = materialize_store(self.store, restored_database)

        self.assertTrue(first["materialized"])
        self.assertFalse(second["materialized"])
        self.assertEqual(expected_embeddings, self.embedding_rows(restored_database))
        restored_status = index_status(restored_database)
        self.assertEqual(
            created["store_generation_sha256"],
            restored_status["store_generation_sha256"],
        )
        self.assertEqual(
            "a" * 64,
            restored_status["providers"]["embedding"]["provider_config_sha256"],
        )
        self.assertEqual(
            "qlkg-node-embedding-text-v1",
            restored_status["providers"]["embedding"]["embedding_input_schema"],
        )

    def test_v1_bundle_remains_verifiable_and_materializes_legacy_digest(self) -> None:
        self.snapshot()
        records = self.convert_embedding_bundle_to_v1()
        legacy_digests = {
            str(record["provider_config_sha256"]) for record in records
        }

        verified = verify_store(self.store)
        target = self.store / "knowledge/build/v1.sqlite"
        result = materialize_store(self.store, target)

        self.assertEqual(len(records), verified["embeddings"])
        self.assertEqual(len(records), result["embeddings"])
        self.assertEqual(
            legacy_digests,
            {str(row[7]) for row in self.embedding_rows(target)},
        )

    def test_v1_bundle_rejects_nonlegacy_provider_digest(self) -> None:
        self.snapshot()
        records = self.convert_embedding_bundle_to_v1()
        records[0]["provider_config_sha256"] = "f" * 64
        self.rewrite_embedding_bundle(
            records, bundle_schema=store_module.LEGACY_EMBEDDING_BUNDLE_SCHEMA
        )

        with self.assertRaisesRegex(StoreError, "provider digest mismatch"):
            verify_store(self.store)

    def test_snapshot_rejects_invalid_current_provider_config_digest(self) -> None:
        with store_module._mutable_agent_index(self.database) as connection:
            connection.execute(
                "UPDATE embeddings SET provider_config_sha256 = 'not-a-digest'"
            )

        with self.assertRaisesRegex(StoreError, "invalid embedding provider digest"):
            self.snapshot()

    def test_snapshot_omits_ineligible_stale_embedding_rows(self) -> None:
        before = self.embedding_rows(self.database)
        state = store_module.load_state(self.graph)
        changed = next(
            node for node in state.nodes.values() if node.get("type") == "knowledge"
        )
        changed["text"] = str(changed.get("text", "")) + " changed"
        changed["properties"]["curation_status"] = "needs-review"
        changed["provenance"]["active"] = False
        artifacts = make_artifacts(
            state,
            dict(state.manifest.get("source_hashes") or {}),
            identity_sha256=str(state.manifest.get("identity_sha256", "")) or None,
            git_revision=str(state.manifest.get("git_revision", "")) or None,
        )
        write_artifacts(self.graph, artifacts)
        snapshot = store_module.make_agent_snapshot(store_module.load_state(self.graph))
        store_module.write_agent_index(self.database, snapshot)

        retained = self.embedding_rows(self.database)
        self.assertEqual(len(before), len(retained))
        created = self.snapshot()
        verified = verify_store(self.store)
        portable = self.embedding_records()
        self.assertGreater(len(retained), len(portable))
        self.assertEqual(len(portable), created["embeddings"])
        self.assertEqual(len(portable), verified["embeddings"])
        target = self.store / "knowledge/build/stale-filtered.sqlite"
        materialized = materialize_store(self.store, target)
        self.assertEqual(len(portable), materialized["embeddings"])

    def test_v2_rejects_duplicate_key_across_provider_configurations(self) -> None:
        self.snapshot()
        records = self.embedding_records()
        second_configuration = dict(records[0])
        second_configuration["provider_config_sha256"] = "b" * 64
        records.append(second_configuration)
        self.rewrite_embedding_bundle(
            records, bundle_schema=store_module.EMBEDDING_BUNDLE_SCHEMA
        )

        with self.assertRaisesRegex(StoreError, "duplicate or incomplete"):
            verify_store(self.store)

    def test_verify_rejects_tampered_embedding_object(self) -> None:
        snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )
        object_path = next((self.store / "knowledge/embeddings/objects").rglob("*.f32"))
        payload = bytearray(object_path.read_bytes())
        payload[0] ^= 0x01
        object_path.write_bytes(payload)

        with self.assertRaisesRegex(StoreError, "object digest mismatch"):
            verify_store(self.store)

    def test_snapshot_reads_current_generation_while_old_reader_stays_open(self) -> None:
        old_reader = sqlite3.connect(self.database)
        try:
            old_count = int(
                old_reader.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            )
            source = self.source / "notes/roundtrip.typ"
            source.write_text(
                source.read_text(encoding="utf-8")
                + "\n#definition(\n"
                + "  title: [#kn[reader generation]],\n"
                + ")[\n"
                + "  A node published while an earlier index reader is open.\n"
                + "]\n",
                encoding="utf-8",
            )
            synchronize(
                self.source,
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

            current_path = resolve_agent_index_path(self.database)
            if os.name == "nt":
                self.assertNotEqual(self.database, current_path)
                self.assertEqual(
                    f".{self.database.name}.generations", current_path.parent.name
                )
            current_reader = sqlite3.connect(current_path)
            try:
                self.assertEqual(
                    old_count + 1,
                    current_reader.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                )
            finally:
                current_reader.close()
            self.assertEqual(
                old_count,
                old_reader.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            )

            created = snapshot_store(
                self.source,
                self.store,
                registry=self.registry,
                graph_dir=self.graph,
                identities=self.identities,
                alignments=self.alignments,
                database=self.database,
            )
            self.assertEqual(old_count + 1, created["counts"]["nodes"])
            self.assertFalse(
                any(
                    ".generations" in path.as_posix()
                    for path in self.store.rglob("*")
                )
            )
        finally:
            old_reader.close()

    def test_embedding_update_does_not_mutate_published_generation(self) -> None:
        old_reader = sqlite3.connect(self.database)
        try:
            source = self.source / "notes/roundtrip.typ"
            source.write_text(
                source.read_text(encoding="utf-8")
                + "\n#definition(\n"
                + "  title: [#kn[immutable generation]],\n"
                + ")[\n"
                + "  Published index generations are immutable.\n"
                + "]\n",
                encoding="utf-8",
            )
            synchronize(
                self.source,
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
        finally:
            old_reader.close()

        published = resolve_agent_index_path(self.database)
        before = published.read_bytes()
        index_embeddings(
            self.database,
            ReplacementEmbeddingProvider(),
            build_similarity_edges=False,
        )

        self.assertEqual(before, published.read_bytes())
        self.assertNotEqual(published, resolve_agent_index_path(self.database))

    def test_materialize_failure_before_embeddings_keeps_old_generation(self) -> None:
        snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )
        target = self.store / "knowledge/build/failure.sqlite"

        with patch(
            "kgdistiller.store._import_embeddings",
            side_effect=RuntimeError("injected embedding import failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected embedding import failure"):
                materialize_store(self.store, target)

        self.assertFalse(agent_index_exists(target))

    def test_materialize_exposes_only_complete_old_or_new_generation(self) -> None:
        created = snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )
        target = self.store / "knowledge/build/observed.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolve_agent_index_path(self.database), target)
        old_bytes = target.read_bytes()
        real_import = store_module._import_embeddings
        observations: list[tuple[Path, bytes, object]] = []

        def observing_import(database: Path, *args: object, **kwargs: object) -> None:
            observations.append(
                (
                    resolve_agent_index_path(target),
                    target.read_bytes(),
                    index_status(target).get("store_generation_sha256"),
                )
            )
            real_import(database, *args, **kwargs)
            observations.append(
                (
                    resolve_agent_index_path(target),
                    target.read_bytes(),
                    index_status(target).get("store_generation_sha256"),
                )
            )

        with patch("kgdistiller.store._import_embeddings", side_effect=observing_import):
            result = materialize_store(self.store, target)

        self.assertEqual(
            [(target, old_bytes, None), (target, old_bytes, None)], observations
        )
        self.assertEqual(old_bytes, target.read_bytes())
        self.assertNotEqual(target, resolve_agent_index_path(target))
        self.assertEqual(
            created["store_generation_sha256"],
            index_status(target)["store_generation_sha256"],
        )
        connection = sqlite3.connect(resolve_agent_index_path(target))
        try:
            self.assertEqual(
                result["embeddings"],
                connection.execute("SELECT count(*) FROM embeddings").fetchone()[0],
            )
        finally:
            connection.close()

    def snapshot(self) -> dict[str, object]:
        return snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )

    def embedding_records(self) -> list[dict[str, object]]:
        records_path = self.store / "knowledge/embeddings/records.jsonl"
        return [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def rewrite_embedding_bundle(
        self,
        records: list[dict[str, object]],
        *,
        bundle_schema: str,
    ) -> None:
        records_path = self.store / "knowledge/embeddings/records.jsonl"
        records_text = store_module._jsonl(records)
        records_path.write_text(records_text, encoding="utf-8")
        manifest_path = self.store / "knowledge/embeddings/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema"] = bundle_schema
        manifest["records"]["count"] = len(records)
        manifest["records"]["sha256"] = store_module._sha256_text(records_text)
        manifest["providers"] = [
            json.loads(value)
            for value in sorted(
                {
                    store_module._canonical_json(
                        store_module._provider_config(record)
                        | {
                            "provider_config_sha256": record[
                                "provider_config_sha256"
                            ]
                        }
                    )
                    for record in records
                }
            )
        ]
        manifest.pop("embedding_generation_sha256", None)
        manifest["embedding_generation_sha256"] = store_module.sha256_json(manifest)
        manifest_path.write_text(
            store_module._pretty_json(manifest), encoding="utf-8"
        )

        store_path = self.store / "knowledge/store.json"
        store = json.loads(store_path.read_text(encoding="utf-8"))
        store["embedding_manifest_sha256"] = store_module.sha256_file(manifest_path)
        store["embedding_generation_sha256"] = manifest[
            "embedding_generation_sha256"
        ]
        store["store_generation_sha256"] = store_module.sha256_json(
            {
                "knowledge_generation_sha256": store[
                    "knowledge_generation_sha256"
                ],
                "embedding_generation_sha256": store[
                    "embedding_generation_sha256"
                ],
            }
        )
        store.pop("store_sha256", None)
        store["store_sha256"] = store_module.sha256_json(store)
        store_path.write_text(store_module._pretty_json(store), encoding="utf-8")

    def convert_embedding_bundle_to_v1(self) -> list[dict[str, object]]:
        records = self.embedding_records()
        for record in records:
            record["schema"] = store_module.LEGACY_EMBEDDING_RECORD_SCHEMA
            record["provider_config_sha256"] = store_module.sha256_json(
                store_module._provider_config(record)
            )
        self.rewrite_embedding_bundle(
            records, bundle_schema=store_module.LEGACY_EMBEDDING_BUNDLE_SCHEMA
        )
        return records

    def test_snapshot_copy_removes_only_unchanged_stale_authority(self) -> None:
        snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )
        (self.source / "notes/roundtrip.typ").unlink()
        synchronize(
            self.source,
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

        refreshed = snapshot_store(
            self.source,
            self.store,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
            database=self.database,
        )

        self.assertEqual(0, refreshed["documents"])
        self.assertFalse((self.store / "notes/roundtrip.typ").exists())
        self.assertEqual(refreshed["store_generation_sha256"], verify_store(self.store)["store_generation_sha256"])


if __name__ == "__main__":
    unittest.main()
