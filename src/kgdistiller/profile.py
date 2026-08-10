"""Machine-local runtime profile discovery and deterministic CLI precedence."""

from __future__ import annotations

import copy
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import parse_contract_json, sha256_json, validate_contract


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
    descriptor: int | None = None
    missing = False
    permission_denied = False
    open_failure: tuple[str, str] | None = None
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        missing = True
    except IsADirectoryError:
        open_failure = ("invalid-profile", "the local profile is not a regular file")
    except PermissionError:
        permission_denied = True
    except OSError:
        open_failure = ("profile-unreadable", "the local profile could not be read")

    if permission_denied:
        is_directory = False
        try:
            is_directory = path.is_dir()
        except OSError:
            pass
        open_failure = (
            ("invalid-profile", "the local profile is not a regular file")
            if is_directory
            else ("profile-unreadable", "the local profile could not be read")
        )

    if missing:
        if required:
            raise ProfileError("profile-not-found", "the explicit local profile does not exist")
        return None
    if open_failure is not None:
        raise ProfileError(*open_failure)

    raw: bytes | None = None
    read_failure: tuple[str, str] | None = None
    try:
        if descriptor is None:
            read_failure = ("profile-unreadable", "the local profile could not be read")
        elif not stat.S_ISREG(os.fstat(descriptor).st_mode):
            read_failure = ("invalid-profile", "the local profile is not a regular file")
        else:
            handle = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = None
            with handle:
                raw = handle.read(MAX_LOCAL_PROFILE_BYTES + 1)
    except OSError:
        read_failure = ("profile-unreadable", "the local profile could not be read")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if read_failure is not None:
        raise ProfileError(*read_failure)
    if raw is None:
        raise ProfileError("profile-unreadable", "the local profile could not be read")
    if len(raw) > MAX_LOCAL_PROFILE_BYTES:
        raise ProfileError("profile-too-large", "the local profile exceeds 1 MiB")

    validated_payload: dict[str, Any] | None = None
    validation_failed = False
    try:
        text = raw.decode("utf-8")
        payload = parse_contract_json(text)
        validated_payload = validate_contract(payload)
        # Ensure every schema-valid string also has a canonical UTF-8 form;
        # JSON escape sequences can otherwise introduce lone surrogates.
        sha256_json(validated_payload)
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError):
        validation_failed = True
    if validation_failed:
        # Raise outside the exception handler so raw profile bytes cannot remain
        # reachable through ProfileError.__cause__ or __context__.
        raise ProfileError("invalid-profile", "the local profile failed validation")
    if validated_payload is None:
        raise ProfileError("invalid-profile", "the local profile failed validation")
    return validated_payload


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
