"""Lossless, provider-neutral packing of validated raw-source retrieval results.

Source and heading metadata are shared, while scientific text is never edited.
A bundle checks projection integrity and provenance closure, not relevance,
scientific truth, or consistency with current source files. Callers that need
that last assurance must retrieve through an index that checks its files.
"""
from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from typing import Any

from .contracts import ContractError, canonical_json, sha256_json
from .json_schema import validate_json_schema
from .source_evidence import (
    PROJECTION,
    RESULT_SCHEMA,
    SourceEvidenceError,
    _finalize_budget,
    _projection,
    validate_source_evidence_result,
)

CONTEXT_SCHEMA = "kgdistiller-source-evidence-context-v1"
CONTEXT_PROJECTION = "kgdistiller-source-evidence-context-projection-v1"
MAX_SOURCE_RESULTS = 32
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_BYTES = MAX_INPUT_BYTES
_SOURCE_KEYS = (
    "doc_id", "source_url", "source_version", "source_type", "hash_mode",
    "source_sha256", "raw_source_sha256", "normalized_source_sha256",
)
_RESULT_KEYS = (
    "query", "query_sha256", "doc_ids", "fragment_count", "matched_fragments",
    "requested_limit", "omitted_fragments", "diagnostics_truncated",
    "scoring", "budget",
)


def _error(message: str, code: str = "invalid-source-context") -> SourceEvidenceError:
    return SourceEvidenceError(code, message)


def _canonical(value: Any) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except (ContractError, UnicodeError, RecursionError) as error:
        raise _error("source context requires finite canonical UTF-8 JSON") from error


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_inputs(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bytes]:
    if not isinstance(results, list) or not 1 <= len(results) <= MAX_SOURCE_RESULTS:
        raise _error(f"source context requires 1..{MAX_SOURCE_RESULTS} retrieval results")
    original = _canonical(results)
    if len(original) > MAX_INPUT_BYTES:
        raise _error("source retrieval inputs exceed the 4 MiB canonical byte bound", "source-context-input-too-large")
    snapshot = json.loads(original)
    for result in snapshot:
        validate_source_evidence_result(result)
    if _canonical(results) != original:
        raise _error("source retrieval inputs changed during validation", "stale-source-context")
    return snapshot, original


def _heading_record(doc_id: str, heading: dict[str, Any]) -> dict[str, Any]:
    body = {"source_ref": doc_id, **copy.deepcopy(heading)}
    return {"heading_id": "heading:sha256:" + sha256_json(body), **body}


