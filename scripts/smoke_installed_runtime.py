"""Exercise the installed wheel end to end: base, records (local and foreign), drafts and replacements, check, index, retrieval, link following, browse, pack, find and relocate."""

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
PASSAGE = "A measure space is a triple (X, F, mu) with mu countably additive"
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
conclusion: ["[[countable-additivity]]"]
---
The measure of a measure space is countably additive.

## Evidence

> with mu countably additive.
"""
WEB = "https://en.wikipedia.org/wiki/Sigma-additive_set_function"
COUNTABLE_ADDITIVITY = f"""---
label: Countable additivity
kind: concept
source: {WEB}
---
A set function is countably additive when it adds over countable disjoint unions.

## Evidence

> the measure of a countable disjoint union is the sum of the measures
"""
MEASURE_THEORY = """---
label: 测度论
kind: concept
source: notes/measure.txt
lines: 4
understanding: no
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


def embedding(runtime: Runtime, outside: Path) -> None:
    """Show the home's embedding model and turn it off; neither downloads a model."""
    expected = {"home": str(runtime.home), "embedding": None, "precision": None, "vectors": None, "unembedded": 0,
                "embedding_changed": False}
    shown = runtime.object("embedding", cwd=outside)
    require(shown == expected, f"embedding status mismatch: {shown}")
    off = runtime.object("embedding", "off", cwd=outside)
    require(off == expected, f"embedding off status mismatch: {off}")
    config = json.loads((runtime.home / "config.json").read_text(encoding="utf-8"))
    require(config.get("embedding") is None, f"embedding off left a model in config.json: {config}")
    require(not (runtime.home / "index.sqlite").exists(), "embedding created the index")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def follow_links(runtime: Runtime, root: Path, outside: Path) -> None:
    """neighbors, browse and pack over the indexed smoke base."""
    relation = "smoke:measure-space-implies-countable-additivity"
    cited = runtime.object("neighbors", "measure-space", "--dir", "in", cwd=outside)
    require(cited["edges"] == [{"from": relation, "role": "premise", "to": "smoke:measure-space", "depth": 1}],
            f"neighbors --dir in edges mismatch: {cited['edges']}")
    require(cited["records"][relation]["exists"] and cited["missing"] == [], f"neighbors records mismatch: {cited}")
    bases = runtime.object("browse", cwd=outside)["bases"]
    require(bases == [{"name": "smoke", "available": True, "records": 4, "relations": 1}],
            f"browse bases mismatch: {bases}")
    entries = runtime.object("browse", "smoke", cwd=outside)["entries"]
    require(entries == [{"path": WEB, "type": "foreign", "records": 1, "relations": 0},
                        {"path": "notes/", "type": "dir", "records": 3, "relations": 1}],
            f"browse base entries mismatch: {entries}")
    by_kind = runtime.object("browse", "smoke:notes/measure.txt", cwd=root)
    require([group["kind"] for group in by_kind["kinds"]] == ["concept", "definition", "implies"],
            f"browse source kinds mismatch: {by_kind['kinds']}")
    web = runtime.object("browse", f"smoke:{WEB}", cwd=outside)
    require([[item["uid"] for item in group["records"]] for group in web["kinds"]] == [["smoke:countable-additivity"]],
            f"browse foreign source mismatch: {web['kinds']}")
    listing = runtime.object("browse", "--kind", "implies", cwd=outside)["records"]
    require([item["uid"] for item in listing] == [relation], f"browse --kind listing mismatch: {listing}")
    require(sorted(listing[0]["links"]) == ["conclusion", "premise"],
            f"browse --kind links mismatch: {listing[0]['links']}")
    packet = runtime.object("pack", relation, cwd=outside)
    require([item["uid"] for item in packet["records"]] == [relation], f"pack records mismatch: {packet['records']}")
    require(packet["bytes"] <= packet["budget"], f"pack exceeded its budget: {packet['bytes']}")
    require({"reason": "not-packed", "from": relation, "role": "conclusion", "uid": "smoke:countable-additivity"}
            in packet["gaps"], f"pack foreign not-packed gap missing: {packet['gaps']}")
    require({"reason": "not-packed", "from": relation, "role": "premise", "uid": "smoke:measure-space"}
            in packet["gaps"], f"pack not-packed gap missing: {packet['gaps']}")


