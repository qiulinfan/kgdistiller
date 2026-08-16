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
        "qlkg-recall-request-v1",
        "qlkg-recall-report-v1",
        "qlkg-recall-error-v1",
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


def _validate_recall_paths(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-recall-report-v1":
        return
    result = payload.get("result") or {}
    for index, node in enumerate(result.get("nodes") or []):
        authority = node.get("authority")
        if authority is not None:
            _validate_portable_path(authority, field=f"result.nodes.{index}.authority")
    for index, evidence in enumerate(result.get("evidence") or []):
        _validate_portable_path(
            evidence.get("source_path"),
            field=f"result.evidence.{index}.source_path",
        )


def _validate_recall_request(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-recall-request-v1":
        return
    texts = list(payload.get("queries") or [])
    if payload.get("query") is not None:
        texts.append(payload["query"])
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ContractError("recall query text must contain a non-whitespace character")


def _validate_recall_report(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-recall-report-v1":
        return
    result = payload.get("result") or {}
    vault_rows = payload.get("vaults") or []
    incomplete_rows = payload.get("incomplete_vaults") or []
    vault_ids = [str(row.get("vault_id")) for row in vault_rows]
    incomplete_ids = [str(row.get("vault_id")) for row in incomplete_rows]
    if len(vault_ids) != len(set(vault_ids)) or len(incomplete_ids) != len(set(incomplete_ids)):
        raise ContractError("recall Vault identities must be unique")
    if set(vault_ids) & set(incomplete_ids):
        raise ContractError("recall Vaults cannot be both complete and incomplete")
    if (payload.get("status") == "partial") != bool(incomplete_rows):
        raise ContractError("recall status does not match incomplete Vaults")
    expected_generation = sha256_json(
        {
            "registry_generation": payload.get("registry_generation"),
            "vaults": [
                {"vault_id": row.get("vault_id"), "generation": row.get("generation")}
                for row in vault_rows
            ],
            "incomplete_vaults": [
                {"vault_id": row.get("vault_id"), "code": row.get("code")}
                for row in incomplete_rows
            ],
        }
    )
    if payload.get("generation") != expected_generation:
        raise ContractError("recall generation does not match its Vault projection")
    if result.get("omissions") and not result.get("truncated"):
        raise ContractError("recall omissions require a truncated result")

    vault_id_set = set(vault_ids)
    for resolution in result.get("resolutions") or []:
        status = resolution.get("status")
        match_kind = resolution.get("match_kind")
        matches = resolution.get("matches") or []
        overflow = bool(resolution.get("overflow"))
        if any(str(match).partition(":")[0] not in vault_id_set for match in matches):
            raise ContractError("recall resolution references an unavailable Vault")
        if status == "missing":
            valid = not matches and match_kind is None and not overflow
        elif status == "alias":
            valid = len(matches) == 1 and match_kind == "alias" and not overflow
        elif status == "exact":
            valid = (
                len(matches) == 1
                and match_kind in {"id", "label"}
                and not overflow
            )
        else:
            valid = (
                (len(matches) >= 2 or overflow)
                and (
                    match_kind in {"id", "label", "alias", "mixed"}
                    or (not matches and overflow and match_kind is None)
                )
            )
        if not valid:
            raise ContractError("recall resolution fields are inconsistent")

    lane_order = {"identity": 0, "taxonomy": 1, "lexical": 2, "graph": 3}
    ranks: dict[str, set[int]] = {lane: set() for lane in lane_order}
    ranked_rows: dict[str, list[tuple[str, float, int]]] = {
        lane: [] for lane in lane_order
    }
    for node in result.get("nodes") or []:
        vault_id = str(node.get("vault_id"))
        handle = str(node.get("handle"))
        if handle != f"{vault_id}:{node.get('node_id')}":
            raise ContractError("recall node handle does not match its Vault and node identity")
        if vault_id not in vault_id_set:
            raise ContractError("recall node references an unavailable Vault")
        if any(str(parent).partition(":")[0] != vault_id for parent in node.get("parents") or []):
            raise ContractError("recall node parents must remain within one Vault")
        rows = node.get("lane_evidence") or []
        lanes = [row.get("lane") for row in rows]
        if len(lanes) != len(set(lanes)) or lanes != sorted(
            lanes, key=lambda lane: lane_order.get(str(lane), 99)
        ):
            raise ContractError("recall node lanes must be unique and canonically ordered")
        expected_score = round(sum(float(row.get("score", 0.0)) for row in rows), 12)
        if rows and abs(float(node.get("score", -1.0)) - expected_score) > 1e-9:
            raise ContractError("recall node score must equal its lane score sum")
        if not rows and node.get("score") is not None:
            raise ContractError("recall node without lanes must have a null score")
        for row in rows:
            lane = str(row.get("lane"))
            rank = int(row.get("rank", 0))
            if rank in ranks[lane]:
                raise ContractError("recall lane ranks must be unique in one report")
            ranks[lane].add(rank)
            ranked_rows[lane].append((handle, float(row.get("score", 0.0)), rank))
            reason = row.get("reason")
            match_kind = row.get("match_kind")
            fields = row.get("matched_fields") or []
            terms = row.get("matched_terms") or []
            scope = row.get("scope")
            seed = row.get("seed")
            path = row.get("path") or []
            if scope is not None and str(scope).partition(":")[0] != vault_id:
                raise ContractError("recall taxonomy scope crosses a Vault boundary")
            if seed is not None and str(seed).partition(":")[0] != vault_id:
                raise ContractError("recall graph seed crosses a Vault boundary")
            if any(
                str(step.get(endpoint)).partition(":")[0] != vault_id
                for step in path
                for endpoint in ("source", "target")
            ):
                raise ContractError("recall lane path crosses a Vault boundary")
            if lane == "identity":
                valid = (
                    (reason, match_kind) in {
                        ("exact-id", "id"),
                        ("exact-label", "label"),
                        ("reviewed-alias", "alias"),
                    }
                    and not fields and not terms and scope is None and seed is None and not path
                )
            elif lane == "taxonomy":
                valid = (
                    reason == "scope-member" and match_kind is None
                    and not fields and not terms and scope is not None and seed is None
                    and (
                        (not path and scope == handle)
                        or (
                            bool(path)
                            and path[0].get("source") == scope
                            and path[-1].get("target") == handle
                            and all(step.get("relation") == "contains" for step in path)
                            and all(
                                previous.get("target") == following.get("source")
                                for previous, following in zip(path, path[1:])
                            )
                        )
                    )
                )
            elif lane == "lexical":
                valid = (
                    reason in {"token-overlap", "phrase-match"}
                    and match_kind is None and bool(fields) and bool(terms)
                    and scope is None and seed is None and not path
                )
            else:
                cursor = seed
                connected = cursor is not None
                for step in path:
                    if step.get("source") == cursor:
                        cursor = step.get("target")
                    elif step.get("target") == cursor:
                        cursor = step.get("source")
                    else:
                        connected = False
                        break
                valid = (
                    reason in {"trusted-seed", "trusted-edge"}
                    and match_kind is None and not fields and not terms
                    and scope is None and seed is not None
                    and ((reason == "trusted-seed" and not path) or (reason == "trusted-edge" and bool(path)))
                    and connected
                    and cursor == handle
                )
            if not valid:
                raise ContractError("recall lane evidence fields are inconsistent")

    for lane, rows in ranked_rows.items():
        ordered = sorted(rows, key=lambda item: (-item[1], item[0]))
        if any(actual != expected for expected, (_, _, actual) in enumerate(ordered, 1)):
            raise ContractError(
                f"recall {lane} lane ranks must follow deterministic fusion order"
            )

    for edge in result.get("edges") or []:
        source_vault = str(edge.get("source")).partition(":")[0]
        target_vault = str(edge.get("target")).partition(":")[0]
        if source_vault != target_vault or source_vault not in vault_id_set:
            raise ContractError("recall edge crosses a Vault boundary")

    for evidence in result.get("evidence") or []:
        kind = evidence.get("kind")
        handle = evidence.get("handle")
        source = evidence.get("source")
        relation = evidence.get("relation")
        target = evidence.get("target")
        if kind == "concept":
            if source is not None or relation is not None or target is not None:
                raise ContractError("concept evidence must not contain relation endpoints")
        elif source is None or relation is None or target is None or handle != source:
            raise ContractError("relation evidence must bind its source handle and endpoints")
        handle_vault = str(handle).partition(":")[0]
        if handle_vault not in vault_id_set:
            raise ContractError("recall evidence references an unavailable Vault")
        if source is not None and (
            str(source).partition(":")[0] != handle_vault
            or str(target).partition(":")[0] != handle_vault
        ):
            raise ContractError("recall evidence crosses a Vault boundary")
        start_line = int(evidence.get("start_line", 0))
        end_line = int(evidence.get("end_line", 0))
        start_column = evidence.get("start_column")
        end_column = evidence.get("end_column")
        if end_line < start_line or ((start_column is None) != (end_column is None)):
            raise ContractError("recall evidence coordinates are inconsistent")
        if (
            start_column is not None
            and start_line == end_line
            and int(end_column) <= int(start_column)
        ):
            raise ContractError("recall evidence columns are reversed")
        if evidence.get("version_id") is not None and not str(evidence["version_id"]).startswith(
            f"doc:{evidence.get('document_id')}:"
        ):
            raise ContractError("recall evidence version does not match its document")
        excerpt = str(evidence.get("excerpt", ""))
        if evidence.get("excerpt_sha256") != hashlib.sha256(excerpt.encode("utf-8")).hexdigest():
            raise ContractError("recall evidence excerpt digest does not match")

    estimated_bytes = len(canonical_json(result).encode("utf-8"))
    if int(result.get("estimated_tokens", -1)) != estimated_bytes:
        raise ContractError("recall estimated_tokens must equal canonical result bytes")


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
    _validate_recall_request(payload)
    _validate_recall_paths(payload)
    _validate_recall_report(payload)
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
