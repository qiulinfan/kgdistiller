"""Read-only MCP JSON-RPC server over a freshly loaded entry store per call."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .contracts import canonical_json, load_contract_schema
from .graph_retrieval import GraphRetrievalPolicy
from .home import Base
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
    MAX_RETRIEVAL_RESPONSE_BYTES,
    RETRIEVAL_PLAN_SCHEMA,
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    query_retrieval_plan,
)

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_MESSAGE_JSON_DEPTH = 64
MAX_MESSAGE_JSON_VALUES = 100_000
MAX_TOOL_RESPONSE_BYTES = MAX_RETRIEVAL_RESPONSE_BYTES
READ_ONLY_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


def _object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


RETRIEVAL_PLAN_INPUT_SCHEMA = load_contract_schema(RETRIEVAL_PLAN_SCHEMA)
COMMON_RETRIEVAL_PROPERTIES = {
    "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1},
    "graph_strategy": {"type": "string", "enum": ["bfs", "ppr", "hybrid"], "default": "hybrid"},
    "graph_retrieval": {"type": "boolean", "default": False},
    "graph_seed_candidates": {"type": "integer", "minimum": 1, "maximum": 32, "default": 5},
    "graph_edge_policy": {"type": "string", "enum": ["high-confidence", "all"], "default": "high-confidence"},
}


def _tool(name: str, title: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "title": title, "description": description, "inputSchema": schema, "annotations": READ_ONLY_ANNOTATIONS}


TOOL_DEFINITIONS = [
    _tool("kg_compiled_knowledge", "Compiled Knowledge", "Search compiled meanings, inventory exact authored term declarations, browse explicit dependencies and claims, read complete definitions, or pack selected evidence. Inventories do not certify source-corpus completeness; search candidates and packed records do not certify scientific truth.", _object_schema({"library_path": {"type": "string", "minLength": 1, "maxLength": 4096}, "operation": {"type": "string", "enum": ["search", "browse", "get", "inventory", "pack"]}, "query": {"type": "string", "minLength": 1, "maxLength": 8192}, "term": {"type": "string", "minLength": 1, "maxLength": 8192}, "reference": {"type": "string", "minLength": 1, "maxLength": 4096}, "references": {"type": "array", "minItems": 1, "maxItems": 128, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 4096}}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 40}, "byte_budget": {"type": "integer", "minimum": 1, "maximum": 200000, "default": 24000}}, ["library_path", "operation"])),
    _tool("kg_status", "Knowledge Graph Status", "Count the reviewed entries and accepted edges by relation.", _object_schema()),
    _tool("kg_resolve_concepts", "Resolve Knowledge Concepts", "Resolve only explicit IDs, entry labels, and entry aliases as identity.", _object_schema({"concepts": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4096}, "minItems": 1, "maxItems": 512}}, ["concepts"])),
    _tool("kg_search", "Search Knowledge Graph", "Execute one bounded deterministic retrieval plan or plain query.", _object_schema({"query": {"type": "string", "minLength": 1, "maxLength": 4096}, "plan": RETRIEVAL_PLAN_INPUT_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20}, **COMMON_RETRIEVAL_PROPERTIES})),
    _tool("kg_get_node", "Get Knowledge Node", "Read one entry with its direct incoming and outgoing typed edges.", _object_schema({"id": {"type": "string", "minLength": 1, "maxLength": 256}}, ["id"])),
    _tool("kg_expand", "Expand Knowledge Subgraph", "Traverse a bounded typed neighborhood with explicit paths.", _object_schema({"ids": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256}, "minItems": 1, "maxItems": 128}, "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "default": "both"}, "edge_types": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 64}, "maxItems": 32}, "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}}, ["ids"])),
    _tool("kg_ppr", "Run Knowledge Graph PPR", "Run deterministic Personalized PageRank over the permitted typed edges; edge confidence is authored metadata, not scientific review.", _object_schema({"ids": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256}, "minItems": 1, "maxItems": 128}, "edge_types": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 64}, "maxItems": 32}, "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "default": "outgoing"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}}, ["ids"])),
    _tool("kg_build_context", "Build Knowledge Context", "Pack entries and their edges from a bounded search execution.", _object_schema({"query": {"type": "string", "minLength": 1, "maxLength": 4096}, "plan": RETRIEVAL_PLAN_INPUT_SCHEMA, "token_budget": {"type": "integer", "minimum": 1, "maximum": 200000, "default": 6000}, "result_limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, **COMMON_RETRIEVAL_PROPERTIES})),
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
    return value is None or type(value) is int or (type(value) is float and math.isfinite(value)) or (type(value) is str and len(value.encode("utf-8")) <= MAX_MESSAGE_BYTES)


def _bounded_input_lines(source: TextIO):
    while True:
        raw_line = source.readline(MAX_MESSAGE_BYTES + 1)
        if raw_line == "":
            return
        truncated = len(raw_line) >= MAX_MESSAGE_BYTES + 1 and not raw_line.endswith("\n")
        if truncated:
            while raw_line and not raw_line.endswith("\n"):
                raw_line = source.readline(MAX_MESSAGE_BYTES + 1)
            yield "", True
            continue
        try:
            oversized = len(raw_line.encode("utf-8")) > MAX_MESSAGE_BYTES
        except UnicodeError:
            oversized = True
        yield raw_line, oversized


def _protocol_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _check_json_type(value: Any, expected: str) -> bool:
    return {"string": lambda: isinstance(value, str), "integer": lambda: isinstance(value, int) and not isinstance(value, bool), "boolean": lambda: isinstance(value, bool), "array": lambda: isinstance(value, list), "object": lambda: isinstance(value, dict)}.get(expected, lambda: True)()


def _validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise QueryError("tool arguments must be an object")
    schema = TOOL_SCHEMAS[name]
    properties = schema.get("properties") or {}
    unexpected = sorted(set(arguments) - set(properties))
    if unexpected:
        raise QueryError(f"unexpected tool arguments: {', '.join(unexpected)}")
    for required in schema.get("required") or []:
        if required not in arguments:
            raise QueryError(f"missing required tool argument: {required}")
    for key, value in arguments.items():
        field = properties[key]
        expected = str(field.get("type", ""))
        if not _check_json_type(value, expected):
            raise QueryError(f"tool argument {key} must be {expected}")
        if expected == "string":
            if len(value) < int(field.get("minLength", 0)) or len(value) > int(field.get("maxLength", len(value))):
                raise QueryError(f"tool argument {key} has an invalid length")
            if field.get("enum") and value not in field["enum"]:
                raise QueryError(f"tool argument {key} has an unsupported value")
        elif expected == "integer" and (value < int(field.get("minimum", value)) or value > int(field.get("maximum", value))):
            raise QueryError(f"tool argument {key} is outside its allowed range")
        elif expected == "array":
            if len(value) < int(field.get("minItems", 0)) or len(value) > int(field.get("maxItems", len(value))):
                raise QueryError(f"tool argument {key} has an invalid item count")
            items = field.get("items") or {}
            if items.get("type") and not all(_check_json_type(item, items["type"]) for item in value):
                raise QueryError(f"tool argument {key} contains invalid items")
            if items.get("enum") and not all(item in items["enum"] for item in value):
                raise QueryError(f"tool argument {key} contains unsupported items")
            if items.get("type") == "string" and any(
                len(item) < int(items.get("minLength", 0))
                or len(item) > int(items.get("maxLength", len(item)))
                for item in value
            ):
                raise QueryError(f"tool argument {key} contains an invalid string length")
    if name in {"kg_search", "kg_build_context"} and (("query" in arguments) == ("plan" in arguments)):
        raise QueryError(f"tool {name} requires exactly one of query or plan")
    if name in {"kg_search", "kg_build_context"} and not arguments.get("graph_retrieval", False):
        unused = sorted({"graph_seed_candidates", "graph_edge_policy"}.intersection(arguments))
        if unused:
            raise QueryError("graph options require graph_retrieval: " + ", ".join(unused))
    if "plan" in arguments:
        controls = {"max_depth", "graph_strategy", "limit" if name == "kg_search" else "result_limit"}
        conflict = sorted(controls.intersection(arguments))
        if conflict:
            raise QueryError("retrieval plan cannot be combined with plain-query controls: " + ", ".join(conflict))
    return arguments


def call_tool(
    base: Base,
    name: str,
    raw_arguments: Any,
    *,
    ranking_service: Any = None,
) -> dict[str, Any]:
    """Execute one tool against exactly one complete, freshly loaded GraphView."""
    if name not in TOOL_SCHEMAS:
        raise QueryError(f"unknown tool: {name}")
    arguments = _validate_arguments(name, raw_arguments)
    if name == "kg_compiled_knowledge":
        from .compiled_retrieval import CompiledLibrary, CompiledRetrievalError

        library_path = Path(arguments["library_path"])
        if not library_path.is_absolute():
            raise QueryError("compiled library_path must be absolute")
        operation = arguments["operation"]
        if operation == "search" and "query" not in arguments:
            raise QueryError("compiled search requires query")
        if operation == "get" and "reference" not in arguments:
            raise QueryError("compiled get requires reference")
        if operation == "inventory" and "term" not in arguments:
            raise QueryError("compiled inventory requires term")
        if operation == "inventory" and any(key in arguments for key in ("limit", "byte_budget")):
            raise QueryError("compiled inventory does not rank or truncate declarations")
        if operation == "pack" and "references" not in arguments:
            raise QueryError("compiled pack requires references")
        try:
            library = CompiledLibrary.from_path(library_path)
            if operation == "search":
                return {"candidates": library.search(arguments["query"], limit=arguments.get("limit", 40))}
            if operation == "browse":
                return library.browse(arguments.get("reference"))
            if operation == "get":
                return library.get(arguments["reference"])
            if operation == "inventory":
                return library.inventory(arguments["term"])
            return library.pack(arguments["references"], byte_budget=arguments.get("byte_budget", 24000))
        except (CompiledRetrievalError, OSError) as error:
            raise QueryError(str(error)) from error
    view = load_graph_view(base)
    if name == "kg_status":
        return query_status(view)
    if name == "kg_resolve_concepts":
        return {"results": resolve_concepts(view, list(arguments["concepts"]))}
    if name == "kg_get_node":
        return get(view, str(arguments["id"]))
    if name == "kg_expand":
        return expand(view, list(arguments["ids"]), direction=str(arguments.get("direction", "both")), edge_types=arguments.get("edge_types"), max_depth=int(arguments.get("max_depth", 1)), limit=int(arguments.get("limit", 50)))
    if name == "kg_ppr":
        return personalized_pagerank(view, {str(node_id): 1.0 for node_id in arguments["ids"]}, edge_types=arguments.get("edge_types"), direction=str(arguments.get("direction", "outgoing")), limit=int(arguments.get("limit", 50)))
    if "plan" in arguments:
        plan = dict(arguments["plan"])
        plan_mode = "planned"
    else:
        limit_key = "limit" if name == "kg_search" else "result_limit"
        plan = query_retrieval_plan(str(arguments["query"]), limit=int(arguments.get(limit_key, 20 if name == "kg_search" else 50)), max_depth=int(arguments.get("max_depth", 1)), graph_strategy=str(arguments.get("graph_strategy", "hybrid")))
        plan_mode = "query"
    graph_policy = GraphRetrievalPolicy(candidate_limit=int(arguments.get("graph_seed_candidates", 5)), edge_policy=str(arguments.get("graph_edge_policy", "high-confidence"))) if arguments.get("graph_retrieval", False) else None
    execution = execute_retrieval_plan(view, plan, plan_mode=plan_mode, ranking_service=ranking_service, graph_policy=graph_policy)
    if name == "kg_search":
        return execution
    return build_context_from_execution(view, execution, plan=plan, token_budget=int(arguments.get("token_budget", 6000)))


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    text = canonical_json(value)
    if len(text.encode("utf-8")) > MAX_TOOL_RESPONSE_BYTES:
        raise QueryError(
            f"tool response exceeds the {MAX_TOOL_RESPONSE_BYTES}-byte limit"
        )
    return {"content": [{"type": "text", "text": text}], "structuredContent": value, "isError": is_error}


class MCPServer:
    """Small stateful MCP dispatcher for newline-delimited stdio transport."""

    def __init__(self, base: Base, *, ranking_service: Any = None):
        self.base = base
        self.ranking_service = ranking_service
        self.initialized = False
        self.protocol_version = MCP_PROTOCOL_VERSION

    def handle(self, message: Any) -> dict[str, Any] | None:
        if type(message) is not dict or not _bounded_json_shape(message) or message.get("jsonrpc") != "2.0" or type(message.get("method")) is not str:
            return _protocol_error(None, -32600, "Invalid Request")
        method = message["method"]
        request_id = message.get("id")
        if "id" in message and not _valid_request_id(request_id):
            return _protocol_error(None, -32600, "Invalid Request")
        notification = "id" not in message
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return _protocol_error(request_id, -32602, "Invalid params")
            requested = str(params.get("protocolVersion", ""))
            self.protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
            return _result(request_id, {"protocolVersion": self.protocol_version, "capabilities": {"tools": {"listChanged": False}, "experimental": {"queryBackend": "json-memory"}}, "serverInfo": {"name": "kgdistiller", "version": __version__}, "instructions": "Read-only access to a source-backed knowledge graph of reviewed entries. Resolve identities before assuming equivalence and retain evidence."})
        if notification:
            return None
        if method == "ping":
            return _result(request_id, {})
        if not self.initialized:
            return _protocol_error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            return _result(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return _protocol_error(request_id, -32602, "Invalid params")
            name = str(params.get("name", ""))
            try:
                options = {}
                if name in {"kg_search", "kg_build_context"}:
                    options["ranking_service"] = self.ranking_service
                value = call_tool(self.base, name, params.get("arguments"), **options)
                return _result(request_id, _tool_result(value))
            except RetrievalError as error:
                return _result(request_id, _tool_result({"error": error.to_payload()}, is_error=True))
            except (QueryError, OSError, ValueError) as error:
                return _result(request_id, _tool_result({"error": {"code": "tool-error", "message": str(error), "tool": name}}, is_error=True))
            except Exception:  # noqa: BLE001
                return _result(request_id, _tool_result({"error": {"code": "tool-error", "message": "tool execution failed", "tool": name if name in TOOL_SCHEMAS else "unknown"}}, is_error=True))
        return _protocol_error(request_id, -32601, "Method not found")


def serve_stdio(
    base: Base,
    *,
    ranking_service: Any = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    server = MCPServer(base, ranking_service=ranking_service)
    for raw_line, oversized in _bounded_input_lines(source):
        if oversized:
            destination.write(canonical_json(_protocol_error(None, -32700, "Parse error")) + "\n")
            destination.flush()
            continue
        try:
            message = json.loads(raw_line, parse_int=_bounded_json_int, parse_float=_bounded_json_float, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError):
            response = _protocol_error(None, -32700, "Parse error")
        else:
            response = server.handle(message)
        if response is not None:
            destination.write(canonical_json(response) + "\n")
            destination.flush()
