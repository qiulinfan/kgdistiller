"""Transactional, review-gated ingestion for kgdistiller projects."""

from __future__ import annotations

import copy
import errno
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterator

from . import __version__
from .agent import AgentIndexError, validate_agent_snapshot
from .alignment import (
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
    sha256_file,
    sha256_text,
    synchronize,
    unique_source_for_path,
    apply_delta,
    write_database,
)
from .json_schema import validate_json_schema


REQUEST_SCHEMA = "qlkg-ingest-request-v1"
PLAN_SCHEMA = "qlkg-ingest-plan-v1"
RECEIPT_SCHEMA = "qlkg-ingest-receipt-v1"
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
    database: Path
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
        payload, "qlkg-ingest-request-v1.schema.json", "invalid-request"
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
        except AgentIndexError as error:
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
    if schema == "qlkg-alignment-report-v1":
        supplied = str(report.get("report_sha256", ""))
        expected = canonical_digest(report, "report_sha256")
    elif schema == "qlkg-agent-proposal-v1":
        supplied = str(report.get("proposal_sha256", ""))
        expected = canonical_digest(report, "proposal_sha256")
    elif schema == "qlkg-graph-comparison-v1":
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
        ("database", paths.database),
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
        actual = sha256_file(path) if path.is_file() else None
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
    if str(candidate_record.get("snapshot_sha256", "")) != str(
        candidate.get("snapshot_sha256", "")
    ):
        raise IngestError(
            "stale-query-report",
            "query report was not produced from the supplied candidate snapshot",
            stage="precondition",
        )
    if schema in {"qlkg-graph-comparison-v1", "qlkg-agent-proposal-v1"}:
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
    results = {
        str(item.get("candidate", {}).get("id", "")): str(item.get("status", ""))
        for item in query_report.get("results", [])
        if isinstance(item, dict)
    }
    for candidate_id, decision in decision_by_id.items():
        status = results.get(candidate_id)
        action = str(decision["action"])
        if status in {"conflict", "uncertain"} and action not in {"reject", "defer"}:
            raise IngestError(
                "unresolved-identity",
                f"{candidate_id} is {status} and cannot be written",
            )
        if status == "known" and action == "add":
            raise IngestError("duplicate-identity", f"known candidate cannot be added: {candidate_id}")
        if status == "new" and action == "reuse":
            raise IngestError("invalid-decision", f"new candidate cannot be reused: {candidate_id}")


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _shadow_paths(paths: IngestPaths, root: Path) -> IngestPaths:
    def shadow(path: Path, field: str) -> Path:
        return root / _config_relative(paths, path, field)

    return IngestPaths(
        repo_root=root,
        registry=shadow(paths.registry, "registry"),
        graph_dir=shadow(paths.graph_dir, "graph"),
        identities=shadow(paths.identities, "identities"),
        alignments=shadow(paths.alignments, "alignments"),
        database=shadow(paths.database, "database"),
        typst_registry=shadow(paths.typst_registry, "typst_registry"),
    )


def _prepare_shadow(paths: IngestPaths, root: Path) -> IngestPaths:
    shadow = _shadow_paths(paths, root)
    specs = load_sources(paths.repo_root, paths.registry)
    for source in (paths.registry, paths.identities, paths.alignments):
        if source.is_file():
            _copy_file(source, root / _config_relative(paths, source, source.name))
    if paths.graph_dir.is_dir():
        shutil.copytree(paths.graph_dir, shadow.graph_dir)
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
    with tempfile.TemporaryDirectory(prefix="kgdistiller-ingest-stage-") as temporary:
        stage_root = Path(temporary) / "repo"
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
                    shadow.database,
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
        delta_path = shadow.database.parent / f"{request_sha}.delta.json"
        atomic_write(delta_path, pretty_json(request["delta"]))
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
                    shadow.database,
                    shadow.typst_registry,
                    delta_path,
                    shadow.alignments,
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
                shadow.database,
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
                    shadow.database,
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
            except (KnowledgeError, AgentIndexError, AlignmentError, OSError, ValueError) as error:
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
                shadow.database,
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
                sha256_file(shadow.repo_root / str(patch["path"]))
                if (shadow.repo_root / str(patch["path"])).is_file()
                else None
            )
            for patch in request["authority_patches"]
        }
        durable_root = Path(tempfile.mkdtemp(prefix="kgdistiller-ingest-ready-"))
        durable_stage = durable_root / "repo"
        shutil.copytree(stage_root, durable_stage)
        durable_shadow = _shadow_paths(paths, durable_stage)
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
        shutil.rmtree(staged.root, ignore_errors=True)


def _state_dir(paths: IngestPaths) -> Path:
    return paths.database.parent / "kgdistiller-ingest"


def _receipt_path(paths: IngestPaths, request_sha256: str) -> Path:
    return _state_dir(paths) / "receipts" / f"{request_sha256}.json"


def _journal_path(paths: IngestPaths) -> Path:
    return _state_dir(paths) / "journal.json"


