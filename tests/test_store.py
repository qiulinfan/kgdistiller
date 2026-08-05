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


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import index_embeddings, index_status  # noqa: E402
from kgdistiller.cli import synchronize  # noqa: E402
from kgdistiller.project import initialize_project  # noqa: E402
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

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index + 1), float(len(text) + 1), 0.25, -0.5]
            for index, text in enumerate(texts)
        ]


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
        connection = sqlite3.connect(database)
        try:
            return connection.execute(
                """
                SELECT namespace, node_id, provider, model, dimensions,
                       content_sha256, vector
                FROM embeddings
                ORDER BY namespace, node_id, provider, model
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
        self.assertEqual(
            created["store_generation_sha256"],
            index_status(restored_database)["store_generation_sha256"],
        )

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
