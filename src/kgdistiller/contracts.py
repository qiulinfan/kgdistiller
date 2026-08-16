"""Packaged versioned contracts and deterministic canonical JSON helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from .json_schema import SchemaViolation, validate_json_schema


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
MAX_NAMESPACE_LENGTH = 256
MAX_PORTABLE_PATH_BYTES = 4096
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
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
        "qlkg-knowledge-report-v1",
        "qlkg-vault-ingest-request-v1",
        "qlkg-vault-ingest-plan-v1",
        "qlkg-vault-ingest-receipt-v1",
        "qlkg-vault-ingest-report-v1",
        "qlkg-vault-ingest-error-v1",
        "qlkg-vault-ingest-journal-v1",
    )
}
SELF_DIGEST_FIELDS = {
    "qlkg-store-v2": "store_sha256",
    "qlkg-obsidian-projection-v1": "projection_sha256",
    "qlkg-static-export-v2": "export_sha256",
    "qlkg-site-graph-v1": "graph_sha256",
    "qlkg-vault-ingest-request-v1": "request_sha256",
    "qlkg-vault-ingest-plan-v1": "plan_sha256",
    "qlkg-vault-ingest-receipt-v1": "receipt_sha256",
    "qlkg-vault-ingest-journal-v1": "journal_sha256",
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


def _validate_portable_path(value: Any, *, field: str) -> None:
    """Enforce the host-neutral path rules shared by persisted F4 contracts."""

    if not isinstance(value, str) or not value:
        raise ContractError(f"{field} must be a non-empty portable relative path")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ContractError(f"{field} is not strict UTF-8") from error
    if (
        len(encoded) > MAX_PORTABLE_PATH_BYTES
        or "\0" in value
        or "\\" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ContractError(f"{field} is not a bounded portable relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
    ):
        raise ContractError(f"{field} is not a canonical relative path")
    if any(
        part.endswith((" ", "."))
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        or any(character in '<>:"|?*' for character in part)
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        for part in relative.parts
    ):
        raise ContractError(f"{field} is not portable across supported hosts")


def _validate_vault_ingest_paths(payload: dict[str, Any]) -> None:
    discriminator = payload.get("schema")
    paths: list[tuple[Any, str]] = []
    if discriminator == "qlkg-vault-ingest-request-v1":
        query_report = payload.get("query_report") or {}
        paths.append((query_report.get("path"), "query_report.path"))
        paths.extend(
            (item.get("path"), f"note_patches.{index}.path")
            for index, item in enumerate(payload.get("note_patches") or [])
        )
    elif discriminator == "qlkg-vault-ingest-plan-v1":
        changes = payload.get("changes") or {}
        paths.extend(
            (path, f"changes.note_paths.{index}")
            for index, path in enumerate(changes.get("note_paths") or [])
        )
    elif discriminator == "qlkg-vault-ingest-receipt-v1":
        changes = payload.get("changes") or {}
        paths.extend(
            (item.get("path"), f"changes.notes.{index}.path")
            for index, item in enumerate(changes.get("notes") or [])
        )
    elif discriminator == "qlkg-vault-ingest-report-v1":
        receipt_path = payload.get("receipt_path")
        if receipt_path is not None:
            paths.append((receipt_path, "receipt_path"))
    elif discriminator == "qlkg-vault-ingest-journal-v1":
        paths.extend(
            (path, f"planned_directories.{index}")
            for index, path in enumerate(payload.get("planned_directories") or [])
        )
        paths.extend(
            (item.get("path"), f"created_directories.{index}.path")
            for index, item in enumerate(payload.get("created_directories") or [])
        )
        for index, record in enumerate(payload.get("targets") or []):
            for key in ("path", "backup_path", "staged_path", "temporary_path"):
                value = record.get(key)
                if value is not None:
                    paths.append((value, f"targets.{index}.{key}"))
    for value, field in paths:
        _validate_portable_path(value, field=field)


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
    _validate_vault_ingest_paths(payload)
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
