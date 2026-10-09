"""Write the Obsidian plugin's typed graph feed from a validated graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cli import (
    KnowledgeError,
    atomic_write,
    expand_source,
    identity_registry_sha256,
    load_sources,
    matching_sources,
    relative_path,
    sha256_authority_file,
    source_registry_sha256,
)
from .contracts import finalize_self_digest, sha256_json, validate_contract
from .knowledge_paths import knowledge_root
from .query import GraphView, QueryError, load_graph_view

PLUGIN_GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1"


class ObsidianExportError(ValueError):
    """Raised when the Obsidian graph feed cannot be built safely."""


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _safe_relative(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ObsidianExportError(f"unsafe repository path: {value}")
    return path


def _resolve(root: Path, value: str | Path) -> Path:
    path = (root / _safe_relative(value)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ObsidianExportError(f"repository path escapes its root: {value}") from error
    return path


def _validate_output_boundary(repo_root: Path, output: Path, registry: Path) -> None:
    if output == repo_root or ".obsidian" in {part.casefold() for part in output.parts}:
        raise ObsidianExportError("graph feed output cannot be the project root or .obsidian")
    try:
        specs = load_sources(repo_root, registry)
    except (KnowledgeError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(f"cannot load the source registry: {error}") from error
    for spec in specs:
        try:
            output.relative_to(spec.root.resolve())
        except ValueError:
            continue
        raise ObsidianExportError(
            f"graph feed output overlaps registered authority root: {spec.id}"
        )


def _current_authority_hashes(repo_root: Path, registry: Path) -> dict[str, str]:
    """Hash the complete, uniquely owned authority inventory from the registry."""
    try:
        specs = load_sources(repo_root, registry)
        hashes: dict[str, str] = {}
        for spec in specs:
            for source in expand_source(spec):
                authority = relative_path(repo_root, source)
                owners = matching_sources(specs, source)
                if len(owners) != 1:
                    owner_ids = ", ".join(sorted(owner.id for owner in owners)) or "none"
                    raise ObsidianExportError(
                        "authority ownership is not unique for "
                        f"{authority}: {owner_ids}; run kgdistiller sync after fixing the registry"
                    )
                if source.suffix.lower() not in {".md", ".typ", ".tex"}:
                    raise ObsidianExportError(
                        f"registered authority has an unsupported format: {authority}"
                    )
                digest = sha256_authority_file(source)
                previous = hashes.setdefault(authority, digest)
                if previous != digest:
                    raise ObsidianExportError(
                        f"authority inventory is inconsistent for {authority}"
                    )
        return dict(sorted(hashes.items()))
    except ObsidianExportError:
        raise
    except (KnowledgeError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(
            f"cannot compute the current registered authority inventory: {error}"
        ) from error


def _require_fresh_authorities(
    repo_root: Path,
    registry: Path,
    view: GraphView,
) -> None:
    """Require the registry's complete canonical inventory to equal the graph generation."""
    graph_hashes = dict(sorted(view.source_hashes.items()))
    current_hashes = _current_authority_hashes(repo_root, registry)
    if current_hashes == graph_hashes:
        return
    graph_paths = set(graph_hashes)
    current_paths = set(current_hashes)
    added = sorted(current_paths - graph_paths)
    deleted = sorted(graph_paths - current_paths)
    modified = sorted(
        authority
        for authority in graph_paths & current_paths
        if graph_hashes[authority] != current_hashes[authority]
    )
    raise ObsidianExportError(
        "registered authorities are out of sync with the graph; "
        f"added={added}, deleted={deleted}, modified={modified}; run kgdistiller sync"
    )


