"""Small, position-preserving syntax helpers for explicit LaTeX markers.

This is not a TeX interpreter: it does not expand macros, load packages, or
follow included files. Environment declarations only describe the bounds and
kind of an explicitly marked statement; they never define graph identities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATEMENT_KINDS = frozenset(
    {"definition", "theorem", "lemma", "corollary", "proposition", "axiom", "example"}
)
LITERAL_ENVIRONMENTS = frozenset(
    {
        "verbatim", "verbatim*", "Verbatim", "BVerbatim", "LVerbatim", "SaveVerbatim",
        "lstlisting", "minted", "comment", "filecontents", "filecontents*", "luacode",
        "luacode*",
    }
)
_INLINE_LITERALS = frozenset({"verb", "Verb", "lstinline", "mintinline"})
_CONDITIONALS = frozenset(
    {
        "if", "ifcat", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode",
        "ifinner", "ifvoid", "ifhbox", "ifvbox", "ifx", "ifeof", "iftrue", "iffalse",
        "ifcase", "ifdefined", "ifcsname", "iffontchar",
    }
)
_COMMAND_DEFINITIONS = frozenset(
    {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"}
)
_ENVIRONMENT_DEFINITIONS = frozenset(
    {"newenvironment", "renewenvironment", "provideenvironment"}
)
_DOCUMENT_COMMAND_DEFINITIONS = frozenset(
    {"NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand", "DeclareDocumentCommand"}
)
_DOCUMENT_ENVIRONMENT_DEFINITIONS = frozenset(
    {"NewDocumentEnvironment", "RenewDocumentEnvironment", "ProvideDocumentEnvironment", "DeclareDocumentEnvironment"}
)
_PRIMITIVE_DEFINITIONS = frozenset({"def", "gdef", "edef", "xdef"})


@dataclass(frozen=True)
class StatementRange:
    start: int
    end: int
    kind: str


def _error(text: str, position: int, message: str) -> ValueError:
    line = text.count("\n", 0, position) + 1
    return ValueError(f"{message} at line {line} (offset {position})")


def _command(text: str, position: int) -> tuple[str, int]:
    """Read one TeX control word or control symbol, including escaped slashes."""
    cursor = position + 1
    if cursor >= len(text):
        return "", cursor
    if text[cursor].isascii() and (text[cursor].isalpha() or text[cursor] == "@"):
        cursor += 1
        while cursor < len(text) and text[cursor].isascii() and (
            text[cursor].isalpha() or text[cursor] == "@"
        ):
            cursor += 1
        return text[position + 1:cursor], cursor
    return text[cursor], cursor + 1


def _comment_end(text: str, position: int) -> int:
    end = text.find("\n", position)
    return len(text) if end < 0 else end


def _skip_trivia(text: str, position: int) -> int:
    while position < len(text):
        if text[position].isspace():
            position += 1
        elif text[position] == "%":
            position = _comment_end(text, position)
        else:
            break
    return position


def _group_end(
    text: str,
    opening_index: int,
    opening: str = "{",
    closing: str = "}",
    *,
    comments: bool = True,
    literals: bool = True,
) -> int:
    if opening_index >= len(text) or opening_index < 0 or text[opening_index] != opening:
        raise _error(text, max(0, opening_index), f"expected {opening!r}")
    depth = 1
    brace_depth = 0
    cursor = opening_index + 1
    while cursor < len(text):
        character = text[cursor]
        if comments and character == "%":
            cursor = _comment_end(text, cursor)
            continue
        if character == "\\":
            name, end = _command(text, cursor)
            cursor = _inline_literal_end(text, end, name) if literals and name in _INLINE_LITERALS else end
            continue
        # Optional arguments may contain groups whose square brackets are data.
        if opening == "[" and character == "{":
            brace_depth += 1
        elif opening == "[" and character == "}" and brace_depth:
            brace_depth -= 1
        elif not brace_depth and character == opening:
            depth += 1
        elif not brace_depth and character == closing:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    raise _error(text, opening_index, f"unclosed {opening!r}")


def find_group_end(text: str, opening_index: int) -> int:
    """Return the closing brace, treating quotes as ordinary TeX characters.

    Escaped braces, comments, and supported inline verbatim commands cannot
    change group depth. The returned index always refers to the original text.
    """
    return _group_end(text, opening_index)


def _required_group(text: str, position: int) -> tuple[str, int]:
    start = _skip_trivia(text, position)
    end = find_group_end(text, start)
    # TeX discards a comment's line ending as well as its text. Keep exact
    # offsets separately so multiline declarations still have literal names.
    value = text[start + 1:end]
    pieces: list[str] = []
    cursor = 0
    while cursor < len(value):
        if value[cursor] == "%":
            cursor = min(len(value), _comment_end(value, cursor) + 1)
        elif value[cursor] == "\\":
            _, command_end = _command(value, cursor)
            pieces.append(value[cursor:command_end])
            cursor = command_end
        else:
            pieces.append(value[cursor])
            cursor += 1
    return "".join(pieces), end + 1


def _optional_arguments_end(text: str, position: int) -> int:
    cursor = _skip_trivia(text, position)
    while cursor < len(text) and text[cursor] == "[":
        cursor = _skip_trivia(text, _group_end(text, cursor, "[", "]") + 1)
    return cursor


def _inline_literal_end(text: str, position: int, name: str) -> int:
    cursor = position
    if cursor < len(text) and text[cursor] == "*":
        cursor += 1
    if name in {"lstinline", "mintinline"}:
        cursor = _optional_arguments_end(text, cursor)
    if name == "mintinline":
        _, cursor = _required_group(text, cursor)  # Explicit language argument.
        cursor = _skip_trivia(text, cursor)
    if cursor >= len(text) or text[cursor] in "\r\n":
        raise _error(text, position, f"missing delimiter for \\{name}")
    if name in {"lstinline", "mintinline"} and text[cursor] == "{":
        return _group_end(text, cursor, comments=False, literals=False) + 1
    delimiter = text[cursor]
    end = text.find(delimiter, cursor + 1)
    newline = text.find("\n", cursor + 1)
    if end < 0 or (newline >= 0 and newline < end):
        raise _error(text, cursor, f"unclosed \\{name} literal")
    return end + 1


def _definition_end(text: str, position: int, name: str) -> int:
    cursor = _skip_trivia(text, position)
    if cursor < len(text) and text[cursor] == "*":
        cursor = _skip_trivia(text, cursor + 1)
    if name in _PRIMITIVE_DEFINITIONS:
        if cursor >= len(text) or text[cursor] != "\\":
            raise _error(text, cursor, f"expected macro name after \\{name}")
        _, cursor = _command(text, cursor)
        while cursor < len(text) and text[cursor] != "{":
            if text[cursor] == "%":
                cursor = _comment_end(text, cursor)
            elif text[cursor] == "\\":
                _, cursor = _command(text, cursor)
            else:
                cursor += 1
        return find_group_end(text, cursor) + 1
    if name in _COMMAND_DEFINITIONS | _DOCUMENT_COMMAND_DEFINITIONS:
        if cursor < len(text) and text[cursor] == "\\":
            _, cursor = _command(text, cursor)
        else:
            _, cursor = _required_group(text, cursor)
    else:
        _, cursor = _required_group(text, cursor)  # Environment/operator name.
    if name in _DOCUMENT_COMMAND_DEFINITIONS | _DOCUMENT_ENVIRONMENT_DEFINITIONS:
        _, cursor = _required_group(text, cursor)  # xparse argument specification.
    else:
        cursor = _optional_arguments_end(text, cursor)
    _, cursor = _required_group(text, cursor)
    if name in _ENVIRONMENT_DEFINITIONS | _DOCUMENT_ENVIRONMENT_DEFINITIONS:
        _, cursor = _required_group(text, cursor)
    return cursor


def _newtheorem(text: str, position: int) -> tuple[str, str, int]:
    cursor = _skip_trivia(text, position)
    if cursor < len(text) and text[cursor] == "*":
        cursor += 1
    environment, cursor = _required_group(text, cursor)
    cursor = _optional_arguments_end(text, cursor)
    title, cursor = _required_group(text, cursor)
    cursor = _optional_arguments_end(text, cursor)
    normalized_title = " ".join(title.split()).casefold()
    environment = environment.strip()
    kind = normalized_title if normalized_title in STATEMENT_KINDS else environment.rstrip("*")
    if kind not in STATEMENT_KINDS:
        kind = "concept"
    return environment, kind, cursor


def _false_branch_end(text: str, position: int, conditionals: set[str]) -> int:
    depth = 1
    cursor = position
    while cursor < len(text):
        if text[cursor] == "%":
            cursor = _comment_end(text, cursor)
        elif text[cursor] == "\\":
            name, end = _command(text, cursor)
            if name in conditionals:
                depth += 1
            elif name == "fi":
                depth -= 1
                if depth == 0:
                    return end
            elif name == "else" and depth == 1:
                return end
            cursor = end
        else:
            cursor += 1
    raise _error(text, position, "unclosed \\iffalse")


def _mask_latex(text: str, *, preserve_theorem_declarations: bool) -> str:
    result = list(text)
    conditionals = set(_CONDITIONALS)

    def hide(start: int, end: int) -> None:
        for index in range(start, end):
            if text[index] not in "\r\n":
                result[index] = " "

    cursor = 0
    while cursor < len(text):
        if text[cursor] == "%":
            end = _comment_end(text, cursor)
            hide(cursor, end)
            cursor = end
            continue
        if text[cursor] != "\\":
            cursor += 1
            continue
        name, end = _command(text, cursor)
        if name == "newif":
            conditional_start = _skip_trivia(text, end)
            if conditional_start < len(text) and text[conditional_start] == "\\":
                conditional_name, _ = _command(text, conditional_start)
                if conditional_name.startswith("if"):
                    conditionals.add(conditional_name)
        if name == "begin":
            environment, argument_end = _required_group(text, end)
            environment = environment.strip()
            if environment in LITERAL_ENVIRONMENTS:
                closing = re.compile(r"\\end\s*\{\s*" + re.escape(environment) + r"\s*\}")
                match = closing.search(text, argument_end)
                if match is None:
                    raise _error(text, cursor, f"unclosed literal environment {environment!r}")
                end = match.end()
                hide(cursor, end)
        elif name in _INLINE_LITERALS:
            end = _inline_literal_end(text, end, name)
            hide(cursor, end)
        elif name == "iffalse":
            end = _false_branch_end(text, end, conditionals)
            hide(cursor, end)
        elif name in (
            _COMMAND_DEFINITIONS | _ENVIRONMENT_DEFINITIONS | _DOCUMENT_COMMAND_DEFINITIONS
            | _DOCUMENT_ENVIRONMENT_DEFINITIONS | _PRIMITIVE_DEFINITIONS | {"DeclareMathOperator"}
        ):
            end = _definition_end(text, end, name)
            hide(cursor, end)
        elif name == "newtheorem":
            _, _, end = _newtheorem(text, end)
            if not preserve_theorem_declarations:
                hide(cursor, end)
        cursor = end
    return "".join(result)


def mask_latex(text: str) -> str:
    """Hide comments, literal/inactive text, and unexpanded macro definitions.

    The result has identical length and newline positions. This protects
    explicit-marker scanning while keeping source offsets and hashes native.
    """
    return _mask_latex(text, preserve_theorem_declarations=False)


def statement_ranges(text: str) -> list[StatementRange]:
    """Find properly nested statement environments in the original source.

    Standard kinds and their starred variants are recognized. Local
    ``\\newtheorem`` declarations provide explicit custom environment bounds;
    only exact standard titles/names provide a kind, otherwise it is concept.
    Other environments are checked when their nesting affects a statement.
    """
    visible = _mask_latex(text, preserve_theorem_declarations=True)
    kinds = {kind: kind for kind in STATEMENT_KINDS}
    kinds.update({kind + "*": kind for kind in STATEMENT_KINDS})
    stack: list[tuple[str, int, str | None]] = []
    result: list[StatementRange] = []
    cursor = 0
    while cursor < len(visible):
        if visible[cursor] != "\\":
            cursor += 1
            continue
        name, end = _command(visible, cursor)
        if name == "newtheorem":
            environment, kind, end = _newtheorem(visible, end)
            kinds[environment] = kind
        elif name in {"begin", "end"}:
            environment, end = _required_group(visible, end)
            environment = environment.strip()
            if name == "begin":
                stack.append((environment, cursor, kinds.get(environment)))
            elif stack and stack[-1][0] == environment:
                _, start, kind = stack.pop()
                if kind is not None:
                    result.append(StatementRange(start, end, kind))
            elif environment in kinds or any(item[2] is not None for item in stack):
                expected = stack[-1][0] if stack else None
                message = f"mismatched \\end{{{environment}}}; expected \\end{{{expected}}}" if expected else f"unmatched \\end{{{environment}}}"
                raise _error(text, cursor, message)
            else:
                # Unrelated fragment/package environments are not our contract.
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index][0] == environment:
                        del stack[index:]
                        break
        cursor = end
    for environment, start, kind in reversed(stack):
        if kind is not None:
            raise _error(text, start, f"unclosed \\begin{{{environment}}}")
    return sorted(result, key=lambda item: item.start)
