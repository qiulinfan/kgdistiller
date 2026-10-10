"""Knowledge records: the file format, its grammar, and file-only validation.

A record is ``<root>/.knowledge/entries/<id>.md`` (accepted) or
``<root>/.knowledge/drafts/<id>.md`` (proposed): YAML frontmatter read with
PyYAML ``BaseLoader`` followed by a Markdown body that ends with an
``## Evidence`` section of verbatim source quotes. A record whose non-fixed
keys hold a non-empty list is a relation; any other record is a node.

``check`` reads files only. Evidence freshness compares whitespace-normalized
quote text with the cited source lines; nothing is hashed. The product never
re-serializes frontmatter: ``fix_lines`` rewrites the one ``lines:`` line
textually.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from .home import (
    CONFIG_FILENAME,
    FIXED_KEYS,
    KNOWLEDGE_DIRECTORY,
    SLUG_RE,
    TYPES_DIRECTORY,
    Base,
    Home,
    KnowledgeError,
    atomic_write_text,
    home_directory,
    load_home,
    load_type,
    lock,
)

UNDERSTANDING = ("unknown", "not-yet-understood", "understood")
ENTRIES = "entries"
DRAFTS = "drafts"
SHEETS = "sheets"
SEARCH_TERMS = "Search terms"
EVIDENCE = "Evidence"
MAX_ID_LENGTH = 80
GLOSS_LIMIT = 240

IGNORED_KEYS = frozenset({"tags", "cssclasses"})
LIST_KEYS = frozenset({"aliases", "requires"})
SCALAR_KEYS = FIXED_KEYS - LIST_KEYS - IGNORED_KEYS
REQUIRED_KEYS = ("label", "kind", "source", "lines")
ID_RE = re.compile(r"^[^\W_]+(?:-[^\W_]+)*$")
LINES_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
LINK_RE = re.compile(r"\[\[([^\[\]]*)\]\]")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SENTENCE_RE = re.compile(r".*?(?:[。！？]|[.!?](?=\s|$))", re.DOTALL)
ENTRY_PATH_PREFIX = f"{KNOWLEDGE_DIRECTORY}/{ENTRIES}/"
LINES_KEY_RE = re.compile(r"^lines[ \t]*:")


class RecordError(ValueError):
    """A record that does not follow the format; ``rule`` names the check rule."""

    def __init__(self, rule: str, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.reason = reason


# ---------------------------------------------------------------- text


def read_text(path: Path | str) -> str:
    """Read UTF-8 text with a leading BOM dropped and universal newlines."""
    with open(path, encoding="utf-8-sig", newline=None) as handle:
        return handle.read()


def split_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def source_lines(path: Path | str) -> list[str]:
    """The file's 1-based lines (index 0 is line 1), as every reader counts them."""
    return split_lines(read_text(path))


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


# ---------------------------------------------------------------- ids


def valid_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(value)) and value == fold(value) and len(value) <= MAX_ID_LENGTH


def slug(label: str) -> str:
    """The creation rule for ids; an empty result means the writer chooses an id."""
    value = re.sub(r"[\W_]+", "-", fold(label)).strip("-")
    if len(value) > MAX_ID_LENGTH:
        cut = value.rfind("-", 0, MAX_ID_LENGTH + 1)
        value = value[:cut] if cut > 0 else value[:MAX_ID_LENGTH]
    return value


# ---------------------------------------------------------------- values


@dataclass(frozen=True)
class Value:
    """One value of ``requires`` or a role: a link resolved textually to a uid, or a pending term."""

    raw: str
    uid: str | None = None
    term: str | None = None

    @property
    def base(self) -> str | None:
        return self.uid.split(":", 1)[0] if self.uid else None

    @property
    def id(self) -> str | None:
        return self.uid.split(":", 1)[1] if self.uid else None


def _value_error(reason: str, message: str) -> RecordError:
    return RecordError("link", message, reason)


