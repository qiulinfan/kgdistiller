"""Distill registered text documents into a source-backed knowledge graph of reviewed entries."""

from __future__ import annotations

import argparse
import codecs
import json
import os
import sys
from pathlib import Path
from typing import Any

from kgdistiller.knowledge_paths import KNOWLEDGE_DIRECTORY, knowledge_root
from kgdistiller.knowledge_store import atomic_write_text
from kgdistiller.sources import KnowledgeError, read_json


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


def defaults(repo_root: Path, value: str | Path) -> Path:
    return (repo_root / value).resolve()


def check_command(repo_root: Path, registry: Path, *, fix: bool) -> int:
    """Validate the entry store; with ``fix`` rewrite the line ranges of moved entries."""
    from .ingest import IngestPaths, journal_path, writer_lock
    from .knowledge_store import fix_lines, load_state, validate

    paths = IngestPaths(repo_root=repo_root, registry=registry)
    if journal_path(paths).exists():
        raise KnowledgeError(
            "an ingest install is in progress or was interrupted; "
            "rerun or recover it with kgdistiller ingest apply before checking"
        )
    load_errors: list[dict[str, Any]] = []
    if fix:
        with writer_lock(paths):
            state = load_state(repo_root, load_errors)
            for item in fix_lines(state, repo_root, registry):
                print(
                    f"fixed {item['entry']}: {item['source']}:{item['line_start']}-{item['line_end']}"
                    f" -> {item['new_line_start']}-{item['new_line_end']}"
                )
            report = validate(state, repo_root, registry)
    else:
        state = load_state(repo_root, load_errors)
        report = validate(state, repo_root, registry)
    report["errors"][:0] = load_errors
    for error in report["errors"]:
        print(f"error {error['code']}: {error['message']}")
    for item in report["stale"]:
        cited = f"{item['source']}:{item['line_start']}-{item['line_end']}"
        if item["status"] == "moved":
            print(
                f"moved {item['entry']}: {cited} -> {item['new_line_start']}-{item['new_line_end']}"
                " (run kgdistiller check --fix-lines)"
            )
        elif item["status"] == "ambiguous":
            ranges = ", ".join(
                f"{candidate['line_start']}-{candidate['line_end']}" for candidate in item["candidates"]
            )
            print(f"ambiguous {item['entry']}: {cited}; Evidence occurs at {ranges}")
        else:
            print(f"stale {item['entry']}: {cited}; Evidence no longer occurs in the source")
    if report["errors"] or report["stale"]:
        print(f"FAILED: {len(report['errors'])} errors, {len(report['stale'])} stale entries")
        return 1
    print(f"OK: {len(state.entries)} entries, {len(state.edges)} edges")
    return 0


def scan_files(repo_root: Path, registry: Path, files: list[Path]) -> dict[str, Any]:
    """Return each file's registered source profile and its text with 1-based line numbers."""
    from .document_types import load_document_types
    from .entries import split_lines
    from .sources import load_sources, source_for_path

    specs = load_sources(repo_root, registry)
    profiles = load_document_types(registry)
    result = []
    for value in files:
        path = value if value.is_absolute() else repo_root / value
        if not path.is_file():
            raise KnowledgeError(f"scan file does not exist: {value}")
        spec = source_for_path(specs, path)
        try:
            relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError as error:
            raise KnowledgeError(f"scan file lies outside the project: {value}") from error
        with path.open("r", encoding="utf-8", newline=None) as handle:
            text = handle.read()
        result.append({
            "path": relative,
            "source_id": spec.id,
            "document_type": spec.document_type or None,
            "profile": profiles[spec.document_type] if spec.document_type else None,
            "lines": [{"line": number, "text": line} for number, line in enumerate(split_lines(text), 1)],
        })
    return {"files": result}


