"""Privacy-filtered, host-consumable static graph exports."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .cli import (
    GRAPH_SCHEMA,
    GraphState,
    KnowledgeError,
    atomic_write,
    identity_registry_sha256,
    load_sources,
    load_state,
    make_agent_snapshot,
    pretty_json,
    sha256_authority_file,
    source_registry_sha256,
    typst_registry_text,
    unique_source_for_path,
    validate_state,
)
from .contracts import finalize_self_digest, sha256_json, validate_contract
from .static_export_verifier import verify_export

EXPORT_SCHEMA = "kgdistiller-static-export-v1"
EXPORT_REPORT_SCHEMA = "kgdistiller-static-export-report-v1"
SITE_GRAPH_SCHEMA = "kgdistiller-site-graph-v1"
PRODUCT_REPOSITORY = "https://github.com/qiulinfan/kgdistiller"
GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SITE_BUNDLE_FILES = {
    "manifest.json",
    "graph.json",
    "knowledge-registry.typ",
    "verify_export.py",
}
EXPORT_RECOVERY_DIRECTORY = ".kgd-export-recovery"
SOURCE_GRAPH_FILES = {
    "manifest.json",
    "nodes.jsonl",
    "edges.jsonl",
    "references.jsonl",
}


class StaticExportError(ValueError):
    """Raised when a static export cannot be produced safely."""


def _require_graph_generation_bindings(
    state: GraphState,
    registry: Path,
    identities: Path | None,
) -> None:
    """Reject registry inputs from a generation other than the loaded graph."""

    try:
        registry_sha = source_registry_sha256(registry)
        identity_sha = identity_registry_sha256(identities)
    except (OSError, UnicodeError, ValueError) as error:
        raise StaticExportError(f"cannot hash the current registries: {error}") from error
    if state.manifest.get("registry_sha256") != registry_sha:
        raise StaticExportError(
            "source registry is out of sync with the authority graph; "
            "run kgdistiller sync"
        )
    if state.manifest.get("identity_sha256") != identity_sha:
        raise StaticExportError(
            "identity registry is out of sync with the authority graph; "
            "run kgdistiller sync"
        )


def _require_same_graph_generation(graph_dir: Path, expected: GraphState) -> GraphState:
    """Reload and validate the graph before committing a generated export."""

    try:
        current = load_state(graph_dir)
        make_agent_snapshot(current)
    except (KnowledgeError, OSError, UnicodeError, ValueError) as error:
        raise StaticExportError(f"cannot reload the authority graph: {error}") from error
    if sha256_json(current.manifest) != sha256_json(expected.manifest):
        raise StaticExportError(
            "authority graph generation changed during static export; retry the export"
        )
    return current


def _strict_json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise StaticExportError(f"non-finite JSON constant in {path}: {value}")

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=reject_constant
        )
    except StaticExportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StaticExportError(f"cannot read source registry: {path}") from error
    if not isinstance(payload, dict):
        raise StaticExportError(f"source registry is not an object: {path}")
    return payload


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_text_bytes(path: Path) -> bytes:
    """Return UTF-8 text bytes with every checkout newline represented as LF."""

    with path.open("r", encoding="utf-8", newline=None) as handle:
        return handle.read().encode("utf-8")


def _safe_repository_url(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StaticExportError(
            f"{label} must be a credential-free HTTPS repository URL"
        )
    return value.rstrip("/")


def _distribution_commit() -> str | None:
    try:
        distribution = metadata.distribution("kgdistiller")
        direct_url = distribution.read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not direct_url:
        return None
    try:
        payload = json.loads(direct_url)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    vcs_info = payload.get("vcs_info")
    if not isinstance(vcs_info, dict):
        return None
    commit = str(vcs_info.get("commit_id") or "").lower()
    return commit if GIT_COMMIT_RE.fullmatch(commit) else None


def _source_checkout_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def _run_git_machine(
    root: Path,
    arguments: list[str],
    error_message: str,
) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=False,
        )
    except OSError as error:
        raise StaticExportError(error_message) from error
    if result.returncode != 0:
        raise StaticExportError(error_message)
    if not isinstance(result.stdout, bytes):
        raise StaticExportError(f"{error_message}: Git machine output is not bytes")
    try:
        return result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StaticExportError(
            f"{error_message}: Git machine output is not valid UTF-8"
        ) from error


def _run_product_git(root: Path, arguments: list[str], label: str) -> str:
    return _run_git_machine(
        root,
        arguments,
        f"cannot establish product source-checkout {label} provenance",
    )


def _source_checkout_commit() -> str | None:
    root = _source_checkout_root()
    if root is None:
        return None
    status = _run_product_git(
        root,
        ["status", "--porcelain", "--untracked-files=all"],
        "cleanliness",
    )
    if status.strip():
        raise StaticExportError(
            "product source checkout is dirty; commit or remove every tracked and untracked "
            "product change before export"
        )
    commit = _run_product_git(root, ["rev-parse", "--verify", "HEAD"], "commit")
    value = commit.strip().lower()
    if GIT_COMMIT_RE.fullmatch(value) is None:
        raise StaticExportError("product source-checkout HEAD is not a full Git commit")
    return value


def resolve_product_commit(explicit: str | None = None) -> str:
    requested: str | None = None
    if explicit is not None:
        requested = explicit.strip().lower()
        if GIT_COMMIT_RE.fullmatch(requested) is None:
            raise StaticExportError(
                "--product-commit must be a full 40- or 64-hex commit"
            )

    checkout_commit = _source_checkout_commit()
    distribution_commit = _distribution_commit()
    if (
        checkout_commit is not None
        and distribution_commit is not None
        and checkout_commit != distribution_commit
    ):
        raise StaticExportError(
            "product source-checkout and installed direct-url commits disagree"
        )
    discovered = checkout_commit or distribution_commit
    if requested is not None:
        if discovered is not None and requested != discovered:
            raise StaticExportError(
                "--product-commit does not match discovered product provenance"
            )
        return requested
    if discovered is None:
        raise StaticExportError(
            "product commit is unavailable; pass --product-commit from the release provenance"
        )
    return discovered


def _run_source_git(root: Path, arguments: list[str], label: str) -> str:
    return _run_git_machine(
        root,
        arguments,
        f"cannot establish source checkout {label}",
    )


def _parse_git_nul_records(value: str, label: str) -> list[str]:
    if not value:
        return []
    if not value.endswith("\0"):
        raise StaticExportError(f"Git {label} machine output is not NUL-terminated")
    records = value[:-1].split("\0")
    if any(not record for record in records):
        raise StaticExportError(f"Git {label} machine output has an empty record")
    return records


def _source_checkout_revision(repo_root: Path, inputs: list[Path]) -> str:
    """Bind every export input to one clean, current instance commit."""

    top_level_text = _run_source_git(
        repo_root,
        ["rev-parse", "--show-toplevel"],
        "repository root",
    )
    top_level_value = top_level_text.strip()
    if not top_level_value:
        raise StaticExportError("cannot establish source checkout repository root")
    git_root = Path(top_level_value).resolve()
    project_root = repo_root.resolve()
    try:
        project_root.relative_to(git_root)
    except ValueError as error:
        raise StaticExportError(
            "knowledge project is outside its source checkout"
        ) from error

    status = _run_source_git(
        git_root,
        ["status", "--porcelain", "--untracked-files=all"],
        "cleanliness",
    )
    if status.strip():
        raise StaticExportError(
            "source repository checkout is dirty; commit or remove every tracked and "
            "untracked instance change before export"
        )
    revision_text = _run_source_git(
        git_root,
        ["rev-parse", "--verify", "HEAD"],
        "revision",
    )
    revision = revision_text.strip().lower()
    if GIT_COMMIT_RE.fullmatch(revision) is None:
        raise StaticExportError("source checkout HEAD is not a full Git commit")

    tracked_text = _run_source_git(
        git_root,
        ["ls-files", "--full-name", "-z"],
        "tracked input inventory",
    )
    tracked = set(_parse_git_nul_records(tracked_text, "tracked input inventory"))
    for path in sorted(set(inputs), key=str):
        if path.is_symlink() or not path.is_file():
            raise StaticExportError(f"export input is missing or symbolic: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(git_root).as_posix()
        except ValueError as error:
            raise StaticExportError(
                f"export input is outside the source checkout: {path}"
            ) from error
        if relative not in tracked:
            raise StaticExportError(
                f"export input is not tracked by source HEAD: {relative}"
            )
    return revision


def _source_inputs(
    repo_root: Path,
    registry: Path,
    graph_dir: Path,
    graph_manifest: dict[str, Any],
    source_hashes: dict[str, Any],
    state: GraphState | None = None,
) -> list[Path]:
    paths = [registry, *(graph_dir / name for name in sorted(SOURCE_GRAPH_FILES))]
    entry_store = graph_manifest.get("entry_store") or {}
    if not isinstance(entry_store, dict):
        raise StaticExportError("authority graph entry_store is invalid")
    raw_shards = entry_store.get("shards") or []
    if not isinstance(raw_shards, list):
        raise StaticExportError("authority graph entry_store is invalid")
    for shard in raw_shards:
        if not isinstance(shard, dict):
            raise StaticExportError("authority graph entry_store contains a non-object")
        relative = Path(str(shard.get("path", "")))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise StaticExportError(
                f"authority graph contains an unsafe entry shard: {relative}"
            )
        paths.append(graph_dir / relative)
    entry_authorities = graph_manifest.get("entry_authorities") or {}
    if not isinstance(entry_authorities, dict):
        raise StaticExportError("authority graph entry_authorities is invalid")
    raw_entries = entry_authorities.get("entries") or []
    if not isinstance(raw_entries, list):
        raise StaticExportError("authority graph entry_authorities is invalid")
    for record in raw_entries:
        if not isinstance(record, dict):
            raise StaticExportError("authority graph entry_authorities contains a non-object")
        relative = Path(str(record.get("path", "")))
        digest = record.get("sha256")
        if (
            not str(relative)
            or relative.is_absolute()
            or ".." in relative.parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise StaticExportError("authority graph contains an invalid entry authority")
        path = repo_root / relative
        try:
            actual = sha256_authority_file(path)
        except (OSError, UnicodeError) as error:
            raise StaticExportError(
                f"cannot read entry authority: {relative.as_posix()}"
            ) from error
        if actual != digest:
            raise StaticExportError(
                f"entry authority does not match the committed graph hash: {relative.as_posix()}"
            )
        paths.append(path)
    entry_sources = graph_manifest.get("entry_sources") or {}
    if not isinstance(entry_sources, dict):
        raise StaticExportError("authority graph entry_sources is invalid")
    raw_entry_sources = entry_sources.get("entries") or []
    if not isinstance(raw_entry_sources, list):
        raise StaticExportError("authority graph entry_sources is invalid")
    for record in raw_entry_sources:
        if not isinstance(record, dict):
            raise StaticExportError("authority graph entry_sources contains a non-object")
        relative = Path(str(record.get("path", "")))
        digest = record.get("sha256")
        if (
            not str(relative)
            or relative.is_absolute()
            or ".." in relative.parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise StaticExportError("authority graph contains an invalid entry source")
        path = repo_root / relative
        try:
            actual = sha256_authority_file(path)
        except (OSError, UnicodeError) as error:
            raise StaticExportError(
                f"cannot read entry source: {relative.as_posix()}"
            ) from error
        if actual != digest:
            raise StaticExportError(
                f"entry source does not match the committed graph hash: {relative.as_posix()}"
            )
        paths.append(path)
    if state is not None:
        for node in state.nodes.values():
            source = str((node.get("properties") or {}).get("entry_source", ""))
            if not source:
                continue
            relative = Path(source)
            if relative.is_absolute() or ".." in relative.parts:
                raise StaticExportError(f"authority graph contains an unsafe entry source: {source}")
            path = repo_root / relative
            if path.is_symlink() or not path.is_file():
                raise StaticExportError(f"entry source is missing or symbolic: {source}")
            paths.append(path)
    for authority, digest in sorted(source_hashes.items()):
        authority_text = str(authority)
        authority_path = Path(authority_text)
        if (
            not isinstance(authority, str)
            or not authority_text
            or not isinstance(digest, str)
            or authority_path.is_absolute()
            or ".." in authority_path.parts
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise StaticExportError(
                f"invalid source hash record for {authority_text!r}"
            )
        source = repo_root / authority_path
        try:
            actual = sha256_authority_file(source)
        except (OSError, UnicodeError) as error:
            raise StaticExportError(
                f"cannot read hashed source authority: {authority_text}"
            ) from error
        if actual != digest:
            raise StaticExportError(
                f"source authority does not match the committed graph hash: {authority_text}"
            )
        paths.append(source)
    return paths


def _product_version() -> str:
    try:
        return metadata.version("kgdistiller")
    except metadata.PackageNotFoundError:
        from . import __version__

        return __version__


def _owner_id(repo_root: Path, specs: list[Any], authority: str) -> str:
    if not authority or Path(authority).is_absolute() or ".." in Path(authority).parts:
        raise StaticExportError(
            f"graph contains an unsafe authority path: {authority!r}"
        )
    try:
        return unique_source_for_path(specs, (repo_root / authority).resolve()).id
    except KnowledgeError as error:
        raise StaticExportError(
            f"graph authority is not uniquely registered: {authority}"
        ) from error


def _published_source_ids(registry_payload: dict[str, Any]) -> tuple[list[str], int]:
    raw_sources = registry_payload.get("sources")
    if not isinstance(raw_sources, list):
        raise StaticExportError("source registry sources must be an array")
    all_ids: list[str] = []
    published: list[str] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            raise StaticExportError("source registry contains a non-object source")
        source_id = str(item.get("id", ""))
        if not source_id or source_id in all_ids:
            raise StaticExportError(
                f"source registry contains an invalid id: {source_id!r}"
            )
        all_ids.append(source_id)
        if item.get("publish") is True:
            published.append(source_id)
    return sorted(published), len(all_ids)


def _taxonomy_visibility_seeds(
    specs: list[Any],
    published_set: set[str],
    state: GraphState,
) -> tuple[set[str], set[str]]:
    expected_public_types: dict[str, str] = {}
    all_explicit: set[str] = set()
    public_explicit: set[str] = set()

    def register_public(node_id: str, expected_type: str) -> None:
        previous = expected_public_types.get(node_id)
        if previous is not None and previous != expected_type:
            raise StaticExportError(
                f"published taxonomy id has conflicting roles: {node_id}"
            )
        expected_public_types[node_id] = expected_type

    for spec in specs:
        field_ids = set(spec.fields)
        topic_ids: set[str] = set()
        for _, topic_id, _, topic_fields in spec.topic_patterns:
            topic_ids.add(topic_id)
            field_ids.update(topic_fields)
        explicit = field_ids | topic_ids
        all_explicit.update(explicit)
        if spec.id not in published_set:
            continue
        public_explicit.update(explicit)
        for field_id in field_ids:
            register_public(field_id, "field")
        for topic_id in topic_ids:
            register_public(topic_id, "topic")

    for node_id, expected_type in sorted(expected_public_types.items()):
        node = state.nodes.get(node_id)
        if not isinstance(node, dict):
            raise StaticExportError(
                f"published taxonomy node is missing from the graph: {node_id}"
            )
        if node.get("type") != expected_type:
            raise StaticExportError(
                f"published taxonomy node {node_id} must have type {expected_type}"
            )
        properties = node.get("properties")
        if (
            not isinstance(properties, dict)
            or properties.get("origin") != "registry-taxonomy"
        ):
            raise StaticExportError(
                f"published taxonomy node is not registry-owned: {node_id}"
            )

    return set(expected_public_types), all_explicit - public_explicit


def _public_diagnostics(
    repo_root: Path,
    specs: list[Any],
    state: GraphState,
    visible: set[str],
    published_set: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return deterministic diagnostics that cannot identify private graph content."""

    private_tokens: set[str] = set()
    for node_id, node in state.nodes.items():
        if node_id in visible:
            continue
        for value in (
            node_id,
            node.get("label"),
            (node.get("provenance") or {}).get("authority"),
            (node.get("provenance") or {}).get("web"),
        ):
            if isinstance(value, str) and len(value) >= 4:
                private_tokens.add(value.casefold())
    raw_hashes = state.manifest.get("source_hashes") or {}
    if isinstance(raw_hashes, dict):
        for authority in raw_hashes:
            authority_text = str(authority)
            try:
                owner_is_public = (
                    _owner_id(repo_root, specs, authority_text) in published_set
                )
            except StaticExportError:
                owner_is_public = False
            if not owner_is_public and len(authority_text) >= 4:
                private_tokens.add(authority_text.casefold())

    raw = validate_state(state)
    result: dict[str, list[dict[str, Any]]] = {
        "errors": [],
        "warnings": [],
        "info": [],
    }
    for severity, collected in result.items():
        values = raw.get(severity, [])
        if not isinstance(values, list):
            raise StaticExportError(
                f"authority diagnostics {severity} must be an array"
            )
        for value in values:
            if not isinstance(value, dict):
                raise StaticExportError(
                    f"authority diagnostics {severity} contains a non-object"
                )
            if set(value) - {"code", "message", "source", "node"}:
                raise StaticExportError(
                    f"authority diagnostics {severity} has unsupported fields"
                )
            code = value.get("code")
            message = value.get("message")
            if (
                not isinstance(code, str)
                or not code
                or not isinstance(message, str)
                or not message
            ):
                raise StaticExportError(
                    f"authority diagnostics {severity} has invalid text"
                )

            node = value.get("node")
            if node is not None and (not isinstance(node, str) or node not in visible):
                continue
            source = value.get("source")
            if source is not None:
                if not isinstance(source, str) or not source:
                    continue
                try:
                    if _owner_id(repo_root, specs, source) not in published_set:
                        continue
                except StaticExportError:
                    continue
            if any(token in message.casefold() for token in private_tokens):
                continue
            collected.append(copy.deepcopy(value))
        collected.sort(
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    return result


def build_site_graph(
    repo_root: Path,
    registry: Path,
    state: GraphState,
) -> tuple[dict[str, Any], GraphState, dict[str, str], list[str], int]:
    # Recompute the private graph from its canonical LF serialization before
    # trusting its digest or publishing a derivative. load_state has already
    # checked manifest-declared entry shard hashes using the same text boundary.
    try:
        make_agent_snapshot(state)
    except KnowledgeError as error:
        raise StaticExportError(
            f"authority graph failed canonical integrity validation: {error}"
        ) from error
    registry_payload = _strict_json(registry)
    specs = load_sources(repo_root, registry)
    published_ids, source_count = _published_source_ids(registry_payload)
    published_set = set(published_ids)
    taxonomy_seeds, private_taxonomy = _taxonomy_visibility_seeds(
        specs,
        published_set,
        state,
    )

    visible = set(taxonomy_seeds)
    for node in state.nodes.values():
        if node.get("type") != "knowledge":
            continue
        provenance = node.get("provenance") or {}
        if provenance.get("active") is not True:
            continue
        authority = str(provenance.get("authority", ""))
        if _owner_id(repo_root, specs, authority) in published_set:
            visible.add(str(node["id"]))

    changed = True
    while changed:
        changed = False
        for edge in state.edges.values():
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            source_node = state.nodes.get(source)
            if (
                edge.get("relation") == "contains"
                and target in visible
                and source not in private_taxonomy
                and source_node is not None
                and source_node.get("type") in {"field", "topic"}
                and source not in visible
            ):
                properties = source_node.get("properties")
                if (
                    not isinstance(properties, dict)
                    or properties.get("origin") != "registry-taxonomy"
                ):
                    raise StaticExportError(
                        f"visible taxonomy ancestor is not registry-owned: {source}"
                    )
                visible.add(source)
                changed = True

    nodes: list[dict[str, Any]] = []
    for node_id in sorted(visible):
        node = copy.deepcopy(state.nodes[node_id])
        properties = dict(node.get("properties") or {})
        properties.pop("entry_path", None)
        node["properties"] = properties
        nodes.append(node)
    edges = sorted(
        (
            {
                "source": str(edge["source"]),
                "relation": str(edge["relation"]),
                "target": str(edge["target"]),
            }
            for edge in state.edges.values()
            if edge.get("source") in visible and edge.get("target") in visible
        ),
        key=lambda item: (
            str(item.get("source", "")),
            str(item.get("relation", "")),
            str(item.get("target", "")),
        ),
    )
    references: list[dict[str, Any]] = []
    for reference in state.references:
        if reference.get("target") not in visible:
            continue
        authority = str(reference.get("authority", ""))
        if _owner_id(repo_root, specs, authority) not in published_set:
            continue
        references.append(copy.deepcopy(reference))
    references.sort(
        key=lambda item: (
            str(item.get("authority", "")),
            int(item.get("line", 0)),
            str(item.get("target", "")),
            str(item.get("id", "")),
        )
    )

    diagnostics = _public_diagnostics(
        repo_root,
        specs,
        state,
        visible,
        published_set,
    )

    counts = {"nodes": len(nodes), "edges": len(edges), "references": len(references)}
    private_digest = str(state.manifest.get("graph_sha256", ""))
    if re.fullmatch(r"[0-9a-f]{64}", private_digest) is None:
        raise StaticExportError("authority graph has no valid graph_sha256")
    graph_payload = finalize_self_digest(
        {
            "schema": SITE_GRAPH_SCHEMA,
            "namespace": "public",
            "source_graph_sha256": private_digest,
            "counts": counts,
            "nodes": nodes,
            "edges": edges,
            "references": references,
            "diagnostics": diagnostics,
        },
        "graph_sha256",
    )
    validate_contract(graph_payload)

    filtered_state = GraphState(
        nodes={str(node["id"]): copy.deepcopy(node) for node in nodes},
        edges={
            (
                str(edge["source"]),
                str(edge["relation"]),
                str(edge["target"]),
            ): copy.deepcopy(edge)
            for edge in edges
        },
        references=copy.deepcopy(references),
        manifest={
            "schema": SITE_GRAPH_SCHEMA,
            "graph_sha256": graph_payload["graph_sha256"],
            "counts": counts,
        },
    )

    raw_hashes = state.manifest.get("source_hashes") or {}
    if not isinstance(raw_hashes, dict):
        raise StaticExportError("authority graph source_hashes must be an object")
    published_hashes: dict[str, str] = {}
    for authority, digest in sorted(raw_hashes.items()):
        authority_text = str(authority)
        if _owner_id(repo_root, specs, authority_text) in published_set:
            if re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None:
                raise StaticExportError(f"invalid source hash for {authority_text}")
            published_hashes[authority_text] = str(digest)
    return graph_payload, filtered_state, published_hashes, published_ids, source_count


def _artifact_record(path: Path, root: Path, kind: str) -> dict[str, Any]:
    content = _canonical_text_bytes(path)
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "bytes": len(content),
        "sha256": _sha256_bytes(content),
    }