def parse_value(raw: str, base: str) -> Value:
    """Apply the value grammar for a record of ``base``."""
    text = raw.strip()
    if "\n" in text:
        raise _value_error("multiline", f"value {raw!r} must be a single line")
    if "[[" not in text and "]]" not in text:
        if not text:
            raise _value_error("empty", "a value must not be empty")
        return Value(raw, term=text)
    match = LINK_RE.fullmatch(text)
    if match is None:
        raise _value_error("stray-brackets", f"{raw!r} has '[[' or ']]' but is not one whole link")
    target = match.group(1).split("|", 1)[0].strip()
    if not target:
        raise _value_error("empty", f"{raw!r} links nothing")
    if "#" in target or "^" in target:
        raise _value_error("fragment", f"{raw!r} links a heading or block; link the record itself")
    target = fold(target).removesuffix(".md")
    if "/" in target:
        identifier = target.removeprefix(ENTRY_PATH_PREFIX)
        if not target.startswith(ENTRY_PATH_PREFIX) or not identifier or "/" in identifier or ":" in identifier:
            raise _value_error(
                "path-form", f"{raw!r} uses a path; only [[{ENTRY_PATH_PREFIX}<id>]] is allowed"
            )
        return Value(raw, uid=f"{base}:{identifier}")
    if ":" in target:
        foreign, identifier = target.split(":", 1)
        if foreign == base:
            raise _value_error("own-base", f"{raw!r} prefixes a local link with its own base; write [[{identifier}]]")
        if not SLUG_RE.fullmatch(foreign) or not identifier or ":" in identifier:
            raise _value_error("foreign-form", f"{raw!r} is not a [[base:id]] link")
        return Value(raw, uid=f"{foreign}:{identifier}")
    return Value(raw, uid=f"{base}:{target}")


# ---------------------------------------------------------------- body


def _trim(lines: list[str]) -> str:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def parse_body(body: str) -> tuple[str, str, tuple[str, ...]]:
    """Split a body into prose, search terms and evidence quotes."""
    lines = body.split("\n")
    headings: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(lines):
        match = FENCE_RE.match(line)
        if fence is not None:
            stripped = line.strip()
            if match and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
            continue
        if match:
            fence = match.group(1)
        elif line.startswith("## "):
            headings.append((number, line[3:].strip()))
    if not headings or headings[-1][1] != EVIDENCE:
        raise RecordError("body", f"the body must end with a '## {EVIDENCE}' section")
    if any(title == EVIDENCE for _, title in headings[:-1]):
        raise RecordError("body", f"the body must have exactly one '## {EVIDENCE}' section")
    evidence_at = headings[-1][0]
    search_at = headings[-2][0] if len(headings) > 1 and headings[-2][1] == SEARCH_TERMS else None
    if any(title == SEARCH_TERMS for number, title in headings if number != search_at):
        raise RecordError("body", f"'## {SEARCH_TERMS}' must be the heading immediately before '## {EVIDENCE}'")
    prose_end = evidence_at if search_at is None else search_at
    prose = _trim(lines[:prose_end])
    search_terms = "" if search_at is None else _trim(lines[search_at + 1 : evidence_at])
    quotes: list[str] = []
    current: list[str] | None = None
    for line in lines[evidence_at + 1 :]:
        if line.startswith(">"):
            current = [] if current is None else current
            current.append(line[1:].removeprefix(" "))
        elif line.strip():
            raise RecordError("body", f"the {EVIDENCE} section holds only '>' quote lines and blank lines")
        elif current is not None:
            quotes.append("\n".join(current))
            current = None
    if current is not None:
        quotes.append("\n".join(current))
    if not quotes:
        raise RecordError("body", f"the {EVIDENCE} section needs at least one '>' quote")
    if any(not normalize_space(quote) for quote in quotes):
        raise RecordError("body", f"an {EVIDENCE} quote is empty")
    return prose, search_terms, tuple(quotes)


def gloss(prose: str) -> str:
    """The first sentence of the first prose paragraph that is not a heading."""
    for paragraph in re.split(r"\n[ \t]*\n", prose):
        lines = [line.strip() for line in paragraph.strip().split("\n")]
        if not lines[0] or lines[0].startswith("#"):
            continue
        joined = " ".join(line for line in lines if line)
        match = SENTENCE_RE.match(joined)
        return (match.group(0) if match else joined)[:GLOSS_LIMIT]
    return ""


# ---------------------------------------------------------------- records


