"""Find text in the registered sources and relocate a record to a passage: ``kgd find`` and ``kgd relocate``.

Sources change: lines shift, files are renamed, merged and split. ``find``
locates a passage the owner gives, or every place a name occurs, in the
sources; ``relocate`` moves one record's citation there, into another base with
the record when the passage lies there. Without a passage, ``relocate`` follows
the record's markers (see ``markers``); with ``--url`` it makes the record
foreign, citing the web page whose text is given. Both are deterministic and
load no model.

Matching compares word tokens, not characters: the text and every source are
read as NFKC-casefolded runs of letters and digits, each CJK character a token
of its own. A long text (at least ``SKIP_FROM`` tokens) matches a span of a
source that holds its tokens in order with at most a quarter as many others
between them, so a passage copied from a reading view, which hides link
targets and markup, still finds the lines that hold it; a short text, such as a
name, matches its tokens exactly in sequence. Line breaks never matter.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .home import (
    FIXED_KEYS,
    KNOWLEDGE_DIRECTORY,
    Home,
    KnowledgeError,
    atomic_write_text,
    base_for_path,
    home_directory,
    load_home,
    lock,
)
from .index import index, is_cjk
from .records import (
    DRAFTS,
    ENTRIES,
    LINES_KEY_RE,
    LINK_RE,
    Files,
    RecordError,
    fold,
    format_ranges,
    normalize_space,
    parse_text,
    parse_value,
    read_text,
    record_files,
    record_problems,
)

FIND_LIMIT = 20
SKIP_FROM = 8
TEXT_LIMIT = 2000
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
SOURCE_KEY_RE = re.compile(r"^source[ \t]*:")
KEY_RE = re.compile(r"^([^\s#:-][^:]*?)[ \t]*:")
# Punctuation that ends a sentence or closes a bracket, kept with a matched span.
CLOSERS = frozenset("。．.!?！？;；)）]］」』”’\"'*_")


def _pieces(text: str) -> list[tuple[str, int, int]]:
    """The tokens of one line with their character offsets: word runs, each CJK character on its own."""
    pieces: list[tuple[str, int, int]] = []
    for word in _WORD.finditer(text):
        start = word.start()
        for offset, char in enumerate(word.group(), word.start()):
            if is_cjk(char):
                if start < offset:
                    pieces.append((text[start:offset], start, offset))
                pieces.append((char, offset, offset + 1))
                start = offset + 1
        if start < word.end():
            pieces.append((text[start:word.end()], start, word.end()))
    return [(unicodedata.normalize("NFKC", piece).casefold(), start, end) for piece, start, end in pieces]


def match_tokens(text: str) -> list[str]:
    """The tokens matching compares: NFKC-casefolded word runs, each CJK character on its own."""
    return [token for line in text.splitlines() for token, _, _ in _pieces(line)]


# A token's place in a source: (line number, start offset, end offset).
Place = tuple[int, int, int]


def _line_tokens(lines: list[str]) -> tuple[list[str], list[Place]]:
    tokens: list[str] = []
    places: list[Place] = []
    for number, line in enumerate(lines, 1):
        for token, start, end in _pieces(line):
            tokens.append(token)
            places.append((number, start, end))
    return tokens, places


def _span_text(lines: list[str], first: Place, last: Place) -> str:
    """The source's own text from the first matched token to the last, with the punctuation that closes it."""
    (start_line, start, _), (end_line, _, end) = first, last
    tail = lines[end_line - 1]
    while end < len(tail) and tail[end] in CLOSERS:
        end += 1
    if start_line == end_line:
        return tail[start:end]
    return "\n".join([lines[start_line - 1][start:], *lines[start_line:end_line - 1], tail[:end]])


def _spans(query: list[str], tokens: list[str]) -> list[tuple[int, int, int]]:
    """Every non-overlapping span of ``tokens`` that holds ``query`` in order: (first, last, tokens skipped)."""
    budget = len(query) // 4 if len(query) >= SKIP_FROM else 0
    spans: list[tuple[int, int, int]] = []
    start = 0
    while start < len(tokens):
        try:
            first = tokens.index(query[0], start)
        except ValueError:
            break
        position, skipped, matched = first + 1, 0, 1
        while matched < len(query) and position < len(tokens) and skipped <= budget:
            if tokens[position] == query[matched]:
                matched += 1
            else:
                skipped += 1
            position += 1
        if matched == len(query) and skipped <= budget:
            spans.append((first, position - 1, skipped))
            start = position
        else:
            start = first + 1
    return spans


