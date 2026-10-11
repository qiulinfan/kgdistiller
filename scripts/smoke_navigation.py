"""Exercise navigation authoring and real CLI reads in a temporary home.

Run with the Python that has kgdistiller installed. The semantic proposal is a
fixture; this validates persistence, growth and recovery, not model quality.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/organize-kgdistiller/scripts/organize.py"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-navigation-smoke-") as temp:
        root = Path(temp).resolve()
        home = root / "home"
        environment = {**os.environ, "KGDISTILLER_HOME": str(home)}

        def call(arguments: list[str]) -> dict:
            result = subprocess.run([sys.executable, *arguments], env=environment,
                                    capture_output=True, text=True, encoding="utf-8", check=False)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return json.loads(result.stdout)

        def kgd(*arguments: str) -> dict:
            return call(["-m", "kgdistiller", *arguments])

        bases = {name: root / name for name in ("theory", "methods")}
        for name, base in bases.items():
            base.mkdir()
            kgd("base", "add", str(base), "--name", name)
            (base / "source.txt").write_text("A measurable structure defines which sets can be measured.\n"
                                             "A concentration method controls random deviations.\n", encoding="utf-8")
        (home / "types/science.md").write_text("---\nnode_kinds: [concept]\n---\nKeep precise concepts with their assumptions.\n", encoding="utf-8")
        config_path = home / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for base in config["bases"].values():
            base["sources"] = {"source.txt": "science"}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        science = {}
        for name, identifier, line in (("theory", "structure", 1), ("methods", "concentration", 2)):
            draft = bases[name] / f".knowledge/drafts/{identifier}.md"
            draft.parent.mkdir(parents=True)
            quote = (bases[name] / "source.txt").read_text(encoding="utf-8").splitlines()[line - 1]
            draft.write_text(f"---\nlabel: {identifier}\nkind: concept\nsource: source.txt\nlines: {line}\n---\n"
                             f"{quote}\n\n## Evidence\n\n> {quote}\n", encoding="utf-8")
            kgd("accept", str(draft))
            science[name] = bases[name].joinpath(f".knowledge/entries/{identifier}.md").read_bytes()
        kgd("index")

        def organize(name: str, proposal: dict) -> dict:
            call([str(HELPER), "init", "--base", name])
            ctx = call([str(HELPER), "context", "--base", name])
            context_path, proposal_path = root / "context.json", root / "proposal.json"
            context_path.write_text(json.dumps(ctx), encoding="utf-8")
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            return call([str(HELPER), "apply", "--base", name,
                         "--context", str(context_path), "--proposal", str(proposal_path)])

        def topic(identifier: str, label: str) -> dict:
            return {"id": identifier, "label": label, "summary": f"Choose {label} for its concepts and assumptions."}

        def route(identifier: str, parents: list[str], children: list[str]) -> dict:
            return {"id": identifier, "label": identifier, "summary": "Choose a concept or a more specific entrance.",
                    "parents": parents, "children": children}

        organize("theory", {"scope": "global", "reason": "Build initial semantic entrances.",
                            "topics": [topic("topic-theory", "Theory")],
                            "organizations": [route("route-theory", ["theory:topic-theory"],
                                                    ["theory:structure", "methods:concentration"])], "retire": []})
        organize("methods", {"scope": "local", "reason": "Add a shared method entrance.",
                             "topics": [topic("topic-methods", "Methods")],
                             "organizations": [route("route-methods", ["methods:topic-methods"],
                                                     ["methods:concentration", "theory:structure"])], "retire": []})
        growth = organize("theory", {"scope": "local", "reason": "Split theory into useful subtopics as methods grow.",
                                     "topics": [topic("topic-structures", "Measurable structures"), topic("topic-bounds", "Bounds")],
                                     "organizations": [
                                         route("route-theory", ["theory:topic-theory"], ["theory:topic-structures", "theory:topic-bounds"]),
                                         route("route-structures", ["theory:topic-structures"], ["theory:structure"]),
                                         route("route-bounds", ["theory:topic-bounds"], ["methods:concentration", "theory:structure"]),
                                     ], "retire": []})
        children = kgd("navigate", "theory:topic-theory")
        if {row["uid"] for row in children["records"]} != {"theory:topic-structures", "theory:topic-bounds"}:
            raise RuntimeError("split did not create the expected routes")
        shared = kgd("navigate", "methods:topic-methods", "theory:topic-bounds")
        if sum(row["uid"] == "theory:structure" for row in shared["records"]) != 1:
            raise RuntimeError("shared cross-base leaves were duplicated")
        for name, identifier in (("theory", "structure"), ("methods", "concentration")):
            if bases[name].joinpath(f".knowledge/entries/{identifier}.md").read_bytes() != science[name]:
                raise RuntimeError("scientific knowledge changed during navigation reorganization")
        entrances = kgd("navigate")
        kgd("index", "--rebuild")
        if entrances != kgd("navigate"):
            raise RuntimeError("index rebuild changed navigation")
        kgd("check")
        print(json.dumps({"status": "ok", "science_records_preserved": len(science),
                          "root_uids": [row["uid"] for row in entrances["records"]],
                          "growth_updated": growth["updated"], "covered": entrances["diagnostics"]["covered_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
