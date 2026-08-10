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
from kgdistiller.cli import synchronize  # noqa: E402
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
