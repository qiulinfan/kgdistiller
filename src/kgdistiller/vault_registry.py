"""Cross-platform machine-local registry for kgdistiller vault locations.

The registry is a locator, never a knowledge authority.  Stable vault identity
lives in ``knowledge/vault.json`` inside each vault; the user-level registry
only maps that identity and a convenient local name to an absolute path on the
current machine.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


REGISTRY_SCHEMA = "kgdistiller-vault-registry-v1"
VAULT_SCHEMA = "kgdistiller-vault-v1"
REGISTRY_FILENAME = "vaults.json"
VAULT_MANIFEST = Path("knowledge/vault.json")
HOME_ENVIRONMENT = "KGDISTILLER_HOME"
VAULT_ENVIRONMENT = "KGDISTILLER_VAULT"
MAX_VAULT_NAME_LENGTH = 128


class VaultRegistryError(ValueError):
    """Raised when machine-local vault discovery is ambiguous or unsafe."""


def kgdistiller_home(explicit: Path | None = None) -> Path:
    """Return the absolute user-level kgdistiller directory on every platform."""

    if explicit is not None:
        candidate = Path(explicit).expanduser()
    else:
        configured = os.environ.get(HOME_ENVIRONMENT)
        candidate = Path(configured).expanduser() if configured else Path.home() / ".kgdistiller"
    if not candidate.is_absolute():
        raise VaultRegistryError(
            f"{HOME_ENVIRONMENT} must be an absolute path: {candidate}"
        )
    return candidate.resolve(strict=False)


def registry_path(home: Path | None = None) -> Path:
    return kgdistiller_home(home) / REGISTRY_FILENAME


def vault_manifest_path(root: Path) -> Path:
    return Path(root).resolve(strict=False) / VAULT_MANIFEST


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise VaultRegistryError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise VaultRegistryError(f"{field} must be a UUID string") from error
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise VaultRegistryError(f"{field} must be an RFC 4122 UUIDv4 string")
    canonical = str(parsed)
    if value != canonical:
        raise VaultRegistryError(f"{field} must use canonical lowercase UUID form")
    return canonical


def _vault_name(value: Any) -> str:
    if not isinstance(value, str):
        raise VaultRegistryError("vault name must be a string")
    name = value.strip()
    if (
        not name
        or len(name) > MAX_VAULT_NAME_LENGTH
        or not name.isprintable()
        or "/" in name
        or "\\" in name
    ):
        raise VaultRegistryError(
            f"vault name must contain 1 to {MAX_VAULT_NAME_LENGTH} printable characters "
            "without path separators"
        )
    return name


def _path_key(path: Path) -> str:
    """Return the host platform's canonical comparison key for one vault path."""

    return os.path.normcase(os.path.normpath(os.fspath(path.resolve(strict=False))))


def _empty_registry() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "default_vault_id": None,
        "vaults": [],
    }


def validate_vault_manifest(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict) or set(payload) != {"schema", "vault_id"}:
        raise VaultRegistryError("vault manifest has unsupported fields")
    if payload.get("schema") != VAULT_SCHEMA:
        raise VaultRegistryError(f"expected {VAULT_SCHEMA} vault manifest")
    return {
        "schema": VAULT_SCHEMA,
        "vault_id": _canonical_uuid(payload.get("vault_id"), "vault_id"),
    }


