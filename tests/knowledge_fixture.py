"""Build temporary knowledge projects: registry, plain text sources, entries, edges."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kgdistiller.entries import cited_text, normalize_record, render_entry, split_lines
from kgdistiller.ingest import IngestPaths
from kgdistiller.knowledge_store import KnowledgeState, render_edges
from kgdistiller.query import GraphView
from kgdistiller.sources import SOURCE_SCHEMA

DEFAULT_SOURCE = {"id": "local:notes", "root": "notes", "files": ["**/*"]}


class KnowledgeFixture:
    """A project whose entries are rendered through the real entry writer."""

    def __init__(
        self,
        root: Path,
        *,
        sources: list[dict[str, Any]] | None = None,
        document_types: dict[str, Any] | None = None,
    ) -> None:
        self.root = root
        self.registry = root / ".knowledge/sources.json"
        self.sources = sources if sources is not None else [dict(DEFAULT_SOURCE)]
        self.document_types = document_types
        self.edges: list[dict[str, str]] = []
        for source in self.sources:
            (root / source["root"]).mkdir(parents=True, exist_ok=True)
        self.write_registry()

    @property
    def paths(self) -> IngestPaths:
        return IngestPaths(repo_root=self.root, registry=self.registry)

    def write_registry(self) -> None:
        payload: dict[str, Any] = {"schema": SOURCE_SCHEMA, "sources": self.sources}
        if self.document_types is not None:
            payload["document_types"] = self.document_types
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def write_source(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def lines(self, relative: str) -> list[str]:
        return split_lines((self.root / relative).read_text(encoding="utf-8"))

    def entry(
        self,
        entry_id: str,
        label: str,
        source: str,
        line_start: int,
        line_end: int | None = None,
        *,
        kind: str = "concept",
        aliases: list[str] | tuple[str, ...] = (),
        summary: str | None = None,
        understanding: str = "unknown",
        **sections: Any,
    ) -> dict[str, Any]:
        """Return an entry record whose Evidence quotes the cited source lines."""
        end = line_end if line_end is not None else line_start
        return {
            "id": entry_id,
            "label": label,
            "kind": kind,
            "aliases": list(aliases),
            "source": source,
            "line_start": line_start,
            "line_end": end,
            "understanding": understanding,
            "summary": summary or f"{label} as stated in the source.",
            **sections,
            "evidence": cited_text(self.lines(source), line_start, end),
        }

    def add_entry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        record = self.entry(*args, **kwargs)
        self.write_entry(record)
        return record

    def write_entry(self, record: dict[str, Any]) -> Path:
        path = self.root / ".knowledge/entries" / f"{record['id']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_entry(record), encoding="utf-8")
        return path

    def edge(
        self,
        source: str,
        relation: str,
        target: str,
        *,
        origin: str = "agent",
        confidence: str = "high",
        evidence: str = "Stated in the reviewed source.",
    ) -> dict[str, str]:
        return {"source": source, "relation": relation, "target": target,
                "origin": origin, "confidence": confidence, "evidence": evidence}

    def add_edge(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        edge = self.edge(*args, **kwargs)
        self.edges.append(edge)
        path = self.root / ".knowledge/edges.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_edges(self.edges), encoding="utf-8")
        return edge


def make_fixture(test: unittest.TestCase, **kwargs: Any) -> KnowledgeFixture:
    """Create a fixture in a temporary directory removed after ``test``."""
    directory = tempfile.TemporaryDirectory(prefix="kgdistiller-knowledge-")
    test.addCleanup(directory.cleanup)
    return KnowledgeFixture(Path(directory.name).resolve(), **kwargs)


def node_record(
    entry_id: str,
    label: str,
    text: str | None = None,
    *,
    aliases: list[str] | tuple[str, ...] = (),
    kind: str = "concept",
    source: str = "notes/fixture.txt",
    line_start: int = 1,
    line_end: int | None = None,
    understanding: str = "unknown",
    evidence: str | None = None,
    **sections: Any,
) -> dict[str, Any]:
    """Return a canonical in-memory entry record without touching the filesystem."""
    return normalize_record({
        "id": entry_id,
        "label": label,
        "kind": kind,
        "aliases": list(aliases),
        "source": source,
        "line_start": line_start,
        "line_end": line_end if line_end is not None else line_start,
        "understanding": understanding,
        "summary": text or f"{label}.",
        **sections,
        "evidence": evidence or label,
    })


def edge_record(
    source: str,
    relation: str,
    target: str,
    *,
    confidence: str = "high",
    evidence: str = "Stated in the reviewed source.",
    origin: str = "agent",
) -> dict[str, str]:
    return {"source": source, "relation": relation, "target": target,
            "origin": origin, "confidence": confidence, "evidence": evidence}


def memory_view(
    nodes: list[dict[str, Any]], edges: list[dict[str, str]] = (), root: Path = Path(".")
) -> GraphView:
    """Build a GraphView from records without a project on disk."""
    state = KnowledgeState(
        entries={record["id"]: record for record in nodes},
        edges={(edge["source"], edge["relation"], edge["target"]): edge for edge in edges},
    )
    return GraphView.from_state(root, state)