@dataclass(frozen=True)
class Record:
    base: str
    id: str
    path: Path
    label: str
    kind: str
    source: str
    lines: tuple[int, int]
    aliases: tuple[str, ...]
    understanding: str
    epistemic: str | None
    requires: tuple[Value, ...]
    roles: tuple[tuple[str, tuple[Value, ...]], ...]
    body: str
    search_terms: str
    evidence: tuple[str, ...]
    class_: str

    @property
    def uid(self) -> str:
        return f"{self.base}:{self.id}"

    @property
    def lines_text(self) -> str:
        return f"{self.lines[0]}-{self.lines[1]}"

    def values(self) -> Iterator[tuple[str, int, Value]]:
        """Every value as (role, position, value): roles in frontmatter order, then requires."""
        for role, values in self.roles:
            for position, value in enumerate(values):
                yield role, position, value
        for position, value in enumerate(self.requires):
            yield "requires", position, value


def split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    if lines[0] != "---":
        raise RecordError("frontmatter", "the file must start with a '---' line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise RecordError("frontmatter", "the frontmatter has no closing '---' line") from None
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :])


def _nested(item: Any) -> str:
    """The text of an unquoted ``[[x]]`` that YAML read as nested lists."""
    return ", ".join(_nested(part) for part in item) if isinstance(item, list) else str(item)


def _single_line(key: str, value: str) -> str:
    value = value.strip()
    if "\n" in value:
        raise RecordError("frontmatter", f"{key} must be a single line")
    return value


def record_class(frontmatter: dict[str, Any]) -> str:
    """The shared class rule: a relation iff a non-fixed key holds a non-empty list."""
    return "relation" if any(
        isinstance(value, list) and value for key, value in frontmatter.items() if key not in FIXED_KEYS
    ) else "node"


def parse_text(text: str, base: str, identifier: str, path: Path) -> Record:
    """Structural parse: frontmatter shape, value grammar and body grammar."""
    if not valid_id(identifier):
        suggestion = slug(identifier)
        raise RecordError(
            "id",
            f"the file name {identifier!r} is not a valid id: Unicode letters and digits (CJK included) "
            f"joined by single hyphens, equal to its own NFKC casefold, at most {MAX_ID_LENGTH} characters"
            + (f"; for example {suggestion!r}" if suggestion else ""),
        )
    head, body = split_frontmatter(text)
    try:
        data = yaml.load(head, Loader=yaml.BaseLoader)
    except yaml.YAMLError as error:
        raise RecordError("frontmatter", f"invalid YAML frontmatter: {error}") from error
    if not isinstance(data, dict):
        raise RecordError("frontmatter", "the frontmatter must be a mapping")
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    roles: list[tuple[str, list[str]]] = []
    for key, value in data.items():
        if not isinstance(key, str):
            raise RecordError("frontmatter", f"frontmatter key {key!r} must be a string")
        if key in IGNORED_KEYS:
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, list):
                    raise RecordError("link", f'{key}: quote wikilinks: write "[[{_nested(item)}]]"', "unquoted")
                if not isinstance(item, str):
                    raise RecordError("frontmatter", f"{key} must be a list of strings")
        elif not isinstance(value, str):
            raise RecordError("frontmatter", f"{key} must be a string or a list of strings")
        elif value == "" and key not in SCALAR_KEYS:
            value = []
        if key in SCALAR_KEYS:
            if not isinstance(value, str):
                raise RecordError("frontmatter", f"{key} must be a single value, not a list")
            scalars[key] = _single_line(key, value)
        elif isinstance(value, str):
            hint = "" if key in LIST_KEYS else f'; write {key}: ["…"]'
            raise RecordError("frontmatter", f"{key} must be a list{hint}")
        elif key in LIST_KEYS:
            lists[key] = value
        else:
            roles.append((key, value))
    for key in REQUIRED_KEYS:
        if not scalars.get(key):
            raise RecordError("frontmatter", f"the frontmatter needs a non-empty {key}")
    match = LINES_RE.fullmatch(scalars["lines"])
    if match is None:
        raise RecordError("frontmatter", f"lines must be 'a' or 'a-b', not {scalars['lines']!r}")
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    if not 1 <= start <= end:
        raise RecordError("frontmatter", f"lines {scalars['lines']} must satisfy 1 <= a <= b")
    understanding = scalars.get("understanding") or "unknown"
    if understanding not in UNDERSTANDING:
        raise RecordError("frontmatter", f"understanding must be one of {', '.join(UNDERSTANDING)}")
    aliases = tuple(_single_line("aliases", alias) for alias in lists.get("aliases", []))
    if any(not alias for alias in aliases):
        raise RecordError("frontmatter", "aliases must not be empty")
    prose, search_terms, quotes = parse_body(body)
    parsed_roles = tuple((role, tuple(parse_value(item, base) for item in items)) for role, items in roles)
    return Record(
        base=base,
        id=identifier,
        path=path,
        label=scalars["label"],
        kind=scalars["kind"],
        source=scalars["source"],
        lines=(start, end),
        aliases=aliases,
        understanding=understanding,
        epistemic=scalars.get("epistemic") or None,
        requires=tuple(parse_value(item, base) for item in lists.get("requires", [])),
        roles=parsed_roles,
        body=prose,
        search_terms=search_terms,
        evidence=quotes,
        class_=record_class(data),
    )


