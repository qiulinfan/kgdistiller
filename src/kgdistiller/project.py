"""Project scaffolding for kgdistiller."""

from __future__ import annotations

import json
from pathlib import Path


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
    try:
        configured_root = resolved_source.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("source root must be inside the project") from error
    payload = {
        "schema": "qlkg-sources-v2",
        "fields": [
            {
                "id": "general",
                "label": "General Knowledge",
                "text": "Knowledge that has not yet been assigned a more specific field.",
            }
        ],
        "sources": [
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
        ],
    }
    registry.parent.mkdir(parents=True, exist_ok=True)
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
                {"schema": "qlkg-alignments-v1", "mappings": []},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
