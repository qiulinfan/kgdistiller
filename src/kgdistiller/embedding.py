"""Explicit, bounded embedding status and synchronization orchestration."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import struct
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    ContractError,
    parse_contract_json,
    sha256_json,
    validate_contract,
)
from .providers import (
    MAX_EMBEDDING_BATCH,
    MAX_EMBEDDING_TEXT_BYTES,
    ProviderError,
    default_provider_registry,
    provider_config_sha256,
)


EMBEDDING_INPUT_SCHEMA = "qlkg-node-embedding-text-v1"
EMBEDDING_STATUS_SCHEMA = "qlkg-embedding-status-v1"
EMBEDDING_SYNC_SCHEMA = "qlkg-embedding-sync-v1"
MAX_EMBEDDING_POLICY_BYTES = 1024 * 1024

DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_NODES = 10_000
DEFAULT_MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_VECTOR_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_BATCHES = 1024

_MAX_RETRIES_LIMIT = 10
_MAX_NODES_LIMIT = 100_000
_MAX_INPUT_BYTES_LIMIT = 128 * 1024 * 1024
_MAX_VECTOR_BYTES_LIMIT = 128 * 1024 * 1024
_MAX_BATCHES_LIMIT = 100_000
_RETRYABLE_PROVIDER_CODES = {"provider-timeout", "provider-unavailable"}
_SAFE_PROVIDER_MESSAGES = {
    "adapter-initialization": "embedding provider could not be initialized",
    "dimension-mismatch": "provider vector dimensions do not match policy",
    "invalid-provider-config": "embedding provider configuration is invalid",
    "invalid-provider-request": "embedding provider request is invalid",
    "invalid-response": "embedding provider returned an invalid response",
    "missing-adapter": "embedding provider adapter is unavailable",
    "missing-credential": "embedding provider credential is unavailable",
    "profile-mismatch": "embedding provider does not match policy",
    "provider-timeout": "embedding provider timed out",
    "provider-unavailable": "embedding provider is unavailable",
}


class EmbeddingError(ValueError):
    """Stable, bounded failure from embedding status or synchronization."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def payload(self) -> dict[str, str]:
        return {
            "kind": "kgdistiller-embedding-error",
            "code": self.code,
            "message": self.message,
        }


def _validated_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] | None = None
    failed = False
    try:
        validated = validate_contract(dict(policy))
        sha256_json(validated)
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        failed = True
    if failed or validated is None or validated.get("schema") != "qlkg-embedding-policy-v1":
        raise EmbeddingError(
            "invalid-embedding-policy", "embedding policy failed validation"
        )
    return validated


def load_embedding_policy(path: Path) -> dict[str, Any]:
    """Load one bounded committed ``qlkg-embedding-policy-v1`` document."""
    descriptor: int | None = None
    failure: tuple[str, str] | None = None
    raw: bytes | None = None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        failure = ("embedding-policy-not-found", "embedding policy does not exist")
    except (IsADirectoryError, PermissionError, OSError):
        failure = ("embedding-policy-unreadable", "embedding policy could not be read")
    if failure is None:
        try:
            if descriptor is None or not stat.S_ISREG(os.fstat(descriptor).st_mode):
                failure = (
                    "invalid-embedding-policy",
                    "embedding policy must be a regular file",
                )
            else:
                with os.fdopen(descriptor, "rb", closefd=True) as handle:
                    descriptor = None
                    raw = handle.read(MAX_EMBEDDING_POLICY_BYTES + 1)
        except OSError:
            failure = (
                "embedding-policy-unreadable",
                "embedding policy could not be read",
            )
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if failure is not None:
        raise EmbeddingError(*failure)
    if raw is None:
        raise EmbeddingError(
            "embedding-policy-unreadable", "embedding policy could not be read"
        )
    if len(raw) > MAX_EMBEDDING_POLICY_BYTES:
        raise EmbeddingError(
            "embedding-policy-too-large", "embedding policy exceeds the byte budget"
        )

    parsed: Any = None
    parse_failed = False
    try:
        parsed = parse_contract_json(raw.decode("utf-8"))
    except (ContractError, UnicodeDecodeError, ValueError, RecursionError, OverflowError):
        parse_failed = True
    if parse_failed or not isinstance(parsed, dict):
        raise EmbeddingError(
            "invalid-embedding-policy", "embedding policy failed validation"
        )
    return _validated_policy(parsed)