def parse_record(path: Path, base: str) -> Record:
    return parse_text(read_text(path), base, path.stem, path)


# ---------------------------------------------------------------- evidence


def evidence_state(quotes: Iterable[str], lines: list[str], start: int, end: int) -> tuple[str, tuple[int, int] | None]:
    """``("fresh", None)``, ``("moved", (a, b))`` or ``("stale", None)``."""
    normalized = [normalize_space(quote) for quote in quotes]
    if 1 <= start <= end <= len(lines):
        cited = normalize_space("\n".join(lines[start - 1 : end]))
        if all(quote in cited for quote in normalized):
            return "fresh", None
    parts: list[str] = []
    offsets: list[int] = []
    numbers: list[int] = []
    position = 0
    for number, line in enumerate(lines, 1):
        words = line.split()
        if not words:
            continue
        chunk = " ".join(words)
        if parts:
            position += 1
        offsets.append(position)
        numbers.append(number)
        parts.append(chunk)
        position += len(chunk)
    flat = " ".join(parts)
    spans: list[tuple[int, int]] = []
    for quote in normalized:
        first = flat.find(quote)
        if first < 0 or flat.find(quote, first + 1) >= 0:
            return "stale", None
        last = first + len(quote) - 1
        spans.append((numbers[bisect_right(offsets, first) - 1], numbers[bisect_right(offsets, last) - 1]))
    return "moved", (min(a for a, _ in spans), max(b for _, b in spans))


# ---------------------------------------------------------------- file view


def knowledge_folder(root: Path, folder: str) -> Path:
    return root / KNOWLEDGE_DIRECTORY / folder


def record_files(root: Path, folder: str) -> list[Path]:
    """The regular ``*.md`` files of one knowledge folder; symlinks are never records."""
    directory = knowledge_folder(root, folder)
    if directory.is_symlink() or not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if path.is_file() and not path.is_symlink())


def symlinked_files(root: Path, folder: str) -> list[Path]:
    """The ``*.md`` symlinks of one knowledge folder, which ``check`` and ``index`` refuse."""
    directory = knowledge_folder(root, folder)
    if directory.is_symlink() or not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if path.is_symlink())


def valid_source_path(source: str) -> bool:
    if not source or source.startswith("/") or "\\" in source or PureWindowsPath(source).drive:
        return False
    return all(segment and segment != ".." for segment in source.split("/"))


class Files:
    """A lazily read, cached file-only view of the registered bases."""

    def __init__(self, home: Home) -> None:
        self.home = home
        self._folders: dict[tuple[str, str], dict[str, Path]] = {}
        self._records: dict[Path, Record | None] = {}
        self._sources: dict[str, tuple[dict[str, str], dict[str, list[str]]]] = {}
        self._lines: dict[Path, list[str]] = {}
        self._names: dict[str, dict[str, list[str]]] = {}

    def available(self, name: str) -> bool:
        base = self.home.bases.get(name)
        return base is not None and base.root.is_dir()

    def ids(self, name: str, folder: str) -> dict[str, Path]:
        """Folded id -> file for one folder of an available base."""
        key = (name, folder)
        if key not in self._folders:
            files = record_files(self.home.bases[name].root, folder) if self.available(name) else []
            self._folders[key] = {fold(path.stem): path for path in files}
        return self._folders[key]

    def record(self, name: str, folder: str, identifier: str) -> Record | None:
        path = self.ids(name, folder).get(identifier)
        if path is None:
            return None
        if path not in self._records:
            try:
                self._records[path] = parse_record(path, name)
            except (RecordError, OSError, UnicodeDecodeError):
                self._records[path] = None
        return self._records[path]

    def label(self, uid: str, *, drafts: bool = False) -> str | None:
        name, identifier = uid.split(":", 1)
        if not self.available(name):
            return None
        found = self.record(name, ENTRIES, identifier)
        if found is None and drafts:
            found = self.record(name, DRAFTS, identifier)
        return found.label if found else None

    def sources(self, base: Base) -> tuple[dict[str, str], dict[str, list[str]]]:
        if base.name not in self._sources:
            self._sources[base.name] = base.source_types()
        return self._sources[base.name]

    def lines(self, path: Path) -> list[str]:
        if path not in self._lines:
            self._lines[path] = source_lines(path)
        return self._lines[path]

    def named(self, name: str, key: str) -> list[str]:
        """Accepted records of a base whose label or an alias has this name key."""
        from .index import name_key

        if name not in self._names:
            names: dict[str, list[str]] = {}
            for identifier in sorted(self.ids(name, ENTRIES)):
                found = self.record(name, ENTRIES, identifier)
                if found is None:
                    continue
                for text in dict.fromkeys(name_key(text) for text in (found.label, *found.aliases)):
                    names.setdefault(text, []).append(found.uid)
            self._names[name] = names
        return self._names[name].get(key, [])


