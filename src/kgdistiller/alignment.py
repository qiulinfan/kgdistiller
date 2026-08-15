"""Provider-neutral contracts for scoped aliases and cross-graph alignments."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .contracts import MAX_NAMESPACE_LENGTH, canonical_json, sha256_json


ALIGNMENT_SCHEMA = "qlkg-alignments-v2"
ALIGNMENT_REPORT_SCHEMA = "qlkg-alignment-report-v2"
SCOPED_ALIAS_SCHEMA = "qlkg-scoped-aliases-v1"
ALIGNMENT_PREDICATES = {
    "exact-match",
    "close-match",
    "broad-match",
    "narrow-match",
    "related-match",
    "different-from",
}
ALIGNMENT_STATUSES = {
    "proposed",
    "reviewed",
    "rejected",
    "ambiguous",
    "deprecated",
}
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAMESPACE_RE = re.compile(
    rf"(?=.{{1,{MAX_NAMESPACE_LENGTH}}}\Z)[a-z0-9][a-z0-9._-]*"
    r"(?::[a-z0-9][a-z0-9._-]*)*"
)
NODE_ID_RE = re.compile(r"(?=.{1,256}\Z)[a-z0-9]+(?:-[a-z0-9]+)*")
SHORT_FORM_RE = re.compile(r"^[A-Z][A-Z0-9-]{1,15}$")
PARENTHETICAL_RE = re.compile(r"\((?P<inside>[^()\n]{2,120})\)")
REVERSE_DEFINITION_RE = re.compile(
    r"\b(?P<short>[A-Z][A-Z0-9-]{1,15})\s*\(\s*"
    r"(?P<long>[A-Za-z][A-Za-z0-9'’\- ]{2,100})\s*\)"
)
VERBAL_DEFINITION_RE = re.compile(
    r"\b(?P<short>[A-Z][A-Z0-9-]{1,15})\s+"
    r"(?:stands\s+for|denotes|means|is\s+short\s+for)\s+"
    r"(?P<long>[A-Za-z][A-Za-z0-9'’\- ]{2,100}?)(?=[.;,:\n]|$)",
    re.IGNORECASE,
)
ABBREVIATED_AS_RE = re.compile(
    r"(?P<long>[A-Za-z][A-Za-z0-9'’\- ]{2,100}?),?\s+"
    r"(?:abbreviated|written|referred\s+to)\s+as\s+"
    r"(?P<short>[A-Z][A-Z0-9-]{1,15})\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[A-Za-z0-9]+")


class AlignmentError(ValueError):
    """Raised when alignment data violates the deterministic contract."""


def normalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def node_fingerprint(node: dict[str, Any]) -> str:
    """Hash identity-relevant candidate content without document ordering."""
    payload = {
        "id": str(node.get("id", "")),
        "type": str(node.get("type", "")),
        "label": str(node.get("label", "")),
        "text": str(node.get("text", "")),
        "entry": node.get("entry") if isinstance(node.get("entry"), dict) else {},
        "properties": node.get("properties") if isinstance(node.get("properties"), dict) else {},
    }
    return sha256_json(payload)


def canonical_acronym(value: str) -> str:
    words = WORD_RE.findall(unicodedata.normalize("NFKC", value))
    if not words:
        return ""
    return "".join(word[0] for word in words if word).upper()


def _short_form(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _matches_short_form(short: str, long_form: str) -> bool:
    short_key = _short_form(short)
    if len(short_key) < 2:
        return False
    words = WORD_RE.findall(long_form)
    if not words:
        return False
    initials = "".join(word[0] for word in words).upper()
    if initials == short_key:
        return True
    cursor = len(long_form) - 1
    for character in reversed(short_key):
        while cursor >= 0 and long_form[cursor].upper() != character:
            cursor -= 1
        if cursor < 0:
            return False
        cursor -= 1
    return True


def _select_long_form(short: str, left_context: str) -> tuple[str, int] | None:
    matches = list(WORD_RE.finditer(left_context))
    if not matches:
        return None
    short_length = len(_short_form(short))
    maximum_words = min(len(matches), max(2 * short_length, short_length + 5, 2))
    for count in range(1, maximum_words + 1):
        start = matches[-count].start()
        candidate = left_context[start:].strip(" \t\n,;:-")
        if _matches_short_form(short, candidate):
            return candidate, start
    return None


def _node_texts(node: dict[str, Any]) -> Iterable[tuple[str, str]]:
    label = str(node.get("label", ""))
    if label:
        yield "label", label
    text = str(node.get("text", ""))
    if text and text != label:
        yield "text", text
    entry = node.get("entry")
    if isinstance(entry, dict):
        for key in ("summary", "definition", "context", "role"):
            value = entry.get(key)
            if isinstance(value, str) and value and value not in {label, text}:
                yield f"entry.{key}", value


def _alias_record(
    *,
    namespace: str,
    node: dict[str, Any],
    surface: str,
    expansion: str,
    field: str,
    source_text: str,
    start: int,
    end: int,
    kind: str,
) -> dict[str, Any]:
    provenance = node.get("provenance") or {}
    quote_start = max(0, start - 80)
    quote_end = min(len(source_text), end + 80)
    payload = {
        "namespace": namespace,
        "node_id": str(node.get("id", "")),
        "surface": surface.strip(),
        "normalized_surface": normalize_surface(surface),
        "expansion": expansion.strip(),
        "scope": {
            "authority": str(provenance.get("authority", "")),
            "field": field,
            "start": start,
            "end": end,
        },
        "evidence": {
            "kind": kind,
            "quote": source_text[quote_start:quote_end],
        },
    }
    payload["id"] = sha256_json(payload)[:24]
    return payload


def extract_scoped_aliases(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract explicit, document-local abbreviation definitions with evidence."""
    namespace = str(snapshot.get("namespace", ""))
    if not NAMESPACE_RE.fullmatch(namespace):
        raise AlignmentError(f"invalid alias namespace: {namespace!r}")
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    nodes = sorted(
        snapshot.get("nodes") or [], key=lambda item: str(item.get("id", ""))
    )
    for node in nodes:
        node_id = str(node.get("id", ""))
        if not node_id:
            raise AlignmentError("scoped alias node has no id")
        for field, source_text in _node_texts(node):
            for match in PARENTHETICAL_RE.finditer(source_text):
                short = match.group("inside").strip()
                if not SHORT_FORM_RE.fullmatch(short):
                    continue
                selected = _select_long_form(short, source_text[: match.start()])
                if selected is None:
                    continue
                long_form, long_start = selected
                record = _alias_record(
                    namespace=namespace,
                    node=node,
                    surface=short,
                    expansion=long_form,
                    field=field,
                    source_text=source_text,
                    start=long_start,
                    end=match.end(),
                    kind="parenthetical-abbreviation",
                )
                key = (
                    node_id,
                    record["normalized_surface"],
                    normalize_surface(long_form),
                )
                records[key] = record
            for pattern, kind in (
                (REVERSE_DEFINITION_RE, "reverse-parenthetical-abbreviation"),
                (VERBAL_DEFINITION_RE, "verbal-abbreviation-definition"),
                (ABBREVIATED_AS_RE, "abbreviated-as-definition"),
            ):
                for match in pattern.finditer(source_text):
                    short = match.group("short").strip()
                    long_form = match.group("long").strip(" \t\n,;:-")
                    if not SHORT_FORM_RE.fullmatch(short) or not _matches_short_form(
                        short, long_form
                    ):
                        continue
                    record = _alias_record(
                        namespace=namespace,
                        node=node,
                        surface=short,
                        expansion=long_form,
                        field=field,
                        source_text=source_text,
                        start=match.start(),
                        end=match.end(),
                        kind=kind,
                    )
                    key = (
                        node_id,
                        record["normalized_surface"],
                        normalize_surface(long_form),
                    )
                    records[key] = record
    aliases = sorted(
        records.values(),
        key=lambda item: (
            str(item["node_id"]),
            str(item["normalized_surface"]),
            str(item["expansion"]),
            str(item["id"]),
        ),
    )
    return {
        "schema": SCOPED_ALIAS_SCHEMA,
        "namespace": namespace,
        "aliases": aliases,
        "count": len(aliases),
    }


