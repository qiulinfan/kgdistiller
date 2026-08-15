"""Transactional, review-gated ingestion for kgdistiller projects."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from . import __version__
from .query import COMPARISON_SCHEMA, PROPOSAL_SCHEMA, validate_agent_snapshot
from .alignment import (
    ALIGNMENT_REPORT_SCHEMA,
    AlignmentError,
    load_alignment_set,
    sha256_json,
)
from .cli import (
    DELTA_SCHEMA,
    GraphState,
    KnowledgeError,
    atomic_write,
    curation_report,
    build_identity_index,
    json_text,
    load_sources,
    load_identity_registry,
    load_state,
    make_agent_snapshot,
    pretty_json,
    read_json,
    reconcile_alignment_mapping,
    relative_path,
    scan_scope,
    select_scope,
    sha256_authority_file,
    sha256_text,
    synchronize,
    unique_source_for_path,
    apply_delta,
)
from .json_schema import validate_json_schema


REQUEST_SCHEMA = "qlkg-ingest-request-v2"
PLAN_SCHEMA = "qlkg-ingest-plan-v1"
RECEIPT_SCHEMA = "qlkg-ingest-receipt-v2"
ERROR_SCHEMA = "qlkg-ingest-error-v1"
CAPABILITY = "transactional-ingest-v1"
JOURNAL_SCHEMA = "qlkg-ingest-journal-v1"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_PATCHES = 128
MAX_DECISIONS = 4096
MAX_ALIGNMENTS = 1024
MAX_EVIDENCE_ITEMS = 4096
MAX_TEXT_LENGTH = 16 * 1024
MAX_JOURNAL_TARGETS = MAX_PATCHES + 3
MAX_JOURNAL_PATH_LENGTH = 4096
FailureInjector = Callable[[str], None]


class IngestError(KnowledgeError):
    """A stable, machine-readable transactional ingestion failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "validation",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.diagnostics = diagnostics or []

    def payload(self) -> dict[str, Any]:
        return {
            "schema": ERROR_SCHEMA,
            "error": {
                "code": self.code,
                "message": str(self),
                "stage": self.stage,
                "diagnostics": self.diagnostics,
            },
        }


@dataclass(frozen=True)
class IngestPaths:
    repo_root: Path
    registry: Path
    graph_dir: Path
    identities: Path
    alignments: Path
    typst_registry: Path


@dataclass
class StagedIngest:
    root: Path
    paths: IngestPaths
    request: dict[str, Any]
    request_sha256: str
    candidate: dict[str, Any]
    query_report: dict[str, Any]
    before_state: GraphState
    after_state: GraphState
    before_alignment_sha256: str
    after_alignment_sha256: str
    before_source_hashes: dict[str, str | None]
    after_source_hashes: dict[str, str | None]
    sync_report: dict[str, Any]
    delta_report: dict[str, Any]
    curation: dict[str, Any]
    validations: list[dict[str, Any]]
    durations_ms: dict[str, int]


@dataclass(frozen=True)
class _JournalTarget:
    relative: str
    target: Path
    backup: Path
    existed: bool
    kind: str


@dataclass(frozen=True)
class _ValidatedJournal:
    request_sha256: str
    status: str
    backup_root: Path
    targets: tuple[_JournalTarget, ...]


def canonical_digest(payload: dict[str, Any], field: str) -> str:
    value = copy.deepcopy(payload)
    value.pop(field, None)
    return sha256_text(json_text(value))


def _validate_json_schema(payload: Any, filename: str, code: str) -> None:
    schema = json.loads(
        resources.files("kgdistiller")
        .joinpath("schemas", filename)
        .read_text(encoding="utf-8")
    )
    errors = validate_json_schema(payload, schema)
    if not errors:
        return
    diagnostics = [
        {
            "path": ".".join(str(item) for item in error.path),
            "message": error.message,
        }
        for error in errors[:32]
    ]
    raise IngestError(
        code,
        f"JSON Schema validation failed with {len(errors)} error(s)",
        diagnostics=diagnostics,
    )


def finalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a request with its canonical request digest populated."""
    result = copy.deepcopy(payload)
    result["request_sha256"] = canonical_digest(result, "request_sha256")
    return result


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IngestError("invalid-request", f"{field} must be an object")
    return value


def _require_array(value: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise IngestError("invalid-request", f"{field} must be an array")
    if len(value) > maximum:
        raise IngestError(
            "request-too-large", f"{field} exceeds the limit of {maximum} items"
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise IngestError(
            "invalid-request", f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _require_sha256(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    rendered = str(value)
    if not HEX_SHA256_RE.fullmatch(rendered):
        raise IngestError("invalid-request", f"{field} must be a lowercase SHA-256")
    return rendered


def _bounded_text(value: Any, field: str, *, required: bool = True) -> str:
    rendered = str(value or "")
    if required and not rendered.strip():
        raise IngestError("invalid-request", f"{field} must not be empty")
    if len(rendered.encode("utf-8")) > MAX_TEXT_LENGTH:
        raise IngestError("request-too-large", f"{field} exceeds {MAX_TEXT_LENGTH} bytes")
    return rendered


def validate_request(payload: Any, *, mode: str | None = None) -> dict[str, Any]:
    _validate_json_schema(
        payload, f"{REQUEST_SCHEMA}.schema.json", "invalid-request"
    )
    request = _require_object(payload, "request")
    _reject_unknown(
        request,
        {
            "schema",
            "request_id",
            "request_sha256",
            "mode",
            "capabilities",
            "base_graph_sha256",
            "base_alignment_sha256",
            "candidate_snapshot",
            "query_report",
            "authority_patches",
            "decisions",
            "delta",
            "alignment_decisions",
            "review",
        },
        "request",
    )
    required = {
        "schema",
        "request_id",
        "request_sha256",
        "mode",
        "capabilities",
        "base_graph_sha256",
        "base_alignment_sha256",
        "candidate_snapshot",
        "query_report",
        "authority_patches",
        "decisions",
        "delta",
        "alignment_decisions",
        "review",
    }
    missing = sorted(required - set(request))
    if missing:
        raise IngestError("invalid-request", f"request is missing: {', '.join(missing)}")
    if request["schema"] != REQUEST_SCHEMA:
        raise IngestError(
            "unsupported-schema", f"expected {REQUEST_SCHEMA}, got {request['schema']!r}"
        )
    request_id = str(request["request_id"])
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise IngestError("invalid-request", f"invalid request_id: {request_id!r}")
    request_mode = str(request["mode"])
    if request_mode not in {"plan", "apply"}:
        raise IngestError("invalid-request", "mode must be plan or apply")
    if mode is not None and request_mode != mode:
        raise IngestError(
            "mode-mismatch", f"request mode {request_mode!r} does not match {mode!r}"
        )
    capabilities = _require_array(request["capabilities"], "capabilities", 32)
    if CAPABILITY not in {str(item) for item in capabilities}:
        raise IngestError(
            "unsupported-capability", f"request must require {CAPABILITY}"
        )
    _require_sha256(request["base_graph_sha256"], "base_graph_sha256")
    _require_sha256(request["base_alignment_sha256"], "base_alignment_sha256")
    _validate_artifact_reference(request["candidate_snapshot"], "candidate_snapshot")
    _validate_artifact_reference(request["query_report"], "query_report")
    patches = _require_array(request["authority_patches"], "authority_patches", MAX_PATCHES)
    seen_paths: set[str] = set()
    for index, raw in enumerate(patches):
        patch = _require_object(raw, f"authority_patches[{index}]")
        _reject_unknown(
            patch,
            {
                "path",
                "operation",
                "expected_sha256",
                "content",
                "content_sha256",
                "expected_markers",
            },
            f"authority_patches[{index}]",
        )
        for field in ("path", "operation", "expected_sha256", "expected_markers"):
            if field not in patch:
                raise IngestError(
                    "invalid-request", f"authority_patches[{index}] is missing {field}"
                )
        path = _bounded_text(patch["path"], f"authority_patches[{index}].path")
        if path in seen_paths:
            raise IngestError("invalid-request", f"duplicate authority patch: {path}")
        seen_paths.add(path)
        operation = str(patch["operation"])
        if operation not in {"write", "delete"}:
            raise IngestError("invalid-request", f"invalid patch operation: {operation!r}")
        _require_sha256(
            patch["expected_sha256"],
            f"authority_patches[{index}].expected_sha256",
            nullable=True,
        )
        markers = _require_object(
            patch["expected_markers"], f"authority_patches[{index}].expected_markers"
        )
        _reject_unknown(markers, {"definitions", "references"}, "expected_markers")
        for marker_kind in ("definitions", "references"):
            values = _require_array(
                markers.get(marker_kind),
                f"authority_patches[{index}].expected_markers.{marker_kind}",
                4096,
            )
            for item in values:
                _bounded_text(item, f"expected_markers.{marker_kind}")
        if operation == "write":
            if "content" not in patch or "content_sha256" not in patch:
                raise IngestError(
                    "invalid-request", f"write patch {path!r} needs content and content_sha256"
                )
            content = str(patch["content"])
            if len(content.encode("utf-8")) > MAX_PATCH_BYTES:
                raise IngestError(
                    "request-too-large", f"authority patch {path!r} exceeds 2 MiB"
                )
            expected_content_sha = _require_sha256(
                patch["content_sha256"], f"authority_patches[{index}].content_sha256"
            )
            if sha256_text(content) != expected_content_sha:
                raise IngestError(
                    "invalid-request", f"authority patch content digest mismatch: {path}"
                )
        elif "content" in patch or "content_sha256" in patch:
            raise IngestError(
                "invalid-request", f"delete patch {path!r} must not contain content"
            )
    decisions = _require_array(request["decisions"], "decisions", MAX_DECISIONS)
    seen_candidates: set[str] = set()
    for index, raw in enumerate(decisions):
        decision = _require_object(raw, f"decisions[{index}]")
        _reject_unknown(
            decision,
            {"candidate_id", "action", "target_id", "evidence"},
            f"decisions[{index}]",
        )
        candidate_id = _bounded_text(
            decision.get("candidate_id"), f"decisions[{index}].candidate_id"
        )
        if candidate_id in seen_candidates:
            raise IngestError("invalid-request", f"duplicate candidate decision: {candidate_id}")
        seen_candidates.add(candidate_id)
        if decision.get("action") not in {"reuse", "add", "update", "reject", "defer"}:
            raise IngestError("invalid-request", f"invalid candidate action at index {index}")
        if decision.get("target_id") is not None:
            _bounded_text(decision["target_id"], f"decisions[{index}].target_id")
        _bounded_text(decision.get("evidence"), f"decisions[{index}].evidence")
    delta = _require_object(request["delta"], "delta")
    if delta.get("schema") != DELTA_SCHEMA:
        raise IngestError("invalid-delta", f"expected {DELTA_SCHEMA} delta")
    for field in ("nodes", "edges", "remove_nodes", "remove_edges"):
        _require_array(delta.get(field, []), f"delta.{field}", MAX_DECISIONS)
    alignments = _require_array(
        request["alignment_decisions"], "alignment_decisions", MAX_ALIGNMENTS
    )
    for index, raw in enumerate(alignments):
        decision = _require_object(raw, f"alignment_decisions[{index}]")
        _reject_unknown(
            decision,
            {
                "candidate_id",
                "target_id",
                "predicate",
                "status",
                "justification",
                "evidence",
                "target_namespace",
            },
            f"alignment_decisions[{index}]",
        )
        for field in ("candidate_id", "target_id", "justification", "evidence"):
            _bounded_text(decision.get(field), f"alignment_decisions[{index}].{field}")
        if decision.get("status") not in {"reviewed", "rejected"}:
            raise IngestError("invalid-request", "alignment status must be reviewed or rejected")
        if decision.get("predicate") not in {
            "exact-match",
            "close-match",
            "broad-match",
            "narrow-match",
            "related-match",
            "different-from",
        }:
            raise IngestError("invalid-request", "invalid alignment predicate")
    review = _require_object(request["review"], "review")
    _reject_unknown(review, {"status", "reviewer", "evidence", "provenance"}, "review")
    if review.get("status") != "reviewed":
        raise IngestError("unreviewed-request", "review.status must be reviewed")
    _bounded_text(review.get("reviewer"), "review.reviewer")
    for field in ("evidence", "provenance"):
        values = _require_array(review.get(field), f"review.{field}", MAX_EVIDENCE_ITEMS)
        if not values:
            raise IngestError("unreviewed-request", f"review.{field} must not be empty")
        for item in values:
            if isinstance(item, str):
                _bounded_text(item, f"review.{field}")
            elif isinstance(item, dict):
                if len(json_text(item).encode("utf-8")) > MAX_TEXT_LENGTH:
                    raise IngestError("request-too-large", f"review.{field} item is too large")
            else:
                raise IngestError("invalid-request", f"review.{field} items must be text or objects")
    supplied = _require_sha256(request["request_sha256"], "request_sha256")
    expected = canonical_digest(request, "request_sha256")
    if supplied != expected:
        raise IngestError(
            "invalid-request-digest",
            f"request_sha256 does not match canonical request: expected {expected}",
        )
    return copy.deepcopy(request)


def _validate_artifact_reference(value: Any, field: str) -> None:
    artifact = _require_object(value, field)
    _reject_unknown(artifact, {"path", "sha256"}, field)
    _bounded_text(artifact.get("path"), f"{field}.path")
    _require_sha256(artifact.get("sha256"), f"{field}.sha256")


def load_request(path: Path, *, mode: str | None = None) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise IngestError("invalid-request", f"cannot read request: {path}") from error
    if size > MAX_REQUEST_BYTES:
        raise IngestError("request-too-large", "ingest request exceeds 8 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IngestError("invalid-request", f"invalid JSON request: {path}") from error
    return validate_request(payload, mode=mode)


def _path_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_relative_path(
    repo_root: Path,
    value: str | Path,
    *,
    field: str,
    allow_missing: bool = True,
) -> tuple[str, Path]:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise IngestError("unsafe-source-path", f"unsafe {field}: {value!r}")
    normalized = Path(*[part for part in raw.parts if part not in {"", "."}])
    candidate = repo_root / normalized
    existing = candidate
    while not existing.exists() and existing != repo_root:
        existing = existing.parent
    try:
        resolved_root = repo_root.resolve(strict=True)
        resolved_existing = existing.resolve(strict=True)
    except OSError as error:
        raise IngestError("unsafe-source-path", f"cannot resolve {field}: {value!r}") from error
    if not _path_inside(resolved_root, resolved_existing):
        raise IngestError("unsafe-source-path", f"{field} escapes repository: {value!r}")
    cursor = candidate
    while cursor != repo_root:
        if cursor.is_symlink():
            resolved = cursor.resolve(strict=False)
            if not _path_inside(resolved_root, resolved):
                raise IngestError(
                    "unsafe-source-path", f"{field} escapes through a symlink: {value!r}"
                )
        cursor = cursor.parent
    if not allow_missing and not candidate.exists():
        raise IngestError("unsafe-source-path", f"missing {field}: {value!r}")
    return normalized.as_posix(), candidate


def _config_relative(paths: IngestPaths, path: Path, field: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.repo_root.resolve()).as_posix()
    except ValueError as error:
        raise IngestError(
            "unsafe-project-path", f"{field} must be inside the repository: {path}"
        ) from error


def _alignment_digest(path: Path) -> str:
    try:
        return sha256_json(load_alignment_set(path))
    except (AlignmentError, OSError, json.JSONDecodeError) as error:
        raise IngestError("invalid-alignment", str(error), stage="precondition") from error


def _artifact_payload(paths: IngestPaths, reference: dict[str, Any], kind: str) -> dict[str, Any]:
    _, path = _safe_relative_path(
        paths.repo_root, str(reference["path"]), field=f"{kind}.path", allow_missing=False
    )
    if not path.is_file():
        raise IngestError("invalid-artifact", f"{kind} is not a file: {reference['path']}")
    if path.stat().st_size > MAX_REQUEST_BYTES:
        raise IngestError("request-too-large", f"{kind} exceeds 8 MiB")
    try:
        payload = read_json(path, {})
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IngestError("invalid-artifact", f"invalid {kind}: {path}") from error
    expected = str(reference["sha256"])
    if kind == "candidate_snapshot":
        try:
            validate_agent_snapshot(payload)
        except ValueError as error:
            raise IngestError("invalid-candidate-snapshot", str(error)) from error
        actual = str(payload.get("snapshot_sha256", ""))
    else:
        actual = _query_report_digest(payload)
    if actual != expected:
        raise IngestError(
            f"invalid-{kind.replace('_', '-')}-digest",
            f"{kind} digest changed: expected {expected}, got {actual}",
            stage="precondition",
        )
    return payload


def _query_report_digest(report: dict[str, Any]) -> str:
    schema = str(report.get("schema", ""))
    if schema == ALIGNMENT_REPORT_SCHEMA:
        supplied = str(report.get("report_sha256", ""))
        expected = canonical_digest(report, "report_sha256")
    elif schema == PROPOSAL_SCHEMA:
        supplied = str(report.get("proposal_sha256", ""))
        expected = canonical_digest(report, "proposal_sha256")
    elif schema == COMPARISON_SCHEMA:
        supplied = sha256_json(report)
        expected = supplied
    else:
        raise IngestError("invalid-query-report", f"unsupported query report schema: {schema!r}")
    if supplied != expected:
        raise IngestError("invalid-query-report", f"stale digest inside query report: {schema}")
    return supplied


def _validate_preconditions(
    paths: IngestPaths, request: dict[str, Any]
) -> tuple[GraphState, dict[str, Any], dict[str, Any], dict[str, str | None]]:
    for field, path in (
        ("registry", paths.registry),
        ("graph", paths.graph_dir),
        ("identities", paths.identities),
        ("alignments", paths.alignments),
        ("typst_registry", paths.typst_registry),
    ):
        _config_relative(paths, path, field)
    state = load_state(paths.graph_dir)
    graph_sha = str(state.manifest.get("graph_sha256", ""))
    if graph_sha != request["base_graph_sha256"]:
        raise IngestError(
            "stale-base-graph",
            f"graph changed after query: expected {request['base_graph_sha256']}, got {graph_sha}",
            stage="precondition",
        )
    alignment_sha = _alignment_digest(paths.alignments)
    if alignment_sha != request["base_alignment_sha256"]:
        raise IngestError(
            "stale-base-alignment",
            "alignment registry changed after query",
            stage="precondition",
        )
    specs = load_sources(paths.repo_root, paths.registry)
    for spec in specs:
        try:
            spec.root.relative_to(paths.repo_root.resolve())
        except ValueError as error:
            raise IngestError(
                "unsafe-source-path", f"registered source root escapes repository: {spec.root}"
            ) from error
    source_hashes: dict[str, str | None] = {}
    for patch in request["authority_patches"]:
        key, path = _safe_relative_path(
            paths.repo_root, str(patch["path"]), field="authority patch"
        )
        if path.suffix.lower() not in {".md", ".typ", ".tex"}:
            raise IngestError("source-ownership", f"unsupported authority format: {key}")
        try:
            unique_source_for_path(specs, path)
        except KnowledgeError as error:
            raise IngestError("source-ownership", str(error), stage="precondition") from error
        actual = sha256_authority_file(path) if path.is_file() else None
        expected = patch["expected_sha256"]
        if actual != expected:
            raise IngestError(
                "stale-source",
                f"authority changed after review: {key}",
                stage="precondition",
                diagnostics=[{"path": key, "expected": expected, "actual": actual}],
            )
        source_hashes[key] = actual
    candidate = _artifact_payload(paths, request["candidate_snapshot"], "candidate_snapshot")
    query_report = _artifact_payload(paths, request["query_report"], "query_report")
    _validate_query_binding(request, state, candidate, query_report)
    _validate_decisions(request, candidate, query_report)
    return state, candidate, query_report, source_hashes


def _validate_query_binding(
    request: dict[str, Any],
    state: GraphState,
    candidate: dict[str, Any],
    query_report: dict[str, Any],
) -> None:
    schema = str(query_report.get("schema", ""))
    candidate_record = query_report.get("candidate") or {}
    target_record = query_report.get("target") or {}
    bound_report_schemas = {
        ALIGNMENT_REPORT_SCHEMA,
        COMPARISON_SCHEMA,
        PROPOSAL_SCHEMA,
    }
    if schema in bound_report_schemas:
        report_alignment_sha = str(query_report.get("alignment_sha256", ""))
        if not HEX_SHA256_RE.fullmatch(report_alignment_sha):
            raise IngestError(
                "stale-query-report",
                "query report does not bind a valid alignment registry digest",
                stage="precondition",
            )
        if report_alignment_sha != str(request["base_alignment_sha256"]):
            raise IngestError(
                "stale-query-report",
                "query report targets a different alignment registry",
                stage="precondition",
            )
    if str(candidate_record.get("snapshot_sha256", "")) != str(
        candidate.get("snapshot_sha256", "")
    ):
        raise IngestError(
            "stale-query-report",
            "query report was not produced from the supplied candidate snapshot",
            stage="precondition",
        )
    if schema in {COMPARISON_SCHEMA, PROPOSAL_SCHEMA}:
        if str(target_record.get("graph_sha256", "")) != str(
            request["base_graph_sha256"]
        ):
            raise IngestError(
                "stale-query-report",
                "query report targets a different graph digest",
                stage="precondition",
            )
    target_snapshot = make_agent_snapshot(state)["snapshot_sha256"]
    if str(target_record.get("snapshot_sha256", "")) != target_snapshot:
        raise IngestError(
            "stale-query-report",
            "query report targets a different personal snapshot",
            stage="precondition",
        )


def _validate_decisions(
    request: dict[str, Any], candidate: dict[str, Any], query_report: dict[str, Any]
) -> None:
    candidate_ids = {str(node.get("id", "")) for node in candidate.get("nodes", [])}
    decision_by_id = {
        str(decision["candidate_id"]): decision for decision in request["decisions"]
    }
    if set(decision_by_id) != candidate_ids:
        missing = sorted(candidate_ids - set(decision_by_id))
        extra = sorted(set(decision_by_id) - candidate_ids)
        raise IngestError(
            "incomplete-review",
            "candidate decisions do not cover the snapshot",
            diagnostics=[{"missing": missing, "extra": extra}],
        )
    schema = str(query_report.get("schema", ""))
    allowed_statuses = (
        {"exact", "candidate", "ambiguous", "unresolved"}
        if schema == ALIGNMENT_REPORT_SCHEMA
        else {"matched", "ambiguous", "unmatched"}
    )
    results: dict[str, str] = {}
    for item in query_report.get("results", []):
        if not isinstance(item, dict):
            raise IngestError("invalid-query-report", "query result must be an object")
        candidate_record = item.get("candidate")
        if not isinstance(candidate_record, dict):
            raise IngestError(
                "invalid-query-report", "query result candidate must be an object"
            )
        candidate_id = str(candidate_record.get("id", ""))
        status = str(item.get("status", ""))
        if (
            candidate_id not in candidate_ids
            or candidate_id in results
            or status not in allowed_statuses
        ):
            raise IngestError(
                "invalid-query-report",
                "query results do not satisfy the active report contract",
            )
        results[candidate_id] = status
    if set(results) != candidate_ids:
        raise IngestError(
            "invalid-query-report",
            "query results do not cover the candidate snapshot",
        )
    for candidate_id, decision in decision_by_id.items():
        status = results.get(candidate_id)
        action = str(decision["action"])
        if status == "ambiguous" and action not in {"reject", "defer"}:
            raise IngestError(
                "unresolved-identity",
                f"{candidate_id} is {status} and cannot be written",
            )
        if status in {"matched", "exact"} and action == "add":
            raise IngestError(
                "duplicate-identity",
                f"matched candidate cannot be added: {candidate_id}",
            )
        if status in {"unmatched", "unresolved"} and action == "reuse":
            raise IngestError(
                "invalid-decision",
                f"unmatched candidate cannot be reused: {candidate_id}",
            )


def _copy_file(source: Path, target: Path) -> None:
    filesystem_source = _filesystem_path(source)
    filesystem_target = _filesystem_path(target)
    filesystem_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(filesystem_source, filesystem_target)


def _filesystem_path(path: Path) -> Path:
    """Use Win32 extended-length paths for I/O without persisting that spelling."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(os.fspath(path))
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{absolute[2:]}")
    return Path(f"\\\\?\\{absolute}")