def _require_fresh_entry_authorities(repo_root: Path, graph_dir: Path) -> None:
    """Require Obsidian-visible entry Markdown to match the graph generation."""

    try:
        manifest = json.loads((graph_dir / "manifest.json").read_text(encoding="utf-8"))
        for key, label in (
            ("entry_authorities", "entry authority"),
            ("entry_sources", "entry source"),
        ):
            inventory = manifest.get(key) or {}
            if not isinstance(inventory, dict):
                raise ValueError(f"invalid {label} inventory")  # noqa: TRY004
            entries = inventory.get("entries") or []
            if not isinstance(entries, list):
                raise ValueError(f"invalid {label} inventory")  # noqa: TRY004
            for record in entries:
                if not isinstance(record, dict):
                    raise ValueError(f"invalid {label} record")  # noqa: TRY004
                relative = _safe_relative(str(record.get("path", "")))
                digest = str(record.get("sha256", ""))
                path = _resolve(repo_root, relative)
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"{label} is missing: {relative}")
                if sha256_authority_file(path) != digest:
                    raise ValueError(f"{label} changed: {relative}")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ObsidianExportError(
            f"entry Markdown authorities are out of sync with the graph: {error}; "
            "run kgdistiller sync"
        ) from error


def _require_fresh_registries(
    graph_dir: Path,
    registry: Path,
    identities: Path | None,
    view: GraphView,
) -> None:
    """Bind the live source and identity registries to the loaded graph manifest."""

    manifest_path = graph_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest is not an object")  # noqa: TRY004
        registry_sha = source_registry_sha256(registry)
        identity_sha = identity_registry_sha256(identities)
    except (OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(
            f"cannot validate the current registry generation: {error}"
        ) from error
    if sha256_json(manifest) != view.generation:
        raise ObsidianExportError(
            "authority graph generation changed during Obsidian export; retry the export"
        )
    if manifest.get("registry_sha256") != registry_sha:
        raise ObsidianExportError(
            "source registry is out of sync with the authority graph; "
            "run kgdistiller sync"
        )
    if manifest.get("identity_sha256") != identity_sha:
        raise ObsidianExportError(
            "identity registry is out of sync with the authority graph; "
            "run kgdistiller sync"
        )


def _require_same_graph_generation(graph_dir: Path, view: GraphView) -> None:
    """Reject a build if its committed GraphView generation changed mid-export."""
    try:
        current = load_graph_view(graph_dir, repo_root=view.repo_root)
    except (QueryError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(f"cannot reload the authority graph: {error}") from error
    if current.generation != view.generation:
        raise ObsidianExportError(
            "authority graph generation changed during Obsidian export; retry the export"
        )


def _build_plugin_graph(
    *,
    graph_schema: str,
    graph_sha256: str,
    snapshot_sha256: str,
    source_hashes_sha256: str,
    nodes: dict[str, dict[str, Any]],
    authorities: list[str],
    semantic_edges: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the typed, read-only graph consumed by the Obsidian plugin."""

    concepts: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    for node_id, node in sorted(nodes.items()):
        properties = (
            node.get("properties") if isinstance(node.get("properties"), dict) else {}
        )
        provenance = (
            node.get("provenance") if isinstance(node.get("provenance"), dict) else {}
        )
        label = str(node.get("label", node_id)).strip() or node_id
        authority = str(provenance.get("authority", ""))
        aliases = sorted(
            {
                str(value).strip()
                for value in [label, *(properties.get("aliases") or [])]
                if str(value).strip() and str(value).strip() != node_id
            },
            key=lambda value: value.casefold(),
        )
        concepts.append(
            {
                "id": node_id,
                "label": label,
                "authority": authority,
                "curation_status": str(properties.get("curation_status", "pending")),
                "aliases": aliases,
            }
        )
        line_start = int(
            provenance.get("definition_start_line") or provenance.get("line") or 1
        )
        definitions.append(
            {
                "source_authority": authority,
                "target": node_id,
                "line_start": line_start,
                "line_end": int(provenance.get("definition_end_line") or line_start),
            }
        )
    sources = [{"authority": authority} for authority in authorities]
    plugin_references: list[dict[str, Any]] = []
    for reference in sorted(
        references,
        key=lambda item: (
            str(item.get("authority", "")),
            int(item.get("line", 0)),
            str(item.get("target", "")),
            str(item.get("id", "")),
        ),
    ):
        target = str(reference.get("target", ""))
        if target not in nodes:
            continue
        item: dict[str, Any] = {
            "id": str(reference.get("id", "")),
            "source_authority": str(reference.get("authority", "")),
            "target": target,
            "label": str(reference.get("label") or target),
            "line": int(reference.get("line") or 1),
        }
        context = str(reference.get("context") or "").strip()
        if context:
            item["context"] = context
        plugin_references.append(item)
    graph = {
        "schema": PLUGIN_GRAPH_SCHEMA,
        "source": {
            "graph_schema": graph_schema,
            "graph_sha256": graph_sha256,
            "snapshot_sha256": snapshot_sha256,
            "source_hashes_sha256": source_hashes_sha256,
        },
        "counts": {
            "concepts": len(concepts),
            "sources": len(sources),
            "semantic_edges": len(semantic_edges),
            "definitions": len(definitions),
            "references": len(plugin_references),
        },
        "concepts": concepts,
        "sources": sources,
        "semantic_edges": sorted(
            semantic_edges,
            key=lambda edge: (edge["source"], edge["relation"], edge["target"]),
        ),
        "definitions": sorted(
            definitions,
            key=lambda item: (item["source_authority"], item["target"]),
        ),
        "references": plugin_references,
    }
    finalized = finalize_self_digest(graph, "bundle_sha256")
    validate_contract(finalized)
    return finalized


def export_obsidian_graph(
    repo_root: Path,
    output_file: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path | None = None,
) -> dict[str, Any]:
    """Atomically write the plugin feed for the current graph generation."""
    repo_root = repo_root.resolve()
    identities = (
        (knowledge_root(repo_root) / "identities.json").resolve()
        if identities is None
        else identities.resolve()
    )
    if output_file.is_symlink():
        raise ObsidianExportError("graph feed output must not be a symlink")
    if output_file.suffix.lower() != ".json":
        raise ObsidianExportError("graph feed output must be a .json file")
    if output_file.exists() and not output_file.is_file():
        raise ObsidianExportError("graph feed output is not an ordinary file")
    output_file = output_file.resolve()
    _validate_output_boundary(repo_root, output_file, registry)
    try:
        view = load_graph_view(graph_dir, repo_root=repo_root)
    except (QueryError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(f"cannot load the authority graph: {error}") from error
    _require_fresh_registries(graph_dir, registry, identities, view)
    _require_fresh_authorities(repo_root, registry, view)
    _require_fresh_entry_authorities(repo_root, graph_dir)
    snapshot = view.snapshot
    nodes = {
        str(node["id"]): node
        for node in snapshot["nodes"]
        if node.get("type") == "knowledge"
        and (node.get("provenance") or {}).get("active") is True
    }
    semantic_edges = [
        {
            "source": str(edge.get("source", "")),
            "relation": str(edge.get("relation", "")),
            "target": str(edge.get("target", "")),
            "evidence": str(edge.get("evidence", "")).strip(),
        }
        for edge in snapshot["edges"]
        if str(edge.get("source", "")) in nodes
        and str(edge.get("target", "")) in nodes
        and edge.get("curation_status") != "needs-review"
    ]
    authorities = sorted(
        {str((node.get("provenance") or {}).get("authority", "")) for node in nodes.values()}
        | {
            str(reference.get("authority", ""))
            for reference in snapshot["references"]
            if str(reference.get("target", "")) in nodes
        }
    )
    plugin_graph = _build_plugin_graph(
        graph_schema=snapshot["graph"]["schema"],
        graph_sha256=str(snapshot["graph"]["sha256"]),
        snapshot_sha256=str(snapshot["snapshot_sha256"]),
        source_hashes_sha256=sha256_json(view.source_hashes),
        nodes=nodes,
        authorities=authorities,
        semantic_edges=semantic_edges,
        references=list(snapshot["references"]),
    )
    _require_same_graph_generation(graph_dir, view)
    _require_fresh_registries(graph_dir, registry, identities, view)
    _require_fresh_authorities(repo_root, registry, view)
    atomic_write(output_file, _pretty_json(plugin_graph))
    return {
        "status": "exported",
        "output": str(output_file),
        "counts": plugin_graph["counts"],
    }
