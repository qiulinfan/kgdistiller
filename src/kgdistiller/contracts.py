"""Packaged versioned contracts and deterministic canonical JSON helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from .json_schema import SchemaViolation, validate_json_schema


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
CONTRACT_SCHEMAS = {
    name: f"{name}.schema.json"
    for name in (
        "qlkg-local-profile-v1",
        "qlkg-embedding-policy-v1",
        "qlkg-retrieval-plan-v1",
        "qlkg-search-result-v2",
        "qlkg-document-record-v2",
        "qlkg-document-upsert-request-v1",
        "qlkg-document-ingest-receipt-v1",
    )
}
SELF_DIGEST_FIELDS = {
    "qlkg-document-upsert-request-v1": "request_sha256",
    "qlkg-document-ingest-receipt-v1": "receipt_sha256",
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


def _validate_policy_profile_names(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-embedding-policy-v1":
        return
    names = [profile.get("name") for profile in payload.get("profiles", [])]
    if len(names) != len(set(names)):
        raise ContractError("embedding policy profile names must be unique")


def _validate_local_profile(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-local-profile-v1":
        return
    selected = payload.get("embedding_profile")
    profiles = payload.get("provider_profiles") or {}
    if selected not in profiles:
        raise ContractError("embedding_profile must name a provider_profiles entry")


def _validate_document_record(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-document-record-v2":
        return
    authority = payload.get("authority")
    if authority in (payload.get("authority_history") or []):
        raise ContractError("authority_history must not repeat the current authority")
    expected_suffix = {"md": ".md", "typ": ".typ", "tex": ".tex"}.get(
        payload.get("format")
    )
    if expected_suffix and not str(authority).endswith(expected_suffix):
        raise ContractError("document format must match the authority extension")
    for name, value in (payload.get("external_ids") or {}).items():
        if isinstance(value, str) and value != value.strip():
            raise ContractError(f"external_ids.{name} must be normalized")
        if name in {"doi", "arxiv"} and isinstance(value, str) and value != value.lower():
            raise ContractError(f"external_ids.{name} must be lowercase normalized")


def _glob_variants(pattern: str) -> set[str]:
    variants = {pattern}
    pending = [pattern]
    while pending:
        candidate = pending.pop()
        marker = "**/"
        offset = candidate.find(marker)
        if offset < 0:
            continue
        shortened = candidate[:offset] + candidate[offset + len(marker) :]
        if shortened not in variants:
            variants.add(shortened)
            pending.append(shortened)
    return variants


def _validate_upsert_source(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-document-upsert-request-v1":
        return
    source = payload.get("source") or {}
    authority = str(source.get("authority", ""))
    registered_glob = str(source.get("registered_glob", ""))
    wildcard_offsets = [
        offset for token in ("*", "?", "[") if (offset := registered_glob.find(token)) >= 0
    ]
    static_prefix = registered_glob[: min(wildcard_offsets)] if wildcard_offsets else registered_glob
    if not static_prefix or static_prefix.startswith("/"):
        raise ContractError("registered_glob must have a bounded relative prefix")
    try:
        matches_registered_glob = any(
            PurePosixPath(authority).match(candidate)
            for candidate in _glob_variants(registered_glob)
        )
    except (re.error, ValueError) as error:
        raise ContractError("registered_glob is malformed") from error
    if not matches_registered_glob:
        raise ContractError("authority must match its registered bounded glob")
    expected_suffix = {"md": ".md", "typ": ".typ", "tex": ".tex"}.get(
        source.get("format")
    )
    if expected_suffix and not authority.endswith(expected_suffix):
        raise ContractError("source format must match the authority extension")
    artifacts = payload.get("artifacts") or {}
    preconditions = payload.get("preconditions") or {}
    query = artifacts.get("query") or {}
    if query.get("sha256") != preconditions.get("query_sha256"):
        raise ContractError("query artifact digest must match its exact precondition")


def _validate_receipt_state(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-document-ingest-receipt-v1":
        return
    overall = payload.get("overall_status")
    stages = payload.get("stages") or {}
    authority = (stages.get("authority_graph") or {}).get("status")
    embeddings = (stages.get("embeddings") or {}).get("status")
    portable = (stages.get("portable") or {}).get("status")
    materialization = (stages.get("materialization") or {}).get("status")
    git_ready = payload.get("git_ready")
    if overall == "ready" and (
        authority != "committed"
        or embeddings not in {"complete", "not-required"}
        or portable != "verified"
        or materialization != "current"
        or git_ready is not True
    ):
        raise ContractError("ready receipt requires every stage ready and git_ready=true")
    if overall == "rejected" and (authority != "rejected" or git_ready is not False):
        raise ContractError("rejected receipt requires rejected authority_graph and git_ready=false")
    if authority == "committed" and overall == "failed":
        raise ContractError("a post-commit failure must be reported as degraded, not failed")
    if overall != "ready" and git_ready is not False:
        raise ContractError("non-ready receipt must set git_ready=false")
    operation = (payload.get("document") or {}).get("operation")
    if overall == "ready" and operation in {"ambiguous", "rejected"}:
        raise ContractError("ready receipt cannot report an ambiguous or rejected document")


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
    _validate_local_profile(payload)
    _validate_policy_profile_names(payload)
    _validate_document_record(payload)
    _validate_upsert_source(payload)
    _validate_receipt_state(payload)
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
