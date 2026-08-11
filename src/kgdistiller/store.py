"""Portable, Git-friendly knowledge store snapshots and materialization."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from .agent import (
    EMBEDDING_INPUT_SCHEMA,
    INDEX_SCHEMA,
    _filesystem_path,
    _mutable_agent_index,
    agent_index_exists,
    embedding_input_sha256,
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
    KnowledgeError,
    identity_registry_sha256,
    load_sources,
    load_state,
    make_agent_snapshot,
    relative_path,
    sha256_authority_file,
    sha256_file,
    source_format,
    unique_source_for_path,
)
from .json_schema import validate_json_schema
from .project import ensure_knowledge_gitignore


STORE_SCHEMA = "qlkg-store-v1"
DOCUMENT_RECORD_SCHEMA = "qlkg-document-record-v1"
LEGACY_EMBEDDING_BUNDLE_SCHEMA = "qlkg-embedding-bundle-v1"
LEGACY_EMBEDDING_RECORD_SCHEMA = "qlkg-embedding-record-v1"
EMBEDDING_BUNDLE_SCHEMA = "qlkg-embedding-bundle-v2"
EMBEDDING_RECORD_SCHEMA = "qlkg-embedding-record-v2"
STORE_MANIFEST_PATH = Path("knowledge/store.json")
DOCUMENTS_PATH = Path("knowledge/documents.jsonl")
EMBEDDING_MANIFEST_PATH = Path("knowledge/embeddings/manifest.json")
EMBEDDING_RECORDS_NAME = "records.jsonl"
EMBEDDING_OBJECTS_NAME = "objects"


class StoreError(ValueError):
    """Raised when a portable store violates its deterministic contract."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


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
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _safe_relative(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise StoreError(f"unsafe store path: {value}")
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
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary_name)
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


def _validate_vector(payload: bytes, dimensions: int) -> None:
    if dimensions < 1 or len(payload) != dimensions * 4:
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
            rows = connection.execute(
                f"""
                SELECT namespace, node_id, provider, model, dimensions,
                       {input_schema_column}, content_sha256,
                       {config_digest_column}, vector
                FROM embeddings
                WHERE namespace = ?
                ORDER BY namespace, node_id, provider, model,
                         provider_config_sha256
                """,
                (str(snapshot["namespace"]),),
            ).fetchall()
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
        if not destination.is_file() or _sha256_bytes(destination.read_bytes()) != vector_sha256:
            _atomic_write_bytes(destination, payload)
        object_paths.add(relative_object.as_posix())
        if vector_sha256 not in object_hashes:
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


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StoreError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise StoreError(f"expected JSON object record: {path}")
        records.append(value)
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
    manifest = _read_json(manifest_path)
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
    records_relative = _safe_relative(str(records_meta.get("path", "")))
    records_path = _resolve(manifest_path.parent, records_relative)
    records_text = records_path.read_text(encoding="utf-8")
    if _sha256_text(records_text) != str(records_meta.get("sha256", "")):
        raise StoreError("embedding record digest mismatch")
    records = _read_jsonl(records_path)
    if len(records) != int(records_meta.get("count", -1)):
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
        payload = object_path.read_bytes()
        if _sha256_bytes(payload) != vector_sha256:
            raise StoreError(f"embedding object digest mismatch: {vector_sha256}")
        _validate_vector(payload, int(record.get("dimensions", 0)))
        if vector_sha256 not in object_hashes:
            object_hashes.add(vector_sha256)
            total_object_bytes += len(payload)
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
    if total_object_bytes != int(object_meta.get("bytes", -1)):
        raise StoreError("embedding object byte count mismatch")
    if sha256_json(sorted(object_hashes)) != str(object_meta.get("sha256", "")):
        raise StoreError("embedding object set digest mismatch")
    return manifest, verified


def _store_manifest(root: Path) -> dict[str, Any]:
    path = _resolve(root, STORE_MANIFEST_PATH)
    manifest = _read_json(path)
    _validate_schema(manifest, "qlkg-store-v1.schema.json", "store manifest")
    if manifest.get("schema") != STORE_SCHEMA:
        raise StoreError("unsupported portable store schema")
    claimed = str(manifest.get("store_sha256", ""))
    payload = dict(manifest)
    payload.pop("store_sha256", None)
    if sha256_json(payload) != claimed:
        raise StoreError("portable store manifest digest mismatch")
    return manifest