def _read_inventory(path: Path, *, namespace: str) -> dict[str, Any]:
    from .agent import embedding_inventory

    return embedding_inventory(path, namespace=namespace)


def _install_records(
    path: Path,
    records: list[dict[str, Any]],
    *,
    expected_snapshot_sha256: str,
    expected_graph_sha256: str,
    namespace: str,
) -> dict[str, Any]:
    from .agent import install_embedding_records

    return install_embedding_records(
        path,
        records,
        expected_snapshot_sha256=expected_snapshot_sha256,
        expected_graph_sha256=expected_graph_sha256,
        namespace=namespace,
    )


def _selected_profiles(
    policy: dict[str, Any], profile_names: Iterable[str] | str | None
) -> list[dict[str, Any]]:
    profiles = {str(profile["name"]): profile for profile in policy["profiles"]}
    if profile_names is None:
        selected_names = sorted(profiles)
    elif isinstance(profile_names, str):
        selected_names = [profile_names]
    else:
        selected_names = []
        invalid_selection = False
        try:
            iterator = iter(profile_names)
            for _ in range(33):
                try:
                    name = next(iterator)
                except StopIteration:
                    break
                selected_names.append(str(name))
        except Exception:
            invalid_selection = True
        if invalid_selection:
            raise EmbeddingError(
                "invalid-embedding-request", "embedding profile selection is invalid"
            )
        if len(selected_names) > 32:
            raise EmbeddingError(
                "invalid-embedding-request", "too many embedding profiles were selected"
            )
    selected_names = list(dict.fromkeys(selected_names))
    if not selected_names:
        raise EmbeddingError(
            "invalid-embedding-request", "at least one embedding profile is required"
        )
    unknown = [name for name in selected_names if name not in profiles]
    if unknown:
        raise EmbeddingError(
            "unknown-embedding-profile", "selected embedding profile is not in policy"
        )
    return [profiles[name] for name in selected_names]


