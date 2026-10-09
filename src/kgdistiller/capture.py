"""Prepare one reviewed knowledge entry for the existing ingest transaction."""

from __future__ import annotations

import copy
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .candidate import CANDIDATE_SOURCE_SCHEMA, build_candidate_snapshot
from .cli import (
    DELTA_SCHEMA,
    KnowledgeError,
    build_identity_index,
    load_identity_registry,
    load_sources,
    load_state,
    pretty_json,
    scan_source,
    sha256_authority_file,
    sha256_text,
    unique_source_for_path,
)
from .contracts import sha256_json
from .entry_markdown import DERIVED_SOURCE_ROOT, ENTRY_ROOT, normalize_entry, resolve_entry_source
from .ingest import CAPABILITY, REQUEST_SCHEMA, IngestPaths, finalize_request, validate_request
from .query import GraphView, compare


class CaptureError(KnowledgeError):
    """A single-entry capture cannot be prepared safely."""


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


def prepare_capture(
    paths: IngestPaths, payload: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Write a candidate, comparison and plan request; never mutate knowledge.

    ``payload`` selects ``name`` in one registered ``source``, supplies ``text``
    and optional ``entry``, and carries an explicit ``review`` with ``action``
    (add/update), ``reviewer`` and ``evidence``. Updates use the selected native
    identity in the same source; an optional ``target_id`` must agree with it.
    Optional ``source_content`` or ``source_content_file`` contains
    a complete proposed native authority file; no marker is invented here.
    """
    if not isinstance(payload, dict):
        raise CaptureError("capture must be an object")
    unknown = set(payload) - {
        "name", "source", "text", "entry", "review", "source_content",
        "source_content_file",
    }
    if unknown:
        raise CaptureError(f"unknown capture fields: {', '.join(sorted(unknown))}")
    name = _text(payload, "name")
    if any(character in name for character in ("\n", "\r", "\0")):
        raise CaptureError("name must be a single-line native marker name")
    text = _text(payload, "text")
    review = payload.get("review")
    if not isinstance(review, dict) or set(review) - {
        "action", "reviewer", "evidence", "target_id",
    }:
        raise CaptureError("review requires an explicit identity decision")
    action = review.get("action")
    if action not in {"add", "update"}:
        raise CaptureError("review.action must be add or update")
    reviewer, evidence = _text(review, "reviewer"), _text(review, "evidence")
    target_id = _text(review, "target_id") if action == "update" and "target_id" in review else None
    if action == "add" and "target_id" in review:
        raise CaptureError("add uses the reviewed native marker, not a target_id")
    entry = normalize_entry(payload.get("entry"), text)
    root = paths.repo_root.resolve()
    source = _inside(root, _text(payload, "source"), "source")
    output = _inside(root, output_dir, "output_dir")
    specs = load_sources(root, paths.registry)
    owner = unique_source_for_path(specs, source)
    if any(output == spec.root or output.is_relative_to(spec.root) for spec in specs):
        raise CaptureError("output_dir must be outside registered source roots")
    for protected in (paths.graph_dir.resolve(), root / ENTRY_ROOT, root / DERIVED_SOURCE_ROOT):
        if output == protected or output.is_relative_to(protected):
            raise CaptureError("output_dir must be outside committed knowledge and derived evidence")
    if "source_content" in payload and "source_content_file" in payload:
        raise CaptureError("supply only one proposed source content input")
    expected_source = sha256_authority_file(source) if source.is_file() else None
    if "source_content_file" in payload:
        proposed = Path(_text(payload, "source_content_file"))
        content = (proposed if proposed.is_absolute() else root / proposed).read_text(encoding="utf-8")
    elif "source_content" in payload:
        content = payload["source_content"]
        if not isinstance(content, str):
            raise CaptureError("source_content must be text")
    elif source.is_file():
        content = source.read_text(encoding="utf-8")
    else:
        raise CaptureError("a new source needs complete proposed source content")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    state = load_state(paths.graph_dir)
    identities = build_identity_index(state, load_identity_registry(paths.identities))
    view = GraphView.load(paths.graph_dir, paths.alignments)
    if state.manifest["graph_sha256"] != view.snapshot["graph"]["sha256"]:
        raise CaptureError("knowledge changed while preparing; retry capture")
    relative = source.relative_to(root)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=output) as temporary:
        shadow = Path(temporary)
        staged_source = shadow / relative
        staged_source.parent.mkdir(parents=True, exist_ok=True)
        staged_source.write_text(content, encoding="utf-8")
        staged_owner = replace(owner, root=shadow / owner.root.relative_to(root))
        scan = scan_source(shadow, staged_owner, staged_source, identities)
    if scan.errors:
        raise CaptureError(f"proposed source scan failed: {scan.errors}")
    if len({item.id for item in scan.definitions}) != len(scan.definitions):
        raise CaptureError("proposed source contains duplicate native identities")
    selected = [item for item in scan.definitions if item.label == name]
    if len(selected) != 1:
        raise CaptureError("name must select exactly one explicit native definition marker")
    definition = selected[0]
    if source.is_file():
        before = scan_source(root, owner, source, identities)
        if before.errors:
            raise CaptureError(f"current source scan failed: {before.errors}")
        previous = {item.id: item.definition_sha256 for item in before.definitions if item.id != definition.id}
        proposed = {item.id: item.definition_sha256 for item in scan.definitions if item.id != definition.id}
        if previous != proposed:
            raise CaptureError("capture may change only the selected definition")
    elif len(scan.definitions) != 1:
        raise CaptureError("a new capture source must contain only the selected definition")
    if target_id is not None and target_id != definition.id:
        raise CaptureError("reviewed target differs from the selected native identity")
    if action == "update":
        target_id = definition.id
        existing = state.nodes.get(target_id) or {}
        provenance = existing.get("provenance") or {}
        if (
            existing.get("type") != "knowledge"
            or provenance.get("authority") != relative.as_posix()
            or provenance.get("active") is False
        ):
            raise CaptureError("update requires an existing native identity in this source")
        previous_entry = copy.deepcopy(existing.get("entry") or {})
        previous_entry.update(payload.get("entry") or {})
        if "summary" not in (payload.get("entry") or {}):
            previous_entry["summary"] = text
        entry = normalize_entry(previous_entry, text)
        # Keep explicit clears in the delta; omission preserves mastery metadata
        # in the normal entry writer.
        for field, value in (payload.get("entry") or {}).items():
            if value == []:
                entry[field] = []
    if definition.source_format != "markdown":
        # The entry store consumes prepared Markdown evidence for native notes.
        # Capture does not convert or create that evidence as a side effect.
        resolve_entry_source(root, {
            "id": definition.id,
            "properties": (state.nodes.get(definition.id) or {}).get("properties", {}),
            "provenance": {"authority": relative.as_posix()},
        })
    request_id = f"capture-{uuid.uuid4().hex}"
    candidate_node: dict[str, Any] = {
        "id": definition.id, "type": "knowledge", "label": definition.label,
        "text": text, "entry": copy.deepcopy(entry),
        "properties": {"target_id": target_id} if target_id else {},
        "provenance": {
            "authority": relative.as_posix(), "line": definition.line,
            "source_format": definition.source_format,
        },
    }
    candidate = build_candidate_snapshot({
        "schema": CANDIDATE_SOURCE_SCHEMA, "namespace": request_id,
        "nodes": [candidate_node], "edges": [], "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    })
    report = compare(view, candidate)
    result = report["results"][0]
    if result["status"] == "ambiguous":
        raise CaptureError("identity is ambiguous; resolve it before capture")
    if action == "add" and result["status"] != "unmatched":
        raise CaptureError("identity already exists; review an update or a distinct scoped name")
    if action == "update" and (
        result["status"] != "matched" or result["identity_target_id"] != target_id
    ):
        raise CaptureError("comparison does not confirm the explicitly reviewed target")
    artifacts = {
        kind: output / f"{request_id}.{kind}.json"
        for kind in ("candidate", "comparison", "plan", "apply")
    }
    request = finalize_request({
        "schema": REQUEST_SCHEMA, "request_id": request_id, "mode": "plan",
        "capabilities": [CAPABILITY],
        "base_graph_sha256": report["target"]["graph_sha256"],
        "base_alignment_sha256": report["alignment_sha256"],
        "candidate_snapshot": {
            "path": artifacts["candidate"].relative_to(root).as_posix(),
            "sha256": candidate["snapshot_sha256"],
        },
        "query_report": {
            "path": artifacts["comparison"].relative_to(root).as_posix(),
            "sha256": sha256_json(report),
        },
        "authority_patches": [{
            "path": relative.as_posix(), "operation": "write",
            "expected_sha256": expected_source, "content": content,
            "content_sha256": sha256_text(content),
            "expected_markers": {
                "definitions": sorted(item.id for item in scan.definitions),
                "references": sorted(item.target for item in scan.references),
            },
        }],
        "decisions": [{
            "candidate_id": definition.id, "action": action,
            "target_id": definition.id, "evidence": evidence,
        }],
        "delta": {
            "schema": DELTA_SCHEMA, "remove_nodes": [], "remove_edges": [],
            "nodes": [{"id": definition.id, "text": text, "entry": entry}], "edges": [],
        },
        "alignment_decisions": [],
        "review": {
            "status": "reviewed", "reviewer": reviewer, "evidence": [evidence],
            "provenance": [{"path": relative.as_posix(), "line": definition.line, "kind": "authority"}],
        },
    })
    validate_request(request, mode="plan")
    apply_request = finalize_request({**request, "mode": "apply"})
    validate_request(apply_request, mode="apply")
    for kind, value in (
        ("candidate", candidate), ("comparison", report),
        ("plan", request), ("apply", apply_request),
    ):
        with artifacts[kind].open("x", encoding="utf-8") as handle:
            handle.write(pretty_json(value))
    return {
        "status": "prepared", "mode": "plan", "name": definition.label,
        "source": relative.as_posix(), "action": action,
        "artifacts": {kind: str(path) for kind, path in artifacts.items()},
        "counts": {"candidates": 1, "comparisons": 1, "entries": 1},
    }
