"""The knowledge source registry: any registered UTF-8 text document is a source.

kgdistiller never parses a source's syntax. A registered source is a root
directory plus glob patterns; every admitted file is read as plain text with
1-based line numbers, whatever its extension.
"""

from __future__ import annotations

import copy
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .knowledge_paths import KNOWLEDGE_DIRECTORY

SOURCE_SCHEMA = "kgdistiller-sources-v1"
SOURCE_REGISTRY_KEYS = {"schema", "sources", "document_types"}
SOURCE_KEYS = {"id", "root", "files", "document_type"}


class KnowledgeError(RuntimeError):
    """Raised when the knowledge contract cannot be satisfied."""


@dataclass(frozen=True)
class SourceSpec:
    id: str
    root: Path
    patterns: tuple[str, ...]
    document_type: str = ""


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def load_sources(repo_root: Path, registry: Path) -> list[SourceSpec]:
    from .document_types import parse_document_types, validate_document_type

    payload = read_json(registry, {})
    if not isinstance(payload, dict) or payload.get("schema") != SOURCE_SCHEMA:
        raise KnowledgeError(f"expected {SOURCE_SCHEMA} source registry: {registry}")
    unknown = sorted(set(payload) - SOURCE_REGISTRY_KEYS)
    if unknown:
        raise KnowledgeError(f"unknown source registry key {unknown[0]!r}: {registry}")
    profiles = parse_document_types(payload)
    repository = repo_root.resolve()
    result: list[SourceSpec] = []
    seen: set[str] = set()
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        raise KnowledgeError(f"source registry sources must be a list: {registry}")
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise KnowledgeError(f"source registry entries must be objects: {registry}")
        unknown = sorted(set(raw) - SOURCE_KEYS)
        if unknown:
            raise KnowledgeError(f"unknown key {unknown[0]!r} in source {raw.get('id', '')!r}")
        source_id = str(raw.get("id", ""))
        if not source_id or source_id in seen:
            raise KnowledgeError(f"duplicate or empty source id: {source_id!r}")
        seen.add(source_id)
        root_value = raw.get("root")
        if not isinstance(root_value, str) or not root_value:
            raise KnowledgeError(f"source {source_id} has no portable relative root")
        relative_root = Path(root_value)
        if relative_root.is_absolute() or ".." in relative_root.parts:
            raise KnowledgeError(
                f"source root must be a portable relative path for {source_id}: {root_value}"
            )
        if KNOWLEDGE_DIRECTORY in relative_root.parts:
            raise KnowledgeError(
                f"source root must be outside the {KNOWLEDGE_DIRECTORY}/ tree for {source_id}"
            )
        lexical_root = repository / relative_root
        root = lexical_root.resolve()
        try:
            root.relative_to(repository)
        except ValueError as error:
            raise KnowledgeError(
                f"source root escapes repository for {source_id}: {root}"
            ) from error
        if lexical_root != root:
            raise KnowledgeError(
                f"source root must not traverse a symlink for {source_id}: {root_value}"
            )
        if not root.is_dir():
            raise KnowledgeError(f"missing source root for {source_id}: {root}")
        files = raw.get("files", [])
        if not isinstance(files, list) or not files or not all(
            isinstance(item, str) and item for item in files
        ):
            raise KnowledgeError(f"source {source_id} has no bounded file patterns")
        result.append(
            SourceSpec(
                id=source_id,
                root=root,
                patterns=tuple(files),
                document_type=(validate_document_type(raw["document_type"], profiles)
                               if "document_type" in raw else ""),
            )
        )
    return result


def glob_matches_path(relative: Path, pattern: str) -> bool:
    """Match one relative path with ``Path.glob`` segment semantics."""
    path_parts = relative.as_posix().split("/")
    pattern_parts = Path(pattern).as_posix().split("/")

    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], segment)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def source_admits(spec: SourceSpec, path: Path) -> bool:
    """Return whether a registered source's globs admit ``path``."""
    try:
        relative = path.resolve().relative_to(spec.root)
    except ValueError:
        return False
    if KNOWLEDGE_DIRECTORY in relative.parts:
        return False
    return any(glob_matches_path(relative, pattern) for pattern in spec.patterns)


def source_for_path(specs: list[SourceSpec], path: Path) -> SourceSpec:
    """Return the single registered source that admits ``path``."""
    owners = [spec for spec in specs if source_admits(spec, path)]
    if len(owners) == 1:
        return owners[0]
    if owners:
        raise KnowledgeError(
            f"source file is admitted by several registered sources: {path} "
            f"({', '.join(sorted(spec.id for spec in owners))})"
        )
    raise KnowledgeError(f"source file is not admitted by any registered source: {path}")
