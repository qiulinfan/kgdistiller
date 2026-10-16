"""The knowledge write path: list and ``accept`` drafts, then index and embed what they admit, and describe a source.

Drafts are proposed records in ``<root>/.knowledge/drafts/``: a new record, or,
under the id of an accepted record of the same base, its replacement.
``drafts`` reads them from files with what ``accept`` would make of each,
writing nothing. ``accept`` validates them in the post-acceptance world under
the home lock; it moves a new record into ``entries/`` with ``os.link`` then
``os.unlink``, so an accepted record is never overwritten by accident, and
moves a replacement over its record with ``os.replace``. ``source`` prints a
source's extraction profile and the records and drafts citing it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .home import (
    KNOWLEDGE_DIRECTORY,
    Base,
    DocumentType,
    Home,
    KnowledgeError,
    atomic_write_text,
    base_for_path,
    base_relation_kinds,
    home_directory,
    load_home,
    lock,
    record_class,
)
from .index import index, name_key
from .records import (
    DRAFTS,
    ENTRIES,
    MAX_ID_LENGTH,
    Files,
    Record,
    RecordError,
    Value,
    body_sections,
    fold,
    format_ranges,
    gloss,
    parse_record,
    parse_text,
    read_text,
    record_files,
    record_problems,
    slug,
    source_lines,
)


def _realpath(path: Path | str) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))


def _knowledge(base: Base) -> Path:
    knowledge = base.root / KNOWLEDGE_DIRECTORY
    if knowledge.is_symlink():
        raise KnowledgeError(f"{knowledge} must not be a symlink")
    return knowledge


# ---------------------------------------------------------------- accept


def _locate_draft(home: Home, path: Path | str) -> tuple[Base | None, str, str | None]:
    """(base, id, problem) for one DRAFT argument."""
    real = _realpath(path)
    base = base_for_path(home.bases, real)
    if base is None:
        return None, "", f"{path} is not inside a registered base"
    if real.parent != base.root / KNOWLEDGE_DIRECTORY / DRAFTS or real.suffix != ".md":
        return base, "", f"{path} is not a {KNOWLEDGE_DIRECTORY}/{DRAFTS}/<id>.md file of base {base.name}"
    if (base.root / KNOWLEDGE_DIRECTORY).is_symlink():
        return base, "", f"{base.root / KNOWLEDGE_DIRECTORY} must not be a symlink"
    if not real.is_file():
        return base, "", f"{path} does not exist"
    return base, real.stem, None


def _validate_draft(
    files: Files, base: Base, identifier: str, path: Path, text: str, linkable: set[str]
) -> tuple[Record | None, Path | None, list[str]]:
    """What ``accept`` makes of one draft selected with the drafts ``linkable`` (folded uids, of any base).

    ``(record, None, problems)`` proposes a new record and ``(record, entry,
    problems)`` the replacement of the accepted ``entry`` with its id, each
    refused while ``problems`` is non-empty; ``(None, entry, [])`` is a draft
    whose entry already holds its text, which ``accept`` only removes;
    ``(None, None, problems)`` is a draft that cannot become a record at all.
    """
    existing = files.ids(base.name, ENTRIES).get(fold(identifier))
    if existing is not None:
        try:
            identical = read_text(existing) == text
        except (OSError, UnicodeDecodeError):
            identical = False
        if identical:
            return None, existing, []
    try:
        record = parse_text(text, base.name, identifier, path)
    except RecordError as error:
        return None, None, [error.message]
    problems, freshness = record_problems(files, record, draft=True, linkable_drafts=linkable)
    messages = [message for _, message in problems]
    if freshness is not None and freshness[0] != "fresh":
        state, proposed = freshness
        where = f" (found at lines {format_ranges(proposed)})" if proposed else ""
        messages.append(f"evidence is {state}{where}; the quotes must occur in the cited lines")
    if existing is not None:
        try:
            if existing.stat().st_mtime_ns > path.stat().st_mtime_ns:
                messages.append(f"{existing} changed after this replacement was drafted; draft it again from the record")
        except OSError as error:
            messages.append(f"cannot compare the draft with {existing}: {error}")
    return record, existing, messages


def accept(paths: Iterable[Path | str], dry_run: bool = False) -> dict[str, Any]:
    """Admit drafts, all or nothing, then index them and embed the records they created or replaced.

    Returns ``{"refused": [{path, message}]}``, ``{"would_create": [uid],
    "would_update": [uid]}`` for a dry run, or the receipt ``{"created": [uid],
    "updated": [uid]}``. A receipt with ``aborted`` lists what was admitted
    before a target appeared. When records were admitted, ``index`` is the
    report of the index run that follows, outside the home lock: every changed
    record file is indexed, but only the admitted records are embedded.
    """
    home = load_home(home_directory())
    selected: list[tuple[Base, str, Path]] = []
    refused: list[dict[str, str]] = []
    seen: set[Path] = set()
    for argument in paths:
        base, identifier, problem = _locate_draft(home, argument)
        if problem is not None:
            refused.append({"path": str(_realpath(argument)), "message": problem})
            continue
        assert base is not None
        path = base.root / KNOWLEDGE_DIRECTORY / DRAFTS / f"{identifier}.md"
        if path not in seen:
            seen.add(path)
            selected.append((base, identifier, path))
    if refused:
        return {"refused": refused}
    with lock():
        receipt = _promote(home, selected, dry_run)
    admitted = [*receipt.get("created", ()), *receipt.get("updated", ())]
    if admitted:
        receipt["index"] = index(only=admitted)
    return receipt


def _promote(home: Home, selected: list[tuple[Base, str, Path]], dry_run: bool) -> dict[str, Any]:
    """The part of ``accept`` under the home lock: validate every draft, then move each into ``entries/``."""
    refused: list[dict[str, str]] = []
    files = Files(home)
    chosen = {f"{base.name}:{fold(identifier)}" for base, identifier, _ in selected}
    plan: list[tuple[Path, Path, str, str]] = []
    for base, identifier, path in selected:
        target = base.root / KNOWLEDGE_DIRECTORY / ENTRIES / f"{identifier}.md"
        try:
            text = read_text(path)
        except (OSError, UnicodeDecodeError) as error:
            refused.append({"path": str(path), "message": f"cannot read the draft: {error}"})
            continue
        record, existing, problems = _validate_draft(
            files, base, identifier, path, text, chosen - {f"{base.name}:{fold(identifier)}"}
        )
        refused.extend({"path": str(path), "message": message} for message in problems)
        if record is None and existing is not None:
            plan.append((path, existing, text, "remove"))
        elif record is not None:
            plan.append((path, existing or target, text, "update" if existing is not None else "create"))
    if refused:
        return {"refused": refused}
    uids = [f"{base.name}:{identifier}" for base, identifier, _ in selected]
    if dry_run:
        return {
            "would_create": [uid for (*_, action), uid in zip(plan, uids, strict=True) if action == "create"],
            "would_update": [uid for (*_, action), uid in zip(plan, uids, strict=True) if action != "create"],
        }
    created: list[str] = []
    updated: list[str] = []
    for (path, target, text, action), uid in zip(plan, uids, strict=True):
        if action != "create":
            # A replacement moves over its record; a draft its record already equals, unchanged since it was
            # drawn from the record or left by an interrupted accept, is only removed.
            if action == "update":
                os.replace(path, target)
            else:
                os.unlink(path)
            updated.append(uid)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, target)
        except FileExistsError:
            try:
                identical = read_text(target) == text
            except (OSError, UnicodeDecodeError):
                identical = False
            if not identical:
                return {
                    "created": created,
                    "updated": updated,
                    "aborted": {"path": str(path), "message": f"{target} appeared with different text; nothing was overwritten"},
                }
        os.unlink(path)
        created.append(uid)
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------- drafts


def _draft_value(files: Files, role: str, position: int, value: Value) -> dict[str, Any]:
    """One value of a draft: a link with the state of its target."""
    found: dict[str, Any] = {"role": role, "pos": position, "uid": value.uid}
    target_base, target = value.uid.split(":", 1)
    accepted = files.record(target_base, ENTRIES, target) if files.available(target_base) else None
    draft = files.record(target_base, DRAFTS, target) if accepted is None and files.available(target_base) else None
    if accepted is not None:
        found.update(state="accepted", label=accepted.label, understanding=accepted.understanding)
    elif draft is not None:
        found.update(state="draft", label=draft.label)
    else:
        found.update(state="missing", label=None)
    return found


def _choices(files: Files, base: Base, record: Record) -> dict[str, Any] | None:
    """What a draft's kind, roles and epistemic may be: its source's type, or for a foreign draft every type
    registered for its base, which `kgd check` keeps from declaring a kind differently; None when there is none."""
    if record.foreign:
        types = [files.home.types[name] for name in sorted(set(base.sources.values())) if name in files.home.types]
    else:
        mapping, _ = files.sources(base)
        name = mapping.get(record.source)
        types = [files.home.types[name]] if name in files.home.types else []
    if not types:
        return None
    node_kinds: dict[str, list[str]] = {}
    relation_kinds: dict[str, list[str]] = {}
    for kind_type in types:
        for kind, roles in kind_type.node_kinds.items():
            node_kinds.setdefault(kind, list(roles))
        for kind, roles in kind_type.relation_kinds.items():
            relation_kinds.setdefault(kind, list(roles))
    return {
        "node_kinds": node_kinds,
        "relation_kinds": relation_kinds,
        "epistemic": list(dict.fromkeys(value for kind_type in types for value in kind_type.epistemic)),
    }


def _reachable(links: dict[str, set[str]], start: str) -> set[str]:
    """The drafts ``start`` links, directly or through other drafts, without ``start`` itself."""
    reached: set[str] = set()
    queue = list(links.get(start, ()))
    while queue:
        key = queue.pop()
        if key not in reached and key != start:
            reached.add(key)
            queue.extend(links.get(key, ()))
    return reached


def drafts(bases: Iterable[str] = (), sources: Iterable[str] = (), names: Iterable[str] = ()) -> dict[str, Any]:
    """Every draft of the available bases, read from files; writes nothing and takes no lock.

    Each draft comes with its fields, each value's target, the drafts it must
    be accepted with (its links to other drafts, of any base, followed
    transitively), the drafts linking it, and ``problems``: what ``accept``
    would refuse when given the draft and those drafts, empty when it would
    accept them. ``bases`` and ``sources`` (base-relative path prefixes) narrow
    the listing; ``names`` keeps the drafts whose label or an alias has one of
    their name keys. A draft that cannot be read or parsed is listed with its
    problem unless a source or name narrows the listing.
    """
    home = load_home(home_directory())
    files = Files(home)
    relations = base_relation_kinds(home)
    wanted = set(bases)
    prefixes = tuple(sources)
    keys = {name_key(name) for name in names} - {""}
    listed: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    unavailable = [name for name in sorted(home.bases) if not files.available(name) and (not wanted or name in wanted)]
    # Drafts link drafts of every base, so every available base's drafts are read before any is listed.
    paths: dict[str, Path] = {}
    texts: dict[str, str] = {}
    parsed: dict[str, Record] = {}
    broken: dict[str, str] = {}
    for name in sorted(home.bases):
        if not files.available(name) or (home.bases[name].root / KNOWLEDGE_DIRECTORY).is_symlink():
            continue
        for key, path in files.ids(name, DRAFTS).items():
            uid = f"{name}:{key}"
            paths[uid] = path
            try:
                texts[uid] = read_text(path)
                parsed[uid] = parse_text(texts[uid], name, path.stem, path)
            except (OSError, UnicodeDecodeError) as error:
                broken[uid] = f"cannot read the draft: {error}"
            except RecordError as error:
                broken[uid] = error.message
    shown = {uid: f"{uid.split(':', 1)[0]}:{path.stem}" for uid, path in paths.items()}
    links = {
        uid: {value.uid for _, _, value in record.values()
              if value.uid in paths and value.uid != uid and value.id not in files.ids(value.base, ENTRIES)}
        for uid, record in parsed.items()
    }

    for key, path in paths.items():
        name = key.split(":", 1)[0]
        if wanted and name not in wanted:
            continue
        base = home.bases[name]
        uid = shown[key]
        linked_by = sorted(shown[other] for other, targets in links.items() if key in targets)
        record = parsed.get(key)
        if record is None:
            if not prefixes and not keys:
                listed.append(((name, "", 0, key), {
                    "uid": uid, "base": name, "id": path.stem, "path": str(path), "label": None,
                    "with": [], "linked_by": linked_by, "problems": [broken[key]],
                }))
            continue
        if prefixes and not any(record.source.startswith(prefix) for prefix in prefixes):
            continue
        if keys and not keys & {name_key(text) for text in (record.label, *record.aliases)}:
            continue
        others = _reachable(links, key)
        _, _, problems = _validate_draft(files, base, record.id, path, texts[key], others)
        listed.append(((name, record.source, record.first_line, key), {
            "uid": uid,
            "base": name,
            "id": record.id,
            "path": str(path),
            "replaces": uid if key.split(":", 1)[1] in files.ids(name, ENTRIES) else None,
            "foreign": record.foreign,
            "class": record_class(relations, name, record.kind),
            "kind": record.kind,
            "label": record.label,
            "aliases": list(record.aliases),
            "source": record.source,
            "lines": record.lines_text,
            "understanding": record.understanding,
            "epistemic": record.epistemic,
            "gloss": gloss(record.body),
            "body": record.body,
            "search_terms": record.search_terms,
            "evidence": list(record.evidence),
            "out": [_draft_value(files, role, position, value) for role, position, value in record.values()],
            "choices": _choices(files, base, record),
            "with": sorted(shown[other] for other in others),
            "linked_by": linked_by,
            "problems": problems,
        }))
    return {"drafts": [item for _, item in sorted(listed, key=lambda pair: pair[0])], "unavailable_bases": unavailable}


# ---------------------------------------------------------------- edit

SCALAR_FIELDS = ("label", "kind", "epistemic", "understanding")
BODY = "body"
_KEY_RE = re.compile(r"^([^\s#:][^:]*?)[ \t]*:")


def _field_lines(key: str, values: list[str]) -> list[str]:
    """A frontmatter field as written by kgd: scalars and list items as JSON strings, which YAML reads unchanged."""
    if key in SCALAR_FIELDS:
        return [f"{key}: {json.dumps(values[0], ensure_ascii=False)}"] if values else []
    return [f"{key}:", *(f"  - {json.dumps(value, ensure_ascii=False)}" for value in values)] if values else []


def _with_field(text: str, key: str, values: list[str]) -> str:
    """The record text with one top-level frontmatter field replaced, added before the closing line, or removed."""
    lines = text.split("\n")
    close = lines.index("---", 1)
    start = next((number for number in range(1, close) if (match := _KEY_RE.match(lines[number])) and match.group(1) == key), None)
    new = _field_lines(key, values)
    if start is None:
        return "\n".join(lines[:close] + new + lines[close:])
    end = start + 1
    while end < close and (lines[end][:1] in (" ", "\t", "-") or not lines[end].strip()):
        end += 1
    return "\n".join(lines[:start] + new + lines[end:])


def _with_body(text: str, prose: str) -> str:
    """The record text with the prose before ``## Search terms`` or ``## Evidence`` replaced."""
    lines = text.split("\n")
    close = lines.index("---", 1)
    body = lines[close + 1 :]
    prose_end, _, _ = body_sections(body)
    return "\n".join([*lines[: close + 1], *prose.strip("\n").split("\n"), "", *body[prose_end:]])