def obsidian(runtime: Runtime, root: Path, outside: Path) -> None:
    """Install the plugin by base name, then again from the base root with settings and a link format to keep."""
    plugin_root = root / ".obsidian/plugins/kgdistiller"
    settings = plugin_root / "data.json"

    def read(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    plugin = runtime.object("obsidian", "install", "--base", "smoke", cwd=outside)
    require(plugin.get("status") == "installed", f"Obsidian plugin install status mismatch: {plugin}")
    for name in ("main.js", "manifest.json", "styles.css"):
        require((plugin_root / name).is_file(), f"installed Obsidian plugin is missing {name}")
    require(read(root / ".obsidian/community-plugins.json") == ["kgdistiller"],
            "Obsidian plugin was not configured as enabled")
    require(read(settings) == {"hiddenKnowledgeEnabled": True}, "hidden indexing was not enabled")
    require(plugin.get("warnings") == [], f"unexpected Obsidian install warnings: {plugin.get('warnings')}")

    settings.write_text(json.dumps({"hiddenKnowledgeEnabled": False, "note": False}), encoding="utf-8")
    (root / ".obsidian/app.json").write_text(json.dumps({"newLinkFormat": "relative"}), encoding="utf-8")
    again = runtime.object("obsidian", "install", cwd=root)
    require(again.get("status") == "current", f"Obsidian plugin reinstall status mismatch: {again}")
    require(read(settings) == {"hiddenKnowledgeEnabled": True, "note": False},
            "plugin settings were not merged")
    warnings = again.get("warnings")
    require(isinstance(warnings, list) and len(warnings) == 1 and "newLinkFormat" in warnings[0],
            f"relative newLinkFormat was not reported: {warnings}")
    runtime.text("obsidian", "install", cwd=outside, expect=1)


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
        embedding(runtime, outside)
        (root / ".obsidian").mkdir()
        source = root / "notes/measure.txt"
        write(source, SOURCE)
        write(root / ".knowledge/entries/measure-space.md", MEASURE_SPACE)
        write(root / ".knowledge/entries/measure-space-implies-countable-additivity.md", ADDITIVITY)
        write(root / ".knowledge/entries/countable-additivity.md", COUNTABLE_ADDITIVITY)
        write(root / ".knowledge/drafts/measure-theory.md", MEASURE_THEORY)

        profile = runtime.object("source", "measure.txt", cwd=root / "notes")
        require(profile["type"] == "smoke-notes" and profile["line_count"] == 4, f"source profile: {profile}")
        require(sorted(profile) == ["base", "drafts", "epistemic", "guidance", "line_count", "node_kinds",
                                    "records", "relation_kinds", "source", "type"], f"source profile keys: {profile}")
        require([row["class"] for row in profile["records"]] == ["node", "relation"], "source records mismatch")
        require([row["id"] for row in profile["drafts"]] == ["measure-theory"], "source drafts mismatch")
        require(runtime.object("source", "notes/measure.txt", cwd=root) == profile, "source differs by cwd")
        require(not (root / ".knowledge/sheets").exists(), "source wrote a sheet")
        listed = runtime.object("drafts", cwd=outside)
        require([(item["uid"], item["problems"], item["with"], item["replaces"]) for item in listed["drafts"]]
                == [("smoke:measure-theory", [], [], None)] and listed["unavailable_bases"] == [],
                f"drafts listing mismatch: {listed}")
        receipt = runtime.object("accept", ".knowledge/drafts/measure-theory.md", cwd=root)
        indexed = receipt.pop("index", None) or {}
        require(receipt == {"created": ["smoke:measure-theory"], "updated": []}, f"accept receipt: {receipt}")
        require(indexed.get("created") and indexed["bases"]["smoke"]["parsed"] == 4, f"accept index report: {indexed}")
        remaining = runtime.object("drafts", cwd=outside)
        require(remaining == {"drafts": [], "unavailable_bases": []}, f"drafts after accept: {remaining}")

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
        # accept built the index; --fix-lines rewrote the three records since.
        require(not report["created"] and report["bases"]["smoke"]["parsed"] == 3, f"index report: {report}")
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
        unsure = runtime.object("search", "测度", "--understanding", "no", cwd=outside)
        require([row["uid"] for row in unsure["results"]] == ["smoke:measure-theory"], "understanding filter failed")
        resolved = runtime.object("resolve", "测度空间", "countable additivity", cwd=outside)
        require([sense["uid"] for sense in resolved["terms"][0]["senses"]] == ["smoke:measure-space"],
                "resolve senses mismatch")
        require([(sense["uid"], sense["foreign"]) for sense in resolved["terms"][1]["senses"]]
                == [("smoke:countable-additivity", True)], f"resolve foreign sense mismatch: {resolved}")
        require(all("pending" not in term for term in resolved["terms"]), "resolve still reports pending uses")
        got = runtime.object("get", "measure-space", "--source-lines", "1", cwd=outside)
        record = got["records"][0]
        require(record["source_text"] is not None and record["source_text"].startswith("3\t"),
                f"get --source-lines failed: {record.get('source_text')}")
        require([link["role"] for link in record["in"]] == ["premise"], "get in-links mismatch")
        foreign = runtime.object("get", "countable-additivity", "--source-lines", "1", cwd=outside)["records"][0]
        require((foreign["foreign"], foreign["lines"], foreign["source_text"]) == (True, "", None),
                f"get of a foreign record mismatch: {foreign}")
        follow_links(runtime, root, outside)

        entry = root / ".knowledge/entries/measure-space.md"
        replacement = entry.read_text(encoding="utf-8").replace(
            "A set with a sigma-algebra and a measure.", "A measurable space with a measure.")
        write(root / ".knowledge/drafts/measure-space.md", replacement)
        require(runtime.object("check", cwd=root) == CLEAN, "a replacement draft did not check clean")
        dry = runtime.object("accept", ".knowledge/drafts/measure-space.md", "--dry-run", cwd=root)
        require(dry == {"would_create": [], "would_update": ["smoke:measure-space"]}, f"accept --dry-run: {dry}")
        updated = runtime.object("accept", ".knowledge/drafts/measure-space.md", cwd=root)
        updated.pop("index", None)
        require(updated == {"created": [], "updated": ["smoke:measure-space"]}, f"replacement receipt: {updated}")
        require(entry.read_text(encoding="utf-8") == replacement, "accept did not replace the record")
        replaced = runtime.object("get", "measure-space", cwd=outside)
        require(replaced["records"][0]["body"] == "A measurable space with a measure." and
                replaced["lag"]["changed_files"] == 0, f"replaced record was not indexed: {replaced}")

        write(root / "notes/renamed.txt", "New preface\n" + source.read_text(encoding="utf-8"))
        found = runtime.object("find", "A measure space is a triple (X, F, mu) with mu", cwd=outside)
        require(found["total"] == 2 and [match["source"] for match in found["matches"]]
                == ["notes/measure.txt", "notes/renamed.txt"], f"find mismatch: {found}")
        several = json.loads(runtime.text("relocate", "smoke:measure-space", PASSAGE, cwd=outside, expect=1))
        require(len(several["refused"]["matches"]) == 2, f"relocate did not refuse two matches: {several}")
        relocated = runtime.object("relocate", "smoke:measure-space", PASSAGE, "--source", "notes/renamed", cwd=outside)
        require((relocated["to"], relocated["evidence"]) == ({"source": "notes/renamed.txt", "lines": "5-6"}, "kept"),
                f"relocate mismatch: {relocated}")
        require('source: "notes/renamed.txt"\nlines: 5-6\n' in entry.read_text(encoding="utf-8"),
                "relocate did not rewrite the citation")
        require(runtime.object("check", cwd=root) == CLEAN, "check after relocate is not clean")

        obsidian(runtime, root, outside)

        require(not any(".sqlite" in path.name for path in root.rglob("*")), "the index was written into the base")
        require(not (runtime.user / ".knowledge").exists(), "the runtime wrote to the default user home")

    print(
        "installed command, base add, embedding show and off, source, drafts and accept, check --fix-lines, index, "
        "search (including CJK), resolve, get, neighbors, browse, pack, foreign records, accepting a replacement, find, "
        "relocate and "
        "Obsidian plugin install (hidden indexing and the newLinkFormat warning) smoke passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