def _dangling(files: Files, value: Value) -> str:
    from .index import name_key

    message = f"dangling link {value.raw.strip()}"
    owners = files.named(value.base, name_key(value.id)) if value.base and files.available(value.base) else []
    if owners:
        message += "; records named so: " + ", ".join(owners) + " (link them by id)"
    return message


def record_problems(
    files: Files, record: Record, *, draft: bool, linkable_drafts: Iterable[str] = ()
) -> tuple[list[tuple[str, str]], tuple[str, tuple[int, int] | None] | None]:
    """The source, kind and link rules of `kgd check` for one parsed record, plus its evidence freshness.

    ``linkable_drafts`` are the folded ids of same-base drafts this record may
    link: none for an accepted record, every draft in ``check``, and the
    selected drafts in ``accept``.
    """
    problems: list[tuple[str, str]] = []
    freshness: tuple[str, tuple[int, int] | None] | None = None
    base = files.home.bases[record.base]
    mapping, conflicts = files.sources(base)
    source = record.source
    if not valid_source_path(source):
        problems.append(("source", f"source {source!r} must be a POSIX path relative to the base root"))
    elif source in conflicts:
        problems.append(("source", f"source {source} matches globs of several types: {', '.join(conflicts[source])}"))
    elif source not in mapping:
        problems.append(("source", (
            f"{source} is not a registered source of base {base.name}; add a glob for it under "
            f"bases.{base.name}.sources in {CONFIG_FILENAME}"
        )))
    elif not base.holds(source):
        problems.append(("source", f"source {source} resolves outside the base root through a symlink"))
    else:
        document_type = files.home.types[mapping[source]]
        try:
            lines = files.lines(base.root / source)
        except UnicodeDecodeError:
            problems.append(("source", f"source {source} is not UTF-8 text"))
        except OSError as error:
            problems.append(("source", f"source {source} cannot be read: {error.strerror or error}"))
        else:
            if record.lines[1] > len(lines):
                problems.append(("source", f"lines {record.lines_text} exceed the {len(lines)} lines of {source}"))
            freshness = evidence_state(record.evidence, lines, *record.lines)
        role_keys = [role for role, _ in record.roles]
        if record.kind in document_type.node_kinds:
            if role_keys:
                problems.append(("kind", f"{record.kind} is a node kind of {document_type.name}; remove the keys {', '.join(role_keys)}"))
        elif record.kind in document_type.relation_kinds:
            declared = document_type.relation_kinds[record.kind]
            for role in role_keys:
                if role not in declared:
                    problems.append((
                        "frontmatter",
                        f"{role} is not a role of {record.kind}; its roles are {', '.join(declared)}",
                    ))
            if record.class_ != "relation":
                problems.append(("kind", f"the relation {record.kind} needs at least one participant"))
        else:
            problems.append(("kind", f"kind {record.kind!r} is not a node or relation kind of type {document_type.name}"))
        if record.epistemic is not None and record.epistemic not in document_type.epistemic:
            allowed = ", ".join(document_type.epistemic) or "no epistemic values"
            problems.append(("kind", f"epistemic {record.epistemic!r} is not allowed by {document_type.name} ({allowed})"))
    linkable = set(linkable_drafts)
    for role, position, value in record.values():
        if value.uid is None:
            continue
        where = f"{role}[{position}]"
        if value.uid == record.uid:
            problems.append(("link", f"{where} links the record itself"))
            continue
        target_base, target = value.base, value.id
        if target_base not in files.home.bases:
            problems.append(("link", f"{where} links base {target_base}, which is not registered"))
        elif not files.available(target_base):
            problems.append(("link", f"{where} links base {target_base}, which is unavailable"))
        elif target in files.ids(target_base, ENTRIES) or (target_base == record.base and target in linkable):
            continue
        elif target in files.ids(target_base, DRAFTS):
            if target_base != record.base:
                problems.append(("link", f"{where} links a draft of base {target_base}; foreign links must target accepted records"))
            elif draft:
                problems.append(("link", f"{where}: select [[{target}]] too"))
            else:
                problems.append(("link", f"{where} links the draft [[{target}]]; accept it first"))
        else:
            problems.append(("link", f"{where}: {_dangling(files, value)}"))
    return problems, freshness


