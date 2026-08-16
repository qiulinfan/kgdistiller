"""Packaged versioned contracts and deterministic canonical JSON helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from .json_schema import SchemaViolation, validate_json_schema


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
MAX_NAMESPACE_LENGTH = 256
MAX_PORTABLE_PATH_BYTES = 4096
MAX_VAULT_STORE_BYTES = 8 * 1024 * 1024 * 1024
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
        "qlkg-api-response-v1",
        "qlkg-api-error-v1",
        "qlkg-vault-store-v3",
        "qlkg-vault-store-report-v1",
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
    "qlkg-vault-store-v3": "store_sha256",
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


def _validate_vault_ingest_receipt(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-vault-ingest-receipt-v1":
        return

    summaries = payload["after"]["derivations"]
    summary_ids = [str(item["version_id"]) for item in summaries]
    if summary_ids != sorted(summary_ids) or len(summary_ids) != len(set(summary_ids)):
        raise ContractError("receipt derivation summaries are not canonical")
    if summary_ids != list(payload["changes"]["derivation_version_ids"]):
        raise ContractError("receipt derivation summary IDs do not match its changes")

    def require_sorted_unique(
        values: list[dict[str, Any]],
        keys: tuple[str, ...],
        *,
        identity_keys: tuple[str, ...],
        field: str,
    ) -> None:
        ordered = sorted(
            values,
            key=lambda item: tuple(str(item[key]) for key in keys)
            + (canonical_json(item),),
        )
        identities = [tuple(str(item[key]) for key in identity_keys) for item in values]
        if values != ordered or len(identities) != len(set(identities)):
            raise ContractError(f"receipt {field} records are not canonical")

    for summary in summaries:
        candidates = summary["candidate_dispositions"]
        require_sorted_unique(
            candidates,
            ("candidate_id", "disposition"),
            identity_keys=("candidate_id",),
            field="candidate disposition",
        )
        concept_ids = list(summary["concept_ids"])
        if concept_ids != sorted(concept_ids) or len(concept_ids) != len(set(concept_ids)):
            raise ContractError("receipt concept IDs are not canonical")
        concept_evidence = summary["concept_evidence"]
        require_sorted_unique(
            concept_evidence,
            ("concept_id",),
            identity_keys=("concept_id",),
            field="concept evidence",
        )
        evidence_ids = [str(item["concept_id"]) for item in concept_evidence]
        if set(evidence_ids) != set(concept_ids):
            raise ContractError("receipt concept evidence does not match its concept IDs")

        relation_evidence = summary["relation_evidence"]
        relation_identities: list[tuple[str, str, str]] = []
        for item in relation_evidence:
            source = str(item["source"])
            relation = str(item["relation"])
            target = str(item["target"])
            if relation == "contrasts-with":
                canonical_source, canonical_target = sorted((source, target))
                if (source, target) != (canonical_source, canonical_target):
                    raise ContractError("receipt relation evidence is not canonical")
            relation_identities.append((source, relation, target))
        ordered_relations = sorted(
            relation_evidence,
            key=lambda item: (
                str(item["source"]),
                str(item["relation"]),
                str(item["target"]),
                canonical_json(item),
            ),
        )
        if (
            relation_evidence != ordered_relations
            or len(relation_identities) != len(set(relation_identities))
        ):
            raise ContractError("receipt relation evidence records are not canonical")


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


def _validate_retrieval_rows(
    result: dict[str, Any], vault_ids: set[str], *, label: str
) -> None:
    lane_order = {"identity": 0, "taxonomy": 1, "lexical": 2, "graph": 3}
    ranked_rows: dict[str, list[tuple[str, float, int]]] = {
        lane: [] for lane in lane_order
    }
    for node in result.get("nodes") or []:
        vault_id = str(node.get("vault_id"))
        handle = str(node.get("handle"))
        if handle != f"{vault_id}:{node.get('node_id')}" or vault_id not in vault_ids:
            raise ContractError(
                f"{label} node handle identity is unavailable or inconsistent"
            )
        if any(
            str(parent).partition(":")[0] != vault_id
            for parent in node.get("parents") or []
        ):
            raise ContractError(f"{label} node parents cross a Vault boundary")
        rows = node.get("lane_evidence") or []
        lanes = [str(row.get("lane")) for row in rows]
        if len(lanes) != len(set(lanes)) or lanes != sorted(
            lanes, key=lambda lane: lane_order.get(lane, 99)
        ):
            raise ContractError(
                f"{label} node lane names are not unique or canonical"
            )
        expected_score = round(sum(float(row.get("score", 0.0)) for row in rows), 12)
        if rows and abs(float(node.get("score", -1.0)) - expected_score) > 1e-9:
            raise ContractError(f"{label} node score does not match its lanes")
        if not rows and node.get("score") is not None:
            raise ContractError(f"{label} node without lanes has a nonnull score")
        for row in rows:
            lane = str(row.get("lane"))
            rank = int(row.get("rank", 0))
            ranked_rows[lane].append((handle, float(row.get("score", 0.0)), rank))
            reason = row.get("reason")
            match_kind = row.get("match_kind")
            fields = row.get("matched_fields") or []
            terms = row.get("matched_terms") or []
            scope = row.get("scope")
            seed = row.get("seed")
            path = row.get("path") or []
            if scope is not None and str(scope).partition(":")[0] != vault_id:
                raise ContractError(f"{label} taxonomy scope crosses a Vault boundary")
            if seed is not None and str(seed).partition(":")[0] != vault_id:
                raise ContractError(f"{label} graph seed crosses a Vault boundary")
            if any(
                str(step.get(endpoint)).partition(":")[0] != vault_id
                for step in path
                for endpoint in ("source", "target")
            ):
                raise ContractError(f"{label} lane path crosses a Vault boundary")
            if lane == "identity":
                valid = (
                    (reason, match_kind)
                    in {
                        ("exact-id", "id"),
                        ("exact-label", "label"),
                        ("reviewed-alias", "alias"),
                    }
                    and not fields
                    and not terms
                    and scope is None
                    and seed is None
                    and not path
                )
            elif lane == "taxonomy":
                valid = (
                    reason == "scope-member"
                    and match_kind is None
                    and not fields
                    and not terms
                    and scope is not None
                    and seed is None
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
                    and match_kind is None
                    and bool(fields)
                    and bool(terms)
                    and scope is None
                    and seed is None
                    and not path
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
                    and match_kind is None
                    and not fields
                    and not terms
                    and scope is None
                    and seed is not None
                    and (
                        (reason == "trusted-seed" and not path)
                        or (reason == "trusted-edge" and bool(path))
                    )
                    and connected
                    and cursor == handle
                )
            if not valid:
                raise ContractError(f"{label} lane evidence is inconsistent")
    for lane, rows in ranked_rows.items():
        ordered = sorted(rows, key=lambda item: (-item[1], item[0]))
        if any(actual != expected for expected, (_, _, actual) in enumerate(ordered, 1)):
            raise ContractError(
                f"{label} fusion order for {lane} ranks is not deterministic and contiguous"
            )

    for edge in result.get("edges") or []:
        source_vault = str(edge.get("source")).partition(":")[0]
        target_vault = str(edge.get("target")).partition(":")[0]
        if source_vault != target_vault or source_vault not in vault_ids:
            raise ContractError(f"{label} edge crosses a Vault boundary")

    for evidence in result.get("evidence") or []:
        kind = evidence.get("kind")
        handle = evidence.get("handle")
        source = evidence.get("source")
        relation = evidence.get("relation")
        target = evidence.get("target")
        if kind == "concept":
            if source is not None or relation is not None or target is not None:
                raise ContractError(f"{label} concept evidence has relation endpoints")
        elif source is None or relation is None or target is None or handle != source:
            raise ContractError(f"{label} relation evidence is not source-bound")
        handle_vault = str(handle).partition(":")[0]
        if handle_vault not in vault_ids:
            raise ContractError(f"{label} evidence references an unavailable Vault")
        if source is not None and (
            str(source).partition(":")[0] != handle_vault
            or str(target).partition(":")[0] != handle_vault
        ):
            raise ContractError(f"{label} evidence crosses a Vault boundary")
        start_line = int(evidence.get("start_line", 0))
        end_line = int(evidence.get("end_line", 0))
        start_column = evidence.get("start_column")
        end_column = evidence.get("end_column")
        if end_line < start_line or ((start_column is None) != (end_column is None)):
            raise ContractError(f"{label} evidence coordinates are inconsistent")
        if (
            start_column is not None
            and start_line == end_line
            and int(end_column) <= int(start_column)
        ):
            raise ContractError(f"{label} evidence columns are reversed")
        if not str(evidence.get("version_id", "")).startswith(
            f"doc:{evidence.get('document_id')}:"
        ):
            raise ContractError(f"{label} evidence version belongs to another document")
        excerpt = str(evidence.get("excerpt", ""))
        if evidence.get("excerpt_sha256") != hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest():
            raise ContractError(f"{label} evidence excerpt digest does not match")


def _validate_retrieval_controls(
    result: Mapping[str, Any], vault_ids: set[str], *, label: str
) -> None:
    if result.get("omissions") and not result.get("truncated"):
        raise ContractError(f"{label} omissions require a truncated result")
    for resolution in result.get("resolutions") or []:
        status = resolution.get("status")
        match_kind = resolution.get("match_kind")
        matches = resolution.get("matches") or []
        overflow = bool(resolution.get("overflow"))
        if any(str(match).partition(":")[0] not in vault_ids for match in matches):
            raise ContractError(f"{label} resolution references an unavailable Vault")
        if status == "missing":
            valid = not matches and match_kind is None and not overflow
        elif status == "alias":
            valid = len(matches) == 1 and match_kind == "alias" and not overflow
        elif status == "exact":
            valid = len(matches) == 1 and match_kind in {"id", "label"} and not overflow
        else:
            valid = (
                (len(matches) >= 2 or overflow)
                and (
                    match_kind in {"id", "label", "alias", "mixed"}
                    or (not matches and overflow and match_kind is None)
                )
            )
        if not valid:
            raise ContractError(f"{label} resolution fields are inconsistent")


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
    vault_id_set = set(vault_ids)
    _validate_retrieval_rows(result, vault_id_set, label="recall")
    _validate_retrieval_controls(result, vault_id_set, label="recall")

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


def _validate_api_response(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-api-response-v1":
        return
    route = str(payload["route"])
    result = payload["result"]
    if result["kind"] != route:
        raise ContractError("API result kind does not match its route")

    vaults = payload["vault_generations"]
    vault_ids = [str(item["vault_id"]) for item in vaults]
    incomplete = payload["incomplete_vaults"]
    incomplete_ids = [str(item["vault_id"]) for item in incomplete]
    if (
        vault_ids != sorted(vault_ids)
        or incomplete_ids != sorted(incomplete_ids)
        or len(vault_ids) != len(set(vault_ids))
        or len(incomplete_ids) != len(set(incomplete_ids))
        or bool(set(vault_ids) & set(incomplete_ids))
    ):
        raise ContractError("API Vault generation rows are not canonical")
    if (payload["status"] == "partial") != bool(incomplete):
        raise ContractError("API response status does not match incomplete Vaults")
    for item in vaults:
        expected_vault_generation = sha256_json(
            {
                "vault_manifest_sha256": item["vault_manifest_sha256"],
                "graph_manifest_sha256": item["graph_manifest_sha256"],
                "graph_sha256": item["graph_sha256"],
                "source_ledger_generation_sha256": item[
                    "source_ledger_generation_sha256"
                ],
                "authority_generation_sha256": item["authority_generation_sha256"],
            }
        )
        if item["generation"] != expected_vault_generation:
            raise ContractError("API Vault generation row is not canonical")
    generation = sha256_json(
        {
            "registry_generation": payload["registry_generation"],
            "vaults": [
                {"vault_id": item["vault_id"], "generation": item["generation"]}
                for item in vaults
            ],
            "incomplete_vaults": [
                {"vault_id": item["vault_id"], "code": item["code"]}
                for item in incomplete
            ],
        }
    )
    if payload["generation"] != generation:
        raise ContractError("API federation generation is not canonical")

    complete = set(vault_ids)

    def split_handle(handle: str, field: str) -> tuple[str, str]:
        vault_id, node_id = handle.split(":", 1)
        if vault_id not in complete:
            raise ContractError(f"{field} belongs to an unavailable Vault")
        return vault_id, node_id

    def check_node(node: dict[str, Any], field: str) -> None:
        vault_id, node_id = split_handle(str(node["handle"]), field)
        if node["vault_id"] != vault_id or node["node_id"] != node_id:
            raise ContractError(f"{field} handle does not match its identity")
        authority = node.get("authority")
        if authority is not None:
            _validate_portable_path(authority, field=f"{field}.authority")
        if "provenance" in node or "open_actions" in node:
            provenance = node.get("provenance")
            open_action = node.get("open_actions")
            if authority is None:
                if provenance is not None or open_action is not None:
                    raise ContractError(
                        f"{field} provenance must be absent without an authority"
                    )
            elif provenance is None or open_action is None:
                raise ContractError(
                    f"{field} authority must have provenance and an open action"
                )
            else:
                provenance_authority = str(provenance["authority"])
                action_authority = str(open_action["authority"])
                _validate_portable_path(
                    provenance_authority, field=f"{field}.provenance.authority"
                )
                _validate_portable_path(
                    action_authority, field=f"{field}.open_actions.authority"
                )
                if provenance_authority != authority or action_authority != authority:
                    raise ContractError(
                        f"{field} provenance and open action must bind its authority"
                    )
                line = int(provenance["line"])
                if int(open_action["line"]) != line:
                    raise ContractError(
                        f"{field} provenance and open action line must match"
                    )
                if not (
                    int(provenance["definition_start_line"])
                    <= line
                    <= int(provenance["definition_end_line"])
                ):
                    raise ContractError(
                        f"{field} provenance line is outside its definition span"
                    )
        for parent in node.get("parents") or []:
            parent_vault, _ = split_handle(str(parent), f"{field}.parents")
            if parent_vault != vault_id:
                raise ContractError(f"{field} contains a cross-Vault taxonomy parent")

    node_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = list(result.get("edges") or [])
    evidence_rows: list[dict[str, Any]] = list(result.get("evidence") or [])
    if route == "roots":
        node_rows.extend(result["nodes"])
    elif route == "stale":
        stale_keys: list[str] = []
        for item in result["items"]:
            if item["kind"] == "node":
                node = item["node"]
                node_rows.append(node)
                stale_keys.append(f"node/{node['handle']}")
                reason = item["reason"]
                if (
                    (reason == "needs-review" and node["curation_status"] != "needs-review")
                    or (reason == "pending" and node["curation_status"] != "pending")
                    or (reason == "orphaned" and node["source_status"] != "orphaned")
                ):
                    raise ContractError("API stale node reason does not match its state")
            elif item["kind"] == "edge":
                edge = item["edge"]
                edge_rows.append(edge)
                stale_keys.append(
                    f"edge/{edge['source']}/{edge['relation']}/{edge['target']}"
                )
                if edge["curation_status"] != "needs-review":
                    raise ContractError("API stale edge is not marked needs-review")
            else:
                source = item["source"]
                stale_keys.append(
                    f"source/{source['vault_id']}/{source['document_id']}"
                )
                if source["vault_id"] not in complete:
                    raise ContractError("API stale source belongs to an unavailable Vault")
                _validate_portable_path(source["path"], field="result.items.source.path")
                if source["status"] != item["reason"]:
                    raise ContractError("API stale source reason does not match its state")
        if stale_keys != sorted(stale_keys) or len(stale_keys) != len(set(stale_keys)):
            raise ContractError("API stale items are not canonically ordered and unique")
        if result["truncated"]:
            if not stale_keys or result["next_cursor"] != stale_keys[-1]:
                raise ContractError("API stale cursor does not match its final item")
        elif result["next_cursor"] is not None:
            raise ContractError("untruncated API stale result has a cursor")
    elif route == "node":
        node_rows.append(result["node"])
    elif route == "neighbors":
        split_handle(str(result["center"]), "result.center")
        node_rows.extend(result["nodes"])
    elif route in {"search", "context"}:
        node_rows.extend(result["nodes"])
    for index, node in enumerate(node_rows):
        check_node(node, f"result.nodes.{index}")
    _validate_retrieval_rows(
        {"nodes": node_rows, "edges": edge_rows, "evidence": evidence_rows},
        complete,
        label="API",
    )
    _validate_retrieval_controls(result, complete, label="API")

    for index, edge in enumerate(edge_rows):
        source_vault, _ = split_handle(
            str(edge["source"]), f"result.edges.{index}.source"
        )
        target_vault, _ = split_handle(
            str(edge["target"]), f"result.edges.{index}.target"
        )
        if source_vault != target_vault:
            raise ContractError("API graph edge crosses Vault identities")

    if route == "status" and (
        result["api_version"] != 1
        or result["read_only"] is not True
        or result["registered_vaults"] != len(vaults) + len(incomplete)
        or result["healthy_vaults"] != len(vaults)
        or result["incomplete_vaults"] != len(incomplete)
    ):
        raise ContractError("API status fields do not match its generation rows")
    if route == "vaults":
        if [item["vault_id"] for item in result["vaults"]] != vault_ids:
            raise ContractError("API Vault cards do not match its generation rows")
        generation_fields = (
            "vault_id",
            "generation",
            "vault_manifest_sha256",
            "graph_manifest_sha256",
            "graph_sha256",
            "source_ledger_generation_sha256",
            "authority_generation_sha256",
            "live_source_generation_sha256",
        )
        for card, row in zip(result["vaults"], vaults):
            if any(card[field] != row[field] for field in generation_fields):
                raise ContractError(
                    "API Vault card does not match its generation row"
                )
            freshness = card["source_freshness"]
            if sum(int(freshness[field]) for field in (
                "current", "changed", "missing", "unavailable"
            )) != int(card["counts"]["documents"]):
                raise ContractError("API Vault source freshness counts are inconsistent")

    if route == "source":
        source = result["source"]
        if source["vault_id"] not in complete:
            raise ContractError("API source belongs to an unavailable Vault")
        _validate_portable_path(source["path"], field="result.source.path")
        if source["current_version_id"] != (
            f"doc:{source['document_id']}:v{int(source['version_count']):08d}"
        ):
            raise ContractError("API source current version belongs to another document")
    elif route == "versions":
        document_id = str(result["document_id"])
        sequences = [int(item["sequence"]) for item in result["versions"]]
        if sequences != sorted(sequences, reverse=True) or len(sequences) != len(
            set(sequences)
        ):
            raise ContractError("API source versions are not latest-first and unique")
        for index, version in enumerate(result["versions"]):
            sequence = int(version["sequence"])
            version_id = f"doc:{document_id}:v{sequence:08d}"
            if version["version_id"] != version_id:
                raise ContractError("API source version belongs to another document")
            expected_predecessor = (
                None if sequence == 1 else f"doc:{document_id}:v{sequence - 1:08d}"
            )
            if version["predecessor_version_id"] != expected_predecessor:
                raise ContractError("API source version predecessor is not contiguous")
            captured_at = str(version["captured_at"])
            if not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z",
                captured_at,
            ):
                raise ContractError("API source version timestamp is not RFC3339 Z")
            try:
                datetime.fromisoformat(captured_at[:-1] + "+00:00")
            except ValueError as error:
                raise ContractError("API source version timestamp is not a real UTC time") from error
            _validate_portable_path(
                version["captured_path"],
                field=f"result.versions.{index}.captured_path",
            )
        if result["truncated"]:
            if not sequences or result["next_before_sequence"] != sequences[-1]:
                raise ContractError("API source version cursor is not canonical")
        elif result["next_before_sequence"] is not None:
            raise ContractError("untruncated API source versions have a cursor")
    elif route == "diff":
        document_id = str(result["document_id"])
        for field in ("from_version_id", "to_version_id"):
            value = result[field]
            if value is not None and not str(value).startswith(f"doc:{document_id}:"):
                raise ContractError("API diff version belongs to another document")
        _validate_portable_path(result["path"], field="result.path")
        if result["max_bytes"] != 1024 * 1024 or result["max_lines"] != 10_000:
            raise ContractError("API diff bounds are not the fixed v1 bounds")
        diff_text = str(result["text"])
        if len(diff_text.encode("utf-8")) > int(result["max_bytes"]):
            raise ContractError("API diff text exceeds its byte bound")
        if len(diff_text.splitlines(keepends=True)) != int(result["emitted_lines"]):
            raise ContractError("API diff emitted line count does not match its text")
    elif route == "excerpt":
        document_id = str(result["document_id"])
        if not str(result["version_id"]).startswith(f"doc:{document_id}:"):
            raise ContractError("API excerpt version belongs to another document")
        _validate_portable_path(result["path"], field="result.path")
        numbers = [int(item["number"]) for item in result["lines"]]
        if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise ContractError("API excerpt line numbers are not contiguous")
        if numbers and (
            result["start"] != numbers[0] or result["end"] != numbers[-1]
        ):
            raise ContractError("API excerpt bounds do not match its lines")
        if numbers:
            if not (result["start"] <= result["line"] <= result["end"]):
                raise ContractError("API excerpt focus lies outside its span")
        elif not (
            result["start"] == 1 and result["end"] == 0 and result["line"] == 1
        ):
            raise ContractError("empty API excerpt does not use the canonical bounds")
        excerpt = "\n".join(str(item["text"]) for item in result["lines"])
        if result["excerpt_sha256"] != hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest():
            raise ContractError("API excerpt digest does not match its lines")

    for index, evidence in enumerate(evidence_rows):
        handle_vault, _ = split_handle(
            str(evidence["handle"]), f"result.evidence.{index}.handle"
        )
        for field in ("source", "target"):
            value = evidence[field]
            if value is not None and split_handle(
                str(value), f"result.evidence.{index}.{field}"
            )[0] != handle_vault:
                raise ContractError("API evidence crosses Vault identities")
        _validate_portable_path(
            evidence["source_path"], field=f"result.evidence.{index}.source_path"
        )


def _validate_vault_store(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "qlkg-vault-store-v3":
        return

    def records(value: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ContractError(f"{field} must be an array")
        result = [dict(item) for item in value]
        paths = [str(item.get("path", "")) for item in result]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ContractError(f"{field} must be uniquely sorted by path")
        for index, path in enumerate(paths):
            _validate_portable_path(path, field=f"{field}.{index}.path")
        return result

    def validate_authority_path(path: str, *, field: str) -> None:
        _validate_portable_path(path, field=field)
        forbidden = {".git", ".obsidian", ".kgdistiller"}
        if any(part.casefold() in forbidden for part in PurePosixPath(path).parts):
            raise ContractError(
                "Vault store authority paths must not enter excluded local-state directories"
            )

    vault = payload["vault"]
    vault_manifest = dict(vault["manifest"])
    _validate_portable_path(vault_manifest["path"], field="vault.manifest.path")
    if vault_manifest["path"] != ".kgdistiller/vault.json":
        raise ContractError("Vault store manifest record is not canonical")

    authority = payload["authority"]
    roots = [dict(item) for item in authority["roots"]]
    expected_kinds = ["concept", "field", "topic"]
    if [item["kind"] for item in roots] != expected_kinds:
        raise ContractError("Vault store authority roots must use canonical kind order")
    root_paths: dict[str, str] = {}
    for item in roots:
        path = str(item["path"])
        validate_authority_path(
            path, field=f"authority.roots.{item['kind']}.path"
        )
        root_paths[str(item["kind"])] = path
    if len(set(root_paths.values())) != 3:
        raise ContractError("Vault store authority roots must be distinct")
    folded_roots = [
        tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in PurePosixPath(path).parts
        )
        for path in root_paths.values()
    ]
    for index, left in enumerate(folded_roots):
        for right in folded_roots[index + 1 :]:
            common = min(len(left), len(right))
            if left[:common] == right[:common]:
                raise ContractError(
                    "Vault store authority roots overlap on a portable filesystem"
                )
    authority_artifacts = records(authority["artifacts"], "authority.artifacts")
    root_by_kind = {item["kind"]: item["path"] for item in roots}
    for item in authority_artifacts:
        validate_authority_path(
            str(item["path"]), field="authority.artifacts.path"
        )
        root = str(root_by_kind[item["kind"]])
        if not str(item["path"]).startswith(root + "/") or not str(item["path"]).endswith(
            ".md"
        ):
            raise ContractError("authority artifact is outside its managed Markdown root")
    authority_projection = [
        {
            "path": item["path"],
            "kind": item["kind"],
            "normalized_bytes": item["normalized_bytes"],
            "normalized_sha256": item["normalized_sha256"],
        }
        for item in authority_artifacts
    ]
    if authority["generation_sha256"] != sha256_json(authority_projection):
        raise ContractError("authority generation does not match normalized inventory")

    source = payload["source"]
    source_artifacts = records(source["artifacts"], "source.artifacts")
    source_blobs = records(source["blobs"], "source.blobs")
    source_manifest = source["manifest"]
    source_records: list[dict[str, Any]] = []
    if source_manifest is not None:
        source_manifest = dict(source_manifest)
        _validate_portable_path(source_manifest["path"], field="source.manifest.path")
        if source_manifest["path"] != ".kgdistiller/sources/manifest.json":
            raise ContractError("source manifest path is not canonical")
        generation = str(source["generation_sha256"])
        expected_source_paths = [
            f".kgdistiller/sources/generations/{generation}/{name}.jsonl"
            for name in ("derivations", "documents", "versions")
        ]
        if [item["path"] for item in source_artifacts] != sorted(expected_source_paths):
            raise ContractError("source artifact paths do not match the current generation")
        source_records.append(source_manifest)
    for item in source_blobs:
        digest = str(item["sha256"])
        if item["path"] != f".kgdistiller/sources/blobs/sha256/{digest[:2]}/{digest}":
            raise ContractError("source blob path does not match its digest")
    source_records.extend(source_artifacts)
    source_records.extend(source_blobs)
    source_records.sort(key=lambda item: item["path"])
    if source["inventory_sha256"] != sha256_json(source_records):
        raise ContractError("source inventory digest does not match its records")

    graph = payload["graph"]
    graph_artifacts = records(graph["artifacts"], "graph.artifacts")
    graph_paths = {str(item["path"]): item for item in graph_artifacts}
    required_graph = {
        ".kgdistiller/graph/manifest.json",
        ".kgdistiller/graph/sources.json",
        ".kgdistiller/graph/nodes.jsonl",
        ".kgdistiller/graph/edges.jsonl",
        ".kgdistiller/graph/references.jsonl",
        ".kgdistiller/graph/diagnostics.json",
    }
    if not required_graph.issubset(graph_paths) or any(
        path not in required_graph
        and re.fullmatch(
            r"\.kgdistiller/graph/entries/(?:by-source|meta)/.+\.jsonl", path
        )
        is None
        for path in graph_paths
    ):
        raise ContractError("graph inventory is not a complete native graph")
    if graph["manifest_sha256"] != graph_paths[".kgdistiller/graph/manifest.json"]["sha256"]:
        raise ContractError("graph manifest digest does not match its artifact")
    if graph["inventory_sha256"] != sha256_json(graph_artifacts):
        raise ContractError("graph inventory digest does not match its records")

    receipts = payload["receipts"]
    receipt_artifacts = records(receipts["artifacts"], "receipts.artifacts")
    for item in receipt_artifacts:
        digest = str(item["receipt_sha256"])
        if item["path"] != f".kgdistiller/receipts/sha256/{digest[:2]}/{digest}.json":
            raise ContractError("receipt path does not match its content digest")
    if receipts["count"] != len(receipt_artifacts):
        raise ContractError("receipt count does not match its inventory")
    if receipts["inventory_sha256"] != sha256_json(receipt_artifacts):
        raise ContractError("receipt inventory digest does not match its records")

    scaffolds = records(payload["scaffolds"], "scaffolds")
    scaffold_paths = {str(item["path"]) for item in scaffolds}
    required_scaffolds = {
        ".kgdistiller/.gitattributes",
        ".kgdistiller/build/.gitignore",
    }
    allowed_scaffolds = required_scaffolds | {
        f"{root}/.gitkeep" for root in root_paths.values()
    } | {".kgdistiller/sources/.gitkeep"}
    empty_root_gitkeeps = {
        f"{root}/.gitkeep"
        for root in root_paths.values()
        if not any(str(item["path"]).startswith(root + "/") for item in authority_artifacts)
    }
    source_gitkeep = ".kgdistiller/sources/.gitkeep"
    required_conditional = empty_root_gitkeeps | (
        {source_gitkeep} if source["manifest"] is None else set()
    )
    forbidden_conditional = (
        {source_gitkeep} if source["manifest"] is not None else set()
    )
    if (
        not (required_scaffolds | required_conditional).issubset(scaffold_paths)
        or not scaffold_paths.issubset(
        allowed_scaffolds
        )
        or bool(scaffold_paths & forbidden_conditional)
        or any(
            f"{root}/.gitkeep" in scaffold_paths
            for root in root_paths.values()
            if any(str(item["path"]).startswith(root + "/") for item in authority_artifacts)
        )
    ):
        raise ContractError("Vault store scaffold inventory is not canonical")
    fixed_scaffold_bytes = {
        ".kgdistiller/.gitattributes": (
            b"* text=auto eol=lf\nsources/blobs/** -text\n"
        ),
        ".kgdistiller/build/.gitignore": b"*\n!.gitignore\n",
    }
    for item in scaffolds:
        path = str(item["path"])
        expected = fixed_scaffold_bytes.get(path, b"")
        if (
            int(item["bytes"]) != len(expected)
            or item["sha256"] != hashlib.sha256(expected).hexdigest()
        ):
            raise ContractError("Vault store scaffold content record is not canonical")

    all_records: list[Mapping[str, Any]] = [vault_manifest]
    all_records.extend(authority_artifacts)
    all_records.extend(source_records)
    all_records.extend(graph_artifacts)
    all_records.extend(receipt_artifacts)
    all_records.extend(scaffolds)
    record_paths = [str(item["path"]) for item in all_records]
    if len(record_paths) != len(set(record_paths)):
        raise ContractError("Vault store artifact paths overlap")
    folded: dict[str, str] = {}
    folded_parts: list[tuple[tuple[str, ...], str]] = []
    for path in record_paths + [".kgdistiller/store.json"]:
        key = unicodedata.normalize("NFC", path).casefold()
        previous = folded.setdefault(key, path)
        if previous != path:
            raise ContractError("Vault store paths collide on a portable filesystem")
        folded_parts.append((tuple(part.casefold() for part in PurePosixPath(path).parts), path))
    folded_parts.sort()
    for index, (parts, _) in enumerate(folded_parts[:-1]):
        next_parts = folded_parts[index + 1][0]
        if len(parts) < len(next_parts) and next_parts[: len(parts)] == parts:
            raise ContractError("Vault store file paths have an ancestor collision")
    expected_managed = sorted(record_paths + [".kgdistiller/store.json"])
    if payload["managed_paths"] != expected_managed:
        raise ContractError("managed_paths does not exactly match the portable inventory")

    content_projection = {
        "vault_manifest_sha256": vault["manifest_sha256"],
        "authority_generation_sha256": authority["generation_sha256"],
        "source_inventory_sha256": source["inventory_sha256"],
        "graph_inventory_sha256": graph["inventory_sha256"],
        "receipt_inventory_sha256": receipts["inventory_sha256"],
        "scaffold_inventory_sha256": sha256_json(scaffolds),
    }
    if payload["content_generation_sha256"] != sha256_json(content_projection):
        raise ContractError("content generation does not match the portable inventory")
    total_bytes = len(canonical_json(payload).encode("utf-8")) + sum(
        int(
            item["normalized_bytes"]
            if "normalized_bytes" in item
            else item["bytes"]
        )
        for item in all_records
    )
    if total_bytes > MAX_VAULT_STORE_BYTES:
        raise ContractError("Vault store inventory exceeds its aggregate byte bound")


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
    _validate_vault_ingest_receipt(payload)
    _validate_recall_request(payload)
    _validate_recall_paths(payload)
    _validate_recall_report(payload)
    _validate_api_response(payload)
    _validate_vault_store(payload)
    digest_field = SELF_DIGEST_FIELDS.get(discriminator)
    if verify_digest and digest_field is not None:
        claimed = payload.get(digest_field)
        if claimed != self_digest(payload, digest_field):
            raise ContractError(f"{digest_field} does not match canonical content")
    return copy.deepcopy(payload)