def _validated_store(
    root: Path,
) -> tuple[
    dict[str, Any],
    GraphState,
    dict[str, Any],
    dict[str, Any],
    list[tuple[dict[str, Any], bytes]],
]:
    root = root.resolve()
    manifest = _store_manifest(root)
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

    state = load_state(graph_dir)
    snapshot = make_agent_snapshot(state)
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

    documents_text = documents_path.read_text(encoding="utf-8")
    documents_meta = manifest.get("documents") or {}
    if _sha256_text(documents_text) != str(documents_meta.get("sha256", "")):
        raise StoreError("portable store document inventory digest mismatch")
    documents = _read_jsonl(documents_path)
    if len(documents) != int(documents_meta.get("count", -1)):
        raise StoreError("portable store document inventory count mismatch")
    source_hashes: dict[str, str] = {}
    for record in documents:
        _validate_schema(
            record,
            "qlkg-document-record-v1.schema.json",
            "document record",
        )
        if record.get("schema") != DOCUMENT_RECORD_SCHEMA:
            raise StoreError("unsupported document record schema")
        authority = str(record.get("authority", ""))
        digest = str(record.get("source_sha256", ""))
        if not authority or authority in source_hashes:
            raise StoreError(f"duplicate or empty authority record: {authority!r}")
        source_path = _resolve(root, authority)
        if sha256_authority_file(source_path) != digest:
            raise StoreError(f"authority snapshot digest mismatch: {authority}")
        source_hashes[authority] = digest
    expected_source_hashes = dict(state.manifest.get("source_hashes") or {})
    if source_hashes != expected_source_hashes:
        raise StoreError("document inventory does not match graph source hashes")
    source_snapshot_sha256 = sha256_json(documents)
    if source_snapshot_sha256 != str(
        documents_meta.get("source_snapshot_sha256", "")
    ):
        raise StoreError("portable store source snapshot digest mismatch")

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
    if sha256_json(knowledge_payload) != str(
        manifest.get("knowledge_generation_sha256", "")
    ):
        raise StoreError("portable store knowledge generation mismatch")
    store_generation = sha256_json(
        {
            "knowledge_generation_sha256": manifest["knowledge_generation_sha256"],
            "embedding_generation_sha256": manifest["embedding_generation_sha256"],
        }
    )
    if store_generation != str(manifest.get("store_generation_sha256", "")):
        raise StoreError("portable store generation mismatch")
    return manifest, state, snapshot, alignment_set, embedding_records


def verify_store(root: Path) -> dict[str, Any]:
    manifest, _, snapshot, _, embedding_records = _validated_store(root)
    return {
        "schema": STORE_SCHEMA,
        "store_generation_sha256": manifest["store_generation_sha256"],
        "knowledge_generation_sha256": manifest["knowledge_generation_sha256"],
        "embedding_generation_sha256": manifest["embedding_generation_sha256"],
        "graph_sha256": manifest["graph_sha256"],
        "documents": int((manifest.get("documents") or {}).get("count", 0)),
        "embeddings": len(embedding_records),
        "counts": snapshot["graph"]["counts"],
    }