# ---------------------------------------------------------------- check


def _load_for_check(home_dir: Path, errors: list[dict[str, str]]) -> Home | None:
    """The home, or None after recording every type file's error and config.json's own error.

    config.json is validated without its type names even when a type file is
    broken; a glob naming an unknown type (or a missing types/) is a type error.
    """
    config = str(home_dir / CONFIG_FILENAME)
    types = home_dir / TYPES_DIRECTORY
    if (home_dir / CONFIG_FILENAME).is_file() and types.is_dir():
        for path in sorted(types.glob("*.md")):
            try:
                load_type(path)
            except (KnowledgeError, OSError, UnicodeDecodeError) as error:
                errors.append({"path": str(path), "rule": "type", "message": str(error)})
    try:
        load_home(home_dir, types=False)
    except (KnowledgeError, OSError, UnicodeDecodeError) as error:
        errors.append({"path": config, "rule": "config", "message": str(error)})
    if errors:
        return None
    try:
        return load_home(home_dir)
    except (KnowledgeError, OSError, UnicodeDecodeError) as error:
        errors.append({"path": config, "rule": "type", "message": str(error)})
        return None


def _check(home_dir: Path, bases: Iterable[str] | None) -> tuple[dict[str, Any], dict[Path, tuple[str, tuple[int, int]]]]:
    errors: list[dict[str, str]] = []
    report: dict[str, Any] = {"errors": errors, "stale": [], "moved": []}
    moved_texts: dict[Path, tuple[str, tuple[int, int]]] = {}
    home = _load_for_check(home_dir, errors)
    if home is None:
        return report, moved_texts

    def error(path: Path, rule: str, message: str) -> None:
        errors.append({"path": str(path), "rule": rule, "message": message})

    names = sorted(home.bases) if bases is None else list(dict.fromkeys(bases))
    files = Files(home)
    for name in names:
        base = home.bases.get(name)
        if base is None:
            error(home_dir / CONFIG_FILENAME, "config", f"unknown base {name!r}; registered bases: {', '.join(sorted(home.bases)) or '(none)'}")
            continue
        if not base.root.is_dir():
            error(home_dir / CONFIG_FILENAME, "config", f"base {name} is unavailable: {base.path} is not a directory")
            continue
        if (base.root / KNOWLEDGE_DIRECTORY).is_symlink():
            error(base.root / KNOWLEDGE_DIRECTORY, "config", "the knowledge tree must not be a symlink")
            continue
        _, conflicts = files.sources(base)
        for relative, type_names in sorted(conflicts.items()):
            error(base.root / relative, "source-type", f"{relative} matches globs of several types: {', '.join(type_names)}")
        entry_ids = files.ids(name, ENTRIES)
        draft_ids = files.ids(name, DRAFTS)
        for folder in (ENTRIES, DRAFTS):
            draft = folder == DRAFTS
            if knowledge_folder(base.root, folder).is_symlink():
                error(knowledge_folder(base.root, folder), "config", f"{KNOWLEDGE_DIRECTORY}/{folder} must not be a symlink")
                continue
            for path in symlinked_files(base.root, folder):
                error(path, "frontmatter", "a record file must be a regular file, not a symlink")
            seen: set[str] = set()
            for path in record_files(base.root, folder):
                key = fold(path.stem)
                if key in seen:
                    error(path, "id", f"id {path.stem} repeats another file of {folder}/ case-insensitively")
                    continue
                seen.add(key)
                try:
                    text = read_text(path)
                except (OSError, UnicodeDecodeError) as problem:
                    error(path, "frontmatter", f"cannot read the record as UTF-8 text: {problem}")
                    continue
                if draft and key in entry_ids:
                    accepted = entry_ids[key]
                    try:
                        identical = read_text(accepted) == text
                    except (OSError, UnicodeDecodeError):
                        identical = False
                    if identical:
                        error(path, "id", f"an interrupted accept left this draft beside {accepted}; rerun `kgd accept {path}` to finish it")
                    else:
                        error(path, "id", f"id {path.stem} already exists in {ENTRIES}/; edit that record in place or choose a new id")
                    continue
                try:
                    record = parse_text(text, name, path.stem, path)
                except RecordError as problem:
                    error(path, problem.rule, problem.message)
                    continue
                problems, freshness = record_problems(
                    files, record, draft=draft, linkable_drafts=draft_ids if draft else ()
                )
                for rule, message in problems:
                    error(path, rule, message)
                if freshness is None:
                    continue
                state, proposed = freshness
                if state == "stale":
                    report["stale"].append(str(path))
                elif state == "moved" and proposed is not None:
                    report["moved"].append({"path": str(path), "lines": f"{proposed[0]}-{proposed[1]}"})
                    moved_texts[path] = (text, proposed)
    return report, moved_texts