def _profile_binding(
    profile: dict[str, Any],
    provider_configs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    name = str(profile["name"])
    config = provider_configs.get(name)
    if config is None:
        return {"status": "missing", "config": None, "digest": None}
    if not isinstance(config, Mapping):
        return {"status": "invalid", "config": None, "digest": None}
    copied = dict(config)
    if (
        str(copied.get("adapter", "")) != str(profile["provider"])
        or str(copied.get("model", "")) != str(profile["model"])
        or isinstance(copied.get("dimensions"), bool)
        or copied.get("dimensions") != profile["dimensions"]
    ):
        return {"status": "mismatch", "config": copied, "digest": None}
    digest: str | None = None
    invalid = False
    try:
        digest = provider_config_sha256(copied)
    except ProviderError:
        invalid = True
    if invalid or digest is None:
        return {"status": "invalid", "config": copied, "digest": None}
    return {"status": "ready", "config": copied, "digest": digest}


def _validated_inventory(inventory: Any) -> dict[str, Any]:
    if not isinstance(inventory, dict):
        raise EmbeddingError(
            "invalid-embedding-inventory", "embedding inventory is invalid"
        )
    snapshot_sha256 = str(inventory.get("snapshot_sha256", ""))
    graph_sha256 = str(inventory.get("graph_sha256", ""))
    if not _is_sha256(snapshot_sha256) or not _is_sha256(graph_sha256):
        raise EmbeddingError(
            "invalid-embedding-inventory", "embedding inventory generation is invalid"
        )
    raw_nodes = inventory.get("nodes")
    raw_records = inventory.get("records")
    if not isinstance(raw_nodes, list) or not isinstance(raw_records, list):
        raise EmbeddingError(
            "invalid-embedding-inventory", "embedding inventory records are invalid"
        )
    nodes: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise EmbeddingError(
                "invalid-embedding-inventory", "embedding inventory node is invalid"
            )
        node_id = str(raw_node.get("node_id", raw_node.get("id", "")))
        text = raw_node.get("text", raw_node.get("canonical_text"))
        content_sha256 = str(raw_node.get("content_sha256", ""))
        if (
            not node_id
            or node_id in seen_nodes
            or not isinstance(text, str)
            or not _is_sha256(content_sha256)
        ):
            raise EmbeddingError(
                "invalid-embedding-inventory", "embedding inventory node is invalid"
            )
        encoded: bytes | None = None
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            pass
        if encoded is None or hashlib.sha256(encoded).hexdigest() != content_sha256:
            raise EmbeddingError(
                "invalid-embedding-inventory", "embedding canonical input is invalid"
            )
        seen_nodes.add(node_id)
        node = dict(raw_node)
        node["node_id"] = node_id
        node["type"] = str(raw_node.get("type", raw_node.get("node_type", "")))
        node["text"] = text
        node["content_sha256"] = content_sha256
        properties = raw_node.get("properties")
        if not isinstance(properties, dict):
            properties = {
                "curation_status": str(raw_node.get("curation_status", "")),
                "source_status": str(raw_node.get("source_status", "")),
            }
        node["properties"] = properties
        provenance = raw_node.get("provenance")
        provenance = dict(provenance) if isinstance(provenance, dict) else {}
        if raw_node.get("active") is False:
            provenance["active"] = False
        node["provenance"] = provenance
        nodes.append(node)
    records = [dict(record) for record in raw_records if isinstance(record, dict)]
    if len(records) != len(raw_records):
        raise EmbeddingError(
            "invalid-embedding-inventory", "embedding inventory record is invalid"
        )
    return {
        "snapshot_sha256": snapshot_sha256,
        "graph_sha256": graph_sha256,
        "nodes": nodes,
        "records": records,
    }


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _eligible(node: dict[str, Any], required_types: set[str]) -> bool:
    if str(node.get("type", "")) not in required_types:
        return False
    properties = node.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    return (
        properties.get("source_status") != "orphaned"
        and properties.get("curation_status") != "needs-review"
        and node.get("status") not in {"orphaned", "needs-review"}
        and node.get("active") is not False
        and provenance.get("active") is not False
        and bool(str(node.get("text", "")).strip())
    )


def _record_matches_provider_model(
    record: dict[str, Any], profile: dict[str, Any]
) -> bool:
    return (
        str(record.get("provider", "")) == str(profile["provider"])
        and str(record.get("model", "")) == str(profile["model"])
    )


def _record_matches_binding(
    record: dict[str, Any], profile: dict[str, Any], digest: str | None
) -> bool:
    return (
        digest is not None
        and _record_matches_provider_model(record, profile)
        and not isinstance(record.get("dimensions"), bool)
        and record.get("dimensions") == profile["dimensions"]
        and str(record.get("embedding_input_schema", "")) == EMBEDDING_INPUT_SCHEMA
        and str(record.get("provider_config_sha256", "")) == digest
    )


def _coverage_payload(
    *,
    node_type: str | None,
    eligible: set[str],
    ready: set[str],
    missing: set[str],
    stale: set[str],
    incompatible: set[str],
    minimum_coverage: float,
    configuration_status: str,
) -> dict[str, Any]:
    count = len(eligible)
    coverage = (len(ready) / count) if count else None
    if count == 0:
        readiness = "not-applicable"
    elif configuration_status != "ready":
        readiness = "unavailable"
    elif coverage is not None and coverage >= minimum_coverage:
        readiness = "ready"
    else:
        readiness = "partial"
    payload: dict[str, Any] = {
        "eligible": count,
        "ready": len(ready & eligible),
        "missing": len(missing & eligible),
        "stale": len(stale & eligible),
        # Desired slots obey ready + missing + stale == eligible. An old or
        # malformed record can coexist with a missing desired slot, so record
        # incompatibility is deliberately reported in a separate namespace.
        "vector_records": {"incompatible": len(incompatible & eligible)},
        "coverage": coverage,
        "readiness": readiness,
    }
    if node_type is not None:
        payload["node_type"] = node_type
    return payload


def _analyze(
    inventory: dict[str, Any],
    policy: dict[str, Any],
    provider_configs: Mapping[str, Mapping[str, Any]],
    profiles: list[dict[str, Any]],
    *,
    namespace: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records_by_node: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(inventory["records"]):
        records_by_node.setdefault(str(record.get("node_id", "")), []).append(
            (index, record)
        )
    consumed_records: set[int] = set()
    public_profiles: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []

    for profile in profiles:
        binding = _profile_binding(profile, provider_configs)
        required_types = {str(value) for value in profile["required_node_types"]}
        eligible_by_type: dict[str, set[str]] = {
            node_type: set() for node_type in sorted(required_types)
        }
        ready: set[str] = set()
        missing: set[str] = set()
        stale: set[str] = set()
        incompatible: set[str] = set()
        pending: list[dict[str, Any]] = []

        for node in inventory["nodes"]:
            if not _eligible(node, required_types):
                continue
            node_id = str(node["node_id"])
            node_type = str(node["type"])
            eligible_by_type[node_type].add(node_id)
            matching_provider_model: list[dict[str, Any]] = []
            valid_binding: list[dict[str, Any]] = []
            for record_index, record in records_by_node.get(node_id, []):
                if not _record_matches_provider_model(record, profile):
                    continue
                consumed_records.add(record_index)
                matching_provider_model.append(record)
                if _record_matches_binding(record, profile, binding["digest"]):
                    if record.get("vector_valid") is True:
                        valid_binding.append(record)
                    else:
                        incompatible.add(node_id)
                else:
                    incompatible.add(node_id)

            current = [
                record
                for record in valid_binding
                if str(record.get("content_sha256", "")) == node["content_sha256"]
            ]
            if current:
                ready.add(node_id)
                reason = None
            elif valid_binding:
                stale.add(node_id)
                reason = "stale"
            else:
                missing.add(node_id)
                reason = "missing"
            if reason is not None:
                pending.append({"node": node, "reason": reason})

        eligible_ids = set().union(*eligible_by_type.values()) if eligible_by_type else set()
        minimum_coverage = float(profile["minimum_coverage"])
        summary = _coverage_payload(
            node_type=None,
            eligible=eligible_ids,
            ready=ready,
            missing=missing,
            stale=stale,
            incompatible=incompatible,
            minimum_coverage=minimum_coverage,
            configuration_status=str(binding["status"]),
        )
        node_type_payloads = []
        for node_type, node_ids in sorted(eligible_by_type.items()):
            node_type_payloads.append(
                _coverage_payload(
                    node_type=node_type,
                    eligible=node_ids,
                    ready=ready,
                    missing=missing,
                    stale=stale,
                    incompatible=incompatible,
                    minimum_coverage=minimum_coverage,
                    configuration_status=str(binding["status"]),
                )
            )
        public_profile = {
            "name": str(profile["name"]),
            "provider": str(profile["provider"]),
            "model": str(profile["model"]),
            "dimensions": int(profile["dimensions"]),
            "provider_config_sha256": binding["digest"],
            "configuration_status": binding["status"],
            "required": bool(profile["required"]),
            "minimum_coverage": minimum_coverage,
            **summary,
            "node_types": node_type_payloads,
        }
        public_profiles.append(public_profile)
        plans.append(
            {
                "profile": profile,
                "binding": binding,
                "eligible_ids": eligible_ids,
                "pending": sorted(
                    pending, key=lambda item: str(item["node"]["node_id"])
                ),
                "public": public_profile,
            }
        )

    required = [profile for profile in public_profiles if profile["required"]]
    applicable_required = [
        profile
        for profile in required
        if profile["readiness"] != "not-applicable"
    ]
    if not applicable_required:
        readiness = "not-applicable"
    elif all(profile["readiness"] == "ready" for profile in applicable_required):
        readiness = "ready"
    else:
        readiness = "partial"
    status = {
        "schema": EMBEDDING_STATUS_SCHEMA,
        "namespace": namespace,
        "snapshot_sha256": inventory["snapshot_sha256"],
        "graph_sha256": inventory["graph_sha256"],
        "policy_sha256": sha256_json(policy),
        "embedding_input_schema": EMBEDDING_INPUT_SCHEMA,
        "readiness": readiness,
        "profiles": public_profiles,
        "unmanaged": {"records": len(inventory["records"]) - len(consumed_records)},
    }
    return status, plans


def _inventory_or_error(path: Path, *, namespace: str) -> dict[str, Any]:
    raw: Any = None
    failure = False
    try:
        raw = _read_inventory(path, namespace=namespace)
    except Exception:
        failure = True
    if failure:
        raise EmbeddingError(
            "embedding-inventory-unavailable", "embedding inventory could not be read"
        )
    return _validated_inventory(raw)


def embedding_status(
    path: Path,
    policy: Mapping[str, Any],
    provider_configs: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    namespace: str = "personal",
    profile_names: Iterable[str] | str | None = None,
) -> dict[str, Any]:
    """Classify current vectors without constructing a provider or reading credentials."""
    validated_policy = _validated_policy(policy)
    selected = _selected_profiles(validated_policy, profile_names)
    configs = {} if provider_configs is None else provider_configs
    if not isinstance(configs, Mapping):
        raise EmbeddingError(
            "invalid-embedding-request", "provider configuration map is invalid"
        )
    inventory = _inventory_or_error(path, namespace=namespace)
    status, _ = _analyze(
        inventory, validated_policy, configs, selected, namespace=namespace
    )
    return status


def _validated_budget(
    *,
    batch_size: int,
    max_retries: int,
    max_nodes: int,
    max_input_bytes: int,
    max_vector_bytes: int,
    max_batches: int,
) -> None:
    values = (
        ("batch_size", batch_size, 1, MAX_EMBEDDING_BATCH),
        ("max_retries", max_retries, 0, _MAX_RETRIES_LIMIT),
        ("max_nodes", max_nodes, 1, _MAX_NODES_LIMIT),
        ("max_input_bytes", max_input_bytes, 1, _MAX_INPUT_BYTES_LIMIT),
        ("max_vector_bytes", max_vector_bytes, 1, _MAX_VECTOR_BYTES_LIMIT),
        ("max_batches", max_batches, 1, _MAX_BATCHES_LIMIT),
    )
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
        for _, value, minimum, maximum in values
    ):
        raise EmbeddingError(
            "invalid-work-budget", "embedding work budget is invalid"
        )


def _make_batches(
    pending: list[dict[str, Any]], batch_size: int
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for item in pending:
        text = str(item["node"]["text"])
        encoded = text.encode("utf-8")
        item["input_bytes"] = len(encoded)
        if len(encoded) > MAX_EMBEDDING_TEXT_BYTES:
            raise EmbeddingError(
                "work-budget-exceeded",
                "one canonical embedding input exceeds the batch byte budget",
            )
        if current and (
            len(current) >= batch_size
            or current_bytes + len(encoded) > MAX_EMBEDDING_TEXT_BYTES
        ):
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += len(encoded)
    if current:
        batches.append(current)
    return batches


def _provider_or_error(
    registry: Any,
    profile: dict[str, Any],
    binding: dict[str, Any],
    environ: Mapping[str, str] | None,
) -> Any:
    provider: Any = None
    failure: tuple[str, str] | None = None
    try:
        provider = registry.create(
            str(profile["name"]), binding["config"], environ=environ
        )
    except ProviderError as error:
        failure = _safe_provider_failure(error)
    except Exception:
        failure = (
            "provider-unavailable",
            "embedding provider could not be created",
        )
    if failure is not None:
        raise EmbeddingError(*failure)
    if provider is None:
        raise EmbeddingError(
            "provider-unavailable", "embedding provider could not be created"
        )
    metadata_failed = False
    metadata_matches = False
    try:
        provider_dimensions = getattr(provider, "dimensions", None)
        metadata_matches = (
            str(getattr(provider, "name", "")) == str(profile["provider"])
            and str(getattr(provider, "model", "")) == str(profile["model"])
            and not isinstance(provider_dimensions, bool)
            and provider_dimensions == profile["dimensions"]
            and str(getattr(provider, "provider_config_sha256", ""))
            == str(binding["digest"])
        )
    except Exception:
        metadata_failed = True
    if metadata_failed or not metadata_matches:
        raise EmbeddingError(
            "profile-mismatch", "provider does not match embedding policy"
        )
    return provider


def _provider_vectors(
    provider: Any, texts: list[str], *, max_retries: int
) -> tuple[list[Any], int]:
    method: Any = None
    method_failed = False
    try:
        method = getattr(provider, "embed_documents", None)
        if not callable(method):
            method = getattr(provider, "embed", None)
    except Exception:
        method_failed = True
    if method_failed:
        raise EmbeddingError(
            "provider-unavailable", "embedding provider is unavailable"
        )
    if not callable(method):
        raise EmbeddingError(
            "missing-adapter", "embedding provider has no document embedding method"
        )

    attempts = 0
    for retry in range(max_retries + 1):
        attempts += 1
        vectors: Any = None
        failure: tuple[str, str] | None = None
        try:
            vectors = method(texts)
        except ProviderError as error:
            failure = _safe_provider_failure(error)
        except Exception:
            failure = (
                "provider-unavailable",
                "embedding provider failed",
            )
        if failure is None:
            response_failed = False
            normalized_vectors: list[Any] | None = None
            try:
                if not isinstance(vectors, list) or len(vectors) != len(texts):
                    response_failed = True
                else:
                    normalized_vectors = list(vectors)
            except Exception:
                response_failed = True
            if response_failed or normalized_vectors is None:
                raise EmbeddingError(
                    "invalid-response", "provider returned an invalid vector batch"
                )
            return normalized_vectors, attempts
        if failure[0] in _RETRYABLE_PROVIDER_CODES and retry < max_retries:
            continue
        raise EmbeddingError(*failure)
    raise EmbeddingError("provider-unavailable", "embedding provider failed")


def _safe_provider_failure(error: ProviderError) -> tuple[str, str]:
    try:
        code = str(error.code)
    except Exception:
        return ("provider-unavailable", "embedding provider is unavailable")
    if code not in _SAFE_PROVIDER_MESSAGES:
        return ("provider-unavailable", "embedding provider is unavailable")
    return (code, _SAFE_PROVIDER_MESSAGES[code])


def _packed_vector(vector: Any, dimensions: int) -> bytes:
    invalid_shape = False
    wrong_dimensions = False
    values_source: list[Any] | None = None
    try:
        if not isinstance(vector, list):
            invalid_shape = True
        elif len(vector) != dimensions:
            wrong_dimensions = True
        else:
            values_source = list(vector)
    except Exception:
        invalid_shape = True
    if invalid_shape or values_source is None and not wrong_dimensions:
        raise EmbeddingError("invalid-response", "provider vector is invalid")
    if wrong_dimensions:
        raise EmbeddingError(
            "dimension-mismatch", "provider vector dimensions do not match policy"
        )
    values: list[float] = []
    for raw in values_source or []:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EmbeddingError("invalid-response", "provider vector is invalid")
        converted = 0.0
        conversion_failed = False
        try:
            converted = float(raw)
            converted = struct.unpack("<f", struct.pack("<f", converted))[0]
        except Exception:
            conversion_failed = True
        if conversion_failed or not math.isfinite(converted):
            raise EmbeddingError("invalid-response", "provider vector is invalid")
        values.append(converted)
    if not any(value != 0.0 for value in values):
        raise EmbeddingError("invalid-response", "provider vector is all zero")
    return struct.pack(f"<{dimensions}f", *values)


def sync_embeddings(
    path: Path,
    policy: Mapping[str, Any],
    provider_configs: Mapping[str, Mapping[str, Any]],
    *,
    registry: Any = None,
    environ: Mapping[str, str] | None = None,
    namespace: str = "personal",
    profile_names: Iterable[str] | str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_vector_bytes: int = DEFAULT_MAX_VECTOR_BYTES,
    max_batches: int = DEFAULT_MAX_BATCHES,
) -> dict[str, Any]:
    """Create all missing/stale vectors, then atomically install them once."""
    _validated_budget(
        batch_size=batch_size,
        max_retries=max_retries,
        max_nodes=max_nodes,
        max_input_bytes=max_input_bytes,
        max_vector_bytes=max_vector_bytes,
        max_batches=max_batches,
    )
    validated_policy = _validated_policy(policy)
    selected = _selected_profiles(validated_policy, profile_names)
    if not isinstance(provider_configs, Mapping):
        raise EmbeddingError(
            "invalid-embedding-request", "provider configuration map is invalid"
        )
    inventory = _inventory_or_error(path, namespace=namespace)
    before, plans = _analyze(
        inventory, validated_policy, provider_configs, selected, namespace=namespace
    )

    desired_keys: set[tuple[str, str, str]] = set()
    for plan in plans:
        provider = str(plan["profile"]["provider"])
        model = str(plan["profile"]["model"])
        for node_id in plan["eligible_ids"]:
            key = (str(node_id), provider, model)
            if key in desired_keys:
                raise EmbeddingError(
                    "conflicting-embedding-profiles",
                    "selected profiles share one portable embedding record key",
                )
            desired_keys.add(key)

    for plan in plans:
        status = str(plan["binding"]["status"])
        if plan["pending"] and status != "ready":
            code = "profile-unavailable" if status == "missing" else "profile-mismatch"
            raise EmbeddingError(code, "provider configuration does not match policy")
        plan["batches"] = _make_batches(plan["pending"], batch_size)

    pending_count = sum(len(plan["pending"]) for plan in plans)
    input_bytes = sum(
        int(item["input_bytes"])
        for plan in plans
        for item in plan["pending"]
    )
    vector_bytes = sum(
        int(plan["profile"]["dimensions"]) * 4 * len(plan["pending"])
        for plan in plans
    )
    batch_count = sum(len(plan["batches"]) for plan in plans)
    if (
        pending_count > max_nodes
        or input_bytes > max_input_bytes
        or vector_bytes > max_vector_bytes
        or batch_count > max_batches
    ):
        raise EmbeddingError(
            "work-budget-exceeded", "embedding synchronization exceeds its work budget"
        )

    if pending_count == 0:
        return {
            "schema": EMBEDDING_SYNC_SCHEMA,
            "status": "unchanged",
            "namespace": namespace,
            "snapshot_sha256": inventory["snapshot_sha256"],
            "graph_sha256": inventory["graph_sha256"],
            "embedded": 0,
            "installed": 0,
            "unchanged": sum(int(profile["ready"]) for profile in before["profiles"]),
            "batches": 0,
            "attempts": 0,
            "embedding_status": before,
        }

    active_registry = registry if registry is not None else default_provider_registry()
    install_records: list[dict[str, Any]] = []
    attempts = 0
    profile_results: list[dict[str, Any]] = []
    for plan in plans:
        if not plan["pending"]:
            profile_results.append(
                {
                    "name": str(plan["profile"]["name"]),
                    "embedded": 0,
                    "batches": 0,
                    "attempts": 0,
                }
            )
            continue
        provider = _provider_or_error(
            active_registry,
            plan["profile"],
            plan["binding"],
            environ,
        )
        profile_attempts = 0
        for batch in plan["batches"]:
            texts = [str(item["node"]["text"]) for item in batch]
            vectors, batch_attempts = _provider_vectors(
                provider, texts, max_retries=max_retries
            )
            attempts += batch_attempts
            profile_attempts += batch_attempts
            if len(vectors) != len(batch):
                raise EmbeddingError(
                    "invalid-response", "provider returned the wrong vector count"
                )
            for item, vector in zip(batch, vectors):
                node = item["node"]
                install_records.append(
                    {
                        "node_id": str(node["node_id"]),
                        "provider": str(plan["profile"]["provider"]),
                        "model": str(plan["profile"]["model"]),
                        "dimensions": int(plan["profile"]["dimensions"]),
                        "embedding_input_schema": EMBEDDING_INPUT_SCHEMA,
                        "content_sha256": str(node["content_sha256"]),
                        "provider_config_sha256": str(plan["binding"]["digest"]),
                        "vector": _packed_vector(
                            vector, int(plan["profile"]["dimensions"])
                        ),
                    }
                )
        profile_results.append(
            {
                "name": str(plan["profile"]["name"]),
                "embedded": len(plan["pending"]),
                "batches": len(plan["batches"]),
                "attempts": profile_attempts,
            }
        )

    install_result: Any = None
    install_failure = False
    try:
        install_result = _install_records(
            path,
            install_records,
            expected_snapshot_sha256=inventory["snapshot_sha256"],
            expected_graph_sha256=inventory["graph_sha256"],
            namespace=namespace,
        )
    except Exception:
        install_failure = True
    if install_failure or not isinstance(install_result, dict):
        raise EmbeddingError(
            "embedding-install-failed", "embedding records could not be installed"
        )
    if install_result.get("status") == "stale-generation":
        raise EmbeddingError(
            "stale-generation", "graph changed during embedding synchronization"
        )
    if install_result.get("status") != "installed":
        raise EmbeddingError(
            "embedding-install-failed", "embedding records could not be installed"
        )

    after = embedding_status(
        path,
        validated_policy,
        provider_configs,
        namespace=namespace,
        profile_names=[str(profile["name"]) for profile in selected],
    )
    if (
        after.get("snapshot_sha256") != inventory["snapshot_sha256"]
        or after.get("graph_sha256") != inventory["graph_sha256"]
        or any(
            int(profile.get("ready", -1)) != int(profile.get("eligible", -2))
            for profile in after.get("profiles", [])
        )
    ):
        raise EmbeddingError(
            "stale-generation",
            "graph or embedding state changed immediately after installation",
        )
    return {
        "schema": EMBEDDING_SYNC_SCHEMA,
        "status": "installed",
        "namespace": namespace,
        "snapshot_sha256": inventory["snapshot_sha256"],
        "graph_sha256": inventory["graph_sha256"],
        "embedded": len(install_records),
        "installed": int(install_result.get("installed", len(install_records))),
        "unchanged": int(install_result.get("unchanged", 0)),
        "batches": batch_count,
        "attempts": attempts,
        "profiles": profile_results,
        "embedding_status": after,
    }