def _verified_existing_export(output: Path) -> dict[str, Any]:
    if output.is_symlink() or not output.is_dir():
        raise StaticExportError(
            "--replace requires an existing non-symbolic bundle directory"
        )
    try:
        children = {path.name for path in output.iterdir()}
    except OSError as error:
        raise StaticExportError(f"cannot inspect existing export: {output}") from error
    if children != SITE_BUNDLE_FILES or any(
        not path.is_file() for path in output.iterdir()
    ):
        raise StaticExportError(
            "--replace refuses a directory that is not exactly a four-file kgdistiller bundle"
        )
    try:
        return verify_export(output)
    except (OSError, UnicodeError, ValueError) as error:
        raise StaticExportError(
            "--replace refuses an unverified existing export"
        ) from error


def _export_recovery_root(repo_root: Path, output: Path) -> Path:
    identity = os.path.normcase(os.path.abspath(output)).encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()[:32]
    return repo_root / "knowledge" / "build" / EXPORT_RECOVERY_DIRECTORY / key


def _safe_recovery_directory(repo_root: Path, recovery_root: Path) -> None:
    try:
        relative = recovery_root.relative_to(repo_root)
    except ValueError as error:
        raise StaticExportError("export recovery path escapes the instance") from error
    current = repo_root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            break
        if current.is_symlink() or not current.is_dir():
            raise StaticExportError(
                f"export recovery path is not an ordinary directory: {current}"
            )


