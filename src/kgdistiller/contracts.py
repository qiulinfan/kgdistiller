"""Packaged versioned contracts and deterministic canonical JSON helpers."""

from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from typing import Any

from .json_schema import SchemaViolation, validate_json_schema


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
MAX_NAMESPACE_LENGTH = 256
CONTRACT_SCHEMAS = {
    name: f"{name}.schema.json"
    for name in (
        "kgdistiller-query-status-v1",
        "kgdistiller-retrieval-plan-v1",
        "kgdistiller-search-result-v1",
        "kgdistiller-search-execution-v1",
        "kgdistiller-document-record-v1",
        "kgdistiller-store-v1",
        "kgdistiller-store-report-v1",
        "kgdistiller-obsidian-projection-v1",
        "kgdistiller-obsidian-graph-v1",
        "kgdistiller-obsidian-export-report-v1",
        "kgdistiller-static-export-v1",
        "kgdistiller-static-export-report-v1",
        "kgdistiller-site-graph-v1",
    )
}
SELF_DIGEST_FIELDS = {
    "kgdistiller-store-v1": "store_sha256",
    "kgdistiller-obsidian-projection-v1": "projection_sha256",
    "kgdistiller-obsidian-graph-v1": "bundle_sha256",
    "kgdistiller-static-export-v1": "export_sha256",
    "kgdistiller-site-graph-v1": "graph_sha256",
}


class ContractError(ValueError):
    """Raised when a packaged closure contract fails closed."""


def canonical_json(value: Any) -> str:
    """Return the project's immutable UTF-8 canonical JSON representation."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ContractError(f"value is not finite canonical JSON: {error}") from error


def sha256_json(value: Any) -> str:
    """Hash canonical JSON bytes using lowercase SHA-256."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def self_digest(value: dict[str, Any], field: str) -> str:
    """Hash an object after omitting its own digest field."""
    payload = copy.deepcopy(value)
    payload.pop(field, None)
    return sha256_json(payload)


def finalize_self_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    """Return a copy with its canonical self-digest populated."""
    payload = copy.deepcopy(value)
    payload[field] = self_digest(payload, field)
    return payload


def parse_contract_json(text: str) -> Any:
    """Parse strict JSON, rejecting NaN and Infinity before schema validation."""

    def reject_constant(value: str) -> None:
        raise ContractError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except ContractError:
        raise
    except json.JSONDecodeError as error:
        raise ContractError(f"malformed contract JSON: {error.msg}") from error