def _remove_stale_managed(
    root: Path,
    old_manifest: dict[str, Any],
    current: set[str],
    allowed: set[str],
    authority_hashes: dict[str, str],
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
            if expected is not None and sha256_authority_file(path) != expected:
                raise StoreError(
                    f"refusing to remove locally modified stale authority: {relative}"
                )
            path.unlink()


def snapshot_store(
    repo_root: Path,
    output_root: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path,
    alignments: Path,
    database: Path,
) -> dict[str, Any]:
    """Create or refresh one self-contained portable store generation."""
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
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
    output_root.mkdir(parents=True, exist_ok=True)

    old_manifest: dict[str, Any] = {}
    old_cleanup_allowed: set[str] = set()
    old_authority_hashes: dict[str, str] = {}
    old_path = output_root / STORE_MANIFEST_PATH
    if old_path.is_file():
        old_manifest = _store_manifest(output_root)
        old_paths = old_manifest.get("paths") or {}
        old_documents_path = _resolve(
            output_root, str(old_paths.get("documents", ""))
        )
        old_documents_text = old_documents_path.read_text(encoding="utf-8")
        if _sha256_text(old_documents_text) != str(
            (old_manifest.get("documents") or {}).get("sha256", "")
        ):
            raise StoreError("existing document inventory digest mismatch")
        for record in _read_jsonl(old_documents_path):
            authority = str(record.get("authority", ""))
            digest = str(record.get("source_sha256", ""))
            if authority and digest:
                old_authority_hashes[authority] = digest
        generated = {
            DOCUMENTS_PATH.as_posix(),
            EMBEDDING_MANIFEST_PATH.as_posix(),
            (EMBEDDING_MANIFEST_PATH.parent / EMBEDDING_RECORDS_NAME).as_posix(),
        }
        old_cleanup_allowed.update(generated)
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
                for key in ("registry", "identities", "alignments")
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
                output_root / EMBEDDING_MANIFEST_PATH.parent / EMBEDDING_RECORDS_NAME,
            )
            if path.exists()
        ]
        if conflicts:
            raise StoreError(
                "refusing to overwrite portable artifacts without a valid store manifest: "
                + ", ".join(str(path) for path in conflicts)
            )

    state = load_state(graph_dir)
    snapshot = make_agent_snapshot(state)
    if agent_index_exists(database):
        status = index_status(database)
        if status.get("graph_sha256") != snapshot["graph"]["sha256"]:
            raise StoreError("local Agent index is stale for the graph generation")
    ensure_knowledge_gitignore(output_root / "knowledge/.gitignore")
    source_hashes = dict(state.manifest.get("source_hashes") or {})
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
    for authority, digest in sorted(source_hashes.items()):
        source_path = (repo_root / authority).resolve()
        if not source_path.is_file() or sha256_authority_file(source_path) != digest:
            raise StoreError(f"graph source hash is stale: {authority}")
        spec = unique_source_for_path(specs, source_path)
        source_paths.append(source_path)
        documents.append(
            {
                "schema": DOCUMENT_RECORD_SCHEMA,
                "source_id": spec.id,
                "subject": spec.subject,
                "course": spec.course,
                "knowledge_origin": spec.knowledge_origin,
                "authority": authority,
                "format": source_format(source_path),
                "source_sha256": digest,
                "definition_ids": sorted(definition_ids.get(authority, [])),
                "reference_count": reference_counts.get(authority, 0),
            }
        )
    documents_text = _jsonl(documents)
    source_snapshot_sha256 = sha256_json(documents)

    config_paths = [registry]
    if identities.is_file():
        config_paths.append(identities)
    if alignments.is_file():
        config_paths.append(alignments)
    graph_paths = _graph_paths(graph_dir, state)
    paths_to_copy = [*source_paths, *config_paths, *graph_paths]
    copied_managed: set[str] = set()
    if output_root != repo_root:
        for source in paths_to_copy:
            relative = relative_path(repo_root, source)
            destination = _resolve(output_root, relative)
            if destination.is_file() and not old_manifest:
                if source.read_bytes() != destination.read_bytes():
                    raise StoreError(f"refusing to overwrite unrelated output file: {relative}")
            _copy_file(source, destination)
            copied_managed.add(relative)

    _atomic_write_text(_resolve(output_root, DOCUMENTS_PATH), documents_text)
    embedding_manifest, embedding_managed = _export_embeddings(
        database, output_root, snapshot
    )
    managed_paths = copied_managed | embedding_managed | {DOCUMENTS_PATH.as_posix()}

    registry_relative = relative_path(repo_root, registry)
    graph_relative = relative_path(repo_root, graph_dir)
    identities_relative = relative_path(repo_root, identities) if identities.is_file() else None
    alignments_relative = relative_path(repo_root, alignments) if alignments.is_file() else None
    output_registry = _resolve(output_root, registry_relative)
    output_identities = (
        _resolve(output_root, identities_relative) if identities_relative else None
    )
    output_alignments = (
        _resolve(output_root, alignments_relative) if alignments_relative else None
    )
    alignment_set = load_alignment_set(output_alignments)
    registry_sha256 = sha256_file(output_registry)
    identity_sha256 = identity_registry_sha256(output_identities)
    alignment_sha256 = alignment_sha256_json(alignment_set)
    knowledge_payload = {
        "registry_sha256": registry_sha256,
        "source_snapshot_sha256": source_snapshot_sha256,
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
        },
        "documents": {
            "count": len(documents),
            "sha256": _sha256_text(documents_text),
            "source_snapshot_sha256": source_snapshot_sha256,
        },
        "registry_sha256": registry_sha256,
        "identity_sha256": identity_sha256,
        "alignment_sha256": alignment_sha256,
        "graph_sha256": snapshot["graph"]["sha256"],
        "embedding_manifest_sha256": sha256_file(
            _resolve(output_root, EMBEDDING_MANIFEST_PATH)
        ),
        "knowledge_generation_sha256": knowledge_generation_sha256,
        "embedding_generation_sha256": embedding_manifest[
            "embedding_generation_sha256"
        ],
        "store_generation_sha256": store_generation_sha256,
        "managed_paths": sorted(managed_paths),
    }
    manifest["store_sha256"] = sha256_json(manifest)
    _remove_stale_managed(
        output_root,
        old_manifest,
        managed_paths,
        old_cleanup_allowed,
        old_authority_hashes,
    )
    _atomic_write_text(_resolve(output_root, STORE_MANIFEST_PATH), _pretty_json(manifest))
    report = verify_store(output_root)
    report["root"] = str(output_root)
    report["mode"] = "in-place" if output_root == repo_root else "snapshot-copy"
    return report


