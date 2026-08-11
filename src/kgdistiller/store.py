"""Portable, Git-friendly knowledge store snapshots and materialization."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterator, Mapping

from .agent import (
    EMBEDDING_INPUT_SCHEMA,
    INDEX_SCHEMA,
    _embedding_text,
    _filesystem_path,
    _mutable_agent_index,
    agent_index_exists,
    backup_agent_index,
    embedding_input_sha256,
    index_generation_token,
    index_status,
    open_agent_index,
    publish_agent_index_file,
    resolve_agent_index_path,
    sha256_json,
    write_agent_index,
)
from .alignment import load_alignment_set, sha256_json as alignment_sha256_json
from .cli import (
    GraphState,
    identity_registry_sha256,
    load_sources,
    load_state,
    make_agent_snapshot,
    relative_path,
    sha256_file,
    unique_source_for_path,
)
from .contracts import ContractError, parse_contract_json, validate_contract
from .embedding import EmbeddingError, load_embedding_policy
from .json_schema import validate_json_schema
from .project import ensure_knowledge_gitignore
from .providers import ProviderError, provider_config_sha256


LEGACY_STORE_SCHEMA = "qlkg-store-v1"
STORE_SCHEMA = "qlkg-store-v2"
STORE_RECEIPT_SCHEMA = "qlkg-store-operation-receipt-v1"
LEGACY_DOCUMENT_RECORD_SCHEMA = "qlkg-document-record-v1"
DOCUMENT_RECORD_SCHEMA = "qlkg-document-record-v2"
LEGACY_EMBEDDING_BUNDLE_SCHEMA = "qlkg-embedding-bundle-v1"
LEGACY_EMBEDDING_RECORD_SCHEMA = "qlkg-embedding-record-v1"
EMBEDDING_BUNDLE_SCHEMA = "qlkg-embedding-bundle-v2"
EMBEDDING_RECORD_SCHEMA = "qlkg-embedding-record-v2"
STORE_MANIFEST_PATH = Path("knowledge/store.json")
DOCUMENTS_PATH = Path("knowledge/documents.jsonl")
EMBEDDING_MANIFEST_PATH = Path("knowledge/embeddings/manifest.json")
EMBEDDING_RECORDS_NAME = "records.jsonl"
EMBEDDING_OBJECTS_NAME = "objects"
DEFAULT_EMBEDDING_POLICY_PATH = Path("knowledge/embedding-policy.json")

MAX_JSON_BYTES = 1024 * 1024
MAX_STORE_MANIFEST_BYTES = 128 * 1024 * 1024
MAX_GRAPH_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_RECORDS = 100_000
MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
MAX_AUTHORITY_BYTES = 256 * 1024 * 1024
MAX_AUTHORITY_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_GRAPH_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_GRAPH_DIAGNOSTICS_BYTES = 16 * 1024 * 1024
MAX_GRAPH_ENTRY_SHARD_BYTES = 48 * 1024 * 1024
MAX_GRAPH_TOTAL_BYTES = 512 * 1024 * 1024
MAX_GRAPH_SHARDS = 10_000
MAX_EMBEDDING_RECORDS = 200_000
MAX_EMBEDDING_RECORD_BYTES = 128 * 1024 * 1024
MAX_EMBEDDING_VECTOR_BYTES = 128 * 1024 * 1024
MAX_EMBEDDING_DIMENSIONS = 1_048_576
MAX_PROVIDER_CONFIGURATIONS = 1024
MAX_MANAGED_PATHS = 400_000
MAX_JOURNAL_BYTES = 128 * 1024 * 1024
STORE_LOCK_TIMEOUT_SECONDS = 60.0
STORE_LOCK_POLL_SECONDS = 0.05


class StoreError(ValueError):
    """Raised when a portable store violates its deterministic contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "store-command-failed",
        receipt: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.receipt = dict(receipt) if receipt is not None else None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "kgdistiller-store-error",
            "code": self.code,
            "message": str(self),
        }
        if self.receipt is not None:
            payload["receipt"] = self.receipt
        return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _pretty_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def _jsonl(values: list[dict[str, Any]]) -> str:
    return "".join(_canonical_json(value) + "\n" for value in values)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _bounded_bytes(path: Path, limit: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(_filesystem_path(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StoreError(f"{label} is not a regular file")
        if metadata.st_size < 0 or metadata.st_size > limit:
            raise StoreError(f"{label} exceeds the byte budget")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            payload = handle.read(limit + 1)
    except OSError as error:
        raise StoreError(f"cannot read {label}: {path}") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(payload) > limit:
        raise StoreError(f"{label} exceeds the byte budget")
    return payload


def _bounded_regular_file_size(path: Path, limit: int, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(_filesystem_path(path), flags)
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise StoreError(f"cannot inspect {label}: {path}") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not stat.S_ISREG(metadata.st_mode):
        raise StoreError(f"{label} is not a regular file")
    if metadata.st_size < 0 or metadata.st_size > limit:
        raise StoreError(f"{label} exceeds the byte budget")
    return int(metadata.st_size)


def _journal_path(root: Path) -> Path:
    digest = hashlib.sha256(os.fspath(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return root.parent / f".{root.name}.kgdistiller-store-{digest}.journal.json"


def _lock_path(root: Path) -> Path:
    digest = hashlib.sha256(os.fspath(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return root.parent / f".{root.name}.kgdistiller-store-{digest}.lock"


@contextmanager
def _store_writer_lock(root: Path) -> Iterator[None]:
    """Serialize publication without changing the portable generation tree."""

    root = root.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    path = _lock_path(root)
    handle = path.open("a+b")
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + STORE_LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EDEADLK,
                    }:
                        raise
                    if time.monotonic() >= deadline:
                        raise StoreError(
                            "timed out waiting for portable store writer lock",
                            code="store-busy",
                        ) from error
                    time.sleep(STORE_LOCK_POLL_SECONDS)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    if time.monotonic() >= deadline:
                        raise StoreError(
                            "timed out waiting for portable store writer lock",
                            code="store-busy",
                        ) from error
                    time.sleep(STORE_LOCK_POLL_SECONDS)
        locked = True
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    stop = stop.resolve()
    while current != stop and current != current.parent:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _publication_file_sha256(path: Path) -> str | None:
    try:
        return sha256_file(path) if path.is_file() else None
    except OSError:
        return None


def _recover_publication(root: Path) -> None:
    """Recover a publication interrupted before its manifest commit point."""

    journal_path = _journal_path(root)
    if not journal_path.is_file():
        return
    try:
        journal = _read_json(
            journal_path,
            limit=MAX_JOURNAL_BYTES,
            label="store publication journal",
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise StoreError(
            "portable store publication journal is invalid",
            code="portable-pending",
        ) from error
    if (
        journal.get("schema") != "qlkg-store-publication-journal-v1"
        or not _is_sha256(journal.get("new_manifest_sha256"))
    ):
        raise StoreError(
            "portable store publication journal contract is invalid",
            code="portable-pending",
        )
    if str(journal.get("root", "")) != os.fspath(root.resolve()):
        raise StoreError(
            "portable store publication journal targets another root",
            code="portable-pending",
        )
    stage_value = journal.get("stage")
    stage = Path(str(stage_value)).resolve() if stage_value else None
    try:
        stage_has_expected_parent = bool(
            stage is not None and os.path.samefile(stage.parent, root.parent)
        )
    except OSError:
        stage_has_expected_parent = False
    if (
        stage is None
        or not stage_has_expected_parent
        or not stage.name.startswith(f".{root.name}.kgdistiller-store-stage-")
    ):
        raise StoreError(
            "portable store publication backup is unavailable",
            code="portable-pending",
        )
    new_manifest_sha256 = str(journal.get("new_manifest_sha256", ""))
    manifest_path = root / STORE_MANIFEST_PATH
    if _publication_file_sha256(manifest_path) == new_manifest_sha256:
        journal_path.unlink(missing_ok=True)
        shutil.rmtree(_filesystem_path(stage), ignore_errors=True)
        return

    backups = journal.get("backups")
    created = journal.get("created")
    if not isinstance(backups, list) or not isinstance(created, list):
        raise StoreError(
            "portable store publication journal is incomplete",
            code="portable-pending",
        )
    backup_set = set(backups) if all(isinstance(value, str) for value in backups) else set()
    created_set = set(created) if all(isinstance(value, str) for value in created) else set()
    if (
        len(backups) + len(created) > MAX_MANAGED_PATHS + 2
        or len(backup_set) != len(backups)
        or len(created_set) != len(created)
        or backup_set & created_set
    ):
        raise StoreError(
            "portable store publication journal inventory is invalid",
            code="portable-pending",
        )
    # Restore data first and the old top-level manifest last.
    manifest_relative = STORE_MANIFEST_PATH.as_posix()
    ordered = sorted(
        (str(value) for value in backups),
        key=lambda value: (value == manifest_relative, value),
    )
    for relative in ordered:
        source = _resolve(stage / "backup", relative)
        destination = _resolve(root, relative)
        if not source.is_file():
            raise StoreError(
                f"portable store publication backup is missing: {relative}",
                code="portable-pending",
            )
        _copy_file(source, destination)
    for relative in sorted((str(value) for value in created), reverse=True):
        destination = _resolve(root, relative)
        if destination.is_file():
            destination.unlink()
            _remove_empty_parents(destination, root)
    journal_path.unlink(missing_ok=True)
    shutil.rmtree(_filesystem_path(stage), ignore_errors=True)


def _safe_relative(value: str | Path) -> Path:
    rendered = os.fspath(value)
    path = Path(value)
    if (
        len(rendered) > 1024
        or path.is_absolute()
        or not path.parts
        or ".." in path.parts
    ):
        raise StoreError(f"unsafe store path: {rendered[:256]!r}")
    return path


def _resolve(root: Path, value: str | Path) -> Path:
    relative = _safe_relative(value)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise StoreError(f"store path escapes root: {value}") from error
    return candidate


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise StoreError(f"store artifact lies outside repository: {path}") from error


def _copy_file(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve(strict=False):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            with source.open("rb") as input_:
                shutil.copyfileobj(input_, output)
                output.flush()
                os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _graph_paths(graph_dir: Path, state: GraphState) -> list[Path]:
    paths = [
        graph_dir / "manifest.json",
        graph_dir / "nodes.jsonl",
        graph_dir / "edges.jsonl",
        graph_dir / "references.jsonl",
        graph_dir / "diagnostics.json",
    ]
    for shard in ((state.manifest.get("entry_store") or {}).get("shards") or []):
        relative = _safe_relative(str(shard.get("path", "")))
        paths.append(graph_dir / relative)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise StoreError(f"missing graph artifacts: {', '.join(missing)}")
    return paths


def _preflight_graph_artifacts(graph_dir: Path) -> None:
    """Bound every graph file before the legacy graph loader allocates it."""

    manifest_path = graph_dir / "manifest.json"
    manifest = _read_json(
        manifest_path,
        limit=MAX_GRAPH_MANIFEST_BYTES,
        label="graph manifest",
    )
    total_bytes = _bounded_regular_file_size(
        manifest_path,
        MAX_GRAPH_MANIFEST_BYTES,
        "graph manifest",
    )
    for name, limit in (
        ("nodes.jsonl", MAX_GRAPH_ARTIFACT_BYTES),
        ("edges.jsonl", MAX_GRAPH_ARTIFACT_BYTES),
        ("references.jsonl", MAX_GRAPH_ARTIFACT_BYTES),
        ("diagnostics.json", MAX_GRAPH_DIAGNOSTICS_BYTES),
    ):
        total_bytes += _bounded_regular_file_size(
            graph_dir / name,
            limit,
            f"graph {name}",
        )
    shards = (manifest.get("entry_store") or {}).get("shards") or []
    if not isinstance(shards, list) or len(shards) > MAX_GRAPH_SHARDS:
        raise StoreError("graph entry shard inventory exceeds the file budget")
    seen: set[str] = set()
    for item in shards:
        if not isinstance(item, dict):
            raise StoreError("graph entry shard inventory is invalid")
        relative = _safe_relative(str(item.get("path", ""))).as_posix()
        if relative in seen:
            raise StoreError("graph entry shard inventory contains duplicates")
        seen.add(relative)
        total_bytes += _bounded_regular_file_size(
            _resolve(graph_dir, relative),
            MAX_GRAPH_ENTRY_SHARD_BYTES,
            "graph entry shard",
        )
        if total_bytes > MAX_GRAPH_TOTAL_BYTES:
            raise StoreError("graph artifact inventory exceeds the byte budget")
    if total_bytes > MAX_GRAPH_TOTAL_BYTES:
        raise StoreError("graph artifact inventory exceeds the byte budget")


def _validate_vector(payload: bytes, dimensions: int) -> None:
    if (
        dimensions < 1
        or dimensions > MAX_EMBEDDING_DIMENSIONS
        or len(payload) != dimensions * 4
    ):
        raise StoreError("embedding vector byte length does not match dimensions")
    values = struct.unpack(f"<{dimensions}f", payload)
    if not all(math.isfinite(value) for value in values):
        raise StoreError("embedding vector contains a non-finite value")
    if not any(value != 0.0 for value in values):
        raise StoreError("embedding vector cannot be all zero")


def _provider_config(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(record["provider"]),
        "model": str(record["model"]),
        "dimensions": int(record["dimensions"]),
        "dtype": "float32-le",
        "embedding_input_schema": EMBEDDING_INPUT_SCHEMA,
    }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _export_embeddings(
    database: Path,
    output_root: Path,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    embedding_root = _resolve(output_root, EMBEDDING_MANIFEST_PATH.parent)
    records_path = embedding_root / EMBEDDING_RECORDS_NAME
    object_root = embedding_root / EMBEDDING_OBJECTS_NAME
    rows: list[sqlite3.Row] = []
    if agent_index_exists(database):
        status = index_status(database)
        if status.get("schema") != INDEX_SCHEMA:
            raise StoreError("cannot export an unsupported Agent index")
        connection = open_agent_index(database)
        try:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(embeddings)")
            }
            input_schema_column = (
                "embedding_input_schema"
                if "embedding_input_schema" in columns
                else f"'{EMBEDDING_INPUT_SCHEMA}' AS embedding_input_schema"
            )
            has_config_digest = "provider_config_sha256" in columns
            config_digest_column = (
                "provider_config_sha256"
                if has_config_digest
                else "NULL AS provider_config_sha256"
            )
            config_order = ", provider_config_sha256" if has_config_digest else ""
            record_count, vector_bytes = connection.execute(
                """
                SELECT count(*), coalesce(sum(length(vector)), 0)
                FROM embeddings
                WHERE namespace = ?
                """,
                (str(snapshot["namespace"]),),
            ).fetchone()
            if int(record_count) > MAX_EMBEDDING_RECORDS:
                raise StoreError("embedding inventory exceeds the record budget")
            if int(vector_bytes) > MAX_EMBEDDING_VECTOR_BYTES:
                raise StoreError("embedding inventory exceeds the vector byte budget")
            cursor = connection.execute(
                f"""
                SELECT namespace, node_id, provider, model, dimensions,
                       {input_schema_column}, content_sha256,
                       {config_digest_column}, vector
                FROM embeddings
                WHERE namespace = ?
                ORDER BY namespace, node_id, provider, model{config_order}
                """,
                (str(snapshot["namespace"]),),
            )
            for row in cursor:
                rows.append(row)
        finally:
            connection.close()

    records: list[dict[str, Any]] = []
    object_paths: set[str] = set()
    object_hashes: set[str] = set()
    total_object_bytes = 0
    nodes = {str(node["id"]): node for node in snapshot.get("nodes") or []}
    for row in rows:
        node_id = str(row["node_id"])
        node = nodes.get(node_id)
        if node is None:
            raise StoreError(f"embedding references unknown node: {node_id}")
        if str(row["content_sha256"]) != embedding_input_sha256(node):
            # Graph rebuilds retain superseded rows so embedding status can report
            # them as stale.  Portable snapshots contain only vectors for the
            # current canonical input; stale rows are disposable local state.
            continue
        payload = bytes(row["vector"])
        dimensions = int(row["dimensions"])
        _validate_vector(payload, dimensions)
        vector_sha256 = _sha256_bytes(payload)
        relative_object = (
            EMBEDDING_MANIFEST_PATH.parent
            / EMBEDDING_OBJECTS_NAME
            / vector_sha256[:2]
            / f"{vector_sha256}.f32"
        )
        destination = _resolve(output_root, relative_object)
        if not destination.is_file() or sha256_file(destination) != vector_sha256:
            _atomic_write_bytes(destination, payload)
        object_paths.add(relative_object.as_posix())
        if vector_sha256 not in object_hashes:
            if total_object_bytes + len(payload) > MAX_EMBEDDING_VECTOR_BYTES:
                raise StoreError("embedding inventory exceeds the vector byte budget")
            object_hashes.add(vector_sha256)
            total_object_bytes += len(payload)
        record: dict[str, Any] = {
            "schema": EMBEDDING_RECORD_SCHEMA,
            "namespace": str(row["namespace"]),
            "node_id": node_id,
            "provider": str(row["provider"]),
            "model": str(row["model"]),
            "dimensions": dimensions,
            "embedding_input_schema": str(row["embedding_input_schema"]),
            "content_sha256": str(row["content_sha256"]),
            "vector_sha256": vector_sha256,
        }
        if record["embedding_input_schema"] != EMBEDDING_INPUT_SCHEMA:
            raise StoreError(
                f"unsupported embedding input schema for node: {node_id}"
            )
        config_digest = row["provider_config_sha256"]
        if has_config_digest:
            if not _is_sha256(config_digest):
                raise StoreError(
                    f"invalid embedding provider digest for node: {node_id}"
                )
            record["provider_config_sha256"] = str(config_digest)
        else:
            record["provider_config_sha256"] = sha256_json(_provider_config(record))
        records.append(record)

    records_text = _jsonl(records)
    _atomic_write_text(records_path, records_text)
    providers = sorted(
        {
            _canonical_json(_provider_config(record) | {
                "provider_config_sha256": record["provider_config_sha256"]
            })
            for record in records
        }
    )
    payload: dict[str, Any] = {
        "schema": EMBEDDING_BUNDLE_SCHEMA,
        "embedding_input_schema": EMBEDDING_INPUT_SCHEMA,
        "dtype": "float32-le",
        "records": {
            "path": EMBEDDING_RECORDS_NAME,
            "count": len(records),
            "sha256": _sha256_text(records_text),
        },
        "objects": {
            "directory": EMBEDDING_OBJECTS_NAME,
            "count": len(object_hashes),
            "bytes": total_object_bytes,
            "sha256": sha256_json(sorted(object_hashes)),
        },
        "providers": [json.loads(value) for value in providers],
    }
    payload["embedding_generation_sha256"] = sha256_json(payload)
    _atomic_write_text(embedding_root / "manifest.json", _pretty_json(payload))
    managed = {
        EMBEDDING_MANIFEST_PATH.as_posix(),
        (EMBEDDING_MANIFEST_PATH.parent / EMBEDDING_RECORDS_NAME).as_posix(),
        *object_paths,
    }
    return payload, managed


def _read_json(
    path: Path,
    *,
    limit: int = MAX_JSON_BYTES,
    label: str = "JSON artifact",
) -> dict[str, Any]:
    raw = _bounded_bytes(path, limit, label)
    try:
        payload = parse_contract_json(raw.decode("utf-8"))
    except (ContractError, UnicodeDecodeError) as error:
        raise StoreError(f"invalid {label}: {path}") from error
    if not isinstance(payload, dict):
        raise StoreError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(
    path: Path,
    *,
    byte_limit: int = MAX_DOCUMENT_BYTES,
    record_limit: int = MAX_DOCUMENT_RECORDS,
    label: str = "JSONL artifact",
) -> list[dict[str, Any]]:
    raw = _bounded_bytes(path, byte_limit, label)
    return _parse_jsonl(raw, record_limit=record_limit, label=label, path=path)


def _parse_jsonl(
    raw: bytes,
    *,
    record_limit: int,
    label: str,
    path: Path,
) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"invalid UTF-8 in {label}: {path}") from error
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line:
            continue
        try:
            value = parse_contract_json(line)
        except ContractError as error:
            raise StoreError(f"invalid JSON record in {label}: {path}") from error
        if not isinstance(value, dict):
            raise StoreError(f"expected JSON object record: {path}")
        records.append(value)
        if len(records) > record_limit:
            raise StoreError(f"{label} exceeds the record budget")
    return records


def _validate_schema(payload: Any, filename: str, label: str) -> None:
    schema = json.loads(
        resources.files("kgdistiller")
        .joinpath("schemas", filename)
        .read_text(encoding="utf-8")
    )
    errors = validate_json_schema(payload, schema)
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.path) or label
        raise StoreError(f"{label} schema violation at {location}: {first.message}")


def _embedding_bundle(
    root: Path,
    manifest_path: Path,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    manifest = _read_json(manifest_path, label="embedding manifest")
    bundle_schema = manifest.get("schema")
    if bundle_schema == LEGACY_EMBEDDING_BUNDLE_SCHEMA:
        bundle_schema_filename = "qlkg-embedding-bundle-v1.schema.json"
        record_schema = LEGACY_EMBEDDING_RECORD_SCHEMA
        record_schema_filename = "qlkg-embedding-record-v1.schema.json"
    elif bundle_schema == EMBEDDING_BUNDLE_SCHEMA:
        bundle_schema_filename = "qlkg-embedding-bundle-v2.schema.json"
        record_schema = EMBEDDING_RECORD_SCHEMA
        record_schema_filename = "qlkg-embedding-record-v2.schema.json"
    else:
        raise StoreError("unsupported embedding bundle schema")
    _validate_schema(
        manifest,
        bundle_schema_filename,
        "embedding manifest",
    )
    claimed_generation = str(manifest.get("embedding_generation_sha256", ""))
    generation_payload = dict(manifest)
    generation_payload.pop("embedding_generation_sha256", None)
    if sha256_json(generation_payload) != claimed_generation:
        raise StoreError("embedding generation digest mismatch")
    if manifest.get("embedding_input_schema") != EMBEDDING_INPUT_SCHEMA:
        raise StoreError("unsupported embedding input schema")
    if manifest.get("dtype") != "float32-le":
        raise StoreError("unsupported embedding dtype")

    records_meta = manifest.get("records") or {}
    claimed_record_count = int(records_meta.get("count", -1))
    if claimed_record_count < 0 or claimed_record_count > MAX_EMBEDDING_RECORDS:
        raise StoreError("embedding record count exceeds the budget")
    records_relative = _safe_relative(str(records_meta.get("path", "")))
    records_path = _resolve(manifest_path.parent, records_relative)
    records_raw = _bounded_bytes(
        records_path,
        MAX_EMBEDDING_RECORD_BYTES,
        "embedding records",
    )
    try:
        records_text = records_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError("embedding records are not valid UTF-8") from error
    if _sha256_text(records_text) != str(records_meta.get("sha256", "")):
        raise StoreError("embedding record digest mismatch")
    records = _parse_jsonl(
        records_raw,
        record_limit=MAX_EMBEDDING_RECORDS,
        label="embedding records",
        path=records_path,
    )
    if len(records) != claimed_record_count:
        raise StoreError("embedding record count mismatch")

    object_directory = _safe_relative(
        str((manifest.get("objects") or {}).get("directory", ""))
    )
    object_root = _resolve(manifest_path.parent, object_directory)
    nodes = {str(node["id"]): node for node in snapshot.get("nodes") or []}
    seen: set[tuple[str, ...]] = set()
    object_hashes: set[str] = set()
    total_object_bytes = 0
    verified: list[tuple[dict[str, Any], bytes]] = []
    if len(manifest.get("providers") or []) > MAX_PROVIDER_CONFIGURATIONS:
        raise StoreError("embedding provider inventory exceeds the budget")
    claimed_object_bytes = int((manifest.get("objects") or {}).get("bytes", -1))
    if claimed_object_bytes < 0 or claimed_object_bytes > MAX_EMBEDDING_VECTOR_BYTES:
        raise StoreError("embedding object inventory exceeds the byte budget")
    for record in records:
        _validate_schema(
            record,
            record_schema_filename,
            "embedding record",
        )
        if record.get("schema") != record_schema:
            raise StoreError("unsupported embedding record schema")
        if record.get("embedding_input_schema") != EMBEDDING_INPUT_SCHEMA:
            raise StoreError("unsupported embedding record input schema")
        base_key = (
            str(record.get("namespace", "")),
            str(record.get("node_id", "")),
            str(record.get("provider", "")),
            str(record.get("model", "")),
        )
        config_digest = record.get("provider_config_sha256")
        if bundle_schema == LEGACY_EMBEDDING_BUNDLE_SCHEMA:
            if config_digest != sha256_json(_provider_config(record)):
                raise StoreError(
                    f"embedding provider digest mismatch for node: {base_key[1]}"
                )
            key = base_key
        else:
            if not _is_sha256(config_digest):
                raise StoreError(
                    f"embedding provider digest mismatch for node: {base_key[1]}"
                )
            key = base_key
        if not all(key) or key in seen:
            raise StoreError(f"duplicate or incomplete embedding record: {key}")
        seen.add(key)
        if key[0] != str(snapshot.get("namespace", "")):
            raise StoreError(f"embedding namespace does not match store: {key[0]}")
        node = nodes.get(key[1])
        if node is None:
            raise StoreError(f"embedding references unknown node: {key[1]}")
        if str(record.get("content_sha256", "")) != embedding_input_sha256(node):
            raise StoreError(f"stale embedding input for node: {key[1]}")
        vector_sha256 = str(record.get("vector_sha256", ""))
        if len(vector_sha256) != 64:
            raise StoreError(f"invalid vector digest for node: {key[1]}")
        object_path = _resolve(
            object_root,
            Path(vector_sha256[:2]) / f"{vector_sha256}.f32",
        )
        dimensions = int(record.get("dimensions", 0))
        if dimensions < 1 or dimensions > MAX_EMBEDDING_DIMENSIONS:
            raise StoreError(f"invalid embedding dimensions for node: {key[1]}")
        payload = _bounded_bytes(
            object_path,
            dimensions * 4,
            "embedding vector object",
        )
        if _sha256_bytes(payload) != vector_sha256:
            raise StoreError(f"embedding object digest mismatch: {vector_sha256}")
        _validate_vector(payload, dimensions)
        if vector_sha256 not in object_hashes:
            object_hashes.add(vector_sha256)
            total_object_bytes += len(payload)
            if total_object_bytes > MAX_EMBEDDING_VECTOR_BYTES:
                raise StoreError("embedding object inventory exceeds the byte budget")
        verified.append((record, payload))

    expected_providers = sorted(
        {
            _canonical_json(
                _provider_config(record)
                | {"provider_config_sha256": record["provider_config_sha256"]}
            )
            for record in records
        }
    )
    actual_providers = sorted(
        _canonical_json(provider) for provider in (manifest.get("providers") or [])
    )
    if actual_providers != expected_providers:
        raise StoreError("embedding provider inventory mismatch")

    object_meta = manifest.get("objects") or {}
    if len(object_hashes) != int(object_meta.get("count", -1)):
        raise StoreError("embedding object count mismatch")
    if total_object_bytes != claimed_object_bytes:
        raise StoreError("embedding object byte count mismatch")
    if sha256_json(sorted(object_hashes)) != str(object_meta.get("sha256", "")):
        raise StoreError("embedding object set digest mismatch")
    return manifest, verified


def _document_id(source_id: str, authority: str, source_sha256: str) -> str:
    digest = sha256_json(
        {
            "source_id": source_id,
            "initial_authority": authority,
            "initial_source_sha256": source_sha256,
        }
    )
    return f"doc:sha256:{digest}"


def _document_format_v2(path: Path) -> str:
    try:
        return {".md": "md", ".typ": "typ", ".tex": "tex"}[path.suffix.lower()]
    except KeyError as error:
        raise StoreError(f"unsupported document authority format: {path}") from error


def _validate_document_record(record: dict[str, Any], schema: str) -> None:
    if schema == LEGACY_DOCUMENT_RECORD_SCHEMA:
        _validate_schema(
            record,
            "qlkg-document-record-v1.schema.json",
            "document record",
        )
        if record.get("schema") != LEGACY_DOCUMENT_RECORD_SCHEMA:
            raise StoreError("unsupported document record schema")
        return
    if schema != DOCUMENT_RECORD_SCHEMA:
        raise StoreError("unsupported document inventory schema")
    try:
        validate_contract(record)
    except ContractError as error:
        raise StoreError(f"document record contract violation: {error}") from error


def _old_document_records(
    root: Path, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    paths = manifest.get("paths") or {}
    documents_path = _resolve(root, str(paths.get("documents", "")))
    documents_meta = manifest.get("documents") or {}
    raw = _bounded_bytes(documents_path, MAX_DOCUMENT_BYTES, "document inventory")
    if _sha256_bytes(raw) != str(documents_meta.get("sha256", "")):
        raise StoreError("existing document inventory digest mismatch")
    records = _parse_jsonl(
        raw,
        record_limit=MAX_DOCUMENT_RECORDS,
        label="document inventory",
        path=documents_path,
    )
    if len(records) != int(documents_meta.get("count", -1)):
        raise StoreError("existing document inventory count mismatch")
    schema = (
        str(documents_meta.get("record_schema", ""))
        if manifest.get("schema") == STORE_SCHEMA
        else LEGACY_DOCUMENT_RECORD_SCHEMA
    )
    by_authority: dict[str, dict[str, Any]] = {}
    document_ids: set[str] = set()
    for record in records:
        _validate_document_record(record, schema)
        authority = str(record.get("authority", ""))
        if not authority or authority in by_authority:
            raise StoreError(f"duplicate or empty authority record: {authority!r}")
        if schema == DOCUMENT_RECORD_SCHEMA:
            document_id = str(record.get("document_id", ""))
            if document_id in document_ids:
                raise StoreError(f"duplicate document identity: {document_id}")
            document_ids.add(document_id)
        by_authority[authority] = record
    return by_authority


def _eligible(node: dict[str, Any], required_types: set[str]) -> bool:
    if str(node.get("type", "")) not in required_types:
        return False
    properties = node.get("properties") or {}
    provenance = node.get("provenance") or {}
    return (
        properties.get("source_status") != "orphaned"
        and properties.get("curation_status") != "needs-review"
        and node.get("status") not in {"orphaned", "needs-review"}
        and node.get("active") is not False
        and provenance.get("active") is not False
        and bool(_embedding_text(node).strip())
    )


def _profile_bindings(
    policy: dict[str, Any],
    provider_configs: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for profile in policy.get("profiles") or []:
        name = str(profile["name"])
        config = provider_configs.get(name)
        if config is None:
            bindings[name] = {
                "configuration_status": "missing",
                "provider_config_sha256": None,
            }
            continue
        copied = dict(config) if isinstance(config, Mapping) else {}
        if (
            not copied
            or str(copied.get("adapter", "")) != str(profile["provider"])
            or str(copied.get("model", "")) != str(profile["model"])
            or isinstance(copied.get("dimensions"), bool)
            or copied.get("dimensions") != profile["dimensions"]
        ):
            bindings[name] = {
                "configuration_status": "mismatch",
                "provider_config_sha256": None,
            }
            continue
        try:
            digest = provider_config_sha256(copied)
        except ProviderError:
            bindings[name] = {
                "configuration_status": "invalid",
                "provider_config_sha256": None,
            }
            continue
        bindings[name] = {
            "configuration_status": "ready",
            "provider_config_sha256": digest,
        }
    return bindings


def _validated_provider_configs(
    value: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_PROVIDER_CONFIGURATIONS:
        raise StoreError("provider configuration map is invalid")
    configs: dict[str, Mapping[str, Any]] = {}
    for raw_name, config in value.items():
        name = str(raw_name)
        if (
            raw_name != name
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name)
            or not isinstance(config, Mapping)
        ):
            raise StoreError("provider configuration map is invalid")
        configs[name] = config
    return configs


def _coverage_counts(
    *,
    node_ids: set[str],
    ready: set[str],
    incompatible: set[str],
    minimum_coverage: float,
    configuration_status: str,
    node_type: str | None = None,
) -> dict[str, Any]:
    eligible = len(node_ids)
    ready_count = len(node_ids & ready)
    coverage = (ready_count / eligible) if eligible else None
    if eligible == 0:
        state = "not-applicable"
    elif configuration_status != "ready":
        state = "unavailable"
    elif coverage is not None and coverage >= minimum_coverage:
        state = "ready"
    else:
        state = "partial"
    payload: dict[str, Any] = {
        "eligible": eligible,
        "ready": ready_count,
        "missing": eligible - ready_count,
        "stale": 0,
        "incompatible": len(node_ids & incompatible),
        "coverage": coverage,
        "readiness": state,
    }
    if node_type is not None:
        payload["node_type"] = node_type
    return payload


def _portable_readiness(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    policy: dict[str, Any] | None,
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if policy is None:
        return {
            "namespace": str(snapshot["namespace"]),
            "snapshot_sha256": str(snapshot["snapshot_sha256"]),
            "graph_sha256": str(snapshot["graph"]["sha256"]),
            "policy_sha256": None,
            "state": "unmanaged",
            "profiles": [],
            "unmanaged": {"records": len(records)},
            "optional_similarity_state": "absent",
        }
    resolved_bindings = dict(bindings or {})
    nodes = list(snapshot.get("nodes") or [])
    consumed: set[int] = set()
    profiles: list[dict[str, Any]] = []
    for policy_profile in sorted(
        policy.get("profiles") or [], key=lambda value: str(value["name"])
    ):
        name = str(policy_profile["name"])
        binding = dict(resolved_bindings.get(name) or {})
        configuration_status = str(binding.get("configuration_status", "missing"))
        config_digest = binding.get("provider_config_sha256")
        if configuration_status == "ready" and not _is_sha256(config_digest):
            raise StoreError(f"invalid provider configuration binding: {name}")
        if configuration_status != "ready":
            config_digest = None
        required_types = {
            str(value) for value in policy_profile.get("required_node_types") or []
        }
        eligible_by_type: dict[str, set[str]] = {
            node_type: set() for node_type in sorted(required_types)
        }
        current_inputs: dict[str, str] = {}
        for node in nodes:
            if not _eligible(node, required_types):
                continue
            node_id = str(node["id"])
            node_type = str(node["type"])
            eligible_by_type[node_type].add(node_id)
            current_inputs[node_id] = embedding_input_sha256(node)
        ready: set[str] = set()
        incompatible: set[str] = set()
        for index, record in enumerate(records):
            node_id = str(record.get("node_id", ""))
            if node_id not in current_inputs:
                continue
            if (
                str(record.get("provider", "")) != str(policy_profile["provider"])
                or str(record.get("model", "")) != str(policy_profile["model"])
            ):
                continue
            consumed.add(index)
            matches = (
                configuration_status == "ready"
                and record.get("dimensions") == policy_profile["dimensions"]
                and record.get("embedding_input_schema") == EMBEDDING_INPUT_SCHEMA
                and record.get("provider_config_sha256") == config_digest
                and record.get("content_sha256") == current_inputs[node_id]
            )
            if matches:
                ready.add(node_id)
            else:
                incompatible.add(node_id)
        eligible_ids = (
            set().union(*eligible_by_type.values()) if eligible_by_type else set()
        )
        threshold = float(policy_profile["minimum_coverage"])
        summary = _coverage_counts(
            node_ids=eligible_ids,
            ready=ready,
            incompatible=incompatible,
            minimum_coverage=threshold,
            configuration_status=configuration_status,
        )
        profiles.append(
            {
                "name": name,
                "provider": str(policy_profile["provider"]),
                "model": str(policy_profile["model"]),
                "dimensions": int(policy_profile["dimensions"]),
                "required_node_types": sorted(required_types),
                "required": bool(policy_profile["required"]),
                "minimum_coverage": threshold,
                "provider_config_sha256": config_digest,
                "configuration_status": configuration_status,
                **summary,
                "node_types": [
                    _coverage_counts(
                        node_ids=node_ids,
                        ready=ready,
                        incompatible=incompatible,
                        minimum_coverage=threshold,
                        configuration_status=configuration_status,
                        node_type=node_type,
                    )
                    for node_type, node_ids in sorted(eligible_by_type.items())
                ],
            }
        )
    required = [profile for profile in profiles if profile["required"]]
    applicable = [
        profile for profile in required if profile["readiness"] != "not-applicable"
    ]
    overall = (
        "ready"
        if applicable and all(profile["readiness"] == "ready" for profile in applicable)
        else "partial"
    )
    return {
        "namespace": str(snapshot["namespace"]),
        "snapshot_sha256": str(snapshot["snapshot_sha256"]),
        "graph_sha256": str(snapshot["graph"]["sha256"]),
        "policy_sha256": sha256_json(policy),
        "state": overall,
        "profiles": profiles,
        "unmanaged": {"records": len(records) - len(consumed)},
        "optional_similarity_state": "absent",
    }


def _receipt(
    operation: str,
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
    embedding_records: list[tuple[dict[str, Any], bytes]],
    *,
    materialization_status: str = "not-checked",
    semantic_status: str = "not-checked",
    **extra: Any,
) -> dict[str, Any]:
    legacy = manifest.get("schema") == LEGACY_STORE_SCHEMA
    readiness = None if legacy else manifest.get("readiness")
    portable_status = "unmanaged" if legacy else str(readiness["state"])
    result: dict[str, Any] = {
        "schema": STORE_RECEIPT_SCHEMA,
        "operation": operation,
        "store_schema": str(manifest["schema"]),
        "integrity_status": "integrity-valid",
        "portable_status": portable_status,
        "retrieval_status": (
            "retrieval-ready" if portable_status == "ready" else "retrieval-not-ready"
        ),
        "materialization_status": materialization_status,
        "semantic_status": semantic_status,
        "working_state": "current",
        "store_generation_sha256": str(manifest["store_generation_sha256"]),
        "knowledge_generation_sha256": str(manifest["knowledge_generation_sha256"]),
        "embedding_generation_sha256": str(manifest["embedding_generation_sha256"]),
        "document_generation_sha256": (
            None
            if legacy
            else str(
                (manifest.get("documents") or {}).get(
                    "document_generation_sha256"
                )
            )
        ),
        "embedding_policy_sha256": (
            None if legacy else manifest.get("embedding_policy_sha256")
        ),
        "readiness_sha256": None if legacy else manifest.get("readiness_sha256"),
        "documents": int((manifest.get("documents") or {}).get("count", 0)),
        "embeddings": len(embedding_records),
        "counts": snapshot["graph"]["counts"],
        "optional_similarity_state": "absent",
        "coverage": readiness,
        "warnings": [],
    }
    if portable_status == "partial":
        result["warnings"].append("required-coverage-incomplete")
    elif portable_status == "unmanaged":
        result["warnings"].append("embedding-policy-unmanaged")
    if semantic_status == "semantic-search-not-ready":
        result["warnings"].append("semantic-configuration-not-ready")
    result.update(extra)
    result["receipt_sha256"] = sha256_json(result)
    try:
        return validate_contract(result)
    except ContractError as error:
        raise StoreError(f"store operation receipt contract violation: {error}") from error


def _require_ready(receipt: dict[str, Any]) -> None:
    if receipt["portable_status"] != "ready":
        raise StoreError(
            "portable store does not satisfy required retrieval coverage",
            code="coverage-blocked",
            receipt=receipt,
        )


def _store_manifest(root: Path) -> dict[str, Any]:
    path = _resolve(root, STORE_MANIFEST_PATH)
    manifest = _read_json(
        path,
        limit=MAX_STORE_MANIFEST_BYTES,
        label="store manifest",
    )
    schema = manifest.get("schema")
    if schema == LEGACY_STORE_SCHEMA:
        filename = "qlkg-store-v1.schema.json"
    elif schema == STORE_SCHEMA:
        filename = "qlkg-store-v2.schema.json"
    else:
        raise StoreError("unsupported portable store schema")
    _validate_schema(manifest, filename, "store manifest")
    managed_paths = manifest.get("managed_paths") or []
    if len(managed_paths) > MAX_MANAGED_PATHS:
        raise StoreError("portable store managed path inventory exceeds the budget")
    if len(set(managed_paths)) != len(managed_paths):
        raise StoreError("portable store managed path inventory contains duplicates")
    claimed = str(manifest.get("store_sha256", ""))
    payload = dict(manifest)
    payload.pop("store_sha256", None)
    if sha256_json(payload) != claimed:
        raise StoreError("portable store manifest digest mismatch")
    return manifest


def _validated_store(
    root: Path,
) -> dict[str, Any]:
    root = root.resolve()
    if _journal_path(root).is_file():
        raise StoreError(
            "portable store publication is pending recovery",
            code="portable-pending",
        )
    manifest = _store_manifest(root)
    legacy = manifest.get("schema") == LEGACY_STORE_SCHEMA
    paths = manifest.get("paths") or {}
    registry = _resolve(root, str(paths.get("registry", "")))
    graph_dir = _resolve(root, str(paths.get("graph", "")))
    documents_path = _resolve(root, str(paths.get("documents", "")))
    embedding_manifest_path = _resolve(
        root, str(paths.get("embedding_manifest", ""))
    )
    identities_value = paths.get("identities")
    alignments_value = paths.get("alignments")
    identities = _resolve(root, str(identities_value)) if identities_value else None
    alignments = _resolve(root, str(alignments_value)) if alignments_value else None

    _bounded_regular_file_size(registry, MAX_JSON_BYTES, "source registry")
    if identities is not None:
        _bounded_regular_file_size(
            identities,
            MAX_JSON_BYTES,
            "identity registry",
        )
    if alignments is not None:
        _bounded_regular_file_size(
            alignments,
            MAX_JSON_BYTES,
            "alignment registry",
        )
    _preflight_graph_artifacts(graph_dir)
    state = load_state(graph_dir)
    snapshot_namespace = (
        "personal"
        if legacy
        else str((manifest.get("readiness") or {}).get("namespace", ""))
    )
    snapshot = make_agent_snapshot(state, namespace=snapshot_namespace)
    if snapshot["graph"]["sha256"] != str(manifest.get("graph_sha256", "")):
        raise StoreError("portable store graph digest mismatch")
    if sha256_file(registry) != str(manifest.get("registry_sha256", "")):
        raise StoreError("portable store source registry digest mismatch")
    current_identity = identity_registry_sha256(identities)
    if current_identity != manifest.get("identity_sha256"):
        raise StoreError("portable store identity registry digest mismatch")
    alignment_set = load_alignment_set(alignments)
    current_alignment = alignment_sha256_json(alignment_set)
    if current_alignment != str(manifest.get("alignment_sha256", "")):
        raise StoreError("portable store alignment registry digest mismatch")

    documents_raw = _bounded_bytes(
        documents_path,
        MAX_DOCUMENT_BYTES,
        "document inventory",
    )
    try:
        documents_text = documents_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError("portable document inventory is not valid UTF-8") from error
    documents_meta = manifest.get("documents") or {}
    if _sha256_text(documents_text) != str(documents_meta.get("sha256", "")):
        raise StoreError("portable store document inventory digest mismatch")
    documents = _parse_jsonl(
        documents_raw,
        record_limit=MAX_DOCUMENT_RECORDS,
        label="document inventory",
        path=documents_path,
    )
    if len(documents) != int(documents_meta.get("count", -1)):
        raise StoreError("portable store document inventory count mismatch")
    source_hashes: dict[str, str] = {}
    document_ids: set[str] = set()
    definition_ids: dict[str, list[str]] = {}
    for node in state.nodes.values():
        provenance = node.get("provenance") or {}
        authority = str(provenance.get("authority", ""))
        if authority and provenance.get("active") and node.get("type") == "knowledge":
            definition_ids.setdefault(authority, []).append(str(node["id"]))
    reference_counts: dict[str, int] = {}
    for reference in state.references:
        authority = str(reference.get("authority", ""))
        reference_counts[authority] = reference_counts.get(authority, 0) + 1
    document_schema = (
        LEGACY_DOCUMENT_RECORD_SCHEMA
        if legacy
        else str(documents_meta.get("record_schema", ""))
    )
    authorities = [str(record.get("authority", "")) for record in documents]
    if authorities != sorted(authorities):
        raise StoreError("portable document inventory is not authority-sorted")
    authority_bytes = 0
    for record in documents:
        _validate_document_record(record, document_schema)
        authority = str(record.get("authority", ""))
        digest = str(record.get("source_sha256", ""))
        if not authority or authority in source_hashes:
            raise StoreError(f"duplicate or empty authority record: {authority!r}")
        source_path = _resolve(root, authority)
        authority_bytes += _bounded_regular_file_size(
            source_path,
            MAX_AUTHORITY_BYTES,
            "authority document",
        )
        if authority_bytes > MAX_AUTHORITY_TOTAL_BYTES:
            raise StoreError("authority document inventory exceeds the byte budget")
        if sha256_file(source_path) != digest:
            raise StoreError(f"authority snapshot digest mismatch: {authority}")
        source_hashes[authority] = digest
        if list(record.get("definition_ids") or []) != sorted(
            definition_ids.get(authority, [])
        ):
            raise StoreError(
                f"document definition inventory does not match graph: {authority}"
            )
        if record.get("reference_count") != reference_counts.get(authority, 0):
            raise StoreError(
                f"document reference inventory does not match graph: {authority}"
            )
        if not legacy:
            document_id = str(record.get("document_id", ""))
            if document_id in document_ids:
                raise StoreError(f"duplicate document identity: {document_id}")
            document_ids.add(document_id)
    expected_source_hashes = dict(state.manifest.get("source_hashes") or {})
    if source_hashes != expected_source_hashes:
        raise StoreError("document inventory does not match graph source hashes")
    source_snapshot_sha256 = sha256_json(documents)
    if source_snapshot_sha256 != str(
        documents_meta.get("source_snapshot_sha256", "")
    ):
        raise StoreError("portable store source snapshot digest mismatch")
    document_generation_sha256: str | None = None
    if not legacy:
        document_generation_sha256 = sha256_json(
            {
                "record_schema": document_schema,
                "count": len(documents),
                "sha256": _sha256_text(documents_text),
            }
        )
        if document_generation_sha256 != str(
            documents_meta.get("document_generation_sha256", "")
        ):
            raise StoreError("portable store document generation mismatch")

    embedding_manifest, embedding_records = _embedding_bundle(
        root, embedding_manifest_path, snapshot
    )
    if sha256_file(embedding_manifest_path) != str(
        manifest.get("embedding_manifest_sha256", "")
    ):
        raise StoreError("portable store embedding manifest digest mismatch")
    if embedding_manifest["embedding_generation_sha256"] != str(
        manifest.get("embedding_generation_sha256", "")
    ):
        raise StoreError("portable store embedding generation mismatch")

    knowledge_payload = {
        "registry_sha256": manifest["registry_sha256"],
        "source_snapshot_sha256": source_snapshot_sha256,
        "graph_sha256": manifest["graph_sha256"],
        "identity_sha256": manifest.get("identity_sha256"),
        "alignment_sha256": manifest["alignment_sha256"],
    }
    if not legacy:
        knowledge_payload["document_generation_sha256"] = document_generation_sha256
    if sha256_json(knowledge_payload) != str(
        manifest.get("knowledge_generation_sha256", "")
    ):
        raise StoreError("portable store knowledge generation mismatch")
    readiness: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    if not legacy:
        policy_value = paths.get("embedding_policy")
        if policy_value is not None:
            policy_path = _resolve(root, str(policy_value))
            _bounded_regular_file_size(
                policy_path,
                MAX_JSON_BYTES,
                "embedding policy",
            )
            try:
                policy = load_embedding_policy(policy_path)
            except EmbeddingError as error:
                raise StoreError(f"portable embedding policy is invalid: {error}") from error
            if sha256_file(policy_path) != str(
                manifest.get("embedding_policy_file_sha256", "")
            ):
                raise StoreError("portable embedding policy file digest mismatch")
            if sha256_json(policy) != manifest.get("embedding_policy_sha256"):
                raise StoreError("portable embedding policy digest mismatch")
        elif manifest.get("embedding_policy_sha256") is not None:
            raise StoreError("unmanaged portable store has an embedding policy digest")
        elif manifest.get("embedding_policy_file_sha256") is not None:
            raise StoreError("unmanaged portable store has an embedding policy file digest")
        stored_readiness = manifest.get("readiness")
        if not isinstance(stored_readiness, dict):
            raise StoreError("portable readiness payload is invalid")
        bindings = {
            str(profile.get("name", "")): {
                "configuration_status": profile.get("configuration_status"),
                "provider_config_sha256": profile.get("provider_config_sha256"),
            }
            for profile in stored_readiness.get("profiles") or []
            if isinstance(profile, dict)
        }
        readiness = _portable_readiness(
            snapshot,
            [record for record, _ in embedding_records],
            policy,
            bindings,
        )
        if readiness != stored_readiness:
            raise StoreError("portable retrieval readiness mismatch")
        if sha256_json(readiness) != str(manifest.get("readiness_sha256", "")):
            raise StoreError("portable retrieval readiness digest mismatch")
    store_payload = {
        "knowledge_generation_sha256": manifest["knowledge_generation_sha256"],
        "embedding_generation_sha256": manifest["embedding_generation_sha256"],
    }
    if not legacy:
        store_payload["readiness_sha256"] = manifest["readiness_sha256"]
    store_generation = sha256_json(store_payload)
    if store_generation != str(manifest.get("store_generation_sha256", "")):
        raise StoreError("portable store generation mismatch")
    return {
        "manifest": manifest,
        "state": state,
        "snapshot": snapshot,
        "alignment_set": alignment_set,
        "embedding_records": embedding_records,
        "documents": documents,
        "policy": policy,
        "readiness": readiness,
    }


def verify_store(root: Path, *, require_ready: bool = False) -> dict[str, Any]:
    root = root.resolve()
    with _store_writer_lock(root):
        _recover_publication(root)
        validated = _validated_store(root)
        receipt = _receipt(
            "verify",
            validated["manifest"],
            validated["snapshot"],
            validated["embedding_records"],
        )
        if require_ready:
            _require_ready(receipt)
        return receipt


def _remove_stale_managed(
    root: Path,
    old_manifest: dict[str, Any],
    current: set[str],
    allowed: set[str],
    authority_hashes: dict[str, str],
    *,
    remove: bool = True,
) -> None:
    old = {
        str(item)
        for item in old_manifest.get("managed_paths", [])
        if str(item).strip()
    }
    for relative in sorted((old - current) & allowed, reverse=True):
        path = _resolve(root, relative)
        if path.is_file():
            expected = authority_hashes.get(relative)
            if expected is not None and sha256_file(path) != expected:
                raise StoreError(
                    f"refusing to remove locally modified stale authority: {relative}"
                )
            if remove:
                path.unlink()


def _source_generation_token(
    repo_root: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path,
    alignments: Path,
) -> str:
    _bounded_regular_file_size(registry, MAX_JSON_BYTES, "source registry")
    if identities.is_file():
        _bounded_regular_file_size(
            identities,
            MAX_JSON_BYTES,
            "identity registry",
        )
    if alignments.is_file():
        _bounded_regular_file_size(
            alignments,
            MAX_JSON_BYTES,
            "alignment registry",
        )
    _preflight_graph_artifacts(graph_dir)
    state = load_state(graph_dir)
    paths = [registry, *_graph_paths(graph_dir, state)]
    paths.extend(
        path for path in (identities, alignments) if path.is_file()
    )
    authority_bytes = 0
    for authority in sorted((state.manifest.get("source_hashes") or {})):
        path = _resolve(repo_root, authority)
        authority_bytes += _bounded_regular_file_size(
            path,
            MAX_AUTHORITY_BYTES,
            "authority document",
        )
        if authority_bytes > MAX_AUTHORITY_TOTAL_BYTES:
            raise StoreError("authority document inventory exceeds the byte budget")
        paths.append(path)
    inventory = {
        relative_path(repo_root, path): sha256_file(path)
        for path in sorted(paths, key=lambda value: os.fspath(value))
    }
    return sha256_json(inventory)


def _publish_candidate(
    candidate: Path,
    root: Path,
    paths: set[str],
    *,
    stage: Path,
) -> bool:
    """Publish candidate files with a durable rollback journal and manifest last."""

    manifest_relative = STORE_MANIFEST_PATH.as_posix()
    ordered = sorted(paths - {manifest_relative}) + [manifest_relative]

    def files_equal(first: Path, second: Path) -> bool:
        try:
            first_stat = first.stat()
            second_stat = second.stat()
        except OSError:
            return False
        return (
            stat.S_ISREG(first_stat.st_mode)
            and stat.S_ISREG(second_stat.st_mode)
            and first_stat.st_size == second_stat.st_size
            and sha256_file(first) == sha256_file(second)
        )

    changed = [
        relative
        for relative in ordered
        if not files_equal(
            _resolve(root, relative),
            _resolve(candidate, relative),
        )
    ]
    if not changed:
        return False
    backup_root = stage / "backup"
    backups: list[str] = []
    created: list[str] = []
    for relative in changed:
        destination = _resolve(root, relative)
        if destination.is_file():
            _copy_file(destination, _resolve(backup_root, relative))
            backups.append(relative)
        else:
            created.append(relative)
    manifest_source = _resolve(candidate, manifest_relative)
    journal = {
        "schema": "qlkg-store-publication-journal-v1",
        "root": os.fspath(root.resolve()),
        "stage": os.fspath(stage.resolve()),
        "new_manifest_sha256": sha256_file(manifest_source),
        "backups": backups,
        "created": created,
    }
    journal_path = _journal_path(root)
    _atomic_write_text(journal_path, _pretty_json(journal))
    committed = False
    try:
        for relative in changed:
            _copy_file(_resolve(candidate, relative), _resolve(root, relative))
            if relative == manifest_relative:
                committed = True
    except BaseException:
        if not committed:
            # Recovery is deterministic and uses the journal we just sealed.
            _recover_publication(root)
        raise
    else:
        journal_path.unlink(missing_ok=True)
    return True


def snapshot_store(
    repo_root: Path,
    output_root: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path,
    alignments: Path,
    database: Path,
    policy: Mapping[str, Any] | None = None,
    provider_configs: Mapping[str, Mapping[str, Any]] | None = None,
    policy_path: Path | None = None,
    namespace: str = "personal",
    require_ready: bool = False,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Create or refresh one self-contained portable store generation."""
    if require_ready and allow_partial:
        raise StoreError(
            "--require-ready and --allow-partial are mutually exclusive",
            code="invalid-readiness-mode",
        )
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    _relative(repo_root, registry)
    _relative(repo_root, graph_dir)
    if identities.is_file():
        _relative(repo_root, identities)
    if alignments.is_file():
        _relative(repo_root, alignments)
    if output_root != repo_root:
        try:
            output_root.relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise StoreError("portable store output cannot be nested inside its source project")
        try:
            repo_root.relative_to(output_root)
        except ValueError:
            pass
        else:
            raise StoreError("portable store output cannot contain its source project")
    resolved_policy_path = (
        policy_path.resolve()
        if policy_path is not None
        else (repo_root / DEFAULT_EMBEDDING_POLICY_PATH).resolve()
    )
    policy_was_explicit = policy_path is not None
    validated_policy: dict[str, Any] | None = None
    if resolved_policy_path.is_file():
        _relative(repo_root, resolved_policy_path)
        _bounded_regular_file_size(
            resolved_policy_path,
            MAX_JSON_BYTES,
            "embedding policy",
        )
        try:
            loaded_policy = load_embedding_policy(resolved_policy_path)
        except EmbeddingError as error:
            raise StoreError(f"embedding policy is invalid: {error}") from error
        if policy is not None:
            try:
                supplied = validate_contract(dict(policy))
            except ContractError as error:
                raise StoreError(f"embedding policy is invalid: {error}") from error
            if supplied != loaded_policy:
                raise StoreError("supplied embedding policy does not match policy_path")
        validated_policy = loaded_policy
    elif policy is not None:
        try:
            validated_policy = validate_contract(dict(policy))
        except ContractError as error:
            raise StoreError(f"embedding policy is invalid: {error}") from error
        if output_root == repo_root:
            raise StoreError("managed in-place snapshot requires a committed policy file")
    elif policy_was_explicit:
        raise StoreError("explicit embedding policy does not exist")
    configs = _validated_provider_configs(provider_configs)

    with _store_writer_lock(output_root):
        _recover_publication(output_root)
        old_manifest: dict[str, Any] = {}
        old_documents: dict[str, dict[str, Any]] = {}
        old_cleanup_allowed: set[str] = set()
        old_authority_hashes: dict[str, str] = {}
        old_path = output_root / STORE_MANIFEST_PATH
        if old_path.is_file():
            old_manifest = _store_manifest(output_root)
            old_documents = _old_document_records(output_root, old_manifest)
            old_paths = old_manifest.get("paths") or {}
            for authority, record in old_documents.items():
                old_authority_hashes[authority] = str(record["source_sha256"])
            old_cleanup_allowed.update(
                {
                    DOCUMENTS_PATH.as_posix(),
                    EMBEDDING_MANIFEST_PATH.as_posix(),
                    (
                        EMBEDDING_MANIFEST_PATH.parent / EMBEDDING_RECORDS_NAME
                    ).as_posix(),
                }
            )
            for value in old_manifest.get("managed_paths") or []:
                relative = str(value)
                if re.fullmatch(
                    r"knowledge/embeddings/objects/[0-9a-f]{2}/[0-9a-f]{64}\.f32",
                    relative,
                ):
                    old_cleanup_allowed.add(relative)
            if output_root != repo_root:
                old_cleanup_allowed.update(old_authority_hashes)
                old_cleanup_allowed.update(
                    str(value)
                    for key in ("registry", "identities", "alignments", "embedding_policy")
                    if (value := old_paths.get(key))
                )
                old_graph = str(old_paths.get("graph", "")).rstrip("/")
                if old_graph:
                    old_cleanup_allowed.update(
                        str(value)
                        for value in old_manifest.get("managed_paths") or []
                        if str(value) == old_graph
                        or str(value).startswith(f"{old_graph}/")
                    )
        else:
            conflicts = [
                path
                for path in (
                    output_root / DOCUMENTS_PATH,
                    output_root / EMBEDDING_MANIFEST_PATH,
                    output_root
                    / EMBEDDING_MANIFEST_PATH.parent
                    / EMBEDDING_RECORDS_NAME,
                )
                if path.exists()
            ]
            if conflicts:
                raise StoreError(
                    "refusing to overwrite portable artifacts without a valid store manifest: "
                    + ", ".join(str(path) for path in conflicts)
                )

        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{output_root.name}.kgdistiller-store-stage-",
                dir=_filesystem_path(output_root.parent),
            )
        ).resolve()
        candidate = stage / "candidate"
        candidate.mkdir(parents=True)
        try:
            _bounded_regular_file_size(registry, MAX_JSON_BYTES, "source registry")
            if identities.is_file():
                _bounded_regular_file_size(
                    identities,
                    MAX_JSON_BYTES,
                    "identity registry",
                )
            if alignments.is_file():
                _bounded_regular_file_size(
                    alignments,
                    MAX_JSON_BYTES,
                    "alignment registry",
                )
            _preflight_graph_artifacts(graph_dir)
            source_token = _source_generation_token(
                repo_root,
                registry=registry,
                graph_dir=graph_dir,
                identities=identities,
                alignments=alignments,
            )
            _preflight_graph_artifacts(graph_dir)
            state = load_state(graph_dir)
            snapshot = make_agent_snapshot(state, namespace=namespace)

            frozen_database = stage / "frozen.sqlite"
            database_token: str | None = None
            database_existed = agent_index_exists(database)
            if database_existed:
                before_backup_token = index_generation_token(database)
                backup_agent_index(database, frozen_database)
                database_token = index_generation_token(database)
                if database_token != before_backup_token:
                    raise StoreError(
                        "Agent index changed while portable snapshot was frozen",
                        code="stale-generation",
                    )
                status = index_status(frozen_database)
                if status.get("graph_sha256") != snapshot["graph"]["sha256"]:
                    raise StoreError("local Agent index is stale for the graph generation")
                if status.get("namespace") != namespace:
                    raise StoreError("local Agent index namespace does not match snapshot")
            policy_file_token = (
                sha256_file(resolved_policy_path)
                if resolved_policy_path.is_file()
                else None
            )

            source_hashes = dict(state.manifest.get("source_hashes") or {})
            _bounded_regular_file_size(registry, MAX_JSON_BYTES, "source registry")
            specs = load_sources(repo_root, registry)
            definition_ids: dict[str, list[str]] = {}
            for node in state.nodes.values():
                provenance = node.get("provenance") or {}
                authority = str(provenance.get("authority", ""))
                if authority and provenance.get("active") and node.get("type") == "knowledge":
                    definition_ids.setdefault(authority, []).append(str(node["id"]))
            reference_counts: dict[str, int] = {}
            for reference in state.references:
                authority = str(reference.get("authority", ""))
                reference_counts[authority] = reference_counts.get(authority, 0) + 1

            documents: list[dict[str, Any]] = []
            source_paths: list[Path] = []
            seen_document_ids: set[str] = set()
            authority_bytes = 0
            for authority, digest in sorted(source_hashes.items()):
                source_path = _resolve(repo_root, authority)
                authority_bytes += _bounded_regular_file_size(
                    source_path,
                    MAX_AUTHORITY_BYTES,
                    "authority document",
                )
                if authority_bytes > MAX_AUTHORITY_TOTAL_BYTES:
                    raise StoreError(
                        "authority document inventory exceeds the byte budget"
                    )
                if sha256_file(source_path) != digest:
                    raise StoreError(f"graph source hash is stale: {authority}")
                spec = unique_source_for_path(specs, source_path)
                source_paths.append(source_path)
                previous = old_documents.get(authority) or {}
                document_id = (
                    str(previous["document_id"])
                    if previous.get("schema") == DOCUMENT_RECORD_SCHEMA
                    else _document_id(spec.id, authority, digest)
                )
                if document_id in seen_document_ids:
                    raise StoreError(f"duplicate document identity: {document_id}")
                seen_document_ids.add(document_id)
                record = {
                    "schema": DOCUMENT_RECORD_SCHEMA,
                    "document_id": document_id,
                    "source_id": spec.id,
                    "authority": authority,
                    "authority_history": list(previous.get("authority_history") or []),
                    "format": _document_format_v2(source_path),
                    "knowledge_origin": spec.knowledge_origin,
                    "external_ids": dict(previous.get("external_ids") or {}),
                    "source_sha256": digest,
                    "definition_ids": sorted(definition_ids.get(authority, [])),
                    "reference_count": reference_counts.get(authority, 0),
                }
                _validate_document_record(record, DOCUMENT_RECORD_SCHEMA)
                documents.append(record)
            documents_text = _jsonl(documents)
            source_snapshot_sha256 = sha256_json(documents)
            documents_sha256 = _sha256_text(documents_text)
            document_generation_sha256 = sha256_json(
                {
                    "record_schema": DOCUMENT_RECORD_SCHEMA,
                    "count": len(documents),
                    "sha256": documents_sha256,
                }
            )

            config_paths = [registry]
            if identities.is_file():
                config_paths.append(identities)
            if alignments.is_file():
                config_paths.append(alignments)
            graph_paths = _graph_paths(graph_dir, state)
            paths_to_copy = [*source_paths, *config_paths, *graph_paths]
            copied_managed: set[str] = set()
            for source in paths_to_copy:
                relative = relative_path(repo_root, source)
                _copy_file(source, _resolve(candidate, relative))
                if output_root != repo_root:
                    copied_managed.add(relative)

            gitignore_source = output_root / "knowledge/.gitignore"
            if not gitignore_source.is_file():
                gitignore_source = repo_root / "knowledge/.gitignore"
            if gitignore_source.is_file():
                _copy_file(gitignore_source, candidate / "knowledge/.gitignore")
            ensure_knowledge_gitignore(candidate / "knowledge/.gitignore")

            embedding_policy_relative: str | None = None
            embedding_policy_file_sha256: str | None = None
            if validated_policy is not None:
                try:
                    embedding_policy_relative = relative_path(
                        repo_root, resolved_policy_path
                    )
                except ValueError as error:
                    raise StoreError(
                        "portable embedding policy must be repository-relative"
                    ) from error
                candidate_policy = _resolve(candidate, embedding_policy_relative)
                if resolved_policy_path.is_file():
                    _copy_file(resolved_policy_path, candidate_policy)
                else:
                    _atomic_write_text(candidate_policy, _pretty_json(validated_policy))
                embedding_policy_file_sha256 = sha256_file(candidate_policy)

            _atomic_write_text(_resolve(candidate, DOCUMENTS_PATH), documents_text)
            embedding_manifest, embedding_managed = _export_embeddings(
                frozen_database if frozen_database.is_file() else database,
                candidate,
                snapshot,
            )
            portable_records = _read_jsonl(
                candidate / EMBEDDING_MANIFEST_PATH.parent / EMBEDDING_RECORDS_NAME,
                byte_limit=MAX_EMBEDDING_RECORD_BYTES,
                record_limit=MAX_EMBEDDING_RECORDS,
                label="embedding records",
            )
            bindings = (
                _profile_bindings(validated_policy, configs)
                if validated_policy is not None
                else {}
            )
            readiness = _portable_readiness(
                snapshot, portable_records, validated_policy, bindings
            )
            readiness_sha256 = sha256_json(readiness)
            managed_paths = copied_managed | embedding_managed | {
                DOCUMENTS_PATH.as_posix()
            }
            if output_root != repo_root and embedding_policy_relative is not None:
                managed_paths.add(embedding_policy_relative)

            registry_relative = relative_path(repo_root, registry)
            graph_relative = relative_path(repo_root, graph_dir)
            identities_relative = (
                relative_path(repo_root, identities) if identities.is_file() else None
            )
            alignments_relative = (
                relative_path(repo_root, alignments) if alignments.is_file() else None
            )
            output_registry = _resolve(candidate, registry_relative)
            output_identities = (
                _resolve(candidate, identities_relative) if identities_relative else None
            )
            output_alignments = (
                _resolve(candidate, alignments_relative) if alignments_relative else None
            )
            alignment_set = load_alignment_set(output_alignments)
            registry_sha256 = sha256_file(output_registry)
            identity_sha256 = identity_registry_sha256(output_identities)
            alignment_sha256 = alignment_sha256_json(alignment_set)
            knowledge_payload = {
                "registry_sha256": registry_sha256,
                "source_snapshot_sha256": source_snapshot_sha256,
                "document_generation_sha256": document_generation_sha256,
                "graph_sha256": snapshot["graph"]["sha256"],
                "identity_sha256": identity_sha256,
                "alignment_sha256": alignment_sha256,
            }
            knowledge_generation_sha256 = sha256_json(knowledge_payload)
            store_generation_sha256 = sha256_json(
                {
                    "knowledge_generation_sha256": knowledge_generation_sha256,
                    "embedding_generation_sha256": embedding_manifest[
                        "embedding_generation_sha256"
                    ],
                    "readiness_sha256": readiness_sha256,
                }
            )
            manifest: dict[str, Any] = {
                "schema": STORE_SCHEMA,
                "generator": "kgdistiller",
                "paths": {
                    "registry": registry_relative,
                    "identities": identities_relative,
                    "alignments": alignments_relative,
                    "graph": graph_relative,
                    "documents": DOCUMENTS_PATH.as_posix(),
                    "embedding_manifest": EMBEDDING_MANIFEST_PATH.as_posix(),
                    "embedding_policy": embedding_policy_relative,
                },
                "documents": {
                    "record_schema": DOCUMENT_RECORD_SCHEMA,
                    "count": len(documents),
                    "sha256": documents_sha256,
                    "source_snapshot_sha256": source_snapshot_sha256,
                    "document_generation_sha256": document_generation_sha256,
                },
                "registry_sha256": registry_sha256,
                "identity_sha256": identity_sha256,
                "alignment_sha256": alignment_sha256,
                "graph_sha256": snapshot["graph"]["sha256"],
                "embedding_manifest_sha256": sha256_file(
                    _resolve(candidate, EMBEDDING_MANIFEST_PATH)
                ),
                "embedding_policy_file_sha256": embedding_policy_file_sha256,
                "embedding_policy_sha256": (
                    sha256_json(validated_policy)
                    if validated_policy is not None
                    else None
                ),
                "readiness": readiness,
                "readiness_sha256": readiness_sha256,
                "knowledge_generation_sha256": knowledge_generation_sha256,
                "embedding_generation_sha256": embedding_manifest[
                    "embedding_generation_sha256"
                ],
                "store_generation_sha256": store_generation_sha256,
                "managed_paths": sorted(managed_paths),
            }
            manifest["store_sha256"] = sha256_json(manifest)
            _atomic_write_text(
                _resolve(candidate, STORE_MANIFEST_PATH), _pretty_json(manifest)
            )
            validated_candidate = _validated_store(candidate)
            candidate_receipt = _receipt(
                "snapshot",
                manifest,
                snapshot,
                validated_candidate["embedding_records"],
                changed=False,
                root=str(output_root),
                mode=("in-place" if output_root == repo_root else "snapshot-copy"),
            )
            if require_ready or (validated_policy is not None and not allow_partial):
                _require_ready(candidate_receipt)

            if _source_generation_token(
                repo_root,
                registry=registry,
                graph_dir=graph_dir,
                identities=identities,
                alignments=alignments,
            ) != source_token:
                raise StoreError(
                    "source graph changed while portable snapshot was staged",
                    code="stale-generation",
                )
            if (
                sha256_file(resolved_policy_path)
                if resolved_policy_path.is_file()
                else None
            ) != policy_file_token:
                raise StoreError(
                    "embedding policy changed while portable snapshot was staged",
                    code="stale-generation",
                )
            if database_token is not None and index_generation_token(database) != database_token:
                raise StoreError(
                    "Agent index changed while portable snapshot was staged",
                    code="stale-generation",
                )
            if database_token is None and agent_index_exists(database) != database_existed:
                raise StoreError(
                    "Agent index appeared while portable snapshot was staged",
                    code="stale-generation",
                )

            _remove_stale_managed(
                output_root,
                old_manifest,
                managed_paths,
                old_cleanup_allowed,
                old_authority_hashes,
                remove=False,
            )
            publication_paths = set(managed_paths) | {
                "knowledge/.gitignore",
                STORE_MANIFEST_PATH.as_posix(),
            }
            if output_root == repo_root and embedding_policy_relative is not None:
                # It was validated from the live repository and does not need rewriting.
                publication_paths.discard(embedding_policy_relative)
            changed = _publish_candidate(
                candidate,
                output_root,
                publication_paths,
                stage=stage,
            )
            try:
                _remove_stale_managed(
                    output_root,
                    old_manifest,
                    managed_paths,
                    old_cleanup_allowed,
                    old_authority_hashes,
                )
            except (OSError, StoreError):
                # New manifest is already the commit point. A safe stale file is
                # merely unreferenced and can be cleaned by a later snapshot.
                pass
            final = _validated_store(output_root)
            return _receipt(
                "snapshot",
                final["manifest"],
                final["snapshot"],
                final["embedding_records"],
                changed=changed,
                root=str(output_root),
                mode=("in-place" if output_root == repo_root else "snapshot-copy"),
            )
        finally:
            if not _journal_path(output_root).is_file():
                shutil.rmtree(_filesystem_path(stage), ignore_errors=True)


def _import_embeddings(
    database: Path,
    records: list[tuple[dict[str, Any], bytes]],
    *,
    store_generation_sha256: str,
    knowledge_generation_sha256: str,
    embedding_generation_sha256: str,
    document_generation_sha256: str | None,
    embedding_policy_sha256: str | None,
    readiness_sha256: str | None,
    portable_status: str,
) -> None:
    with _mutable_agent_index(database) as connection:
        connection.execute("DELETE FROM embeddings")
        for record, payload in records:
            connection.execute(
                """
                INSERT INTO embeddings
                (namespace, node_id, provider, model, dimensions,
                 embedding_input_schema, content_sha256,
                 provider_config_sha256, vector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record["namespace"]),
                    str(record["node_id"]),
                    str(record["provider"]),
                    str(record["model"]),
                    int(record["dimensions"]),
                    str(record["embedding_input_schema"]),
                    str(record["content_sha256"]),
                    str(record["provider_config_sha256"]),
                    payload,
                ),
            )
        providers = sorted(
            {
                _canonical_json(
                    {
                        "name": record["provider"],
                        "model": record["model"],
                        "dimensions": record["dimensions"],
                        "embedding_input_schema": record[
                            "embedding_input_schema"
                        ],
                        "provider_config_sha256": record[
                            "provider_config_sha256"
                        ],
                    }
                )
                for record, _ in records
            }
        )
        provider_payload: Any
        if len(providers) == 1:
            provider_payload = json.loads(providers[0])
        else:
            provider_payload = {"configurations": [json.loads(value) for value in providers]}
        providers_row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'providers'"
        ).fetchone()
        metadata = json.loads(providers_row[0]) if providers_row else {}
        metadata["embedding"] = provider_payload if records else None
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('providers', ?)",
            (_canonical_json(metadata),),
        )
        lanes_row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'retrieval_lanes'"
        ).fetchone()
        lanes = json.loads(lanes_row[0]) if lanes_row else []
        if records and "embedding" not in lanes:
            lanes.append("embedding")
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('retrieval_lanes', ?)",
            (_canonical_json(lanes),),
        )
        provider_config_digests = sorted(
            {str(record["provider_config_sha256"]) for record, _ in records}
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('provider_config_sha256', ?)",
            (
                _canonical_json(
                    sha256_json(provider_config_digests)
                    if provider_config_digests
                    else None
                ),
            ),
        )
        for key, value in (
            ("store_generation_sha256", store_generation_sha256),
            ("knowledge_generation_sha256", knowledge_generation_sha256),
            ("embedding_generation_sha256", embedding_generation_sha256),
            ("document_generation_sha256", document_generation_sha256),
            ("embedding_policy_sha256", embedding_policy_sha256),
            ("readiness_sha256", readiness_sha256),
            ("portable_status", portable_status),
            ("optional_similarity_state", "absent"),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
                (key, _canonical_json(value)),
            )
        connection.commit()


def _materialized_index_is_current(
    database: Path,
    *,
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
    records: list[tuple[dict[str, Any], bytes]],
) -> bool:
    """Validate the idempotency marker and exact restored vector inventory."""

    legacy = manifest.get("schema") == LEGACY_STORE_SCHEMA
    expected_metadata = {
        "schema": INDEX_SCHEMA,
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "graph_sha256": snapshot["graph"]["sha256"],
        "namespace": snapshot["namespace"],
        "counts": snapshot["graph"]["counts"],
        "alignment_sha256": manifest["alignment_sha256"],
        "store_generation_sha256": manifest["store_generation_sha256"],
        "knowledge_generation_sha256": manifest["knowledge_generation_sha256"],
        "embedding_generation_sha256": manifest["embedding_generation_sha256"],
        "document_generation_sha256": (
            None
            if legacy
            else (manifest.get("documents") or {})["document_generation_sha256"]
        ),
        "embedding_policy_sha256": (
            None if legacy else manifest.get("embedding_policy_sha256")
        ),
        "readiness_sha256": None if legacy else manifest.get("readiness_sha256"),
        "portable_status": (
            "unmanaged" if legacy else (manifest.get("readiness") or {})["state"]
        ),
        "optional_similarity_state": "absent",
    }
    expected_records = sorted(
        records,
        key=lambda item: (
            str(item[0]["namespace"]),
            str(item[0]["node_id"]),
            str(item[0]["provider"]),
            str(item[0]["model"]),
            str(item[0]["provider_config_sha256"]),
        ),
    )
    try:
        before_token = index_generation_token(database)
        status = index_status(database)
        if any(status.get(key) != value for key, value in expected_metadata.items()):
            return False
        connection = open_agent_index(database)
        try:
            actual_counts = {
                "nodes": int(
                    connection.execute("SELECT count(*) FROM nodes").fetchone()[0]
                ),
                "edges": int(
                    connection.execute("SELECT count(*) FROM edges").fetchone()[0]
                ),
                "references": int(
                    connection.execute("SELECT count(*) FROM refs").fetchone()[0]
                ),
            }
            if actual_counts != snapshot["graph"]["counts"]:
                return False
            count, vector_bytes = connection.execute(
                "SELECT count(*), coalesce(sum(length(vector)), 0) FROM embeddings"
            ).fetchone()
            if int(count) != len(expected_records):
                return False
            if int(vector_bytes) > MAX_EMBEDDING_VECTOR_BYTES:
                return False
            cursor = connection.execute(
                """
                SELECT namespace, node_id, provider, model, dimensions,
                       embedding_input_schema, content_sha256,
                       provider_config_sha256, vector
                FROM embeddings
                ORDER BY namespace, node_id, provider, model,
                         provider_config_sha256
                """
            )
            for index, row in enumerate(cursor):
                record, payload = expected_records[index]
                if (
                    str(row["namespace"]) != str(record["namespace"])
                    or str(row["node_id"]) != str(record["node_id"])
                    or str(row["provider"]) != str(record["provider"])
                    or str(row["model"]) != str(record["model"])
                    or int(row["dimensions"]) != int(record["dimensions"])
                    or str(row["embedding_input_schema"])
                    != str(record["embedding_input_schema"])
                    or str(row["content_sha256"])
                    != str(record["content_sha256"])
                    or str(row["provider_config_sha256"])
                    != str(record["provider_config_sha256"])
                    or bytes(row["vector"]) != payload
                ):
                    return False
        finally:
            connection.close()
        return index_generation_token(database) == before_token
    except (OSError, sqlite3.Error, ValueError):
        return False


def materialize_store(
    root: Path,
    database: Path,
    *,
    require_ready: bool = False,
    provider_configs: Mapping[str, Mapping[str, Any]] | None = None,
    namespace: str = "personal",
) -> dict[str, Any]:
    """Build the local disposable index from one verified store generation."""

    root = root.resolve()
    database = database.resolve()
    configs = (
        None
        if provider_configs is None
        else _validated_provider_configs(provider_configs)
    )
    with _store_writer_lock(root):
        _recover_publication(root)
        validated = _validated_store(root)
        manifest = validated["manifest"]
        snapshot = validated["snapshot"]
        alignment_set = validated["alignment_set"]
        embedding_records = validated["embedding_records"]
        if str(snapshot.get("namespace")) != namespace:
            raise StoreError("portable store namespace does not match requested namespace")

        semantic_status = "not-checked"
        if configs is not None:
            policy = validated["policy"]
            if policy is None or validated["readiness"]["state"] != "ready":
                semantic_status = "semantic-search-not-ready"
            else:
                local_readiness = _portable_readiness(
                    snapshot,
                    [record for record, _ in embedding_records],
                    policy,
                    _profile_bindings(policy, configs),
                )
                semantic_status = (
                    "semantic-search-ready"
                    if local_readiness["state"] == "ready"
                    else "semantic-search-not-ready"
                )

        initial_receipt = _receipt(
            "materialize",
            manifest,
            snapshot,
            embedding_records,
            materialization_status="not-checked",
            semantic_status="not-checked",
            database=str(database),
            materialized=False,
        )
        if require_ready:
            _require_ready(initial_receipt)

        if agent_index_exists(database):
            if _materialized_index_is_current(
                database,
                manifest=manifest,
                snapshot=snapshot,
                records=embedding_records,
            ):
                return _receipt(
                    "materialize",
                    manifest,
                    snapshot,
                    embedding_records,
                    materialization_status="current",
                    semantic_status=semantic_status,
                    database=str(database),
                    materialized=False,
                )

        _filesystem_path(database.parent).mkdir(parents=True, exist_ok=True)
        stage_root = Path(
            tempfile.mkdtemp(
                prefix=f".{database.name}.materialize-",
                dir=_filesystem_path(database.parent),
            )
        )
        staged_database = stage_root / "complete.sqlite"
        expected_store_sha256 = str(manifest["store_sha256"])
        expected_store_generation = str(manifest["store_generation_sha256"])
        try:
            # Graph rows and exact vectors are assembled privately. The logical
            # database receives one marker only after the complete SQLite file is
            # durable, so observers can see strictly the old or the new generation.
            write_agent_index(staged_database, snapshot, alignment_set)
            _import_embeddings(
                staged_database,
                embedding_records,
                store_generation_sha256=expected_store_generation,
                knowledge_generation_sha256=str(
                    manifest["knowledge_generation_sha256"]
                ),
                embedding_generation_sha256=str(
                    manifest["embedding_generation_sha256"]
                ),
                document_generation_sha256=(
                    None
                    if manifest.get("schema") == LEGACY_STORE_SCHEMA
                    else str(
                        (manifest.get("documents") or {})[
                            "document_generation_sha256"
                        ]
                    )
                ),
                embedding_policy_sha256=(
                    None
                    if manifest.get("schema") == LEGACY_STORE_SCHEMA
                    else manifest.get("embedding_policy_sha256")
                ),
                readiness_sha256=(
                    None
                    if manifest.get("schema") == LEGACY_STORE_SCHEMA
                    else manifest.get("readiness_sha256")
                ),
                portable_status=str(initial_receipt["portable_status"]),
            )

            # Re-verify every generation-bound artifact immediately before the
            # atomic database publication. The sibling writer lock prevents a
            # cooperating snapshot writer from advancing the store meanwhile;
            # this second pass also fails closed on out-of-band mutation.
            current = _validated_store(root)
            if (
                current["manifest"].get("store_sha256") != expected_store_sha256
                or current["manifest"].get("store_generation_sha256")
                != expected_store_generation
            ):
                raise StoreError(
                    "portable store changed while materialization was staged",
                    code="stale-generation",
                )
            publish_agent_index_file(resolve_agent_index_path(staged_database), database)
        finally:
            shutil.rmtree(_filesystem_path(stage_root), ignore_errors=True)
        return _receipt(
            "materialize",
            manifest,
            snapshot,
            embedding_records,
            materialization_status="materialized",
            semantic_status=semantic_status,
            database=str(database),
            materialized=True,
        )