def _prune_export_recovery(recovery_root: Path) -> None:
    current = recovery_root
    stop = recovery_root.parents[2]
    while current != stop and current.is_dir() and not any(current.iterdir()):
        parent = current.parent
        current.rmdir()
        current = parent


def _remove_export_backup(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise StaticExportError(f"export cleanup tombstone is invalid: {path}")
    children = list(path.iterdir())
    if any(
        child.name not in SITE_BUNDLE_FILES or child.is_symlink() or not child.is_file()
        for child in children
    ):
        raise StaticExportError(
            f"export cleanup tombstone has unexpected content: {path}"
        )
    for child in children:
        try:
            child.unlink()
        except FileNotFoundError:
            pass
    try:
        path.rmdir()
    except FileNotFoundError:
        pass


def _recover_export_swap(
    repo_root: Path,
    output: Path,
    recovery_root: Path,
) -> dict[str, Any] | None:
    if not os.path.lexists(recovery_root):
        return None
    _safe_recovery_directory(repo_root, recovery_root)
    children = list(recovery_root.iterdir())
    if not children:
        _prune_export_recovery(recovery_root)
        return None
    if len(children) != 1:
        raise StaticExportError(
            f"export recovery contains multiple pending items: {recovery_root}"
        )
    pending = children[0]
    previous_match = re.fullmatch(r"p-([0-9a-f]{64})", pending.name)
    deleting_match = re.fullmatch(
        r"d-([0-9a-f]{32})-([0-9a-f]{64})",
        pending.name,
    )
    if previous_match is not None:
        if pending.is_symlink() or not pending.is_dir():
            raise StaticExportError(f"export recovery item is invalid: {pending}")
        previous = _verified_existing_export(pending)
        previous_digest = str(previous["export_sha256"])
        if previous_digest != previous_match.group(1):
            raise StaticExportError(
                f"export recovery receipt does not match its bundle: {pending}"
            )
        if not os.path.lexists(output):
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, output)
            _prune_export_recovery(recovery_root)
            return {"status": "rolled-back", "export_sha256": previous_digest}
        current = _verified_existing_export(output)
        current_digest = str(current["export_sha256"])
        if current.get("replaces_export_sha256") != previous_digest:
            raise StaticExportError(
                "current export does not prove ownership of the pending previous bundle"
            )
        tombstone = recovery_root / (f"d-{previous_digest[:32]}-{current_digest}")
        os.replace(pending, tombstone)
    elif deleting_match is not None:
        previous_prefix, current_digest = deleting_match.groups()
        tombstone = pending
        if not os.path.lexists(output):
            raise StaticExportError(
                "committed export is missing while cleanup tombstone remains"
            )
        current = _verified_existing_export(output)
        if current.get("export_sha256") != current_digest or not str(
            current.get("replaces_export_sha256", "")
        ).startswith(previous_prefix):
            raise StaticExportError(
                "current export does not match its cleanup tombstone"
            )
    else:
        raise StaticExportError(f"export recovery item is invalid: {pending}")
    try:
        _remove_export_backup(tombstone)
        _prune_export_recovery(recovery_root)
    except (StaticExportError, OSError) as error:
        raise StaticExportError(
            f"committed export cleanup remains pending at {tombstone}"
        ) from error
    return {"status": "finalized", "export_sha256": current_digest}


