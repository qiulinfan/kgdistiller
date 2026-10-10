"""Write the Obsidian plugin's typed graph feed from the entry store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ContractError, validate_contract
from .knowledge_store import atomic_write_text
from .query import GraphView, QueryError, load_graph_view
from .sources import KnowledgeError, load_sources

PLUGIN_GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1"


class ObsidianExportError(ValueError):
    """Raised when the Obsidian graph feed cannot be built safely."""


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


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
            f"graph feed output overlaps registered source root: {spec.id}"
        )


def build_plugin_graph(view: GraphView) -> dict[str, Any]:
    """Build the typed, read-only graph consumed by the Obsidian plugin.

    Every entry and every accepted edge is included; staleness is reported by
    ``kgdistiller check`` and never filters the feed.
    """
    concepts = [
        {
            "id": node_id,
            "label": node["label"],
            "kind": node["kind"],
            "aliases": list(node["aliases"]),
            "authority": node["entry"],
            "understanding": node["understanding"],
        }
        for node_id, node in sorted(view.nodes.items())
    ]
    definitions = sorted(
        (
            {
                "source_authority": node["source"],
                "target": node_id,
                "line_start": node["line_start"],
                "line_end": node["line_end"],
            }
            for node_id, node in view.nodes.items()
        ),
        key=lambda item: (item["source_authority"], item["target"]),
    )
    sources = [
        {"authority": authority}
        for authority in sorted({item["source_authority"] for item in definitions})
    ]
    semantic_edges = [
        {
            "source": edge["source"],
            "relation": edge["relation"],
            "target": edge["target"],
            "evidence": edge["evidence"],
        }
        for edge in view.edges
    ]
    graph = {
        "schema": PLUGIN_GRAPH_SCHEMA,
        "counts": {
            "concepts": len(concepts),
            "sources": len(sources),
            "semantic_edges": len(semantic_edges),
            "definitions": len(definitions),
        },
        "concepts": concepts,
        "sources": sources,
        "semantic_edges": semantic_edges,
        "definitions": definitions,
    }
    try:
        return validate_contract(graph)
    except ContractError as error:
        raise ObsidianExportError(f"graph feed violates {PLUGIN_GRAPH_SCHEMA}: {error}") from error


def export_obsidian_graph(repo_root: Path, output_file: Path, *, registry: Path) -> dict[str, Any]:
    """Atomically write the plugin feed for the current entry store."""
    repo_root = repo_root.resolve()
    if output_file.is_symlink():
        raise ObsidianExportError("graph feed output must not be a symlink")
    if output_file.suffix.lower() != ".json":
        raise ObsidianExportError("graph feed output must be a .json file")
    if output_file.exists() and not output_file.is_file():
        raise ObsidianExportError("graph feed output is not an ordinary file")
    output_file = output_file.resolve()
    _validate_output_boundary(repo_root, output_file, registry)
    try:
        view = load_graph_view(repo_root, registry)
    except QueryError as error:
        raise ObsidianExportError(f"cannot load the entry store: {error}") from error
    plugin_graph = build_plugin_graph(view)
    atomic_write_text(output_file, _pretty_json(plugin_graph))
    return {
        "status": "exported",
        "output": str(output_file),
        "counts": plugin_graph["counts"],
    }
