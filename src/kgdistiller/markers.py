"""Knowledge markers: where a source declares a record, in the source's own syntax.

A marker names one record at the place its source states it:

- Markdown and plain text: ``--[[NAME]]--``, or ``--[[NAME|TEXT]]--`` when the
  text shown differs from the name;
- LaTeX: ``\\kn{NAME}``, or ``\\kn[NAME]{TEXT}``.

NAME resolves by name key to the record whose label, label without its
``{scope}·`` prefix, alias or id it is, among the accepted records and the
drafts of every available base. When several records answer to the name, the
one citing the marker's own file wins, then one of the file's base. A marker
lies on one line and never changes the source's line count, so inserting one
moves no other record's lines. Markers are found by reading the sources; no
marker is stored anywhere else.

A record is located through its markers in three rounds: its own source file,
then every source of its base, then every source of the other bases. ``check``
uses the rounds to follow a record whose source was renamed or moved, and
``relocate`` without a passage uses them to find where the record went.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .home import (
    Base,
    KnowledgeError,
    atomic_write_text,
    base_for_path,
    home_directory,
    load_home,
    lock,
    source_format,
)
from .index import name_key
from .records import (
    DRAFTS,
    ENTRIES,
    Files,
    Record,
    fold,
    normalize_space,
    parse_text,
    read_text,
)

MD_MARKER = re.compile(r"--\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]*))?\]\]--")
TEX_MARKER = re.compile(r"\\kn(?:\[([^\]\n]*)\])?\{((?:[^{}\n]|\{(?:[^{}\n]|\{[^{}\n]*\})*\})*)\}")
SCOPE_SEPARATOR = "·"
# Characters a marker cannot carry in its NAME: such a record is marked by its id.
UNMARKABLE = re.compile(r"[\[\]|{}\n]")
ROUNDS = ("source", "base", "bases")


@dataclass(frozen=True)
class Marker:
    base: str
    source: str
    line: int
    start: int
    end: int
    name: str
    text: str

    def report(self) -> dict[str, Any]:
        return {"base": self.base, "source": self.source, "line": self.line, "column": self.start + 1,
                "name": self.name, "text": self.text}


def scan(lines: list[str], fmt: str) -> list[tuple[int, int, int, str, str]]:
    """Every marker of a source's lines as (line, start, end, name, text); ``fmt`` is md, tex or txt."""
    found: list[tuple[int, int, int, str, str]] = []
    for number, line in enumerate(lines, 1):
        if fmt == "tex":
            code = line
            comment = re.search(r"(?<!\\)%", line)
            if comment:
                code = line[: comment.start()]
            for match in TEX_MARKER.finditer(code):
                target, shown = match.group(1), match.group(2)
                name = (target if target and target.strip() else shown).strip()
                if name:
                    found.append((number, match.start(), match.end(), name, shown))
        else:
            for match in MD_MARKER.finditer(line):
                name = match.group(1).strip()
                if name:
                    found.append((number, match.start(), match.end(), name, match.group(2) or name))
    return found


def unscoped(label: str) -> str:
    """A label without its ``{scope}·`` prefix."""
    return label.rsplit(SCOPE_SEPARATOR, 1)[1] if SCOPE_SEPARATOR in label else label


def marker_name(record: Record) -> str:
    """The NAME that marks a record: its label, or its id when the label holds a character a marker cannot."""
    return record.id if UNMARKABLE.search(record.label) else record.label