def _install_export(
    staging: Path,
    output: Path,
    replace: bool,
    *,
    recovery_root: Path | None = None,
    previous_export_sha256: str | None = None,
    current_export_sha256: str | None = None,
) -> dict[str, Any]:
    if not output.exists() and not output.is_symlink():
        os.replace(staging, output)
        return {
            "committed": True,
            "cleanup_status": "complete",
            "warnings": [],
            "recovery_paths": [],
        }
    if not replace:
        raise StaticExportError(f"export output already exists: {output}")
    if (
        recovery_root is None
        or previous_export_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", previous_export_sha256) is None
        or current_export_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", current_export_sha256) is None
    ):
        raise StaticExportError("replacement requires a verified recovery receipt")
    if os.path.lexists(recovery_root):
        if recovery_root.is_symlink() or not recovery_root.is_dir():
            raise StaticExportError("export recovery root is not an ordinary directory")
        if any(recovery_root.iterdir()):
            raise StaticExportError("export recovery root is not empty")
    else:
        recovery_root.mkdir(parents=True)
    backup = recovery_root / f"p-{previous_export_sha256}"
    try:
        os.replace(output, backup)
    except BaseException as error:
        _prune_export_recovery(recovery_root)
        raise StaticExportError(
            "export swap could not preserve the old bundle"
        ) from error
    try:
        os.replace(staging, output)
    except BaseException as install_error:
        try:
            os.replace(backup, output)
        except BaseException as rollback_error:
            raise StaticExportError(
                f"export swap and rollback failed; previous bundle is preserved at {backup}"
            ) from rollback_error
        _prune_export_recovery(recovery_root)
        raise StaticExportError(
            "export swap failed; previous bundle was restored"
        ) from install_error
    tombstone = recovery_root / (
        f"d-{previous_export_sha256[:32]}-{current_export_sha256}"
    )
    try:
        os.replace(backup, tombstone)
        _remove_export_backup(tombstone)
        _prune_export_recovery(recovery_root)
    except (StaticExportError, OSError):
        pending = tombstone if os.path.lexists(tombstone) else backup
        return {
            "committed": True,
            "cleanup_status": "pending",
            "warnings": [
                (
                    "the new export is committed, but its verified previous bundle "
                    "still needs managed cleanup"
                )
            ],
            "recovery_paths": [str(pending)],
        }
    return {
        "committed": True,
        "cleanup_status": "complete",
        "warnings": [],
        "recovery_paths": [],
    }


