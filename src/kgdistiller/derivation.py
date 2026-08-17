"""Install converted Markdown under a vault's managed knowledge tree."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .vault_registry import VAULT_MANIFEST, VaultRegistryError, load_vault_manifest


DERIVED_SCHEMA = "kgdistiller-derived-markdown-v1"
DERIVED_ROOT = Path("knowledge/derived")
DERIVED_SOURCE_ROOT = DERIVED_ROOT / "by-source"
_FORMATS = {".typ": "typst", ".tex": "latex", ".pdf": "pdf"}


class DerivationError(ValueError):
    """Raised when a derived Markdown destination is ambiguous or unsafe."""


def find_enclosing_vault(source: Path) -> Path | None:
    candidate = source.resolve(strict=False)
    current = candidate if candidate.is_dir() else candidate.parent
    for root in (current, *current.parents):
        manifest = root / VAULT_MANIFEST
        if not manifest.exists() and not manifest.is_symlink():
            continue
        try:
            load_vault_manifest(root)
        except VaultRegistryError as error:
            raise DerivationError(str(error)) from error
        return root
    return None


def _safe_output(vault: Path, relative: Path) -> tuple[str, Path]:
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.casefold() != ".md":
        raise DerivationError("derived output must be a relative .md path under knowledge/derived")
    if relative.parts[:2] != DERIVED_ROOT.parts:
        raise DerivationError("derived output must be under knowledge/derived")
    root = vault.resolve()
    target = (root / relative).resolve(strict=False)
    try:
        canonical = target.relative_to(root).as_posix()
    except ValueError as error:
        raise DerivationError("derived output escapes the vault") from error
    return canonical, target


def plan_derivation(
    source: Path,
    *,
    target_vault: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    source = source.expanduser().resolve(strict=False)
    if source.is_symlink() or not source.is_file():
        raise DerivationError(f"source is not an ordinary file: {source}")
    source_format = _FORMATS.get(source.suffix.casefold())
    if source_format is None:
        raise DerivationError("derivation source must be .typ, .tex, or .pdf")
    enclosing = find_enclosing_vault(source)
    external = enclosing is None
    if external and target_vault is None:
        raise DerivationError(
            "source is not inside a kgdistiller vault; specify --repo-root or --vault "
            "to choose where knowledge/derived Markdown should be stored"
        )
    vault = (target_vault or enclosing).expanduser().resolve(strict=False)  # type: ignore[union-attr]
    try:
        load_vault_manifest(vault)
    except VaultRegistryError as error:
        raise DerivationError(str(error)) from error
    if enclosing is not None and target_vault is not None and vault != enclosing.resolve():
        raise DerivationError(
            f"source belongs to vault {enclosing}; it cannot be redirected to {vault}"
        )
    if output is None:
        if external:
            relative = DERIVED_ROOT / "imports" / f"{source.stem}.md"
        else:
            source_relative = source.relative_to(vault)
            relative = DERIVED_SOURCE_ROOT / Path(f"{source_relative.as_posix()}.md")
    else:
        relative = output
    required_root = DERIVED_ROOT / ("imports" if external else "by-source")
    if required_root not in relative.parents:
        raise DerivationError(
            f"{'external' if external else 'in-vault'} derivation output must be under "
            f"{required_root.as_posix()}"
        )
    relative_text, destination = _safe_output(vault, relative)
    result: dict[str, Any] = {
        "schema": DERIVED_SCHEMA,
        "vault": str(vault),
        "source": str(source),
        "source_format": source_format,
        "external_source": external,
        "output": relative_text,
        "destination": str(destination),
    }
    if not external:
        result["source_relative"] = source.relative_to(vault).as_posix()
    return result


def _source_sha256(path: Path, source_format: str) -> str:
    if source_format == "pdf":
        return hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("r", encoding="utf-8", newline=None) as handle:
        return hashlib.sha256(handle.read().encode("utf-8")).hexdigest()


def _render(plan: dict[str, Any], markdown: str) -> str:
    lines = ["---", f"kgd_schema: {json.dumps(DERIVED_SCHEMA)}"]
    if not plan["external_source"]:
        lines.extend(
            [
                f"kgd_source: {json.dumps(plan['source_relative'], ensure_ascii=False)}",
                f"kgd_source_format: {json.dumps(plan['source_format'])}",
                f"kgd_source_sha256: {json.dumps(_source_sha256(Path(plan['source']), plan['source_format']))}",
            ]
        )
    lines.extend(["---", "", markdown.strip(), ""])
    return "\n".join(lines)


def install_derivation(
    source: Path,
    markdown_input: Path,
    *,
    target_vault: Path | None = None,
    output: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    plan = plan_derivation(source, target_vault=target_vault, output=output)
    markdown_input = markdown_input.expanduser().resolve(strict=False)
    if markdown_input.is_symlink() or not markdown_input.is_file():
        raise DerivationError(f"converted Markdown input is not an ordinary file: {markdown_input}")
    markdown = markdown_input.read_text(encoding="utf-8")
    destination = Path(plan["destination"])
    if destination.exists() or destination.is_symlink():
        if not replace:
            raise DerivationError(f"derived Markdown already exists; pass --replace: {destination}")
        if destination.is_symlink() or not destination.is_file():
            raise DerivationError(f"derived destination is not an ordinary file: {destination}")
    content = _render(plan, markdown)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(content)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    plan["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return plan