class Names:
    """Name key -> the records (accepted and drafts, of every available base) a marker NAME may mean."""

    def __init__(self, files: Files) -> None:
        self.files = files
        self.keys: dict[str, dict[str, Record]] = {}
        for base in sorted(files.home.bases):
            for folder in (DRAFTS, ENTRIES):
                for identifier in sorted(files.ids(base, folder)):
                    record = files.record(base, folder, identifier)
                    if record is None:
                        continue
                    for text in (record.label, unscoped(record.label), *record.aliases, record.id):
                        key = name_key(text)
                        if key:
                            self.keys.setdefault(key, {})[record.uid] = record

    def resolve(self, name: str, base: str, source: str) -> tuple[str | None, list[str]]:
        """(the uid NAME means in ``source`` of ``base``, or None; every uid it may mean)."""
        candidates = self.keys.get(name_key(name), {})
        uids = sorted(candidates)
        if len(uids) <= 1:
            return (uids[0] if uids else None), uids
        for narrowed in (
            [uid for uid, record in candidates.items() if record.base == base and record.source == source],
            [uid for uid, record in candidates.items() if record.base == base],
        ):
            if len(narrowed) == 1:
                return narrowed[0], uids
        return None, uids


def file_markers(files: Files, base: Base, relative: str) -> list[Marker]:
    """The markers of one source of ``base``; an unreadable file has none."""
    fmt = source_format(relative)
    if fmt is None:
        return []
    try:
        lines = files.lines(base.root / relative)
    except (OSError, UnicodeDecodeError):
        return []
    return [Marker(base.name, relative, *found) for found in scan(lines, fmt)]


def _rounds(files: Files, record: Record) -> list[tuple[str, list[tuple[Base, str]]]]:
    """The files each round searches: the record's own source, the other sources of its base, then the other bases."""
    home = files.home
    own = home.bases[record.base]
    first = [(own, record.source)] if not record.foreign and own.is_source(record.source) else []
    second = [(own, relative) for relative in files.source_files(own) if relative != record.source]
    third = [(home.bases[name], relative) for name in sorted(home.bases)
             if name != record.base and files.available(name) for relative in files.source_files(home.bases[name])]
    return [("source", first), ("base", second), ("bases", third)]


def locate(files: Files, names: Names, record: Record) -> tuple[str | None, list[Marker]]:
    """(the round that found the record's markers, those markers), searching round by round; (None, []) when no
    marker in any source names the record."""
    for round_name, candidates in _rounds(files, record):
        found = [marker for base, relative in candidates for marker in file_markers(files, base, relative)
                 if names.resolve(marker.name, marker.base, marker.source)[0] == record.uid]
        if found:
            return round_name, found
    return None, []


def follow(files: Files, names: Names, record: Record) -> dict[str, Any] | None:
    """Where a record's markers say it went, for a record whose quotes are gone from its cited file.

    Returns None when no marker names it. With its markers in exactly one file whose text holds every quote
    exactly once, returns that file's base, source and the quotes' lines; otherwise the markers found and
    ``"evidence": "absent"``, which only a new passage (``kgd relocate`` with its text) resolves.
    """
    from .records import evidence_state, format_ranges

    round_name, found = locate(files, names, record)
    if round_name is None:
        return None
    targets: list[tuple[str, str, str]] = []
    for base_name, relative in dict.fromkeys((marker.base, marker.source) for marker in found):
        try:
            lines = files.lines(files.home.bases[base_name].root / relative)
        except (OSError, UnicodeDecodeError):
            continue
        state, proposed = evidence_state(record.evidence, lines, ())
        if state == "moved" and proposed is not None:
            targets.append((base_name, relative, format_ranges(proposed)))
    if len(targets) == 1:
        base_name, relative, lines_text = targets[0]
        return {"round": round_name, "base": base_name, "source": relative, "lines": lines_text}
    return {"round": round_name, "evidence": "absent", "markers": [marker.report() for marker in found]}


