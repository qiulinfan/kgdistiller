"""Resolve syntactic references to manifest-registered source documents only.

No title, method, keyword, graph identity or alias inference is performed. A
standalone result proves its own declaration and projection closure. Current
manifest membership and file correspondence require validation with its index.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from importlib import resources
from typing import Any

from .contracts import ContractError, canonical_json, self_digest, sha256_json
from .json_schema import validate_json_schema
from .source_evidence import SourceEvidenceError, SourceEvidenceIndex

RESULT_SCHEMA = "kgdistiller-source-reference-result-v1"
SELECTOR_POLICY = "registered-source-reference-selectors-v1"
MAX_HINTS = 128
MAX_HINT_LENGTH = 4096
MAX_RESULT_BYTES = 4 * 1024 * 1024
_SEPARATOR = re.compile(r"[ \t]+|-|@")
_VERSION_SUFFIX = re.compile(r"v[0-9]+$")
_DOCUMENT_KEYS = ("doc_id", "source_version", "source_url", "expected_sha256")


def _error(message: str, code: str = "invalid-source-reference-result") -> SourceEvidenceError:
    return SourceEvidenceError(code, message)


def _canonical(value: Any) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except (ContractError, UnicodeError, RecursionError) as error:
        raise _error("source references require finite canonical UTF-8 JSON") from error


def _hint_snapshot(hints: list[str]) -> tuple[list[str], bytes]:
    if (not isinstance(hints, list) or not 1 <= len(hints) <= MAX_HINTS
            or any(not isinstance(hint, str) or not 1 <= len(hint) <= MAX_HINT_LENGTH for hint in hints)):
        raise _error(f"source references require 1..{MAX_HINTS} hints of 1..{MAX_HINT_LENGTH} characters",
                     "invalid-source-reference-hints")
    original = _canonical(hints)
    return json.loads(original), original


def _doc_key(value: str) -> str:
    # Keep the established surface rule: no stripping or aliases.
    return unicodedata.normalize("NFKC", value).casefold().replace("-", "").replace("_", "")


def _match_type(hint: str, document: dict[str, Any]) -> str | None:
    if _doc_key(hint) == _doc_key(document["doc_id"]):
        return "exact-normalized-doc-id"
    if hint == document["source_version"]:
        return "exact-declared-source-version"
    suffix = _VERSION_SUFFIX.search(document["source_version"])
    versions = {document["source_version"]}
    if suffix:
        versions.add(suffix.group())
    # Try syntactic boundaries against registered metadata. Hyphens within a
    # doc_id or full version remain intact; any valid interpretation is kept.
    for separator in _SEPARATOR.finditer(hint):
        doc_selector = hint[:separator.start()].rstrip(" \t")
        version_selector = hint[separator.end():].lstrip(" \t")
        if version_selector in versions and _doc_key(doc_selector) == _doc_key(document["doc_id"]):
            return "qualified-doc-id-version"
    return None


def _resolve_manifest(manifest: dict[str, Any], manifest_file_sha256: str,
                      hints: list[str]) -> dict[str, Any]:
    documents = sorted(manifest["documents"], key=lambda document: document["doc_id"])
    resolutions, matched_doc_ids = [], []
    for hint_index, hint in enumerate(hints):
        candidates = []
        for document in documents:
            match_type = _match_type(hint, document)
            if match_type:
                candidates.append({**{key: copy.deepcopy(document[key]) for key in _DOCUMENT_KEYS},
                                   "match_type": match_type, "identity_authority": False})
        status = "matched" if len(candidates) == 1 else ("ambiguous" if candidates else "unmatched")
        if status == "matched" and candidates[0]["doc_id"] not in matched_doc_ids:
            matched_doc_ids.append(candidates[0]["doc_id"])
        resolutions.append({"hint_index": hint_index, "hint": hint,
                            "hint_sha256": hashlib.sha256(hint.encode("utf-8")).hexdigest(),
                            "status": status, "candidate_count": len(candidates),
                            "candidates": candidates, "identity_authority": False})
    result = {
        "schema": RESULT_SCHEMA, "selector_policy": SELECTOR_POLICY,
        "reference_kind": "registered-source-document", "identity_authority": False,
        "correspondence_claim": "standalone-projection-closure-only; current-manifest-and-files-require-index",
        "manifest_sha256": sha256_json(manifest), "manifest_file_sha256": manifest_file_sha256,
        "hash_mode": manifest["hash_mode"], "document_count": len(documents),
        "hints": copy.deepcopy(hints), "hints_sha256": sha256_json(hints),
        "hint_count": len(hints), "resolutions": resolutions,
        "matched_count": sum(row["status"] == "matched" for row in resolutions),
        "ambiguous_count": sum(row["status"] == "ambiguous" for row in resolutions),
        "unmatched_count": sum(row["status"] == "unmatched" for row in resolutions),
        "matched_doc_ids": matched_doc_ids,
    }
    result["result_sha256"] = sha256_json(result)
    if len(_canonical(result)) > MAX_RESULT_BYTES:
        raise _error("source reference result exceeds the 4 MiB canonical byte bound",
                     "source-reference-result-too-large")
    return result


def resolve_source_references(index: SourceEvidenceIndex, hints: list[str]) -> dict[str, Any]:
    """Resolve exact document IDs and declared-version selectors, with guards.

    Full versions match literally. A qualified selector uses a space, tab,
    hyphen or @ boundary after a normalized registered doc_id, followed by its
    literal full source_version or trailing vN suffix. Unknown/wrong versions
    remain unmatched; ambiguities are never automatically scoped.
    """
    if not isinstance(index, SourceEvidenceIndex):
        raise _error("source reference resolution requires a SourceEvidenceIndex",
                     "invalid-source-reference-index")
    snapshot, original = _hint_snapshot(hints)
    index._check_sources()
    result = _resolve_manifest(index.manifest, index.manifest_file_sha256, snapshot)
    validate_source_reference_result(result)
    index._check_sources()
    if _canonical(hints) != original:
        raise _error("source reference hints changed during resolution", "stale-source-reference-hints")
    return result


def validate_source_reference_result(payload: dict[str, Any], index: SourceEvidenceIndex | None = None) -> None:
    """Validate finite bounds, hashes, declarations and selector closure.

    An index additionally establishes exact full-manifest selector replay and
    checks current source files before and after validation. Without an index,
    declarations cannot establish manifest membership or absence of a match.
    """
    actual = _canonical(payload)
    if len(actual) > MAX_RESULT_BYTES:
        raise _error("source reference result exceeds the 4 MiB canonical byte bound",
                     "source-reference-result-too-large")
    resource = resources.files("kgdistiller").joinpath("schemas", RESULT_SCHEMA + ".schema.json")
    errors = validate_json_schema(payload, json.loads(resource.read_text(encoding="utf-8")))
    if errors:
        violation = errors[0]
        raise _error(f"{RESULT_SCHEMA} at {'.'.join(map(str, violation.path))}: {violation.message}")
    snapshot = json.loads(actual)
    hints, _ = _hint_snapshot(snapshot["hints"])
    resolutions = snapshot["resolutions"]
    if (snapshot["result_sha256"] != self_digest(snapshot, "result_sha256")
            or snapshot["hints_sha256"] != sha256_json(hints)
            or snapshot["hint_count"] != len(hints)
            or len(resolutions) != len(hints)):
        raise _error("source reference hint/result digests or counts are inconsistent")
    declarations, matched_doc_ids = {}, []
    counts = {status: 0 for status in ("matched", "ambiguous", "unmatched")}
    for hint_index, row in enumerate(resolutions):
        hint = hints[hint_index]
        candidates = row["candidates"]
        status = "matched" if len(candidates) == 1 else ("ambiguous" if candidates else "unmatched")
        candidate_ids = [candidate["doc_id"] for candidate in candidates]
        if (row["hint_index"] != hint_index or row["hint"] != hint
                or row["hint_sha256"] != hashlib.sha256(hint.encode("utf-8")).hexdigest()
                or row["candidate_count"] != len(candidates)
                or len(candidates) > snapshot["document_count"]
                or candidate_ids != sorted(set(candidate_ids)) or row["status"] != status):
            raise _error("source reference row status, order, scope or counts are inconsistent")
        for candidate in candidates:
            document = {key: candidate[key] for key in _DOCUMENT_KEYS}
            if candidate["doc_id"] in declarations and declarations[candidate["doc_id"]] != document:
                raise _error("source document declarations conflict across hints")
            declarations[candidate["doc_id"]] = document
            if _match_type(hint, document) != candidate["match_type"]:
                raise _error("source reference candidate does not satisfy its declared selector")
        counts[status] += 1
        if status == "matched" and candidates[0]["doc_id"] not in matched_doc_ids:
            matched_doc_ids.append(candidates[0]["doc_id"])
    if (len(declarations) > snapshot["document_count"] or snapshot["matched_doc_ids"] != matched_doc_ids
            or any(snapshot[status + "_count"] != count for status, count in counts.items())):
        raise _error("source reference aggregate counts or matched document scope are inconsistent")
    if index is not None:
        if not isinstance(index, SourceEvidenceIndex):
            raise _error("source reference validation requires a SourceEvidenceIndex",
                         "invalid-source-reference-index")
        index._check_sources()
        expected = _resolve_manifest(index.manifest, index.manifest_file_sha256, hints)
        if snapshot != expected:
            raise _error("source reference result does not match the current full manifest",
                         "stale-source-reference-result")
        index._check_sources()
    if _canonical(payload) != actual:
        raise _error("source reference result changed during validation", "stale-source-reference-result")