@dataclass(frozen=True)
class Match:
    base: str
    source: str
    first: int
    last: int
    skipped: int
    text: str

    @property
    def lines(self) -> str:
        return format_ranges(((self.first, self.last),))

    def report(self) -> dict[str, Any]:
        text = self.text if len(self.text) <= TEXT_LIMIT else self.text[:TEXT_LIMIT] + "…"
        return {"base": self.base, "source": self.source, "lines": self.lines, "exact": self.skipped == 0, "text": text}


def _matches(home: Home, text: str, bases: Iterable[str] = (), sources: Iterable[str] = ()) -> list[Match]:
    query = match_tokens(text)
    if not query:
        raise KnowledgeError("the text has no letters or digits to find")
    wanted = set(bases)
    unknown = sorted(wanted - set(home.bases))
    if unknown:
        raise KnowledgeError(f"unknown base {unknown[0]!r}")
    prefixes = tuple(sources)
    found: list[Match] = []
    files = Files(home)
    for name in sorted(home.bases):
        base = home.bases[name]
        if (wanted and name not in wanted) or not files.available(name):
            continue
        for relative in files.source_files(base):
            if prefixes and not relative.startswith(prefixes):
                continue
            try:
                lines = files.lines(base.root / relative)
            except (OSError, UnicodeDecodeError):
                continue
            tokens, places = _line_tokens(lines)
            for first, last, skipped in _spans(query, tokens):
                found.append(Match(name, relative, places[first][0], places[last][0], skipped,
                                   _span_text(lines, places[first], places[last])))
    return found


def find(text: str, bases: Iterable[str] = (), sources: Iterable[str] = (), limit: int = FIND_LIMIT) -> dict[str, Any]:
    """Where ``text`` occurs in the registered sources, by base, source and line; reads files only."""
    if limit < 1:
        raise KnowledgeError("limit must be at least 1")
    home = load_home(home_directory(), types=False)
    found = _matches(home, text, bases, sources)
    return {"matches": [match.report() for match in found[:limit]], "total": len(found)}


# ---------------------------------------------------------------- relocate


def _target(home: Home, target: str) -> tuple[str, Path]:
    """(base, record file) for a uid of an accepted record, or a path to a record or draft file."""
    if "/" not in target and not target.endswith(".md"):
        if ":" not in target:
            raise KnowledgeError(f"{target} is neither a uid base:id nor a record or draft file")
        name, identifier = target.split(":", 1)
        base = home.bases.get(name)
        if base is None:
            raise KnowledgeError(f"unknown base {name!r}")
        if not base.root.is_dir():
            raise KnowledgeError(f"base {name} is unavailable: {base.root} is not a directory")
        path = base.root / ".knowledge" / ENTRIES / f"{fold(identifier)}.md"
        if not path.is_file():
            raise KnowledgeError(f"{target} is not an accepted record; pass a draft by its path")
        return name, path
    real = Path(os.path.realpath(Path(target).expanduser()))
    base = base_for_path(home.bases, real)
    if base is None or real.suffix != ".md" or real.parent.name not in (ENTRIES, DRAFTS) \
            or real.parent.parent != base.root / ".knowledge" or not real.is_file():
        raise KnowledgeError(f"{target} is not a record or draft file of a registered base")
    return base.name, real


def _rewritten(raw: str, source: str, lines: str | None, quotes: list[str] | None) -> str:
    """The record text with its ``source:`` and ``lines:`` lines replaced (``lines:`` added after ``source:``
    when missing, removed when ``lines`` is None) and, when ``quotes`` is given, its Evidence section replaced;
    every other character kept."""
    pieces = re.split(r"(\r\n|\r|\n)", raw)
    texts, endings = pieces[0::2], pieces[1::2] + [""]
    newline = endings[0] or "\n"
    close = texts.index("---", 1)
    head = [number for number in range(1, close) if SOURCE_KEY_RE.match(texts[number])]
    found = [number for number in range(1, close) if LINES_KEY_RE.match(texts[number])]
    if len(head) != 1 or len(found) > 1:
        raise KnowledgeError("the frontmatter must have one source: line and at most one lines: line to relocate")
    texts[head[0]] = f"source: {json.dumps(source, ensure_ascii=False)}"
    if lines is None:
        if found:
            del texts[found[0]], endings[found[0]]
    elif found:
        texts[found[0]] = f"lines: {lines}"
    else:
        texts.insert(head[0] + 1, f"lines: {lines}")
        endings.insert(head[0] + 1, newline)
    text = "".join(text + ending for text, ending in zip(texts, endings, strict=True))
    if quotes is None:
        return text
    at = text.rfind(f"{newline}## Evidence")
    blocks = [newline.join(f"> {line}" if line else ">" for line in quote.split("\n")) for quote in quotes]
    return text[:at] + f"{newline}## Evidence{newline}{newline}" + f"{newline}{newline}".join(blocks) + newline