def check(home: Path | None = None, bases: Iterable[str] | None = None) -> dict[str, Any]:
    """Validate the home and the records of the selected bases (default: all), reading files only."""
    report, _ = _check(home_directory() if home is None else home, bases)
    return report


def links_into(home: Home, name: str) -> list[dict[str, str]]:
    """Every ``[[<name>:…]]`` value in the entries and drafts of the other available bases.

    After ``base rm NAME`` these links dangle; unparseable files are left to ``check``.
    """
    found: list[dict[str, str]] = []
    for other in sorted(home.bases):
        root = home.bases[other].root
        if other == name or not root.is_dir() or (root / KNOWLEDGE_DIRECTORY).is_symlink():
            continue
        for folder in (ENTRIES, DRAFTS):
            for path in record_files(root, folder):
                try:
                    record = parse_record(path, other)
                except (RecordError, OSError, UnicodeDecodeError):
                    continue
                for role, _, value in record.values():
                    if value.base == name:
                        found.append({"path": str(path), "role": role, "value": value.raw.strip()})
    return found


def _rewrite_lines(raw: str, proposed: tuple[int, int]) -> str | None:
    """Replace the frontmatter's one ``lines:`` line, keeping every other character."""
    pieces = re.split(r"(\r\n|\r|\n)", raw)
    texts, endings = pieces[0::2], pieces[1::2] + [""]
    first = texts[0].removeprefix("﻿")
    if first != "---":
        return None
    try:
        close = texts.index("---", 1)
    except ValueError:
        return None
    found = [number for number in range(1, close) if LINES_KEY_RE.match(texts[number])]
    if len(found) != 1:
        return None
    a, b = proposed
    texts[found[0]] = f"lines: {a}-{b}" if a != b else f"lines: {a}"
    return "".join(text + ending for text, ending in zip(texts, endings, strict=True))


def fix_lines(home: Path | None = None, bases: Iterable[str] | None = None) -> dict[str, Any]:
    """Check, then rewrite the ``lines:`` of every moved file that did not change meanwhile."""
    home_dir = home_directory() if home is None else home
    if not (home_dir / CONFIG_FILENAME).is_file():
        report, _ = _check(home_dir, bases)
        return {**report, "fixed": [], "skipped": []}
    with lock():
        report, moved_texts = _check(home_dir, bases)
        fixed: list[str] = []
        skipped: list[str] = []
        remaining: list[dict[str, str]] = []
        for item in report["moved"]:
            path = Path(item["path"])
            text, proposed = moved_texts[path]
            try:
                unchanged = read_text(path) == text
                rewritten = _rewrite_lines(path.read_bytes().decode("utf-8"), proposed) if unchanged else None
            except (OSError, UnicodeDecodeError):
                rewritten = None
            if rewritten is None:
                skipped.append(str(path))
                remaining.append(item)
                continue
            atomic_write_text(path, rewritten)
            fixed.append(str(path))
        report["moved"] = remaining
    return {**report, "fixed": fixed, "skipped": skipped}

