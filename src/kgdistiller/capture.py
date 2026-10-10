"""Prepare reviewed, source-backed entries as ingest requests.

A capture cites a registered source by path and line range. Its Evidence is
copied verbatim from those lines, so the request records exactly what the
reviewer saw. Captures never edit the source and never apply anything; the
emitted plan and apply requests go through the ingest transaction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .document_types import load_document_types, validate_node_kind
from .entries import (
    LIST_SECTIONS,
    UNDERSTANDING_STATES,
    cited_text,
    entry_relative,
    identity_key,
    slug_id,
    split_lines,
    validate_id,
)
from .ingest import (
    CAPABILITY,
    REQUEST_SCHEMA,
    IngestPaths,
    receipt_path,
    validate_request,
)
from .knowledge_store import (
    DELTA_SCHEMA,
    KnowledgeState,
    StoreError,
    apply_delta,
    entries_root,
    identity_index,
    load_state,
)
from .sources import KnowledgeError, load_sources, source_for_path

PAYLOAD_KEYS = {
    "label", "id", "source", "line_start", "line_end", "kind", "aliases", "text", "entry", "review",
}
ENTRY_KEYS = {"context", "role", "understanding", *LIST_SECTIONS}
REVIEW_KEYS = {"action", "reviewer", "evidence", "target_id"}
MAX_REQUEST_ID_STEM = 110


class CaptureError(KnowledgeError):
    """A capture cannot be prepared safely."""


def _text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(f"{field} must be non-empty text")
    return value.strip()


def _inside(root: Path, value: str | Path, field: str) -> Path:
    if any(character in str(value) for character in ("\n", "\r", "\0")):
        raise CaptureError(f"{field} must be a single path")
    raw = Path(value)
    if ".." in raw.parts:
        raise CaptureError(f"{field} must stay inside the project")
    path = (raw if raw.is_absolute() else root / raw).resolve()
    if not path.is_relative_to(root) or path == root:
        raise CaptureError(f"{field} must stay inside the project")
    return path


def _line(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int or value < 1:
        raise CaptureError(f"{field} must be a positive integer")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CaptureError(f"{field} must be a list of text items")
    return [item.strip() for item in value if item.strip()]


def capture_record(
    paths: IngestPaths, payload: Any, state: KnowledgeState | None = None
) -> dict[str, Any]:
    """Validate one capture against the current store and build its entry record.

    Returns the action, the full entry record, the expected label of an update
    target and the existing record (``None`` for an add).
    """
    if not isinstance(payload, dict):
        raise CaptureError("capture must be an object")
    unknown = sorted(set(payload) - PAYLOAD_KEYS)
    if unknown:
        raise CaptureError(f"unknown capture fields: {', '.join(unknown)}")
    label = _text(payload, "label")
    if "\n" in label or "\r" in label:
        raise CaptureError("label must be a single line")
    summary = _text(payload, "text")
    review = payload.get("review")
    if not isinstance(review, dict) or set(review) - REVIEW_KEYS:
        raise CaptureError("review requires an explicit identity decision")
    action = review.get("action")
    if action not in {"add", "update"}:
        raise CaptureError("review.action must be add or update")
    reviewer, evidence = _text(review, "reviewer"), _text(review, "evidence")
    extras = payload.get("entry", {})
    if not isinstance(extras, dict) or set(extras) - ENTRY_KEYS:
        raise CaptureError(f"entry may contain only: {', '.join(sorted(ENTRY_KEYS))}")
    root = paths.repo_root.resolve()
    if state is None:
        state = load_state(root)
    specs = load_sources(root, paths.registry)
    source = _inside(root, _text(payload, "source"), "source")
    try:
        owner = source_for_path(specs, source)
    except KnowledgeError as error:
        raise CaptureError(str(error)) from error
    if not source.is_file():
        raise CaptureError(f"source file does not exist: {source.relative_to(root).as_posix()}")
    try:
        with source.open("r", encoding="utf-8", newline=None) as handle:
            lines = split_lines(handle.read())
    except UnicodeError as error:
        raise CaptureError(f"source is not UTF-8 text: {source}") from error
    start, end = _line(payload, "line_start"), _line(payload, "line_end")
    if start > end or end > len(lines):
        raise CaptureError(f"lines {start}-{end} are outside the source's {len(lines)} lines")
    names = identity_index(state)
    existing: dict[str, Any] | None = None
    if action == "add":
        if "target_id" in review:
            raise CaptureError("add creates a new entry and takes no target_id")
        entry_id = payload["id"] if "id" in payload else slug_id(label)
        if entry_id is None:
            raise CaptureError(f"label {label!r} has no ASCII slug; supply an explicit id")
        try:
            validate_id(entry_id)
        except ValueError as error:
            raise CaptureError(str(error)) from error
        if entry_id in state.entries:
            raise CaptureError(f"entry {entry_id} already exists; review an update instead")
        record: dict[str, Any] = {"aliases": [], "understanding": "unknown"}
        if "kind" not in payload:
            raise CaptureError("kind is required for a new entry")
    else:
        entry_id = _text(review, "target_id")
        existing = state.entries.get(entry_id)
        if existing is None:
            raise CaptureError(f"update target {entry_id} does not exist")
        if "id" in payload and payload["id"] != entry_id:
            raise CaptureError("an update keeps its target's id")
        record = {key: value for key, value in existing.items()
                  if key not in {"context", "role", *LIST_SECTIONS} or key not in extras}
    kind = _text(payload, "kind") if "kind" in payload else record["kind"]
    try:
        validate_node_kind(kind, owner.document_type, load_document_types(paths.registry))
    except KnowledgeError as error:
        raise CaptureError(str(error)) from error
    aliases = _string_list(payload["aliases"], "aliases") if "aliases" in payload else list(record["aliases"])
    if (
        existing is not None
        and identity_key(existing["label"]) != identity_key(label)
        and identity_key(existing["label"]) not in {identity_key(alias) for alias in aliases}
    ):
        aliases.append(existing["label"])
    aliases = [alias for alias in aliases if identity_key(alias) != identity_key(label)]
    for name in (label, *aliases):
        holder = names.get(identity_key(name))
        if holder is not None and holder != entry_id:
            raise CaptureError(f"name {name!r} already identifies {holder}; review an update instead")
    record.update({
        "id": entry_id, "label": label, "kind": kind, "aliases": aliases,
        "source": source.relative_to(root).as_posix(), "line_start": start, "line_end": end,
        "summary": summary, "evidence": cited_text(lines, start, end),
    })
    for field, value in extras.items():
        if field == "understanding":
            if value not in UNDERSTANDING_STATES:
                raise CaptureError(f"understanding must be one of {', '.join(UNDERSTANDING_STATES)}")
            record[field] = value
        elif field in LIST_SECTIONS:
            items = _string_list(value, f"entry.{field}")
            if items:
                record[field] = items
        else:
            if not isinstance(value, str):
                raise CaptureError(f"entry.{field} must be text")
            if value.strip():
                record[field] = value.strip()
    return {
        "action": action, "record": record, "existing": existing,
        "expected_label": existing["label"] if existing else None,
        "reviewer": reviewer, "evidence": evidence,
    }


def _check_output(paths: IngestPaths, output_dir: Path) -> Path:
    root = paths.repo_root.resolve()
    output = _inside(root, output_dir, "output_dir")
    specs = load_sources(root, paths.registry)
    if any(output == spec.root or output.is_relative_to(spec.root) for spec in specs):
        raise CaptureError("output_dir must be outside registered source roots")
    entries = entries_root(root)
    if output == entries or output.is_relative_to(entries):
        raise CaptureError("output_dir must be outside the committed entries")
    return output


def _next_request_id(paths: IngestPaths, output: Path, entry_id: str) -> str:
    stem = f"capture-{entry_id[:MAX_REQUEST_ID_STEM].rstrip('-')}"
    number = 1
    while (
        receipt_path(paths, f"{stem}-{number}").exists()
        or (output / f"{stem}-{number}.plan.json").exists()
        or (output / f"{stem}-{number}.apply.json").exists()
    ):
        number += 1
    return f"{stem}-{number}"


def prepare_captures(
    paths: IngestPaths,
    payloads: list[Any],
    output_dir: Path,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Prepare reviewed captures as one plan request and one apply request."""
    if not isinstance(payloads, list) or not payloads:
        raise CaptureError("captures must be a non-empty array")
    root = paths.repo_root.resolve()
    output = _check_output(paths, output_dir)
    state = load_state(root)
    captures = [capture_record(paths, payload, state) for payload in payloads]
    ids = [capture["record"]["id"] for capture in captures]
    if len(set(ids)) != len(ids):
        raise CaptureError("selected captures name the same entry more than once")
    delta = {
        "schema": DELTA_SCHEMA,
        "create_entries": [c["record"] for c in captures if c["action"] == "add"],
        "update_entries": [{"expected_label": c["expected_label"], "entry": c["record"]}
                           for c in captures if c["action"] == "update"],
        "remove_entries": [], "add_edges": [], "remove_edges": [],
    }
    try:
        apply_delta(state, delta, root, paths.registry)
    except StoreError as error:
        raise CaptureError(f"{error.code}: {error}") from error
    output.mkdir(parents=True, exist_ok=True)
    request_id = request_id or _next_request_id(paths, output, ids[0])
    request = {
        "schema": REQUEST_SCHEMA, "request_id": request_id, "mode": "plan",
        "capabilities": [CAPABILITY],
        "delta": delta,
        "review": {
            "status": "reviewed",
            "reviewer": ", ".join(dict.fromkeys(capture["reviewer"] for capture in captures)),
            "evidence": [capture["evidence"] for capture in captures],
            "provenance": [
                {"source": c["record"]["source"], "line_start": c["record"]["line_start"],
                 "line_end": c["record"]["line_end"]}
                for c in captures
            ],
        },
    }
    validate_request(request, mode="plan")
    apply_request = {**request, "mode": "apply"}
    validate_request(apply_request, mode="apply")
    artifacts = {mode: output / f"{request_id}.{mode}.json" for mode in ("plan", "apply")}
    for mode, value in (("plan", request), ("apply", apply_request)):
        with artifacts[mode].open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "status": "prepared",
        "request_id": request_id,
        "artifacts": {mode: str(path) for mode, path in artifacts.items()},
        "entries": [
            {"id": c["record"]["id"], "label": c["record"]["label"], "action": c["action"],
             "entry": entry_relative(c["record"]["id"]).as_posix()}
            for c in captures
        ],
        "counts": {"entries": len(captures)},
    }


def prepare_capture(paths: IngestPaths, payload: Any, output_dir: Path) -> dict[str, Any]:
    """Prepare one explicitly reviewed capture without applying it."""
    return prepare_captures(paths, [payload], output_dir)