def _links_into(files: Files, base: Base, identifier: str) -> bool:
    """Whether another draft of the base links the draft ``identifier``."""
    uid = f"{base.name}:{fold(identifier)}"
    for other in files.ids(base.name, DRAFTS):
        record = files.record(base.name, DRAFTS, other)
        if record is not None and record.id != identifier and any(value.uid == uid for _, _, value in record.values()):
            return True
    return False


def edit(path: Path | str, key: str, values: Iterable[str]) -> dict[str, Any]:
    """Set one field of a draft: a scalar, a list (aliases, requires or a role), or the prose (``body``).

    The rest of the file is kept as it is. A changed label of a new draft that
    no other draft links also renames the draft to the label's id. The result
    must still parse; whether ``kgd accept`` would take it is what ``drafts``
    reports. ``source`` and ``lines`` move with ``kgd relocate``.
    """
    home = load_home(home_directory())
    base, identifier, problem = _locate_draft(home, path)
    if problem is not None:
        raise KnowledgeError(problem)
    assert base is not None
    draft = base.root / KNOWLEDGE_DIRECTORY / DRAFTS / f"{identifier}.md"
    values = [value for value in values]
    if key in ("source", "lines"):
        raise KnowledgeError(f"{key} moves with `kgd relocate`, not `kgd edit`")
    if key in ("tags", "cssclasses"):
        raise KnowledgeError(f"{key} is Obsidian's own property; edit it in Obsidian")
    if key in SCALAR_FIELDS and len(values) > 1:
        raise KnowledgeError(f"{key} takes one value")
    if key in ("label", "kind") and not (values and values[0].strip()):
        raise KnowledgeError(f"{key} must not be empty")
    if key == "understanding" and values and values[0] == "yes":
        values = []
    if key == "epistemic" and values and not values[0].strip():
        values = []
    before = read_text(draft)
    try:
        after = _with_body(before, values[0] if values else "") if key == BODY else _with_field(before, key, values)
        record = parse_text(after, base.name, identifier, draft)
    except RecordError as error:
        raise KnowledgeError(f"the draft would not parse: {error.message}") from error
    target = draft
    files = Files(home)
    if key == "label" and fold(identifier) not in files.ids(base.name, ENTRIES) and not _links_into(files, base, identifier):
        wanted = slug(record.label)
        taken = (set(files.ids(base.name, ENTRIES)) | set(files.ids(base.name, DRAFTS))) - {fold(identifier)}
        candidate, number = wanted, 2
        while candidate and candidate in taken:
            suffix = f"-{number}"
            candidate, number = f"{wanted[: MAX_ID_LENGTH - len(suffix)]}{suffix}", number + 1
        if candidate and candidate != identifier:
            target = draft.with_name(f"{candidate}.md")
    if read_text(draft) != before:
        raise KnowledgeError(f"{draft} changed while it was edited; edit it again")
    if target == draft:
        atomic_write_text(draft, after)
    else:
        with open(target, "x", encoding="utf-8", newline="") as handle:
            handle.write(after)
        os.unlink(draft)
    return {"draft": str(target), "uid": f"{base.name}:{target.stem}",
            **({"renamed_from": f"{base.name}:{identifier}"} if target != draft else {})}


