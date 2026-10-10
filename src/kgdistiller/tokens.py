"""Shared lexical tokenizer for formal and compiled retrieval."""

from __future__ import annotations

import re
import unicodedata
from itertools import pairwise

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _run_tokens(run: str, cjk: bool) -> list[str]:
    if not cjk:
        return [run]
    return list(run) + [a + b for a, b in pairwise(run)]


def tokenize(text: str) -> list[str]:
    """Return NFKC/casefolded Unicode words, with CJK runs as unigrams and bigrams.

    No domain lexicon is used: a CJK run yields every character and every
    adjacent character pair, so a query for a sub-word matches longer terms.
    """
    result: list[str] = []
    for word in _WORD.findall(unicodedata.normalize("NFKC", text).casefold()):
        run = ""
        previous_cjk: bool | None = None
        for char in word:
            cjk = unicodedata.name(char, "").startswith(
                ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")
            )
            if run and cjk != previous_cjk:
                result.extend(_run_tokens(run, bool(previous_cjk)))
                run = ""
            run += char
            previous_cjk = cjk
        result.extend(_run_tokens(run, bool(previous_cjk)))
    return result
