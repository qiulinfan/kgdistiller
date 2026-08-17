"""Git-friendly portable snapshots for kgdistiller projects."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .alignment import load_alignment_set, sha256_json as alignment_sha256_json
from .cli import (
    GraphState,
    KnowledgeError,
    expand_source,
    identity_registry_sha256,
    load_sources,
    load_state,
    make_agent_snapshot,
    relative_path,
    sha256_authority_file,
    source_registry_sha256,
    source_format,
    unique_source_for_path,
)
from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .json_schema import validate_json_schema
from .project import ensure_knowledge_gitignore
from .vault_registry import (
    ensure_vault_manifest,
    load_vault_manifest,
    vault_manifest_path,
)
from .entry_markdown import ENTRY_INDEX_SCHEMA


STORE_SCHEMA = "kgdistiller-store-v1"
STORE_REPORT_SCHEMA = "kgdistiller-store-report-v1"
STORE_MANIFEST_PATH = Path("knowledge/store.json")
DOCUMENTS_PATH = Path("knowledge/documents.jsonl")
DOCUMENT_RECORD_SCHEMA = "kgdistiller-document-record-v1"


class StoreError(ValueError):
    """Raised when a portable snapshot is stale, unsafe, or malformed."""


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _jsonl(values: list[dict[str, Any]]) -> str:
    return "".join(canonical_json(value) + "\n" for value in values)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _portable_text_metrics(path: Path) -> tuple[int, str]:
    """Return LF-normalized UTF-8 size and digest for a Git text artifact."""
    try:
        with path.open("r", encoding="utf-8", newline=None) as handle:
            normalized = handle.read()
    except (OSError, UnicodeError) as error:
        raise StoreError(f"invalid portable text artifact: {path}") from error
    encoded = normalized.encode("utf-8")
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
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


def _safe_relative(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise StoreError(f"unsafe portable path: {value}")
    return path


def _resolve(root: Path, value: str | Path) -> Path:
    path = (root / _safe_relative(value)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise StoreError(f"portable path escapes the store: {value}") from error
    return path


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        os.close(descriptor)
        shutil.copyfile(source, temporary_name)
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StoreError(f"invalid JSON file: {path}") from error
    if not isinstance(value, dict):
        raise StoreError(f"expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        text = path.read_text(encoding="utf-8")
        values = [json.loads(line) for line in text.splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StoreError(f"invalid JSONL file: {path}") from error
    if any(not isinstance(value, dict) for value in values):
        raise StoreError(f"expected JSON objects in {path}")
    canonical = _jsonl(values)
    if text.replace("\r\n", "\n").replace("\r", "\n") != canonical:
        raise StoreError(f"non-canonical document inventory: {path}")
    return canonical, values


def _graph_paths(graph_dir: Path, state: GraphState) -> list[Path]:
    paths = [
        graph_dir / "manifest.json",
        graph_dir / "nodes.jsonl",
        graph_dir / "edges.jsonl",
        graph_dir / "references.jsonl",
        graph_dir / "diagnostics.json",
    ]
    for shard in (state.manifest.get("entry_store") or {}).get("shards", []):
        relative = _safe_relative(str(shard.get("path", "")))
        paths.append(graph_dir / relative)
    if any(not path.is_file() for path in paths):
        raise StoreError("graph generation is incomplete")
    return paths


def _entry_authority_paths(repo_root: Path, state: GraphState) -> list[Path]:
    """Return verified entry MD authorities and their Markdown evidence."""

    inventory = state.manifest.get("entry_authorities") or {}
    if not isinstance(inventory, dict) or inventory.get("schema") != ENTRY_INDEX_SCHEMA:
        raise StoreError("graph generation has no supported entry authority inventory")
    raw_entries = inventory.get("entries")
    if not isinstance(raw_entries, list):
        raise StoreError("graph entry authority inventory is invalid")
    paths: dict[str, Path] = {}
    for record in raw_entries:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise StoreError("graph entry authority record is invalid")
        relative = _safe_relative(str(record["path"]))
        path = _resolve(repo_root, relative)
        if path.is_symlink() or not path.is_file():
            raise StoreError(f"entry authority is not an ordinary file: {relative}")
        _, digest = _portable_text_metrics(path)
        if digest != record["sha256"]:
            raise StoreError(
                f"entry authority is out of sync with the graph: {relative}; "
                "run kgdistiller sync"
            )
        paths[relative.as_posix()] = path
    source_inventory = state.manifest.get("entry_sources") or {}
    if (
        not isinstance(source_inventory, dict)
        or source_inventory.get("schema") != "kgdistiller-entry-source-index-v1"
        or not isinstance(source_inventory.get("entries"), list)
    ):
        raise StoreError("graph generation has no supported entry source inventory")
    for record in source_inventory["entries"]:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise StoreError("graph entry source record is invalid")
        relative = _safe_relative(str(record["path"]))
        path = _resolve(repo_root, relative)
        if path.is_symlink() or not path.is_file():
            raise StoreError(f"entry source is not an ordinary file: {relative}")
        _, digest = _portable_text_metrics(path)
        if digest != record["sha256"]:
            raise StoreError(
                f"entry source is out of sync with the graph: {relative}; "
                "run kgdistiller sync"
            )
        paths[relative.as_posix()] = path
    return [paths[key] for key in sorted(paths)]


def _graph_artifact_records(
    repo_root: Path, target_root: Path, paths: list[Path]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in paths:
        relative = relative_path(repo_root, source)
        target = _resolve(target_root, relative)
        if target.is_symlink() or not target.is_file():
            raise StoreError(f"graph artifact is not an ordinary file: {relative}")
        normalized_bytes, digest = _portable_text_metrics(target)
        records.append(
            {
                "path": relative,
                "bytes": normalized_bytes,
                "sha256": digest,
            }
        )
    return sorted(records, key=lambda item: str(item["path"]))


def _non_directory_entries(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }


def _is_git_metadata(relative: str) -> bool:
    return bool(Path(relative).parts) and Path(relative).parts[0] == ".git"


def _document_inventory(
    repo_root: Path,
    registry: Path,
    state: GraphState,
) -> tuple[list[dict[str, Any]], list[Path]]:
    specs = load_sources(repo_root, registry)
    current_hashes: dict[str, str] = {}
    for spec in specs:
        for source in expand_source(spec):
            authority = relative_path(repo_root, source)
            unique_source_for_path(specs, source)
            digest = sha256_authority_file(source)
            if authority in current_hashes:
                raise StoreError(f"duplicate registered authority: {authority}")
            current_hashes[authority] = digest
    graph_hashes = {
        str(authority): str(digest)
        for authority, digest in (state.manifest.get("source_hashes") or {}).items()
    }
    if current_hashes != graph_hashes:
        graph_paths = set(graph_hashes)
        current_paths = set(current_hashes)
        modified = sorted(
            authority
            for authority in graph_paths & current_paths
            if graph_hashes[authority] != current_hashes[authority]
        )
        raise StoreError(
            "registered authorities are out of sync with the graph: "
            f"added={sorted(current_paths - graph_paths)}, "
            f"deleted={sorted(graph_paths - current_paths)}, modified={modified}; "
            "run kgdistiller sync"
        )
    definitions: dict[str, list[str]] = {}
    for node in state.nodes.values():
        provenance = node.get("provenance") or {}
        authority = str(provenance.get("authority", ""))
        if node.get("type") == "knowledge" and provenance.get("active") and authority:
            definitions.setdefault(authority, []).append(str(node["id"]))
    references: dict[str, int] = {}
    for reference in state.references:
        authority = str(reference.get("authority", ""))
        references[authority] = references.get(authority, 0) + 1

    records: list[dict[str, Any]] = []
    paths: list[Path] = []
    for authority, digest in sorted(graph_hashes.items()):
        source = (repo_root / str(authority)).resolve()
        if not source.is_file() or sha256_authority_file(source) != str(digest):
            raise StoreError(f"graph source hash is stale: {authority}")
        spec = unique_source_for_path(specs, source)
        paths.append(source)
        records.append(
            {
                "schema": DOCUMENT_RECORD_SCHEMA,
                "source_id": spec.id,
                "subject": spec.subject,
                "course": spec.course,
                "knowledge_origin": spec.knowledge_origin,
                "authority": str(authority),
                "format": source_format(source),
                "source_sha256": str(digest),
                "definition_ids": sorted(definitions.get(str(authority), [])),
                "reference_count": references.get(str(authority), 0),
            }
        )
    return records, paths


def _validate_manifest_schema(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != STORE_SCHEMA:
        raise StoreError(
            f"unsupported-store-schema: expected {STORE_SCHEMA}, got {manifest.get('schema')!r}"
        )
    schema_path = Path(__file__).with_name("schemas") / "kgdistiller-store-v1.schema.json"
    schema = _read_json(schema_path)
    errors = validate_json_schema(manifest, schema)
    if errors:
        raise StoreError("invalid store manifest: " + errors[0].message)


def _require_graph_generation_bindings(
    state: GraphState,
    registry: Path,
    identities: Path | None,
) -> tuple[str, str | None]:
    """Require live registries to be the exact generation recorded by the graph."""

    try:
        registry_sha = source_registry_sha256(registry)
        identity_sha = identity_registry_sha256(identities)
    except (OSError, UnicodeError, ValueError) as error:
        raise StoreError(f"cannot hash the current registries: {error}") from error
    if state.manifest.get("registry_sha256") != registry_sha:
        raise StoreError(
            "source registry is out of sync with the graph; run kgdistiller sync"
        )
    if state.manifest.get("identity_sha256") != identity_sha:
        raise StoreError(
            "identity registry is out of sync with the graph; run kgdistiller sync"
        )
    return registry_sha, identity_sha


def verify_store(root: Path) -> dict[str, Any]:
    """Verify a self-contained portable authority and graph generation."""
    root = root.resolve()
    lexical_manifest = root / STORE_MANIFEST_PATH
    if lexical_manifest.is_symlink() or not lexical_manifest.is_file():
        raise StoreError("store manifest must be an ordinary file")
    manifest_path = _resolve(root, STORE_MANIFEST_PATH)
    manifest = _read_json(manifest_path)
    _validate_manifest_schema(manifest)
    claimed_store_sha = str(manifest.get("store_sha256", ""))
    digest_payload = dict(manifest)
    digest_payload.pop("store_sha256", None)
    if sha256_json(digest_payload) != claimed_store_sha:
        raise StoreError("store manifest digest mismatch")

    paths = manifest["paths"]
    if str(paths["vault"]) != vault_manifest_path(root).relative_to(root).as_posix():
        raise StoreError("portable vault identity path is not canonical")
    vault_path = _resolve(root, str(paths["vault"]))
    registry = _resolve(root, str(paths["registry"]))
    graph_dir = _resolve(root, str(paths["graph"]))
    documents_path = _resolve(root, str(paths["documents"]))
    identities = _resolve(root, str(paths["identities"])) if paths.get("identities") else None
    alignments = _resolve(root, str(paths["alignments"])) if paths.get("alignments") else None
    try:
        vault_manifest = load_vault_manifest(root)
    except (OSError, UnicodeError, ValueError) as error:
        raise StoreError(f"portable vault identity is invalid: {error}") from error
    if vault_path != vault_manifest_path(root).resolve():
        raise StoreError("portable vault identity path is not canonical")
    if vault_manifest["vault_id"] != manifest["vault_id"]:
        raise StoreError("portable vault identity does not match the store")
    if sha256_json(vault_manifest) != manifest["vault_sha256"]:
        raise StoreError("portable vault identity digest mismatch")
    try:
        load_sources(root, registry)
    except (KnowledgeError, OSError, UnicodeError, ValueError) as error:
        raise StoreError(f"portable source registry is invalid: {error}") from error
    documents_text, documents = _read_jsonl(documents_path)
    for index, record in enumerate(documents):
        try:
            validate_contract(record)
        except ContractError as error:
            raise StoreError(f"invalid document inventory record {index}: {error}") from error
    if len(documents) != int(manifest["documents"]["count"]):
        raise StoreError("document inventory count mismatch")
    if _sha256_text(documents_text) != str(manifest["documents"]["sha256"]):
        raise StoreError("document inventory digest mismatch")
    if sha256_json(documents) != str(manifest["documents"]["source_snapshot_sha256"]):
        raise StoreError("document inventory generation mismatch")

    declared_graph_artifacts: dict[str, dict[str, Any]] = {}
    for artifact in manifest["graph_artifacts"]:
        relative = str(artifact["path"])
        if relative in declared_graph_artifacts:
            raise StoreError(f"duplicate graph artifact path: {relative}")
        path = _resolve(root, relative)
        try:
            path.relative_to(graph_dir)
        except ValueError as error:
            raise StoreError(f"graph artifact lies outside graph root: {relative}") from error
        if path.is_symlink() or not path.is_file():
            raise StoreError(f"graph artifact is not an ordinary file: {relative}")
        normalized_bytes, digest = _portable_text_metrics(path)
        if normalized_bytes != int(artifact["bytes"]) or digest != artifact["sha256"]:
            raise StoreError(f"graph artifact digest mismatch: {relative}")
        declared_graph_artifacts[relative] = artifact
    actual_graph_artifacts = {
        path.relative_to(root).as_posix()
        for path in graph_dir.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }
    if actual_graph_artifacts != set(declared_graph_artifacts):
        raise StoreError(
            "graph artifact inventory mismatch: "
            f"extra={sorted(actual_graph_artifacts - set(declared_graph_artifacts))}, "
            f"missing={sorted(set(declared_graph_artifacts) - actual_graph_artifacts)}"
        )

    state = load_state(graph_dir)
    _entry_authority_paths(root, state)
    snapshot = make_agent_snapshot(state)
    if snapshot["graph"]["sha256"] != manifest["graph_sha256"]:
        raise StoreError("portable graph digest mismatch")
    registry_sha, identity_sha = _require_graph_generation_bindings(
        state, registry, identities
    )
    if registry_sha != manifest["registry_sha256"]:
        raise StoreError("registry digest mismatch")
    if identity_sha != manifest["identity_sha256"]:
        raise StoreError("identity registry digest mismatch")
    alignment_sha = alignment_sha256_json(load_alignment_set(alignments))
    if alignment_sha != manifest["alignment_sha256"]:
        raise StoreError("alignment registry digest mismatch")

    source_hashes = dict(state.manifest.get("source_hashes") or {})
    inventory_hashes: dict[str, str] = {}
    for record in documents:
        authority = str(record.get("authority", ""))
        digest = str(record.get("source_sha256", ""))
        if not authority or authority in inventory_hashes:
            raise StoreError("document inventory has an empty or duplicate authority")
        source = _resolve(root, authority)
        if not source.is_file() or sha256_authority_file(source) != digest:
            raise StoreError(f"portable authority digest mismatch: {authority}")
        inventory_hashes[authority] = digest
    if inventory_hashes != source_hashes:
        raise StoreError("document inventory does not match graph source hashes")
    expected_documents, _ = _document_inventory(root, registry, state)
    if documents != expected_documents:
        raise StoreError("document inventory does not match registry and graph semantics")

    generation = sha256_json(
        {
            "vault_id": manifest["vault_id"],
            "vault_sha256": manifest["vault_sha256"],
            "registry_sha256": manifest["registry_sha256"],
            "identity_sha256": manifest["identity_sha256"],
            "alignment_sha256": manifest["alignment_sha256"],
            "graph_sha256": manifest["graph_sha256"],
            "source_snapshot_sha256": manifest["documents"]["source_snapshot_sha256"],
        }
    )
    if generation != manifest["store_generation_sha256"]:
        raise StoreError("store generation digest mismatch")
    if manifest["layout"] == "snapshot-copy":
        declared = set(manifest["managed_paths"])
        actual = {
            relative
            for relative in _non_directory_entries(root)
            if not _is_git_metadata(relative)
        }
        if actual != declared:
            raise StoreError(
                "snapshot-copy managed-file mismatch: "
                f"extra={sorted(actual - declared)}, missing={sorted(declared - actual)}"
            )
    report = {
        "schema": STORE_REPORT_SCHEMA,
        "status": "verified",
        "artifact_schema": STORE_SCHEMA,
        "root": str(root),
        "store_sha256": claimed_store_sha,
        "store_generation_sha256": generation,
        "graph_sha256": manifest["graph_sha256"],
        "documents": len(documents),
        "counts": snapshot["graph"]["counts"],
        "query_backend": "json-memory",
        "layout": manifest["layout"],
    }
    validate_contract(report)
    return report


def _verify_replaceable_snapshot(root: Path) -> dict[str, Any]:
    report = verify_store(root)
    manifest = _read_json(root / STORE_MANIFEST_PATH)
    if manifest.get("layout") != "snapshot-copy":
        raise StoreError(
            "portable replacement target must be a verified snapshot-copy; "
            "refusing to replace an in-place project"
        )
    declared = set(manifest["managed_paths"])
    actual = _non_directory_entries(root)
    if actual != declared:
        raise StoreError(
            "portable replacement target contains repository metadata or unmanaged "
            f"entries: extra={sorted(actual - declared)}, missing={sorted(declared - actual)}"
        )
    return report


def _install_external(stage: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if any(output.iterdir()):
            _verify_replaceable_snapshot(output)
        else:
            output.rmdir()
    backup = output.parent / f".{output.name}.previous"
    if backup.exists():
        raise StoreError(f"portable snapshot recovery path already exists: {backup}")
    if output.exists():
        os.replace(output, backup)
    try:
        os.replace(stage, output)
    except BaseException:
        if backup.exists() and not output.exists():
            os.replace(backup, output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def snapshot_store(
    repo_root: Path,
    output_root: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path,
    alignments: Path,
) -> dict[str, Any]:
    """Create or refresh one self-contained portable generation."""
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    if output_root != repo_root:
        try:
            output_root.relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise StoreError("portable output cannot be nested inside its source project")
        try:
            repo_root.relative_to(output_root)
        except ValueError:
            pass
        else:
            raise StoreError("portable output cannot contain its source project")

    existing_manifest = output_root / STORE_MANIFEST_PATH
    if existing_manifest.is_file():
        existing = _read_json(existing_manifest)
        if existing.get("schema") != STORE_SCHEMA:
            raise StoreError(
                "unsupported-store-schema: refusing to rewrite an older store generation"
            )
        if output_root != repo_root and existing.get("layout") != "snapshot-copy":
            raise StoreError(
                "portable replacement target must be a snapshot-copy; "
                "refusing to replace an in-place project"
            )

    vault_manifest = ensure_vault_manifest(repo_root)
    vault_path = vault_manifest_path(repo_root)
    vault_sha = sha256_json(vault_manifest)
    state = load_state(graph_dir)
    registry_sha, identity_sha = _require_graph_generation_bindings(
        state, registry, identities
    )
    snapshot = make_agent_snapshot(state)
    documents, source_paths = _document_inventory(repo_root, registry, state)
    source_paths.extend(_entry_authority_paths(repo_root, state))
    documents_text = _jsonl(documents)
    source_snapshot_sha = sha256_json(documents)
    graph_paths = _graph_paths(graph_dir, state)
    config_paths = [vault_path, registry]
    if identities.is_file():
        config_paths.append(identities)
    if alignments.is_file():
        config_paths.append(alignments)

    target_root = output_root
    stage_root: Path | None = None
    if output_root != repo_root:
        output_root.parent.mkdir(parents=True, exist_ok=True)
        stage_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.stage-", dir=output_root.parent))
        target_root = stage_root
    target_root.mkdir(parents=True, exist_ok=True)
    layout = "in-place" if output_root == repo_root else "snapshot-copy"
    managed: set[str] = {
        STORE_MANIFEST_PATH.as_posix(),
        DOCUMENTS_PATH.as_posix(),
    }
    try:
        if target_root != repo_root:
            for source in [*source_paths, *config_paths, *graph_paths]:
                relative = relative_path(repo_root, source)
                _copy_file(source, _resolve(target_root, relative))
                managed.add(relative)
            for spec in load_sources(repo_root, registry):
                relative_root = relative_path(repo_root, spec.root)
                _resolve(target_root, relative_root).mkdir(parents=True, exist_ok=True)
        ensure_knowledge_gitignore(target_root / "knowledge/.gitignore")
        if layout == "snapshot-copy":
            managed.add("knowledge/.gitignore")
        _atomic_write_text(_resolve(target_root, DOCUMENTS_PATH), documents_text)

        vault_relative = relative_path(repo_root, vault_path)
        registry_relative = relative_path(repo_root, registry)
        graph_relative = relative_path(repo_root, graph_dir)
        identities_relative = relative_path(repo_root, identities) if identities.is_file() else None
        alignments_relative = relative_path(repo_root, alignments) if alignments.is_file() else None
        output_vault = _resolve(target_root, vault_relative)
        output_registry = _resolve(target_root, registry_relative)
        output_identities = _resolve(target_root, identities_relative) if identities_relative else None
        output_alignments = _resolve(target_root, alignments_relative) if alignments_relative else None
        graph_artifacts = _graph_artifact_records(repo_root, target_root, graph_paths)
        if output_vault != vault_manifest_path(target_root).resolve():
            raise StoreError("portable vault identity path is not canonical")
        try:
            output_vault_manifest = load_vault_manifest(target_root)
        except (OSError, UnicodeError, ValueError) as error:
            raise StoreError(f"portable vault identity is invalid: {error}") from error
        output_vault_sha = sha256_json(output_vault_manifest)
        if (
            output_vault_manifest["vault_id"] != vault_manifest["vault_id"]
            or output_vault_sha != vault_sha
        ):
            raise StoreError("vault identity changed while creating the snapshot")
        output_registry_sha = source_registry_sha256(output_registry)
        output_identity_sha = identity_registry_sha256(output_identities)
        if output_registry_sha != registry_sha or output_identity_sha != identity_sha:
            raise StoreError("registry generation changed while creating the snapshot")
        registry_sha = output_registry_sha
        identity_sha = output_identity_sha
        alignment_sha = alignment_sha256_json(load_alignment_set(output_alignments))
        generation = sha256_json(
            {
                "vault_id": output_vault_manifest["vault_id"],
                "vault_sha256": output_vault_sha,
                "registry_sha256": registry_sha,
                "identity_sha256": identity_sha,
                "alignment_sha256": alignment_sha,
                "graph_sha256": snapshot["graph"]["sha256"],
                "source_snapshot_sha256": source_snapshot_sha,
            }
        )
        manifest: dict[str, Any] = {
            "schema": STORE_SCHEMA,
            "generator": "kgdistiller",
            "layout": layout,
            "paths": {
                "vault": vault_relative,
                "registry": registry_relative,
                "identities": identities_relative,
                "alignments": alignments_relative,
                "graph": graph_relative,
                "documents": DOCUMENTS_PATH.as_posix(),
            },
            "documents": {
                "count": len(documents),
                "sha256": _sha256_text(documents_text),
                "source_snapshot_sha256": source_snapshot_sha,
            },
            "graph_artifacts": graph_artifacts,
            "vault_id": output_vault_manifest["vault_id"],
            "vault_sha256": output_vault_sha,
            "registry_sha256": registry_sha,
            "identity_sha256": identity_sha,
            "alignment_sha256": alignment_sha,
            "graph_sha256": snapshot["graph"]["sha256"],
            "store_generation_sha256": generation,
            "managed_paths": sorted(managed),
        }
        manifest["store_sha256"] = sha256_json(manifest)
        _validate_manifest_schema(manifest)
        _atomic_write_text(_resolve(target_root, STORE_MANIFEST_PATH), _pretty_json(manifest))
        verify_store(target_root)
        if stage_root is not None:
            _install_external(stage_root, output_root)
            stage_root = None
        report = verify_store(output_root)
        return report
    finally:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)
