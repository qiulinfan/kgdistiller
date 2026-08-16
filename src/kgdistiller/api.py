"""Closed, generation-bound read API for federated native Vaults.

This module is intentionally isolated from :mod:`kgdistiller.web`, whose
legacy graph viewer and wire bytes remain unchanged.  Every API request owns
one coherent federation snapshot.  Source routes use only the metadata ledger
captured in that snapshot and immutable, digest-verified archive blobs.
"""

from __future__ import annotations

import copy
import bisect
import hashlib
import ipaddress
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .federation import (
    FederatedVault,
    FederationError,
    FederationSnapshot,
    capture_federation,
    qualified_handle,
)
from .recall import RecallError, execute_recall_request
from .source_archive import (
    MAX_DIFF_BYTES,
    MAX_DIFF_LINES,
    SourceArchiveError,
    verified_version_diff,
    verified_version_text,
)
from .web import _allowed_hostnames, _origin_authority, _request_authority


RESPONSE_SCHEMA = "qlkg-api-response-v1"
ERROR_SCHEMA = "qlkg-api-error-v1"
GENERATION_HEADER = "Kgdistiller-Generation"
MAX_REQUEST_TARGET_BYTES = 8192
MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_NEIGHBOR_WORK = 50_000
MAX_STALE_BUILD_WORK = 1_000_000
MAX_STALE_INDEX_ITEMS = 700_000
MAX_STALE_INDEX_BYTES = 128 * 1024 * 1024
MAX_EXCERPT_BYTES = 64 * 1024
MAX_EXCERPT_LINE_CHARACTERS = 4096
MAX_SOURCE_SCAN = 1_000_000
MAX_CAPTURE_WAIT_SECONDS = 30.0
MAX_HTTP_READ_SECONDS = 10.0
MAX_ACTIVE_HTTP_REQUESTS = 16
MAX_HTTP_LISTEN_BACKLOG = 32
DEFAULT_RELATIONS = {
    "contains",
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}
REQUIRES_GENERATION = {
    "roots",
    "node",
    "neighbors",
    "stale",
    "source",
    "versions",
    "diff",
    "excerpt",
    "search",
    "context",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_VAULT_ID_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_NODE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class StaticAsset:
    content: bytes
    media_type: str
    etag: str
    cache_control: str


class StaticAssetProvider(Protocol):
    """Frozen F8 static asset seam; F7 serves only ``/api/v1`` routes."""

    def resolve(self, request_path: str) -> StaticAsset | None:
        ...


class ApiError(RuntimeError):
    """Stable path-free API failure with an HTTP status."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        route: str | None = None,
        vault_id: str | None = None,
        current_generation: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message[:512] or "API request failed"
        self.route = route
        self.vault_id = vault_id
        self.current_generation = current_generation
        self.retryable = retryable

    def payload(self) -> dict[str, Any]:
        return validate_contract(
            {
                "schema": ERROR_SCHEMA,
                "status": "error",
                "route": self.route,
                "vault_id": self.vault_id,
                "error": {"code": self.code[:64], "message": self.message},
                "current_generation": self.current_generation,
                "retryable": self.retryable,
            }
        )


@dataclass(frozen=True)
class ApiHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


@dataclass
class _CaptureFlight:
    done: threading.Event = field(default_factory=threading.Event)
    snapshot: FederationSnapshot | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class _StaleIndex:
    records: tuple[tuple[str, str, Any], ...]
    keys: tuple[str, ...]
    weight_bytes: int


@dataclass
class _StaleFlight:
    key: tuple[str, str]
    done: threading.Event = field(default_factory=threading.Event)
    index: _StaleIndex | None = None
    error: Exception | None = None


class FederationSnapshotCache:
    """Single-flight capture retaining exactly one READY snapshot generation."""

    def __init__(
        self,
        *,
        home: Path | str | None = None,
        capture: Callable[..., FederationSnapshot] = capture_federation,
    ) -> None:
        self._home = home
        self._capture = capture
        self._lock = threading.Lock()
        self._flight: _CaptureFlight | None = None
        self._ready: FederationSnapshot | None = None

    @property
    def ready(self) -> FederationSnapshot | None:
        with self._lock:
            return self._ready

    def acquire(self) -> FederationSnapshot:
        with self._lock:
            flight = self._flight
            leader = flight is None
            if flight is None:
                flight = _CaptureFlight()
                self._flight = flight
        if not leader:
            if not flight.done.wait(MAX_CAPTURE_WAIT_SECONDS):
                raise FederationError(
                    "federation-capture-timeout",
                    "federation capture did not complete within its wait bound",
                )
            if flight.error is not None:
                raise flight.error
            assert flight.snapshot is not None
            return flight.snapshot
        try:
            snapshot = self._capture(home=self._home)
        except Exception as error:
            with self._lock:
                self._ready = None
                flight.error = error
                flight.done.set()
                if self._flight is flight:
                    self._flight = None
            raise
        with self._lock:
            self._ready = snapshot
            flight.snapshot = snapshot
            flight.done.set()
            if self._flight is flight:
                self._flight = None
        return snapshot


@dataclass(frozen=True)
class _RouteRequest:
    route: str
    vault_id: str | None
    node_id: str | None
    document_id: str | None
    query: Mapping[str, str]
    recall_request: Mapping[str, Any] | None
    requested_generation: str | None


def _strict_json(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ApiError(400, "invalid-request-body", "request body is not strict UTF-8") from error

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as error:
        raise ApiError(400, "invalid-request-body", "request body is not closed JSON") from error
    if not isinstance(value, dict):
        raise ApiError(400, "invalid-request-body", "request body must be a JSON object")
    return value


def _header_values(headers: Sequence[tuple[str, str]], name: str) -> list[str]:
    folded = name.casefold()
    return [value.strip() for key, value in headers if key.casefold() == folded]


def _generation_header(
    headers: Sequence[tuple[str, str]], *, required: bool, route: str
) -> str | None:
    values = _header_values(headers, GENERATION_HEADER)
    if not values:
        if required:
            raise ApiError(
                428,
                "generation-required",
                "request requires a federation generation",
                route=route,
            )
        return None
    if len(values) != 1 or not _SHA256_RE.fullmatch(values[0]):
        raise ApiError(
            400,
            "invalid-generation",
            "federation generation header is malformed",
            route=route,
        )
    return values[0]


def _decode_target(target: str) -> tuple[list[str], dict[str, str]]:
    try:
        encoded = target.encode("ascii", errors="strict")
    except UnicodeEncodeError as error:
        raise ApiError(400, "invalid-request-target", "request target must be ASCII encoded") from error
    if (
        len(encoded) > MAX_REQUEST_TARGET_BYTES
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
        or _BAD_PERCENT_RE.search(target)
    ):
        raise ApiError(400, "invalid-request-target", "request target is malformed or too large")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ApiError(400, "invalid-request-target", "request target must be an origin-form path")
    lowered_path = parsed.path.casefold()
    if "%2f" in lowered_path or "%5c" in lowered_path:
        raise ApiError(400, "invalid-request-target", "encoded path separators are forbidden")
    try:
        decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ApiError(400, "invalid-request-target", "request path is not strict UTF-8") from error
    if (
        not decoded_path.startswith("/")
        or "\\" in decoded_path
        or "\x00" in decoded_path
        or "%" in decoded_path
        or "//" in decoded_path
    ):
        raise ApiError(400, "invalid-request-target", "request path is not canonical")
    segments = decoded_path.split("/")[1:]
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ApiError(400, "invalid-request-target", "request path traversal is forbidden")
    if _BAD_PERCENT_RE.search(parsed.query):
        raise ApiError(400, "invalid-query", "request query is malformed")
    if parsed.query:
        try:
            pairs = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=32,
            )
        except (ValueError, UnicodeError) as error:
            raise ApiError(400, "invalid-query", "request query is malformed") from error
    else:
        pairs = []
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            raise ApiError(400, "invalid-query", "duplicate query fields are forbidden")
        if len(key) > 64 or len(value) > 4096:
            raise ApiError(400, "invalid-query", "request query exceeds its bounds")
        query[key] = value
    return segments, query


def _query_fields(query: Mapping[str, str], allowed: set[str]) -> None:
    if set(query) - allowed:
        raise ApiError(400, "unknown-query-field", "request contains an unknown query field")


def _query_bool(query: Mapping[str, str], key: str, default: bool = False) -> bool:
    value = query.get(key)
    if value is None:
        return default
    if value not in {"true", "false"}:
        raise ApiError(400, "invalid-query", "boolean query field is malformed")
    return value == "true"


def _query_int(
    query: Mapping[str, str], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = query.get(key)
    if value is None:
        return default
    if not value.isascii() or not value.isdigit():
        raise ApiError(400, "invalid-query", "integer query field is malformed")
    number = int(value)
    if number < minimum or number > maximum:
        raise ApiError(400, "invalid-query", "integer query field is outside its bound")
    return number


def _parse_route(
    method: str,
    target: str,
    headers: Sequence[tuple[str, str]],
    body: bytes,
) -> _RouteRequest:
    segments, query = _decode_target(target)
    if segments[:2] != ["api", "v1"]:
        raise ApiError(404, "not-found", "API route is unavailable")
    tail = segments[2:]
    route: str
    vault_id: str | None = None
    node_id: str | None = None
    document_id: str | None = None
    if tail in (["status"], ["vaults"], ["search"], ["context"]):
        route = tail[0]
    elif len(tail) >= 3 and tail[0] == "vaults":
        vault_id = tail[1]
        if len(vault_id) > 64 or not _VAULT_ID_RE.fullmatch(vault_id):
            raise ApiError(400, "invalid-vault-id", "Vault identity is malformed")
        if tail[2:] == ["roots"]:
            route = "roots"
        elif len(tail) in {4, 5} and tail[2] == "nodes":
            node_id = tail[3]
            if len(node_id) > 256 or not _NODE_ID_RE.fullmatch(node_id):
                raise ApiError(400, "invalid-node-id", "node identity is malformed")
            if len(tail) == 4:
                route = "node"
            elif tail[4] == "neighbors":
                route = "neighbors"
            else:
                raise ApiError(404, "not-found", "API route is unavailable")
        elif tail[2:] == ["stale"]:
            route = "stale"
        elif len(tail) in {4, 5} and tail[2] == "sources":
            document_id = tail[3]
            if not _UUID_RE.fullmatch(document_id):
                raise ApiError(400, "invalid-document-id", "source document identity is malformed")
            if len(tail) == 4:
                route = "source"
            elif tail[4] in {"versions", "diff", "excerpt"}:
                route = tail[4]
            else:
                raise ApiError(404, "not-found", "API route is unavailable")
        else:
            raise ApiError(404, "not-found", "API route is unavailable")
    else:
        raise ApiError(404, "not-found", "API route is unavailable")

    def contextual(error: ApiError) -> ApiError:
        if error.route is None:
            error.route = route
        if error.vault_id is None:
            error.vault_id = vault_id
        return error

    try:
        requested_generation = _generation_header(
            headers, required=route in REQUIRES_GENERATION, route=route
        )
    except ApiError as error:
        raise contextual(error)
    recall_request: Mapping[str, Any] | None = None
    if route in {"search", "context"}:
        if method != "POST":
            raise contextual(
                ApiError(405, "method-not-allowed", "route requires POST")
            )
        try:
            _query_fields(query, set())
        except ApiError as error:
            raise contextual(error)
        content_types = _header_values(headers, "Content-Type")
        if len(content_types) != 1:
            raise contextual(
                ApiError(415, "unsupported-content-type", "request requires JSON")
            )
        parts = [part.strip().casefold() for part in content_types[0].split(";")]
        if not parts or parts[0] != "application/json" or any(
            part != "charset=utf-8" for part in parts[1:]
        ):
            raise contextual(
                ApiError(
                    415,
                    "unsupported-content-type",
                    "request requires UTF-8 JSON",
                )
            )
        if not body or len(body) > MAX_REQUEST_BODY_BYTES:
            raise contextual(
                ApiError(
                    413,
                    "request-body-too-large",
                    "request body is empty or too large",
                )
            )
        try:
            recall_request = _strict_json(body)
        except ApiError as error:
            raise contextual(error)
        try:
            recall_request = validate_contract(dict(recall_request))
        except (ContractError, RecursionError) as error:
            raise contextual(
                ApiError(
                    400,
                    "invalid-recall-request",
                    "recall request violates its contract",
                )
            ) from error
        if (
            recall_request.get("schema") != "qlkg-recall-request-v1"
            or recall_request.get("operation") != route
        ):
            raise contextual(
                ApiError(
                    400,
                    "invalid-recall-request",
                    "recall operation does not match its route",
                )
            )
    else:
        if method != "GET":
            raise contextual(
                ApiError(405, "method-not-allowed", "route requires GET")
            )
        if body:
            raise contextual(
                ApiError(
                    400,
                    "unexpected-request-body",
                    "GET request must not contain a body",
                )
            )
        allowed_queries = {
            "status": set(),
            "vaults": set(),
            "roots": {"limit", "include_stale"},
            "node": {"include_stale"},
            "neighbors": {"limit", "include_stale", "direction", "edge_types"},
            "stale": {"limit", "cursor"},
            "source": set(),
            "versions": {"limit", "before_sequence"},
            "diff": {"from", "to"},
            "excerpt": {"version", "line", "radius"},
        }
        try:
            _query_fields(query, allowed_queries[route])
        except ApiError as error:
            raise contextual(error)
    return _RouteRequest(
        route,
        vault_id,
        node_id,
        document_id,
        query,
        recall_request,
        requested_generation,
    )


def _node_allowed(node: Mapping[str, Any], include_stale: bool) -> bool:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    return (
        provenance.get("active") is not False
        and properties.get("source_status") != "orphaned"
        and (include_stale or properties.get("curation_status") != "needs-review")
    )


def _node_summary(
    federated: FederatedVault, node_id: str
) -> tuple[dict[str, Any], bool]:
    node = federated.view.nodes[node_id]
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    parent_ids = list(islice(federated.index.parents.get(node_id, ()), 513))
    parents = [
        qualified_handle(federated.vault.id, parent)
        for parent in parent_ids[:512]
    ]
    truncated = len(parent_ids) > 512
    return (
        {
            "handle": qualified_handle(federated.vault.id, node_id),
            "vault_id": federated.vault.id,
            "node_id": node_id,
            "type": str(node["type"]),
            "label": str(node.get("label", ""))[:1024],
            "curation_status": str(properties.get("curation_status", "not-applicable")),
            "source_status": str(properties.get("source_status", "not-applicable")),
            "parents": parents,
        },
        truncated,
    )


def _node_detail(federated: FederatedVault, node_id: str) -> tuple[dict[str, Any], bool]:
    node = federated.view.nodes[node_id]
    summary, parent_truncated = _node_summary(federated, node_id)
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    raw_provenance = node.get("provenance")
    raw_provenance = raw_provenance if isinstance(raw_provenance, Mapping) else {}
    authority = raw_provenance.get("authority")
    authority = str(authority) if isinstance(authority, str) and authority else None
    provenance: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    if authority is not None:
        provenance = {
            "authority": authority,
            "line": int(raw_provenance["line"]),
            "definition_start_line": int(raw_provenance["definition_start_line"]),
            "definition_end_line": int(raw_provenance["definition_end_line"]),
            "definition_sha256": str(raw_provenance["definition_sha256"]),
        }
        action = {"kind": "open-authority", "authority": authority, "line": provenance["line"]}
    raw_aliases = [
        str(alias)
        for alias in properties.get("aliases", ())
        if isinstance(alias, str) and alias
    ]
    aliases = sorted({alias for alias in raw_aliases if len(alias) <= 1024})
    truncated = parent_truncated or len(aliases) > 256 or len(aliases) != len(set(raw_aliases))
    aliases = aliases[:256]
    text = str(node.get("text", ""))
    if len(text) > 65_536:
        text = ""
        truncated = True
    return (
        {
            **summary,
            "aliases": aliases,
            "text": text or None,
            "authority": authority,
            "provenance": provenance,
            "open_actions": action,
        },
        truncated,
    )


def _edge_dto(federated: FederatedVault, edge: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": qualified_handle(federated.vault.id, str(edge["source"])),
        "relation": str(edge["relation"]),
        "target": qualified_handle(federated.vault.id, str(edge["target"])),
        "evidence": str(edge["evidence"])[:4096] if edge.get("evidence") else None,
        "curation_status": str(edge.get("curation_status", "not-applicable")),
    }


def _search_node(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "handle": row["handle"],
        "vault_id": row["vault_id"],
        "node_id": row["node_id"],
        "type": row["type"],
        "label": row["label"],
        "curation_status": row["curation_status"],
        "source_status": row["source_status"],
        "parents": copy.deepcopy(row["parents"]),
        "score": row["score"],
        "lane_evidence": copy.deepcopy(row["lane_evidence"]),
    }


def _vault_generation(item: FederatedVault) -> dict[str, Any]:
    return {
        "vault_id": item.vault.id,
        "generation": item.generation,
        "vault_manifest_sha256": sha256_json(item.vault.manifest),
        "graph_manifest_sha256": item.card["graph_manifest_sha256"],
        "graph_sha256": item.card["graph_sha256"],
        "source_ledger_generation_sha256": item.card[
            "source_ledger_generation_sha256"
        ],
        "authority_generation_sha256": item.card["authority_generation_sha256"],
        "live_source_generation_sha256": item.card["live_source_generation_sha256"],
    }


def _envelope(
    snapshot: FederationSnapshot, route: str, result: Mapping[str, Any]
) -> dict[str, Any]:
    payload = {
        "schema": RESPONSE_SCHEMA,
        "route": route,
        "status": "partial" if snapshot.incomplete_vaults else "complete",
        "generation": snapshot.generation,
        "registry_generation": snapshot.registry_generation,
        "vault_generations": [_vault_generation(item) for item in snapshot.vaults],
        "incomplete_vaults": copy.deepcopy(list(snapshot.incomplete_vaults)),
        "result": copy.deepcopy(dict(result)),
    }
    try:
        validated = validate_contract(payload)
        encoded = canonical_json(validated).encode("utf-8")
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ApiError(
            500,
            "invalid-api-response",
            "API response failed its closed contract",
            route=route,
            current_generation=snapshot.generation,
        ) from error
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ApiError(
            507,
            "api-response-too-large",
            "API response exceeds its byte bound",
            route=route,
            current_generation=snapshot.generation,
        )
    return validated


def _lookup_vault(snapshot: FederationSnapshot, vault_id: str, route: str) -> FederatedVault:
    vault = snapshot.by_id.get(vault_id)
    if vault is not None:
        return vault
    if any(row["vault_id"] == vault_id for row in snapshot.incomplete_vaults):
        raise ApiError(
            409,
            "incomplete-vault",
            "Vault is incomplete in the current generation",
            route=route,
            vault_id=vault_id,
            current_generation=snapshot.generation,
            retryable=True,
        )
    raise ApiError(
        404,
        "unknown-vault",
        "Vault is not registered",
        route=route,
        vault_id=vault_id,
        current_generation=snapshot.generation,
    )


def _lookup_node(
    federated: FederatedVault, node_id: str, route: str, *, include_stale: bool
) -> Mapping[str, Any]:
    node = federated.view.nodes.get(node_id)
    if node is None:
        raise ApiError(
            404,
            "unknown-node",
            "node is unavailable in this generation",
            route=route,
            vault_id=federated.vault.id,
        )
    if not _node_allowed(node, include_stale):
        raise ApiError(
            409,
            "stale-node",
            "node is stale in this generation",
            route=route,
            vault_id=federated.vault.id,
        )
    return node


def _source_rows(federated: FederatedVault, document_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any], int]:
    document: Mapping[str, Any] | None = None
    for row in federated.ledger.documents:
        if str(row["document_id"]) == document_id:
            document = row
            break
    if document is None:
        raise ApiError(
            404,
            "unknown-source-document",
            "source document is unavailable in this generation",
            vault_id=federated.vault.id,
        )
    current: Mapping[str, Any] | None = None
    count = 0
    for row in federated.ledger.versions:
        if str(row["document_id"]) != document_id:
            continue
        count += 1
        if str(row["version_id"]) == str(document["current_version_id"]):
            current = row
    if current is None or count < 1:
        raise ApiError(409, "invalid-source-ledger", "source ledger is inconsistent")
    return document, current, count


def _derivation_statuses(
    federated: FederatedVault, version_ids: set[str]
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    priority = {
        "superseded": 0,
        "failed": 1,
        "planned": 2,
        "carried-forward": 3,
        "reviewed-empty": 4,
        "committed": 5,
    }
    for row in federated.ledger.derivations:
        version_id = str(row["version_id"])
        status = str(row["status"])
        if version_id in version_ids and (
            version_id not in statuses
            or priority[status] > priority[statuses[version_id]]
        ):
            statuses[version_id] = status
    return statuses


def _source_detail(
    federated: FederatedVault,
    document: Mapping[str, Any],
    current: Mapping[str, Any],
    version_count: int,
) -> dict[str, Any]:
    return {
        "vault_id": federated.vault.id,
        "document_id": str(document["document_id"]),
        "path": str(document["path"]),
        "format": str(current["format"]),
        "status": str(document["status"]),
        "current_version_id": str(current["version_id"]),
        "normalized_text_sha256": str(current["normalized_text_sha256"]),
        "version_count": version_count,
    }


def _build_stale_index(federated: FederatedVault) -> _StaleIndex:
    """Build one bounded canonical stale projection for a Vault generation."""

    records: list[tuple[str, str, Any]] = []
    work = 0
    weight = 0
    temporary_weight = 0

    def consume_work() -> None:
        nonlocal work
        work += 1
        if work > MAX_STALE_BUILD_WORK:
            raise ApiError(
                507,
                "stale-index-too-large",
                "stale index exceeds its work bound",
            )

    def add(key: str, kind: str, reference: Any) -> None:
        nonlocal weight
        if len(records) >= MAX_STALE_INDEX_ITEMS:
            raise ApiError(507, "stale-index-too-large", "stale index exceeds its item bound")
        if kind == "source":
            reference_weight = len(canonical_json(reference).encode("utf-8")) + 1024
        elif kind == "node":
            reference_weight = sum(
                len(str(value).encode("utf-8")) for value in reference
            ) + 384
        else:
            reference_weight = 256
        record_weight = (
            len(key.encode("utf-8"))
            + len(kind.encode("utf-8"))
            + reference_weight
            + 256
        )
        if weight + temporary_weight + record_weight > MAX_STALE_INDEX_BYTES:
            raise ApiError(507, "stale-index-too-large", "stale index exceeds its byte bound")
        weight += record_weight
        records.append((key, kind, reference))

    for node_id, node in federated.view.nodes.items():
        consume_work()
        properties = node.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        reason = None
        if properties.get("source_status") == "orphaned":
            reason = "orphaned"
        elif properties.get("curation_status") == "needs-review":
            reason = "needs-review"
        elif properties.get("curation_status") == "pending":
            reason = "pending"
        if reason is not None:
            add(
                f"node/{qualified_handle(federated.vault.id, node_id)}",
                "node",
                (node_id, reason),
            )
    for index, edge in enumerate(federated.view.edges):
        consume_work()
        if edge.get("curation_status") != "needs-review":
            continue
        add(
            "edge/{}/{}/{}".format(
                qualified_handle(federated.vault.id, str(edge["source"])),
                edge["relation"],
                qualified_handle(federated.vault.id, str(edge["target"])),
            ),
            "edge",
            index,
        )

    source_states: dict[str, list[Any]] = {}
    for row in federated.ledger.documents:
        consume_work()
        document_id = str(row["document_id"])
        state_weight = (
            len(document_id.encode("utf-8"))
            + len(str(row["current_version_id"]).encode("utf-8"))
            + 512
        )
        if weight + temporary_weight + state_weight > MAX_STALE_INDEX_BYTES:
            raise ApiError(
                507,
                "stale-index-too-large",
                "stale index exceeds its byte bound",
            )
        temporary_weight += state_weight
        source_states[document_id] = [row, None, 0]
    for version in federated.ledger.versions:
        consume_work()
        document_id = str(version["document_id"])
        state = source_states.get(document_id)
        if state is None:
            continue
        state[2] = int(state[2]) + 1
        if str(version["version_id"]) == str(state[0]["current_version_id"]):
            state[1] = version
    for document_id, state in source_states.items():
        document, current, count = state
        if document["status"] not in {"stale", "failed"}:
            continue
        if current is None:
            raise ApiError(409, "invalid-source-ledger", "source ledger is inconsistent")
        add(
            f"source/{federated.vault.id}/{document_id}",
            "source",
            _source_detail(federated, document, current, int(count)),
        )
    source_states.clear()
    temporary_weight = 0
    records.sort(key=lambda item: item[0])
    keys = tuple(item[0] for item in records)
    if any(left == right for left, right in zip(keys, keys[1:])):
        raise ApiError(409, "invalid-stale-index", "stale identities are not unique")
    return _StaleIndex(tuple(records), keys, weight)


def _edge_selection(
    federated: FederatedVault,
    node_id: str,
    *,
    direction: str,
    relations: set[str],
    include_stale: bool,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    candidates: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    work = 0
    sequences: list[Iterable[Mapping[str, Any]]] = []
    if direction in {"outgoing", "both"}:
        sequences.append(federated.view.outgoing.get(node_id, ()))
    if direction in {"incoming", "both"}:
        sequences.append(federated.view.incoming.get(node_id, ()))
    work_truncated = False
    for sequence in sequences:
        for edge in sequence:
            work += 1
            if work > MAX_NEIGHBOR_WORK:
                work_truncated = True
                break
            if str(edge["relation"]) not in relations:
                continue
            if not include_stale and edge.get("curation_status") == "needs-review":
                continue
            if not include_stale and any(
                endpoint not in federated.view.nodes
                or not _node_allowed(federated.view.nodes[endpoint], False)
                for endpoint in (str(edge["source"]), str(edge["target"]))
            ):
                continue
            key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
            if len(candidates) < limit + 1:
                candidates[key] = edge
        if work_truncated:
            break
    ordered = sorted(candidates.items())
    overflow = work_truncated or len(ordered) > limit
    ordered = ordered[:limit]
    node_ids = sorted(
        {
            endpoint
            for key, _ in ordered
            for endpoint in (key[0], key[2])
            if endpoint != node_id
        }
    )
    return [_edge_dto(federated, edge) for _, edge in ordered], node_ids, overflow


def _recall_result(report: Mapping[str, Any], route: str) -> dict[str, Any]:
    result = report["result"]
    return {
        "kind": route,
        "query": result["query"],
        "resolutions": copy.deepcopy(result["resolutions"]),
        "nodes": [_search_node(row) for row in result["nodes"]],
        "edges": [
            {
                key: row[key]
                for key in ("source", "relation", "target", "evidence", "curation_status")
            }
            for row in result["edges"]
        ],
        "evidence": copy.deepcopy(result["evidence"]),
        "omissions": copy.deepcopy(result["omissions"]),
        "truncated": bool(result["truncated"]),
    }


class ApiService:
    """Pure request dispatcher backed by one coherent capture per request."""

    def __init__(
        self,
        *,
        home: Path | str | None = None,
        cache: FederationSnapshotCache | None = None,
        static_assets: StaticAssetProvider | None = None,
    ) -> None:
        self.cache = cache or FederationSnapshotCache(home=home)
        self.static_assets = static_assets
        self._stale_lock = threading.Lock()
        self._stale_ready_key: tuple[str, str] | None = None
        self._stale_ready: _StaleIndex | None = None
        self._stale_flight: _StaleFlight | None = None

    def _stale_index(
        self, snapshot: FederationSnapshot, federated: FederatedVault
    ) -> _StaleIndex:
        key = (federated.vault.id, federated.generation)
        while True:
            with self._stale_lock:
                if self._stale_ready_key == key and self._stale_ready is not None:
                    return self._stale_ready
                flight = self._stale_flight
                if flight is None:
                    flight = _StaleFlight(key)
                    self._stale_flight = flight
                    leader = True
                else:
                    leader = False
            if not leader:
                if not flight.done.wait(MAX_CAPTURE_WAIT_SECONDS):
                    raise ApiError(
                        503,
                        "stale-index-timeout",
                        "stale index did not complete within its wait bound",
                        route="stale",
                        vault_id=federated.vault.id,
                        retryable=True,
                    )
                if flight.key != key:
                    continue
                if flight.error is not None:
                    raise flight.error
                assert flight.index is not None
                return flight.index
            try:
                index = _build_stale_index(federated)
            except Exception as error:
                with self._stale_lock:
                    flight.error = error
                    flight.done.set()
                    if self._stale_flight is flight:
                        self._stale_flight = None
                raise
            with self._stale_lock:
                ready = self.cache.ready
                current = (
                    ready.by_id.get(federated.vault.id)
                    if ready is not None
                    else None
                )
                if current is not None and current.generation == federated.generation:
                    self._stale_ready_key = key
                    self._stale_ready = index
                flight.index = index
                flight.done.set()
                if self._stale_flight is flight:
                    self._stale_flight = None
            return index

    def _bind_derived_cache(self, snapshot: FederationSnapshot) -> None:
        """Drop derived bytes whenever the cache installs another READY object."""

        with self._stale_lock:
            if self._stale_ready_key is not None:
                vault_id, generation = self._stale_ready_key
                current = snapshot.by_id.get(vault_id)
                if current is None or current.generation != generation:
                    self._stale_ready_key = None
                    self._stale_ready = None

    def _drop_derived_cache(self) -> None:
        with self._stale_lock:
            self._stale_ready_key = None
            self._stale_ready = None

    def _produce(self, request: _RouteRequest, snapshot: FederationSnapshot) -> dict[str, Any]:
        route = request.route
        if route == "status":
            healthy = len(snapshot.vaults)
            incomplete = len(snapshot.incomplete_vaults)
            return {
                "kind": "status",
                "api_version": 1,
                "read_only": True,
                "registered_vaults": healthy + incomplete,
                "healthy_vaults": healthy,
                "incomplete_vaults": incomplete,
            }
        if route == "vaults":
            cards = []
            for item in snapshot.vaults:
                generation = _vault_generation(item)
                cards.append(
                    {
                        **generation,
                        "label": item.card["label"],
                        "health": item.card["health"],
                        "counts": copy.deepcopy(item.card["counts"]),
                        "source_freshness": copy.deepcopy(item.card["source_freshness"]),
                    }
                )
            return {"kind": "vaults", "vaults": cards}

        assert request.vault_id is not None or route in {"search", "context"}
        if route in {"search", "context"}:
            assert request.recall_request is not None
            try:
                report = execute_recall_request(request.recall_request, snapshot=snapshot)
            except RecallError as error:
                raise ApiError(
                    400,
                    error.code,
                    "recall request could not be completed",
                    route=route,
                    vault_id=error.vault_id,
                    current_generation=snapshot.generation,
                    retryable=error.code in {"incomplete-vault", "stale-native-graph"},
                ) from error
            return _recall_result(report, route)

        federated = _lookup_vault(snapshot, request.vault_id, route)
        query = request.query
        if route == "roots":
            limit = _query_int(query, "limit", 100, 1, 500)
            include_stale = _query_bool(query, "include_stale")
            rows: list[dict[str, Any]] = []
            parent_truncated = False
            for node_id in federated.index.roots:
                if not _node_allowed(federated.view.nodes[node_id], include_stale):
                    continue
                row, row_truncated = _node_summary(federated, node_id)
                rows.append(row)
                parent_truncated = parent_truncated or row_truncated
                if len(rows) > limit:
                    break
            overflow = len(rows) > limit or parent_truncated
            rows = rows[:limit]
            return {
                "kind": "roots",
                "nodes": rows,
                "omissions": (
                    [{"kind": "node", "id": "roots", "reason": "limit"}] if overflow else []
                ),
                "truncated": overflow,
            }
        if route == "node":
            assert request.node_id is not None
            include_stale = _query_bool(query, "include_stale")
            _lookup_node(federated, request.node_id, route, include_stale=include_stale)
            detail, detail_truncated = _node_detail(federated, request.node_id)
            edges, _, edge_truncated = _edge_selection(
                federated,
                request.node_id,
                direction="both",
                relations=DEFAULT_RELATIONS,
                include_stale=include_stale,
                limit=5000,
            )
            handle = qualified_handle(federated.vault.id, request.node_id)
            context_request = {
                "schema": "qlkg-recall-request-v1",
                "operation": "context",
                "vault_ids": [],
                "queries": [],
                "query": None,
                "handle": None,
                "handles": [handle],
                "scopes": [],
                "direction": "both",
                "edge_types": [],
                "max_depth": 1,
                "limit": 1,
                "token_budget": 65_536,
                "include_stale": include_stale,
            }
            try:
                context = execute_recall_request(context_request, snapshot=snapshot)["result"]
                evidence = copy.deepcopy(context["evidence"][:500])
                omissions = copy.deepcopy(context["omissions"])
                context_truncated = bool(context["truncated"] or len(context["evidence"]) > 500)
            except RecallError:
                evidence = []
                omissions = [{"kind": "evidence", "id": handle, "reason": "incomplete-vault"}]
                context_truncated = True
            if detail_truncated:
                omissions.append({"kind": "node", "id": handle, "reason": "limit"})
            if edge_truncated:
                omissions.append({"kind": "edge", "id": handle, "reason": "limit"})
            return {
                "kind": "node",
                "node": detail,
                "edges": edges,
                "evidence": evidence,
                "omissions": omissions[:500],
                "truncated": bool(detail_truncated or edge_truncated or context_truncated),
            }
        if route == "neighbors":
            assert request.node_id is not None
            include_stale = _query_bool(query, "include_stale")
            _lookup_node(federated, request.node_id, route, include_stale=include_stale)
            limit = _query_int(query, "limit", 100, 1, 500)
            direction = query.get("direction", "both")
            if direction not in {"incoming", "outgoing", "both"}:
                raise ApiError(400, "invalid-query", "neighbor direction is invalid", route=route)
            raw_relations = query.get("edge_types", "")
            relations = set(raw_relations.split(",")) if raw_relations else set(DEFAULT_RELATIONS)
            if not relations or not relations <= DEFAULT_RELATIONS:
                raise ApiError(400, "invalid-query", "neighbor edge types are invalid", route=route)
            edges, node_ids, edge_overflow = _edge_selection(
                federated,
                request.node_id,
                direction=direction,
                relations=relations,
                include_stale=include_stale,
                limit=min(5000, max(limit, 1) * 16),
            )
            node_rows: list[dict[str, Any]] = []
            parent_omissions: list[dict[str, Any]] = []
            for node_id in node_ids:
                if not _node_allowed(federated.view.nodes[node_id], include_stale):
                    continue
                row, row_truncated = _node_summary(federated, node_id)
                node_rows.append(row)
                if row_truncated:
                    parent_omissions.append(
                        {"kind": "node", "id": row["handle"], "reason": "limit"}
                    )
            node_overflow = len(node_rows) > limit
            if node_overflow:
                node_rows = node_rows[:limit]
            center = qualified_handle(federated.vault.id, request.node_id)
            omissions = parent_omissions
            if node_overflow:
                omissions.append(
                    {"kind": "node", "id": f"{center}/expanded-nodes", "reason": "limit"}
                )
            if edge_overflow:
                omissions.append({"kind": "edge", "id": center, "reason": "limit"})
            return {
                "kind": "neighbors",
                "center": center,
                "nodes": node_rows,
                "edges": edges,
                "omissions": omissions[:500],
                "truncated": bool(edge_overflow or node_overflow or parent_omissions),
            }
        if route == "stale":
            limit = _query_int(query, "limit", 100, 1, 200)
            cursor = query.get("cursor")
            if cursor is not None and (not cursor or len(cursor) > 4096):
                raise ApiError(400, "invalid-query", "stale cursor is malformed", route=route)
            index = self._stale_index(snapshot, federated)
            start = bisect.bisect_right(index.keys, cursor) if cursor is not None else 0
            selected = index.records[start : start + limit + 1]
            overflow = len(selected) > limit
            selected = selected[:limit]
            items: list[dict[str, Any]] = []
            parent_truncated = False
            for _, kind, reference in selected:
                if kind == "node":
                    node_id, reason = reference
                    summary, row_truncated = _node_summary(federated, str(node_id))
                    parent_truncated = parent_truncated or row_truncated
                    items.append({"kind": "node", "node": summary, "reason": reason})
                elif kind == "edge":
                    items.append(
                        {
                            "kind": "edge",
                            "edge": _edge_dto(federated, federated.view.edges[int(reference)]),
                            "reason": "needs-review",
                        }
                    )
                else:
                    detail = copy.deepcopy(reference)
                    items.append(
                        {"kind": "source", "source": detail, "reason": detail["status"]}
                    )
            overflow = overflow or parent_truncated
            next_cursor = selected[-1][0] if overflow and selected else None
            return {
                "kind": "stale",
                "items": items,
                "next_cursor": next_cursor,
                "omissions": (
                    [{"kind": "vault", "id": federated.vault.id, "reason": "limit"}]
                    if parent_truncated else []
                ),
                "truncated": overflow,
            }

        assert request.document_id is not None
        document, current, version_count = _source_rows(federated, request.document_id)
        if route == "source":
            return {
                "kind": "source",
                "source": _source_detail(federated, document, current, version_count),
            }
        if route == "versions":
            limit = _query_int(query, "limit", 50, 1, 200)
            before = _query_int(query, "before_sequence", 99_999_999, 1, 99_999_999)
            selected: list[Mapping[str, Any]] = []
            work = 0
            for version in reversed(federated.ledger.versions):
                work += 1
                if work > MAX_SOURCE_SCAN:
                    raise ApiError(507, "source-history-too-large", "source history exceeds its work bound", route=route)
                if str(version["document_id"]) != request.document_id or int(version["sequence"]) >= before:
                    continue
                selected.append(version)
                if len(selected) > limit:
                    break
            overflow = len(selected) > limit
            selected = selected[:limit]
            statuses = _derivation_statuses(
                federated, {str(version["version_id"]) for version in selected}
            )
            versions = [
                {
                    "version_id": str(version["version_id"]),
                    "sequence": int(version["sequence"]),
                    "captured_at": str(version["captured_at"]),
                    "captured_path": str(version["captured_path"]),
                    "format": str(version["format"]),
                    "predecessor_version_id": version["predecessor_version_id"],
                    "raw_sha256": str(version["raw_sha256"]),
                    "normalized_text_sha256": str(version["normalized_text_sha256"]),
                    "byte_count": int(version["byte_count"]),
                    "derivation_status": statuses.get(str(version["version_id"])),
                }
                for version in selected
            ]
            return {
                "kind": "versions",
                "document_id": request.document_id,
                "versions": versions,
                "next_before_sequence": versions[-1]["sequence"] if overflow and versions else None,
                "truncated": overflow,
            }
        if route == "diff":
            from_id = query.get("from") or None
            to_id = query.get("to") or None
            for value in (from_id, to_id):
                if value is not None and not re.fullmatch(
                    rf"doc:{re.escape(request.document_id)}:v[0-9]{{8}}", value
                ):
                    raise ApiError(400, "invalid-source-version", "source version identity is malformed", route=route)
            try:
                diff = verified_version_diff(
                    federated.ledger,
                    document_id=request.document_id,
                    from_version_id=from_id,
                    to_version_id=to_id,
                )
            except SourceArchiveError as error:
                error_status = 413 if error.code == "source-diff-too-large" else 409
                raise ApiError(
                    error_status,
                    error.code,
                    "source diff is unavailable",
                    route=route,
                    vault_id=federated.vault.id,
                    current_generation=snapshot.generation,
                ) from error
            return {
                "kind": "diff",
                **{
                    field: diff[field]
                    for field in (
                        "document_id",
                        "path",
                        "from_version_id",
                        "to_version_id",
                        "semantic_changed",
                        "text",
                        "truncated",
                        "emitted_lines",
                        "max_bytes",
                        "max_lines",
                    )
                },
            }
        assert route == "excerpt"
        version_id = query.get("version") or str(current["version_id"])
        if not re.fullmatch(rf"doc:{re.escape(request.document_id)}:v[0-9]{{8}}", version_id):
            raise ApiError(400, "invalid-source-version", "source version identity is malformed", route=route)
        selected_version = None
        for version in federated.ledger.versions:
            if str(version["version_id"]) == version_id and str(version["document_id"]) == request.document_id:
                selected_version = version
                break
        if selected_version is None:
            raise ApiError(404, "unknown-source-version", "source version is unavailable", route=route)
        focus = _query_int(query, "line", 1, 1, 100_000_000)
        radius = _query_int(query, "radius", 8, 0, 50)
        try:
            text = verified_version_text(federated.ledger, selected_version)
        except SourceArchiveError as error:
            raise ApiError(409, error.code, "source excerpt is unavailable", route=route) from error
        if not text:
            if focus != 1:
                raise ApiError(
                    400,
                    "invalid-source-line",
                    "source line is outside the selected version",
                    route=route,
                    vault_id=federated.vault.id,
                )
            return {
                "kind": "excerpt",
                "document_id": request.document_id,
                "version_id": version_id,
                "path": str(selected_version["captured_path"]),
                "line": 1,
                "start": 1,
                "end": 0,
                "lines": [],
                "excerpt_sha256": hashlib.sha256(b"").hexdigest(),
                "truncated": False,
            }
        start_wanted = max(1, focus - radius)
        end_wanted = focus + radius
        window: list[dict[str, Any]] = []
        offset = 0
        number = 1
        truncated = False
        while offset <= len(text) and number <= end_wanted:
            newline = text.find("\n", offset)
            if newline < 0:
                newline = len(text)
            if number >= start_wanted:
                raw_characters = newline - offset
                value = text[offset : min(newline, offset + MAX_EXCERPT_LINE_CHARACTERS)]
                if raw_characters > MAX_EXCERPT_LINE_CHARACTERS:
                    truncated = True
                window.append({"number": number, "text": value})
            if newline == len(text):
                break
            offset = newline + 1
            number += 1
        focus_index = next(
            (index for index, row in enumerate(window) if row["number"] == focus),
            None,
        )
        if focus_index is None:
            raise ApiError(400, "invalid-source-line", "source line is outside the selected version", route=route)
        selected = [window[focus_index]]
        used = len(str(window[focus_index]["text"]).encode("utf-8"))
        for row in reversed(window[:focus_index]):
            row_bytes = len(str(row["text"]).encode("utf-8")) + 1
            if used + row_bytes > MAX_EXCERPT_BYTES:
                truncated = True
                break
            selected.append(row)
            used += row_bytes
        for row in window[focus_index + 1 :]:
            row_bytes = len(str(row["text"]).encode("utf-8")) + 1
            if used + row_bytes > MAX_EXCERPT_BYTES:
                truncated = True
                break
            selected.append(row)
            used += row_bytes
        lines = sorted(selected, key=lambda row: int(row["number"]))
        if len(lines) != len(window):
            truncated = True
        excerpt = "\n".join(str(row["text"]) for row in lines)
        return {
            "kind": "excerpt",
            "document_id": request.document_id,
            "version_id": version_id,
            "path": str(selected_version["captured_path"]),
            "line": focus,
            "start": lines[0]["number"],
            "end": lines[-1]["number"],
            "lines": lines,
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "truncated": truncated,
        }

    def dispatch(
        self,
        method: str,
        target: str,
        *,
        headers: Sequence[tuple[str, str]] = (),
        body: bytes = b"",
    ) -> ApiHttpResponse:
        route: str | None = None
        request: _RouteRequest | None = None
        snapshot: FederationSnapshot | None = None
        try:
            request = _parse_route(method.upper(), target, headers, body)
            route = request.route
            validators = _header_values(headers, "If-None-Match")
            if len(validators) > 1 or (
                validators and not re.fullmatch(r'"[0-9a-f]{64}"', validators[0])
            ):
                raise ApiError(
                    400,
                    "invalid-cache-validator",
                    "cache validator is malformed",
                    route=route,
                )
            requested_generation = request.requested_generation
            try:
                snapshot = self.cache.acquire()
            except (FederationError, OSError, UnicodeError, ValueError, RecursionError) as error:
                self._drop_derived_cache()
                raise ApiError(
                    503,
                    "federation-unavailable",
                    "federation capture is unavailable",
                    route=route,
                    retryable=True,
                ) from error
            self._bind_derived_cache(snapshot)
            if (
                route in REQUIRES_GENERATION
                and requested_generation != snapshot.generation
            ):
                raise ApiError(
                    409,
                    "stale-generation",
                    "request generation is stale",
                    route=route,
                    vault_id=request.vault_id,
                    current_generation=snapshot.generation,
                    retryable=True,
                )
            result = self._produce(request, snapshot)
            payload = _envelope(snapshot, route, result)
            encoded = canonical_json(payload).encode("utf-8")
            etag = f'"{hashlib.sha256(encoded).hexdigest()}"'
            response_headers = {
                "Content-Type": "application/json; charset=utf-8",
                GENERATION_HEADER: snapshot.generation,
                "ETag": etag,
            }
            if validators and validators[0] == etag:
                return ApiHttpResponse(
                    304,
                    b"",
                    {GENERATION_HEADER: snapshot.generation, "ETag": etag},
                )
            return ApiHttpResponse(
                200,
                encoded,
                response_headers,
            )
        except ApiError as error:
            if error.route is None:
                error.route = route
            if error.vault_id is None and request is not None:
                error.vault_id = request.vault_id
            if error.current_generation is None and snapshot is not None:
                error.current_generation = snapshot.generation
            payload = error.payload()
            encoded = canonical_json(payload).encode("utf-8")
            response_headers = {"Content-Type": "application/json; charset=utf-8"}
            if error.current_generation is not None:
                response_headers[GENERATION_HEADER] = error.current_generation
            return ApiHttpResponse(error.status, encoded, response_headers)
        except (ContractError, SourceArchiveError, RecallError, OSError, UnicodeError, ValueError, RecursionError):
            error = ApiError(500, "api-operation-failed", "API operation failed", route=route)
            return ApiHttpResponse(
                500,
                canonical_json(error.payload()).encode("utf-8"),
                {"Content-Type": "application/json; charset=utf-8"},
            )


def create_api_server(
    *,
    home: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    service: ApiService | None = None,
) -> ThreadingHTTPServer:
    """Create the versioned API server without starting its event loop."""

    configured_host = host.strip().strip("[]").casefold()
    try:
        loopback = ipaddress.ip_address(configured_host).is_loopback
    except ValueError:
        loopback = configured_host == "localhost"
    if not loopback:
        raise ValueError("versioned API host must be loopback")
    allowed_hostnames = _allowed_hostnames(host)
    api_service = service or ApiService(home=home)
    class BoundedThreadingHTTPServer(ThreadingHTTPServer):
        request_queue_size = MAX_HTTP_LISTEN_BACKLOG

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._active_requests = threading.BoundedSemaphore(
                MAX_ACTIVE_HTTP_REQUESTS
            )
            super().__init__(*args, **kwargs)

        @staticmethod
        def _capacity_response() -> bytes:
            error = ApiError(
                503,
                "api-capacity-exhausted",
                "API request capacity is exhausted",
                retryable=True,
            )
            body = canonical_json(error.payload()).encode("utf-8")
            return (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Cache-Control: no-store\r\n"
                b"Connection: close\r\n"
                b"X-Content-Type-Options: nosniff\r\n\r\n"
                + body
            )

        def process_request(self, request: Any, client_address: Any) -> None:
            """Acquire capacity before ThreadingMixIn creates a handler thread."""

            if not self._active_requests.acquire(blocking=False):
                try:
                    request.settimeout(MAX_HTTP_READ_SECONDS)
                    request.sendall(self._capacity_response())
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._active_requests.release()
                self.shutdown_request(request)
                raise

        def process_request_thread(self, request: Any, client_address: Any) -> None:
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._active_requests.release()

    class Handler(BaseHTTPRequestHandler):
        server_version = "kgdistiller-api/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(MAX_HTTP_READ_SECONDS)

        def _send(self, response: ApiHttpResponse) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(response.body)
                except OSError:
                    self.close_connection = True

        def _send_closing(self, response: ApiHttpResponse) -> None:
            self.close_connection = True
            self._send(
                ApiHttpResponse(
                    response.status,
                    response.body,
                    {**response.headers, "Connection": "close"},
                )
            )

        def _security_error(self) -> ApiHttpResponse | None:
            hosts = self.headers.get_all("Host", failobj=[])
            authority = (
                _request_authority(hosts[0], self.server.server_port)
                if len(hosts) == 1
                else None
            )
            if authority is None or authority[0] not in allowed_hostnames:
                error = ApiError(421, "misdirected-host", "request host is not allowed")
                return ApiHttpResponse(
                    error.status,
                    canonical_json(error.payload()).encode("utf-8"),
                    {"Content-Type": "application/json; charset=utf-8"},
                )
            origins = self.headers.get_all("Origin", failobj=[])
            if len(origins) > 1 or (
                origins and _origin_authority(origins[0], self.server.server_port) != authority
            ):
                error = ApiError(403, "forbidden-origin", "request origin is not allowed")
                return ApiHttpResponse(
                    error.status,
                    canonical_json(error.payload()).encode("utf-8"),
                    {"Content-Type": "application/json; charset=utf-8"},
                )
            return None

        def _handle_active(self) -> None:
            security = self._security_error()
            if security is not None:
                self._send_closing(security)
                return
            transfer = self.headers.get_all("Transfer-Encoding", failobj=[])
            lengths = self.headers.get_all("Content-Length", failobj=[])
            if transfer or len(lengths) > 1:
                error = ApiError(400, "invalid-request-framing", "request framing is unsupported")
                self._send_closing(
                    ApiHttpResponse(
                        error.status,
                        canonical_json(error.payload()).encode("utf-8"),
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                )
                return
            length = 0
            if lengths:
                if (
                    len(lengths[0]) > 12
                    or not lengths[0].isascii()
                    or not lengths[0].isdigit()
                    or (len(lengths[0]) > 1 and lengths[0].startswith("0"))
                ):
                    length = MAX_REQUEST_BODY_BYTES + 1
                else:
                    length = int(lengths[0])
            if length > MAX_REQUEST_BODY_BYTES:
                error = ApiError(413, "request-body-too-large", "request body exceeds its byte limit")
                self._send_closing(
                    ApiHttpResponse(
                        error.status,
                        canonical_json(error.payload()).encode("utf-8"),
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                )
                return
            try:
                body = self.rfile.read(length) if length else b""
            except OSError:
                error = ApiError(408, "request-body-timeout", "request body was not received in time")
                self._send_closing(
                    ApiHttpResponse(
                        error.status,
                        canonical_json(error.payload()).encode("utf-8"),
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                )
                return
            if len(body) != length:
                error = ApiError(400, "incomplete-request-body", "request body is incomplete")
                self._send_closing(
                    ApiHttpResponse(
                        error.status,
                        canonical_json(error.payload()).encode("utf-8"),
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                )
                return
            response = api_service.dispatch(
                self.command,
                self.path,
                headers=list(self.headers.items()),
                body=body,
            )
            self._send(response)

        def _handle(self) -> None:
            self._handle_active()

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_TRACE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_CONNECT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            if code == 501:
                error = ApiError(405, "method-not-allowed", "HTTP method is not allowed")
            else:
                error = ApiError(
                    400 if code < 500 else 500,
                    "invalid-http-request",
                    "HTTP request is invalid",
                )
            self._send_closing(
                ApiHttpResponse(
                    error.status,
                    canonical_json(error.payload()).encode("utf-8"),
                    {"Content-Type": "application/json; charset=utf-8"},
                )
            )

        def log_message(self, format: str, *args: Any) -> None:
            return

    return BoundedThreadingHTTPServer((host, port), Handler)


def serve_api(
    *,
    home: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the versioned API until interrupted."""

    server = create_api_server(home=home, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = [
    "ApiError",
    "ApiHttpResponse",
    "ApiService",
    "FederationSnapshotCache",
    "StaticAsset",
    "StaticAssetProvider",
    "create_api_server",
    "serve_api",
]