def add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embedding", action="store_true",
        help="explicitly enable local embedding candidates (requires the retrieval extra)",
    )
    parser.add_argument("--model-device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--model-batch-size", type=int, default=4)
    parser.add_argument("--model-max-length", type=int, default=8192)
    parser.add_argument("--embedding-model", help="embedding model; defaults to the adapter's pinned BAAI/bge-m3")
    parser.add_argument("--embedding-revision", help="immutable revision; defaults to the adapter's pinned revision")
    parser.add_argument("--rerank", action="store_true", help="rerank embedding and lexical candidates with the pinned local cross-encoder; requires --embedding")
    parser.add_argument("--rerank-candidates", type=int, default=50, help="maximum candidates sent to the reranker (1 to 500)")
    parser.add_argument("--reranker-model", help="reranker model; defaults to the adapter's pinned BAAI/bge-reranker-v2-m3")
    parser.add_argument("--reranker-revision", help="immutable reranker revision; defaults to the adapter's pinned revision")
    parser.add_argument("--model-cache-dir", type=Path, help=f"derived vector cache; defaults to {KNOWLEDGE_DIRECTORY}/build/retrieval")
    parser.add_argument("--models-offline", action="store_true", help="load only already downloaded local model files")


def add_graph_retrieval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--graph-retrieval", action="store_true",
        help="explicitly explore source-backed graph edges from ranked candidate roots",
    )
    parser.add_argument("--graph-seed-candidates", type=int, default=5, help="maximum ranked candidate roots (1 to 32)")
    parser.add_argument("--graph-edge-policy", choices=("high-confidence", "all"), default="high-confidence", help="edge gate; high-confidence keeps edges with declared high confidence and nonempty evidence, all keeps every accepted edge")


def make_graph_retrieval_policy(args: argparse.Namespace):
    """Keep graph exploration opt-in independently of optional model inference."""
    if not args.graph_retrieval:
        return None
    from .graph_retrieval import GraphRetrievalPolicy
    from .retrieval import RetrievalError

    try:
        return GraphRetrievalPolicy(candidate_limit=args.graph_seed_candidates, edge_policy=args.graph_edge_policy)
    except ValueError as error:
        raise RetrievalError("invalid-graph-settings", str(error)) from error


def make_ranking_service(args: argparse.Namespace, *, repo_root: Path):
    """Keep optional model dependencies out of every non-model operation."""
    if args.rerank and not args.embedding:
        from .retrieval import RetrievalError
        raise RetrievalError("invalid-model-settings", "--rerank requires --embedding")
    if not args.embedding:
        return None
    from .adapters.sentence_transformers import (
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_EMBEDDING_REVISION,
        SentenceTransformersAdapter,
    )
    from .retrieval import RetrievalError
    from .semantic_retrieval import SemanticRankingService, SemanticRetrievalError

    cache_dir = args.model_cache_dir or knowledge_root(repo_root) / "build" / "retrieval"
    if not cache_dir.is_absolute():
        cache_dir = repo_root / cache_dir
    try:
        adapter_options = {
            "model": args.embedding_model if args.embedding_model is not None else DEFAULT_EMBEDDING_MODEL,
            "revision": args.embedding_revision if args.embedding_revision is not None else DEFAULT_EMBEDDING_REVISION,
            "device": args.model_device,
            "batch_size": args.model_batch_size,
            "max_length": args.model_max_length,
            "local_files_only": args.models_offline,
        }
        service_options = {}
        if args.rerank:
            from .adapters.sentence_transformers import (
                DEFAULT_RERANKER_MODEL,
                DEFAULT_RERANKER_REVISION,
            )
            adapter_options.update({
                "reranker_model": args.reranker_model if args.reranker_model is not None else DEFAULT_RERANKER_MODEL,
                "reranker_revision": args.reranker_revision if args.reranker_revision is not None else DEFAULT_RERANKER_REVISION,
            })
            service_options = {"rerank": True, "candidate_limit": args.rerank_candidates}
        adapter = SentenceTransformersAdapter(**adapter_options)
        return SemanticRankingService(adapter, cache_dir=cache_dir.resolve(), **service_options)
    except SemanticRetrievalError as error:
        raise RetrievalError(error.code, error.message) from error


