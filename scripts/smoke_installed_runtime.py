"""Exercise the installed wheel end to end on a plain-text source and the entry store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kgdistiller.contracts import validate_contract

SOURCE = (
    "Chapter one\n"
    "A measure space is a triple (X, F, mu)\n"
    "with mu countably additive.\n"
    "测度论研究可测空间上的集合函数。\n"
)


def executable() -> Path:
    name = "kgdistiller.exe" if os.name == "nt" else "kgdistiller"
    command = Path(sys.executable).with_name(name)
    require(command.is_file(), f"installed console command is missing: {command}")
    return command


def run_command(*arguments: str, cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executable()), *arguments],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError(f"installed command returned non-object JSON: {arguments}")  # noqa: TRY004
    return value


def run_text(root: Path, *arguments: str, expect: int = 0) -> str:
    completed = subprocess.run(
        [str(executable()), "--repo-root", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != expect:
        raise RuntimeError(
            f"{' '.join(arguments)} exited {completed.returncode}, expected {expect}:\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed.stdout


def run(root: Path, *arguments: str) -> dict[str, Any]:
    value = json.loads(run_text(root, *arguments))
    if not isinstance(value, dict):
        raise RuntimeError(f"installed command returned non-object JSON: {arguments}")  # noqa: TRY004
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def capture(root: Path, workspace: Path, name: str, payload: dict[str, Any]) -> None:
    """Prepare one reviewed capture, then plan and apply its ingest request."""
    path = workspace / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    prepared = run(root, "capture", "prepare", str(path), "--output", "reviews")
    require(prepared.get("status") == "prepared", f"capture {name} was not prepared")
    planned = run(root, "ingest", "plan", prepared["artifacts"]["plan"])
    require(planned.get("status") == "planned", f"ingest plan for {name} failed")
    receipt = run(root, "ingest", "apply", prepared["artifacts"]["apply"])
    require(receipt.get("status") == "committed", f"ingest apply for {name} failed")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-wheel-runtime-") as raw:
        root = Path(raw) / "project"
        workspace = Path(raw) / "workspace"
        workspace.mkdir()
        initialized = run(root, "init", "--source-root", "notes", "--files", "*.txt")
        require(initialized.get("initialized") == str(root.resolve()), "init failed")
        (root / ".obsidian").mkdir()
        source = root / "notes/measure.txt"
        source.write_text(SOURCE, encoding="utf-8")
        scanned = run(root, "scan", "--file", "notes/measure.txt")
        require(scanned["files"][0]["lines"][1] == {"line": 2, "text": "A measure space is a triple (X, F, mu)"},
                "scan did not number the source lines")

        review = {"reviewer": "smoke", "evidence": "Read in the installed-wheel smoke test."}
        capture(root, workspace, "measure-space", {
            "label": "Measure space", "source": "notes/measure.txt", "line_start": 2, "line_end": 3,
            "kind": "definition", "aliases": ["测度空间"], "text": "A set with a sigma-algebra and a measure.",
            "review": {"action": "add", **review},
        })
        capture(root, workspace, "measure-theory", {
            "label": "测度论", "id": "measure-theory", "source": "notes/measure.txt", "line_start": 4,
            "line_end": 4, "kind": "concept", "text": "The study of measures.",
            "review": {"action": "add", **review},
        })
        require(run_text(root, "check") == "OK: 2 entries, 0 edges\n", "fresh store check failed")

        source.write_text("Preface\n\n" + SOURCE, encoding="utf-8")
        moved = run_text(root, "check", expect=1)
        require("moved measure-space: notes/measure.txt:2-3 -> 4-5" in moved, f"moved Evidence not reported: {moved}")
        fixed = run_text(root, "check", "--fix-lines")
        require(fixed.endswith("OK: 2 entries, 0 edges\n"), f"check --fix-lines failed: {fixed}")
        require("line_start: 4" in (root / ".knowledge/entries/measure-space.md").read_text(encoding="utf-8"),
                "fixed line range was not written")

        status = run(root, "agent", "status")
        require(status.get("counts") == {"entries": 2, "edges": 0}, "wrong store counts")
        resolved = json.loads(run_text(root, "agent", "resolve", "测度空间", "测度论"))
        require([row.get("candidate_ids") for row in resolved] == [["measure-space"], ["measure-theory"]],
                "identity resolution failed")
        searched = run(root, "agent", "search", "Measure space")
        require(searched["result"]["results"][0]["node_id"] == "measure-space", "lexical search failed")
        cjk = run(root, "agent", "search", "测度")
        require("measure-theory" in [row["node_id"] for row in cjk["result"]["results"]], "CJK search failed")

        state = Path(raw) / "user-state"
        outside = Path(raw) / "outside"
        outside.mkdir()
        registration = run_command(
            "--kgdistiller-home",
            str(state),
            "vault",
            "register",
            str(root),
            "--name",
            "research",
            cwd=outside,
        )
        require(registration.get("status") == "registered", "vault registration failed")
        plugin = run_command(
            "--kgdistiller-home",
            str(state),
            "--vault",
            "research",
            "obsidian",
            "install",
            cwd=outside,
        )
        require(
            plugin.get("schema") == "kgdistiller-obsidian-plugin-install-v1",
            "Obsidian plugin install failed",
        )
        require(
            plugin.get("status") == "installed",
            "Obsidian plugin install status mismatch",
        )
        for name in ("main.js", "manifest.json", "styles.css"):
            require(
                (root / ".obsidian/plugins/kgdistiller" / name).is_file(),
                f"installed Obsidian plugin is missing {name}",
            )
        require(
            json.loads(
                (root / ".obsidian/community-plugins.json").read_text(
                    encoding="utf-8"
                )
            )
            == ["kgdistiller"],
            "Obsidian plugin was not configured as enabled",
        )
        registered_status = run_command(
            "--kgdistiller-home",
            str(state),
            "--vault",
            "research",
            "agent",
            "status",
            cwd=outside,
        )
        require(registered_status == status, "registered vault lookup failed")
        default_status = run_command(
            "--kgdistiller-home",
            str(state),
            "agent",
            "status",
            cwd=outside,
        )
        require(default_status == status, "default vault lookup failed")
        require((root / ".knowledge/vault.json").is_file(), "portable vault identity missing")
        require((state / "vaults.json").is_file(), "machine-local vault registry missing")

        feed = run(root, "export", "obsidian")
        require(feed.get("status") == "exported", "Obsidian graph feed export failed")
        feed_path = root / ".knowledge/build/obsidian/semantic-graph.json"
        require(feed_path.is_file(), "Obsidian graph feed is missing")
        graph = validate_contract(json.loads(feed_path.read_text(encoding="utf-8")))
        require(graph["schema"] == "kgdistiller-obsidian-graph-v1", "Obsidian graph feed schema mismatch")
        require(graph["counts"] == {"concepts": 2, "sources": 1, "semantic_edges": 0, "definitions": 2},
                "Obsidian graph feed counts mismatch")

        require(not any(root.rglob("*.sqlite")), "self-contained runtime created SQLite")

    print(
        "installed command, capture and ingest, check --fix-lines, CJK search, vault registry, "
        "Obsidian plugin and graph feed smoke passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
