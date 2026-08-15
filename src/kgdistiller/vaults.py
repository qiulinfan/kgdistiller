"""Machine-local Vault registration and portable Vault routing.

The registry deliberately contains only machine-local absolute paths.  Every
portable setting lives in ``.kgdistiller/vault.json`` inside the Vault.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from .contracts import ContractError, canonical_json, sha256_json, validate_contract


REGISTRY_SCHEMA = "qlkg-vault-registry-v1"
VAULT_SCHEMA = "qlkg-vault-v1"
REPORT_SCHEMA = "qlkg-vault-report-v1"
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_REGISTRY_ENTRIES = 256
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ID_BYTES = 64
MAX_LABEL_BYTES = 256
MAX_DESCRIPTION_BYTES = 4096
MAX_PATH_BYTES = 4096
MAX_GLOB_BYTES = 512
MAX_GLOBS = 64
MAX_MANAGED_MARKDOWN_FILES = 100_000
MAX_MANAGED_DEPTH = 64
MAX_MANAGED_MARKDOWN_BYTES = 8 * 1024 * 1024
MAX_MANAGED_MARKDOWN_TOTAL_BYTES = 512 * 1024 * 1024
VAULT_ID_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_LAYOUT_DIRECTORIES = (
    ".kgdistiller",
    ".kgdistiller/sources",
    ".kgdistiller/graph",
    ".kgdistiller/build",
)


class VaultError(RuntimeError):
    """A stable, structured Vault registry or routing failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "kgdistiller-vault-error",
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class VaultRegistration:
    id: str
    path: Path


@dataclass(frozen=True)
class Vault:
    id: str
    label: str
    root: Path
    manifest: dict[str, Any]
    concept_root: Path
    field_root: Path
    topic_root: Path

    def card(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "path": str(self.root)}


@dataclass(frozen=True)
class ManagedMarkdownFile:
    """One stable, pinned-I/O snapshot from a configured managed root."""

    path: Path
    authority: str
    data: bytes
    raw_sha256: str


@dataclass(frozen=True)
class VaultRegistry:
    home: Path
    path: Path
    generation: str
    registrations: tuple[VaultRegistration, ...]
    vaults: tuple[Vault, ...]
    exists: bool

    def payload(self) -> dict[str, Any]:
        return {
            "schema": REGISTRY_SCHEMA,
            "vaults": [
                {"id": registration.id, "path": str(registration.path)}
                for registration in self.registrations
            ],
        }


def _utf8_length(value: str, *, field: str, maximum: int) -> int:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise VaultError("unsafe-value", f"{field} is not valid UTF-8") from error
    if size > maximum:
        raise VaultError(
            "value-too-long",
            f"{field} exceeds {maximum} UTF-8 bytes",
        )
    return size


def _validate_vault_id(value: Any, *, field: str = "vault id") -> str:
    if not isinstance(value, str) or not value or not VAULT_ID_RE.fullmatch(value):
        raise VaultError(
            "invalid-vault-id",
            f"{field} must be a lowercase namespace using letters, digits, '.', '_', or '-'",
        )
    _utf8_length(value, field=field, maximum=MAX_ID_BYTES)
    return value


def _validate_text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise VaultError("unsafe-value", f"{field} must be {qualifier}")
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise VaultError(
            "unsafe-value",
            f"{field} must not contain surrounding whitespace or control characters",
        )
    _utf8_length(value, field=field, maximum=maximum)
    return value


