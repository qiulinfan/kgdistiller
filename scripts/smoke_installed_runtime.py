"""Exercise the installed wheel end to end: base, records, drafts, check, index and retrieval."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SOURCE = (
    "Chapter one\n"
    "A measure space is a triple (X, F, mu)\n"
    "with mu countably additive.\n"
    "测度论研究可测空间上的集合函数。\n"
)
TYPE = (
    "---\nnode_kinds: [definition, concept]\nrelation_kinds:\n  implies: [premise, conclusion]\n---\n\n"
    "Definitions are nodes; statements connecting them are relations.\n"
)
MEASURE_SPACE = """---
label: Measure space
kind: definition
source: notes/measure.txt
lines: 2-3
aliases: [测度空间]
---
A set with a sigma-algebra and a measure.

## Evidence

> A measure space is a triple (X, F, mu)
> with mu countably additive.
"""
ADDITIVITY = """---
label: Measure space implies countable additivity
kind: implies
source: notes/measure.txt
lines: 3
premise: ["[[measure-space]]"]
conclusion: [countable additivity]
---
The measure of a measure space is countably additive.

## Evidence

> with mu countably additive.
"""
MEASURE_THEORY = """---
label: 测度论
kind: concept
source: notes/measure.txt
lines: 4
---
The study of set functions on measurable spaces.

## Evidence

