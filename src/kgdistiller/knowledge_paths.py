"""Locate the project knowledge tree, <root>/.knowledge/."""

from __future__ import annotations

from pathlib import Path

KNOWLEDGE_DIRECTORY = ".knowledge"


def knowledge_root(repo_root: Path) -> Path:
    root = repo_root / KNOWLEDGE_DIRECTORY
    if root.is_symlink():
        raise ValueError("knowledge tree must not be a symlink")
    return root