def _is_reparse_stat(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _is_link_or_reparse(path: Path, metadata: os.stat_result | None = None) -> bool:
    metadata = metadata if metadata is not None else _lstat(path)
    return bool(
        metadata is not None
        and (stat.S_ISLNK(metadata.st_mode) or _is_reparse_stat(metadata))
    )


def _path_identity(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _same_path(left: Path | str, right: Path | str) -> bool:
    return _path_identity(left) == _path_identity(right)


def _contains_path(root: Path, candidate: Path, *, allow_equal: bool = True) -> bool:
    root_identity = _path_identity(root)
    candidate_identity = _path_identity(candidate)
    try:
        common = os.path.normcase(os.path.commonpath((root_identity, candidate_identity)))
    except ValueError:
        return False
    return common == root_identity and (allow_equal or candidate_identity != root_identity)


def _is_filesystem_root(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return _same_path(absolute, absolute.parent)


def _absolute_path(value: Path | str, *, field: str) -> Path:
    raw = os.fspath(value)
    if "\0" in raw:
        raise VaultError("unsafe-path", f"{field} contains a NUL byte")
    _utf8_length(raw, field=field, maximum=MAX_PATH_BYTES)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(os.path.abspath(os.fspath(path)))
    else:
        path = Path(os.path.abspath(os.fspath(path)))
    if _is_filesystem_root(path):
        raise VaultError("unsafe-path", f"{field} must not be a filesystem root")
    _utf8_length(str(path), field=field, maximum=MAX_PATH_BYTES)
    return path


def _remove_created_directories(created: list[Path]) -> None:
    for directory in reversed(created):
        try:
            metadata = _lstat(directory)
            if (
                metadata is not None
                and stat.S_ISDIR(metadata.st_mode)
                and not _is_link_or_reparse(directory, metadata)
            ):
                directory.rmdir()
        except OSError:
            pass


def _safe_directory_chain(
    path: Path,
    *,
    field: str,
    error_code: str,
    create: bool,
) -> list[Path]:
    """Validate/create a directory without traversing link-like ancestors."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    anchor = Path(absolute.anchor)
    anchor_metadata = _lstat(anchor)
    if anchor_metadata is None or not stat.S_ISDIR(anchor_metadata.st_mode):
        raise VaultError(error_code, f"{field} has no trusted filesystem anchor: {anchor}")
    try:
        relative_parts = absolute.relative_to(anchor).parts
    except ValueError as error:
        raise VaultError(error_code, f"{field} is not below its filesystem anchor") from error

    created: list[Path] = []
    current = anchor
    try:
        for part in relative_parts:
            candidate = current / part
            metadata = _lstat(candidate)
            if metadata is None:
                if not create:
                    break
                parent_metadata = _lstat(current)
                if (
                    parent_metadata is None
                    or not stat.S_ISDIR(parent_metadata.st_mode)
                    or _is_link_or_reparse(current, parent_metadata)
                ):
                    raise VaultError(
                        error_code,
                        f"{field} has an unsafe directory ancestor: {current}",
                    )
                try:
                    os.mkdir(candidate)
                except OSError as error:
                    raise VaultError(
                        error_code, f"cannot create {field}: {candidate}"
                    ) from error
                created.append(candidate)
                metadata = _lstat(candidate)
            if (
                metadata is None
                or not stat.S_ISDIR(metadata.st_mode)
                or _is_link_or_reparse(candidate, metadata)
            ):
                raise VaultError(
                    error_code,
                    f"{field} must use ordinary, non-reparse directory components: {candidate}",
                )
            current = candidate
        if create:
            try:
                resolved = absolute.resolve(strict=True)
            except OSError as error:
                raise VaultError(error_code, f"cannot resolve {field}: {absolute}") from error
            if not _same_path(absolute, resolved):
                raise VaultError(
                    error_code,
                    f"{field} traverses a symlink or reparse point: {absolute}",
                )
        return created
    except BaseException:
        _remove_created_directories(created)
        raise


def kgdistiller_home(explicit: Path | str | None = None, *, create: bool = False) -> Path:
    """Return the selected machine-local kgdistiller home directory."""

    if explicit is not None:
        selected = Path(explicit)
    elif "KGDISTILLER_HOME" in os.environ:
        raw = os.environ["KGDISTILLER_HOME"]
        if not raw:
            raise VaultError("invalid-home", "KGDISTILLER_HOME must not be empty")
        selected = Path(raw)
    else:
        selected = Path.home() / ".kgdistiller"
    home = _absolute_path(selected, field="kgdistiller home")
    created = _safe_directory_chain(
        home,
        field="kgdistiller home",
        error_code="invalid-home",
        create=create,
    )
    metadata = _lstat(home)
    try:
        if metadata is not None:
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or _is_link_or_reparse(home, metadata)
            ):
                raise VaultError(
                    "invalid-home",
                    f"kgdistiller home must be an ordinary, non-reparse directory: {home}",
                )
            try:
                resolved = home.resolve(strict=True)
            except OSError as error:
                raise VaultError(
                    "invalid-home", f"cannot resolve kgdistiller home: {home}"
                ) from error
            if not _same_path(home, resolved):
                raise VaultError(
                    "invalid-home",
                    f"kgdistiller home traverses a symlink or reparse point: {home}",
                )
    except BaseException:
        _remove_created_directories(created)
        raise
    return home


def registry_path(home: Path | str | None = None) -> Path:
    return kgdistiller_home(home) / "vaults.json"


def _read_bounded_regular(path: Path, *, maximum: int, kind: str) -> bytes:
    metadata = _lstat(path)
    if metadata is None:
        raise VaultError(f"missing-{kind}", f"missing {kind}: {path}")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_link_or_reparse(path, metadata)
    ):
        raise VaultError(
            f"invalid-{kind}", f"{kind} must be an ordinary, non-reparse file: {path}"
        )
    if metadata.st_size > maximum:
        raise VaultError(
            f"{kind}-too-large", f"{kind} exceeds {maximum} bytes: {path}"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VaultError(f"invalid-{kind}", f"cannot open {kind}: {path}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_stat(opened):
            raise VaultError(f"invalid-{kind}", f"{kind} is not an ordinary file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise VaultError(
                    f"{kind}-too-large", f"{kind} exceeds {maximum} bytes: {path}"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _strict_json(data: bytes, *, kind: str, path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate object key {key!r}")
            value[key] = item
        return value

    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise VaultError(f"invalid-{kind}", f"malformed {kind}: {path}: {error}") from error


def _validate_contract(payload: Any, discriminator: str, *, kind: str) -> dict[str, Any]:
    try:
        validated = validate_contract(payload)
    except ContractError as error:
        raise VaultError(f"invalid-{kind}", str(error)) from error
    if validated.get("schema") != discriminator:
        raise VaultError(
            f"invalid-{kind}", f"expected {discriminator}, got {validated.get('schema')!r}"
        )
    return validated


def _registered_path(value: Any, *, index: int) -> Path:
    if not isinstance(value, str) or not value:
        raise VaultError("invalid-registry", f"vaults[{index}].path must be an absolute path")
    _utf8_length(value, field=f"vaults[{index}].path", maximum=MAX_PATH_BYTES)
    if "\0" in value or not Path(value).is_absolute():
        raise VaultError("invalid-registry", f"vaults[{index}].path must be an absolute path")
    parts = re.split(r"[\\/]", value)
    if any(part in {".", ".."} for part in parts):
        raise VaultError("invalid-registry", f"vaults[{index}].path is not canonical")
    path = Path(os.path.abspath(value))
    if _is_filesystem_root(path):
        raise VaultError("invalid-registry", f"vaults[{index}].path is a filesystem root")
    return path


def _normalize_registry(payload: Any) -> tuple[dict[str, Any], tuple[VaultRegistration, ...]]:
    validated = _validate_contract(payload, REGISTRY_SCHEMA, kind="registry")
    raw_vaults = validated["vaults"]
    if len(raw_vaults) > MAX_REGISTRY_ENTRIES:
        raise VaultError(
            "invalid-registry",
            f"registry contains more than {MAX_REGISTRY_ENTRIES} Vaults",
        )
    registrations: list[VaultRegistration] = []
    seen_ids: set[str] = set()
    seen_paths: dict[str, str] = {}
    for index, raw in enumerate(raw_vaults):
        vault_id = _validate_vault_id(raw.get("id"), field=f"vaults[{index}].id")
        if vault_id in seen_ids:
            raise VaultError("duplicate-vault-id", f"duplicate Vault id: {vault_id}")
        path = _registered_path(raw.get("path"), index=index)
        identity = _path_identity(path)
        if identity in seen_paths:
            raise VaultError(
                "duplicate-vault-path",
                f"Vaults {seen_paths[identity]!r} and {vault_id!r} have the same path",
            )
        for existing in registrations:
            if _contains_path(existing.path, path, allow_equal=False) or _contains_path(
                path, existing.path, allow_equal=False
            ):
                raise VaultError(
                    "overlapping-vault-roots",
                    f"Vault roots overlap: {existing.id!r} and {vault_id!r}",
                )
        seen_ids.add(vault_id)
        seen_paths[identity] = vault_id
        registrations.append(VaultRegistration(vault_id, path))
    registrations.sort(key=lambda item: item.id)
    normalized = {
        "schema": REGISTRY_SCHEMA,
        "vaults": [
            {"id": registration.id, "path": str(registration.path)}
            for registration in registrations
        ],
    }
    return normalized, tuple(registrations)


def registry_generation(payload: Mapping[str, Any]) -> str:
    """Return the deterministic digest of a normalized registry contract."""

    normalized, _ = _normalize_registry(dict(payload))
    return sha256_json(normalized)


def _read_registry(home: Path, *, validate_vaults: bool) -> VaultRegistry:
    path = home / "vaults.json"
    metadata = _lstat(path)
    if metadata is None:
        payload: Any = {"schema": REGISTRY_SCHEMA, "vaults": []}
        exists = False
    else:
        data = _read_bounded_regular(path, maximum=MAX_REGISTRY_BYTES, kind="registry")
        payload = _strict_json(data, kind="registry", path=path)
        exists = True
    normalized, registrations = _normalize_registry(payload)
    vaults = (
        tuple(load_vault(item.path, expected_id=item.id) for item in registrations)
        if validate_vaults
        else ()
    )
    return VaultRegistry(
        home=home,
        path=path,
        generation=sha256_json(normalized),
        registrations=registrations,
        vaults=vaults,
        exists=exists,
    )


def load_registry(
    home: Path | str | None = None,
    *,
    validate_vaults: bool = True,
) -> VaultRegistry:
    """Load the selected registry and, by default, every registered Vault."""

    selected_home = kgdistiller_home(home)
    return _read_registry(selected_home, validate_vaults=validate_vaults)


def _portable_parts(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise VaultError("unsafe-path", f"{field} must be a non-empty portable relative path")
    _utf8_length(value, field=field, maximum=MAX_PATH_BYTES)
    if "\0" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise VaultError("unsafe-path", f"{field} is not a portable relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise VaultError("unsafe-path", f"{field} is not a canonical relative path")
    for part in relative.parts:
        if (
            part.endswith((" ", "."))
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        ):
            raise VaultError("unsafe-path", f"{field} contains an unsafe path segment")
    return relative.parts


def _validate_glob(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise VaultError("unsafe-glob", f"{field} must be a non-empty portable glob")
    _utf8_length(value, field=field, maximum=MAX_GLOB_BYTES)
    if (
        "\0" in value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise VaultError("unsafe-glob", f"{field} is not a portable relative glob")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise VaultError("unsafe-glob", f"{field} is not a canonical relative glob")
    if any(
        any(ord(character) < 32 or ord(character) == 127 for character in part)
        or any(character in '<>:"|' for character in part)
        for part in parts
    ):
        raise VaultError("unsafe-glob", f"{field} contains an unsafe glob segment")
    return value


def _validate_manifest_values(payload: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    _validate_vault_id(payload.get("id"))
    _validate_text(
        payload.get("label"), field="vault label", maximum=MAX_LABEL_BYTES, allow_empty=False
    )
    _validate_text(
        payload.get("description"),
        field="vault description",
        maximum=MAX_DESCRIPTION_BYTES,
        allow_empty=True,
    )
    roots = {
        field: _portable_parts(payload.get(field), field=field)
        for field in ("concept_root", "field_root", "topic_root")
    }
    for field, parts in roots.items():
        if parts[0].casefold() == ".kgdistiller":
            raise VaultError("unsafe-path", f"{field} must not be inside .kgdistiller")
    root_items = list(roots.items())
    for index, (left_field, left) in enumerate(root_items):
        left_folded = tuple(part.casefold() if os.name == "nt" else part for part in left)
        for right_field, right in root_items[index + 1 :]:
            right_folded = tuple(
                part.casefold() if os.name == "nt" else part for part in right
            )
            common = min(len(left_folded), len(right_folded))
            if left_folded[:common] == right_folded[:common]:
                raise VaultError(
                    "overlapping-managed-roots",
                    f"{left_field} and {right_field} overlap",
                )
    for field in ("source_include", "source_exclude"):
        values = payload.get(field)
        if not isinstance(values, list) or len(values) > MAX_GLOBS:
            raise VaultError(
                "invalid-vault",
                f"{field} must contain at most {MAX_GLOBS} globs",
            )
        if field == "source_include" and not values:
            raise VaultError("invalid-vault", "source_include must not be empty")
        for index, value in enumerate(values):
            _validate_glob(value, field=f"{field}[{index}]")
    return roots


def _walk_contained(
    root: Path,
    parts: tuple[str, ...],
    *,
    field: str,
    final_kind: str,
) -> Path:
    candidate = root.joinpath(*parts)
    if not _contains_path(root, candidate, allow_equal=False):
        raise VaultError("unsafe-path", f"{field} escapes the Vault")
    current = root
    for index, part in enumerate(parts):
        current /= part
        metadata = _lstat(current)
        if metadata is None:
            raise VaultError("invalid-vault-layout", f"missing {field}: {current}")
        if _is_link_or_reparse(current, metadata):
            raise VaultError(
                "unsafe-path", f"{field} traverses a symlink or reparse point: {current}"
            )
        is_final = index == len(parts) - 1
        expected_file = is_final and final_kind == "file"
        if expected_file:
            valid_kind = stat.S_ISREG(metadata.st_mode)
        else:
            valid_kind = stat.S_ISDIR(metadata.st_mode)
        if not valid_kind:
            raise VaultError(
                "invalid-vault-layout",
                f"{field} is not an ordinary {final_kind if is_final else 'directory'}: {current}",
            )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise VaultError("unsafe-path", f"cannot resolve {field}: {candidate}") from error
    if not _same_path(candidate, resolved) or not _contains_path(root, resolved, allow_equal=False):
        raise VaultError("unsafe-path", f"{field} resolves outside the Vault")
    return candidate


def _canonical_vault_root(path: Path | str) -> Path:
    absolute = _absolute_path(path, field="vault path")
    metadata = _lstat(absolute)
    if metadata is None:
        raise VaultError("missing-vault", f"Vault does not exist: {absolute}")
    if not stat.S_ISDIR(metadata.st_mode) or _is_link_or_reparse(absolute, metadata):
        raise VaultError(
            "invalid-vault", f"Vault root must be an ordinary, non-reparse directory: {absolute}"
        )
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise VaultError("invalid-vault", f"cannot resolve Vault root: {absolute}") from error
    if _is_filesystem_root(resolved):
        raise VaultError("invalid-vault", "Vault root must not be a filesystem root")
    if not _same_path(absolute, resolved):
        raise VaultError(
            "invalid-vault", f"Vault root traverses a symlink or reparse point: {absolute}"
        )
    _utf8_length(str(resolved), field="vault path", maximum=MAX_PATH_BYTES)
    return resolved


def load_vault(path: Path | str, *, expected_id: str | None = None) -> Vault:
    """Validate and load one portable Vault manifest and its F1 layout."""

    root = _canonical_vault_root(path)
    _walk_contained(root, (".kgdistiller",), field="metadata root", final_kind="directory")
    manifest_path = _walk_contained(
        root,
        (".kgdistiller", "vault.json"),
        field="vault manifest",
        final_kind="file",
    )
    data = _read_bounded_regular(
        manifest_path, maximum=MAX_MANIFEST_BYTES, kind="vault-manifest"
    )
    payload = _strict_json(data, kind="vault-manifest", path=manifest_path)
    manifest = _validate_contract(payload, VAULT_SCHEMA, kind="vault")
    roots = _validate_manifest_values(manifest)
    if expected_id is not None and manifest["id"] != expected_id:
        raise VaultError(
            "vault-id-mismatch",
            f"registry id {expected_id!r} does not match manifest id {manifest['id']!r}",
        )
    for relative in _LAYOUT_DIRECTORIES[1:]:
        _walk_contained(
            root,
            tuple(PurePosixPath(relative).parts),
            field=relative,
            final_kind="directory",
        )
    managed = {
        field: _walk_contained(
            root,
            parts,
            field=field,
            final_kind="directory",
        )
        for field, parts in roots.items()
    }
    return Vault(
        id=manifest["id"],
        label=manifest["label"],
        root=root,
        manifest=manifest,
        concept_root=managed["concept_root"],
        field_root=managed["field_root"],
        topic_root=managed["topic_root"],
    )


def _discover_managed_markdown(vault: Vault, root: Path) -> tuple[Path, ...]:
    """Discover bounded Markdown names without treating discovery as file I/O."""

    selected = next(
        (
            candidate
            for candidate in (vault.concept_root, vault.field_root, vault.topic_root)
            if _same_path(root, candidate)
        ),
        None,
    )
    if selected is None:
        raise VaultError(
            "unsafe-path",
            "managed Markdown discovery requires a configured concept, field, or topic root",
        )
    inventory: list[Path] = []
    pending: list[tuple[Path, int]] = [(selected, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > MAX_MANAGED_DEPTH:
            raise VaultError(
                "managed-root-too-deep",
                f"managed Markdown depth exceeds {MAX_MANAGED_DEPTH}: {directory}",
            )
        try:
            with os.scandir(directory) as scanned:
                entries = sorted(scanned, key=lambda item: item.name)
        except OSError as error:
            raise VaultError(
                "invalid-vault-layout", f"cannot inspect managed root: {directory}"
            ) from error
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise VaultError(
                    "invalid-vault-layout", f"cannot inspect managed path: {path}"
                ) from error
            if _is_link_or_reparse(path, metadata):
                raise VaultError(
                    "unsafe-path",
                    f"managed root contains a symlink or reparse point: {path}",
                )
            if stat.S_ISDIR(metadata.st_mode):
                pending.append((path, depth + 1))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise VaultError(
                    "invalid-vault-layout",
                    f"managed root contains a non-ordinary file: {path}",
                )
            if path.suffix.casefold() != ".md":
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                raise VaultError("unsafe-path", f"cannot resolve managed note: {path}") from error
            if not _same_path(path, resolved) or not _contains_path(
                vault.root, resolved, allow_equal=False
            ):
                raise VaultError("unsafe-path", f"managed note resolves outside the Vault: {path}")
            inventory.append(path)
            if len(inventory) > MAX_MANAGED_MARKDOWN_FILES:
                raise VaultError(
                    "managed-root-too-large",
                    f"managed Markdown inventory exceeds {MAX_MANAGED_MARKDOWN_FILES} files",
                )
    return tuple(sorted(inventory, key=lambda item: item.relative_to(vault.root).as_posix()))


def iter_managed_markdown(
    vault: Vault, root: Path
) -> tuple[ManagedMarkdownFile, ...]:
    """Return stable bytes for one bounded managed Markdown inventory.

    Discovery only establishes candidate Vault-relative names. Every file is
    then read through the source archive's pinned-ancestor primitive, and the
    complete inventory is repeated before the snapshot is accepted.
    """

    from .source_archive import read_vault_relative_regular

    discovered = _discover_managed_markdown(vault, root)
    snapshots: list[ManagedMarkdownFile] = []
    total = 0
    for path in discovered:
        authority = path.relative_to(vault.root).as_posix()
        data = read_vault_relative_regular(
            vault,
            authority,
            maximum=MAX_MANAGED_MARKDOWN_BYTES,
        )
        total += len(data)
        if total > MAX_MANAGED_MARKDOWN_TOTAL_BYTES:
            raise VaultError(
                "managed-root-too-large",
                "managed Markdown snapshots exceed "
                f"{MAX_MANAGED_MARKDOWN_TOTAL_BYTES} total bytes",
            )
        snapshots.append(
            ManagedMarkdownFile(
                path=path,
                authority=authority,
                data=data,
                raw_sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    repeated = _discover_managed_markdown(vault, root)
    if discovered != repeated:
        raise VaultError(
            "unstable-managed-root",
            "managed Markdown inventory changed while it was being read",
        )
    return tuple(snapshots)


def _acquire_lock(handle: Any) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise VaultError(
            "registry-lock-conflict",
            "another kgdistiller process holds the Vault registry lock",
        ) from error


def _release_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _registry_lock(home: Path) -> Iterator[None]:
    selected = kgdistiller_home(home, create=True)
    lock_path = selected / "vaults.lock"
    metadata = _lstat(lock_path)
    if metadata is not None and (
        not stat.S_ISREG(metadata.st_mode) or _is_link_or_reparse(lock_path, metadata)
    ):
        raise VaultError(
            "invalid-registry-lock",
            f"registry lock must be an ordinary, non-reparse file: {lock_path}",
        )
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    acquired = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise VaultError(
            "invalid-registry-lock", f"cannot safely open registry lock: {lock_path}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        current = _lstat(lock_path)
        if (
            current is None
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or _is_reparse_stat(opened)
            or _is_link_or_reparse(lock_path, current)
            or opened.st_ino == 0
            or current.st_ino == 0
            or not os.path.samestat(opened, current)
            or opened.st_nlink != 1
            or current.st_nlink != 1
        ):
            raise VaultError(
                "invalid-registry-lock",
                "registry lock handle does not identify the ordinary file at its selected path",
            )
        try:
            resolved = lock_path.resolve(strict=True)
        except OSError as error:
            raise VaultError(
                "invalid-registry-lock", f"cannot resolve registry lock: {lock_path}"
            ) from error
        final_metadata = _lstat(lock_path)
        if (
            final_metadata is None
            or not stat.S_ISREG(final_metadata.st_mode)
            or _is_link_or_reparse(lock_path, final_metadata)
            or final_metadata.st_ino == 0
            or final_metadata.st_nlink != 1
            or not os.path.samestat(opened, final_metadata)
            or not _same_path(lock_path, resolved)
            or not _contains_path(selected, resolved, allow_equal=False)
        ):
            raise VaultError(
                "invalid-registry-lock",
                "registry lock changed or escaped its selected home during open",
            )
        handle = os.fdopen(descriptor, "r+b")
        descriptor = -1
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        _acquire_lock(handle)
        acquired = True
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        else:
            if acquired:
                _release_lock(handle)
            handle.close()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_registry(path: Path, payload: dict[str, Any]) -> None:
    normalized, _ = _normalize_registry(payload)
    content = canonical_json(normalized).encode("utf-8")
    if len(content) > MAX_REGISTRY_BYTES:
        raise VaultError(
            "registry-too-large", f"registry exceeds {MAX_REGISTRY_BYTES} bytes"
        )
    metadata = _lstat(path)
    if metadata is not None and (
        not stat.S_ISREG(metadata.st_mode) or _is_link_or_reparse(path, metadata)
    ):
        raise VaultError(
            "invalid-registry", f"registry must be an ordinary, non-reparse file: {path}"
        )
    descriptor, temporary_name = tempfile.mkstemp(prefix=".vaults.json.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        current = _lstat(path)
        if current is not None and (
            not stat.S_ISREG(current.st_mode) or _is_link_or_reparse(path, current)
        ):
            raise VaultError("invalid-registry", "registry changed into an unsafe file")
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_registered_vaults(registry: VaultRegistry) -> None:
    for registration in registry.registrations:
        load_vault(registration.path, expected_id=registration.id)


def _ensure_registration_available(
    registry: VaultRegistry,
    *,
    vault_id: str,
    root: Path,
) -> None:
    identity = _path_identity(root)
    for registration in registry.registrations:
        if registration.id == vault_id:
            raise VaultError("duplicate-vault-id", f"Vault id is already registered: {vault_id}")
        if _path_identity(registration.path) == identity:
            raise VaultError(
                "duplicate-vault-path",
                f"Vault path is already registered as {registration.id!r}: {root}",
            )
        if _contains_path(registration.path, root, allow_equal=False) or _contains_path(
            root, registration.path, allow_equal=False
        ):
            raise VaultError(
                "overlapping-vault-roots",
                f"Vault root overlaps registered Vault {registration.id!r}: {root}",
            )


def _payload_with_registration(
    registry: VaultRegistry, registration: VaultRegistration
) -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "vaults": [
            {"id": item.id, "path": str(item.path)}
            for item in sorted(
                (*registry.registrations, registration), key=lambda item: item.id
            )
        ],
    }


def _report(
    action: str,
    *,
    status: str,
    generation: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": REPORT_SCHEMA,
        "action": action,
        "status": status,
        "registry_generation": generation,
        "result": result,
    }
    return _validate_contract(payload, REPORT_SCHEMA, kind="vault-report")


def _vault_result(vault: Vault) -> dict[str, Any]:
    return {"kind": "vault", "vault": vault.card()}


def add_vault(path: Path | str, *, home: Path | str | None = None) -> dict[str, Any]:
    """Register an existing valid native Vault."""

    selected_home = kgdistiller_home(home, create=True)
    with _registry_lock(selected_home):
        current = _read_registry(selected_home, validate_vaults=False)
        _validate_registered_vaults(current)
        vault = load_vault(path)
        _ensure_registration_available(current, vault_id=vault.id, root=vault.root)
        payload = _payload_with_registration(
            current, VaultRegistration(vault.id, vault.root)
        )
        _atomic_write_registry(current.path, payload)
        generation = registry_generation(payload)
    return _report(
        "add", status="ok", generation=generation, result=_vault_result(vault)
    )


def _default_manifest(vault_id: str, label: str) -> dict[str, Any]:
    payload = {
        "schema": VAULT_SCHEMA,
        "id": vault_id,
        "label": label,
        "description": "",
        "concept_root": "Knowledge/Concepts",
        "field_root": "Knowledge/Fields",
        "topic_root": "Knowledge/Topics",
        "source_include": ["**/*.md", "**/*.typ", "**/*.tex"],
        "source_exclude": ["Knowledge/**", ".kgdistiller/**"],
    }
    validated = _validate_contract(payload, VAULT_SCHEMA, kind="vault")
    _validate_manifest_values(validated)
    return validated


def _compatible_empty_layout(root: Path, expected: set[str]) -> None:
    if not root.exists():
        return
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except OSError as error:
            raise VaultError("invalid-vault-layout", f"cannot inspect Vault layout: {current}") from error
        for entry in entries:
            relative = entry.relative_to(root).as_posix()
            metadata = _lstat(entry)
            if (
                relative not in expected
                or metadata is None
                or not stat.S_ISDIR(metadata.st_mode)
                or _is_link_or_reparse(entry, metadata)
            ):
                raise VaultError(
                    "vault-not-empty",
                    f"Vault init requires an empty or compatible new layout; found {relative}",
                )
            pending.append(entry)


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise VaultError(
                "manifest-exists", f"Vault manifest already exists and was not overwritten: {path}"
            ) from error
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if descriptor >= 0:
            os.close(descriptor)


def _cleanup_initialized(
    *,
    root: Path,
    created_root_directories: list[Path],
    created_directories: list[Path],
    manifest_path: Path,
    manifest_content: bytes,
    manifest_created: bool,
) -> None:
    if manifest_created:
        try:
            metadata = _lstat(manifest_path)
            if (
                metadata is not None
                and stat.S_ISREG(metadata.st_mode)
                and not _is_link_or_reparse(manifest_path, metadata)
                and _read_bounded_regular(
                    manifest_path, maximum=MAX_MANIFEST_BYTES, kind="vault-manifest"
                )
                == manifest_content
            ):
                manifest_path.unlink()
        except (OSError, VaultError):
            pass
    for directory in reversed(created_directories):
        try:
            directory.rmdir()
        except OSError:
            pass
    _remove_created_directories(created_root_directories)


def init_vault(
    path: Path | str,
    *,
    vault_id: str,
    label: str,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Initialize a new/empty native Vault and register it."""

    vault_id = _validate_vault_id(vault_id)
    label = _validate_text(
        label, field="vault label", maximum=MAX_LABEL_BYTES, allow_empty=False
    )
    manifest = _default_manifest(vault_id, label)
    root = _absolute_path(path, field="vault path")
    _safe_directory_chain(
        root,
        field="Vault root",
        error_code="invalid-vault",
        create=False,
    )
    selected_home = kgdistiller_home(home, create=True)
    created_root_directories: list[Path] = []
    created_directories: list[Path] = []
    manifest_path = root / ".kgdistiller" / "vault.json"
    manifest_content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    manifest_created = False
    with _registry_lock(selected_home):
        current = _read_registry(selected_home, validate_vaults=False)
        _validate_registered_vaults(current)
        _ensure_registration_available(current, vault_id=vault_id, root=root)
        try:
            created_root_directories = _safe_directory_chain(
                root,
                field="Vault root",
                error_code="invalid-vault",
                create=True,
            )
            root = _canonical_vault_root(root)
            manifest_path = root / ".kgdistiller" / "vault.json"
            if _lstat(manifest_path) is not None:
                raise VaultError(
                    "manifest-exists",
                    f"Vault manifest already exists and was not overwritten: {manifest_path}",
                )
            expected_directories = {
                *_LAYOUT_DIRECTORIES,
                manifest["concept_root"],
                manifest["field_root"],
                manifest["topic_root"],
                "Knowledge",
            }
            _compatible_empty_layout(root, expected_directories)
            for relative in sorted(
                expected_directories,
                key=lambda value: (len(PurePosixPath(value).parts), value),
            ):
                directory = root.joinpath(*PurePosixPath(relative).parts)
                metadata = _lstat(directory)
                if metadata is None:
                    directory.mkdir()
                    created_directories.append(directory)
                elif (
                    not stat.S_ISDIR(metadata.st_mode)
                    or _is_link_or_reparse(directory, metadata)
                ):
                    raise VaultError(
                        "invalid-vault-layout", f"layout path is not an ordinary directory: {directory}"
                    )
            _exclusive_write(manifest_path, manifest_content)
            manifest_created = True
            vault = load_vault(root, expected_id=vault_id)
            payload = _payload_with_registration(
                current, VaultRegistration(vault.id, vault.root)
            )
            _atomic_write_registry(current.path, payload)
            generation = registry_generation(payload)
        except BaseException:
            _cleanup_initialized(
                root=root,
                created_root_directories=created_root_directories,
                created_directories=created_directories,
                manifest_path=manifest_path,
                manifest_content=manifest_content,
                manifest_created=manifest_created,
            )
            raise
    return _report(
        "init", status="ok", generation=generation, result=_vault_result(vault)
    )


def remove_vault(
    vault_id: str, *, home: Path | str | None = None
) -> dict[str, Any]:
    """Remove only one machine-local registration; Vault content is untouched."""

    vault_id = _validate_vault_id(vault_id)
    selected_home = kgdistiller_home(home, create=True)
    with _registry_lock(selected_home):
        current = _read_registry(selected_home, validate_vaults=False)
        if vault_id not in {item.id for item in current.registrations}:
            raise VaultError("vault-not-registered", f"Vault is not registered: {vault_id}")
        payload = {
            "schema": REGISTRY_SCHEMA,
            "vaults": [
                {"id": item.id, "path": str(item.path)}
                for item in current.registrations
                if item.id != vault_id
            ],
        }
        _atomic_write_registry(current.path, payload)
        generation = registry_generation(payload)
    return _report(
        "remove",
        status="ok",
        generation=generation,
        result={"kind": "removed-vault", "id": vault_id},
    )


def list_vaults(*, home: Path | str | None = None) -> dict[str, Any]:
    registry = load_registry(home, validate_vaults=True)
    return _report(
        "list",
        status="ok",
        generation=registry.generation,
        result={
            "kind": "vault-list",
            "vaults": [vault.card() for vault in registry.vaults],
        },
    )


def _windows_path_semantics() -> bool:
    return os.name == "nt"


def _glob_matches(relative: str, pattern: str) -> bool:
    if _windows_path_semantics():
        relative = relative.casefold()
        pattern = pattern.casefold()
    path_parts = relative.split("/")
    pattern_parts = pattern.split("/")

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], segment)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _source_relative(vault: Vault, file_path: Path) -> str:
    try:
        relative_native = os.path.relpath(file_path, vault.root)
    except ValueError as error:
        raise VaultError("source-outside-vaults", f"source is outside the Vault: {file_path}") from error
    if relative_native == os.curdir or relative_native == os.pardir or relative_native.startswith(
        os.pardir + os.sep
    ):
        raise VaultError("source-outside-vaults", f"source is outside the Vault: {file_path}")
    relative = PurePosixPath(*Path(relative_native).parts).as_posix()
    _portable_parts(relative, field="source path")
    return relative


def _validate_source_path(vault: Vault, file_path: Path) -> str:
    relative = _source_relative(vault, file_path)
    parts = tuple(PurePosixPath(relative).parts)
    _walk_contained(
        vault.root, parts, field="source file", final_kind="file"
    )
    managed_roots = {
        tuple(PurePosixPath(str(vault.manifest[field])).parts)
        for field in ("concept_root", "field_root", "topic_root")
    }
    folded_parts = tuple(part.casefold() for part in parts)
    if folded_parts[0] == ".kgdistiller":
        raise VaultError("source-excluded", f"managed Vault content is not source input: {relative}")
    for managed in managed_roots:
        left = tuple(part.casefold() if os.name == "nt" else part for part in parts)
        right = tuple(part.casefold() if os.name == "nt" else part for part in managed)
        if left[: len(right)] == right:
            raise VaultError("source-excluded", f"managed Vault content is not source input: {relative}")
    included = any(
        _glob_matches(relative, pattern) for pattern in vault.manifest["source_include"]
    )
    excluded = any(
        _glob_matches(relative, pattern) for pattern in vault.manifest["source_exclude"]
    )
    if not included:
        raise VaultError(
            "source-not-included", f"source does not match source_include: {relative}"
        )
    if excluded:
        raise VaultError("source-excluded", f"source matches source_exclude: {relative}")
    return relative


def locate_file(
    file: Path | str, *, home: Path | str | None = None
) -> dict[str, Any]:
    """Resolve one real, included source file to exactly one registered Vault."""

    file_path = _absolute_path(file, field="source file")
    metadata = _lstat(file_path)
    if metadata is None:
        raise VaultError("source-not-found", f"source file does not exist: {file_path}")
    if not stat.S_ISREG(metadata.st_mode) or _is_link_or_reparse(file_path, metadata):
        raise VaultError(
            "invalid-source", f"source must be an ordinary, non-reparse file: {file_path}"
        )
    registry = load_registry(home, validate_vaults=True)
    owners = [
        vault
        for vault in registry.vaults
        if _contains_path(vault.root, file_path, allow_equal=False)
    ]
    if not owners:
        raise VaultError(
            "source-outside-vaults", f"source is not inside a registered Vault: {file_path}"
        )
    if len(owners) != 1:
        raise VaultError(
            "source-ambiguous",
            f"source is owned by multiple registered Vaults: {file_path}",
            details={"vault_ids": sorted(vault.id for vault in owners)},
        )
    vault = owners[0]
    relative = _validate_source_path(vault, file_path)
    try:
        resolved = file_path.resolve(strict=True)
    except OSError as error:
        raise VaultError("invalid-source", f"cannot resolve source file: {file_path}") from error
    if not _same_path(file_path, resolved):
        raise VaultError(
            "invalid-source", f"source traverses a symlink or reparse point: {file_path}"
        )
    return _report(
        "locate",
        status="ok",
        generation=registry.generation,
        result={
            "kind": "located-source",
            "vault": vault.card(),
            "file": str(resolved),
            "relative_path": relative,
        },
    )


def _diagnostic(error: BaseException) -> dict[str, str]:
    if isinstance(error, VaultError):
        code = error.code
        message = error.message
    else:
        code = "unexpected-error"
        message = str(error) or error.__class__.__name__
    return {"code": code[:128], "message": message[:8192]}


def doctor_vaults(
    vault_id: str | None = None, *, home: Path | str | None = None
) -> dict[str, Any]:
    """Return a bounded health report for the registry and selected Vaults."""

    selection = _validate_vault_id(vault_id) if vault_id is not None else None
    generation: str | None = None
    registry_exists = False
    registry_diagnostics: list[dict[str, str]] = []
    vault_reports: list[dict[str, Any]] = []
    try:
        selected_home = kgdistiller_home(home)
        path = selected_home / "vaults.json"
        registry_exists = _lstat(path) is not None
        registry = _read_registry(selected_home, validate_vaults=False)
        generation = registry.generation
        registrations = list(registry.registrations)
        registry_status = "ok"
        if selection is not None:
            registrations = [item for item in registrations if item.id == selection]
            if not registrations:
                registry_status = "error"
                registry_diagnostics.append(
                    {
                        "code": "vault-not-registered",
                        "message": f"Vault is not registered: {selection}",
                    }
                )
    except (VaultError, OSError, UnicodeError, ValueError) as error:
        selected_home = (
            Path(home)
            if home is not None
            else Path(os.environ.get("KGDISTILLER_HOME", Path.home() / ".kgdistiller"))
        )
        path = Path(os.path.abspath(os.fspath(selected_home))) / "vaults.json"
        registry_exists = _lstat(path) is not None
        registry = None
        registrations = []
        registry_status = "error"
        registry_diagnostics.append(_diagnostic(error))
    for registration in registrations:
        diagnostics: list[dict[str, str]] = []
        label: str | None = None
        try:
            vault = load_vault(registration.path, expected_id=registration.id)
            label = vault.label
            status = "ok"
        except (VaultError, OSError, UnicodeError, ValueError) as error:
            status = "error"
            diagnostics.append(_diagnostic(error))
        vault_reports.append(
            {
                "id": registration.id,
                "path": str(registration.path),
                "label": label,
                "status": status,
                "diagnostics": diagnostics,
            }
        )
    healthy = sum(item["status"] == "ok" for item in vault_reports)
    failed = sum(item["status"] == "error" for item in vault_reports)
    overall = "ok" if registry_status == "ok" and failed == 0 else "failed"
    result = {
        "kind": "doctor",
        "selection": selection,
        "registry": {
            "path": str(path),
            "exists": registry_exists,
            "status": registry_status,
            "diagnostics": registry_diagnostics,
        },
        "vaults": vault_reports,
        "counts": {
            "checked": len(vault_reports),
            "healthy": healthy,
            "failed": failed,
        },
    }
    return _report(
        "doctor", status=overall, generation=generation, result=result
    )
