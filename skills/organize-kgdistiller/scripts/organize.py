"""Stale-read-safe authoring helper for the organization Skill.

The agent supplies semantic choices; this script never clusters or invokes a
model. It authors a registered explanatory source and ordinary navigation
records, and invokes the public CLI for accept/check/index. Context/proposal
files are transient caller-owned files outside .knowledge and the home.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from kgdistiller.codex_product import product_root
from kgdistiller.home import (
    CONFIG_FILENAME,
    KnowledgeError,
    atomic_write_text,
    home_directory,
    load_home,
    load_type,
    lock,
)
from kgdistiller.navigation import project, record_view, source_profiles
from kgdistiller.records import (
    DRAFTS,
    ENTRIES,
    Files,
    Record,
    evidence_state,
    gloss,
    parse_record,
    parse_text,
    record_files,
    record_problems,
    valid_id,
    valid_source_path,
)

SOURCE_NAME = "kgdistiller-navigation.md"
PROVENANCE = "Agent-authored navigation judgment; this organization is not a claim made by the research sources."


def _raw(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise KnowledgeError(f"expected a regular non-symlink file: {path}")
    return path.read_bytes().decode("utf-8")


def _base(home, name: str):
    if name not in home.bases or not home.bases[name].root.is_dir():
        raise KnowledgeError(f"base {name!r} is not registered and available")
    base = home.bases[name]
    for folder in (base.root / ".knowledge", base.root / ".knowledge/entries", base.root / ".knowledge/drafts"):
        if folder.is_symlink():
            raise KnowledgeError(f"the knowledge tree must not be a symlink: {folder}")
    return base


def initialize(name: str, source: str = SOURCE_NAME) -> dict[str, Any]:
    """Opt-in Skill registration, never installation/base-add activation."""
    if not valid_source_path(source) or any(part.startswith(".") for part in source.split("/")):
        raise KnowledgeError("the navigation source must be a visible base-relative UTF-8 document")
    home_path = home_directory()
    with lock():
        home = load_home(home_path)
        base = _base(home, name)
        mapping, conflicts = base.source_types()
        if source in conflicts:
            raise KnowledgeError(f"navigation source {source} matches conflicting type globs")
        if source in mapping:
            profile = home.types[mapping[source]]
            if profile.navigation is None:
                raise KnowledgeError(f"{source} already belongs to a non-navigation profile")
            return {"base": name, "source": source, "type": profile.name, "created": False}
        path = base.root / source
        if path.exists() or path.is_symlink():
            raise KnowledgeError(f"unregistered navigation source already exists; preserve it and choose another --source: {path}")
        # Resolve the parent too: a fresh source may not escape through a symlink.
        if base.root not in path.resolve().parents:
            raise KnowledgeError("the navigation source resolves outside its base")
        template = product_root() / "templates/document-types/navigation.md"
        type_path = home_path / "types/navigation.md"
        if type_path.exists():
            profile = load_type(type_path)
            if profile.navigation is None:
                raise KnowledgeError("types/navigation.md is owner data without navigation roles; choose an existing navigation source")
        config_path = home_path / CONFIG_FILENAME
        config = json.loads(_raw(config_path))
        atomic_write_text(path, "This is an agent-authored navigation guide for accepted knowledge.\n"
                          "Its classifications are compiler judgments, not statements attributed to research sources.\n")
        # A broad existing glob can claim a new file. Check before activating
        # another type; a refusal leaves the previous registry intact.
        claimed, _ = base.source_types()
        if source in claimed and (claimed[source] not in home.types or home.types[claimed[source]].navigation is None):
            os.unlink(path)
            raise KnowledgeError(f"{source} matches an existing non-navigation glob; choose another --source")
        if source in claimed:
            return {"base": name, "source": source, "type": claimed[source], "created": True}
        if not type_path.exists():
            atomic_write_text(type_path, template.read_text(encoding="utf-8"))
        config["bases"][name]["sources"][source] = "navigation"
        atomic_write_text(config_path, json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        current = load_home(home_path)
        if current.bases[name].type_of(source).navigation is None:
            raise KnowledgeError("navigation source registration is invalid")
    return {"base": name, "source": source, "type": "navigation", "created": True}


def _inventory(home) -> tuple[dict[str, Record], dict[str, list[int]]]:
    found, stats = {}, {}
    for name, base in sorted(home.bases.items()):
        if not base.root.is_dir():
            continue
        _base(home, name)
        for path in record_files(base.root, ENTRIES):
            before = path.stat()
            record = parse_record(path, name)
            stat = path.stat()
            if (before.st_mtime_ns, before.st_size) != (stat.st_mtime_ns, stat.st_size):
                raise KnowledgeError(f"accepted record changed while reading inventory; retry context: {path}")
            found[record.uid] = record
            stats[record.uid] = [stat.st_mtime_ns, stat.st_size]
    return found, stats


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _statement(uid: str, spec: dict[str, Any], *, topic: bool) -> str:
    if topic:
        detail = f"topic {uid} named {_literal(spec['label'])}; aliases {_literal(spec.get('aliases', []))}"
    else:
        detail = (f"organization {uid} named {_literal(spec['label'])}; "
                  f"parents {_literal(spec['parents'])}; children {_literal(spec['children'])}")
    return f"Navigation compiler judgment: {detail}. Routing summary: {spec['summary']}"


def _render(base: str, source: str, profile: dict[str, str], spec: dict[str, Any], line: int, *, topic: bool) -> str:
    lines = ["---", f"label: {_literal(spec['label'])}",
             f"kind: {profile['topic_kind'] if topic else profile['relation_kind']}",
             f"source: {_literal(source)}", f"lines: {line}"]
    if topic and spec.get("aliases"):
        lines.append(f"aliases: {_literal(spec['aliases'])}")
    if not topic:
        for key, role in (("parents", profile["parent_role"]), ("children", profile["child_role"])):
            targets = [f"[[{uid.split(':', 1)[1] if uid.startswith(base + ':') else uid}]]" for uid in spec[key]]
            lines.append(f"{role}: {_literal(targets)}")
    lines.extend(["---", spec["summary"], "", PROVENANCE, "", "## Evidence", "",
                  "> " + _statement(f"{base}:{spec['id']}", spec, topic=topic), ""])
    return "\n".join(lines)


def _spec(record: Record, profile: dict[str, str], *, topic: bool) -> dict[str, Any] | None:
    suffix = "\n\n" + PROVENANCE
    if not record.body.endswith(suffix):
        return None
    result = {"id": record.id, "label": record.label, "summary": record.body[:-len(suffix)]}
    if topic:
        result["aliases"] = list(record.aliases)
    else:
        roles = dict(record.roles)
        for key, role in (("parents", profile["parent_role"]), ("children", profile["child_role"])):
            result[key] = [value.uid for value in roles.get(role, ())]
            if any(uid is None for uid in result[key]):
                return None
    return result


def context(name: str, source: str = SOURCE_NAME, changed: list[str] | None = None) -> dict[str, Any]:
    home = load_home(home_directory())
    base = _base(home, name)
    document_type = base.type_of(source)
    profile = document_type.navigation
    if profile is None:
        raise KnowledgeError("the source needs a profile with navigation role conventions; run init")
    records, stats = _inventory(home)
    profiles = source_profiles(home)
    graph = project({uid: record_view(record) for uid, record in records.items()}, profiles)
    owned = {uid: record for uid, record in records.items()
             if record.base == name and record.source == source
             and uid in graph.topics | graph.organizations}
    texts = {uid: _raw(record.path) for uid, record in owned.items()}
    source_text = _raw(base.root / source)
    source_lines = source_text.lstrip("\ufeff").splitlines()
    protected = []
    for uid, record in owned.items():
        topic = uid in graph.topics
        spec = _spec(record, profile, topic=topic)
        if (spec is None or record.lines != ((record.first_line, record.first_line),)
                or _render(name, source, profile, spec, record.first_line, topic=topic) != texts[uid]
                or evidence_state(record.evidence, source_lines, record.lines) != ("fresh", None)):
            protected.append(uid)
    scientific = set(records) - graph.topics - graph.organizations
    required = list(dict.fromkeys(changed)) if changed is not None else sorted(uid for uid in scientific if records[uid].base == name)
    if set(required) - scientific:
        raise KnowledgeError("changed UIDs must be existing scientific records: " + ", ".join(sorted(set(required) - scientific)))
    return {
        "base": name, "source": source, "type": document_type.name,
        "navigation": profile, "guidance": document_type.guidance,
        "records": [{"uid": uid, "label": record.label, "kind": record.kind,
                     "class": record.class_, "gloss": gloss(record.body),
                     "navigation": "topic" if uid in graph.topics else "organization" if uid in graph.organizations else "leaf",
                     "out": record_view(record)["out"]} for uid, record in sorted(records.items())],
        "protected": sorted(protected), "required_uids": required,
        "diagnostics": graph.diagnostics(set(records)),
        "snapshot": {"config": _raw(home.directory / CONFIG_FILENAME),
                     "profile": _raw(home.directory / f"types/{document_type.name}.md"),
                     "source": source_text, "navigation_records": texts, "record_stats": stats},
    }


def _unchanged(ctx: dict[str, Any], *, source_text: str | None = None, new_uids: set[str] | None = None) -> tuple[Any, dict[str, Record]]:
    home = load_home(home_directory())
    base = _base(home, ctx["base"])
    snapshot = ctx["snapshot"]
    if (_raw(home.directory / CONFIG_FILENAME) != snapshot["config"]
            or _raw(home.directory / f"types/{ctx['type']}.md") != snapshot["profile"]
            or _raw(base.root / ctx["source"]) != (snapshot["source"] if source_text is None else source_text)):
        raise KnowledgeError("stale context: configuration, profile or navigation source changed; read a new context")
    records, stats = _inventory(home)
    for uid in new_uids or set():
        stats.pop(uid, None)
    if stats != snapshot["record_stats"]:
        raise KnowledgeError("stale context: accepted records changed; read a new context")
    for uid, text in snapshot["navigation_records"].items():
        if uid not in records or _raw(records[uid].path) != text:
            raise KnowledgeError(f"stale context: {uid} changed")
    return home, records


def _single(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise KnowledgeError(f"{label} must be a non-empty single line")
    return value


def prepare(ctx: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    home, records = _unchanged(ctx)
    base = _base(home, ctx["base"])
    profile = base.type_of(ctx["source"]).navigation
    if profile != ctx["navigation"]:
        raise KnowledgeError("stale navigation conventions")
    if set(proposal) - {"topics", "organizations", "retire", "scope", "reason"}:
        raise KnowledgeError("unknown proposal key")
    if proposal.get("scope") not in {"local", "global"}:
        raise KnowledgeError("proposal scope must be local or global")
    _single(proposal.get("reason"), "reason")
    owned = ctx["snapshot"]["navigation_records"]
    retire = proposal.get("retire", [])
    if not isinstance(retire, list) or any(not isinstance(uid, str) or uid not in owned for uid in retire):
        raise KnowledgeError("retire must contain owned navigation UIDs only")
    edits, topics, seen = {}, {}, set()
    source_text = ctx["snapshot"]["source"]
    original_source_lines = source_text.lstrip("\ufeff").splitlines()
    source_text += "" if source_text.endswith("\n") else "\n"
    source_text += "\n"
    new, updated = [], []
    for category, topic in (("topics", True), ("organizations", False)):
        batch = proposal.get(category, [])
        if not isinstance(batch, list):
            raise KnowledgeError(f"{category} must be a list")
        for raw in batch:
            if not isinstance(raw, dict):
                raise KnowledgeError(f"{category} items must be objects")
            spec = dict(raw)
            keys = {"id", "label", "summary", "aliases"} if topic else {"id", "label", "summary", "parents", "children"}
            if set(spec) - keys or not isinstance(spec.get("id"), str) or not valid_id(spec["id"]):
                raise KnowledgeError(f"{category} has unknown keys or an invalid readable id")
            for key in ("label", "summary"):
                _single(spec.get(key), key)
            if len(spec["summary"]) > 600:
                raise KnowledgeError("routing summaries must be at most 600 characters")
            uid = f"{base.name}:{spec['id']}"
            if uid in seen or uid in retire:
                raise KnowledgeError(f"duplicate or simultaneously retired upsert: {uid}")
            seen.add(uid)
            if uid in records and uid not in owned:
                raise KnowledgeError(f"cannot edit a scientific or foreign-source record: {uid}")
            if uid in records:
                old_spec = _spec(records[uid], profile, topic=topic)
                single = records[uid].lines == ((records[uid].first_line, records[uid].first_line),)
                expected = (_render(base.name, ctx["source"], profile, old_spec, records[uid].first_line, topic=topic)
                            if old_spec is not None and single else None)
                if (expected != _raw(records[uid].path)
                        or evidence_state(records[uid].evidence, original_source_lines,
                                          records[uid].lines) != ("fresh", None)):
                    raise KnowledgeError(f"preserve owner-edited navigation record {uid}; reorganize around it")
            if uid in ctx["protected"]:
                raise KnowledgeError(f"preserve owner-edited navigation record {uid}; reorganize around it")
            if topic:
                spec.setdefault("aliases", list(records[uid].aliases) if uid in records else [])
                if not isinstance(spec["aliases"], list):
                    raise KnowledgeError("aliases must be a list")
                for alias in spec["aliases"]:
                    _single(alias, "alias")
            else:
                for key in ("parents", "children"):
                    values = spec.get(key)
                    if (not isinstance(values, list) or not values
                            or any(not isinstance(value, str) or ":" not in value for value in values)
                            or len(set(values)) != len(values)):
                        raise KnowledgeError(f"{key} must be a non-empty list of unique base-qualified UIDs")
                    for value in values:
                        participant_base, identifier = value.split(":", 1)
                        if participant_base not in home.bases or not valid_id(identifier):
                            raise KnowledgeError(f"{key} must use canonical registered base-qualified UIDs from context: {value!r}")
            # Semantically unchanged compiler records retain their exact bytes
            # and source lines, keeping local reorganization genuinely local.
            if uid in records and spec == _spec(records[uid], profile, topic=topic):
                continue
            statement = _statement(uid, spec, topic=topic)
            line = len(source_text.splitlines()) + 1
            source_text += statement + "\n\n"
            text = _render(base.name, ctx["source"], profile, spec, line, topic=topic)
            path = base.root / f".knowledge/entries/{spec['id']}.md"
            edits[uid] = text
            topics[uid] = parse_text(text, base.name, spec["id"], path)
            (updated if uid in records else new).append(uid)
    if set(retire) & set(ctx["protected"]):
        raise KnowledgeError("cannot retire owner-edited navigation records")
    if not edits and not retire:
        source_text = ctx["snapshot"]["source"]
    after = {uid: record for uid, record in records.items() if uid not in retire}
    after.update(topics)
    profiles = source_profiles(home)
    graph = project({uid: record_view(record) for uid, record in after.items()}, profiles)
    before = project({uid: record_view(record) for uid, record in records.items()}, profiles)
    if graph.cyclic or graph.dangling or graph.malformed:
        raise KnowledgeError("invalid navigation projection: " + _literal(graph.diagnostics(set(after))))
    required = set(ctx["required_uids"]) | (before.reachable(set(records)) - before.topics - before.organizations)
    if proposal["scope"] == "global":
        required |= set(after) - graph.topics - graph.organizations
    missing = required - graph.reachable(set(after))
    if missing:
        raise KnowledgeError("navigation must preserve coverage and route changed knowledge: " + ", ".join(sorted(missing)))
    files = Files(home)
    for folder in (ENTRIES, DRAFTS):
        files.ids(base.name, folder)
    for uid in new:
        identifier = uid.split(":", 1)[1]
        if identifier in files.ids(base.name, DRAFTS):
            raise KnowledgeError(f"a draft already uses {uid}; preserve it")
    files._folders[base.name, ENTRIES] = {record.id: record.path for record in after.values() if record.base == base.name}
    files._records.update({record.path: record for record in topics.values()})
    files._lines[base.root / ctx["source"]] = source_text.lstrip("\ufeff").splitlines()
    for uid, record in after.items():
        if uid not in edits and not any(value.uid in retire for _, _, value in record.values()):
            continue
        problems, freshness = record_problems(files, record, draft=False)
        if problems or (uid in edits and freshness != ("fresh", None)):
            raise KnowledgeError(f"{uid} would fail check: {problems}; evidence {freshness}")
    return {"edits": edits, "source_text": source_text, "created": new, "updated": updated,
            "retired": list(dict.fromkeys(retire)), "metrics": graph.diagnostics(set(after)),
            "scope": proposal["scope"], "reason": proposal["reason"]}


def _cli(*arguments: str) -> dict[str, Any]:
    completed = subprocess.run([sys.executable, "-m", "kgdistiller", *arguments],
                               capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode:
        raise KnowledgeError(f"kgd {' '.join(arguments)} failed; preserve prepared drafts and reread context: "
                             + completed.stdout + completed.stderr)
    return json.loads(completed.stdout)


def apply(ctx: dict[str, Any], proposal: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    plan = prepare(ctx, proposal)
    report = {key: plan[key] for key in ("created", "updated", "retired", "metrics", "scope", "reason")}
    if dry_run:
        return {**report, "dry_run": True}
    name, source = ctx["base"], ctx["source"]
    paths = []
    with lock():
        home, _ = _unchanged(ctx)
        base = _base(home, name)
        # All semantic, record and coverage checks ran before the first write.
        # Each new object still takes the standard draft/accept path.
        for uid in plan["created"]:
            path = base.root / f".knowledge/drafts/{uid.split(':', 1)[1]}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(plan["edits"][uid])
            paths.append(path)
        if plan["source_text"] != ctx["snapshot"]["source"]:
            atomic_write_text(base.root / source, plan["source_text"])
    receipt = _cli("accept", *(str(path) for path in paths)) if paths else {"created": [], "understanding_set": []}
    with lock():
        home, current = _unchanged(ctx, source_text=plan["source_text"], new_uids=set(plan["created"]))
        for uid in plan["updated"]:
            # Exact old text and corpus stats were compared under the home lock;
            # only compiler-owned navigation bytes are authored here.
            atomic_write_text(current[uid].path, plan["edits"][uid])
        for uid in plan["retired"]:
            os.unlink(current[uid].path)
    checked = _cli("check", "--base", name)
    indexed = _cli("index")
    return {**report, "receipt": receipt, "check": checked, "index": indexed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "context", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--base", required=True)
        command.add_argument("--source", default=SOURCE_NAME)
        if name == "context":
            command.add_argument("--changed", nargs="*", default=None, metavar="UID")
        elif name == "apply":
            command.add_argument("--context", type=Path, required=True)
            command.add_argument("--proposal", type=Path, required=True)
            command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.base, args.source)
        elif args.command == "context":
            result = context(args.base, args.source, args.changed)
        else:
            ctx = json.loads(args.context.read_text(encoding="utf-8"))
            if ctx["base"] != args.base or ctx["source"] != args.source:
                raise KnowledgeError("context base/source must equal the explicit command scope")
            result = apply(ctx, json.loads(args.proposal.read_text(encoding="utf-8")), args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (KnowledgeError, OSError, ValueError) as error:
        print(json.dumps({"kind": "kgdistiller-navigation-error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
