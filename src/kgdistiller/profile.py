"""Machine-local runtime profile discovery and deterministic CLI precedence."""

from __future__ import annotations

import copy
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError, parse_contract_json, sha256_json, validate_contract


DEFAULT_LOCAL_PROFILE = Path("knowledge/build/local-profile.json")
DEFAULT_DATABASE = Path("knowledge/build/knowledge.sqlite")
MAX_LOCAL_PROFILE_BYTES = 1024 * 1024


class ProfileError(ValueError):
    """Stable, secret-safe failure while resolving machine-local configuration."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def payload(self) -> dict[str, str]:
        return {
            "kind": "kgdistiller-profile-error",
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    """Resolved machine-local paths and the selected non-secret provider profile."""

    repo_root: Path
    profile_path: Path
    profile_loaded: bool
    profile_sha256: str | None
    database: Path
    portable_store: Path
    embedding_profile: str | None
    provider_profiles: dict[str, dict[str, Any]]
    sources: dict[str, str]

    @property
    def provider_profile(self) -> dict[str, Any] | None:
        if self.embedding_profile is None:
            return None
        return copy.deepcopy(self.provider_profiles[self.embedding_profile])


def _resolve_path(base: Path, value: str | Path, *, field: str) -> Path:
    raw = str(value)
    if not raw:
        raise ProfileError("invalid-path", f"{field} path cannot be empty")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _load_profile(path: Path, *, required: bool) -> dict[str, Any] | None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        if required:
            raise ProfileError("profile-not-found", "the explicit local profile does not exist")
        return None
    except OSError as error:
        raise ProfileError("profile-unreadable", "the local profile could not be read") from error
    if not stat.S_ISREG(mode):
        raise ProfileError("invalid-profile", "the local profile is not a regular file")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ProfileError("profile-unreadable", "the local profile could not be read") from error
    if len(raw) > MAX_LOCAL_PROFILE_BYTES:
        raise ProfileError("profile-too-large", "the local profile exceeds 1 MiB")
    try:
        text = raw.decode("utf-8")
        payload = parse_contract_json(text)
        return validate_contract(payload)
    except (UnicodeDecodeError, ContractError) as error:
        raise ProfileError("invalid-profile", "the local profile failed validation") from error


def resolve_runtime_config(
    repo_root: Path,
    *,
    local_profile: str | Path | None = None,
    database: str | Path | None = None,
    portable_store: str | Path | None = None,
    embedding_profile: str | None = None,
) -> RuntimeConfig:
    """Resolve CLI overrides, then profile values, then repository defaults."""
    root = repo_root.resolve()
    explicit_profile = local_profile is not None
    profile_path = _resolve_path(
        root,
        local_profile if local_profile is not None else DEFAULT_LOCAL_PROFILE,
        field="local profile",
    )
    payload = _load_profile(profile_path, required=explicit_profile)
    profile_base = profile_path.parent
    provider_profiles = copy.deepcopy((payload or {}).get("provider_profiles") or {})

    if database is not None:
        resolved_database = _resolve_path(root, database, field="database")
        database_source = "cli"
    elif payload is not None:
        resolved_database = _resolve_path(
            profile_base, str(payload["database"]), field="database"
        )
        database_source = "profile"
    else:
        resolved_database = _resolve_path(root, DEFAULT_DATABASE, field="database")
        database_source = "default"

    if portable_store is not None:
        resolved_store = _resolve_path(root, portable_store, field="portable store")
        store_source = "cli"
    elif payload is not None:
        resolved_store = _resolve_path(
            profile_base, str(payload["portable_store"]), field="portable store"
        )
        store_source = "profile"
    else:
        resolved_store = root
        store_source = "default"

    if embedding_profile is not None:
        selected_profile = embedding_profile
        profile_source = "cli"
    elif payload is not None:
        selected_profile = str(payload["embedding_profile"])
        profile_source = "profile"
    else:
        selected_profile = None
        profile_source = "none"
    if selected_profile is not None and selected_profile not in provider_profiles:
        raise ProfileError(
            "unknown-embedding-profile",
            "the selected embedding profile is not configured",
        )

    return RuntimeConfig(
        repo_root=root,
        profile_path=profile_path,
        profile_loaded=payload is not None,
        profile_sha256=sha256_json(payload) if payload is not None else None,
        database=resolved_database,
        portable_store=resolved_store,
        embedding_profile=selected_profile,
        provider_profiles=provider_profiles,
        sources={
            "database": database_source,
            "portable_store": store_source,
            "embedding_profile": profile_source,
        },
    )
