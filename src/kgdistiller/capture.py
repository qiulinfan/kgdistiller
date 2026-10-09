"""Prepare one reviewed knowledge entry for the existing ingest transaction."""

from __future__ import annotations

import copy
import difflib
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
from .document_types import load_document_types, validate_node_kind
from .entry_markdown import normalize_entry
from .ingest import (
    CAPABILITY,
    REQUEST_SCHEMA,
    IngestPaths,
    finalize_request,
    validate_request,
)
from .knowledge_paths import knowledge_root
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


def _prepare_capture_record(
    paths: IngestPaths, payload: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Validate one capture and return its independently reviewed source edit.

    ``payload`` selects ``name`` in one registered ``source``, supplies ``text``
    and optional ``entry`` and reviewed ``kind``, and carries an explicit ``review`` with ``action``
    (add/update), ``reviewer`` and ``evidence``. Updates use the selected native
    identity in the same source; an optional ``target_id`` must agree with it.
    Optional ``source_content`` or ``source_content_file`` contains
    a complete proposed native authority file; no marker is invented here.
    """
    if not isinstance(payload, dict):
        raise CaptureError("capture must be an object")
    unknown = set(payload) - {
        "name", "source", "text", "entry", "review", "source_content",
        "source_content_file", "kind",
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
    kind = None
    if "kind" in payload:
        kind = _text(payload, "kind")
        validate_node_kind(kind, owner.document_type, load_document_types(paths.registry))
    if any(output == spec.root or output.is_relative_to(spec.root) for spec in specs):
        raise CaptureError("output_dir must be outside registered source roots")
    for protected in (paths.graph_dir.resolve(), knowledge_root(root) / "entries", knowledge_root(root) / "derived/by-source"):
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
    state = load_state(paths.graph_dir, repo_root=paths.repo_root)
    identities = build_identity_index(state, load_identity_registry(paths.identities))
    view = GraphView.load(paths.graph_dir, paths.alignments, repo_root=paths.repo_root)
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
    return {
        "definition": definition, "scan": scan,
        "source": relative.as_posix(), "content": content,
        "expected_source": expected_source,
        "text": text, "entry": entry, "action": action, "kind": kind,
        "target_id": target_id, "reviewer": reviewer, "evidence": evidence,
        "base_graph": state.manifest["graph_sha256"],
    }


def _merge_source_content(original: str, contents: list[str]) -> str:
    """Combine independent edits against one base; only EOF insertions may meet."""
    base = original.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str]]] = []
    for content in contents:
        changed = content.splitlines(keepends=True)
        for tag, start, end, other_start, other_end in difflib.SequenceMatcher(
            a=base, b=changed, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            replacement = changed[other_start:other_end]
            for old_start, old_end, _ in edits:
                shared_append = start == end == old_start == old_end == len(base)
                intersects = max(start, old_start) < min(end, old_end)
                touches_insert = (
                    start == end and old_start <= start <= old_end
                ) or (old_start == old_end and start <= old_start <= end)
                if (intersects or touches_insert) and not shared_append:
                    raise CaptureError("selected source edits overlap; review one unambiguous batch")
            edits.append((start, end, replacement))
    result: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0]):
        result.extend(base[cursor:start])
        result.extend(replacement)
        cursor = end
    result.extend(base[cursor:])
    return "".join(result)


def prepare_captures(
    paths: IngestPaths, payloads: list[dict[str, Any]], output_dir: Path
) -> dict[str, Any]:
    """Prepare selected independently reviewed captures as one ingest transaction.

    Every proposed source copy is based on the current source and changes only
    its selected definition. Nonoverlapping edits combine; separate EOF additions
    concatenate in caller order. Other overlaps require another review.
    """
    if not isinstance(payloads, list) or not payloads:
        raise CaptureError("captures must be a non-empty array")
    records = [_prepare_capture_record(paths, payload, output_dir) for payload in payloads]
    root = paths.repo_root.resolve()
    output = _inside(root, output_dir, "output_dir")
    definitions = [record["definition"] for record in records]
    if len({definition.id for definition in definitions}) != len(definitions):
        raise CaptureError("selected captures contain duplicate native identities")
    view = GraphView.load(paths.graph_dir, paths.alignments, repo_root=paths.repo_root)
    if any(record["base_graph"] != view.snapshot["graph"]["sha256"] for record in records):
        raise CaptureError("knowledge changed while preparing; retry capture")
    state = load_state(paths.graph_dir, repo_root=paths.repo_root)
    identities = build_identity_index(state, load_identity_registry(paths.identities))
    specs = load_sources(root, paths.registry)
    authority_patches = []
    for source_name in dict.fromkeys(record["source"] for record in records):
        group = [record for record in records if record["source"] == source_name]
        source = root / source_name
        actual = sha256_authority_file(source) if source.is_file() else None
        if any(record["expected_source"] != actual for record in group):
            raise CaptureError("source changed while preparing; retry capture")
        if len(group) == 1:
            content = group[0]["content"]
        else:
            original = source.read_text(encoding="utf-8") if source.is_file() else ""
            content = _merge_source_content(original, [record["content"] for record in group])
        owner = unique_source_for_path(specs, source)
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=output) as temporary:
            shadow = Path(temporary)
            staged_source = shadow / source_name
            staged_source.parent.mkdir(parents=True, exist_ok=True)
            staged_source.write_text(content, encoding="utf-8")
            scan = scan_source(shadow, replace(owner, root=shadow / owner.root.relative_to(root)), staged_source, identities)
        expected = {
            item.id: item.definition_sha256
            for item in (scan_source(root, owner, source, identities).definitions if source.is_file() else [])
        }
        expected.update({record["definition"].id: record["definition"].definition_sha256 for record in group})
        found = {item.id: item.definition_sha256 for item in scan.definitions}
        if scan.errors or found != expected or len(found) != len(scan.definitions):
            raise CaptureError("combined source changes the reviewed definitions; review the source edits")
        authority_patches.append({
            "path": source_name, "operation": "write", "expected_sha256": actual,
            "content": content, "content_sha256": sha256_text(content),
            "expected_markers": {
                "definitions": sorted(found),
                "references": sorted(item.target for item in scan.references),
            },
        })
    request_id = f"capture-{uuid.uuid4().hex}"
    candidate_nodes = [{
        "id": record["definition"].id, "type": "knowledge", "label": record["definition"].label,
        "text": record["text"], "entry": copy.deepcopy(record["entry"]),
        "properties": {
            **({"target_id": record["target_id"]} if record["target_id"] else {}),
            **({"kind": record["kind"]} if record["kind"] is not None else {}),
        },
        "provenance": {
            "authority": record["source"], "line": record["definition"].line,
            "source_format": record["definition"].source_format,
        },
    } for record in records]
    candidate = build_candidate_snapshot({
        "schema": CANDIDATE_SOURCE_SCHEMA, "namespace": request_id,
        "nodes": candidate_nodes, "edges": [], "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    })
    report = compare(view, candidate)
    results = {result["candidate"]["id"]: result for result in report["results"]}
    for record in records:
        result = results[record["definition"].id]
        if result["status"] == "ambiguous":
            raise CaptureError("identity is ambiguous; resolve it before capture")
        if record["action"] == "add" and result["status"] != "unmatched":
            raise CaptureError("identity already exists; review an update or a distinct scoped name")
        if record["action"] == "update" and (
            result["status"] != "matched" or result["identity_target_id"] != record["target_id"]
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
        "authority_patches": authority_patches,
        "decisions": [{
            "candidate_id": record["definition"].id, "action": record["action"],
            "target_id": record["definition"].id, "evidence": record["evidence"],
        } for record in records],
        "delta": {
            "schema": DELTA_SCHEMA, "remove_nodes": [], "remove_edges": [],
            "nodes": [{
                "id": record["definition"].id, "text": record["text"], "entry": record["entry"],
                **({"properties": {"kind": record["kind"]}} if record["kind"] is not None else {}),
            } for record in records],
            "edges": [],
        },
        "alignment_decisions": [],
        "review": {
            "status": "reviewed", "reviewer": ", ".join(dict.fromkeys(record["reviewer"] for record in records)),
            "evidence": [record["evidence"] for record in records],
            "provenance": [{"path": record["source"], "line": record["definition"].line, "kind": "authority"} for record in records],
        },
    })
    validate_request(request, mode="plan")
    apply_request = finalize_request({**request, "mode": "apply"})
    validate_request(apply_request, mode="apply")
    for kind, value in (("candidate", candidate), ("comparison", report), ("plan", request), ("apply", apply_request)):
        with artifacts[kind].open("x", encoding="utf-8") as handle:
            handle.write(pretty_json(value))
    return {
        "status": "prepared", "mode": "plan",
        "artifacts": {kind: str(path) for kind, path in artifacts.items()},
        "counts": {"candidates": len(records), "comparisons": 1, "entries": len(records)},
    }


def prepare_capture(paths: IngestPaths, payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Prepare one explicitly reviewed source-backed capture without applying it."""
    result = prepare_captures(paths, [payload], output_dir)
    result.update(name=payload["name"].strip(), source=_inside(paths.repo_root.resolve(), payload["source"], "source").relative_to(paths.repo_root.resolve()).as_posix(), action=payload["review"]["action"])
    return result