def _paragraphs(text: str) -> list[str]:
    """The matched lines as quotes: one per run of non-blank lines, so no quote spans a blank line."""
    quotes: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            quotes.append("\n".join(current))
            current = []
    if current:
        quotes.append("\n".join(current))
    return quotes


def _relinked(raw: str, read: str, write: str, moved: tuple[str, str] | None) -> str:
    """``raw`` with its link values rewritten, every other character kept.

    A value read as a link of a record of base ``read`` is written as a link of
    base ``write``; with ``moved`` (old uid, new uid), only values naming the old
    uid change, to name the new one. Only role and ``requires`` values change.
    """
    pieces = re.split(r"(\r\n|\r|\n)", raw)
    texts = pieces[0::2]
    close = texts.index("---", 1)

    def form(uid: str) -> str:
        name, identifier = uid.split(":", 1)
        return identifier if name == write else uid

    def link(match: re.Match[str]) -> str:
        try:
            uid = parse_value(match.group(0), read).uid
        except RecordError:
            return match.group(0)
        if moved is not None:
            if uid != moved[0]:
                return match.group(0)
            uid = moved[1]
        display = match.group(1).partition("|")[2]
        return f"[[{form(uid)}{'|' + display if display else ''}]]"

    key = ""
    for number in range(1, close):
        found = KEY_RE.match(texts[number])
        if found:
            key = found.group(1).strip()
        if key == "requires" or key not in FIXED_KEYS:
            texts[number] = LINK_RE.sub(link, texts[number])
    pieces[0::2] = texts
    return "".join(pieces)


def _inbound(files: Files, old: str, new: str, skip: Path) -> list[tuple[Path, str, str]]:
    """(file, its text, the text relinked) for every record and draft of every base that links ``old``.

    A file that is not UTF-8 or has no closed frontmatter is left to ``check``, as ``links_into`` does.
    """
    identifier = old.split(":", 1)[1]
    found: list[tuple[Path, str, str]] = []
    for name in sorted(files.home.bases):
        for folder in (ENTRIES, DRAFTS):
            for path in record_files(files.home.bases[name].root, folder):
                if path == skip:
                    continue
                try:
                    raw = path.read_bytes().decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                lines = re.split(r"\r\n|\r|\n", raw)
                if identifier not in fold(raw) or lines[0] != "---" or "---" not in lines[1:]:
                    continue
                relinked = _relinked(raw, name, name, (old, new))
                if relinked != raw:
                    found.append((path, raw, relinked))
    return found


