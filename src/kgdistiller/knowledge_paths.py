"""Locate the single knowledge tree of a project, visible or hidden."""

from __future__ import annotations

from pathlib import Path


def knowledge_root(repo_root: Path) -> Path:
    candidates = [repo_root / name for name in ("knowledge", ".knowledge")]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(present) > 1:
        raise ValueError("both knowledge/ and .knowledge/ exist; select one knowledge tree")
    root = present[0] if present else candidates[0]
    if root.is_symlink():
        raise ValueError("knowledge tree must not be a symlink")
    return root


def knowledge_relative(repo_root: Path, path: str | Path) -> Path:
    """Resolve a product default within this project's selected tree."""
    relative = Path(path)
    if relative.parts and relative.parts[0] == "knowledge":
        return Path(knowledge_root(repo_root).name).joinpath(*relative.parts[1:])
    return relative
