"""Read-only MCP JSON-RPC server over fresh, generation-safe graph views."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .contracts import canonical_json, load_contract_schema
from .query import (
    QueryError,
    align,
    compare,
    expand,
    get,
    load_graph_view,
    personalized_pagerank,
    propose,
    query_status,
    resolve_concepts,
)
from .retrieval import (
    MAX_RETRIEVAL_RESPONSE_BYTES,
    RETRIEVAL_PLAN_SCHEMA,
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    legacy_retrieval_plan,
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
    "namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"},
    "node_types": {"type": "array", "items": {"type": "string", "enum": ["knowledge", "field", "topic"]}, "maxItems": 16},
    "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1},
    "include_taxonomy": {"type": "boolean", "default": False},
    "include_stale": {"type": "boolean", "default": False},
    "include_orphaned": {"type": "boolean", "default": False},
    "graph_strategy": {"type": "string", "enum": ["bfs", "ppr", "hybrid"], "default": "hybrid"},
}


def _tool(name: str, title: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "title": title, "description": description, "inputSchema": schema, "annotations": READ_ONLY_ANNOTATIONS}


TOOL_DEFINITIONS = [
    _tool("kg_status", "Knowledge Graph Status", "Inspect the fresh JSON-memory graph view and generation.", _object_schema()),
    _tool("kg_resolve_concepts", "Resolve Knowledge Concepts", "Resolve only explicit IDs, canonical labels, and global aliases as identity.", _object_schema({"concepts": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4096}, "minItems": 1, "maxItems": 512}, "namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}}, ["concepts"])),
    _tool("kg_search", "Search Knowledge Graph", "Execute one bounded deterministic retrieval plan or legacy query.", _object_schema({"query": {"type": "string", "minLength": 1, "maxLength": 4096}, "plan": RETRIEVAL_PLAN_INPUT_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20}, **COMMON_RETRIEVAL_PROPERTIES})),
    _tool("kg_get_node", "Get Knowledge Node", "Read one node with direct typed edges and backlinks.", _object_schema({"id": {"type": "string", "minLength": 1, "maxLength": 256}, "namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}}, ["id"])),
    _tool("kg_expand", "Expand Knowledge Subgraph", "Traverse a bounded typed neighborhood with explicit paths.", _object_schema({"ids": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256}, "minItems": 1, "maxItems": 128}, "namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}, "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "default": "both"}, "edge_types": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 64}, "maxItems": 32}, "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, "include_taxonomy": {"type": "boolean", "default": False}, "include_stale": {"type": "boolean", "default": False}, "include_orphaned": {"type": "boolean", "default": False}}, ["ids"])),
    _tool("kg_ppr", "Run Knowledge Graph PPR", "Run deterministic Personalized PageRank over trusted graph edges.", _object_schema({"ids": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256}, "minItems": 1, "maxItems": 128}, "namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}, "node_types": {"type": "array", "items": {"type": "string", "enum": ["knowledge", "field", "topic"]}, "maxItems": 16}, "edge_types": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 64}, "maxItems": 32}, "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "default": "outgoing"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, "include_taxonomy": {"type": "boolean", "default": False}, "include_stale": {"type": "boolean", "default": False}, "include_orphaned": {"type": "boolean", "default": False}}, ["ids"])),
    _tool("kg_build_context", "Build Knowledge Context", "Pack deterministic source evidence from a bounded search execution.", _object_schema({"query": {"type": "string", "minLength": 1, "maxLength": 4096}, "plan": RETRIEVAL_PLAN_INPUT_SCHEMA, "token_budget": {"type": "integer", "minimum": 1, "maximum": 200000, "default": 6000}, "result_limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, **COMMON_RETRIEVAL_PROPERTIES})),
    _tool("kg_align_graph", "Align Candidate Knowledge Graph", "Rank conservative source-backed mappings without committing identity.", _object_schema({"candidate_snapshot": {"type": "object"}, "target_namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}, "limit_per_node": {"type": "integer", "minimum": 1, "maximum": 500, "default": 10}}, ["candidate_snapshot"])),
    _tool("kg_compare_graph", "Compare Candidate Knowledge Graph", "Compare an isolated candidate snapshot with the fresh authority view.", _object_schema({"candidate_snapshot": {"type": "object"}, "target_namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}}, ["candidate_snapshot"])),
    _tool("kg_create_proposal", "Create Knowledge Review Proposal", "Create a deterministic review package without writing authority data.", _object_schema({"candidate_snapshot": {"type": "object"}, "target_namespace": {"type": "string", "minLength": 1, "maxLength": 256, "default": "personal"}, "target_authority": {"type": "string", "maxLength": 4096}}, ["candidate_snapshot"])),
]
TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOL_DEFINITIONS}
RECALL_TOOL_DEFINITION = {
    **_tool(
        "kg_recall",
        "Federated Vault Recall",
        "Execute one closed, coherent, read-only recall request across registered Vaults.",
        load_contract_schema("qlkg-recall-request-v1"),
    ),
    "outputSchema": {
        "oneOf": [
            load_contract_schema("qlkg-recall-report-v1"),
            load_contract_schema("qlkg-recall-error-v1"),
        ]
    },
}
FEDERATED_TOOL_DEFINITIONS = [*TOOL_DEFINITIONS, RECALL_TOOL_DEFINITION]


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
    if "plan" in arguments:
        controls = {"namespace", "node_types", "max_depth", "include_taxonomy", "include_stale", "include_orphaned", "graph_strategy", "limit" if name == "kg_search" else "result_limit"}
        conflict = sorted(controls.intersection(arguments))
        if conflict:
            raise QueryError("retrieval plan cannot be combined with legacy controls: " + ", ".join(conflict))
    return arguments


def call_tool(
    graph_dir: Path,
    name: str,
    raw_arguments: Any,
    *,
    alignments: Path | None = None,
) -> dict[str, Any]:
    """Execute one tool against exactly one complete, fresh GraphView."""
    if name not in TOOL_SCHEMAS:
        raise QueryError(f"unknown tool: {name}")
    arguments = _validate_arguments(name, raw_arguments)
    view = load_graph_view(graph_dir, alignments)
    namespace = str(arguments.get("namespace", "personal"))
    if name == "kg_status":
        return query_status(view)
    if name == "kg_resolve_concepts":
        return {"results": resolve_concepts(view, list(arguments["concepts"]), namespace=namespace)}
    if name == "kg_get_node":
        return get(view, str(arguments["id"]), namespace=namespace)
    if name == "kg_expand":
        return expand(view, list(arguments["ids"]), namespace=namespace, direction=str(arguments.get("direction", "both")), edge_types=arguments.get("edge_types"), max_depth=int(arguments.get("max_depth", 1)), limit=int(arguments.get("limit", 50)), include_taxonomy=bool(arguments.get("include_taxonomy", False)), include_stale=bool(arguments.get("include_stale", False)), include_orphaned=bool(arguments.get("include_orphaned", False)))
    if name == "kg_ppr":
        return personalized_pagerank(view, {str(node_id): 1.0 for node_id in arguments["ids"]}, namespace=namespace, node_types=arguments.get("node_types"), edge_types=arguments.get("edge_types"), direction=str(arguments.get("direction", "outgoing")), limit=int(arguments.get("limit", 50)), include_taxonomy=bool(arguments.get("include_taxonomy", False)), include_stale=bool(arguments.get("include_stale", False)), include_orphaned=bool(arguments.get("include_orphaned", False)))
    if name == "kg_align_graph":
        return align(view, dict(arguments["candidate_snapshot"]), target_namespace=str(arguments.get("target_namespace", "personal")), limit_per_node=int(arguments.get("limit_per_node", 10)))
    if name == "kg_compare_graph":
        return compare(view, dict(arguments["candidate_snapshot"]), target_namespace=str(arguments.get("target_namespace", "personal")))
    if name == "kg_create_proposal":
        return propose(view, dict(arguments["candidate_snapshot"]), target_namespace=str(arguments.get("target_namespace", "personal")), target_authority=str(arguments["target_authority"]) if arguments.get("target_authority") else None)
    if "plan" in arguments:
        plan = dict(arguments["plan"])
        plan_mode = "planned"
        execution_namespace = str(plan.get("namespace", "personal"))
        namespace_argument = None
    else:
        limit_key = "limit" if name == "kg_search" else "result_limit"
        plan = legacy_retrieval_plan(str(arguments["query"]), namespace=namespace, node_types=arguments.get("node_types"), limit=int(arguments.get(limit_key, 20 if name == "kg_search" else 50)), max_depth=int(arguments.get("max_depth", 1)), include_taxonomy=bool(arguments.get("include_taxonomy", False)), include_stale=bool(arguments.get("include_stale", False)), include_orphaned=bool(arguments.get("include_orphaned", False)), graph_strategy=str(arguments.get("graph_strategy", "hybrid")))
        plan_mode = "legacy"
        execution_namespace = namespace
        namespace_argument = namespace
    execution = execute_retrieval_plan(view, plan, plan_mode=plan_mode, namespace=namespace_argument)
    if name == "kg_search":
        return execution
    return build_context_from_execution(view, execution, plan=plan, token_budget=int(arguments.get("token_budget", 6000)), namespace=execution_namespace)


def call_recall_tool(
    raw_arguments: Any,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Execute the federated MCP adapter without loading a legacy graph view."""

    from .recall import RecallError, execute_recall_request

    if not isinstance(raw_arguments, dict):
        raise RecallError(
            "invalid-recall-request", "recall request must be a closed JSON object"
        )
    return execute_recall_request(raw_arguments, home=home)


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    text = canonical_json(value)
    if len(text.encode("utf-8")) > MAX_TOOL_RESPONSE_BYTES:
        raise QueryError(
            f"tool response exceeds the {MAX_TOOL_RESPONSE_BYTES}-byte limit"
        )
    return {"content": [{"type": "text", "text": text}], "structuredContent": value, "isError": is_error}