def markers(sources: Iterable[Path | str] = (), uids: Iterable[str] = (), bases: Iterable[str] = (),
            orphans: bool = False) -> dict[str, Any]:
    """The markers of the given source files, of the given records, or of whole bases, each with the record it
    names; ``orphans`` keeps only markers that name no record or several. Reads files only."""
    home = load_home(home_directory())
    files = Files(home)
    names = Names(files)
    found: list[Marker] = []
    wanted_sources = list(sources)
    wanted_uids = [fold(uid) for uid in uids]
    for source in wanted_sources:
        real = Path(source).expanduser().resolve()
        base = base_for_path(home.bases, real)
        relative = real.relative_to(base.root).as_posix() if base is not None else ""
        if base is None or not base.is_source(relative):
            raise KnowledgeError(f"{source} is not a source of a registered base (formats: md, tex, txt)")
        found.extend(file_markers(files, base, relative))
    for uid in wanted_uids:
        record = _record(files, uid)
        found.extend(locate(files, names, record)[1])
    if not wanted_sources and not wanted_uids:
        selected = list(bases) or sorted(home.bases)
        unknown = sorted(set(selected) - set(home.bases))
        if unknown:
            raise KnowledgeError(f"unknown base {unknown[0]!r}")
        for name in selected:
            if files.available(name):
                base = home.bases[name]
                for relative in files.source_files(base):
                    found.extend(file_markers(files, base, relative))
    listed: list[dict[str, Any]] = []
    for marker in found:
        uid, candidates = names.resolve(marker.name, marker.base, marker.source)
        if orphans and uid is not None:
            continue
        record = names.keys.get(name_key(marker.name), {}).get(uid) if uid else None
        listed.append({**marker.report(), "uid": uid, "candidates": candidates,
                       "path": str(record.path) if record is not None else None})
    return {"markers": listed, "total": len(listed)}


def _record(files: Files, uid: str) -> Record:
    if ":" not in uid:
        raise KnowledgeError(f"{uid} is not a uid base:id")
    base, identifier = uid.split(":", 1)
    if base not in files.home.bases or not files.available(base):
        raise KnowledgeError(f"base {base!r} is not a registered, available base")
    record = files.record(base, ENTRIES, identifier) or files.record(base, DRAFTS, identifier)
    if record is None:
        raise KnowledgeError(f"{uid} is neither an accepted record nor a draft")
    return record


# ---------------------------------------------------------------- mark

MD_BLOCK_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _md_protected(lines: list[str]) -> list[bool]:
    """Per line, whether a marker must not be written on it in Markdown: fenced code, display math, headings,
    tables, HTML blocks and the frontmatter."""
    protected = [False] * len(lines)
    fence: str | None = None
    math = False
    for number, line in enumerate(lines):
        stripped = line.strip()
        if number == 0 and stripped == "---":
            fence = "---"
            protected[number] = True
            continue
        if fence == "---":
            protected[number] = True
            if stripped == "---":
                fence = None
            continue
        match = MD_BLOCK_FENCE.match(line)
        if fence is not None:
            protected[number] = True
            if match and stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}:
                fence = None
            continue
        if match:
            fence = match.group(1)
            protected[number] = True
            continue
        if math:
            protected[number] = True
            if stripped.endswith("$$"):
                math = False
            continue
        if stripped.startswith("$$"):
            protected[number] = True
            math = not (len(stripped) > 2 and stripped.endswith("$$"))
            continue
        if stripped.startswith(("#", "|", "<", "![")):
            protected[number] = True
    return protected


def _in_inline_math(line: str, column: int) -> bool:
    return len(re.findall(r"(?<!\\)\$", line[:column])) % 2 == 1


def _quote_start(lines: list[str], quote: str, ranges: tuple[tuple[int, int], ...]) -> tuple[int, int] | None:
    """(line, column) where a quote's first word starts inside the cited ranges, or None."""
    wanted = normalize_space(quote)[:200]
    first = wanted.split(" ")[0]
    if not first:
        return None
    for start, end in ranges:
        for number in range(start, min(end, len(lines)) + 1):
            text = lines[number - 1]
            column = text.find(first)
            while column >= 0:
                if normalize_space(text[column:] + " " + " ".join(lines[number:end])).startswith(wanted):
                    return number, column
                column = text.find(first, column + 1)
    return None