def _resolve_cli_path(repo_root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (repo_root / value).resolve()


def _option_names(options: set[str]) -> set[str]:
    """Return the long options from ``options`` that appear on the command line."""
    supplied = set()
    for argument in sys.argv[1:]:
        if argument == "--":
            break
        if not argument.startswith("--"):
            continue
        name = argument.split("=", 1)[0]
        if name in options:
            supplied.add(name)
        else:
            # argparse accepts unambiguous option abbreviations too.
            matches = [option for option in options if option.startswith(name)]
            if len(matches) == 1:
                supplied.add(matches[0])
    return supplied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--repo-root",
        type=Path,
        help="use an explicit vault path without consulting the user registry",
    )
    target.add_argument(
        "--vault",
        help="select a registered vault by machine-local name or stable ID",
    )
    parser.add_argument(
        "--kgdistiller-home",
        type=Path,
        help="override the user-level registry directory (or use KGDISTILLER_HOME)",
    )
    parser.add_argument("--registry", default=f"{KNOWLEDGE_DIRECTORY}/sources.json")
    commands = parser.add_subparsers(dest="command", required=True)
    vault_command = commands.add_parser(
        "vault",
        help="manage machine-local vault registrations",
        description="Manage machine-local vault names, paths, and the default target.",
    )
    vault_commands = vault_command.add_subparsers(
        dest="vault_command", required=True
    )
    vault_register = vault_commands.add_parser(
        "register", help="register or relocate a vault"
    )
    vault_register.add_argument("path", type=Path)
    vault_register.add_argument("--name")
    vault_register.add_argument(
        "--replace",
        action="store_true",
        help="relocate an existing vault identity even when its old path still exists",
    )
    vault_commands.add_parser("list", help="list registered vaults")
    vault_show = vault_commands.add_parser("show", help="show one registered vault")
    vault_show.add_argument("selector")
    vault_default = vault_commands.add_parser(
        "default", help="set or clear the default vault"
    )
    vault_default.add_argument("selector", nargs="?")
    vault_default.add_argument(
        "--clear",
        action="store_true",
        help="clear the default vault",
    )
    vault_unregister = vault_commands.add_parser(
        "unregister", help="remove a machine-local registration"
    )
    vault_unregister.add_argument("selector")
    vault_doctor = vault_commands.add_parser(
        "doctor", help="validate registered paths and portable identities"
    )
    vault_doctor.add_argument("selector", nargs="?")
    init_command = commands.add_parser(
        "init", help="create the source registry, an empty entries directory and edges file"
    )
    init_command.add_argument("--source-root", type=Path, default=Path("notes"))
    init_command.add_argument(
        "--files",
        action="append",
        metavar="PATTERN",
        help="glob admitted by the source, relative to its root (repeatable; default **/*)",
    )
    init_command.add_argument("--force", action="store_true")
    scan_command = commands.add_parser(
        "scan", help="show registered files with their document-type profile and numbered lines"
    )
    scan_command.add_argument("--file", action="append", required=True, type=Path)
    check_command_parser = commands.add_parser(
        "check", help="validate entries and edges and report entries whose Evidence moved or vanished"
    )
    check_command_parser.add_argument(
        "--fix-lines",
        action="store_true",
        help="rewrite the line range of every entry whose Evidence moved",
    )
    obsidian_command = commands.add_parser(
        "obsidian",
        help="manage kgdistiller's integration with an Obsidian vault",
    )
    obsidian_commands = obsidian_command.add_subparsers(
        dest="obsidian_command", required=True
    )
    obsidian_install = obsidian_commands.add_parser(
        "install", help="install the bundled kgdistiller plugin into the selected vault"
    )
    obsidian_install.add_argument(
        "--replace",
        action="store_true",
        help="atomically update an existing kgdistiller plugin bundle",
    )
    obsidian_install.add_argument(
        "--no-enable",
        action="store_false",
        dest="enable",
        help="install the plugin files without adding kgdistiller to community-plugins.json",
    )
    agent_command = commands.add_parser("agent", help="read-only queries for agents")
    agent_commands = agent_command.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("status")
    compiled_command = agent_commands.add_parser(
        "compiled", help="search, navigate and read an explicitly supplied compiled knowledge library"
    )
    compiled_command.add_argument("--library", type=Path, required=True)
    compiled_operations = compiled_command.add_subparsers(dest="compiled_operation", required=True)
    compiled_search = compiled_operations.add_parser("search")
    compiled_search.add_argument("query")
    compiled_search.add_argument("--limit", type=int, default=40)
    compiled_browse = compiled_operations.add_parser("browse")
    compiled_browse.add_argument("reference", nargs="?")
    compiled_get = compiled_operations.add_parser("get")
    compiled_get.add_argument("reference", nargs="+")
    compiled_inventory = compiled_operations.add_parser("inventory", help="list all exact authored term declarations")
    compiled_inventory.add_argument("term")
    compiled_pack = compiled_operations.add_parser("pack")
    compiled_pack.add_argument("reference", nargs="+")
    compiled_pack.add_argument("--budget", type=int, default=24000, help="complete UTF-8 response byte budget")
    resolve_command = agent_commands.add_parser("resolve")
    resolve_command.add_argument("concept", nargs="+")
    agent_search_command = agent_commands.add_parser("search")
    agent_search_command.add_argument("query", nargs="?")
    agent_search_command.add_argument(
        "--plan",
        type=Path,
        help="execute a kgdistiller-retrieval-plan-v1 JSON file instead of a query",
    )
    agent_search_command.add_argument("--limit", type=int)
    agent_search_command.add_argument("--depth", type=int)
    agent_search_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
    add_model_arguments(agent_search_command)
    add_graph_retrieval_arguments(agent_search_command)
    get_command = agent_commands.add_parser("get")
    get_command.add_argument("id")
    expand_command = agent_commands.add_parser("expand")
    expand_command.add_argument("id", nargs="+")
    expand_command.add_argument(
        "--direction",
        choices=("incoming", "outgoing", "both"),
        default="both",
    )
    expand_command.add_argument("--relation", action="append", dest="edge_types")
    expand_command.add_argument("--depth", type=int, default=1)
    expand_command.add_argument("--limit", type=int, default=50)
    ppr_command = agent_commands.add_parser("ppr")
    ppr_command.add_argument("id", nargs="+")
    ppr_command.add_argument("--relation", action="append", dest="edge_types")
    ppr_command.add_argument(
        "--direction",
        choices=("incoming", "outgoing", "both"),
        default="outgoing",
    )
    ppr_command.add_argument("--limit", type=int, default=50)
    context_command = agent_commands.add_parser("context")
    context_command.add_argument("query", nargs="?")
    context_command.add_argument(
        "--plan",
        type=Path,
        help="execute a kgdistiller-retrieval-plan-v1 JSON file instead of a query",
    )
    context_command.add_argument("--budget", type=int, default=6000)
    context_command.add_argument("--limit", type=int)
    context_command.add_argument("--depth", type=int)
    context_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
    add_model_arguments(context_command)
    add_graph_retrieval_arguments(context_command)
    harvest_command = commands.add_parser(
        "harvest", help="prepare checkbox reviews of captured knowledge and ingest the checked items"
    )
    harvest_commands = harvest_command.add_subparsers(
        dest="harvest_command", required=True
    )
    harvest_prepare = harvest_commands.add_parser("prepare")
    harvest_prepare.add_argument("input", type=Path)
    harvest_prepare.add_argument("--sheet", type=Path, required=True)
    harvest_prepare.add_argument("--output", type=Path, required=True)
    harvest_apply = harvest_commands.add_parser("apply")
    harvest_apply.add_argument("sheet", type=Path)
    harvest_apply.add_argument("--output", type=Path, required=True)
    capture_command = commands.add_parser(
        "capture", help="turn one reviewed capture into ingest requests"
    )
    capture_commands = capture_command.add_subparsers(
        dest="capture_command", required=True
    )
    capture_prepare = capture_commands.add_parser("prepare")
    capture_prepare.add_argument("input", type=Path)
    capture_prepare.add_argument("--output", type=Path, required=True)
    ingest_command = commands.add_parser(
        "ingest", help="plan or commit a reviewed transactional update of entries and edges"
    )
    ingest_commands = ingest_command.add_subparsers(
        dest="ingest_command", required=True
    )
    ingest_plan = ingest_commands.add_parser("plan")
    ingest_plan.add_argument("request", type=Path)
    ingest_plan.add_argument("--output", type=Path)
    ingest_apply = ingest_commands.add_parser("apply")
    ingest_apply.add_argument("request", type=Path)
    ingest_apply.add_argument("--receipt", type=Path)
    export_command = commands.add_parser("export", help="write the Obsidian plugin's graph feed")
    export_commands = export_command.add_subparsers(
        dest="export_command", required=True
    )
    export_obsidian = export_commands.add_parser(
        "obsidian", help="write the Obsidian plugin's typed graph feed"
    )
    export_obsidian.add_argument(
        "--output",
        type=Path,
        default=Path(KNOWLEDGE_DIRECTORY, "build", "obsidian", "semantic-graph.json"),
    )
    codex_command = commands.add_parser("codex", help="link or verify the Codex integration")
    codex_commands = codex_command.add_subparsers(dest="codex_command", required=True)
    codex_link = codex_commands.add_parser("link")
    codex_link.add_argument("--codex-home", type=Path)
    codex_link.add_argument(
        "--mode",
        choices=("auto", "symlink", "copy"),
        default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    codex_doctor = codex_commands.add_parser("doctor")
    codex_doctor.add_argument("--codex-home", type=Path)
    codex_doctor.add_argument("--source-only", action="store_true")
    claude_command = commands.add_parser("claude", help="link or verify the Claude Code integration")
    claude_commands = claude_command.add_subparsers(
        dest="claude_command", required=True
    )
    claude_link = claude_commands.add_parser("link")
    claude_link.add_argument("--claude-home", type=Path)
    claude_link.add_argument(
        "--mode",
        choices=("auto", "symlink", "copy"),
        default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    claude_doctor = claude_commands.add_parser("doctor")
    claude_doctor.add_argument("--claude-home", type=Path)
    claude_doctor.add_argument("--source-only", action="store_true")
    mcp_command = commands.add_parser("mcp", help="serve read-only MCP tools over stdio")
    add_model_arguments(mcp_command)
    args = parser.parse_args()
    if hasattr(args, "graph_retrieval"):
        supplied_graph_options = _option_names({"--graph-seed-candidates", "--graph-edge-policy"})
        if supplied_graph_options and not args.graph_retrieval:
            parser.error("graph options require --graph-retrieval: " + ", ".join(sorted(supplied_graph_options)))
        if not 1 <= args.graph_seed_candidates <= 32:
            parser.error("--graph-seed-candidates must be between 1 and 32")
    if hasattr(args, "embedding"):
        supplied_options = _option_names({
            "--model-device", "--model-batch-size", "--model-max-length",
            "--embedding-model", "--embedding-revision", "--model-cache-dir",
            "--models-offline", "--rerank", "--rerank-candidates",
            "--reranker-model", "--reranker-revision",
        })
        if args.rerank and not args.embedding:
            parser.error("--rerank requires --embedding")
        if supplied_options and not args.embedding:
            parser.error("model options require --embedding: " + ", ".join(sorted(supplied_options)))
        if not 1 <= args.model_batch_size <= 64:
            parser.error("--model-batch-size must be between 1 and 64")
        if not 1 <= args.model_max_length <= 8192:
            parser.error("--model-max-length must be between 1 and 8192")
        if args.embedding_model is not None and args.embedding_revision is None:
            parser.error("--embedding-model requires an explicit immutable --embedding-revision")
        if not 1 <= args.rerank_candidates <= 500:
            parser.error("--rerank-candidates must be between 1 and 500")
        if args.reranker_model is not None and args.reranker_revision is None:
            parser.error("--reranker-model requires an explicit immutable --reranker-revision")
        if not args.rerank and supplied_options.intersection({"--rerank-candidates", "--reranker-model", "--reranker-revision"}):
            parser.error("reranker settings require --rerank")
    if args.command == "vault" and (args.repo_root is not None or args.vault is not None):
        parser.error("vault registry commands cannot be combined with --repo-root or --vault")
    if (
        args.command == "vault"
        and args.vault_command == "default"
        and ((args.selector is None) == (not args.clear))
    ):
        parser.error("vault default requires one selector or --clear")
    if (
        args.command == "agent"
        and args.agent_command in {"search", "context"}
        and ((args.query is None) == (args.plan is None))
    ):
        parser.error("agent search/context requires exactly one of query or --plan")
    if (
        args.command == "agent"
        and args.agent_command in {"search", "context"}
        and args.plan is not None
        and any(getattr(args, name) is not None for name in ("limit", "depth", "graph_strategy"))
    ):
        parser.error("--plan cannot be combined with query retrieval controls")
    return args


def _error(kind: str, code: str, message: str) -> None:
    print(pretty_json({"kind": kind, "code": code, "message": message}), end="", file=sys.stderr)


def _query_plan(args: argparse.Namespace, repo_root: Path, default_limit: int):
    from .retrieval import load_retrieval_plan, query_retrieval_plan

    if args.plan is not None:
        return load_retrieval_plan(_resolve_cli_path(repo_root, args.plan)), "planned"
    plan = query_retrieval_plan(
        str(args.query),
        limit=args.limit if args.limit is not None else default_limit,
        max_depth=args.depth if args.depth is not None else 1,
        graph_strategy=args.graph_strategy or "hybrid",
    )
    return plan, "query"


def _agent(args: argparse.Namespace, repo_root: Path, registry: Path) -> int:
    from .query import (
        QueryError,
        expand,
        get,
        load_graph_view,
        personalized_pagerank,
        query_status,
        resolve_concepts,
    )
    from .retrieval import (
        RetrievalError,
        build_context_from_execution,
        execute_retrieval_plan,
    )

    try:
        view = load_graph_view(repo_root, registry)
    except QueryError as error:
        raise KnowledgeError(str(error)) from error
    if args.agent_command == "status":
        result = query_status(view)
    elif args.agent_command == "resolve":
        result = resolve_concepts(view, list(args.concept))
    elif args.agent_command == "get":
        result = get(view, args.id)
    elif args.agent_command == "expand":
        result = expand(
            view,
            list(args.id),
            direction=args.direction,
            edge_types=args.edge_types,
            max_depth=args.depth,
            limit=args.limit,
        )
    elif args.agent_command == "ppr":
        result = personalized_pagerank(
            view,
            {str(node_id): 1.0 for node_id in args.id},
            edge_types=args.edge_types,
            direction=args.direction,
            limit=args.limit,
        )
    else:
        try:
            plan, plan_mode = _query_plan(args, repo_root, 20 if args.agent_command == "search" else 50)
            execution = execute_retrieval_plan(
                view,
                plan,
                plan_mode=plan_mode,
                ranking_service=make_ranking_service(args, repo_root=repo_root),
                graph_policy=make_graph_retrieval_policy(args),
            )
            result = (
                execution
                if args.agent_command == "search"
                else build_context_from_execution(view, execution, plan=plan, token_budget=args.budget)
            )
        except RetrievalError as error:
            print(pretty_json(error.to_payload()), end="", file=sys.stderr)
            return 1
    print(pretty_json(result), end="")
    return 0


def _ingest(args: argparse.Namespace, repo_root: Path, registry: Path) -> int:
    from .ingest import (
        IngestError,
        IngestPaths,
        apply_ingest,
        load_request,
        plan_ingest,
    )

    paths = IngestPaths(repo_root=repo_root, registry=registry)
    fail_stage = os.environ.get("KGDISTILLER_INGEST_FAIL_STAGE", "")
    crash_stage = os.environ.get("KGDISTILLER_INGEST_CRASH_STAGE", "")

    def inject(stage: str) -> None:
        if crash_stage and stage == crash_stage:
            os._exit(86)
        if fail_stage and stage == fail_stage:
            raise IngestError("injected-failure", f"failure injected at {stage}", stage=stage)

    injector = inject if fail_stage or crash_stage else None
    try:
        request = load_request(_resolve_cli_path(repo_root, args.request), mode=args.ingest_command)
        if args.ingest_command == "plan":
            result = plan_ingest(paths, request, failure_injector=injector)
            destination = args.output
        else:
            result = apply_ingest(paths, request, failure_injector=injector)
            destination = args.receipt
    except IngestError as error:
        print(pretty_json(error.payload()), end="", file=sys.stderr)
        return 1
    content = pretty_json(result)
    if destination is None:
        print(content, end="")
        return 0
    output = _resolve_cli_path(repo_root, destination)
    atomic_write_text(output, content)
    print(pretty_json({
        "schema": result["schema"],
        "request_id": result["request_id"],
        "status": result["status"],
        "output": str(output),
    }), end="")
    return 0


def _compiled(args: argparse.Namespace) -> int:
    from .compiled_retrieval import CompiledLibrary, CompiledRetrievalError

    try:
        library = CompiledLibrary.from_path(args.library)
        if args.compiled_operation == "search":
            result = {"candidates": library.search(args.query, limit=args.limit)}
        elif args.compiled_operation == "browse":
            result = library.browse(args.reference)
        elif args.compiled_operation == "inventory":
            result = library.inventory(args.term)
        elif args.compiled_operation == "get":
            result = {"entries": [library.get(reference) for reference in args.reference]}
        else:
            result = library.pack(args.reference, byte_budget=args.budget)
    except (CompiledRetrievalError, OSError) as error:
        print(pretty_json({"error": str(error)}), end="", file=sys.stderr)
        return 1
    if args.compiled_operation == "pack":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False), end="")
    else:
        print(pretty_json(result), end="")
    return 0