def empty_alignment_set() -> dict[str, Any]:
    return {"schema": ALIGNMENT_SCHEMA, "mappings": []}


def mapping_id(mapping: dict[str, Any]) -> str:
    subject = mapping.get("subject") or {}
    object_ = mapping.get("object") or {}
    payload = {
        "subject": {
            "namespace": str(subject.get("namespace", "")),
            "node_id": str(subject.get("node_id", "")),
        },
        "predicate": str(mapping.get("predicate", "")),
        "object": {
            "namespace": str(object_.get("namespace", "")),
            "node_id": str(object_.get("node_id", "")),
        },
    }
    return sha256_json(payload)[:24]


def validate_alignment_set(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return empty_alignment_set()
    if not isinstance(payload, dict) or payload.get("schema") != ALIGNMENT_SCHEMA:
        raise AlignmentError(f"expected {ALIGNMENT_SCHEMA} alignment registry")
    raw_mappings = payload.get("mappings", [])
    if not isinstance(raw_mappings, list):
        raise AlignmentError("alignment mappings must be an array")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str, str, str, str]] = set()
    reviewed_exact_targets: dict[tuple[str, str, str], str] = {}
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            raise AlignmentError("alignment mapping must be an object")
        subject = raw.get("subject") or {}
        object_ = raw.get("object") or {}
        subject_namespace = str(subject.get("namespace", ""))
        object_namespace = str(object_.get("namespace", ""))
        subject_id = str(subject.get("node_id", ""))
        object_id = str(object_.get("node_id", ""))
        if not NAMESPACE_RE.fullmatch(
            subject_namespace
        ) or not NAMESPACE_RE.fullmatch(object_namespace):
            raise AlignmentError("alignment mapping has an invalid namespace")
        if not NODE_ID_RE.fullmatch(subject_id) or not NODE_ID_RE.fullmatch(object_id):
            raise AlignmentError("alignment mapping has an invalid bounded node id")
        if subject_namespace == object_namespace and subject_id == object_id:
            raise AlignmentError("alignment mapping cannot map a node to itself")
        predicate = str(raw.get("predicate", ""))
        status = str(raw.get("status", ""))
        if predicate not in ALIGNMENT_PREDICATES:
            raise AlignmentError(f"unsupported alignment predicate: {predicate!r}")
        if status not in ALIGNMENT_STATUSES:
            raise AlignmentError(f"unsupported alignment status: {status!r}")
        justifications = sorted(
            dict.fromkeys(
                str(item).strip()
                for item in raw.get("mapping_justification", [])
                if str(item).strip()
            )
        )
        evidence = list(raw.get("evidence") or [])
        if status in {"reviewed", "rejected"} and (not justifications or not evidence):
            raise AlignmentError(f"{status} alignment requires justification and evidence")
        if any(not isinstance(item, dict) for item in evidence):
            raise AlignmentError("alignment evidence items must be objects")
        for endpoint in (subject, object_):
            fingerprint = str(endpoint.get("node_sha256", ""))
            if fingerprint and not HEX_SHA256_RE.fullmatch(fingerprint):
                raise AlignmentError("alignment node fingerprint must be a sha256")
        normalized = {
            "subject": {
                "namespace": subject_namespace,
                "node_id": subject_id,
                **(
                    {"node_sha256": str(subject["node_sha256"])}
                    if subject.get("node_sha256")
                    else {}
                ),
                **({"surface": str(subject["surface"])} if subject.get("surface") else {}),
            },
            "predicate": predicate,
            "object": {
                "namespace": object_namespace,
                "node_id": object_id,
                **(
                    {"node_sha256": str(object_["node_sha256"])}
                    if object_.get("node_sha256")
                    else {}
                ),
            },
            "status": status,
            "mapping_justification": justifications,
            "evidence": evidence,
        }
        if raw.get("scores") is not None:
            if not isinstance(raw["scores"], dict):
                raise AlignmentError("alignment scores must be an object")
            normalized["scores"] = raw["scores"]
        expected_id = mapping_id(normalized)
        supplied_id = str(raw.get("id", expected_id))
        if supplied_id != expected_id:
            raise AlignmentError(f"alignment id does not match endpoints: {supplied_id}")
        if supplied_id in seen_ids:
            raise AlignmentError(f"duplicate alignment id: {supplied_id}")
        pair = (subject_namespace, subject_id, predicate, object_namespace, object_id)
        if pair in seen_pairs:
            raise AlignmentError("duplicate alignment mapping")
        if status == "reviewed" and predicate == "exact-match":
            anchor_key = (subject_namespace, subject_id, object_namespace)
            previous_target = reviewed_exact_targets.get(anchor_key)
            if previous_target is not None and previous_target != object_id:
                raise AlignmentError(
                    "multiple reviewed exact targets for one alignment subject"
                )
            reviewed_exact_targets[anchor_key] = object_id
        seen_ids.add(supplied_id)
        seen_pairs.add(pair)
        normalized["id"] = supplied_id
        validated.append(normalized)
    validated.sort(key=lambda item: str(item["id"]))
    return {"schema": ALIGNMENT_SCHEMA, "mappings": validated}


