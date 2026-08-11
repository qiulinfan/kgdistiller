"""Read-only Model Context Protocol server for a kgdistiller Agent index."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from . import __version__
from .agent import (
    AgentIndexError,
    align_graph,
    canonical_json,
    compare_graph,
    create_proposal,
    expand_index,
    get_index_node,
    index_status,
    personalized_pagerank,
    resolve_concepts,
)
from .contracts import load_contract_schema
from .retrieval import (
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    legacy_retrieval_plan,
)


MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_MESSAGE_JSON_DEPTH = 64
MAX_MESSAGE_JSON_VALUES = 100_000
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
RETRIEVAL_PLAN_INPUT_SCHEMA = load_contract_schema("qlkg-retrieval-plan-v1")


def _object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


COMMON_RETRIEVAL_PROPERTIES = {
    "namespace": {"type": "string", "default": "personal"},
    "node_types": {
        "type": "array",
        "items": {"type": "string", "enum": ["knowledge", "field", "topic"]},
        "maxItems": 16,
    },
    "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1},
    "include_taxonomy": {"type": "boolean", "default": False},
    "include_stale": {"type": "boolean", "default": False},
    "include_orphaned": {"type": "boolean", "default": False},
    "graph_strategy": {
        "type": "string",
        "enum": ["bfs", "ppr", "hybrid"],
        "default": "hybrid",
    },
}


TOOL_DEFINITIONS = [
    {
        "name": "kg_status",
        "title": "Knowledge Graph Status",
        "description": "Inspect the read-only Agent index schema, graph identity, counts, and retrieval lanes.",
        "inputSchema": _object_schema(),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_resolve_concepts",
        "title": "Resolve Knowledge Concepts",
        "description": "Batch-resolve IDs, canonical labels, global aliases, and evidence-backed scoped aliases without inferring identity from similarity.",
        "inputSchema": _object_schema(
            {
                "concepts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 512,
                },
                "namespace": {"type": "string", "default": "personal"},
            },
            ["concepts"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_search",
        "title": "Search Knowledge Graph",
        "description": "Execute one bounded retrieval plan, or adapt one legacy query, with per-lane evidence.",
        "inputSchema": _object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 4096},
                "plan": RETRIEVAL_PLAN_INPUT_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
                **COMMON_RETRIEVAL_PROPERTIES,
            }
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_get_node",
        "title": "Get Knowledge Node",
        "description": "Read one node with direct typed edges, backlinks, entry, and provenance.",
        "inputSchema": _object_schema(
            {
                "id": {"type": "string", "minLength": 1},
                "namespace": {"type": "string", "default": "personal"},
            },
            ["id"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_expand",
        "title": "Expand Knowledge Subgraph",
        "description": "Traverse a bounded typed neighborhood and return explicit traversal paths.",
        "inputSchema": _object_schema(
            {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 128,
                },
                "namespace": {"type": "string", "default": "personal"},
                "direction": {
                    "type": "string",
                    "enum": ["incoming", "outgoing", "both"],
                    "default": "both",
                },
                "edge_types": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "include_taxonomy": {"type": "boolean", "default": False},
                "include_stale": {"type": "boolean", "default": False},
                "include_orphaned": {"type": "boolean", "default": False},
            },
            ["ids"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_ppr",
        "title": "Run Knowledge Graph PPR",
        "description": "Run weighted Personalized PageRank from explicit seeds over trusted graph edges and disposable similarity edges.",
        "inputSchema": _object_schema(
            {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 128,
                },
                "namespace": {"type": "string", "default": "personal"},
                "node_types": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                "edge_types": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "include_taxonomy": {"type": "boolean", "default": False},
                "include_similarity": {"type": "boolean", "default": True},
                "include_stale": {"type": "boolean", "default": False},
                "include_orphaned": {"type": "boolean", "default": False},
            },
            ["ids"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_build_context",
        "title": "Build Knowledge Context",
        "description": "Build a deterministic evidence bundle from one bounded plan or legacy query.",
        "inputSchema": _object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 4096},
                "plan": RETRIEVAL_PLAN_INPUT_SCHEMA,
                "token_budget": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200000,
                    "default": 6000,
                },
                "result_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                },
                **COMMON_RETRIEVAL_PROPERTIES,
            }
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_align_graph",
        "title": "Align Candidate Knowledge Graph",
        "description": "Propose source-backed cross-namespace concept mappings without committing graph identity.",
        "inputSchema": _object_schema(
            {
                "candidate_snapshot": {"type": "object"},
                "target_namespace": {"type": "string", "default": "personal"},
                "limit_per_node": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 10,
                },
            },
            ["candidate_snapshot"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_compare_graph",
        "title": "Compare Candidate Knowledge Graph",
        "description": "Compare an isolated candidate snapshot with the indexed personal graph without mutation.",
        "inputSchema": _object_schema(
            {
                "candidate_snapshot": {"type": "object"},
                "target_namespace": {"type": "string", "default": "personal"},
            },
            ["candidate_snapshot"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "kg_create_proposal",
        "title": "Create Knowledge Review Proposal",
        "description": "Create a deterministic review package and delta preview without writing authority data.",
        "inputSchema": _object_schema(
            {
                "candidate_snapshot": {"type": "object"},
                "target_namespace": {"type": "string", "default": "personal"},
                "target_authority": {"type": "string"},
            },
            ["candidate_snapshot"],
        ),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
]


TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOL_DEFINITIONS}


def _bounded_json_int(value: str) -> int:
    if len(value.lstrip("-")) > 32:
        raise ValueError("JSON integer is too long")
    return int(value)


def _bounded_json_float(value: str) -> float:
    if len(value) > 64:
        raise ValueError("JSON number is too long")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number is not finite")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON constants are forbidden")


def _bounded_json_shape(value: Any) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if depth > MAX_MESSAGE_JSON_DEPTH or visited > MAX_MESSAGE_JSON_VALUES:
            return False
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    return False
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif type(current) is str:
            try:
                if len(current.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    return False
            except UnicodeError:
                return False
        elif type(current) is int:
            if current.bit_length() > 107:
                return False
        elif type(current) is float:
            if not math.isfinite(current):
                return False
        elif current is not None and type(current) is not bool:
            return False
    return True


def _valid_request_id(value: Any) -> bool:
    if value is None or type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is str:
        try:
            return len(value.encode("utf-8")) <= MAX_MESSAGE_BYTES
        except UnicodeError:
            return False
    return False


def _bounded_input_lines(source: TextIO):
    while True:
        raw_line = source.readline(MAX_MESSAGE_BYTES + 1)
        if raw_line == "":
            return
        truncated = len(raw_line) >= MAX_MESSAGE_BYTES + 1 and not raw_line.endswith(
            "\n"
        )
        if truncated:
            while raw_line and not raw_line.endswith("\n"):
                raw_line = source.readline(MAX_MESSAGE_BYTES + 1)
            yield "", True
            continue
        try:
            oversized = len(raw_line.encode("utf-8")) > MAX_MESSAGE_BYTES
        except UnicodeError:
            oversized = False
        yield raw_line, oversized


def _protocol_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _check_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise AgentIndexError("tool arguments must be an object")
    schema = TOOL_SCHEMAS[name]
    properties = schema.get("properties") or {}
    unexpected = sorted(set(arguments) - set(properties))
    if unexpected:
        raise AgentIndexError(f"unexpected tool arguments: {', '.join(unexpected)}")
    for required in schema.get("required") or []:
        if required not in arguments:
            raise AgentIndexError(f"missing required tool argument: {required}")
    for key, value in arguments.items():
        field = properties[key]
        expected = str(field.get("type", ""))
        if not _check_json_type(value, expected):
            raise AgentIndexError(f"tool argument {key} must be {expected}")
        if expected == "string":
            if len(value) < int(field.get("minLength", 0)):
                raise AgentIndexError(f"tool argument {key} is too short")
            if len(value) > int(field.get("maxLength", len(value))):
                raise AgentIndexError(f"tool argument {key} is too long")
            if field.get("enum") and value not in field["enum"]:
                raise AgentIndexError(f"tool argument {key} has an unsupported value")
        elif expected == "integer":
            if value < int(field.get("minimum", value)) or value > int(
                field.get("maximum", value)
            ):
                raise AgentIndexError(f"tool argument {key} is outside its allowed range")
        elif expected == "array":
            if len(value) < int(field.get("minItems", 0)) or len(value) > int(
                field.get("maxItems", len(value))
            ):
                raise AgentIndexError(f"tool argument {key} has an invalid item count")
            item_schema = field.get("items") or {}
            item_type = str(item_schema.get("type", ""))
            if item_type and not all(_check_json_type(item, item_type) for item in value):
                raise AgentIndexError(f"tool argument {key} contains invalid items")
            if item_schema.get("enum") and not all(
                item in item_schema["enum"] for item in value
            ):
                raise AgentIndexError(f"tool argument {key} contains unsupported items")
    if name in {"kg_search", "kg_build_context"} and (
        ("query" in arguments) == ("plan" in arguments)
    ):
        raise AgentIndexError(f"tool {name} requires exactly one of query or plan")
    if "plan" in arguments:
        legacy_controls = {
            "namespace",
            "node_types",
            "max_depth",
            "include_taxonomy",
            "include_stale",
            "include_orphaned",
            "graph_strategy",
            "limit" if name == "kg_search" else "result_limit",
        }
        conflicting = sorted(legacy_controls.intersection(arguments))
        if conflicting:
            raise AgentIndexError(
                "retrieval plan cannot be combined with legacy controls: "
                + ", ".join(conflicting)
            )
    return arguments


def call_tool(
    database: Path,
    name: str,
    raw_arguments: Any,
    *,
    embedding_profile: str | None = None,
    provider_config: Mapping[str, Any] | None = None,
    provider_registry: Any = None,
    environ: Mapping[str, str] | None = None,
    expected_graph_sha256: str | None = None,
) -> dict[str, Any]:
    if name not in TOOL_SCHEMAS:
        raise AgentIndexError(f"unknown tool: {name}")
    arguments = _validate_arguments(name, raw_arguments)
    namespace = str(arguments.get("namespace", "personal"))
    if name == "kg_status":
        return index_status(database)
    if name == "kg_resolve_concepts":
        return {
            "results": resolve_concepts(
                database,
                list(arguments["concepts"]),
                namespace=namespace,
            )
        }
    if name == "kg_search":
        if "plan" in arguments:
            plan = dict(arguments["plan"])
            plan_mode = "planned"
            execution_namespace_argument = None
        else:
            plan = legacy_retrieval_plan(
                str(arguments["query"]),
                namespace=namespace,
                node_types=arguments.get("node_types"),
                limit=int(arguments.get("limit", 20)),
                max_depth=int(arguments.get("max_depth", 1)),
                include_taxonomy=bool(arguments.get("include_taxonomy", False)),
                include_stale=bool(arguments.get("include_stale", False)),
                include_orphaned=bool(arguments.get("include_orphaned", False)),
                graph_strategy=str(arguments.get("graph_strategy", "hybrid")),
            )
            plan_mode = "legacy"
            execution_namespace_argument = namespace
        return execute_retrieval_plan(
            database,
            plan,
            plan_mode=plan_mode,
            namespace=execution_namespace_argument,
            embedding_profile=embedding_profile,
            provider_config=provider_config,
            provider_registry=provider_registry,
            environ=environ,
            expected_graph_sha256=expected_graph_sha256,
        )
    if name == "kg_get_node":
        return get_index_node(database, str(arguments["id"]), namespace=namespace)
    if name == "kg_expand":
        return expand_index(
            database,
            list(arguments["ids"]),
            namespace=namespace,
            direction=str(arguments.get("direction", "both")),
            edge_types=arguments.get("edge_types"),
            max_depth=int(arguments.get("max_depth", 1)),
            limit=int(arguments.get("limit", 50)),
            include_taxonomy=bool(arguments.get("include_taxonomy", False)),
            include_stale=bool(arguments.get("include_stale", False)),
            include_orphaned=bool(arguments.get("include_orphaned", False)),
        )
    if name == "kg_ppr":
        return personalized_pagerank(
            database,
            {str(node_id): 1.0 for node_id in arguments["ids"]},
            namespace=namespace,
            node_types=arguments.get("node_types"),
            edge_types=arguments.get("edge_types"),
            limit=int(arguments.get("limit", 50)),
            include_taxonomy=bool(arguments.get("include_taxonomy", False)),
            include_similarity=bool(arguments.get("include_similarity", True)),
            include_stale=bool(arguments.get("include_stale", False)),
            include_orphaned=bool(arguments.get("include_orphaned", False)),
        )
    if name == "kg_align_graph":
        return align_graph(
            database,
            dict(arguments["candidate_snapshot"]),
            target_namespace=str(arguments.get("target_namespace", "personal")),
            limit_per_node=int(arguments.get("limit_per_node", 10)),
        )
    if name == "kg_compare_graph":
        return compare_graph(
            database,
            dict(arguments["candidate_snapshot"]),
            target_namespace=str(arguments.get("target_namespace", "personal")),
        )
    if name == "kg_create_proposal":
        return create_proposal(
            database,
            dict(arguments["candidate_snapshot"]),
            target_namespace=str(arguments.get("target_namespace", "personal")),
            target_authority=(
                str(arguments["target_authority"])
                if arguments.get("target_authority")
                else None
            ),
        )
    if "plan" in arguments:
        plan = dict(arguments["plan"])
        plan_mode = "planned"
        execution_namespace = str(plan.get("namespace", "personal"))
        execution_namespace_argument = None
    else:
        plan = legacy_retrieval_plan(
            str(arguments["query"]),
            namespace=namespace,
            node_types=arguments.get("node_types"),
            limit=int(arguments.get("result_limit", 50)),
            max_depth=int(arguments.get("max_depth", 1)),
            include_taxonomy=bool(arguments.get("include_taxonomy", False)),
            include_stale=bool(arguments.get("include_stale", False)),
            include_orphaned=bool(arguments.get("include_orphaned", False)),
            graph_strategy=str(arguments.get("graph_strategy", "hybrid")),
        )
        plan_mode = "legacy"
        execution_namespace = namespace
        execution_namespace_argument = namespace
    execution = execute_retrieval_plan(
        database,
        plan,
        plan_mode=plan_mode,
        namespace=execution_namespace_argument,
        embedding_profile=embedding_profile,
        provider_config=provider_config,
        provider_registry=provider_registry,
        environ=environ,
        expected_graph_sha256=expected_graph_sha256,
    )
    return build_context_from_execution(
        database,
        execution,
        plan=plan,
        token_budget=int(arguments.get("token_budget", 6000)),
        namespace=execution_namespace,
    )


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": canonical_json(value)}],
        "structuredContent": value,
        "isError": is_error,
    }


class MCPServer:
    """Small stateful MCP dispatcher for newline-delimited stdio transport."""

    def __init__(
        self,
        database: Path,
        *,
        embedding_profile: str | None = None,
        provider_config: Mapping[str, Any] | None = None,
        provider_registry: Any = None,
        environ: Mapping[str, str] | None = None,
        expected_graph_sha256: str | None = None,
        expected_graph_sha256_resolver: Callable[[], str] | None = None,
    ):
        self.database = database
        self.embedding_profile = embedding_profile
        self.provider_config = provider_config
        self.provider_registry = provider_registry
        self.environ = environ
        self.expected_graph_sha256 = expected_graph_sha256
        self.expected_graph_sha256_resolver = expected_graph_sha256_resolver
        self.initialized = False
        self.protocol_version = MCP_PROTOCOL_VERSION

    def handle(self, message: Any) -> dict[str, Any] | None:
        if (
            type(message) is not dict
            or not _bounded_json_shape(message)
            or message.get("jsonrpc") != "2.0"
            or type(message.get("method")) is not str
        ):
            return _protocol_error(None, -32600, "Invalid Request")
        method = message["method"]
        request_id = message.get("id")
        if "id" in message and not _valid_request_id(request_id):
            return _protocol_error(None, -32600, "Invalid Request")
        is_notification = "id" not in message
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            params = message.get("params")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                return _protocol_error(request_id, -32602, "Invalid params")
            requested = str(params.get("protocolVersion", ""))
            self.protocol_version = (
                requested if requested in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
            )
            return _result(
                request_id,
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "kgdistiller", "version": __version__},
                    "instructions": (
                        "Read-only access to a source-backed personal knowledge graph. "
                        "Resolve identities before assuming equivalence and retain evidence."
                    ),
                },
            )
        if is_notification:
            return None
        if method == "ping":
            return _result(request_id, {})
        if not self.initialized:
            return _protocol_error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            return _result(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            params = message.get("params")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                return _protocol_error(request_id, -32602, "Invalid params")
            name = str(params.get("name", ""))
            try:
                expected_graph_sha256 = self.expected_graph_sha256
                if (
                    name in {"kg_search", "kg_build_context"}
                    and self.expected_graph_sha256_resolver is not None
                ):
                    resolver_failed = False
                    try:
                        expected_graph_sha256 = self.expected_graph_sha256_resolver()
                    except Exception:
                        resolver_failed = True
                    if resolver_failed:
                        raise RetrievalError(
                            "index-unavailable",
                            "Authority graph metadata is unavailable",
                        )
                value = call_tool(
                    self.database,
                    name,
                    params.get("arguments"),
                    embedding_profile=self.embedding_profile,
                    provider_config=self.provider_config,
                    provider_registry=self.provider_registry,
                    environ=self.environ,
                    expected_graph_sha256=expected_graph_sha256,
                )
                return _result(request_id, _tool_result(value))
            except RetrievalError as error:
                return _result(
                    request_id,
                    _tool_result({"error": error.to_payload()}, is_error=True),
                )
            except (AgentIndexError, OSError, sqlite3.Error, ValueError) as error:
                value = {
                    "error": {
                        "code": "tool-error",
                        "message": str(error),
                        "tool": name,
                    }
                }
                return _result(request_id, _tool_result(value, is_error=True))
            except Exception:
                value = {
                    "error": {
                        "code": "tool-error",
                        "message": "tool execution failed",
                        "tool": name if name in TOOL_SCHEMAS else "unknown",
                    }
                }
                return _result(request_id, _tool_result(value, is_error=True))
        return _protocol_error(request_id, -32601, "Method not found")


def serve_stdio(
    database: Path,
    *,
    embedding_profile: str | None = None,
    provider_config: Mapping[str, Any] | None = None,
    provider_registry: Any = None,
    environ: Mapping[str, str] | None = None,
    expected_graph_sha256: str | None = None,
    expected_graph_sha256_resolver: Callable[[], str] | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    """Serve newline-delimited UTF-8 JSON-RPC without writing logs to stdout."""
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    server = MCPServer(
        database,
        embedding_profile=embedding_profile,
        provider_config=provider_config,
        provider_registry=provider_registry,
        environ=environ,
        expected_graph_sha256=expected_graph_sha256,
        expected_graph_sha256_resolver=expected_graph_sha256_resolver,
    )
    for raw_line, oversized in _bounded_input_lines(source):
        if oversized:
            response = _protocol_error(None, -32600, "Message exceeds size limit")
        else:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(
                    line,
                    parse_int=_bounded_json_int,
                    parse_float=_bounded_json_float,
                    parse_constant=_reject_json_constant,
                )
                if not _bounded_json_shape(message):
                    raise ValueError("JSON message shape exceeds the limit")
            except (
                json.JSONDecodeError,
                RecursionError,
                OverflowError,
                TypeError,
                ValueError,
            ):
                response = _protocol_error(None, -32700, "Parse error")
            else:
                response = server.handle(message)
        if response is not None:
            try:
                encoded = canonical_json(response)
                encoded.encode("utf-8")
            except Exception:
                encoded = canonical_json(
                    _protocol_error(None, -32603, "Internal error")
                )
            destination.write(encoded + "\n")
            destination.flush()