def load_contract_schema(discriminator: str) -> dict[str, Any]:
    """Load one supported immutable schema from installed package resources."""
    filename = CONTRACT_SCHEMAS.get(discriminator)
    if filename is None:
        raise ContractError(f"unsupported contract schema: {discriminator!r}")
    resource = resources.files("kgdistiller").joinpath("schemas", filename)
    try:
        schema = parse_contract_json(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as error:
        raise ContractError(f"packaged contract schema is unavailable: {filename}") from error
    if not isinstance(schema, dict):
        raise ContractError(f"packaged contract schema is not an object: {filename}")
    if schema.get("$schema") != DRAFT_2020_12:
        raise ContractError(f"packaged contract schema is not Draft 2020-12: {filename}")
    discriminator_rule = (schema.get("properties") or {}).get("schema")
    if not isinstance(discriminator_rule, dict) or discriminator_rule.get("const") != discriminator:
        raise ContractError(f"packaged contract discriminator mismatch: {filename}")
    return schema


def _format_violation(error: SchemaViolation) -> str:
    path = ".".join(str(item) for item in error.path) or "contract"
    return f"contract JSON Schema violation at {path}: {error.message}"


def _validate_document_record(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-document-record-v1":
        return
    authority = str(payload.get("authority", ""))
    expected_suffix = {
        "markdown": ".md",
        "typst": ".typ",
        "latex": ".tex",
    }.get(
        payload.get("format")
    )
    if expected_suffix and not authority.endswith(expected_suffix):
        raise ContractError("document format must match the authority extension")


def _validate_search_execution(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-search-execution-v1":
        return
    resolutions = payload.get("identity_resolutions") or []
    indices = [resolution.get("query_index") for resolution in resolutions]
    if indices != list(range(len(resolutions))):
        raise ContractError(
            "identity resolution query_index values must be unique and contiguous"
        )
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("schema") != "kgdistiller-search-result-v1":
        raise ContractError(
            "kgdistiller-search-execution-v1 must contain kgdistiller-search-result-v1"
        )
    validate_contract(result)


def _validate_obsidian_graph(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-obsidian-graph-v1":
        return
    concepts = payload.get("concepts") or []
    sources = payload.get("sources") or []
    semantic_edges = payload.get("semantic_edges") or []
    definitions = payload.get("definitions") or []
    references = payload.get("references") or []
    expected_counts = {
        "concepts": len(concepts),
        "sources": len(sources),
        "semantic_edges": len(semantic_edges),
        "definitions": len(definitions),
        "references": len(references),
    }
    if payload.get("counts") != expected_counts:
        raise ContractError("Obsidian graph counts do not match its arrays")
    concept_ids = [str(item["id"]) for item in concepts]
    source_authorities = [str(item["authority"]) for item in sources]
    if len(concept_ids) != len(set(concept_ids)):
        raise ContractError("Obsidian graph contains duplicate concept IDs")
    if len(source_authorities) != len(set(source_authorities)):
        raise ContractError("Obsidian graph contains duplicate source authorities")
    concept_set = set(concept_ids)
    source_set = set(source_authorities)
    note_paths = [
        str(item["note_path"])
        for item in [*concepts, *sources]
    ]
    if len(note_paths) != len(set(note_paths)):
        raise ContractError("Obsidian graph contains duplicate note paths")
    if any(str(item["authority"]) not in source_set for item in concepts):
        raise ContractError("Obsidian graph concept has an unknown source authority")
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in semantic_edges:
        key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
        if key[0] not in concept_set or key[2] not in concept_set:
            raise ContractError("Obsidian graph semantic edge has an unknown endpoint")
        if key in edge_keys:
            raise ContractError("Obsidian graph contains duplicate semantic edges")
        edge_keys.add(key)
    definition_targets: set[str] = set()
    for definition in definitions:
        source = str(definition["source_authority"])
        target = str(definition["target"])
        if source not in source_set or target not in concept_set:
            raise ContractError("Obsidian graph definition has an unknown endpoint")
        if int(definition["line_end"]) < int(definition["line_start"]):
            raise ContractError("Obsidian graph definition line range is reversed")
        if target in definition_targets:
            raise ContractError("Obsidian graph concept has multiple definitions")
        definition_targets.add(target)
    if definition_targets != concept_set:
        raise ContractError("Obsidian graph concepts must each have one definition")
    reference_ids: set[str] = set()
    for reference in references:
        reference_id = str(reference["id"])
        if (
            str(reference["source_authority"]) not in source_set
            or str(reference["target"]) not in concept_set
        ):
            raise ContractError("Obsidian graph reference has an unknown endpoint")
        if reference_id in reference_ids:
            raise ContractError("Obsidian graph contains duplicate reference IDs")
        reference_ids.add(reference_id)


def validate_contract(payload: Any, *, verify_digest: bool = True) -> dict[str, Any]:
    """Validate a supported contract and its self-digest, failing closed."""
    if not isinstance(payload, dict):
        raise ContractError("contract payload must be an object")
    discriminator = payload.get("schema")
    if not isinstance(discriminator, str):
        raise ContractError("contract payload has no schema discriminator")
    schema = load_contract_schema(discriminator)
    try:
        errors = validate_json_schema(payload, schema)
    except (TypeError, ValueError) as error:
        raise ContractError(f"contract schema evaluation failed: {error}") from error
    if errors:
        raise ContractError(_format_violation(errors[0]))
    _validate_document_record(payload)
    _validate_search_execution(payload)
    _validate_obsidian_graph(payload)
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
