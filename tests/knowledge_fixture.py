"""Build temporary knowledge bases: a home with source globs and types, plain text sources and record files.

Also a deterministic pure-Python fake encoder, so index and search tests never load a model.
"""

from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller.home import HOME_GITIGNORE

BASE_NAME = "kb"


def type_document(spec: dict[str, Any]) -> str:
    """Render a document type file; JSON flow collections are valid YAML."""
    lines = ["---", f"node_kinds: {json.dumps(spec['node_kinds'])}"]
    if "relation_kinds" in spec:
        lines.append(f"relation_kinds: {json.dumps(spec['relation_kinds'])}")
    if "epistemic" in spec:
        lines.append(f"epistemic: {json.dumps(spec['epistemic'])}")
    lines += ["---", "", spec.get("guidance", "Fixture guidance."), ""]
    return "\n".join(lines)


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


# ---------------------------------------------------------------- record-format fixtures

RECORD_TYPES: dict[str, dict[str, Any]] = {
    "math": {
        "node_kinds": ["definition", "theorem", "concept"],
        "relation_kinds": {
            "implies": ["premise", "conclusion"],
            "equivalent": ["side"],
            "example": ["uses", "setting"],
            "contrasts": ["subject", "contrast", "witness"],
        },
        "epistemic": ["proved", "stated"],
        "guidance": "Definitions and named theorems are nodes; statements connecting them are relations.",
    },
}
RECORD_SOURCES = {"notes/**/*.txt": "math"}


def render_record(frontmatter: str, body: str = "", quotes: list[str] | tuple[str, ...] = ()) -> str:
    """A record file: frontmatter text, body text, then an Evidence section of the quotes."""
    text = f"---\n{frontmatter.strip(chr(10))}\n---\n{body.strip(chr(10))}\n"
    if quotes:
        blocks = ["\n".join(f"> {line}" if line else ">" for line in quote.split("\n")) for quote in quotes]
        text += "\n## Evidence\n\n" + "\n\n".join(blocks) + "\n"
    return text


def write_record(
    root: Path,
    id: str,
    frontmatter: str,
    body: str,
    quotes: list[str] | tuple[str, ...],
    folder: str = "entries",
) -> Path:
    """Write ``<root>/.knowledge/<folder>/<id>.md`` textually, exactly as given."""
    path = root / ".knowledge" / folder / f"{id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record(frontmatter, body, quotes), encoding="utf-8")
    return path


class RecordHome:
    """Bases registered in a temporary KGDISTILLER_HOME with record-format types and source globs."""

    def __init__(
        self,
        home: Path,
        roots: dict[str, Path],
        *,
        sources: dict[str, str],
        types: dict[str, dict[str, Any]],
        embedding: str | None = None,
    ) -> None:
        self.home = home
        self.roots = roots
        self.sources = {name: dict(sources) for name in roots}
        self.types = dict(types)
        self.embedding = embedding
        for root in roots.values():
            (root / ".knowledge/entries").mkdir(parents=True, exist_ok=True)
        self.write_config()

    @property
    def root(self) -> Path:
        return next(iter(self.roots.values()))

    def write_config(self) -> None:
        types = self.home / "types"
        types.mkdir(parents=True, exist_ok=True)
        for stale in types.glob("*.md"):
            if stale.stem not in self.types:
                stale.unlink()
        for name, spec in self.types.items():
            (types / f"{name}.md").write_text(type_document(spec), encoding="utf-8")
        bases = {name: {"path": str(root), "sources": self.sources[name]} for name, root in self.roots.items()}
        config = {"bases": bases, "embedding": self.embedding}
        (self.home / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (self.home / ".gitignore").write_text(HOME_GITIGNORE, encoding="utf-8")

    def write_source(self, relative: str, text: str, base: str = BASE_NAME) -> Path:
        path = self.roots[base] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_record(
        self,
        id: str,
        frontmatter: str,
        body: str = "",
        quotes: list[str] | tuple[str, ...] = (),
        *,
        folder: str = "entries",
        base: str = BASE_NAME,
    ) -> Path:
        return write_record(self.roots[base], id, frontmatter, body, quotes, folder)

    def path(self, id: str, folder: str = "entries", base: str = BASE_NAME) -> Path:
        return self.roots[base] / ".knowledge" / folder / f"{id}.md"


def make_record_home(
    test: unittest.TestCase,
    *,
    bases: tuple[str, ...] = (BASE_NAME,),
    sources: dict[str, str] | None = None,
    types: dict[str, dict[str, Any]] | None = None,
    embedding: str | None = None,
) -> RecordHome:
    """Register ``bases`` (roots beside the home) with a record-format type and glob."""
    home = use_temporary_home(test)
    roots = {}
    for name in bases:
        roots[name] = home.parent / name
        roots[name].mkdir()
    return RecordHome(
        home,
        roots,
        sources=RECORD_SOURCES if sources is None else sources,
        types=RECORD_TYPES if types is None else types,
        embedding=embedding,
    )


# ---------------------------------------------------------------- fake encoder

FAKE_DIMENSION = 32


class FakeEncoder:
    """A deterministic encoder: a character histogram of the casefolded text, L2-normalized, as '<f4' bytes."""

    def __init__(self, model: str, max_chars: int | None = None,
                 on_encode: Callable[[list[str]], None] | None = None) -> None:
        self.model = model
        self.max_chars = max_chars
        self.on_encode = on_encode
        self.calls: list[int] = []

    @staticmethod
    def vector(text: str) -> bytes:
        counts = [0.0] * FAKE_DIMENSION
        for char in text.casefold():
            counts[ord(char) % FAKE_DIMENSION] += 1.0
        norm = math.sqrt(sum(value * value for value in counts))
        if norm:
            counts = [value / norm for value in counts]
        return struct.pack(f"<{FAKE_DIMENSION}f", *counts)

    def over_limit(self, texts: list[str]) -> list[int]:
        if self.max_chars is None:
            return []
        return [position for position, text in enumerate(texts) if len(text) > self.max_chars]

    def encode_documents(self, texts: list[str]) -> list[bytes]:
        if self.on_encode is not None:
            self.on_encode(texts)
        self.calls.append(len(texts))
        return [self.vector(text) for text in texts]

    def encode_query(self, text: str) -> bytes:
        return self.vector(text)


class FakeEncoders:
    """What ``fake_encoder`` yields: the models requested in order and one encoder per model id."""

    def __init__(self, on_encode: Callable[[list[str]], None] | None, max_chars: int | None) -> None:
        self.on_encode = on_encode
        self.max_chars = max_chars
        self.loads: list[str] = []
        self.encoders: dict[str, FakeEncoder] = {}

    def __call__(self, model: str) -> FakeEncoder:
        self.loads.append(model)
        if model not in self.encoders:
            self.encoders[model] = FakeEncoder(model, self.max_chars, self.on_encode)
        return self.encoders[model]


@contextmanager
def fake_encoder(on_encode: Callable[[list[str]], None] | None = None,
                 max_chars: int | None = None) -> Iterator[FakeEncoders]:
    """Patch the adapter's ``encoder`` seam with fake encoders; ``on_encode`` runs before each document batch."""
    factory = FakeEncoders(on_encode, max_chars)
    with patch("kgdistiller.adapters.sentence_transformers.encoder", factory):
        yield factory
