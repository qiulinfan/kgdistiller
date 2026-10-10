"""kgdistiller: source-backed research knowledge records and role-bound relations in Obsidian vaults.

Bases are registered in $KGDISTILLER_HOME (default ~/.knowledge). Records live
in <root>/.knowledge/entries/, proposed records in drafts/, and every read goes
through the derived index $KGDISTILLER_HOME/index.sqlite. Output is JSON. Exit
codes: 0 ok, 1 findings or refusal, 2 usage.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from kgdistiller.home import KnowledgeError, knowledge_root, resolve_base


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _json_backslash_replace(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeEncodeError):
        raise error
    escaped: list[str] = []
    for character in error.object[error.start : error.end]:
        codepoint = ord(character)
        if codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
            continue
        codepoint -= 0x10000
        escaped.append(
            f"\\u{0xD800 + (codepoint >> 10):04x}"
            f"\\u{0xDC00 + (codepoint & 0x3FF):04x}"
        )
    return "".join(escaped), error.end


def configure_console_streams() -> None:
    """Escape unencodable console text as valid JSON Unicode escapes."""
    error_handler = "kgdistiller_json_backslashreplace"
    codecs.register_error(error_handler, _json_backslash_replace)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors=error_handler)


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
    base_rm = base_commands.add_parser(
        "rm", help="remove a base from the registry; files stay, and links into it are listed as dangling"
    )
    base_rm.add_argument("name")
    base_commands.add_parser("list", help="list the registered bases with record, draft and index counts")

    check = commands.add_parser(
        "check", help="validate the home, records and drafts and report moved or stale evidence"
    )
    check.add_argument("--base", action="append", default=[], metavar="B", help="check only this base (repeatable)")
    check.add_argument("--fix-lines", action="store_true", help="rewrite the lines: of every record whose evidence moved")

    sheet = commands.add_parser("sheet", help="write a source's sheet of records, pending terms and drafts")
    sheet.add_argument("source", type=Path, help="a registered source file")
    sheet.add_argument("--json", action="store_true", help="print the extraction profile and inventory; write nothing")

    accept = commands.add_parser("accept", help="promote drafts to accepted records, all or nothing")
    accept.add_argument("drafts", nargs="+", type=Path, metavar="DRAFT")
    accept.add_argument("--dry-run", action="store_true")

    harvest = commands.add_parser("harvest", help="accept the ticked drafts of a sheet and regenerate it")
    harvest.add_argument("sheet", type=Path)
    harvest.add_argument("--dry-run", action="store_true")

    index = commands.add_parser("index", help="bring the derived database up to date with the record files")
    index.add_argument("--rebuild", action="store_true", help="re-derive every row in place")
    index.add_argument("--no-embed", action="store_true", help="skip the embedding phase")

    search = commands.add_parser("search", parents=[filters], help="rank records by the lexical, dense and name lanes")
    search.add_argument("query")
    search.add_argument("--limit", type=_positive, default=40)
    search.add_argument("--no-dense", action="store_true", help="skip the dense lane (no model load)")

    resolve = commands.add_parser("resolve", parents=[filters], help="list the senses, mentions and pending uses of terms")
    resolve.add_argument("terms", nargs="+", metavar="TERM")

    get = commands.add_parser("get", help="read complete records with their links")
    get.add_argument("uids", nargs="+", metavar="UID", help="base:id, or a bare id held by exactly one base")
    get.add_argument("--source-lines", type=_non_negative, metavar="N", help="add the live cited source range widened by N lines")

    neighbors = commands.add_parser(
        "neighbors", parents=[filters], help="follow links from records: dependency and claim closures"
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
        "pack", parents=[filters], help="pack whole records within a byte budget, with shared relations and typed gaps"
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
    obsidian_install = obsidian_commands.add_parser("install", help="install the bundled kgdistiller plugin into the base root")
    obsidian_install.add_argument(
        "--base", metavar="NAME",
        help="registered base to use; defaults to the base whose root contains the working directory",
    )
    obsidian_install.add_argument("--replace", action="store_true", help="atomically update an existing kgdistiller plugin bundle")
    obsidian_install.add_argument(
        "--no-enable", action="store_false", dest="enable",
        help="install the plugin files without adding kgdistiller to community-plugins.json",
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


def _filters(args: argparse.Namespace):
    from .retrieve import Filters

    return Filters(
        base=tuple(args.base), kind=tuple(args.kind), class_=tuple(args.class_),
        source=tuple(args.source), understanding=tuple(args.understanding),
    )


def _base(args: argparse.Namespace) -> int:
    from .home import add_base, home_directory, list_bases, load_home, remove_base

    if args.base_command == "add":
        return _print(add_base(args.path, name=args.name))
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


def _runtime_product(args: argparse.Namespace) -> int:
    if args.command == "codex":
        from .codex_product import CodexProductError, doctor_product, link_product

        try:
            if args.codex_command == "link":
                result = link_product(codex_home=args.codex_home, mode=args.mode)
            else:
                result = doctor_product(codex_home=args.codex_home, source_only=args.source_only)
        except (CodexProductError, OSError) as error:
            _error("kgdistiller-codex-product-error", "codex-product-failed", str(error))
            return 1
    else:
        from .claude_product import (
            ClaudeProductError,
            doctor_claude_product,
            link_claude_product,
        )

        try:
            if args.claude_command == "link":
                result = link_claude_product(claude_home=args.claude_home, mode=args.mode)
            else:
                result = doctor_claude_product(claude_home=args.claude_home, source_only=args.source_only)
        except (ClaudeProductError, OSError) as error:
            _error("kgdistiller-claude-product-error", "claude-product-failed", str(error))
            return 1
    print(pretty_json(result), end="")
    return 0


def _obsidian(args: argparse.Namespace) -> int:
    from .obsidian_plugin import ObsidianPluginError, install_obsidian_plugin

    base = resolve_base(args.base, Path.cwd())
    knowledge_root(base.root)
    try:
        result = install_obsidian_plugin(base.root, replace=args.replace, enable=args.enable)
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
    if args.command == "check":
        from .records import check, fix_lines

        bases = args.base or None
        report = fix_lines(bases=bases) if args.fix_lines else check(bases=bases)
        return _print(report, failed=bool(report["errors"] or report["stale"] or report["moved"]))
    if args.command == "sheet":
        from .write import sheet, sheet_json

        source = _cwd_path(args.source)
        return _print(sheet_json(source) if args.json else sheet(source))
    if args.command in {"accept", "harvest"}:
        from .write import accept, harvest

        if args.command == "accept":
            receipt = accept([_cwd_path(path) for path in args.drafts], args.dry_run)
        else:
            receipt = harvest(_cwd_path(args.sheet), args.dry_run)
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