def validate_registry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "default_vault_id",
        "vaults",
    }:
        raise VaultRegistryError("vault registry has unsupported fields")
    if payload.get("schema") != REGISTRY_SCHEMA:
        raise VaultRegistryError(f"expected {REGISTRY_SCHEMA} vault registry")
    raw_vaults = payload.get("vaults")
    if not isinstance(raw_vaults, list):
        raise VaultRegistryError("vault registry vaults must be an array")
    vaults: list[dict[str, str]] = []
    ids: set[str] = set()
    names: set[str] = set()
    paths: set[str] = set()
    for raw in raw_vaults:
        if not isinstance(raw, dict) or set(raw) != {"id", "name", "path"}:
            raise VaultRegistryError("vault registry record has unsupported fields")
        vault_id = _canonical_uuid(raw.get("id"), "vault record id")
        name = _vault_name(raw.get("name"))
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise VaultRegistryError("vault registry path must be a non-empty string")
        path = Path(raw_path)
        if not path.is_absolute():
            raise VaultRegistryError(f"registered vault path must be absolute: {raw_path}")
        normalized_path = str(path)
        name_key = name.casefold()
        path_key = _path_key(path)
        if vault_id in ids:
            raise VaultRegistryError(f"duplicate registered vault id: {vault_id}")
        if name_key in names:
            raise VaultRegistryError(f"duplicate registered vault name: {name}")
        if path_key in paths:
            raise VaultRegistryError(f"duplicate registered vault path: {raw_path}")
        ids.add(vault_id)
        names.add(name_key)
        paths.add(path_key)
        vaults.append({"id": vault_id, "name": name, "path": normalized_path})
    default = payload.get("default_vault_id")
    if default is not None:
        default = _canonical_uuid(default, "default_vault_id")
        if default not in ids:
            raise VaultRegistryError("default_vault_id is not registered")
    vaults.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return {
        "schema": REGISTRY_SCHEMA,
        "default_vault_id": default,
        "vaults": vaults,
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VaultRegistryError(f"invalid JSON file: {path}") from error


def load_registry(home: Path | None = None) -> dict[str, Any]:
    path = registry_path(home)
    if not path.exists():
        if path.is_symlink():
            raise VaultRegistryError(f"vault registry is a broken symlink: {path}")
        return _empty_registry()
    if path.is_symlink() or not path.is_file():
        raise VaultRegistryError(f"vault registry is not an ordinary file: {path}")
    return validate_registry(_read_json(path))


def load_vault_manifest(root: Path) -> dict[str, str]:
    path = vault_manifest_path(root)
    if path.is_symlink() or not path.is_file():
        raise VaultRegistryError(f"vault manifest is not an ordinary file: {path}")
    return validate_vault_manifest(_read_json(path))


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise VaultRegistryError(f"kgdistiller home is not a directory: {path}")
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict[str, Any], *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            if private and os.name != "nt" and hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def ensure_vault_manifest(root: Path) -> dict[str, str]:
    root = Path(root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise VaultRegistryError(f"vault root is not a directory: {root}")
    path = vault_manifest_path(root)
    if path.exists() or path.is_symlink():
        return load_vault_manifest(root)
    manifest = {"schema": VAULT_SCHEMA, "vault_id": str(uuid.uuid4())}
    _atomic_write_json(path, manifest, private=False)
    return manifest


def _acquire_lock(handle: Any) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as error:
        raise VaultRegistryError("cannot acquire the vault registry lock") from error


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


@contextmanager
def _registry_lock(home: Path | None = None) -> Iterator[Path]:
    root = kgdistiller_home(home)
    _ensure_private_directory(root)
    lock_path = root / "registry.lock"
    if lock_path.exists() or lock_path.is_symlink():
        if lock_path.is_symlink() or not lock_path.is_file():
            raise VaultRegistryError(
                f"vault registry lock is not an ordinary file: {lock_path}"
            )
    handle = lock_path.open("a+b")
    try:
        if os.name != "nt":
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        _acquire_lock(handle)
        yield root
    finally:
        _release_lock(handle)
        handle.close()


def _write_registry(home: Path, payload: dict[str, Any]) -> dict[str, Any]:
    validated = validate_registry(payload)
    _atomic_write_json(home / REGISTRY_FILENAME, validated, private=True)
    return validated


def _record_by_selector(registry: dict[str, Any], selector: str) -> dict[str, str]:
    raw = selector.strip()
    if not raw:
        raise VaultRegistryError("vault selector must not be empty")
    matches = [
        record
        for record in registry["vaults"]
        if record["id"] == raw or record["name"].casefold() == raw.casefold()
    ]
    if not matches:
        raise VaultRegistryError(f"unknown registered vault: {selector}")
    return dict(matches[0])


def register_vault(
    root: Path,
    *,
    name: str | None = None,
    home: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    resolved = Path(root).expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise VaultRegistryError(f"vault root is not a directory: {resolved}")
    manifest = ensure_vault_manifest(resolved)
    vault_id = manifest["vault_id"]
    vault_name = _vault_name(name if name is not None else resolved.name)
    with _registry_lock(home) as registry_home:
        registry = load_registry(registry_home)
        by_id = {record["id"]: record for record in registry["vaults"]}
        by_name = {record["name"].casefold(): record for record in registry["vaults"]}
        by_path = {_path_key(Path(record["path"])): record for record in registry["vaults"]}
        id_record = by_id.get(vault_id)
        name_record = by_name.get(vault_name.casefold())
        path_record = by_path.get(_path_key(resolved))
        for record, label in ((name_record, "name"), (path_record, "path")):
            if record is not None and record["id"] != vault_id:
                raise VaultRegistryError(
                    f"registered vault {label} belongs to another vault: {record['name']}"
                )
        if id_record is not None and _path_key(Path(id_record["path"])) != _path_key(resolved):
            old_path_exists = Path(id_record["path"]).exists()
            if old_path_exists and not replace:
                raise VaultRegistryError(
                    "vault identity is already registered at another existing path; "
                    "pass --replace to relocate it"
                )
        previous = dict(id_record) if id_record is not None else None
        registry["vaults"] = [
            record for record in registry["vaults"] if record["id"] != vault_id
        ]
        record = {"id": vault_id, "name": vault_name, "path": str(resolved)}
        registry["vaults"].append(record)
        if registry["default_vault_id"] is None:
            registry["default_vault_id"] = vault_id
        _write_registry(registry_home, registry)
    status = "registered" if previous is None else (
        "unchanged" if previous == record else "updated"
    )
    return {
        "schema": "kgdistiller-vault-registration-v1",
        "status": status,
        "vault": record,
        "default": registry["default_vault_id"] == vault_id,
        "registry": str(registry_path(home)),
    }


def list_vaults(home: Path | None = None) -> dict[str, Any]:
    registry = load_registry(home)
    return {
        "schema": "kgdistiller-vault-list-v1",
        "default_vault_id": registry["default_vault_id"],
        "vaults": [dict(record) for record in registry["vaults"]],
        "registry": str(registry_path(home)),
    }


def show_vault(selector: str, home: Path | None = None) -> dict[str, Any]:
    registry = load_registry(home)
    record = _record_by_selector(registry, selector)
    return {
        "schema": "kgdistiller-vault-record-v1",
        "vault": record,
        "default": registry["default_vault_id"] == record["id"],
        "registry": str(registry_path(home)),
    }


def set_default_vault(selector: str | None, home: Path | None = None) -> dict[str, Any]:
    with _registry_lock(home) as registry_home:
        registry = load_registry(registry_home)
        record = None if selector is None else _record_by_selector(registry, selector)
        registry["default_vault_id"] = None if record is None else record["id"]
        _write_registry(registry_home, registry)
    return {
        "schema": "kgdistiller-vault-default-v1",
        "default_vault_id": registry["default_vault_id"],
        "vault": record,
        "registry": str(registry_path(home)),
    }


def unregister_vault(selector: str, home: Path | None = None) -> dict[str, Any]:
    with _registry_lock(home) as registry_home:
        registry = load_registry(registry_home)
        record = _record_by_selector(registry, selector)
        registry["vaults"] = [
            item for item in registry["vaults"] if item["id"] != record["id"]
        ]
        if registry["default_vault_id"] == record["id"]:
            registry["default_vault_id"] = None
        _write_registry(registry_home, registry)
    return {
        "schema": "kgdistiller-vault-registration-v1",
        "status": "unregistered",
        "vault": record,
        "manifest_preserved": str(vault_manifest_path(Path(record["path"]))),
        "registry": str(registry_path(home)),
    }


def _validated_record_root(record: dict[str, str]) -> Path:
    root = Path(record["path"])
    if not root.is_dir():
        raise VaultRegistryError(
            f"registered vault path is unavailable: {record['name']} ({root})"
        )
    manifest = load_vault_manifest(root)
    if manifest["vault_id"] != record["id"]:
        raise VaultRegistryError(
            f"registered vault identity does not match its manifest: {record['name']}"
        )
    return root.resolve(strict=True)


def resolve_registered_vault(selector: str, home: Path | None = None) -> Path:
    return _validated_record_root(_record_by_selector(load_registry(home), selector))


def _nearest_project_root(cwd: Path) -> Path | None:
    resolved = cwd.resolve(strict=False)
    for candidate in (resolved, *resolved.parents):
        manifest = candidate / VAULT_MANIFEST
        if manifest.exists() or manifest.is_symlink():
            load_vault_manifest(candidate)
            return candidate
        if (candidate / "knowledge/sources.json").is_file():
            return candidate
    return None


def _registered_root_containing(cwd: Path, registry: dict[str, Any]) -> Path | None:
    resolved = cwd.resolve(strict=False)
    matches: list[tuple[int, dict[str, str]]] = []
    for record in registry["vaults"]:
        root = Path(record["path"]).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        matches.append((len(root.parts), record))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1]["id"]))
    return _validated_record_root(matches[0][1])