def relocate(
    target: str, text: str | None = None, sources: Iterable[str] = (), bases: Iterable[str] = (),
    dry_run: bool = False, url: str | None = None,
) -> dict[str, Any]:
    """Move one record's citation: to the only passage of the sources that holds ``text``; without ``text``, to
    where its markers say it went; with ``url``, to that web page, whose text ``text`` gives.

    The record keeps everything but its ``source``, ``lines`` and, when its
    quotes do not all lie in the passage, its Evidence, which becomes the
    passage's lines (or, for a web page, the given text). A foreign record
    becomes local and a local one given ``url`` foreign. When the passage lies
    in another base, the record moves there with its id, its links are
    rewritten for that base and every link to it is rewritten to its new uid.
    Refuses, writing nothing, when the passage is not found exactly once or no
    marker leads to quotes that still hold.
    """
    from .markers import Names, follow
    from .records import is_foreign

    if url is not None and (not is_foreign(url) or not text or not normalize_space(text)):
        raise KnowledgeError("--url takes a web address and the page's own text as the passage")
    home = load_home(home_directory())
    with lock():
        name, path = _target(home, target)
        draft = path.parent.name == DRAFTS
        raw = path.read_bytes().decode("utf-8")
        record = parse_text(read_text(path), name, path.stem, path)
        files = Files(home)
        if url is not None:
            match = Match(name, url, 0, 0, 0, text or "")
        elif text is None:
            followed = follow(files, Names(files), record)
            if followed is None:
                return {"refused": {
                    "message": "no marker names the record; pass the passage's text, or --url and the page's text "
                               "to make it foreign",
                    "matches": [],
                }}
            if "lines" not in followed:
                return {"refused": {
                    "message": "markers name the record, but its quotes no longer hold where they are; pass the "
                               "new passage's text",
                    "markers": followed["markers"],
                }}
            match = Match(followed["base"], followed["source"], 0, 0, 0, "")
            match_lines = followed["lines"]
        else:
            found = _matches(home, text, bases, sources)
            if len(found) != 1:
                return {"refused": {
                    "message": (f"the passage occurs {len(found)} times; pass --base or --source to choose one"
                                if found else "the passage does not occur in a source"),
                    "matches": [match.report() for match in found[:FIND_LIMIT]],
                }}
            match = found[0]
        if text is not None:
            match_lines = None if url is not None else match.lines
        old = f"{name}:{fold(path.stem)}"
        new = f"{match.base}:{fold(path.stem)}"
        destination = path
        relinked: list[tuple[Path, str, str]] = []
        if match.base != name:
            folder = DRAFTS if draft else ENTRIES
            destination = home.bases[match.base].root / KNOWLEDGE_DIRECTORY / folder / path.name
            unavailable = sorted(other for other in home.bases if not files.available(other))
            if unavailable:
                raise KnowledgeError(f"base {unavailable[0]} is unavailable; its links to {old} could not be rewritten")
            if fold(path.stem) in files.ids(match.base, ENTRIES) or fold(path.stem) in files.ids(match.base, DRAFTS):
                raise KnowledgeError(f"base {match.base} already has a record or draft {path.stem}; it cannot move there")
            if draft and fold(path.stem) in files.ids(name, ENTRIES):
                raise KnowledgeError(f"{path.name} replaces the accepted record {old} and stays in its base; "
                                     "relocate the record, or accept or discard the draft first")
            if not draft and fold(path.stem) in files.ids(name, DRAFTS):
                raise KnowledgeError(f"a replacement draft of {old} waits; accept or discard it before moving the record")
            relinked = _inbound(files, old, new, path)
        span = normalize_space(match.text)
        keep = text is None or (url is None and all(normalize_space(quote) in span for quote in record.evidence))
        quotes = None if keep else _paragraphs(match.text)
        if not keep and url is None:
            # The old quotes no longer lie in the passage; its own lines become the evidence.
            lines_text = "\n".join(files.lines(home.bases[match.base].root / match.source)[match.first - 1 : match.last])
            keep = all(normalize_space(quote) in normalize_space(lines_text) for quote in record.evidence)
            quotes = None if keep else quotes
        relocated = _rewritten(raw, match.source, match_lines, quotes)
        if match.base != name:
            relocated = _relinked(relocated, name, match.base, None)
        moved = parse_text(relocated.replace("\r\n", "\n").replace("\r", "\n").removeprefix("\ufeff"),
                           match.base, path.stem, destination)
        problems, freshness = record_problems(files, moved, draft=draft, linkable_drafts=files.drafts())
        # Its links and its label are not this command's concern; only where it cites and what it quotes changed.
        problems = [message for rule, message in problems if rule not in ("link", "label")]
        if freshness is not None and freshness[0] != "fresh":
            problems.append(f"the relocated evidence is {freshness[0]}")
        if problems:
            raise KnowledgeError("the relocated record would not pass check: " + "; ".join(problems))
        report: dict[str, Any] = {
            "relocated": new, "path": str(destination),
            "from": {"source": record.source, "lines": record.lines_text},
            "to": {"source": match.source, "lines": match_lines or ""},
            "evidence": "kept" if keep else "replaced",
        }
        if match.base != name:
            report["moved"] = {"from": old, "to": new}
            report["relinked"] = [str(item[0]) for item in relinked]
        if dry_run:
            return {**report, "dry_run": True}
        for changed, before, _ in [(path, raw, None), *relinked]:
            if changed.read_bytes().decode("utf-8") != before:
                raise KnowledgeError(f"{changed} changed while it was relocated; run the command again")
        atomic_write_text(destination, relocated)
        for changed, _, after in relinked:
            atomic_write_text(changed, after)
        if destination != path:
            path.unlink()
    if not draft or any(item[0].parent.name == ENTRIES for item in relinked):
        report["index"] = index(embed=False)
    return report