# ---------------------------------------------------------------- source


def _source(home: Home, source: Path | str) -> tuple[Base, str, DocumentType]:
    real = _realpath(source)
    base = base_for_path(home.bases, real)
    if base is None:
        raise KnowledgeError(f"{source} is not inside a registered base")
    _knowledge(base)
    relative = real.relative_to(base.root).as_posix()
    if not real.is_file():
        raise KnowledgeError(f"{source} is not a file")
    return base, relative, base.type_of(relative)


def _records(base: Base, folder: str, relative: str) -> list[Record]:
    found = []
    for path in record_files(base.root, folder):
        try:
            record = parse_record(path, base.name)
        except (RecordError, OSError, UnicodeDecodeError):
            continue
        if record.source == relative:
            found.append(record)
    return sorted(found, key=lambda record: (record.first_line, record.id))


def source(path: Path | str) -> dict[str, Any]:
    """The extraction profile of one registered source and the records and drafts citing it; writes nothing."""
    home = load_home(home_directory())
    base, relative, document_type = _source(home, path)
    return {
        "base": base.name,
        "source": relative,
        "type": document_type.name,
        "node_kinds": {kind: list(roles) for kind, roles in document_type.node_kinds.items()},
        "relation_kinds": {kind: list(roles) for kind, roles in document_type.relation_kinds.items()},
        "epistemic": list(document_type.epistemic),
        "guidance": document_type.guidance,
        "line_count": len(source_lines(base.root / relative)),
        "records": [{
            "uid": record.uid,
            "label": record.label,
            "kind": record.kind,
            "class": "relation" if record.kind in document_type.relation_kinds else "node",
            "lines": record.lines_text,
            "understanding": record.understanding,
            "gloss": gloss(record.body),
        } for record in _records(base, ENTRIES, relative)],
        "drafts": [{
            "id": record.id,
            "label": record.label,
            "kind": record.kind,
            "class": "relation" if record.kind in document_type.relation_kinds else "node",
            "lines": record.lines_text,
        } for record in _records(base, DRAFTS, relative)],
    }