def resolve_repo_root(
    *,
    explicit_repo_root: Path | None,
    explicit_vault: str | None,
    cwd: Path | None = None,
    home: Path | None = None,
    use_default: bool = True,
) -> Path:
    """Resolve a command target without depending on the launching directory."""

    if explicit_repo_root is not None:
        if explicit_vault is not None:
            raise VaultRegistryError("--repo-root and --vault cannot be combined")
        return Path(explicit_repo_root).expanduser().resolve(strict=False)
    selector = explicit_vault or os.environ.get(VAULT_ENVIRONMENT)
    if selector:
        return resolve_registered_vault(selector, home)
    current = Path.cwd() if cwd is None else Path(cwd)
    local = _nearest_project_root(current)
    if local is not None:
        return local
    registry = load_registry(home)
    containing = _registered_root_containing(current, registry)
    if containing is not None:
        return containing
    default_id = registry["default_vault_id"]
    if use_default and default_id is not None:
        return resolve_registered_vault(default_id, home)
    # Preserve the unregistered bootstrap workflow: ``kgdistiller init`` from
    # a new directory still targets that directory. Other commands will fail
    # against their normal missing-project checks without creating state.
    return current.expanduser().resolve(strict=False)


def doctor_vaults(
    selector: str | None = None, home: Path | None = None
) -> dict[str, Any]:
    registry = load_registry(home)
    records = (
        [_record_by_selector(registry, selector)]
        if selector is not None
        else list(registry["vaults"])
    )
    results: list[dict[str, Any]] = []
    for record in records:
        try:
            root = _validated_record_root(record)
            result = {"vault": dict(record), "status": "healthy", "root": str(root)}
        except VaultRegistryError as error:
            result = {"vault": dict(record), "status": "error", "message": str(error)}
        results.append(result)
    errors = sum(result["status"] == "error" for result in results)
    return {
        "schema": "kgdistiller-vault-doctor-v1",
        "status": "healthy" if errors == 0 else "error",
        "checked": len(results),
        "errors": errors,
        "results": results,
        "registry": str(registry_path(home)),
    }