@contextmanager
def _writer_lock(paths: IngestPaths) -> Iterator[None]:
    state_dir = _state_dir(paths)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "writer.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError) as error:
            if isinstance(error, OSError) and error.errno not in {
                errno.EACCES,
                errno.EAGAIN,
            }:
                raise
            raise IngestError(
                "lock-conflict", "another kgdistiller writer holds the repository lock", stage="lock"
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def _backup_target(repo_root: Path, target: Path, backup_root: Path) -> dict[str, Any]:
    relative = target.resolve(strict=False).relative_to(repo_root.resolve()).as_posix()
    backup = backup_root / relative
    existed = target.exists()
    kind = "directory" if target.is_dir() else "file"
    if existed:
        if target.is_dir():
            shutil.copytree(target, backup)
        else:
            _copy_file(target, backup)
    return {"path": relative, "existed": existed, "kind": kind}


def _remove_target(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()


def _restore_journal(paths: IngestPaths, journal: dict[str, Any]) -> None:
    backup_root = Path(str(journal["backup_root"]))
    errors: list[dict[str, Any]] = []
    for target_record in reversed(journal.get("targets", [])):
        relative = str(target_record["path"])
        target = paths.repo_root / relative
        backup = backup_root / relative
        try:
            _remove_target(target)
            if target_record.get("existed"):
                if target_record.get("kind") == "directory":
                    shutil.copytree(backup, target)
                else:
                    _copy_file(backup, target)
        except OSError as error:
            errors.append({"path": relative, "message": str(error)})
    receipt = _receipt_path(paths, str(journal.get("request_sha256", "")))
    if receipt.is_file():
        receipt.unlink()
    if errors:
        raise IngestError(
            "rollback-failed",
            "transaction rollback could not restore every target",
            stage="rollback",
            diagnostics=errors,
        )


def recover_ingest(paths: IngestPaths) -> dict[str, Any] | None:
    journal_path = _journal_path(paths)
    if not journal_path.is_file():
        return None
    journal = read_json(journal_path, {})
    if journal.get("schema") != JOURNAL_SCHEMA:
        raise IngestError("rollback-failed", "invalid ingest journal", stage="recovery")
    if journal.get("status") != "committed":
        _restore_journal(paths, journal)
        outcome = "rolled-back"
    else:
        outcome = "committed"
    backup_root = Path(str(journal.get("backup_root", "")))
    shutil.rmtree(backup_root, ignore_errors=True)
    journal_path.unlink(missing_ok=True)
    return {"request_sha256": journal.get("request_sha256"), "status": outcome}


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.ingest-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_:
            shutil.copyfileobj(input_, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _install_directory(source: Path, target: Path, request_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = target.parent / f".{target.name}.ingest-{request_sha256[:12]}"
    displaced = target.parent / f".{target.name}.previous-{request_sha256[:12]}"
    _remove_target(prepared)
    _remove_target(displaced)
    shutil.copytree(source, prepared)
    if target.exists():
        os.replace(target, displaced)
    try:
        os.replace(prepared, target)
    except BaseException:
        if displaced.exists() and not target.exists():
            os.replace(displaced, target)
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
    shutil.rmtree(backup_root, ignore_errors=True)
    targets: list[Path] = []
    for patch in staged.request["authority_patches"]:
        targets.append(paths.repo_root / str(patch["path"]))
    targets.extend([paths.graph_dir, paths.alignments, paths.typst_registry, paths.database])
    unique_targets: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = target.resolve(strict=False).as_posix()
        if key not in seen:
            seen.add(key)
            unique_targets.append(target)
    records = [_backup_target(paths.repo_root, target, backup_root) for target in unique_targets]
    journal = {
        "schema": JOURNAL_SCHEMA,
        "request_sha256": staged.request_sha256,
        "status": "installing",
        "backup_root": str(backup_root),
        "targets": records,
    }
    atomic_write(_journal_path(paths), pretty_json(journal))
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
        write_database(paths.database, load_state(paths.graph_dir), paths.alignments)
        _invoke(failure_injector, "rebuilt-index")
        return journal
    except BaseException as error:
        try:
            _restore_journal(paths, journal)
            journal["status"] = "rolled-back"
            atomic_write(_journal_path(paths), pretty_json(journal))
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
            "index_schema": "qlkg-agent-index-v2",
        },
        "before": plan["before"],
        "after": plan["after"],
        "changes": plan["changes"],
        "validations": [
            *staged.validations,
            {"stage": "install", "status": "passed"},
            {"stage": "index-rebuild", "status": "passed"},
        ],
        "durations_ms": staged.durations_ms,
        "warnings": plan["warnings"],
        "status": "committed",
    }
    receipt["receipt_sha256"] = canonical_digest(receipt, "receipt_sha256")
    _validate_json_schema(
        receipt, "qlkg-ingest-receipt-v1.schema.json", "invalid-receipt"
    )
    return receipt


def _find_request_conflict(paths: IngestPaths, request: dict[str, Any]) -> None:
    receipts = _state_dir(paths) / "receipts"
    if not receipts.is_dir():
        return
    for path in receipts.glob("*.json"):
        existing = read_json(path, {})
        if (
            existing.get("request_id") == request["request_id"]
            and existing.get("request_sha256") != request["request_sha256"]
        ):
            raise IngestError(
                "request-id-conflict",
                f"request_id {request['request_id']!r} was already committed with different content",
                stage="idempotency",
            )


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
        if existing_path.is_file():
            return read_json(existing_path, {})
        _find_request_conflict(paths, validated)
        staged = _stage_ingest(paths, validated, failure_injector=failure_injector)
        try:
            plan = _plan_payload(staged)
            install_started = time.monotonic()
            journal = _install_staged(
                paths, staged, failure_injector=failure_injector
            )
            staged.durations_ms["install-and-index"] = _duration(install_started)
            try:
                receipt = _receipt_payload(staged, plan)
                receipt_path = _receipt_path(paths, request_sha)
                atomic_write(receipt_path, pretty_json(receipt))
                _invoke(failure_injector, "receipt-written")
                journal["status"] = "committed"
                atomic_write(_journal_path(paths), pretty_json(journal))
                recover_ingest(paths)
                return receipt
            except BaseException:
                _restore_journal(paths, journal)
                journal["status"] = "rolled-back"
                atomic_write(_journal_path(paths), pretty_json(journal))
                raise
        finally:
            shutil.rmtree(staged.root, ignore_errors=True)