def _import_embeddings(
    database: Path,
    records: list[tuple[dict[str, Any], bytes]],
    *,
    store_generation_sha256: str,
    embedding_generation_sha256: str,
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
        provider_configs = sorted(
            {str(record["provider_config_sha256"]) for record, _ in records}
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('provider_config_sha256', ?)",
            (_canonical_json(sha256_json(provider_configs) if provider_configs else None),),
        )
        for key, value in (
            ("store_generation_sha256", store_generation_sha256),
            ("embedding_generation_sha256", embedding_generation_sha256),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
                (key, _canonical_json(value)),
            )
        connection.commit()


def materialize_store(root: Path, database: Path) -> dict[str, Any]:
    """Build the local disposable index from a verified portable store."""
    manifest, _, snapshot, alignment_set, embedding_records = _validated_store(root)
    if agent_index_exists(database):
        try:
            status = index_status(database)
        except (OSError, sqlite3.Error, ValueError):
            status = {}
        if status.get("store_generation_sha256") == manifest["store_generation_sha256"]:
            return {
                "schema": STORE_SCHEMA,
                "materialized": False,
                "database": str(database),
                "store_generation_sha256": manifest["store_generation_sha256"],
                "embeddings": len(embedding_records),
                "counts": snapshot["graph"]["counts"],
            }
    _filesystem_path(database.parent).mkdir(parents=True, exist_ok=True)
    stage_root = Path(
        tempfile.mkdtemp(
            prefix=f".{database.name}.materialize-",
            dir=_filesystem_path(database.parent),
        )
    )
    staged_database = stage_root / "complete.sqlite"
    try:
        # Graph rows and exact vectors are assembled privately. The logical
        # database receives one marker only after the complete SQLite file is
        # durable, so observers can see strictly the old or the new generation.
        write_agent_index(staged_database, snapshot, alignment_set)
        _import_embeddings(
            staged_database,
            embedding_records,
            store_generation_sha256=manifest["store_generation_sha256"],
            embedding_generation_sha256=manifest["embedding_generation_sha256"],
        )
        publish_agent_index_file(
            resolve_agent_index_path(staged_database), database
        )
    finally:
        shutil.rmtree(_filesystem_path(stage_root), ignore_errors=True)
    return {
        "schema": STORE_SCHEMA,
        "materialized": True,
        "database": str(database),
        "store_generation_sha256": manifest["store_generation_sha256"],
        "embeddings": len(embedding_records),
        "counts": snapshot["graph"]["counts"],
    }
