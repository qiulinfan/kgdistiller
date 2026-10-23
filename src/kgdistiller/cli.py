"""kgdistiller: source-backed research knowledge records and role-bound relations in Obsidian vaults.

Bases are registered in $KGDISTILLER_HOME (default ~/.knowledge). Records live
in <root>/.knowledge/entries/, proposed records in drafts/, and the retrieval
commands (search, resolve, get, neighbors, browse, pack, mcp) read the derived
index $KGDISTILLER_HOME/index.sqlite. Output is JSON. Exit codes: 0 ok, 1
findings or refusal, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from kgdistiller.home import KnowledgeError, resolve_base


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def configure_console_streams() -> None:
    """Read and write UTF-8 whatever the locale or Windows ANSI code page.

    JSON between programs and MCP stdio are UTF-8. A lone surrogate on output
    becomes a valid JSON ``\\udcxx`` escape; invalid input bytes become lone
    surrogates, which the MCP server answers with a parse error.
    """
    for stream, errors in ((sys.stdin, "surrogateescape"), (sys.stdout, "backslashreplace"),
                           (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)


def _cwd_path(value: Path) -> Path:
    """Resolve a command-line path against the working directory; keep absolute paths."""
    return Path(os.path.abspath(value))


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _bounded(value: str, low: int) -> int:
    from .retrieve import MAX_DEPTH

    number = int(value)
    if not low <= number <= MAX_DEPTH:
        raise argparse.ArgumentTypeError(f"must be between {low} and {MAX_DEPTH}")
    return number


def _depth(value: str) -> int:
    return _bounded(value, 0)


def _hops(value: str) -> int:
    return _bounded(value, 1)


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return number


def _filter_parser() -> argparse.ArgumentParser:
    from .records import UNDERSTANDING
    from .retrieve import CLASSES

    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group("filters (repeatable; OR within a filter, AND across filters)")
    group.add_argument("--base", action="append", default=[], metavar="B", help="registered base name")
    group.add_argument("--kind", action="append", default=[], metavar="K", help="record kind")
    group.add_argument("--class", action="append", default=[], dest="class_", choices=CLASSES)
    group.add_argument("--source", action="append", default=[], metavar="PREFIX", help="base-relative source path prefix")
    group.add_argument("--understanding", action="append", default=[], choices=UNDERSTANDING)
    return parser


def parse_args() -> argparse.Namespace:
    from .retrieve import DIRECTIONS, PACK_BUDGET

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    filters = _filter_parser()
    commands = parser.add_subparsers(dest="command", required=True)

    base_command = commands.add_parser(
        "base",
        help="register knowledge bases in the kgdistiller home",
        description="Add, remove or list the bases registered in $KGDISTILLER_HOME/config.json.",
    )
    base_commands = base_command.add_subparsers(dest="base_command", required=True)
    base_add = base_commands.add_parser("add", help="register a directory as a base, creating the home on first use")
    base_add.add_argument("path", type=Path)
    base_add.add_argument("--name", help="base name; defaults to the directory's basename")
    base_add.add_argument("--type", action="append", default=[], dest="types", metavar="TYPE",
                          help="a document type of types/ the base's records may use (repeatable)")
    base_rm = base_commands.add_parser(
        "rm", help="remove a base from the registry; files stay, and links into it are listed as dangling"
    )
    base_rm.add_argument("name")
    base_commands.add_parser("list", help="list the registered bases with record, draft and index counts")

    embedding = commands.add_parser(
        "embedding",
        help="show or change the home's embedding model",
        description="Show the embedding model in $KGDISTILLER_HOME/config.json and the stored vectors, or change it. "
        "The model runs on this computer; `set` downloads it into the Hugging Face cache and checks that kgdistiller "
        "can run it. A change re-embeds nothing; the next `kgd index` run of any kind drops the old vectors, and a "
        "plain `kgd index` embeds every record with the new model.",
    )
    embedding_commands = embedding.add_subparsers(dest="embedding_command")
    embedding_set = embedding_commands.add_parser(
        "set", help="download and check a model, then make it the home's embedding model"
    )
    embedding_set.add_argument("model", help="a Hugging Face model id, such as BAAI/bge-m3")
    embedding_commands.add_parser("off", help="turn the dense lane off (embedding null)")

    check = commands.add_parser(
        "check", help="validate the home, records and drafts and report moved or stale evidence"
    )
    check.add_argument("--base", action="append", default=[], metavar="B", help="check only this base (repeatable)")
    check.add_argument("--fix-lines", action="store_true", help=(
        "rewrite the lines: of every record whose evidence moved, and the source: and lines: of every record "
        "its markers followed to another file of its base"))

    source = commands.add_parser(
        "source", help="print a source's extraction profile and the records and drafts citing it; writes nothing"
    )
    source.add_argument("source", type=Path, help="a source file: md, tex or txt inside a base")

    markers = commands.add_parser(
        "markers",
        help="list the knowledge markers of sources, records or bases with the record each names; reads files only",
        description="Markers name a record where its source states it: --[[NAME]]-- or --[[NAME|TEXT]]-- in md and txt, "
        "\\kn{NAME} or \\kn[NAME]{TEXT} in tex. Without a source or uid, every source of the bases is read.",
    )
    markers.add_argument("sources", nargs="*", type=Path, metavar="SOURCE", help="a source file")
    markers.add_argument("--uid", action="append", default=[], metavar="BASE:ID",
                         help="the markers of this record, found round by round (repeatable)")
    markers.add_argument("--base", action="append", default=[], metavar="B", help="read only this base (repeatable)")
    markers.add_argument("--orphans", action="store_true", help="only markers that name no record or several")

    mark = commands.add_parser(
        "mark", help="write a marker naming a draft into its source, before its first quote; the line count stays"
    )
    mark.add_argument("draft", type=Path, help="a draft file")
    mark.add_argument("--name", help="the NAME to write; defaults to the label, or the id when the label cannot be one")
    mark.add_argument("--dry-run", action="store_true")

    find = commands.add_parser(
        "find", help="find a passage or a name in the registered sources, tolerant of line breaks and hidden markup"
    )
    find.add_argument("text", help="the text to find; - reads it from standard input")
    find.add_argument("--base", action="append", default=[], metavar="B", help="search only this base (repeatable)")
    find.add_argument(
        "--source", action="append", default=[], metavar="PREFIX", help="base-relative source path prefix (repeatable)"
    )
    find.add_argument("--limit", type=_positive, default=20)

    relocate = commands.add_parser(
        "relocate",
        help="move a record's source, lines and evidence to the only passage of the registered sources that holds a text, "
        "into that passage's base when it lies in another",
    )
    relocate.add_argument("target", metavar="TARGET", help="base:id of an accepted record, or a record or draft file")
    relocate.add_argument("text", nargs="?", help=(
        "the passage's text; - reads it from standard input; without it the record's markers are followed"))
    relocate.add_argument("--url", help="make the record foreign, citing this web page; the text is the page's own")
    relocate.add_argument(
        "--source", action="append", default=[], metavar="PREFIX", help="look only in sources with this prefix"
    )
    relocate.add_argument("--base", action="append", default=[], metavar="NAME", help="look only in this base (repeatable)")
    relocate.add_argument("--dry-run", action="store_true")

    drafts = commands.add_parser(
        "drafts", help="list the drafts with their links and what accept would refuse; reads files, writes nothing"
    )
    drafts.add_argument("--base", action="append", default=[], metavar="B", help="list only this base (repeatable)")
    drafts.add_argument(
        "--source", action="append", default=[], metavar="PREFIX", help="base-relative source path prefix (repeatable)"
    )
    drafts.add_argument(
        "--name", action="append", default=[], metavar="NAME",
        help="list only drafts whose label or an alias has this name (repeatable)",
    )

    edit = commands.add_parser(
        "edit", help="set one field of a draft: label, kind, epistemic, understanding, aliases, requires, a role, or body"
    )
    edit.add_argument("draft", type=Path, metavar="DRAFT")
    edit.add_argument("key", metavar="FIELD")
    edit.add_argument(
        "values", nargs="*", metavar="VALUE",
        help="the field's value or values; none removes the field; - as the only value of body reads standard input",
    )

    accept = commands.add_parser(
        "accept",
        help="admit drafts as new or replacing records, all or nothing, then index and embed the admitted records",
    )
    accept.add_argument("drafts", nargs="+", type=Path, metavar="DRAFT")
    accept.add_argument("--dry-run", action="store_true")

    index = commands.add_parser("index", help="bring the derived database up to date with the record files")
    index.add_argument("--rebuild", action="store_true", help="re-derive every row in place")
    index.add_argument("--no-embed", action="store_true", help="skip the embedding phase")

    search = commands.add_parser("search", parents=[filters], help="rank records by the lexical, dense and name lanes")
    search.add_argument("query")
    search.add_argument("--limit", type=_positive, default=40)
    search.add_argument("--no-dense", action="store_true", help="skip the dense lane (no model load)")

    resolve = commands.add_parser("resolve", parents=[filters], help="list the senses and mentions of terms")
    resolve.add_argument("terms", nargs="+", metavar="TERM")

    get = commands.add_parser("get", help="read complete records with their links")
    get.add_argument("uids", nargs="+", metavar="UID", help="base:id, or a bare id held by exactly one base")
    get.add_argument("--source-lines", type=_non_negative, metavar="N", help="add the live cited source range widened by N lines")

    neighbors = commands.add_parser(
        "neighbors", parents=[filters], help="follow links from records: requires chains and the statements and relations linking them"
    )
    neighbors.add_argument("uids", nargs="+", metavar="UID", help="base:id, or a bare id held by exactly one base")
    neighbors.add_argument(
        "--role", action="append", default=[], metavar="R", help="follow only this role (repeatable), at every hop"
    )
    neighbors.add_argument(
        "--dir", dest="direction", choices=DIRECTIONS, default="out",
        help="out follows a record's own links, in the links citing it, both either (default: out)",
    )
    neighbors.add_argument("--depth", type=_hops, default=1, metavar="N", help="hops to follow; cuts cycles (default: 1)")

    browse = commands.add_parser(
        "browse", parents=[filters],
        help="list bases, source directories, a source's records by kind, or every record of a --kind",
    )
    browse.add_argument("handle", nargs="?", metavar="HANDLE", help="base, base:dir/ or base:path/file")

    pack = commands.add_parser(
        "pack", parents=[filters], help="pack whole records within a byte budget, with the records linking several of them and typed gaps"
    )
    pack.add_argument("uids", nargs="+", metavar="UID", help="base:id, or a bare id held by exactly one base")
    pack.add_argument(
        "--budget", type=_positive, default=PACK_BUDGET, metavar="BYTES",
        help=f"UTF-8 bytes of the compact JSON of the packed records (default: {PACK_BUDGET})",
    )
    pack.add_argument(
        "--requires-depth", type=_depth, default=1, metavar="N",
        help="layers of the requires closure to add (default: 1)",
    )

    obsidian_command = commands.add_parser("obsidian", help="manage kgdistiller's integration with an Obsidian vault")
    obsidian_commands = obsidian_command.add_subparsers(dest="obsidian_command", required=True)
    obsidian_install = obsidian_commands.add_parser(
        "install",
        help=(
            "install or update the bundled kgdistiller plugin in a registered base's vault, enable it "
            "and its hidden-folder indexing, and warn about relative link formats"
        ),
    )
    obsidian_install.add_argument(
        "--base", metavar="NAME",
        help="registered base to use; defaults to the base whose root contains the working directory",
    )

    claude_command = commands.add_parser("claude", help="link or verify the Claude Code integration")
    claude_commands = claude_command.add_subparsers(dest="claude_command", required=True)
    claude_link = claude_commands.add_parser("link")
    claude_link.add_argument("--claude-home", type=Path)
    claude_link.add_argument(
        "--mode", choices=("auto", "symlink", "copy"), default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    claude_doctor = claude_commands.add_parser("doctor")
    claude_doctor.add_argument("--claude-home", type=Path)
    claude_doctor.add_argument("--source-only", action="store_true")

    codex_command = commands.add_parser("codex", help="link or verify the Codex integration")
    codex_commands = codex_command.add_subparsers(dest="codex_command", required=True)
    codex_link = codex_commands.add_parser("link")
    codex_link.add_argument("--codex-home", type=Path)
    codex_link.add_argument(
        "--mode", choices=("auto", "symlink", "copy"), default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    codex_doctor = codex_commands.add_parser("doctor")
    codex_doctor.add_argument("--codex-home", type=Path)
    codex_doctor.add_argument("--source-only", action="store_true")

    commands.add_parser("mcp", help="serve read-only MCP tools over stdio for the whole home")
    return parser.parse_args()


def _error(kind: str, code: str, message: str) -> None:
    print(pretty_json({"kind": kind, "code": code, "message": message}), end="", file=sys.stderr)


def _print(value: Any, *, failed: bool = False) -> int:
    print(pretty_json(value), end="")
    return 1 if failed else 0


def _text(value: str) -> str:
    """A text argument, read from standard input when it is -."""
    return sys.stdin.read() if value == "-" else value


def _filters(args: argparse.Namespace):
    from .retrieve import Filters

    return Filters(
        base=tuple(args.base), kind=tuple(args.kind), class_=tuple(args.class_),
        source=tuple(args.source), understanding=tuple(args.understanding),
    )


def _base(args: argparse.Namespace) -> int:
    from .home import add_base, home_directory, list_bases, load_home, remove_base

    if args.base_command == "add":
        return _print(add_base(args.path, name=args.name, types=args.types))
    if args.base_command == "rm":
        from .records import links_into

        result = remove_base(args.name)
        result["dangling"] = links_into(load_home(home_directory(), types=False), args.name)
        return _print(result)
    from .index import base_status

    result = list_bases()
    status = base_status(load_home(home_directory(), types=False))
    for item in result["bases"]:
        item.update(status[item["name"]])
    return _print(result)


def _embedding(args: argparse.Namespace) -> int:
    from .home import home_directory, load_home, set_embedding
    from .index import embedding_status

    if args.embedding_command == "set":
        from .adapters.embedding import describe

        model = args.model.strip()
        if not model:
            raise KnowledgeError("the model id must not be empty")
        checked = describe(model).report()
        set_embedding(model)
        return _print({**embedding_status(load_home(home_directory(), types=False)), "checked": checked})
    if args.embedding_command == "off":
        set_embedding(None)
    return _print(embedding_status(load_home(home_directory(), types=False)))


def _runtime_product(args: argparse.Namespace) -> int:
    from .codex_product import ProductError

    if args.command == "codex":
        from .codex_product import doctor_product, link_product

        try:
            if args.codex_command == "link":
                result = link_product(codex_home=args.codex_home, mode=args.mode)
            else:
                result = doctor_product(codex_home=args.codex_home, source_only=args.source_only)
        except (ProductError, OSError) as error:
            _error("kgdistiller-codex-product-error", "codex-product-failed", str(error))
            return 1
    else:
        from .claude_product import doctor_claude_product, link_claude_product

        try:
            if args.claude_command == "link":
                result = link_claude_product(claude_home=args.claude_home, mode=args.mode)
            else:
                result = doctor_claude_product(claude_home=args.claude_home, source_only=args.source_only)
        except (ProductError, OSError) as error:
            _error("kgdistiller-claude-product-error", "claude-product-failed", str(error))
            return 1
    print(pretty_json(result), end="")
    return 0


def _obsidian(args: argparse.Namespace) -> int:
    from .obsidian_plugin import ObsidianPluginError, install_obsidian_plugin

    base = resolve_base(args.base, Path.cwd())
    try:
        result = install_obsidian_plugin(base)
    except ObsidianPluginError as error:
        _error("kgdistiller-obsidian-plugin-error", "obsidian-plugin-install-failed", str(error))
        return 1
    return _print(result)


def _command(args: argparse.Namespace) -> int:
    if args.command == "base":
        return _base(args)
    if args.command in {"codex", "claude"}:
        return _runtime_product(args)
    if args.command == "obsidian":
        return _obsidian(args)
    if args.command == "embedding":
        return _embedding(args)
    if args.command == "check":
        from .records import check, fix_lines

        bases = args.base or None
        report = fix_lines(bases=bases) if args.fix_lines else check(bases=bases)
        return _print(report, failed=bool(report["errors"] or report["stale"] or report["moved"] or report["follow"]))
    if args.command == "source":
        from .write import source

        return _print(source(_cwd_path(args.source)))
    if args.command == "markers":
        from .markers import markers

        return _print(markers([_cwd_path(path) for path in args.sources], args.uid, args.base, args.orphans))
    if args.command == "mark":
        from .markers import mark

        report = mark(_cwd_path(args.draft), args.name, args.dry_run)
        return _print(report)
    if args.command == "find":
        from .locate import find

        return _print(find(_text(args.text), args.base, args.source, args.limit))
    if args.command == "relocate":
        from .locate import relocate

        target = args.target if ":" in args.target and "/" not in args.target else str(_cwd_path(Path(args.target)))
        text = None if args.text is None else _text(args.text)
        report = relocate(target, text, args.source, args.base, args.dry_run, args.url)
        return _print(report, failed="refused" in report)
    if args.command == "drafts":
        from .write import drafts

        return _print(drafts(args.base, args.source, args.name))
    if args.command == "edit":
        from .write import edit

        values = [sys.stdin.read()] if args.key == "body" and args.values == ["-"] else args.values
        return _print(edit(_cwd_path(args.draft), args.key, values))
    if args.command == "accept":
        from .write import accept

        receipt = accept([_cwd_path(path) for path in args.drafts], args.dry_run)
        return _print(receipt, failed="refused" in receipt or "aborted" in receipt)
    if args.command == "index":
        from .index import index, index_clean

        report = index(rebuild=args.rebuild, embed=not args.no_embed)
        return _print(report, failed=not index_clean(report))
    if args.command == "search":
        from .retrieve import search

        return _print(search(args.query, limit=args.limit, filters=_filters(args), dense=not args.no_dense))
    if args.command == "resolve":
        from .retrieve import resolve

        return _print(resolve(args.terms, filters=_filters(args)))
    if args.command == "get":
        from .retrieve import get

        return _print(get(args.uids, source_lines=args.source_lines))
    if args.command == "neighbors":
        from .retrieve import neighbors

        return _print(neighbors(
            args.uids, roles=tuple(args.role), direction=args.direction, depth=args.depth, filters=_filters(args),
        ))
    if args.command == "browse":
        from .retrieve import browse

        return _print(browse(args.handle, filters=_filters(args)))
    if args.command == "pack":
        from .retrieve import pack

        return _print(pack(
            args.uids, budget=args.budget, requires_depth=args.requires_depth, filters=_filters(args),
        ))
    from .mcp import serve_stdio

    serve_stdio()
    return 0


def main() -> int:
    configure_console_streams()
    args = parse_args()
    try:
        return _command(args)
    except (KnowledgeError, OSError, UnicodeError, ValueError, sqlite3.Error) as error:
        _error("kgdistiller-error", "command-failed", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
