"""Find text in the registered sources and relocate a record to a passage: ``kgd find`` and ``kgd relocate``.

Sources change: lines shift, files are renamed, merged and split. Nothing
follows them automatically. ``find`` locates a passage the owner gives, or
every place a name occurs, in the registered sources; ``relocate`` moves one
record's citation there. Both are deterministic and load no model.

Matching compares word tokens, not characters: the text and every source are
read as NFKC-casefolded runs of letters and digits, each CJK character a token
of its own. A long text (at least ``SKIP_FROM`` tokens) matches a span of a
source that holds its tokens in order with at most a quarter as many others
between them, so a passage copied from a reading view, which hides link
targets and markup, still finds the lines that hold it; a short text, such as a
name, matches its tokens exactly in sequence. Line breaks never matter.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .home import (
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
    Files,
    fold,
    format_ranges,
    normalize_space,
    parse_text,
    read_text,
    record_problems,
)

FIND_LIMIT = 20
SKIP_FROM = 8
TEXT_LIMIT = 2000
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
SOURCE_KEY_RE = re.compile(r"^source[ \t]*:")
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
        mapping, _ = files.sources(base)
        for relative in sorted(mapping):
            if prefixes and not relative.startswith(prefixes):
                continue
            if not base.holds(relative):
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


def _rewritten(raw: str, source: str, lines: str, quotes: list[str] | None) -> str:
    """The record text with its ``source:`` and ``lines:`` lines replaced (``lines:`` added after ``source:``
    when missing) and, when ``quotes`` is given, its Evidence section replaced; every other character kept."""
    pieces = re.split(r"(\r\n|\r|\n)", raw)
    texts, endings = pieces[0::2], pieces[1::2] + [""]
    newline = endings[0] or "\n"
    close = texts.index("---", 1)
    head = [number for number in range(1, close) if SOURCE_KEY_RE.match(texts[number])]
    found = [number for number in range(1, close) if LINES_KEY_RE.match(texts[number])]
    if len(head) != 1 or len(found) > 1:
        raise KnowledgeError("the frontmatter must have one source: line and at most one lines: line to relocate")
    texts[head[0]] = f'source: "{source}"'
    if found:
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


def relocate(target: str, text: str, sources: Iterable[str] = (), dry_run: bool = False) -> dict[str, Any]:
    """Move one record's citation to the only passage of its base that holds ``text``.

    The record keeps everything but its ``source``, ``lines`` and, when its
    quotes do not all lie in the passage, its Evidence, which becomes the
    passage's lines. A foreign record becomes local. Refuses, writing nothing,
    when the passage is not found exactly once in the record's base.
    """
    home = load_home(home_directory())
    with lock():
        name, path = _target(home, target)
        raw = path.read_bytes().decode("utf-8")
        record = parse_text(read_text(path), name, path.stem, path)
        files = Files(home)
        found = _matches(home, text, (name,), sources)
        if len(found) != 1:
            elsewhere = [match for match in _matches(home, text, (), sources) if match.base != name] if not found else []
            return {"refused": {
                "message": (f"the passage occurs {len(found)} times in base {name}; pass --source to choose one"
                            if found else f"the passage does not occur in base {name}"
                            + ("; it occurs in another base, and moving a record across bases is a relocation "
                               "of the knowledge model, not this command" if elsewhere else "")),
                "matches": [match.report() for match in (found or elsewhere)[:FIND_LIMIT]],
            }}
        match = found[0]
        span = normalize_space(match.text)
        keep = all(normalize_space(quote) in span for quote in record.evidence)
        quotes = None if keep else _paragraphs(match.text)
        if not keep:
            # The old quotes no longer lie in the passage; its own lines become the evidence.
            lines_text = "\n".join(files.lines(home.bases[name].root / match.source)[match.first - 1 : match.last])
            keep = all(normalize_space(quote) in normalize_space(lines_text) for quote in record.evidence)
            quotes = None if keep else quotes
        relocated = _rewritten(raw, match.source, match.lines, quotes)
        moved = parse_text(relocated.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿"), name, path.stem, path)
        problems, freshness = record_problems(files, moved, draft=path.parent.name == DRAFTS,
                                              linkable_drafts=files.ids(name, DRAFTS))
        # Its links are not this command's concern; only where it cites and what it quotes changed.
        problems = [message for rule, message in problems if rule != "link"]
        if freshness is not None and freshness[0] != "fresh":
            problems.append(f"the relocated evidence is {freshness[0]}")
        if problems:
            raise KnowledgeError("the relocated record would not pass check: " + "; ".join(problems))
        report: dict[str, Any] = {
            "relocated": f"{name}:{path.stem}", "path": str(path),
            "from": {"source": record.source, "lines": record.lines_text},
            "to": {"source": match.source, "lines": match.lines},
            "evidence": "kept" if keep else "replaced",
        }
        if dry_run:
            return {**report, "dry_run": True}
        if path.read_bytes().decode("utf-8") != raw:
            raise KnowledgeError(f"{path} changed while it was relocated; run the command again")
        atomic_write_text(path, relocated)
    if path.parent.name == ENTRIES:
        report["index"] = index(embed=False)
    return report

