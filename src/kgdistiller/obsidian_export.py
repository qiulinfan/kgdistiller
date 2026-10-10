"""Write the Obsidian plugin's typed graph feed from the entry store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ContractError, validate_contract
from .home import KNOWLEDGE_DIRECTORY, Base, atomic_write_text
from .knowledge_store import entries_root
from .query import GraphView, QueryError, load_graph_view

PLUGIN_GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1"


class ObsidianExportError(ValueError):
    """Raised when the Obsidian graph feed cannot be built safely."""


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _validate_output_boundary(base: Base, output: Path) -> None:
    """Keep the feed out of the base's sources whether or not the file exists yet.

    Inside the base root the feed may only live under the hidden knowledge tree,
    which no source glob can match, and never among the committed entries.
    """
    if ".obsidian" in {part.casefold() for part in output.parts}:
        raise ObsidianExportError("graph feed output cannot be inside .obsidian")
    if not output.is_relative_to(base.root):
        return
    knowledge = base.root / KNOWLEDGE_DIRECTORY
    if not output.is_relative_to(knowledge) or output.is_relative_to(entries_root(base.root)):
        raise ObsidianExportError(
            f"graph feed output inside base {base.name} must lie under {KNOWLEDGE_DIRECTORY}/, "
            "outside entries/, where no source glob matches"
        )


def build_plugin_graph(view: GraphView) -> dict[str, Any]:
    """Build the typed, read-only graph consumed by the Obsidian plugin.

    Every entry and every accepted edge is included; staleness is reported by
    ``kgd check`` and never filters the feed.
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


def export_obsidian_graph(base: Base, output_file: Path) -> dict[str, Any]:
    """Atomically write the plugin feed for the current entry store."""
    if output_file.is_symlink():
        raise ObsidianExportError("graph feed output must not be a symlink")
    if output_file.suffix.lower() != ".json":
        raise ObsidianExportError("graph feed output must be a .json file")
    if output_file.exists() and not output_file.is_file():
        raise ObsidianExportError("graph feed output is not an ordinary file")
    output_file = output_file.resolve()
    _validate_output_boundary(base, output_file)
    try:
        view = load_graph_view(base)
    except QueryError as error:
        raise ObsidianExportError(f"cannot load the entry store: {error}") from error
    plugin_graph = build_plugin_graph(view)
    atomic_write_text(output_file, _pretty_json(plugin_graph))
    return {
        "status": "exported",
        "output": str(output_file),
        "counts": plugin_graph["counts"],
    }