> 测度论研究可测空间上的集合函数。
"""
CLEAN = {"errors": [], "stale": [], "moved": []}


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


def register(runtime: Runtime, root: Path, outside: Path) -> None:
    """Register the base, then map its .txt notes to a user-defined document type."""
    added = runtime.object("base", "add", str(root), "--name", "smoke", cwd=outside)
    require(added.get("added", {}).get("name") == "smoke", "base add failed")
    require((runtime.home / "config.json").is_file(), "home config.json is missing")
    require((runtime.home / "types").is_dir(), "home types/ is missing")
    require((runtime.home / ".gitignore").read_text(encoding="utf-8") == "index.sqlite*\nlock\n",
            "home .gitignore mismatch")
    require([path.name for path in (root / ".knowledge").iterdir()] == ["entries"],
            "base add wrote more than .knowledge/entries/ into the base")
    require(not (runtime.user / ".knowledge").exists(), "state was created in the default user home")
    config_path = runtime.home / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["bases"]["smoke"]["sources"] = {"notes/*.txt": "smoke-notes"}
    staged = config_path.with_name(".config.json.smoke")
    staged.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, config_path)
    (runtime.home / "types/smoke-notes.md").write_text(TYPE, encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-wheel-runtime-") as raw:
        temporary = Path(raw).resolve()
        runtime = Runtime(temporary)
        root = temporary / "vault"
        outside = temporary / "outside"
        for directory in (root, outside):
            directory.mkdir()
        refused = runtime.text("search", "measure", cwd=outside, expect=1)
        require("kgd base add" in refused, f"missing home was not reported: {refused}")
        require(not runtime.home.exists(), "a read created the home")
        register(runtime, root, outside)
        (root / ".obsidian").mkdir()
        source = root / "notes/measure.txt"
        write(source, SOURCE)
        write(root / ".knowledge/entries/measure-space.md", MEASURE_SPACE)
        write(root / ".knowledge/entries/measure-space-implies-countable-additivity.md", ADDITIVITY)
        write(root / ".knowledge/drafts/measure-theory.md", MEASURE_THEORY)

        profile = runtime.object("sheet", "notes/measure.txt", "--json", cwd=root)
        require(profile["type"] == "smoke-notes" and profile["line_count"] == 4, f"sheet --json profile: {profile}")
        require([row["class"] for row in profile["records"]] == ["node", "relation"], "sheet --json records mismatch")
        require([row["id"] for row in profile["drafts"]] == ["measure-theory"], "sheet --json drafts mismatch")
        written = runtime.object("sheet", "measure.txt", cwd=root / "notes")
        sheet = root / ".knowledge/sheets/notes/measure.txt.md"
        require(written == {"sheet": str(sheet)} and sheet.is_file(), f"sheet was not written: {written}")
        require("- [ ] [[.knowledge/drafts/measure-theory|测度论]]" in sheet.read_text(encoding="utf-8"),
                "sheet does not list the draft")
        receipt = runtime.object("accept", ".knowledge/drafts/measure-theory.md", cwd=root)
        require(receipt == {"created": ["smoke:measure-theory"], "understanding_set": []}, f"accept receipt: {receipt}")

        require(runtime.object("check", cwd=outside) == CLEAN, "fresh records did not check clean")
        write(source, "Preface\n\n" + SOURCE)
        moved = json.loads(runtime.text("check", "--base", "smoke", cwd=outside, expect=1))
        require(sorted(item["lines"] for item in moved["moved"]) == ["4-5", "5-5", "6-6"],
                f"moved evidence was not reported: {moved}")
        fixed = runtime.object("check", "--fix-lines", cwd=root)
        require(len(fixed["fixed"]) == 3 and fixed["moved"] == [] and fixed["skipped"] == [],
                f"check --fix-lines failed: {fixed}")
        require("\nlines: 4-5\n" in (root / ".knowledge/entries/measure-space.md").read_text(encoding="utf-8"),
                "fixed line range was not written")
        require(runtime.object("check", cwd=root) == CLEAN, "check after --fix-lines is not clean")

        report = runtime.object("index", "--no-embed", cwd=outside)
        require(report["created"] and report["bases"]["smoke"]["parsed"] == 3, f"index report: {report}")
        require((runtime.home / "index.sqlite").is_file(), "index.sqlite is not in the home")
        found = runtime.object("search", "measure space", cwd=outside)
        require(found["results"][0]["uid"] == "smoke:measure-space", f"search ranking: {found['results'][:3]}")
        require(found["lag"]["changed_files"] == 0, "search reported lag after index")
        require(found["lanes"] == ["lexical", "name"], f"search lanes with embedding null: {found['lanes']}")
        sparse = runtime.object("search", "measure space", "--no-dense", cwd=outside)
        require(sparse["lanes"] == ["lexical", "name"], f"search --no-dense lanes: {sparse['lanes']}")
        require(sparse["results"][0]["uid"] == "smoke:measure-space", f"search --no-dense ranking: {sparse['results'][:3]}")
        cjk = runtime.object("search", "测度", cwd=outside)
        require("smoke:measure-theory" in [row["uid"] for row in cjk["results"]], "CJK search failed")
        resolved = runtime.object("resolve", "测度空间", "countable additivity", cwd=outside)
        require([sense["uid"] for sense in resolved["terms"][0]["senses"]] == ["smoke:measure-space"],
                "resolve senses mismatch")
        require([item["owner"] for item in resolved["terms"][1]["pending"]]
                == ["smoke:measure-space-implies-countable-additivity"], "resolve pending mismatch")
        got = runtime.object("get", "measure-space", "--source-lines", "1", cwd=outside)
        record = got["records"][0]
        require(record["source_text"] is not None and record["source_text"].startswith("3\t"),
                f"get --source-lines failed: {record.get('source_text')}")
        require([link["role"] for link in record["in"]] == ["premise"], "get in-links mismatch")

        plugin = runtime.object("obsidian", "install", "--base", "smoke", cwd=outside)
        require(plugin.get("status") == "installed", "Obsidian plugin install status mismatch")
        for name in ("main.js", "manifest.json", "styles.css"):
            require((root / ".obsidian/plugins/kgdistiller" / name).is_file(),
                    f"installed Obsidian plugin is missing {name}")
        require(json.loads((root / ".obsidian/community-plugins.json").read_text(encoding="utf-8"))
                == ["kgdistiller"], "Obsidian plugin was not configured as enabled")

        require(not any(".sqlite" in path.name for path in root.rglob("*")), "the index was written into the base")
        require(not (runtime.user / ".knowledge").exists(), "the runtime wrote to the default user home")

    print(
        "installed command, base add, sheet and accept, check --fix-lines, index, search (including CJK), "
        "resolve, get and Obsidian plugin smoke passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