class MCPServer:
    """Small stateful MCP dispatcher for newline-delimited stdio transport."""

    def __init__(
        self,
        graph_dir: Path | None,
        *,
        alignments: Path | None = None,
        federated: bool = False,
        home: Path | str | None = None,
    ):
        self.graph_dir = Path(graph_dir) if graph_dir is not None else None
        self.alignments = Path(alignments) if alignments is not None else None
        self.federated = federated
        self.home = home
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
            backend = "federated-json-memory" if self.federated else "json-memory"
            return _result(request_id, {"protocolVersion": self.protocol_version, "capabilities": {"tools": {"listChanged": False}, "experimental": {"queryBackend": backend}}, "serverInfo": {"name": "kgdistiller", "version": __version__}, "instructions": "Read-only access to a source-backed personal knowledge graph. Resolve identities before assuming equivalence and retain evidence."})
        if notification:
            return None
        if method == "ping":
            return _result(request_id, {})
        if not self.initialized:
            return _protocol_error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            definitions = (
                ([RECALL_TOOL_DEFINITION] if self.graph_dir is None else FEDERATED_TOOL_DEFINITIONS)
                if self.federated
                else TOOL_DEFINITIONS
            )
            return _result(request_id, {"tools": definitions})
        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return _protocol_error(request_id, -32602, "Invalid params")
            name = str(params.get("name", ""))
            if self.federated and name == "kg_recall":
                from .recall import RecallError

                try:
                    value = call_recall_tool(params.get("arguments"), home=self.home)
                    return _result(request_id, _tool_result(value))
                except RecallError as error:
                    return _result(
                        request_id, _tool_result(error.payload(), is_error=True)
                    )
                except Exception:
                    failure = RecallError(
                        "recall-tool-failed",
                        "federated recall could not produce a closed result",
                    )
                    return _result(
                        request_id, _tool_result(failure.payload(), is_error=True)
                    )
            try:
                if self.graph_dir is None:
                    raise QueryError("legacy graph directory is unavailable")
                value = call_tool(self.graph_dir, name, params.get("arguments"), alignments=self.alignments)
                return _result(request_id, _tool_result(value))
            except RetrievalError as error:
                return _result(request_id, _tool_result({"error": error.to_payload()}, is_error=True))
            except (QueryError, OSError, ValueError) as error:
                return _result(request_id, _tool_result({"error": {"code": "tool-error", "message": str(error), "tool": name}}, is_error=True))
            except Exception:
                return _result(request_id, _tool_result({"error": {"code": "tool-error", "message": "tool execution failed", "tool": name if name in TOOL_SCHEMAS else "unknown"}}, is_error=True))
        return _protocol_error(request_id, -32601, "Method not found")


def serve_stdio(
    graph_dir: Path | None,
    *,
    alignments: Path | None = None,
    federated: bool = False,
    home: Path | str | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    server = MCPServer(
        graph_dir, alignments=alignments, federated=federated, home=home
    )
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
