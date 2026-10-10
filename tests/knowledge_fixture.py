"""Build temporary knowledge bases: a home with source globs and types, plain text sources, entries, edges."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller.entries import cited_text, normalize_record, render_entry, split_lines
from kgdistiller.home import HOME_GITIGNORE, Base, resolve_base
from kgdistiller.ingest import IngestPaths
from kgdistiller.knowledge_store import KnowledgeState, render_edges
from kgdistiller.query import GraphView

BASE_NAME = "kb"
DEFAULT_SOURCES = {"notes/**/*": "fixture"}
FIXTURE_KINDS = [
    "concept", "definition", "theorem", "method", "note", "term", "construction",
    "axiom", "lemma", "proposition", "corollary", "example",
]
DEFAULT_TYPES: dict[str, dict[str, Any]] = {
    "fixture": {"node_kinds": FIXTURE_KINDS, "guidance": "Extract every stated idea."},
}


def type_document(spec: dict[str, Any]) -> str:
    """Render a document type file; JSON flow collections are valid YAML."""
    lines = ["---", f"node_kinds: {json.dumps(spec['node_kinds'])}"]
    if "relation_kinds" in spec:
        lines.append(f"relation_kinds: {json.dumps(spec['relation_kinds'])}")
    if "epistemic" in spec:
        lines.append(f"epistemic: {json.dumps(spec['epistemic'])}")
    lines += ["---", "", spec.get("guidance", "Fixture guidance."), ""]
    return "\n".join(lines)


class KnowledgeFixture:
    """Base ``kb`` registered in ``home``; entries are rendered through the real entry writer."""

    def __init__(
        self,
        root: Path,
        home: Path,
        *,
        sources: dict[str, str] | None = None,
        types: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.root = root
        self.home = home
        self.sources = dict(sources) if sources is not None else dict(DEFAULT_SOURCES)
        self.types = dict(types) if types is not None else dict(DEFAULT_TYPES)
        self.edges: list[dict[str, str]] = []
        (root / ".knowledge/entries").mkdir(parents=True, exist_ok=True)
        (root / "notes").mkdir(exist_ok=True)
        self.write_home()

    @property
    def base(self) -> Base:
        return resolve_base(BASE_NAME, self.root)

    @property
    def paths(self) -> IngestPaths:
        return IngestPaths(self.base)

    def write_home(self) -> None:
        types = self.home / "types"
        types.mkdir(parents=True, exist_ok=True)
        for stale in types.glob("*.md"):
            if stale.stem not in self.types:
                stale.unlink()
        for name, spec in self.types.items():
            (types / f"{name}.md").write_text(type_document(spec), encoding="utf-8")
        config = {"bases": {BASE_NAME: {"path": str(self.root), "sources": self.sources}}, "embedding": None}
        (self.home / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (self.home / ".gitignore").write_text(HOME_GITIGNORE, encoding="utf-8")

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


def use_temporary_home(test: unittest.TestCase) -> Path:
    """Point KGDISTILLER_HOME at a fresh, not yet created directory for ``test``.

    The home path is realpath-resolved; tests never touch the real ~/.knowledge.
    """
    directory = tempfile.TemporaryDirectory(prefix="kgdistiller-home-")
    test.addCleanup(directory.cleanup)
    home = Path(directory.name).resolve() / "home"
    environment = patch.dict(os.environ, {"KGDISTILLER_HOME": str(home)})
    environment.start()
    test.addCleanup(environment.stop)
    return home


def make_fixture(test: unittest.TestCase, **kwargs: Any) -> KnowledgeFixture:
    """Create base ``kb`` at <tmp>/kb beside a temporary home <tmp>/home, removed after ``test``."""
    home = use_temporary_home(test)
    root = home.parent / BASE_NAME
    root.mkdir()
    return KnowledgeFixture(root, home, **kwargs)


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
