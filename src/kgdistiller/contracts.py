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
        "qlkg-query-status-v1",
        "qlkg-retrieval-plan-v2",
        "qlkg-search-result-v3",
        "qlkg-search-execution-v2",
        "qlkg-document-record-v1",
        "qlkg-store-v2",
        "qlkg-store-report-v1",
        "qlkg-obsidian-projection-v1",
        "qlkg-obsidian-export-report-v1",
        "qlkg-static-export-v2",
        "qlkg-static-export-report-v1",
        "qlkg-site-graph-v1",
        "qlkg-vault-registry-v1",
        "qlkg-vault-v1",
        "qlkg-vault-report-v1",
        "qlkg-source-document-v1",
        "qlkg-source-version-v1",
        "qlkg-derivation-v1",
        "qlkg-source-ledger-v1",
        "qlkg-source-report-v1",
    )
}
SELF_DIGEST_FIELDS = {
    "qlkg-store-v2": "store_sha256",
    "qlkg-obsidian-projection-v1": "projection_sha256",
    "qlkg-static-export-v2": "export_sha256",
    "qlkg-site-graph-v1": "graph_sha256",
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
    if payload.get("schema") != "qlkg-document-record-v1":
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
    if payload.get("schema") != "qlkg-search-execution-v2":
        return
    resolutions = payload.get("identity_resolutions") or []
    indices = [resolution.get("query_index") for resolution in resolutions]
    if indices != list(range(len(resolutions))):
        raise ContractError(
            "identity resolution query_index values must be unique and contiguous"
        )
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("schema") != "qlkg-search-result-v3":
        raise ContractError(
            "qlkg-search-execution-v2 must contain qlkg-search-result-v3"
        )
    validate_contract(result)


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
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
