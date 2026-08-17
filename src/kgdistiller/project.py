"""Project scaffolding for kgdistiller."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

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
    """Atomically ensure machine-local knowledge/build artifacts stay ignored."""
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
    alignments: Path | None = None,
    force: bool = False,
) -> None:
    if registry.exists() and not force:
        raise FileExistsError(f"project registry already exists: {registry}")
    resolved_source = source_root if source_root.is_absolute() else project_root / source_root
    resolved_source.mkdir(parents=True, exist_ok=True)
    ensure_vault_manifest(project_root)
    try:
        configured_root = resolved_source.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("source root must be inside the project") from error
    sources = [
        {
            "id": "local:notes",
            "subject": "local",
            "course": "notes",
            "knowledge_origin": "personal-note",
            "fields": ["general"],
            "root": configured_root,
            "files": ["**/*.md", "**/*.typ", "**/*.tex"],
            "web": "",
            "topics": [],
        }
    ]
    derived_imports = project_root / "knowledge/derived/imports"
    derived_imports.mkdir(parents=True, exist_ok=True)
    (project_root / "knowledge/derived/by-source").mkdir(
        parents=True, exist_ok=True
    )
    if resolved_source.resolve() not in (
        derived_imports.resolve(),
        *derived_imports.resolve().parents,
    ):
        sources.append(
            {
                "id": "local:derived-imports",
                "subject": "local",
                "course": "derived-imports",
                "knowledge_origin": "personal-note",
                "fields": ["general"],
                "root": "knowledge/derived/imports",
                "files": ["**/*.md"],
                "web": "",
                "topics": [],
            }
        )
        sources.append(
            {
                "id": "local:derived-pdf",
                "subject": "local",
                "course": "derived-pdf",
                "knowledge_origin": "personal-note",
                "fields": ["general"],
                "root": "knowledge/derived/by-source",
                "files": ["**/*.pdf.md"],
                "web": "",
                "topics": [],
            }
        )
    payload = {
        "schema": "kgdistiller-sources-v1",
        "fields": [
            {
                "id": "general",
                "label": "General Knowledge",
                "text": "Knowledge that has not yet been assigned a more specific field.",
            }
        ],
        "sources": sources,
    }
    registry.parent.mkdir(parents=True, exist_ok=True)
    default_knowledge = project_root / "knowledge"
    (default_knowledge / "entries").mkdir(parents=True, exist_ok=True)
    (default_knowledge / "derived").mkdir(parents=True, exist_ok=True)
    ensure_knowledge_gitignore(default_knowledge / ".gitignore")
    if registry.parent.resolve() != default_knowledge.resolve():
        ensure_knowledge_gitignore(registry.parent / ".gitignore")
    registry.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    alignment_path = alignments or registry.parent / "alignments.json"
    # Reviewed mappings are user curation. Even --force must not erase them.
    if not alignment_path.exists():
        alignment_path.parent.mkdir(parents=True, exist_ok=True)
        alignment_path.write_text(
            json.dumps(
                {"schema": "kgdistiller-alignments-v1", "mappings": []},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