def export_site_bundle(
    repo_root: Path,
    output: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path | None = None,
    product_commit: str | None = None,
    product_repository: str = PRODUCT_REPOSITORY,
    source_repository: str,
    replace: bool = False,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output = Path(os.path.abspath(output))
    registry = registry.resolve()
    graph_dir = graph_dir.resolve()
    identities = (
        (repo_root / "knowledge/identities.json").resolve()
        if identities is None
        else identities.resolve()
    )
    if output == repo_root or output == graph_dir or repo_root == output.parent:
        raise StaticExportError("export output must be a dedicated non-root directory")
    recovery_root = _export_recovery_root(repo_root, output)
    if (
        output == recovery_root
        or output in recovery_root.parents
        or recovery_root in output.parents
    ):
        raise StaticExportError("export output cannot overlap managed recovery")
    _safe_recovery_directory(repo_root, recovery_root)
    _recover_export_swap(repo_root, output, recovery_root)
    output_exists = output.exists() or output.is_symlink()
    if output_exists and not replace:
        raise StaticExportError(f"export output already exists: {output}")
    previous_export = _verified_existing_export(output) if output_exists else None

    producer_repository = _safe_repository_url(product_repository, "product repository")
    assert producer_repository is not None
    source_repository = _safe_repository_url(source_repository, "source repository")
    assert source_repository is not None
    producer_commit = resolve_product_commit(product_commit)
    state = load_state(graph_dir)
    _require_graph_generation_bindings(state, registry, identities)
    graph_payload, filtered_state, published_hashes, published_ids, source_count = (
        build_site_graph(repo_root, registry, state)
    )

    raw_source_hashes = state.manifest.get("source_hashes") or {}
    if not isinstance(raw_source_hashes, dict):
        raise StaticExportError("authority graph source_hashes must be an object")
    if GIT_COMMIT_RE.fullmatch(str(state.manifest.get("git_revision", ""))) is None:
        raise StaticExportError("authority graph has no full source Git revision")
    source_revision = _source_checkout_revision(
        repo_root,
        _source_inputs(
            repo_root,
            registry,
            graph_dir,
            state.manifest,
            raw_source_hashes,
            state,
        ),
    )
    private_counts_raw = state.manifest.get("counts") or {}
    private_counts = {
        key: int(private_counts_raw.get(key, -1))
        for key in ("nodes", "edges", "references")
    }
    if any(value < 0 for value in private_counts.values()):
        raise StaticExportError("authority graph has invalid counts")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        graph_path = staging / "graph.json"
        atomic_write(graph_path, pretty_json(graph_payload))
        registry_path = staging / "knowledge-registry.typ"
        atomic_write(registry_path, typst_registry_text(filtered_state))
        verifier_source = Path(__file__).with_name("static_export_verifier.py")
        verifier_path = staging / "verify_export.py"
        shutil.copyfile(verifier_source, verifier_path)

        artifacts = [
            _artifact_record(graph_path, staging, "site-graph"),
            _artifact_record(registry_path, staging, "typst-registry"),
            _artifact_record(verifier_path, staging, "standalone-verifier"),
        ]
        manifest_payload: dict[str, Any] = {
            "schema": EXPORT_SCHEMA,
            "status": "exported",
            "producer": {
                "name": "kgdistiller",
                "repository": producer_repository,
                "version": _product_version(),
                "commit": producer_commit,
            },
            "source": {
                "repository": source_repository,
                "revision": source_revision,
                "digest": sha256_json(dict(sorted(raw_source_hashes.items()))),
                "files": len(raw_source_hashes),
                "published_digest": sha256_json(published_hashes),
                "published_files": len(published_hashes),
                "published_hashes": published_hashes,
            },
            "visibility": {
                "policy": "explicit-publish",
                "published_sources": published_ids,
                "excluded_sources": source_count - len(published_ids),
            },
            "graph": {
                "private_schema": GRAPH_SCHEMA,
                "private_sha256": str(state.manifest["graph_sha256"]),
                "private_counts": private_counts,
                "public_schema": SITE_GRAPH_SCHEMA,
                "public_sha256": str(graph_payload["graph_sha256"]),
                "public_counts": dict(graph_payload["counts"]),
            },
            "artifacts": artifacts,
        }
        if previous_export is not None:
            manifest_payload["replaces_export_sha256"] = previous_export[
                "export_sha256"
            ]
        manifest = finalize_self_digest(manifest_payload, "export_sha256")
        validate_contract(manifest)
        atomic_write(staging / "manifest.json", pretty_json(manifest))
        verify_export(staging)
        current_state = _require_same_graph_generation(graph_dir, state)
        _require_graph_generation_bindings(current_state, registry, identities)
        install = _install_export(
            staging,
            output,
            replace,
            recovery_root=recovery_root,
            previous_export_sha256=(
                str(previous_export["export_sha256"])
                if previous_export is not None
                else None
            ),
            current_export_sha256=str(manifest["export_sha256"]),
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    report = {
        "schema": EXPORT_REPORT_SCHEMA,
        "status": "exported",
        "artifact_schema": EXPORT_SCHEMA,
        "committed": install["committed"],
        "cleanup_status": install["cleanup_status"],
        "warnings": install["warnings"],
        "recovery_paths": install["recovery_paths"],
        "output": str(output),
        "export_sha256": manifest["export_sha256"],
        "producer": manifest["producer"],
        "source": {
            "repository": manifest["source"]["repository"],
            "revision": manifest["source"]["revision"],
            "digest": manifest["source"]["digest"],
            "published_digest": manifest["source"]["published_digest"],
        },
        "graph": manifest["graph"],
        "visibility": manifest["visibility"],
        "replaced": previous_export is not None,
        "replaces_export_sha256": (
            previous_export["export_sha256"] if previous_export is not None else None
        ),
    }
    validate_contract(report)
    return report
