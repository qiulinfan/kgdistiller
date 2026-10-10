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


def run_text(*arguments: str, cwd: Path, env: dict[str, str], expect: int = 0) -> str:
    completed = subprocess.run(
        [str(executable()), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
    )
    if completed.returncode != expect:
        raise RuntimeError(
            f"{' '.join(arguments)} exited {completed.returncode}, expected {expect}:\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed.stdout if expect == 0 else completed.stdout + completed.stderr


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class Runtime:
    """Run the installed command with a temporary home and a temporary user directory."""

    def __init__(self, temporary: Path) -> None:
        self.home = temporary / "home"
        self.user = temporary / "user"
        self.user.mkdir()
        self.env = dict(os.environ, KGDISTILLER_HOME=str(self.home), HOME=str(self.user),
                        USERPROFILE=str(self.user))

    def text(self, *arguments: str, cwd: Path, expect: int = 0) -> str:
        return run_text(*arguments, cwd=cwd, env=self.env, expect=expect)

    def json(self, *arguments: str, cwd: Path) -> Any:
        return json.loads(self.text(*arguments, cwd=cwd))

    def object(self, *arguments: str, cwd: Path) -> dict[str, Any]:
        value = self.json(*arguments, cwd=cwd)
        if not isinstance(value, dict):
            raise RuntimeError(f"installed command returned non-object JSON: {arguments}")  # noqa: TRY004
        return value


def capture(runtime: Runtime, root: Path, workspace: Path, name: str, payload: dict[str, Any]) -> None:
    """Prepare one reviewed capture, then plan and apply its ingest request."""
    path = workspace / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    prepared = runtime.object("capture", "prepare", str(path), "--output", ".knowledge/build/reviews",
                              "--base", "research", cwd=root)
    require(prepared.get("status") == "prepared", f"capture {name} was not prepared")
    planned = runtime.object("ingest", "plan", prepared["artifacts"]["plan"], "--base", "research", cwd=workspace)
    require(planned.get("status") == "planned", f"ingest plan for {name} failed")
    receipt = runtime.object("ingest", "apply", prepared["artifacts"]["apply"], cwd=root)
    require(receipt.get("status") == "committed", f"ingest apply for {name} failed")


def register(runtime: Runtime, root: Path, outside: Path) -> None:
    """Register the base, then map its .txt notes to a user-defined document type."""
    added = runtime.object("base", "add", str(root), "--name", "research", cwd=outside)
    require(added.get("added", {}).get("name") == "research", "base add failed")
    require((runtime.home / "config.json").is_file(), "home config.json is missing")
    require((runtime.home / "types").is_dir(), "home types/ is missing")
    require((runtime.home / ".gitignore").read_text(encoding="utf-8") == "index.sqlite*\nlock\n",
            "home .gitignore mismatch")
    require((root / ".knowledge/entries").is_dir(), "base add did not create .knowledge/entries")
    require([path.name for path in (root / ".knowledge").iterdir()] == ["entries"],
            "base add wrote more than .knowledge/entries/ into the base")
    require(not (runtime.user / ".knowledge").exists(), "state was created in the default user home")
    config_path = runtime.home / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["bases"]["research"]["sources"] = {"notes/*.txt": "smoke-notes"}
    staged = config_path.with_name(".config.json.smoke")
    staged.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, config_path)
    (runtime.home / "types/smoke-notes.md").write_text(
        "---\nnode_kinds: [definition, concept]\n---\n\nExtract every defined term and named idea.\n",
        encoding="utf-8",
    )
    listed = runtime.object("base", "list", cwd=outside)
    require([item["name"] for item in listed["bases"]] == ["research"], "base list mismatch")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-wheel-runtime-") as raw:
        temporary = Path(raw).resolve()
        runtime = Runtime(temporary)
        root = temporary / "research"
        workspace = temporary / "workspace"
        outside = temporary / "outside"
        for directory in (root, workspace, outside):
            directory.mkdir()
        refused = runtime.text("agent", "status", cwd=outside, expect=1)
        require("kgd base add" in refused, f"missing home was not reported: {refused}")
        require(not runtime.home.exists(), "a read created the home")
        register(runtime, root, outside)
        (root / ".obsidian").mkdir()
        source = root / "notes/measure.txt"
        source.parent.mkdir()
        source.write_text(SOURCE, encoding="utf-8")
        scanned = runtime.object("scan", "--file", "notes/measure.txt", cwd=root)
        require(scanned["files"][0]["lines"][1] == {"line": 2, "text": "A measure space is a triple (X, F, mu)"},
                "scan did not number the source lines")
        require(scanned["files"][0]["type"] == "smoke-notes", "scan did not report the document type")

        review = {"reviewer": "smoke", "evidence": "Read in the installed-wheel smoke test."}
        capture(runtime, root, workspace, "measure-space", {
            "label": "Measure space", "source": "notes/measure.txt", "line_start": 2, "line_end": 3,
            "kind": "definition", "aliases": ["测度空间"], "text": "A set with a sigma-algebra and a measure.",
            "review": {"action": "add", **review},
        })
        capture(runtime, root, workspace, "measure-theory", {
            "label": "测度论", "id": "measure-theory", "source": "notes/measure.txt", "line_start": 4,
            "line_end": 4, "kind": "concept", "text": "The study of measures.",
            "review": {"action": "add", **review},
        })
        require(runtime.text("check", "--base", "research", cwd=outside) == "OK: 2 entries, 0 edges\n",
                "fresh store check failed")

        source.write_text("Preface\n\n" + SOURCE, encoding="utf-8")
        moved = runtime.text("check", cwd=root / "notes", expect=1)
        require("moved measure-space: notes/measure.txt:2-3 -> 4-5" in moved, f"moved Evidence not reported: {moved}")
        fixed = runtime.text("check", "--fix-lines", "--base", "research", cwd=outside)
        require("fixed measure-space: notes/measure.txt:2-3 -> 4-5" in fixed, f"moved Evidence not fixed: {fixed}")
        require(fixed.endswith("OK: 2 entries, 0 edges\n"), f"check --fix-lines failed: {fixed}")
        require("line_start: 4" in (root / ".knowledge/entries/measure-space.md").read_text(encoding="utf-8"),
                "fixed line range was not written")

        status = runtime.object("agent", "status", "--base", "research", cwd=outside)
        require(status.get("counts") == {"entries": 2, "edges": 0}, "wrong store counts")
        require(runtime.object("agent", "status", cwd=root / "notes") == status, "cwd-inside-root lookup failed")
        outside_refusal = runtime.text("agent", "status", cwd=outside, expect=1)
        require("not inside any registered base root" in outside_refusal and "research" in outside_refusal,
                f"a cwd outside every base was not refused: {outside_refusal}")
        resolved = runtime.json("agent", "resolve", "测度空间", "测度论", "--base", "research", cwd=outside)
        require([row.get("candidate_ids") for row in resolved] == [["measure-space"], ["measure-theory"]],
                "identity resolution failed")
        searched = runtime.object("agent", "search", "Measure space", "--base", "research", cwd=outside)
        require(searched["result"]["results"][0]["node_id"] == "measure-space", "lexical search failed")
        cjk = runtime.object("agent", "search", "测度", cwd=root)
        require("measure-theory" in [row["node_id"] for row in cjk["result"]["results"]], "CJK search failed")

        plugin = runtime.object("obsidian", "install", "--base", "research", cwd=outside)
        require(plugin.get("schema") == "kgdistiller-obsidian-plugin-install-v1", "Obsidian plugin install failed")
        require(plugin.get("status") == "installed", "Obsidian plugin install status mismatch")
        for name in ("main.js", "manifest.json", "styles.css"):
            require((root / ".obsidian/plugins/kgdistiller" / name).is_file(),
                    f"installed Obsidian plugin is missing {name}")
        require(json.loads((root / ".obsidian/community-plugins.json").read_text(encoding="utf-8"))
                == ["kgdistiller"], "Obsidian plugin was not configured as enabled")

        feed = runtime.object("export", "obsidian", "--base", "research", cwd=outside)
        require(feed.get("status") == "exported", "Obsidian graph feed export failed")
        feed_path = root / ".knowledge/build/obsidian/semantic-graph.json"
        require(feed_path.is_file(), "Obsidian graph feed is missing")
        graph = validate_contract(json.loads(feed_path.read_text(encoding="utf-8")))
        require(graph["schema"] == "kgdistiller-obsidian-graph-v1", "Obsidian graph feed schema mismatch")
        require(graph["counts"] == {"concepts": 2, "sources": 1, "semantic_edges": 0, "definitions": 2},
                "Obsidian graph feed counts mismatch")

        require(not any(root.rglob("*.sqlite")), "self-contained runtime created SQLite")
        require(not (runtime.user / ".knowledge").exists(), "the runtime wrote to the default user home")

    print(
        "installed command, base add and --base lookup, capture and ingest, check --fix-lines, CJK search, "
        "Obsidian plugin and graph feed smoke passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