def _read_json_file(path: Path, default: Any) -> Any:
    filesystem_path = _filesystem_path(path)
    if not filesystem_path.is_file():
        return copy.deepcopy(default)
    return json.loads(filesystem_path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    filesystem_path = _filesystem_path(path)
    filesystem_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=filesystem_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(_filesystem_path(temporary), filesystem_path)
    except (Exception, KeyboardInterrupt, SystemExit):
        try:
            _filesystem_path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _shadow_paths(paths: IngestPaths, root: Path) -> IngestPaths:
    def shadow(path: Path, field: str) -> Path:
        return root / _config_relative(paths, path, field)

    return IngestPaths(
        repo_root=root,
        registry=shadow(paths.registry, "registry"),
        graph_dir=shadow(paths.graph_dir, "graph"),
        identities=shadow(paths.identities, "identities"),
        alignments=shadow(paths.alignments, "alignments"),
        typst_registry=shadow(paths.typst_registry, "typst_registry"),
    )


def _prepare_shadow(paths: IngestPaths, root: Path) -> IngestPaths:
    shadow = _shadow_paths(paths, root)
    specs = load_sources(paths.repo_root, paths.registry)
    for source in (paths.registry, paths.identities, paths.alignments):
        if source.is_file():
            _copy_file(source, root / _config_relative(paths, source, source.name))
    if paths.graph_dir.is_dir():
        shutil.copytree(
            _filesystem_path(paths.graph_dir),
            _filesystem_path(shadow.graph_dir),
        )
    for spec in specs:
        relative_root = relative_path(paths.repo_root, spec.root)
        (root / relative_root).mkdir(parents=True, exist_ok=True)
        for pattern in spec.patterns:
            for source in spec.root.glob(pattern):
                if not source.is_file():
                    continue
                relative = relative_path(paths.repo_root, source)
                _copy_file(source, root / relative)
    return shadow


def _apply_shadow_patches(shadow: IngestPaths, request: dict[str, Any]) -> list[Path]:
    selected: list[Path] = []
    for patch in request["authority_patches"]:
        key, target = _safe_relative_path(
            shadow.repo_root, str(patch["path"]), field="staged authority"
        )
        selected.append(Path(key))
        if patch["operation"] == "delete":
            if target.exists():
                target.unlink()
        else:
            atomic_write(target, str(patch["content"]))
    return selected


def _validate_marker_expectations(
    shadow: IngestPaths, request: dict[str, Any], selected: list[Path]
) -> None:
    specs = load_sources(shadow.repo_root, shadow.registry)
    pairs, _, _ = select_scope(shadow.repo_root, specs, selected, None, None)
    state = load_state(shadow.graph_dir)
    identities = build_identity_index(
        state, load_identity_registry(shadow.identities)
    )
    result = scan_scope(shadow.repo_root, pairs, identities)
    if result.errors:
        raise IngestError(
            "scan-failed",
            "staged authority scan failed",
            stage="scan",
            diagnostics=result.errors,
        )
    actual: dict[str, dict[str, list[str]]] = {
        str(patch["path"]): {"definitions": [], "references": []}
        for patch in request["authority_patches"]
    }
    for definition in result.definitions:
        if definition.authority in actual:
            actual[definition.authority]["definitions"].append(definition.id)
    for reference in result.references:
        if reference.authority in actual:
            actual[reference.authority]["references"].append(reference.target)
    for patch in request["authority_patches"]:
        key = str(patch["path"])
        expected = {
            field: sorted(str(item) for item in patch["expected_markers"][field])
            for field in ("definitions", "references")
        }
        observed = {field: sorted(actual[key][field]) for field in expected}
        if expected != observed:
            raise IngestError(
                "marker-state-mismatch",
                f"staged marker state differs for {key}",
                stage="scan",
                diagnostics=[{"path": key, "expected": expected, "actual": observed}],
            )


def _invoke(injector: FailureInjector | None, stage: str) -> None:
    if injector is not None:
        injector(stage)


def _duration(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _stage_ingest(
    paths: IngestPaths,
    request: dict[str, Any],
    *,
    failure_injector: FailureInjector | None = None,
) -> StagedIngest:
    request = validate_request(request)
    request_sha = str(request["request_sha256"])
    durations: dict[str, int] = {}
    validations: list[dict[str, Any]] = []
    started = time.monotonic()
    before_state, candidate, query_report, before_source_hashes = _validate_preconditions(
        paths, request
    )
    durations["preconditions"] = _duration(started)
    validations.append({"stage": "preconditions", "status": "passed"})
    _invoke(failure_injector, "validated-preconditions")
    before_alignment = _alignment_digest(paths.alignments)
    temporary_root = Path(tempfile.mkdtemp(prefix="kgdistiller-ingest-stage-"))
    durable_root: Path | None = None
    durable_ready = False
    try:
        stage_root = temporary_root / "repo"
        stage_root.mkdir()
        started = time.monotonic()
        shadow = _prepare_shadow(paths, stage_root)
        selected = _apply_shadow_patches(shadow, request)
        durations["stage-authorities"] = _duration(started)
        _invoke(failure_injector, "staged-authorities")
        started = time.monotonic()
        _validate_marker_expectations(shadow, request, selected)
        durations["scan"] = _duration(started)
        validations.append({"stage": "scan", "status": "passed"})
        _invoke(failure_injector, "staged-scan")
        if selected:
            started = time.monotonic()
            try:
                synchronize(
                    shadow.repo_root,
                    shadow.registry,
                    shadow.graph_dir,
                    shadow.typst_registry,
                    identities=shadow.identities,
                    alignments=shadow.alignments,
                    files=selected,
                    course=None,
                    subject=None,
                    write=True,
                )
            except (KnowledgeError, OSError, ValueError) as error:
                raise IngestError("scan-failed", str(error), stage="initial-sync") from error
            durations["initial-sync"] = _duration(started)
            validations.append({"stage": "initial-sync", "status": "passed"})
        _invoke(failure_injector, "staged-initial-sync")
        # The delta is only an input to the staged apply, so keep it outside
        # the mirrored repository and use the extended-length-safe writer.
        delta_path = temporary_root / f"{request_sha}.delta.json"
        _atomic_write_text(delta_path, pretty_json(request["delta"]))
        delta_has_changes = any(
            request["delta"].get(field)
            for field in ("nodes", "edges", "remove_nodes", "remove_edges")
        )
        delta_report: dict[str, Any] = {
            "nodes_removed": 0,
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "edges_removed": 0,
        }
        if delta_has_changes:
            started = time.monotonic()
            try:
                delta_report = apply_delta(
                    shadow.graph_dir,
                    shadow.typst_registry,
                    delta_path,
                )
            except (KnowledgeError, OSError, ValueError) as error:
                raise IngestError("delta-failed", str(error), stage="delta") from error
            durations["delta"] = _duration(started)
            validations.append({"stage": "delta", "status": "passed"})
        _invoke(failure_injector, "staged-delta")
        started = time.monotonic()
        try:
            after_state, _, sync_report = synchronize(
                shadow.repo_root,
                shadow.registry,
                shadow.graph_dir,
                shadow.typst_registry,
                identities=shadow.identities,
                alignments=shadow.alignments,
                files=selected,
                course=None,
                subject=None,
                write=True,
            )
        except (KnowledgeError, OSError, ValueError) as error:
            raise IngestError("sync-failed", str(error), stage="sync") from error
        durations["sync"] = _duration(started)
        validations.append({"stage": "sync", "status": "passed"})
        _invoke(failure_injector, "staged-sync")
        for decision in request["alignment_decisions"]:
            try:
                reconcile_alignment_mapping(
                    after_state,
                    shadow.graph_dir,
                    shadow.alignments,
                    candidate,
                    str(decision["candidate_id"]),
                    str(decision["target_id"]),
                    predicate=str(decision["predicate"]),
                    status=str(decision["status"]),
                    justification=str(decision["justification"]),
                    evidence=str(decision["evidence"]),
                    target_namespace=str(decision.get("target_namespace", "personal")),
                )
            except (KnowledgeError, AlignmentError, OSError, ValueError) as error:
                raise IngestError("alignment-failed", str(error), stage="alignment") from error
        validations.append({"stage": "alignment", "status": "passed"})
        _invoke(failure_injector, "staged-alignments")
        authorities = {str(patch["path"]) for patch in request["authority_patches"]}
        curation = curation_report(after_state, authorities)
        if curation["errors"]:
            raise IngestError(
                "curation-failed",
                "staged curation validation failed",
                stage="curation",
                diagnostics=curation["errors"],
            )
        validations.append({"stage": "curation", "status": "passed"})
        _invoke(failure_injector, "staged-curation")
        started = time.monotonic()
        try:
            checked_state, checked_artifacts, global_report = synchronize(
                shadow.repo_root,
                shadow.registry,
                shadow.graph_dir,
                shadow.typst_registry,
                identities=shadow.identities,
                alignments=shadow.alignments,
                files=[],
                course=None,
                subject=None,
                write=False,
            )
        except (KnowledgeError, OSError, ValueError) as error:
            raise IngestError(
                "global-validation-failed", str(error), stage="global-check"
            ) from error
        stale = [
            name
            for name, content in checked_artifacts.items()
            if not (shadow.graph_dir / name).is_file()
            or (shadow.graph_dir / name).read_text(encoding="utf-8") != content
        ]
        if stale:
            raise IngestError(
                "global-validation-failed",
                f"staged graph is stale: {', '.join(stale)}",
                stage="global-check",
            )
        after_state = checked_state
        sync_report["global"] = global_report
        durations["global-check"] = _duration(started)
        validations.append({"stage": "global-check", "status": "passed"})
        _invoke(failure_injector, "staged-global-check")
        after_alignment = _alignment_digest(shadow.alignments)
        after_source_hashes = {
            str(patch["path"]): (
                sha256_authority_file(shadow.repo_root / str(patch["path"]))
                if (shadow.repo_root / str(patch["path"])).is_file()
                else None
            )
            for patch in request["authority_patches"]
        }
        durable_root = Path(tempfile.mkdtemp(prefix="kgdistiller-ingest-ready-"))
        durable_stage = durable_root / "repo"
        shutil.copytree(
            _filesystem_path(stage_root),
            _filesystem_path(durable_stage),
        )
        durable_shadow = _shadow_paths(paths, durable_stage)
        durable_ready = True
    finally:
        # Cleanup through the platform I/O boundary and keep it best-effort so
        # it cannot mask the original staging failure.
        shutil.rmtree(_filesystem_path(temporary_root), ignore_errors=True)
        if durable_root is not None and not durable_ready:
            shutil.rmtree(_filesystem_path(durable_root), ignore_errors=True)
    if durable_root is None:
        raise AssertionError("staging completed without a durable root")
    return StagedIngest(
        root=durable_root,
        paths=durable_shadow,
        request=request,
        request_sha256=request_sha,
        candidate=candidate,
        query_report=query_report,
        before_state=before_state,
        after_state=after_state,
        before_alignment_sha256=before_alignment,
        after_alignment_sha256=after_alignment,
        before_source_hashes=before_source_hashes,
        after_source_hashes=after_source_hashes,
        sync_report=sync_report,
        delta_report=delta_report,
        curation=curation,
        validations=validations,
        durations_ms=durations,
    )


def _node_changes(
    before: GraphState, after: GraphState, request: dict[str, Any]
) -> dict[str, list[str]]:
    before_ids = set(before.nodes)
    after_ids = set(after.nodes)
    updated = sorted(
        node_id
        for node_id in before_ids & after_ids
        if json_text(before.nodes[node_id]) != json_text(after.nodes[node_id])
    )
    reused = sorted(
        {
            str(decision.get("target_id") or decision["candidate_id"])
            for decision in request["decisions"]
            if decision["action"] == "reuse"
        }
        & after_ids
    )
    return {
        "added": sorted(after_ids - before_ids),
        "reused": reused,
        "updated": updated,
        "orphaned": sorted(
            node_id
            for node_id in after_ids
            if (after.nodes[node_id].get("properties") or {}).get("source_status") == "orphaned"
            and (before.nodes.get(node_id, {}).get("properties") or {}).get("source_status")
            != "orphaned"
        ),
        "removed": sorted(before_ids - after_ids),
    }


def _edge_key_set(state: GraphState) -> set[str]:
    return {
        f"{source}|{relation}|{target}" for source, relation, target in state.edges
    }


def _reference_ids(state: GraphState) -> set[str]:
    return {str(item.get("id", "")) for item in state.references}


def _entry_changes(before: GraphState, after: GraphState) -> list[str]:
    return sorted(
        node_id
        for node_id in set(before.nodes) | set(after.nodes)
        if {
            "text": before.nodes.get(node_id, {}).get("text"),
            "entry": before.nodes.get(node_id, {}).get("entry"),
        }
        != {
            "text": after.nodes.get(node_id, {}).get("text"),
            "entry": after.nodes.get(node_id, {}).get("entry"),
        }
    )


def _alias_changes(before: GraphState, after: GraphState) -> list[str]:
    return sorted(
        node_id
        for node_id in set(before.nodes) | set(after.nodes)
        if (before.nodes.get(node_id, {}).get("properties") or {}).get("aliases", [])
        != (after.nodes.get(node_id, {}).get("properties") or {}).get("aliases", [])
    )


def _authority_definitions(state: GraphState, authorities: set[str]) -> set[str]:
    return {
        node_id
        for node_id, node in state.nodes.items()
        if str((node.get("provenance") or {}).get("authority", "")) in authorities
        and bool((node.get("provenance") or {}).get("active"))
    }


def _plan_payload(staged: StagedIngest) -> dict[str, Any]:
    before_edges = _edge_key_set(staged.before_state)
    after_edges = _edge_key_set(staged.after_state)
    before_refs = _reference_ids(staged.before_state)
    after_refs = _reference_ids(staged.after_state)
    authorities = {
        str(patch["path"]) for patch in staged.request["authority_patches"]
    }
    before_definitions = _authority_definitions(staged.before_state, authorities)
    after_definitions = _authority_definitions(staged.after_state, authorities)
    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "request_id": staged.request["request_id"],
        "request_sha256": staged.request_sha256,
        "engine": {"version": __version__, "capabilities": [CAPABILITY]},
        "before": {
            "graph_sha256": staged.before_state.manifest.get("graph_sha256"),
            "alignment_sha256": staged.before_alignment_sha256,
            "source_hashes": staged.before_source_hashes,
        },
        "after": {
            "graph_sha256": staged.after_state.manifest.get("graph_sha256"),
            "alignment_sha256": staged.after_alignment_sha256,
            "source_hashes": staged.after_source_hashes,
        },
        "changes": {
            "nodes": _node_changes(
                staged.before_state, staged.after_state, staged.request
            ),
            "markers": {
                "definitions_added": sorted(after_definitions - before_definitions),
                "definitions_removed": sorted(before_definitions - after_definitions),
            },
            "entries": {"changed": _entry_changes(staged.before_state, staged.after_state)},
            "aliases": {"changed": _alias_changes(staged.before_state, staged.after_state)},
            "edges": {
                "added": sorted(after_edges - before_edges),
                "removed": sorted(before_edges - after_edges),
            },
            "references": {
                "added": sorted(after_refs - before_refs),
                "removed": sorted(before_refs - after_refs),
            },
            "alignments": [
                {
                    "candidate_id": decision["candidate_id"],
                    "target_id": decision["target_id"],
                    "predicate": decision["predicate"],
                    "status": decision["status"],
                }
                for decision in staged.request["alignment_decisions"]
            ],
            "authority_patches": [
                {
                    "path": patch["path"],
                    "operation": patch["operation"],
                    "before_sha256": staged.before_source_hashes[str(patch["path"])],
                    "after_sha256": staged.after_source_hashes[str(patch["path"])],
                }
                for patch in staged.request["authority_patches"]
            ],
        },
        "validations": staged.validations,
        "warnings": staged.curation.get("warnings", []),
        "status": "planned",
    }
    plan["plan_sha256"] = canonical_digest(plan, "plan_sha256")
    return plan


def plan_ingest(
    paths: IngestPaths,
    request: dict[str, Any],
    *,
    failure_injector: FailureInjector | None = None,
) -> dict[str, Any]:
    validated = validate_request(request, mode="plan")
    staged = _stage_ingest(paths, validated, failure_injector=failure_injector)
    try:
        return _plan_payload(staged)
    finally:
        shutil.rmtree(_filesystem_path(staged.root), ignore_errors=True)


def _state_dir(paths: IngestPaths) -> Path:
    return paths.repo_root / "knowledge/build/kgdistiller-ingest"


def _receipt_path(paths: IngestPaths, request_sha256: str) -> Path:
    return _state_dir(paths) / "receipts" / f"{request_sha256}.json"


def _journal_path(paths: IngestPaths) -> Path:
    return _state_dir(paths) / "journal.json"


def _acquire_writer_lock(handle: Any) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise IngestError(
            "lock-conflict",
            "another kgdistiller writer holds the repository lock",
            stage="lock",
        ) from error


def _release_writer_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def _writer_lock(paths: IngestPaths) -> Iterator[None]:
    state_dir = _state_dir(paths)
    _filesystem_path(state_dir).mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "writer.lock"
    handle = _filesystem_path(lock_path).open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        _acquire_writer_lock(handle)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        handle.flush()
        yield
    finally:
        _release_writer_lock(handle)
        handle.close()


def _backup_target(
    repo_root: Path,
    target: Path,
    backup_root: Path,
    *,
    source: Path | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    relative = target.resolve(strict=False).relative_to(repo_root.resolve()).as_posix()
    backup = backup_root / relative
    backup_source = source if source is not None else target
    filesystem_backup_source = _filesystem_path(backup_source)
    target_kind = kind or ("directory" if filesystem_backup_source.is_dir() else "file")
    existed = filesystem_backup_source.exists()
    if existed:
        if filesystem_backup_source.is_dir():
            shutil.copytree(
                filesystem_backup_source,
                _filesystem_path(backup),
            )
        else:
            _copy_file(backup_source, backup)
    return {"path": relative, "existed": existed, "kind": target_kind}


def _remove_target(target: Path) -> None:
    filesystem_target = _filesystem_path(target)
    if filesystem_target.is_dir() and not filesystem_target.is_symlink():
        shutil.rmtree(filesystem_target)
    elif filesystem_target.exists() or filesystem_target.is_symlink():
        filesystem_target.unlink()


def _journal_failure(message: str, *, stage: str) -> IngestError:
    return IngestError("rollback-failed", message, stage=stage)


def _journal_lstat(path: Path, *, field: str, stage: str) -> int | None:
    try:
        return _filesystem_path(path).lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _journal_failure(
            f"cannot inspect ingest journal {field}: {path}", stage=stage
        ) from error


def _validate_no_symlink_chain(
    base: Path,
    candidate: Path,
    *,
    field: str,
    stage: str,
) -> None:
    base_absolute = Path(os.path.abspath(os.fspath(base)))
    candidate_absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = candidate_absolute.relative_to(base_absolute)
    except ValueError as error:
        raise _journal_failure(
            f"ingest journal {field} escapes its trusted root", stage=stage
        ) from error

    cursor = base_absolute
    missing_parent = False
    for part in relative.parts:
        cursor /= part
        if missing_parent:
            continue
        mode = _journal_lstat(cursor, field=field, stage=stage)
        if mode is None:
            missing_parent = True
            continue
        if stat.S_ISLNK(mode):
            raise _journal_failure(
                f"ingest journal {field} traverses a symlink: {cursor}", stage=stage
            )
        if cursor != candidate_absolute and not stat.S_ISDIR(mode):
            raise _journal_failure(
                f"ingest journal {field} has a non-directory parent: {cursor}",
                stage=stage,
            )


def _journal_relative_path(value: Any, *, field: str, stage: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_JOURNAL_PATH_LENGTH
        or "\0" in value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise _journal_failure(
            f"ingest journal {field} is not a portable relative path", stage=stage
        )
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise _journal_failure(
            f"ingest journal {field} is not a lexical relative path", stage=stage
        )
    return value


def _configured_journal_target(
    paths: IngestPaths,
    target: Path,
    *,
    field: str,
    stage: str,
) -> str:
    repository = Path(os.path.abspath(os.fspath(paths.repo_root)))
    absolute_target = Path(
        os.path.abspath(
            os.fspath(target if target.is_absolute() else paths.repo_root / target)
        )
    )
    try:
        relative_path_value = absolute_target.relative_to(repository)
    except ValueError as error:
        raise _journal_failure(
            f"configured {field} target escapes the repository", stage=stage
        ) from error
    relative = PurePosixPath(*relative_path_value.parts).as_posix()
    try:
        normalized, safe_target = _safe_relative_path(
            paths.repo_root, relative, field=f"journal {field} target"
        )
    except IngestError as error:
        raise _journal_failure(str(error), stage=stage) from error
    if Path(os.path.abspath(os.fspath(safe_target))) != absolute_target:
        raise _journal_failure(
            f"configured {field} target is not lexical repository state", stage=stage
        )
    return normalized


def _validate_backup_tree(path: Path, *, field: str, stage: str) -> None:
    pending = [path]
    while pending:
        current = pending.pop()
        mode = _journal_lstat(current, field=field, stage=stage)
        if mode is None:
            raise _journal_failure(
                f"missing ingest journal backup: {current}", stage=stage
            )
        if stat.S_ISLNK(mode):
            raise _journal_failure(
                f"ingest journal backup must not contain symlinks: {current}",
                stage=stage,
            )
        if stat.S_ISREG(mode):
            continue
        if not stat.S_ISDIR(mode):
            raise _journal_failure(
                f"ingest journal backup is not an ordinary file or directory: {current}",
                stage=stage,
            )
        try:
            pending.extend(_filesystem_path(current).iterdir())
        except OSError as error:
            raise _journal_failure(
                f"cannot inspect ingest journal backup directory: {current}",
                stage=stage,
            ) from error


def _validate_journal(
    paths: IngestPaths,
    journal: Any,
    *,
    stage: str,
) -> _ValidatedJournal:
    if not isinstance(journal, dict) or journal.get("schema") != JOURNAL_SCHEMA:
        raise _journal_failure("invalid ingest journal", stage=stage)

    request_sha256 = journal.get("request_sha256")
    if not isinstance(request_sha256, str) or not HEX_SHA256_RE.fullmatch(
        request_sha256
    ):
        raise _journal_failure(
            "ingest journal has an invalid request_sha256", stage=stage
        )
    status = journal.get("status")
    if not isinstance(status, str) or status not in {
        "installing",
        "committed",
        "rolled-back",
    }:
        raise _journal_failure("ingest journal has an invalid status", stage=stage)

    expected_backup_root = _state_dir(paths) / "backups" / request_sha256
    backup_root_value = journal.get("backup_root")
    if not isinstance(backup_root_value, str) or backup_root_value != os.fspath(
        expected_backup_root
    ):
        raise _journal_failure(
            "ingest journal backup_root does not match the request", stage=stage
        )
    _validate_no_symlink_chain(
        paths.repo_root,
        expected_backup_root,
        field="backup_root",
        stage=stage,
    )
    backup_root_mode = _journal_lstat(
        expected_backup_root, field="backup_root", stage=stage
    )
    if backup_root_mode is not None and not stat.S_ISDIR(backup_root_mode):
        raise _journal_failure(
            "ingest journal backup_root is not an ordinary directory", stage=stage
        )

    raw_targets = journal.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) > MAX_JOURNAL_TARGETS:
        raise _journal_failure(
            f"ingest journal targets must be an array of at most {MAX_JOURNAL_TARGETS} items",
            stage=stage,
        )

    configured: dict[str, str] = {}
    for field, target, kind in (
        ("graph", paths.graph_dir, "directory"),
        ("alignments", paths.alignments, "file"),
        ("typst_registry", paths.typst_registry, "file"),
    ):
        relative = _configured_journal_target(
            paths, target, field=field, stage=stage
        )
        configured[os.path.normcase(relative)] = kind
    try:
        specs = load_sources(paths.repo_root, paths.registry)
    except (
        KnowledgeError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise _journal_failure(
            f"cannot validate journal authority ownership: {error}", stage=stage
        ) from error

    seen: set[str] = set()
    targets: list[_JournalTarget] = []
    for index, raw_record in enumerate(raw_targets):
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "path",
            "existed",
            "kind",
        }:
            raise _journal_failure(
                f"ingest journal target[{index}] has an invalid shape", stage=stage
            )
        relative = _journal_relative_path(
            raw_record["path"], field=f"target[{index}].path", stage=stage
        )
        key = os.path.normcase(relative)
        if key in seen:
            raise _journal_failure(
                f"ingest journal contains duplicate target: {relative}", stage=stage
            )
        seen.add(key)
        existed = raw_record["existed"]
        kind = raw_record["kind"]
        if (
            type(existed) is not bool
            or not isinstance(kind, str)
            or kind not in {"file", "directory"}
        ):
            raise _journal_failure(
                f"ingest journal target[{index}] has invalid kind/existed values",
                stage=stage,
            )
        try:
            normalized, target = _safe_relative_path(
                paths.repo_root, relative, field=f"journal target[{index}]"
            )
        except IngestError as error:
            raise _journal_failure(str(error), stage=stage) from error
        if normalized != relative:
            raise _journal_failure(
                f"ingest journal target[{index}] is not lexical", stage=stage
            )

        expected_kind = configured.get(key)
        if expected_kind is None:
            if target.suffix.lower() not in {".md", ".typ", ".tex"}:
                raise _journal_failure(
                    f"ingest journal target is not managed: {relative}", stage=stage
                )
            try:
                unique_source_for_path(specs, target)
            except KnowledgeError as error:
                raise _journal_failure(
                    f"ingest journal target is not uniquely source-owned: {relative}",
                    stage=stage,
                ) from error
            expected_kind = "file"
        if kind != expected_kind:
            raise _journal_failure(
                f"ingest journal target kind does not match {relative}", stage=stage
            )

        backup = expected_backup_root.joinpath(*PurePosixPath(relative).parts)
        try:
            backup.relative_to(expected_backup_root)
        except ValueError as error:
            raise _journal_failure(
                f"ingest journal backup escapes backup_root: {relative}", stage=stage
            ) from error
        _validate_no_symlink_chain(
            expected_backup_root,
            backup,
            field=f"target[{index}] backup",
            stage=stage,
        )
        backup_mode = _journal_lstat(
            backup, field=f"target[{index}] backup", stage=stage
        )
        if not existed:
            if backup_mode is not None:
                raise _journal_failure(
                    f"unexpected backup for non-existent target: {relative}", stage=stage
                )
        elif kind == "file":
            if backup_mode is None or not stat.S_ISREG(backup_mode):
                raise _journal_failure(
                    f"file backup does not match journal target: {relative}", stage=stage
                )
        else:
            if backup_mode is None or not stat.S_ISDIR(backup_mode):
                raise _journal_failure(
                    f"directory backup does not match journal target: {relative}",
                    stage=stage,
                )
            _validate_backup_tree(
                backup, field=f"target[{index}] backup", stage=stage
            )
        targets.append(
            _JournalTarget(
                relative=relative,
                target=target,
                backup=backup,
                existed=existed,
                kind=kind,
            )
        )

    return _ValidatedJournal(
        request_sha256=request_sha256,
        status=status,
        backup_root=expected_backup_root,
        targets=tuple(targets),
    )


def _restore_validated_journal(
    paths: IngestPaths, journal: _ValidatedJournal
) -> None:
    errors: list[dict[str, Any]] = []
    for target_record in reversed(journal.targets):
        relative = target_record.relative
        try:
            _, target = _safe_relative_path(
                paths.repo_root, relative, field="journal rollback target"
            )
            _validate_no_symlink_chain(
                journal.backup_root,
                target_record.backup,
                field="rollback backup",
                stage="rollback",
            )
            if target_record.existed and target_record.kind == "directory":
                _validate_backup_tree(
                    target_record.backup,
                    field="rollback backup",
                    stage="rollback",
                )
            _remove_target(target)
            if target_record.existed and target_record.kind == "directory":
                shutil.copytree(
                    _filesystem_path(target_record.backup),
                    _filesystem_path(target),
                )
            elif target_record.existed:
                mode = _journal_lstat(
                    target_record.backup, field="rollback backup", stage="rollback"
                )
                if mode is None or not stat.S_ISREG(mode):
                    raise _journal_failure(
                        f"file backup changed during rollback: {relative}",
                        stage="rollback",
                    )
                _copy_file(target_record.backup, target)
        except (OSError, IngestError) as error:
            errors.append({"path": relative, "message": str(error)})
    receipt_path = _receipt_path(paths, journal.request_sha256)
    try:
        _filesystem_path(receipt_path).unlink(missing_ok=True)
    except OSError as error:
        errors.append({"path": str(receipt_path), "message": str(error)})
    if errors:
        raise IngestError(
            "rollback-failed",
            "transaction rollback could not restore every target",
            stage="rollback",
            diagnostics=errors,
        )


def _restore_journal(paths: IngestPaths, journal: dict[str, Any]) -> None:
    validated = _validate_journal(paths, journal, stage="rollback")
    _restore_validated_journal(paths, validated)


def recover_ingest(paths: IngestPaths) -> dict[str, Any] | None:
    journal_path = _journal_path(paths)
    _validate_no_symlink_chain(
        paths.repo_root, journal_path, field="journal path", stage="recovery"
    )
    journal_mode = _journal_lstat(journal_path, field="path", stage="recovery")
    if journal_mode is None:
        return None
    if not stat.S_ISREG(journal_mode):
        raise _journal_failure(
            "ingest journal path is not an ordinary file", stage="recovery"
        )
    try:
        journal = _read_json_file(journal_path, {})
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _journal_failure("invalid ingest journal", stage="recovery") from error
    validated = _validate_journal(paths, journal, stage="recovery")
    if validated.status != "committed":
        _restore_validated_journal(paths, validated)
        outcome = "rolled-back"
    else:
        outcome = "committed"
    try:
        backup_mode = _journal_lstat(
            validated.backup_root, field="backup_root", stage="recovery"
        )
        if backup_mode is not None:
            if not stat.S_ISDIR(backup_mode):
                raise _journal_failure(
                    "ingest journal backup_root changed before cleanup",
                    stage="recovery",
                )
            shutil.rmtree(_filesystem_path(validated.backup_root))
        _filesystem_path(journal_path).unlink()
    except IngestError:
        raise
    except OSError as error:
        raise _journal_failure(
            "could not clean up a recovered ingest journal", stage="recovery"
        ) from error
    return {"request_sha256": validated.request_sha256, "status": outcome}


def _atomic_copy(source: Path, target: Path) -> None:
    filesystem_source = _filesystem_path(source)
    filesystem_target = _filesystem_path(target)
    filesystem_target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.ingest-", dir=filesystem_target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, filesystem_source.open("rb") as input_:
            shutil.copyfileobj(input_, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(_filesystem_path(temporary), filesystem_target)
    finally:
        try:
            _filesystem_path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


def _install_directory(source: Path, target: Path, request_sha256: str) -> None:
    _filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
    prepared = target.parent / f".{target.name}.ingest-{request_sha256[:12]}"
    displaced = target.parent / f".{target.name}.previous-{request_sha256[:12]}"
    _remove_target(prepared)
    _remove_target(displaced)
    shutil.copytree(
        _filesystem_path(source),
        _filesystem_path(prepared),
    )
    filesystem_target = _filesystem_path(target)
    filesystem_prepared = _filesystem_path(prepared)
    filesystem_displaced = _filesystem_path(displaced)
    if filesystem_target.exists():
        os.replace(filesystem_target, filesystem_displaced)
    try:
        os.replace(filesystem_prepared, filesystem_target)
    except (Exception, KeyboardInterrupt, SystemExit):
        if filesystem_displaced.exists() and not filesystem_target.exists():
            os.replace(filesystem_displaced, filesystem_target)
        raise
    _remove_target(displaced)


def _install_staged(
    paths: IngestPaths,
    staged: StagedIngest,
    *,
    failure_injector: FailureInjector | None,
) -> dict[str, Any]:
    state_dir = _state_dir(paths)
    backup_root = state_dir / "backups" / staged.request_sha256
    shutil.rmtree(_filesystem_path(backup_root), ignore_errors=True)
    targets: list[Path] = []
    for patch in staged.request["authority_patches"]:
        targets.append(paths.repo_root / str(patch["path"]))
    targets.extend([paths.graph_dir, paths.alignments, paths.typst_registry])
    unique_targets: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = target.resolve(strict=False).as_posix()
        if key not in seen:
            seen.add(key)
            unique_targets.append(target)
    records: list[dict[str, Any]] = []
    for target in unique_targets:
        records.append(_backup_target(paths.repo_root, target, backup_root))
    journal = {
        "schema": JOURNAL_SCHEMA,
        "request_sha256": staged.request_sha256,
        "status": "installing",
        "backup_root": str(backup_root),
        "targets": records,
    }
    _atomic_write_text(_journal_path(paths), pretty_json(journal))
    _invoke(failure_injector, "prepared-install")
    try:
        for patch in staged.request["authority_patches"]:
            target = paths.repo_root / str(patch["path"])
            source = staged.paths.repo_root / str(patch["path"])
            if patch["operation"] == "delete":
                _remove_target(target)
            else:
                _atomic_copy(source, target)
        _invoke(failure_injector, "installed-authorities")
        if staged.paths.alignments.is_file():
            _atomic_copy(staged.paths.alignments, paths.alignments)
        _invoke(failure_injector, "installed-alignments")
        _install_directory(staged.paths.graph_dir, paths.graph_dir, staged.request_sha256)
        _invoke(failure_injector, "installed-graph")
        if staged.paths.typst_registry.is_file():
            _atomic_copy(staged.paths.typst_registry, paths.typst_registry)
        _invoke(failure_injector, "installed-registry")
        return journal
    except BaseException as error:
        try:
            _restore_journal(paths, journal)
            journal["status"] = "rolled-back"
            _atomic_write_text(_journal_path(paths), pretty_json(journal))
        except IngestError:
            raise
        if isinstance(error, IngestError):
            raise
        raise IngestError("install-failed", str(error), stage="install") from error


def _receipt_payload(staged: StagedIngest, plan: dict[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "request_id": staged.request["request_id"],
        "request_sha256": staged.request_sha256,
        "engine": {
            "version": __version__,
            "capabilities": [CAPABILITY],
            "graph_schema": staged.after_state.manifest.get("schema"),
            "query_backend": "json-memory",
        },
        "before": plan["before"],
        "after": plan["after"],
        "changes": plan["changes"],
        "validations": [
            *staged.validations,
            {"stage": "install", "status": "passed"},
        ],
        "durations_ms": staged.durations_ms,
        "warnings": plan["warnings"],
        "status": "committed",
    }
    receipt["receipt_sha256"] = canonical_digest(receipt, "receipt_sha256")
    _validate_json_schema(
        receipt, "qlkg-ingest-receipt-v2.schema.json", "invalid-receipt"
    )
    return receipt


def _find_request_conflict(paths: IngestPaths, request: dict[str, Any]) -> None:
    receipts = _state_dir(paths) / "receipts"
    filesystem_receipts = _filesystem_path(receipts)
    if not filesystem_receipts.is_dir():
        return
    for path in filesystem_receipts.glob("*.json"):
        existing = _read_json_file(path, {})
        if (
            existing.get("request_id") == request["request_id"]
            and existing.get("request_sha256") != request["request_sha256"]
        ):
            raise IngestError(
                "request-id-conflict",
                f"request_id {request['request_id']!r} was already committed with different content",
                stage="idempotency",
            )


def _validate_stored_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != RECEIPT_SCHEMA:
        raise IngestError(
            "unsupported-receipt-schema",
            f"expected stored {RECEIPT_SCHEMA} receipt",
            stage="idempotency",
        )
    _validate_json_schema(
        payload, "qlkg-ingest-receipt-v2.schema.json", "invalid-receipt"
    )
    if payload.get("receipt_sha256") != canonical_digest(payload, "receipt_sha256"):
        raise IngestError(
            "invalid-receipt",
            "stored receipt digest does not match its content",
            stage="idempotency",
        )
    return payload


def apply_ingest(
    paths: IngestPaths,
    request: dict[str, Any],
    *,
    failure_injector: FailureInjector | None = None,
) -> dict[str, Any]:
    validated = validate_request(request, mode="apply")
    request_sha = str(validated["request_sha256"])
    with _writer_lock(paths):
        recover_ingest(paths)
        existing_path = _receipt_path(paths, request_sha)
        if _filesystem_path(existing_path).is_file():
            return _validate_stored_receipt(_read_json_file(existing_path, {}))
        _find_request_conflict(paths, validated)
        staged = _stage_ingest(paths, validated, failure_injector=failure_injector)
        try:
            plan = _plan_payload(staged)
            install_started = time.monotonic()
            journal = _install_staged(
                paths, staged, failure_injector=failure_injector
            )
            staged.durations_ms["install"] = _duration(install_started)
            try:
                receipt = _receipt_payload(staged, plan)
                receipt_path = _receipt_path(paths, request_sha)
                _atomic_write_text(receipt_path, pretty_json(receipt))
                _invoke(failure_injector, "receipt-written")
                journal["status"] = "committed"
                _atomic_write_text(_journal_path(paths), pretty_json(journal))
                recover_ingest(paths)
                return receipt
            except BaseException:
                _restore_journal(paths, journal)
                journal["status"] = "rolled-back"
                _atomic_write_text(_journal_path(paths), pretty_json(journal))
                raise
        finally:
            shutil.rmtree(_filesystem_path(staged.root), ignore_errors=True)
