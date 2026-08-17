"""Generate a private, lossy Obsidian projection from a validated graph."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from .cli import (
    GRAPH_SCHEMA,
    KnowledgeError,
    expand_source,
    identity_registry_sha256,
    load_sources,
    matching_sources,
    relative_path,
    sha256_authority_file,
    source_registry_sha256,
)
from .contracts import (
    ContractError,
    finalize_self_digest,
    sha256_json,
    validate_contract,
)
from .json_schema import validate_json_schema
from .query import GraphView, QueryError, load_graph_view


PROJECTION_SCHEMA = "kgdistiller-obsidian-projection-v1"
PROJECTION_REPORT_SCHEMA = "kgdistiller-obsidian-export-report-v1"
PLUGIN_GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1"
CONCEPT_SCHEMA = "kgdistiller-obsidian-concept-v1"
SOURCE_SCHEMA = "kgdistiller-obsidian-source-v1"
_WIKILINK_SEMANTIC_RE = re.compile(r"%%|[#^|\[\]\\\r\n]")
_WINDOWS_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_MAX_PORTABLE_FILENAME_BYTES = 255


class ObsidianExportError(ValueError):
    """Raised when an Obsidian projection cannot be built or verified safely."""


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ObsidianExportError(f"unsafe projection path: {value}")
    return path


def _resolve(root: Path, value: str | Path) -> Path:
    path = (root / _safe_relative(value)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ObsidianExportError(f"projection path escapes its root: {value}") from error
    return path


def _yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(values: list[str], *, indent: str = "") -> list[str]:
    if not values:
        return [f"{indent}[]"]
    return [f"{indent}- {_yaml_string(value)}" for value in values]


def _wiki_label(value: Any) -> str:
    return str(value).replace("|", "¦").replace("]]", "] ]").replace("\n", " ").strip()


def _wiki_target(value: str) -> str:
    return value.replace("\\", "/").replace("|", "%7C").replace("]]", "%5D%5D")


def _relative_wiki(from_directory: PurePosixPath, target_without_suffix: PurePosixPath) -> str:
    source_parts = from_directory.parts
    target_parts = target_without_suffix.parts
    common = 0
    while (
        common < len(source_parts)
        and common < len(target_parts)
        and source_parts[common] == target_parts[common]
    ):
        common += 1
    parts = [".."] * (len(source_parts) - common) + list(target_parts[common:])
    return "/".join(parts) or "."


def _is_portable_filename(filename: str) -> bool:
    if not filename or filename in {".", ".."}:
        return False
    try:
        if len(filename.encode("utf-8")) > _MAX_PORTABLE_FILENAME_BYTES:
            return False
    except UnicodeEncodeError:
        return False
    if _WINDOWS_INVALID_FILENAME_RE.search(filename):
        return False
    if filename.endswith((".", " ")):
        return False
    stem = filename.rsplit(".", 1)[0]
    if stem.endswith((".", " ")):
        return False
    reserved_stem = stem.split(".", 1)[0].rstrip(". ").casefold()
    return reserved_stem not in _WINDOWS_RESERVED_FILENAMES


def _hashed_concept_relative(node_id: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", node_id.casefold()).strip("-")[:48]
    digest = _sha256_bytes(node_id.encode("utf-8"))
    return Path("concepts") / f"_kgd-{slug or 'concept'}-{digest}.md"


def _portable_filename_key(filename: str) -> str:
    """Return a conservative cross-platform filename collision key."""

    return unicodedata.normalize("NFKC", filename).casefold()


def _preferred_concept_relative(node: dict[str, Any]) -> Path | None:
    """Use the authored Markdown target (or label) when Obsidian can name it."""

    node_id = str(node["id"])
    label = str(node.get("label", node_id)).strip()
    properties = node.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    source_name = str(properties.get("source_name", "")).strip()
    raw_target = (
        source_name
        if properties.get("source_format") == "markdown" and source_name
        else label
    )
    filename = raw_target if raw_target.casefold().endswith(".md") else f"{raw_target}.md"
    if (
        not raw_target
        or raw_target in {".", ".."}
        or raw_target.startswith(".")
        or _WIKILINK_SEMANTIC_RE.search(raw_target)
        or not _is_portable_filename(filename)
    ):
        return None
    return Path("concepts") / filename


def _concept_relatives(nodes: dict[str, dict[str, Any]]) -> dict[str, Path]:
    """Plan one collision-free node-to-note mapping for the complete export."""

    fallbacks = {
        node_id: _hashed_concept_relative(node_id)
        for node_id in nodes
    }
    preferred = {
        node_id: _preferred_concept_relative(node)
        for node_id, node in nodes.items()
    }
    preferred_groups: dict[str, list[str]] = {}
    for node_id, relative in preferred.items():
        if relative is not None:
            preferred_groups.setdefault(_portable_filename_key(relative.name), []).append(node_id)
    selected = {
        node_id: (
            relative
            if relative is not None
            and len(preferred_groups[_portable_filename_key(relative.name)]) == 1
            else fallbacks[node_id]
        )
        for node_id, relative in preferred.items()
    }

    # A legitimate canonical label can itself look like another node's hashed
    # fallback. Prefer the stable fallback and demote only label-derived paths.
    while True:
        groups: dict[str, list[str]] = {}
        for node_id, relative in selected.items():
            groups.setdefault(_portable_filename_key(relative.name), []).append(node_id)
        collisions = [node_ids for node_ids in groups.values() if len(node_ids) > 1]
        if not collisions:
            return selected
        changed = False
        for node_ids in collisions:
            for node_id in node_ids:
                if selected[node_id] != fallbacks[node_id]:
                    selected[node_id] = fallbacks[node_id]
                    changed = True
        if not changed:
            raise ObsidianExportError(
                "hashed concept filenames collide under Unicode/case folding"
            )


def _require_unambiguous_registered_markdown_targets(
    nodes: dict[str, dict[str, Any]],
    authorities: dict[str, str],
) -> None:
    """Reject raw marker targets shadowed by a registered Markdown filename."""

    markdown_by_name: dict[str, list[str]] = {}
    for authority in authorities:
        path = PurePosixPath(authority)
        if path.suffix.casefold() == ".md":
            markdown_by_name.setdefault(
                _portable_filename_key(path.name),
                [],
            ).append(authority)
    conflicts: list[tuple[str, str, list[str]]] = []
    for node_id, node in sorted(nodes.items()):
        relative = _preferred_concept_relative(node)
        if relative is None:
            continue
        occupied = markdown_by_name.get(_portable_filename_key(relative.name))
        if occupied:
            conflicts.append(
                (
                    node_id,
                    str(node.get("label", node_id)),
                    sorted(occupied),
                )
            )
    if conflicts:
        node_id, label, occupied = conflicts[0]
        more = f" (+{len(conflicts) - 1} more)" if len(conflicts) > 1 else ""
        raise ObsidianExportError(
            "Obsidian marker target conflicts with a registered "
            f"Markdown authority basename: node={node_id!r}, label={label!r}, "
            f"authorities={occupied}{more}; rename the authority file or use "
            "an Obsidian plugin"
        )


def _source_relative(authority: str) -> Path:
    authority_path = _safe_relative(authority)
    if authority_path.suffix.lower() not in {".md", ".typ", ".tex"}:
        raise ObsidianExportError(f"unsupported authority format in projection: {authority}")
    if _WIKILINK_SEMANTIC_RE.search(authority_path.as_posix()):
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            authority_path.as_posix().casefold(),
        ).strip("-")[:48] or "authority"
        digest = _sha256_bytes(authority_path.as_posix().encode("utf-8"))
        return Path("sources/by-authority") / f"{slug}-{digest}.md"
    return Path("sources") / Path(f"{authority_path.as_posix()}.md")


def _entry_markdown(node: dict[str, Any]) -> list[str]:
    entry = node.get("entry") if isinstance(node.get("entry"), dict) else {}
    text = str(node.get("text", "")).strip()
    summary = str(entry.get("summary", "")).strip() or text
    lines = ["## Entry", "", summary or "_No curated entry yet._", ""]
    labels = {
        "context": "Context",
        "role": "Role",
        "prerequisites": "Prerequisites",
        "common_confusions": "Common confusions",
        "open_questions": "Open questions",
        "sources": "Sources",
    }
    for key, title in labels.items():
        value = entry.get(key)
        values = value if isinstance(value, list) else ([value] if value else [])
        rendered = [str(item).strip() for item in values if str(item).strip()]
        if not rendered:
            continue
        lines.extend([f"### {title}", ""])
        lines.extend(f"- {item}" for item in rendered)
        lines.append("")
    return lines


def _render_concept(
    node: dict[str, Any],
    *,
    graph_sha256: str,
    relations: list[tuple[str, str, dict[str, Any]]],
    concept_relatives: dict[str, Path],
) -> str:
    node_id = str(node["id"])
    label = str(node.get("label", node_id))
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    provenance = node.get("provenance") if isinstance(node.get("provenance"), dict) else {}
    aliases = sorted(
        {
            str(value).strip()
            for value in [label, *(properties.get("aliases") or [])]
            if str(value).strip() and str(value).strip() != node_id
        },
        key=lambda value: value.casefold(),
    )
    fields = [str(value) for value in properties.get("fields", [])]
    authority = str(provenance.get("authority", ""))
    start = int(provenance.get("definition_start_line") or provenance.get("line") or 1)
    end = int(provenance.get("definition_end_line") or start)
    source_relative = _source_relative(authority)
    source_target = _relative_wiki(
        PurePosixPath("concepts"),
        PurePosixPath(source_relative.as_posix()).with_suffix(""),
    )
    alias_lines = ["aliases:", *_yaml_list(aliases, indent="  ")] if aliases else ["aliases: []"]
    field_tags = [f"  - {_yaml_string(f'kgdistiller/field/{field}')}" for field in fields]
    lines = [
        "---",
        f"kgd_schema: {_yaml_string(CONCEPT_SCHEMA)}",
        f"kgd_id: {_yaml_string(node_id)}",
        f"kgd_graph_sha256: {_yaml_string(graph_sha256)}",
        f"kgd_definition_sha256: {_yaml_string(provenance.get('definition_sha256', ''))}",
        f"kgd_authority: {_yaml_string(authority)}",
        f"kgd_definition_start_line: {start}",
        f"kgd_definition_end_line: {end}",
        *alias_lines,
        "tags:",
        "  - kgdistiller/concept",
        *field_tags,
        "---",
        "",
        f"# {label}",
        "",
        "> [!warning] Generated downstream projection",
        "> Edit the registered authority, then rebuild this projection.",
        "",
    ]
    status = str(properties.get("curation_status", "pending"))
    if status != "current":
        lines.extend(
            [
                "> [!caution] Curation status",
                f"> This entry is `{status}` and must not be treated as a reviewed definition.",
                "",
            ]
        )
    lines.extend(_entry_markdown(node))
    line_label = str(start) if end == start else f"{start}–{end}"
    lines.extend(
        [
            "## Authority",
            "",
            f"- [[{_wiki_target(source_target)}|{_wiki_label(authority)}:{line_label}]]",
            "",
        ]
    )
    if relations:
        lines.extend(["## Relations", ""])
        for direction, relation, other in relations:
            other_id = str(other["id"])
            target = _relative_wiki(
                PurePosixPath("concepts"),
                PurePosixPath(concept_relatives[other_id].as_posix()).with_suffix(""),
            )
            arrow = "→" if direction == "outgoing" else "←"
            lines.append(
                f"- `{relation}` {arrow} [[{_wiki_target(target)}|{_wiki_label(other.get('label', other_id))}]]"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_source_proxy(
    repo_root: Path,
    authority: str,
    *,
    graph_sha256: str,
    authority_link_mode: str,
    definitions: list[dict[str, Any]],
    references: list[dict[str, Any]],
    concept_relatives: dict[str, Path],
) -> str:
    relative = _source_relative(authority)
    from_directory = PurePosixPath(relative.parent.as_posix())
    source = (repo_root / authority).resolve()
    try:
        source.relative_to(repo_root.resolve())
    except ValueError as error:
        raise ObsidianExportError(f"authority escapes the repository: {authority}") from error
    suffix = source.suffix.lower()
    if (
        suffix == ".md"
        and authority_link_mode == "vault-relative"
        and _WIKILINK_SEMANTIC_RE.search(authority) is None
    ):
        # A qualified Wikilink is resolved from the vault root by Obsidian.
        target = PurePosixPath(authority).with_suffix("").as_posix()
        source_link = f"[[{_wiki_target(target)}|Open registered Markdown authority]]"
    else:
        label = "Markdown" if suffix == ".md" else suffix[1:].upper()
        source_link = f"[Open registered {label} authority]({source.as_uri()})"
    lines = [
        "---",
        f"kgd_schema: {_yaml_string(SOURCE_SCHEMA)}",
        f"kgd_authority: {_yaml_string(authority)}",
        f"kgd_graph_sha256: {_yaml_string(graph_sha256)}",
        "tags:",
        "  - kgdistiller/source",
        "---",
        "",
        f"# {authority}",
        "",
        "> [!warning] Generated source proxy",
        "> Edit the registered authority, not this file.",
        "",
        f"- {source_link}",
        "",
    ]
    if definitions:
        lines.extend(["## Definitions", ""])
        for node in definitions:
            node_id = str(node["id"])
            provenance = node.get("provenance") or {}
            start = int(provenance.get("definition_start_line") or provenance.get("line") or 1)
            end = int(provenance.get("definition_end_line") or start)
            span = str(start) if start == end else f"{start}–{end}"
            concept = _relative_wiki(
                from_directory,
                PurePosixPath(concept_relatives[node_id].as_posix()).with_suffix(""),
            )
            lines.append(
                f"- [[{_wiki_target(concept)}|{_wiki_label(node.get('label', node_id))}]] · lines {span}"
            )
        lines.append("")
    if references:
        lines.extend(["## References", ""])
        for reference in references:
            target_id = str(reference.get("target", ""))
            concept = _relative_wiki(
                from_directory,
                PurePosixPath(concept_relatives[target_id].as_posix()).with_suffix(""),
            )
            lines.append(
                f"- line {int(reference.get('line') or 1)} → [[{_wiki_target(concept)}|{_wiki_label(reference.get('label') or target_id)}]]"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _validate_output_boundary(repo_root: Path, output: Path, registry: Path) -> None:
    if output == repo_root or ".obsidian" in {part.casefold() for part in output.parts}:
        raise ObsidianExportError("projection output cannot be the project root or .obsidian")
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
            f"projection output overlaps registered authority root: {spec.id}"
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
                raise ValueError(f"invalid {label} inventory")
            entries = inventory.get("entries") or []
            if not isinstance(entries, list):
                raise ValueError(f"invalid {label} inventory")
            for record in entries:
                if not isinstance(record, dict):
                    raise ValueError(f"invalid {label} record")
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
            raise ValueError("manifest is not an object")
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
        current = load_graph_view(graph_dir)
    except (QueryError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(f"cannot reload the authority graph: {error}") from error
    if current.generation != view.generation:
        raise ObsidianExportError(
            "authority graph generation changed during Obsidian export; retry the export"
        )


def _artifact_record(relative: str, content: bytes, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": relative,
        "bytes": len(content),
        "sha256": _sha256_bytes(content),
    }


def _build_plugin_graph(
    *,
    graph_sha256: str,
    snapshot_sha256: str,
    source_hashes_sha256: str,
    nodes: dict[str, dict[str, Any]],
    authorities: list[str],
    concept_relatives: dict[str, Path],
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
        fields = sorted(
            {
                str(value).strip()
                for value in properties.get("fields", [])
                if str(value).strip()
            }
        )
        concepts.append(
            {
                "id": node_id,
                "label": label,
                "note_path": concept_relatives[node_id].as_posix(),
                "authority": authority,
                "curation_status": str(properties.get("curation_status", "pending")),
                "aliases": aliases,
                "fields": fields,
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
    sources = [
        {
            "authority": authority,
            "note_path": _source_relative(authority).as_posix(),
        }
        for authority in authorities
    ]
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
            "graph_schema": GRAPH_SCHEMA,
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


def _schema() -> dict[str, Any]:
    path = Path(__file__).with_name("schemas") / "kgdistiller-obsidian-projection-v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verify_obsidian_projection(output: Path) -> dict[str, Any]:
    """Verify manifest digests and reject unmanaged files in a projection."""
    if output.is_symlink() or not output.is_dir():
        raise ObsidianExportError("projection output is not an ordinary directory")
    output = output.resolve()
    manifest_path = output / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ObsidianExportError("projection manifest is not an ordinary file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_json_schema(manifest, _schema())
    if errors:
        raise ObsidianExportError(f"invalid projection manifest: {errors[0].message}")
    semantic_artifacts = [
        artifact
        for artifact in manifest["artifacts"]
        if artifact.get("kind") == "semantic-graph"
    ]
    if len(semantic_artifacts) != 1:
        raise ObsidianExportError(
            "projection must contain exactly one semantic graph artifact"
        )
    digest_payload = dict(manifest)
    claimed = str(digest_payload.pop("projection_sha256", ""))
    if sha256_json(digest_payload) != claimed:
        raise ObsidianExportError("projection manifest digest mismatch")
    declared = {"manifest.json"}
    for artifact in manifest["artifacts"]:
        relative = str(artifact["path"])
        lexical_path = output / _safe_relative(relative)
        if lexical_path.is_symlink() or not lexical_path.is_file():
            raise ObsidianExportError(
                f"projection artifact is not an ordinary file: {relative}"
            )
        path = _resolve(output, relative)
        content = path.read_bytes()
        if len(content) != int(artifact["bytes"]) or _sha256_bytes(content) != artifact["sha256"]:
            raise ObsidianExportError(f"projection artifact digest mismatch: {relative}")
        if artifact["kind"] == "semantic-graph":
            try:
                validate_contract(json.loads(content.decode("utf-8")))
            except (ContractError, UnicodeError, json.JSONDecodeError) as error:
                raise ObsidianExportError(
                    f"invalid semantic graph artifact: {relative}: {error}"
                ) from error
        declared.add(relative)
    actual = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }
    if actual != declared:
        extra = sorted(actual - declared)
        missing = sorted(declared - actual)
        raise ObsidianExportError(
            f"projection managed-file mismatch: extra={extra}, missing={missing}"
        )
    report = {
        "schema": PROJECTION_REPORT_SCHEMA,
        "status": "verified",
        "artifact_schema": PROJECTION_SCHEMA,
        "projection_sha256": claimed,
        "source": manifest["source"],
        "policy": manifest["policy"],
        "counts": manifest["counts"],
        "output": str(output),
    }
    validate_contract(report)
    return report


def _install(stage: Path, output: Path, *, replace: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if output.exists():
        verify_obsidian_projection(output)
        if not replace:
            raise ObsidianExportError("projection exists with different content; pass --replace")
        recovery = output.parent / ".kgd-obsidian-recovery"
        recovery.mkdir(parents=True, exist_ok=True)
        backup = recovery / output.name
        if backup.exists():
            raise ObsidianExportError(f"projection recovery path already exists: {backup}")
        os.replace(output, backup)
    try:
        os.replace(stage, output)
    except BaseException:
        if backup is not None and backup.exists() and not output.exists():
            os.replace(backup, output)
        raise
    if backup is not None and backup.exists():
        shutil.rmtree(backup)
        try:
            backup.parent.rmdir()
        except OSError:
            pass


def build_obsidian_projection(
    repo_root: Path,
    output: Path,
    *,
    registry: Path,
    graph_dir: Path,
    identities: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Build, verify, and atomically install a private Obsidian projection."""
    repo_root = repo_root.resolve()
    identities = (
        (repo_root / "knowledge/identities.json").resolve()
        if identities is None
        else identities.resolve()
    )
    if output.is_symlink():
        raise ObsidianExportError("projection output is not an ordinary directory")
    output = output.resolve()
    _validate_output_boundary(repo_root, output, registry)
    try:
        view = load_graph_view(graph_dir)
    except (QueryError, OSError, UnicodeError, ValueError) as error:
        raise ObsidianExportError(f"cannot load the authority graph: {error}") from error
    _require_fresh_registries(graph_dir, registry, identities, view)
    _require_fresh_authorities(repo_root, registry, view)
    _require_fresh_entry_authorities(repo_root, graph_dir)
    snapshot = view.snapshot
    graph_sha = str(snapshot["graph"]["sha256"])
    source_hashes = view.source_hashes
    source_hashes_sha = sha256_json(source_hashes)
    try:
        output.relative_to(repo_root)
        authority_link_mode = "vault-relative"
    except ValueError:
        authority_link_mode = "file-uri"
    nodes = {
        str(node["id"]): node
        for node in snapshot["nodes"]
        if node.get("type") == "knowledge"
        and (node.get("provenance") or {}).get("active") is True
    }
    if authority_link_mode == "vault-relative":
        _require_unambiguous_registered_markdown_targets(nodes, source_hashes)
    concept_relatives = _concept_relatives(nodes)
    relations: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
        node_id: [] for node_id in nodes
    }
    semantic_edges: list[dict[str, Any]] = []
    link_count = 0
    for edge in snapshot["edges"]:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        relation = str(edge.get("relation", ""))
        if (
            relation == "contains"
            or source not in nodes
            or target not in nodes
            or edge.get("curation_status") == "needs-review"
        ):
            continue
        relations[source].append(("outgoing", relation, nodes[target]))
        relations[target].append(("incoming", relation, nodes[source]))
        semantic_edges.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "evidence": str(edge.get("evidence", "")).strip(),
            }
        )
        link_count += 2
    for values in relations.values():
        values.sort(key=lambda item: (item[1], str(item[2].get("label", "")), str(item[2]["id"])))

    by_authority: dict[str, list[dict[str, Any]]] = {}
    for node in nodes.values():
        authority = str((node.get("provenance") or {}).get("authority", ""))
        by_authority.setdefault(authority, []).append(node)
    refs_by_authority: dict[str, list[dict[str, Any]]] = {}
    for reference in snapshot["references"]:
        if str(reference.get("target", "")) in nodes:
            refs_by_authority.setdefault(str(reference.get("authority", "")), []).append(reference)
    authorities = sorted(set(by_authority) | set(refs_by_authority))

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    artifacts: list[dict[str, Any]] = []
    try:
        for node_id, node in sorted(nodes.items()):
            relative = concept_relatives[node_id]
            content = _render_concept(
                node,
                graph_sha256=graph_sha,
                relations=relations[node_id],
                concept_relatives=concept_relatives,
            ).encode("utf-8")
            target = _resolve(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            artifacts.append(_artifact_record(relative.as_posix(), content, "concept"))
            link_count += 1
        for authority in authorities:
            relative = _source_relative(authority)
            content = _render_source_proxy(
                repo_root,
                authority,
                graph_sha256=graph_sha,
                authority_link_mode=authority_link_mode,
                definitions=sorted(by_authority.get(authority, []), key=lambda node: str(node["id"])),
                references=sorted(
                    refs_by_authority.get(authority, []),
                    key=lambda reference: (int(reference.get("line", 0)), str(reference.get("target", ""))),
                ),
                concept_relatives=concept_relatives,
            ).encode("utf-8")
            target = _resolve(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            artifacts.append(_artifact_record(relative.as_posix(), content, "source"))
            link_count += len(by_authority.get(authority, [])) + len(refs_by_authority.get(authority, []))
        plugin_graph = _build_plugin_graph(
            graph_sha256=graph_sha,
            snapshot_sha256=str(snapshot["snapshot_sha256"]),
            source_hashes_sha256=source_hashes_sha,
            nodes=nodes,
            authorities=authorities,
            concept_relatives=concept_relatives,
            semantic_edges=semantic_edges,
            references=list(snapshot["references"]),
        )
        plugin_graph_content = _pretty_json(plugin_graph).encode("utf-8")
        plugin_graph_relative = "semantic-graph.json"
        _resolve(stage, plugin_graph_relative).write_bytes(plugin_graph_content)
        artifacts.append(
            _artifact_record(plugin_graph_relative, plugin_graph_content, "semantic-graph")
        )
        artifacts.sort(key=lambda artifact: str(artifact["path"]))
        manifest: dict[str, Any] = {
            "schema": PROJECTION_SCHEMA,
            "status": "ready",
            "source": {
                "graph_schema": GRAPH_SCHEMA,
                "graph_sha256": graph_sha,
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "source_hashes_sha256": source_hashes_sha,
            },
            "policy": {
                "nodes": "active-knowledge",
                "edges": "current-semantic",
                "edge_semantics_in_obsidian_graph": "lossy",
                "plugin_graph": "typed",
                "authority_links": authority_link_mode,
            },
            "counts": {
                "concepts": len(nodes),
                "sources": len(authorities),
                "links": link_count,
            },
            "artifacts": artifacts,
        }
        manifest["projection_sha256"] = sha256_json(manifest)
        errors = validate_json_schema(manifest, _schema())
        if errors:
            raise ObsidianExportError(f"invalid generated manifest: {errors[0].message}")
        (stage / "manifest.json").write_text(_pretty_json(manifest), encoding="utf-8")
        verify_obsidian_projection(stage)
        current: dict[str, Any] | None = None
        if output.exists():
            current = verify_obsidian_projection(output)
        _require_same_graph_generation(graph_dir, view)
        _require_fresh_registries(graph_dir, registry, identities, view)
        _require_fresh_authorities(repo_root, registry, view)
        if current is not None and current["projection_sha256"] == manifest["projection_sha256"]:
            shutil.rmtree(stage)
            stage = Path()
            report = {**current, "changed": False}
            validate_contract(report)
            return report
        _install(stage, output, replace=replace)
        stage = Path()
        report = {**verify_obsidian_projection(output), "changed": True}
        validate_contract(report)
        return report
    finally:
        if stage != Path() and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
