"""Read-only MCP JSON-RPC server over stdio with six tools.

The tools are ``kg_search``, ``kg_resolve``, ``kg_get``, ``kg_neighbors``,
``kg_browse`` and ``kg_pack``. The server is read-only and serves the whole
home. Every tool call goes through ``retrieve``, which opens a fresh read-only
connection to the derived database, so a call sees the latest ``kgd index``
and never writes anything. Only ``kg_search``'s dense lane loads the embedding
model: it loads lazily on the first dense search and stays resident until
``meta.embedding`` changes. The other tools load no model.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from typing import Any, TextIO

from . import __version__
from .home import KnowledgeError
from .records import UNDERSTANDING
from .retrieve import (
    CLASSES,
    DIRECTIONS,
    MAX_DEPTH,
    PACK_BUDGET,
    Filters,
    browse,
    compact_json,
    get,
    neighbors,
    pack,
    resolve,
    search,
)

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_MESSAGE_JSON_DEPTH = 64
MAX_MESSAGE_JSON_VALUES = 100_000
MAX_TOOL_RESPONSE_BYTES = 8 * 1024 * 1024
READ_ONLY_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
INSTRUCTIONS = (
    "Read-only access to every base registered in the kgdistiller home: source-backed records and "
    "role-bound relations. Same name is not same concept; compare senses before assuming identity, and "
    "deliver source:lines with the evidence quotes. Every result reports lag; when lag.changed_files > 0, "
    "lag.unembedded > 0 or lag.embedding_changed, run `kgd index` and repeat the call. If kg_search reports "
    "the retrieval extra missing, repeat it with no_dense. kg_neighbors follows links (dependency closure with "
    "role requires, claims citing a record with dir in), kg_browse lists bases, source trees, a source's records "
    "by kind or every record of a kind, and kg_pack returns whole records within a byte budget with shared "
    "relations and typed gaps."
)


class ToolError(ValueError):
    """A tool call that cannot be answered; reported as a tool result with ``isError``."""


def _strings(max_length: int, max_items: int, *, min_items: int = 0, enum: tuple[str, ...] = ()) -> dict[str, Any]:
    items: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": max_length}
    if enum:
        items["enum"] = list(enum)
    return {"type": "array", "items": items, "minItems": min_items, "maxItems": max_items}


FILTER_PROPERTIES = {
    "base": _strings(64, 32),
    "kind": _strings(64, 32),
    "class": _strings(16, 2, enum=CLASSES),
    "source": _strings(4096, 32),
    "understanding": _strings(32, 3, enum=UNDERSTANDING),
}


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tool(name: str, title: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "title": title, "description": description, "inputSchema": schema, "annotations": READ_ONLY_ANNOTATIONS}


TOOL_DEFINITIONS = [
    _tool(
        "kg_search", "Search Knowledge",
        "Rank records and relations across every base by fusing a lexical lane, a dense lane (when "
        "meta.embedding is set: an embedding model built the index) and a name lane; no_dense skips the "
        "dense lane. Filters repeat: OR within one filter, AND across filters.",
        _object_schema({
            "query": {"type": "string", "minLength": 1, "maxLength": 4096},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 40},
            "no_dense": {"type": "boolean", "default": False},
            **FILTER_PROPERTIES,
        }, ["query"]),
    ),
    _tool(
        "kg_resolve", "Resolve Knowledge Terms",
        "For each term list its senses (records named exactly so), mentions (records whose name contains "
        "it) and pending uses. Names never establish identity.",
        _object_schema({"terms": _strings(4096, 128, min_items=1), **FILTER_PROPERTIES}, ["terms"]),
    ),
    _tool(
        "kg_get", "Get Knowledge Records",
        "Read complete records by uid (base:id, or a bare id held by one base) with their outgoing and "
        "incoming links; source_lines adds the live cited source range widened by that many lines.",
        _object_schema({
            "uids": _strings(256, 128, min_items=1),
            "source_lines": {"type": "integer", "minimum": 0, "maximum": 200},
        }, ["uids"]),
    ),
    _tool(
        "kg_neighbors", "Follow Knowledge Links",
        "Follow links from records (uid: base:id, or a bare id held by one base) and return the edges reached, "
        "each once at its minimum depth and in the link's own direction, with the visited records. dir out "
        "follows a record's own links, in the links citing it (claims and applications), both either. role "
        "restricts the roles followed at every hop (role requires gives the dependency closure). depth limits "
        "the hops and so cuts cycles. Filters restrict the records the walk may enter; start uids always count, "
        "and pending terms and missing targets are reported as leaves.",
        _object_schema({
            "uids": _strings(256, 128, min_items=1),
            "role": _strings(64, 32),
            "dir": {"type": "string", "enum": list(DIRECTIONS), "default": "out", "minLength": 1, "maxLength": 8},
            "depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH, "default": 1},
            **FILTER_PROPERTIES,
        }, ["uids"]),
    ),
    _tool(
        "kg_browse", "Browse Knowledge",
        "Without handle list every base with record, relation and pending-term counts. handle 'base' or "
        "'base:dir/' lists that directory's subdirectories and sources with counts; 'base:path/file' lists the "
        "source's records grouped by kind with its pending terms. Only sources with indexed records appear. A "
        "kind filter switches any scope to a listing of every matching record with its links by role.",
        _object_schema({"handle": {"type": "string", "minLength": 1, "maxLength": 4096}, **FILTER_PROPERTIES}, []),
    ),
    _tool(
        "kg_pack", "Pack Knowledge Evidence",
        "Pack whole records, never truncated, within budget bytes (the UTF-8 length of the compact JSON of the "
        "records list): the given uids, then their requires closure breadth-first up to requires_depth, then "
        "relations linking at least two packed records. A record that does not fit is an over-budget gap and "
        "later smaller ones may still fit. Gaps also report unknown uids, pending terms, missing targets and "
        "linked records not packed. Filters restrict only the records pack adds itself.",
        _object_schema({
            "uids": _strings(256, 128, min_items=1),
            "budget": {"type": "integer", "minimum": 1, "maximum": 4194304, "default": PACK_BUDGET},
            "requires_depth": {"type": "integer", "minimum": 0, "maximum": MAX_DEPTH, "default": 1},
            **FILTER_PROPERTIES,
        }, ["uids"]),
    ),
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
        raise ToolError("tool arguments must be an object")
    schema = TOOL_SCHEMAS[name]
    properties = schema["properties"]
    unexpected = sorted(set(arguments) - set(properties))
    if unexpected:
        raise ToolError(f"unexpected tool arguments: {', '.join(unexpected)}")
    for required in schema["required"]:
        if required not in arguments:
            raise ToolError(f"missing required tool argument: {required}")
    for key, value in arguments.items():
        field = properties[key]
        expected = field["type"]
        if not _check_json_type(value, expected):
            raise ToolError(f"tool argument {key} must be {expected}")
        if expected == "string" and not field["minLength"] <= len(value) <= field["maxLength"]:
            raise ToolError(f"tool argument {key} has an invalid length")
        if expected == "string" and "enum" in field and value not in field["enum"]:
            raise ToolError(f"tool argument {key} must be one of {', '.join(field['enum'])}")
        if expected == "integer" and not field["minimum"] <= value <= field["maximum"]:
            raise ToolError(f"tool argument {key} is outside its allowed range")
        if expected == "array":
            if not field["minItems"] <= len(value) <= field["maxItems"]:
                raise ToolError(f"tool argument {key} has an invalid item count")
            items = field["items"]
            if not all(isinstance(item, str) for item in value):
                raise ToolError(f"tool argument {key} contains invalid items")
            if any(not items["minLength"] <= len(item) <= items["maxLength"] for item in value):
                raise ToolError(f"tool argument {key} contains an invalid string length")
            if "enum" in items and not all(item in items["enum"] for item in value):
                raise ToolError(f"tool argument {key} contains unsupported items")
    return arguments


def _filters(arguments: dict[str, Any]) -> Filters:
    return Filters(
        base=tuple(arguments.get("base", ())),
        kind=tuple(arguments.get("kind", ())),
        class_=tuple(arguments.get("class", ())),
        source=tuple(arguments.get("source", ())),
        understanding=tuple(arguments.get("understanding", ())),
    )


def call_tool(name: str, raw_arguments: Any) -> dict[str, Any]:
    """Run one tool over a fresh read-only connection."""
    if name not in TOOL_SCHEMAS:
        raise ToolError(f"unknown tool: {name}")
    arguments = _validate_arguments(name, raw_arguments)
    if name == "kg_search":
        return search(
            arguments["query"], limit=arguments.get("limit", 40), filters=_filters(arguments),
            dense=not arguments.get("no_dense", False),
        )
    if name == "kg_resolve":
        return resolve(arguments["terms"], filters=_filters(arguments))
    if name == "kg_neighbors":
        return neighbors(
            arguments["uids"], roles=tuple(arguments.get("role", ())), direction=arguments.get("dir", "out"),
            depth=arguments.get("depth", 1), filters=_filters(arguments),
        )
    if name == "kg_browse":
        return browse(arguments.get("handle"), filters=_filters(arguments))
    if name == "kg_pack":
        return pack(
            arguments["uids"], budget=arguments.get("budget", PACK_BUDGET),
            requires_depth=arguments.get("requires_depth", 1), filters=_filters(arguments),
        )
    return get(arguments["uids"], source_lines=arguments.get("source_lines"))


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    text = compact_json(value)
    if len(text.encode("utf-8")) > MAX_TOOL_RESPONSE_BYTES:
        raise ToolError(f"tool response exceeds the {MAX_TOOL_RESPONSE_BYTES}-byte limit; narrow the call")
    return {"content": [{"type": "text", "text": text}], "structuredContent": value, "isError": is_error}


def _tool_error(name: str, message: str) -> dict[str, Any]:
    return _tool_result({"error": {"code": "tool-error", "message": message, "tool": name if name in TOOL_SCHEMAS else "unknown"}}, is_error=True)


class MCPServer:
    """Small stateful MCP dispatcher for newline-delimited stdio transport."""

    def __init__(self) -> None:
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
            return _result(request_id, {"protocolVersion": self.protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "kgdistiller", "version": __version__}, "instructions": INSTRUCTIONS})
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
                return _result(request_id, _tool_result(call_tool(name, params.get("arguments"))))
            except (ToolError, KnowledgeError, OSError, sqlite3.Error) as error:
                return _result(request_id, _tool_error(name, str(error)))
            except Exception:  # noqa: BLE001
                return _result(request_id, _tool_error(name, "tool execution failed"))
        return _protocol_error(request_id, -32601, "Method not found")


def serve_stdio(*, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    server = MCPServer()
    for raw_line, oversized in _bounded_input_lines(source):
        if oversized:
            destination.write(compact_json(_protocol_error(None, -32700, "Parse error")) + "\n")
            destination.flush()
            continue
        try:
            message = json.loads(raw_line, parse_int=_bounded_json_int, parse_float=_bounded_json_float, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError):
            response = _protocol_error(None, -32700, "Parse error")
        else:
            response = server.handle(message)
        if response is not None:
            destination.write(compact_json(response) + "\n")
            destination.flush()