def _md_place(lines: list[str], line: int, column: int) -> tuple[int, int, str] | None:
    """Where a Markdown marker may go for a quote starting at (line, column): (line, column, separator)."""
    protected = _md_protected(lines)
    text = lines[line - 1]
    if not protected[line - 1]:
        if _in_inline_math(text, column):
            column = 0
        prefix = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)*", text)
        column = max(column, prefix.end() if prefix else 0)
        return line, column, ""
    for number in range(line - 1, max(0, line - 40), -1):
        if not lines[number - 1].strip() and not protected[number - 1]:
            return number, 0, ""
    return None


def mark(draft: Path | str, name: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Write a marker naming a draft into its source, before its first quote; the line count stays the same.

    Markdown and plain text get ``--[[NAME]]--:``, introducing the passage it names (on the nearest blank line
    above when the quote opens inside a heading, table, code or math block); LaTeX gets ``\\kn[NAME]{}``, which
    shows nothing in print. NAME defaults to the draft's label, or its id
    when the label holds a character a marker cannot carry. Refuses when the source already marks the draft.
    """
    home = load_home(home_directory())
    real = Path(draft).expanduser().resolve()
    base = base_for_path(home.bases, real)
    if base is None or real.parent.name != DRAFTS or real.parent.parent != base.root / ".knowledge" or not real.is_file():
        raise KnowledgeError(f"{draft} is not a draft of a registered base")
    with lock():
        files = Files(home)
        record = parse_text(read_text(real), base.name, real.stem, real)
        if record.foreign:
            raise KnowledgeError(f"{record.uid} is foreign: its evidence lies on a web page, which takes no marker")
        if not base.is_source(record.source):
            raise KnowledgeError(f"{record.source} is not a source of base {base.name}")
        names = Names(files)
        target = name or marker_name(record)
        if UNMARKABLE.search(target):
            raise KnowledgeError(f"a marker NAME cannot hold [ ] | {{ }}: {target!r}")
        resolved, candidates = names.resolve(target, base.name, record.source)
        if resolved != record.uid:
            raise KnowledgeError(f"the NAME {target!r} would mean {resolved or 'several records: ' + ', '.join(candidates)}, "
                                 f"not {record.uid}; pass --name with its id")
        existing = [marker for marker in file_markers(files, base, record.source)
                    if names.resolve(marker.name, marker.base, marker.source)[0] == record.uid]
        if existing:
            return {"marked": record.uid, "already": [marker.report() for marker in existing]}
        path = base.root / record.source
        raw = path.read_bytes().decode("utf-8")
        pieces = re.split(r"(\r\n|\r|\n)", raw)
        texts = pieces[0::2]
        lines = files.lines(path)
        start = _quote_start(lines, record.evidence[0], record.lines)
        if start is None:
            raise KnowledgeError(f"the first Evidence quote of {record.uid} does not occur in its cited lines; "
                                 "run kgd check and fix the draft first")
        fmt = source_format(record.source)
        if fmt == "tex":
            line, column, written = start[0], start[1], f"\\kn[{target}]{{}}"
        else:
            place = _md_place(lines, *start)
            if place is None:
                raise KnowledgeError(f"no line near {record.source}:{start[0]} takes a marker; mark it by hand")
            line, column, _ = place
            written = f"--[[{target}]]--:"
            if lines[line - 1].strip() and column < len(texts[line - 1]) and not texts[line - 1][column].isspace():
                written += " "
        text = texts[line - 1]
        texts[line - 1] = text[:column] + written + text[column:]
        pieces[0::2] = texts
        report = {"marked": record.uid, "source": record.source, "line": line, "column": column + 1, "marker": written.strip()}
        if dry_run:
            return {**report, "dry_run": True}
        if path.read_bytes().decode("utf-8") != raw:
            raise KnowledgeError(f"{path} changed while it was marked; run the command again")
        atomic_write_text(path, "".join(pieces))
    return report
