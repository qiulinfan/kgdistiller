"""Project scaffolding for kgdistiller."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .knowledge_paths import knowledge_root
from .knowledge_store import edges_file, entries_root
from .sources import SOURCE_SCHEMA
from .vault_registry import ensure_vault_manifest

_BUILD_IGNORE_RULES = {
    b"build",
    b"build/",
    b"build/*",
    b"build/**",
    b"/build",
    b"/build/",
    b"/build/*",
    b"/build/**",
    b"**/build",
    b"**/build/",
}


def _has_effective_build_ignore(content: bytes) -> bool:
    """Return whether an explicit build rule follows every possible negation."""
    rules = []
    for raw_line in content.split(b"\n"):
        # Git treats CR as a line ending only when it is the CR in CRLF. A bare
        # CR remains part of the pattern and must not manufacture another rule.
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
        rules.append(line.rstrip(b" \t"))
    last_negation = max(
        (index for index, rule in enumerate(rules) if rule.startswith(b"!")),
        default=-1,
    )
    return any(
        rule in _BUILD_IGNORE_RULES
        for rule in rules[last_negation + 1 :]
        if rule and not rule.startswith(b"#")
    )


def _atomic_write_bytes(path: Path, content: bytes, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            target_mode = 0o644 if mode is None else mode
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), target_mode)
            else:
                os.chmod(temporary_name, target_mode)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def ensure_knowledge_gitignore(path: Path) -> bool:
    """Atomically ensure machine-local .knowledge/build artifacts stay ignored."""
    original_mode: int | None = None
    try:
        with path.open("rb") as handle:
            original = handle.read()
            original_mode = stat.S_IMODE(os.fstat(handle.fileno()).st_mode)
    except FileNotFoundError:
        original = b""
    if _has_effective_build_ignore(original):
        return False
    separator = b"" if not original or original.endswith(b"\n") else b"\n"
    _atomic_write_bytes(
        path,
        original + separator + b"build/\n",
        mode=original_mode,
    )
    return True


def initialize_project(
    project_root: Path,
    registry: Path,
    *,
    source_root: Path,
    files: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create the source registry and an empty entry store; nothing is scanned."""
    if registry.exists() and not force:
        raise FileExistsError(f"project registry already exists: {registry}")
    patterns = list(files) if files else ["**/*"]
    if any(not isinstance(item, str) or not item for item in patterns):
        raise ValueError("source file patterns must be nonempty strings")
    resolved_source = source_root if source_root.is_absolute() else project_root / source_root
    try:
        configured_root = resolved_source.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("source root must be inside the project") from error
    resolved_source.mkdir(parents=True, exist_ok=True)
    ensure_vault_manifest(project_root)
    payload = {
        "schema": SOURCE_SCHEMA,
        "sources": [{"id": "local:notes", "root": configured_root, "files": patterns}],
    }
    knowledge = knowledge_root(project_root)
    entries_root(project_root).mkdir(parents=True, exist_ok=True)
    edges = edges_file(project_root)
    if not edges.exists():
        edges.write_text("", encoding="utf-8")
    ensure_knowledge_gitignore(knowledge / ".gitignore")
    registry.parent.mkdir(parents=True, exist_ok=True)
    if registry.parent.resolve() != knowledge.resolve():
        ensure_knowledge_gitignore(registry.parent / ".gitignore")
    registry.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "initialized": str(project_root),
        "registry": str(registry),
        "source_root": configured_root,
        "files": patterns,
    }
