#!/usr/bin/env python3
"""Exercise the self-contained JSON runtime through an installed wheel."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run_text(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgdistiller",
            "--repo-root",
            str(root),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def run(root: Path, *arguments: str) -> dict[str, Any]:
    value = json.loads(run_text(root, *arguments))
    if not isinstance(value, dict):
        raise RuntimeError(f"installed command returned non-object JSON: {arguments}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-wheel-runtime-") as raw:
        root = Path(raw) / "project"
        initialized = run(root, "init", "--source-root", "notes")
        require(initialized.get("initialized") == str(root.resolve()), "init failed")
        authority = root / "notes/concepts.md"
        authority.write_text(
            "> **Definition: --[[Measure]]--**\n>\n"
            "> A countably additive set function.\n",
            encoding="utf-8",
        )

        synchronized = run(root, "sync")
        require(synchronized.get("definitions") == 1, "sync omitted authority definition")
        require(run_text(root, "check").startswith("OK: qlkg-v3"), "graph check failed")

        status = run(root, "agent", "status")
        require(status.get("backend") == "json-memory", "wrong query backend")
        require("json-memory" in status.get("capabilities", []), "runtime capability missing")
        resolved = json.loads(run_text(root, "agent", "resolve", "Measure"))
        require(isinstance(resolved, list) and resolved[0].get("candidate_ids") == ["measure"], "identity resolution failed")
        searched = run(root, "agent", "search", "Measure")
        require(searched.get("result", {}).get("results", [{}])[0].get("node_id") == "measure", "lexical search failed")

        store = run(root, "store", "snapshot")
        require(store.get("schema") == "qlkg-store-report-v1", "store-v2 snapshot failed")
        require(store.get("artifact_schema") == "qlkg-store-v2", "store-v2 report mismatch")
        require(run(root, "store", "verify").get("status") == "verified", "store verify failed")
        projection = run(root, "export", "obsidian")
        require(projection.get("schema") == "qlkg-obsidian-export-report-v1", "Obsidian export failed")
        require(projection.get("artifact_schema") == "qlkg-obsidian-projection-v1", "Obsidian report mismatch")
        require(
            (root / "knowledge/build/obsidian/concepts/Measure.md").is_file(),
            "raw Markdown Wikilink target projection missing",
        )

        require(not any(root.rglob("*.sqlite")), "self-contained runtime created SQLite")

    print("installed JSON runtime, store-v2, and Obsidian projection smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
