"""Install the bundled Obsidian plugin into a registered base's vault and enable hidden indexing."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any

from .home import KNOWLEDGE_DIRECTORY, Base

PLUGIN_ID = "kgdistiller"
PLUGIN_FILES = ("main.js", "manifest.json", "styles.css")
HIDDEN_INDEXING_KEY = "hiddenKnowledgeEnabled"


class ObsidianPluginError(RuntimeError):
    """Raised when the bundled plugin cannot be installed safely."""


def _bundled_plugin() -> tuple[dict[str, bytes], dict[str, Any]]:
    candidates = (
        Path(__file__).resolve().parents[2] / "integrations" / "obsidian",
        files("kgdistiller").joinpath("obsidian_plugin"),
    )
    for candidate in candidates:
        try:
            assets = {
                name: candidate.joinpath(name).read_bytes() for name in PLUGIN_FILES
            }
        except (FileNotFoundError, NotADirectoryError, OSError):
            continue
        if any(not content for content in assets.values()):
            raise ObsidianPluginError(
                "the bundled Obsidian plugin contains an empty file"
            )
        try:
            manifest = json.loads(assets["manifest.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ObsidianPluginError(
                "the bundled Obsidian plugin manifest is invalid"
            ) from error
        if not isinstance(manifest, dict) or manifest.get("id") != PLUGIN_ID:
            raise ObsidianPluginError(
                f"the bundled Obsidian plugin manifest must use id {PLUGIN_ID!r}"
            )
        if not isinstance(manifest.get("version"), str) or not manifest["version"]:
            raise ObsidianPluginError(
                "the bundled Obsidian plugin manifest has no version"
            )
        return assets, manifest
    raise ObsidianPluginError(
        "the installed kgdistiller package does not contain the Obsidian plugin bundle"
    )


def _validate_existing_target(target: Path) -> bytes | None:
    if target.is_symlink():
        raise ObsidianPluginError(f"plugin destination must not be a symlink: {target}")
    if not target.exists():
        return None
    if not target.is_dir():
        raise ObsidianPluginError(f"plugin destination is not a directory: {target}")
    if target.resolve(strict=True) != target:
        raise ObsidianPluginError(
            f"plugin destination must not redirect outside its vault path: {target}"
        )
    allowed = {*PLUGIN_FILES, "data.json"}
    children = list(target.iterdir())
    unexpected = sorted(path.name for path in children if path.name not in allowed)
    if unexpected:
        raise ObsidianPluginError(
            f"plugin destination contains unmanaged files: {unexpected}"
        )
    for path in children:
        if path.is_symlink() or not path.is_file():
            raise ObsidianPluginError(
                f"plugin destination contains an unsafe entry: {path.name}"
            )
    settings = target / "data.json"
    return settings.read_bytes() if settings.is_file() else None


def _load_enabled_plugins(path: Path) -> list[str]:
    if path.is_symlink():
        raise ObsidianPluginError(
            f"Obsidian community plugin configuration must not be a symlink: {path}"
        )
    if not path.exists():
        return []
    if not path.is_file():
        raise ObsidianPluginError(
            f"Obsidian community plugin configuration is not a file: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObsidianPluginError(
            f"Obsidian community plugin configuration is invalid: {path}"
        ) from error
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ObsidianPluginError(
            "Obsidian community plugin configuration must be a unique JSON string list"
        )
    return value


def _atomic_write_json(path: Path, value: Any) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _configure_enabled(obsidian_root: Path) -> bool:
    path = obsidian_root / "community-plugins.json"
    enabled = _load_enabled_plugins(path)
    if PLUGIN_ID in enabled:
        return False
    enabled.append(PLUGIN_ID)
    _atomic_write_json(path, enabled)
    return True


def _same_bundle(target: Path, assets: dict[str, bytes]) -> bool:
    return all(
        (target / name).is_file() and (target / name).read_bytes() == content
        for name, content in assets.items()
    )


def _load_settings(target: Path, content: bytes | None) -> dict[str, Any] | None:
    if content is None:
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObsidianPluginError(
            f"plugin settings data.json must be a JSON object: {target / 'data.json'}"
        ) from error
    if not isinstance(value, dict):
        raise ObsidianPluginError(
            f"plugin settings data.json must be a JSON object: {target / 'data.json'}"
        )
    return value


def _link_format_warnings(obsidian_root: Path) -> list[str]:
    path = obsidian_root / "app.json"
    if path.is_symlink() or not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        return ["cannot read .obsidian/app.json to check newLinkFormat"]
    if value.get("newLinkFormat") == "relative":
        return [
            (
                ".obsidian/app.json sets newLinkFormat to relative, which writes '../' "
                "paths that the record link grammar rejects; set it to shortest or absolute"
            )
        ]
    return []


def _restore_settings(target: Path, content: bytes | None) -> None:
    settings = target / "data.json"
    if content is None:
        settings.unlink(missing_ok=True)
    else:
        settings.write_bytes(content)


def install_obsidian_plugin(base: Base) -> dict[str, Any]:
    """Install or update the packaged plugin bundle, enable it and turn on its hidden indexing."""

    vault_root = base.root
    if not vault_root.is_dir():
        raise ObsidianPluginError(f"vault is not a directory: {vault_root}")
    knowledge = vault_root / KNOWLEDGE_DIRECTORY
    if knowledge.is_symlink():
        raise ObsidianPluginError(f"knowledge tree must not be a symlink: {knowledge}")
    obsidian_root = vault_root / ".obsidian"
    if (
        obsidian_root.is_symlink()
        or not obsidian_root.is_dir()
        or obsidian_root.resolve(strict=True) != obsidian_root
    ):
        raise ObsidianPluginError(
            f"vault has no ordinary .obsidian directory: {obsidian_root}"
        )

    plugins_root = obsidian_root / "plugins"
    if plugins_root.is_symlink():
        raise ObsidianPluginError(
            f"Obsidian plugins directory is a symlink: {plugins_root}"
        )
    if plugins_root.exists() and not plugins_root.is_dir():
        raise ObsidianPluginError(
            f"Obsidian plugins destination is not a directory: {plugins_root}"
        )
    if plugins_root.exists() and plugins_root.resolve(strict=True) != plugins_root:
        raise ObsidianPluginError(
            f"Obsidian plugins directory must not redirect outside its vault path: {plugins_root}"
        )

    assets, manifest = _bundled_plugin()
    target = plugins_root / PLUGIN_ID
    settings = _validate_existing_target(target)
    existing = _load_settings(target, settings)
    merged = {**(existing or {}), HIDDEN_INDEXING_KEY: True}
    write_settings = existing is None or existing.get(HIDDEN_INDEXING_KEY) is not True
    warnings = _link_format_warnings(obsidian_root)
    plugins_root.mkdir(parents=False, exist_ok=True)

    current = target.is_dir() and _same_bundle(target, assets)
    status = "current" if current else ("updated" if target.exists() else "installed")
    stage: Path | None = None
    backup: Path | None = None
    installed_new_bundle = False
    settings_written = False
    try:
        if not current:
            stage = Path(
                tempfile.mkdtemp(prefix=f".{PLUGIN_ID}.stage-", dir=plugins_root)
            )
            for name, content in assets.items():
                (stage / name).write_bytes(content)
            if settings is not None:
                (stage / "data.json").write_bytes(settings)
            if target.exists():
                backup = plugins_root / f".{PLUGIN_ID}.backup-{uuid.uuid4().hex}"
                os.replace(target, backup)
            os.replace(stage, target)
            stage = None
            installed_new_bundle = True

        if write_settings:
            settings_written = True
            _atomic_write_json(target / "data.json", merged)

        enabled_changed = _configure_enabled(obsidian_root)
    except BaseException as error:
        if installed_new_bundle and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif settings_written:
            _restore_settings(target, settings)
        if backup is not None and backup.exists():
            os.replace(backup, target)
            backup = None
        if isinstance(error, ObsidianPluginError):
            raise
        if not isinstance(error, Exception):
            raise
        raise ObsidianPluginError(f"could not install Obsidian plugin: {error}") from error
    finally:
        if stage is not None and stage.is_dir() and not stage.is_symlink():
            shutil.rmtree(stage)
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)

    hidden_indexing = "enabled" if write_settings else "current"
    community_plugins = "updated" if enabled_changed else "current"
    return {
        "status": status,
        "plugin_id": PLUGIN_ID,
        "plugin_version": manifest["version"],
        "base": base.name,
        "vault": str(vault_root),
        "plugin_root": str(target),
        "files": [
            {
                "path": name,
                "bytes": len(assets[name]),
            }
            for name in PLUGIN_FILES
        ],
        "hidden_indexing": hidden_indexing,
        "community_plugins": community_plugins,
        "warnings": warnings,
        "reload_required": (
            status != "current"
            or hidden_indexing == "enabled"
            or community_plugins == "updated"
        ),
    }