def load_alignment_set(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return empty_alignment_set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlignmentError(f"cannot read alignment registry {path}: {error}") from error
    return validate_alignment_set(payload)


def make_reviewed_mapping(
    *,
    subject_namespace: str,
    subject_node: dict[str, Any],
    predicate: str,
    object_namespace: str,
    object_node: dict[str, Any],
    status: str,
    justification: str,
    evidence: str,
) -> dict[str, Any]:
    mapping = {
        "subject": {
            "namespace": subject_namespace,
            "node_id": str(subject_node["id"]),
            "node_sha256": node_fingerprint(subject_node),
        },
        "predicate": predicate,
        "object": {
            "namespace": object_namespace,
            "node_id": str(object_node["id"]),
            "node_sha256": node_fingerprint(object_node),
        },
        "status": status,
        "mapping_justification": [justification],
        "evidence": [{"kind": "review", "text": evidence}],
    }
    mapping["id"] = mapping_id(mapping)
    return validate_alignment_set({"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]})[
        "mappings"
    ][0]


def upsert_mapping(alignment_set: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    current = validate_alignment_set(alignment_set)
    replacement = validate_alignment_set(
        {"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]}
    )["mappings"][0]
    mappings = {
        str(item["id"]): item for item in current["mappings"]
    }
    mappings[str(replacement["id"])] = replacement
    return validate_alignment_set(
        {"schema": ALIGNMENT_SCHEMA, "mappings": list(mappings.values())}
    )