def _collect(results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    binding = (results[0]["manifest_sha256"], results[0]["manifest_file_sha256"], results[0]["projection"])
    sources: dict[str, Any] = {}
    fragments: dict[str, Any] = {}
    headings: dict[tuple[str, int, int], Any] = {}
    retrievals = []
    origins: dict[str, Any] = {}
    for result_index, result in enumerate(results):
        if (result["manifest_sha256"], result["manifest_file_sha256"], result["projection"]) != binding:
            raise _error("retrieval results do not share manifest and source projection bindings")
        retrieval = {"result_index": result_index, "result_sha256": sha256_json(result),
                     **{key: copy.deepcopy(result[key]) for key in _RESULT_KEYS},
                     "returned_fragments": len(result["fragments"]), "reported_omissions": len(result["omissions"])}
        for fragment in result["fragments"]:
            doc_id, fragment_id = fragment["doc_id"], fragment["fragment_id"]
            source = {key: copy.deepcopy(fragment[key]) for key in _SOURCE_KEYS}
            if doc_id in sources and sources[doc_id] != source:
                raise _error("source metadata conflicts for the same document")
            sources[doc_id] = source
            projection = _projection(fragment)
            if fragment_id in fragments and _projection(fragments[fragment_id]) != projection:
                raise _error("the same fragment ID has conflicting source content")
            # Dict insertion order preserves the first retrieval's baseline order.
            if fragment_id not in fragments:
                fragments[fragment_id] = copy.deepcopy(fragment)
            for heading in fragment["heading_context"]:
                key = (doc_id, heading["byte_start"], heading["byte_end"])
                if key in headings and headings[key] != heading:
                    raise _error("heading metadata conflicts for the same source span")
                headings[key] = copy.deepcopy(heading)
            origins.setdefault(fragment_id, []).append({
                "result_index": result_index, "rank": fragment["rank"], "score": fragment["score"],
            })
        retrievals.append(retrieval)
    return sources, fragments, retrievals, origins


def _gaps(retrievals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = []
    for retrieval in retrievals:
        selected_count = min(retrieval["requested_limit"], retrieval["matched_fragments"])
        if retrieval["omitted_fragments"]:
            gaps.append({"result_index": retrieval["result_index"], "reason": "retrieval-byte-budget",
                         "fragment_count": retrieval["omitted_fragments"],
                         "reported_fragment_ids": [], "diagnostics_truncated": True})
        if retrieval["matched_fragments"] > selected_count:
            gaps.append({"result_index": retrieval["result_index"], "reason": "retrieval-limit",
                         "fragment_count": retrieval["matched_fragments"] - selected_count,
                         "reported_fragment_ids": [], "diagnostics_truncated": False})
    return gaps


def _selected_ids(selected: list[str] | None, fragments: dict[str, Any]) -> list[str]:
    if selected is None:
        return list(fragments)
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise _error("selected fragment IDs must be a list of exact retrieval IDs")
    if len(set(selected)) != len(selected) or any(item not in fragments for item in selected):
        raise _error("selected fragment IDs must be unique and come from the retrieval inputs")
    return list(selected)


def _compact_fragment(fragment: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    headings = [_heading_record(fragment["doc_id"], item) for item in fragment["heading_context"]]
    compact = {key: copy.deepcopy(value) for key, value in fragment.items()
               if key not in {*_SOURCE_KEYS, "heading_context", "rank", "score"}}
    compact["source_ref"] = fragment["doc_id"]
    compact["heading_refs"] = [heading["heading_id"] for heading in headings]
    return compact, headings


def _list_extra_bytes(existing: list[Any], added: list[Any]) -> int:
    """Exact canonical size increase, including array separators."""
    if not added:
        return 0
    return sum(len(_canonical(item)) for item in added) + len(added) - (0 if existing else 1)


def _size_after_delta(old_size: int, delta: int) -> int:
    # used_bytes describes its own serialized digits. Other field changes are
    # measured as canonical bytes; settle the digits without recopying a large
    # bundle for every candidate.
    fixed = old_size + delta - len(str(old_size))
    size = fixed + len(str(old_size))
    while size != fixed + len(str(size)):
        size = fixed + len(str(size))
    return size


def _pack(results: list[dict[str, Any]], original: bytes, *, byte_budget: int,
          selected_fragment_ids: list[str] | None) -> dict[str, Any]:
    sources, fragments, retrievals, origins = _collect(results)
    selected = _selected_ids(selected_fragment_ids, fragments)
    selected_set = set(selected)
    payload = {
        "schema": CONTEXT_SCHEMA, "projection": CONTEXT_PROJECTION,
        "source_projection": results[0]["projection"],
        "manifest_sha256": results[0]["manifest_sha256"],
        "manifest_file_sha256": results[0]["manifest_file_sha256"],
        "input_sha256": _sha(original), "input_bytes": len(original),
        "source_kind": "raw-source-evidence", "identity_authority": False,
        "complete_definition": False, "candidate_count": len(fragments),
        "selected_fragment_ids": None if selected_fragment_ids is None else selected,
        "selection_sha256": sha256_json(selected), "selected_fragment_count": len(selected),
        "retrievals": retrievals, "sources": [], "headings": [], "fragments": [],
        "omitted_fragments": len(fragments), "not_selected_fragments": len(fragments) - len(selected),
        "budget_omitted_fragments": len(selected), "omissions": [],
        "diagnostics_truncated": bool(fragments),
        "gaps": _gaps(retrievals), "budget": {"byte_budget": byte_budget, "used_bytes": 0},
    }
    if _finalize_budget(payload) > byte_budget:
        raise _error("source context budget cannot hold required provenance metadata", "source-budget-too-small")
    packed_sources, heading_indices, packed_ids = set(), {}, set()
    for fragment_id in selected:
        fragment = fragments[fragment_id]
        compact, headings = _compact_fragment(fragment)
        compact["origins"] = copy.deepcopy(origins[fragment_id])
        added_sources = [] if fragment["doc_id"] in packed_sources else [sources[fragment["doc_id"]]]
        added_headings = [heading for heading in headings if heading["heading_id"] not in heading_indices]
        new_indices = {heading["heading_id"]: len(payload["headings"]) + index for index, heading in enumerate(added_headings)}
        compact["heading_refs"] = [heading_indices[heading["heading_id"]] if heading["heading_id"] in heading_indices else new_indices[heading["heading_id"]] for heading in headings]
        old_count, old_budget_count = payload["omitted_fragments"], payload["budget_omitted_fragments"]
        delta = (_list_extra_bytes(payload["sources"], added_sources)
                 + _list_extra_bytes(payload["headings"], added_headings)
                 + _list_extra_bytes(payload["fragments"], [compact])
                 + len(str(old_count - 1)) - len(str(old_count))
                 + len(str(old_budget_count - 1)) - len(str(old_budget_count))
                 + (1 if old_count == 1 else 0))  # true -> false gains one byte.
        candidate_size = _size_after_delta(payload["budget"]["used_bytes"], delta)
        if candidate_size <= byte_budget:
            payload["sources"].extend(copy.deepcopy(added_sources))
            payload["headings"].extend(added_headings)
            payload["fragments"].append(compact)
            payload["omitted_fragments"] -= 1
            payload["budget_omitted_fragments"] -= 1
            payload["diagnostics_truncated"] = bool(payload["omitted_fragments"])
            payload["budget"]["used_bytes"] = candidate_size
            packed_sources.add(fragment["doc_id"])
            heading_indices.update(new_indices)
            packed_ids.add(fragment_id)
    # Diagnostics use only the remaining budget. The full unselected ledger is
    # bound by input/result hashes and is available through source-bound replay.
    for fragment_id in fragments:
        if fragment_id in packed_ids:
            continue
        omission = {"fragment_id": fragment_id,
                    "reason": "fragment-exceeds-byte-budget" if fragment_id in selected_set else "not-selected"}
        last = len(payload["omissions"]) + 1 == payload["omitted_fragments"]
        delta = _list_extra_bytes(payload["omissions"], [omission]) + (1 if last else 0)
        candidate_size = _size_after_delta(payload["budget"]["used_bytes"], delta)
        if candidate_size <= byte_budget:
            payload["omissions"].append(omission)
            payload["diagnostics_truncated"] = not last
            payload["budget"]["used_bytes"] = candidate_size
        else:
            break
    _finalize_budget(payload)
    return payload


def build_source_context(results: list[dict[str, Any]], *, byte_budget: int = 24000,
                         selected_fragment_ids: list[str] | None = None) -> dict[str, Any]:
    """Pack complete fragments, sharing exact source/heading metadata once.

    Selection chooses a unique candidate subset and its packing order. It does
    not attest coverage. Rank/score remain local to each original query; this
    function supplies no fused relevance score and performs no filesystem I/O.
    """
    if isinstance(byte_budget, bool) or not isinstance(byte_budget, int) or not 1 <= byte_budget <= MAX_CONTEXT_BYTES:
        raise _error("source context byte budget must be an integer in 1..4 MiB")
    snapshot, original = _snapshot_inputs(results)
    payload = _pack(snapshot, original, byte_budget=byte_budget, selected_fragment_ids=selected_fragment_ids)
    validate_source_context(payload)
    if _canonical(results) != original:
        raise _error("source retrieval inputs changed while building context", "stale-source-context")
    return payload


def _expanded(fragment: dict[str, Any], sources: dict[str, Any], headings: dict[int, Any]) -> dict[str, Any]:
    source_ref = fragment["source_ref"]
    if source_ref not in sources or any(reference not in headings for reference in fragment["heading_refs"]):
        raise _error("fragment has an unknown source or heading reference")
    if any(headings[reference]["source_ref"] != source_ref for reference in fragment["heading_refs"]):
        raise _error("fragment heading references cross source scope")
    expanded = {key: copy.deepcopy(value) for key, value in fragment.items() if key not in {"source_ref", "heading_refs", "origins"}}
    expanded.update(copy.deepcopy(sources[source_ref]))
    expanded["heading_context"] = [
        {key: copy.deepcopy(value) for key, value in headings[reference].items() if key not in {"heading_id", "source_ref"}}
        for reference in fragment["heading_refs"]
    ]
    return expanded


def _validate_fragment_projection(fragment: dict[str, Any]) -> None:
    # Reuse the existing raw-fragment contract rather than inventing weaker span
    # or content-address rules for the compact serialization.
    result = {"schema": RESULT_SCHEMA, "projection": PROJECTION,
              "query": "context-projection-validation", "query_sha256": _sha(b"context-projection-validation"),
              "manifest_sha256": "0" * 64, "manifest_file_sha256": "0" * 64,
              "source_kind": "raw-source-evidence", "identity_authority": False,
              "complete_definition": False, "doc_ids": [fragment["doc_id"]],
              "fragment_count": 1, "matched_fragments": 1, "requested_limit": 1,
              "fragments": [{**fragment, "rank": 1, "score": 1.0}],
              "omitted_fragments": 0, "diagnostics_truncated": False, "omissions": [],
              "budget": {"byte_budget": 200000, "used_bytes": 0},
              "scoring": {"method": "bm25", "k1": 1.2, "b": 0.75, "heading_weight": 2, "identity_authority": False}}
    _finalize_budget(result)
    validate_source_evidence_result(result)


def validate_source_context(payload: dict[str, Any], source_results: list[dict[str, Any]] | None = None) -> None:
    """Check schema, lossless projection closure, provenance and exact budget.

    Supplying original results additionally verifies every compacted or omitted
    field by deterministic replay. Neither mode verifies current source files
    or establishes scientific truth or relevance.
    """
    actual = _canonical(payload)
    schema = json.loads(resources.files("kgdistiller").joinpath("schemas", CONTEXT_SCHEMA + ".schema.json").read_text(encoding="utf-8"))
    errors = validate_json_schema(payload, schema)
    if errors:
        error = errors[0]
        raise _error(f"{CONTEXT_SCHEMA} at {'.'.join(map(str, error.path))}: {error.message}")
    if len(actual) != payload["budget"]["used_bytes"] or len(actual) > payload["budget"]["byte_budget"]:
        raise _error("source context exceeds its declared byte budget or used_bytes is false")
    sources = {source["doc_id"]: source for source in payload["sources"]}
    headings = dict(enumerate(payload["headings"]))
    if len(sources) != len(payload["sources"]) or len({heading["heading_id"] for heading in headings.values()}) != len(headings):
        raise _error("source or heading tables contain duplicate references")
    heading_spans = set()
    for heading in headings.values():
        body = {key: value for key, value in heading.items() if key != "heading_id"}
        span = (heading["source_ref"], heading["byte_start"], heading["byte_end"])
        if heading["source_ref"] not in sources or heading["heading_id"] != "heading:sha256:" + sha256_json(body) or span in heading_spans:
            raise _error("heading table content address or source-span closure is invalid")
        heading_spans.add(span)
    for index, retrieval in enumerate(payload["retrievals"]):
        selected_count = min(retrieval["requested_limit"], retrieval["matched_fragments"])
        if (retrieval["result_index"] != index or retrieval["query_sha256"] != _sha(retrieval["query"].encode("utf-8"))
                or len(set(retrieval["doc_ids"])) != len(retrieval["doc_ids"])
                or retrieval["matched_fragments"] > retrieval["fragment_count"]
                or retrieval["omitted_fragments"] != selected_count - retrieval["returned_fragments"]
                or retrieval["omitted_fragments"] < retrieval["reported_omissions"]
                or retrieval["budget"]["used_bytes"] > retrieval["budget"]["byte_budget"]):
            raise _error("retrieval query or original counts are inconsistent")
    # Canonical result objects already include their own declared used_bytes.
    # The enclosing JSON array adds two brackets and one comma per boundary.
    if payload["input_bytes"] != sum(retrieval["budget"]["used_bytes"] for retrieval in payload["retrievals"]) + len(payload["retrievals"]) + 1:
        raise _error("source context input_bytes conflicts with original result budgets")
    fragment_ids = [fragment["fragment_id"] for fragment in payload["fragments"]]
    omission_ids = [omission["fragment_id"] for omission in payload["omissions"]]
    selected = payload["selected_fragment_ids"]
    selected_count = payload["selected_fragment_count"]
    if selected is not None:
        if len(set(selected)) != len(selected) or selected_count != len(selected) or payload["selection_sha256"] != sha256_json(selected):
            raise _error("explicit fragment selection digest or count is inconsistent")
        if fragment_ids != [item for item in selected if item in set(fragment_ids)]:
            raise _error("packed fragments do not follow caller selection order")
    elif selected_count != payload["candidate_count"]:
        raise _error("baseline selection count does not equal available candidates")
    returned_counts = [retrieval["returned_fragments"] for retrieval in payload["retrievals"]]
    if (not max(returned_counts) <= payload["candidate_count"] <= sum(returned_counts)
            or selected_count > payload["candidate_count"]
            or len(set(fragment_ids)) != len(fragment_ids) or len(set(omission_ids)) != len(omission_ids)
            or set(fragment_ids) & set(omission_ids)
            or payload["omitted_fragments"] != payload["candidate_count"] - len(fragment_ids)
            or payload["not_selected_fragments"] != payload["candidate_count"] - selected_count
            or payload["budget_omitted_fragments"] != selected_count - len(fragment_ids)
            or payload["omitted_fragments"] < len(omission_ids)
            or payload["diagnostics_truncated"] != (len(omission_ids) < payload["omitted_fragments"])
            or payload["gaps"] != _gaps(payload["retrievals"])):
        raise _error("source context candidate, selection or omission counts are inconsistent")
    for omission in payload["omissions"]:
        expected = "fragment-exceeds-byte-budget" if selected is None or omission["fragment_id"] in selected else "not-selected"
        if omission["reason"] != expected:
            raise _error("source context omission reason conflicts with its selection")
    used_sources, used_headings, origin_ranks, baseline_order = set(), set(), set(), []
    origin_counts = [0] * len(payload["retrievals"])
    for fragment in payload["fragments"]:
        origin_indices = [origin["result_index"] for origin in fragment["origins"]]
        if origin_indices != sorted(set(origin_indices)):
            raise _error("fragment origins must reference unique retrievals in input order")
        for origin in fragment["origins"]:
            index = origin["result_index"]
            if index >= len(payload["retrievals"]):
                raise _error("fragment origin references an unknown retrieval")
            retrieval = payload["retrievals"][index]
            rank_key = (index, origin["rank"])
            if (origin["rank"] > min(retrieval["requested_limit"], retrieval["matched_fragments"])
                    or fragment["source_ref"] not in retrieval["doc_ids"] or rank_key in origin_ranks):
                raise _error("fragment origin rank or source scope is inconsistent")
            origin_ranks.add(rank_key)
            origin_counts[index] += 1
        baseline_order.append((fragment["origins"][0]["result_index"], fragment["origins"][0]["rank"]))
        expanded = _expanded(fragment, sources, headings)
        _validate_fragment_projection(expanded)
        used_sources.add(fragment["source_ref"])
        used_headings.update(fragment["heading_refs"])
    if selected is None and baseline_order != sorted(baseline_order):
        raise _error("packed fragments do not preserve first-result baseline order")
    if any(not max(0, returned - payload["omitted_fragments"]) <= count <= returned
           for count, returned in zip(origin_counts, returned_counts)):
        raise _error("fragment origin counts conflict with original returned counts")
    if used_sources != set(sources) or used_headings != set(headings):
        raise _error("source context metadata tables contain unreferenced records")
    if source_results is not None:
        snapshot, original = _snapshot_inputs(source_results)
        expected = _pack(snapshot, original, byte_budget=payload["budget"]["byte_budget"], selected_fragment_ids=selected)
        if _canonical(expected) != actual:
            raise _error("source context does not match the supplied original retrieval results")
        if _canonical(source_results) != original:
            raise _error("source retrieval inputs changed during context validation", "stale-source-context")