def _vault(args: argparse.Namespace) -> int:
    from kgdistiller.vault_registry import (
        doctor_vaults,
        list_vaults,
        register_vault,
        set_default_vault,
        show_vault,
        unregister_vault,
    )

    if args.vault_command == "register":
        result = register_vault(args.path, name=args.name, home=args.kgdistiller_home, replace=args.replace)
    elif args.vault_command == "list":
        result = list_vaults(args.kgdistiller_home)
    elif args.vault_command == "show":
        result = show_vault(args.selector, args.kgdistiller_home)
    elif args.vault_command == "default":
        result = set_default_vault(None if args.clear else args.selector, args.kgdistiller_home)
    elif args.vault_command == "unregister":
        result = unregister_vault(args.selector, args.kgdistiller_home)
    else:
        result = doctor_vaults(args.selector, args.kgdistiller_home)
    print(pretty_json(result), end="")
    return 1 if result.get("status") == "error" else 0


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


def main() -> int:
    configure_console_streams()
    args = parse_args()
    try:
        if args.command == "agent" and args.agent_command == "compiled":
            return _compiled(args)
        if args.command == "vault":
            return _vault(args)
        if args.command in {"codex", "claude"}:
            return _runtime_product(args)
        from kgdistiller.vault_registry import resolve_repo_root

        repo_root = resolve_repo_root(
            explicit_repo_root=args.repo_root,
            explicit_vault=args.vault,
            home=args.kgdistiller_home,
            use_default=args.command != "init",
        )
        # Every repo-relative default lives under the knowledge tree; reject a
        # symlinked tree before any command reads or writes through it.
        knowledge_root(repo_root)
        registry = defaults(repo_root, args.registry)
        if args.command == "init":
            from .project import initialize_project

            result = initialize_project(
                repo_root,
                registry,
                source_root=args.source_root,
                files=args.files,
                force=args.force,
            )
            print(pretty_json(result), end="")
            return 0
        if args.command == "check":
            return check_command(repo_root, registry, fix=args.fix_lines)
        if args.command == "scan":
            print(pretty_json(scan_files(repo_root, registry, list(args.file))), end="")
            return 0
        if args.command == "obsidian":
            from .obsidian_plugin import ObsidianPluginError, install_obsidian_plugin

            try:
                result = install_obsidian_plugin(repo_root, replace=args.replace, enable=args.enable)
            except ObsidianPluginError as error:
                _error("kgdistiller-obsidian-plugin-error", "obsidian-plugin-install-failed", str(error))
                return 1
            print(pretty_json(result), end="")
            return 0
        if args.command == "export":
            from .obsidian_export import ObsidianExportError, export_obsidian_graph

            output = (
                Path(os.path.abspath(args.output))
                if args.output.is_absolute()
                else Path(os.path.abspath(repo_root / args.output))
            )
            try:
                result = export_obsidian_graph(repo_root, output, registry=registry)
            except ObsidianExportError as error:
                _error("kgdistiller-obsidian-export-error", "obsidian-export-failed", str(error))
                return 1
            print(pretty_json(result), end="")
            return 0
        if args.command == "harvest":
            from .harvest import apply_harvest, prepare_harvest
            from .ingest import IngestPaths

            paths = IngestPaths(repo_root=repo_root, registry=registry)
            sheet_path = defaults(repo_root, args.sheet)
            output_path = defaults(repo_root, args.output)
            if args.harvest_command == "prepare":
                payload = read_json(defaults(repo_root, args.input), {})
                result = prepare_harvest(paths, payload, sheet_path, output_path)
            else:
                result = apply_harvest(paths, sheet_path, output_path)
            print(pretty_json(result), end="")
            return 0
        if args.command == "capture":
            from .capture import prepare_capture
            from .ingest import IngestPaths

            paths = IngestPaths(repo_root=repo_root, registry=registry)
            result = prepare_capture(
                paths, read_json(defaults(repo_root, args.input), {}), defaults(repo_root, args.output)
            )
            print(pretty_json(result), end="")
            return 0
        if args.command == "ingest":
            return _ingest(args, repo_root, registry)
        if args.command == "mcp":
            from kgdistiller.mcp import serve_stdio
            from kgdistiller.retrieval import RetrievalError

            try:
                ranking_service = make_ranking_service(args, repo_root=repo_root)
            except RetrievalError as error:
                print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                return 1
            serve_stdio(repo_root, registry, ranking_service=ranking_service)
            return 0
        return _agent(args, repo_root, registry)
    except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"knowledge command failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
