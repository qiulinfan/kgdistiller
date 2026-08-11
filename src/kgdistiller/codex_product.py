"""Install and diagnose kgdistiller-owned Codex product assets."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_SCHEMA = "kgdistiller-workflows-v1"
STATE_SCHEMA = "kgdistiller-codex-links-v1"
STATE_NAME = ".kgdistiller-product-links.json"
RECOVERY_ROOT_NAME = ".kgdistiller-product-recovery"
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
AGENT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FORBIDDEN_TEXT_RE = re.compile(
    r"(?:qlblog|vendor/kgdistiller|knowledge/kgd\.py|[A-Za-z]:\\|/Users/|/home/)",
    re.IGNORECASE,
)


class CodexProductError(ValueError):
    """Raised when product assets or their installation fail closed."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise CodexProductError(f"non-finite JSON constant in {path}: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=reject_constant
        )
    except CodexProductError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CodexProductError(f"cannot read product JSON: {path}") from error
    if not isinstance(value, dict):
        raise CodexProductError(f"product JSON must be an object: {path}")
    return value


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise CodexProductError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value:
        raise CodexProductError(f"{label} is not a portable relative path: {value!r}")
    return path


def _join(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*relative.parts)


def product_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
        if not (root / "workflows" / "manifest.json").is_file():
            raise CodexProductError(f"product manifest is missing below {root}")
        return root
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "workflows" / "manifest.json").is_file():
        return checkout
    installed = Path(__file__).resolve().parent / "product"
    if (installed / "workflows" / "manifest.json").is_file():
        return installed
    raise CodexProductError(
        "kgdistiller product assets are missing from this installation"
    )


def _frontmatter_name(text: str, path: Path) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise CodexProductError(f"Skill has no YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise CodexProductError(f"Skill frontmatter is not closed: {path}") from error
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    if not fields.get("description"):
        raise CodexProductError(f"Skill has no frontmatter description: {path}")
    name = fields.get("name", "")
    if SKILL_NAME_RE.fullmatch(name) is None:
        raise CodexProductError(f"Skill has an invalid frontmatter name: {path}")
    return name


def _validate_skill(root: Path, declared_name: str) -> None:
    skill_file = root / "SKILL.md"
    agent_file = root / "agents" / "openai.yaml"
    try:
        text = skill_file.read_text(encoding="utf-8")
        agent_text = agent_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CodexProductError(f"Skill is incomplete: {root}") from error
    if _frontmatter_name(text, skill_file) != declared_name:
        raise CodexProductError(
            f"Skill name does not match its manifest entry: {declared_name}"
        )
    combined = f"{text}\n{agent_text}"
    if FORBIDDEN_TEXT_RE.search(combined):
        raise CodexProductError(
            f"Skill contains a host or machine-specific path: {declared_name}"
        )
    if "interface:" not in agent_text or "default_prompt:" not in agent_text:
        raise CodexProductError(
            f"Skill has invalid agents/openai.yaml: {declared_name}"
        )
    if f"${declared_name}" not in agent_text:
        raise CodexProductError(f"Skill default prompt does not name ${declared_name}")
    for raw_link in LINK_RE.findall(text):
        target = raw_link.split("#", 1)[0]
        if not target or "://" in target or target.startswith("#"):
            continue
        relative = _safe_relative(target, f"Skill link in {declared_name}")
        linked = _join(root, relative).resolve()
        try:
            linked.relative_to(root.resolve())
        except ValueError as error:
            raise CodexProductError(
                f"Skill link escapes its directory: {declared_name}"
            ) from error
        if not linked.is_file():
            raise CodexProductError(f"Skill link is missing: {declared_name}/{target}")


def _validate_agent(path: Path, declared_name: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CodexProductError(f"cannot read Codex agent preset: {path}") from error
    if FORBIDDEN_TEXT_RE.search(text):
        raise CodexProductError(f"agent preset is host-specific: {declared_name}")
    required = {
        "name": re.compile(r'^name\s*=\s*"[^"\r\n]+"\s*$', re.MULTILINE),
        "description": re.compile(r'^description\s*=\s*"[^"\r\n]+"\s*$', re.MULTILINE),
        "developer_instructions": re.compile(
            r'^developer_instructions\s*=\s*(?:"""[\s\S]+?"""|"[^"\r\n]+")\s*$',
            re.MULTILINE,
        ),
    }
    missing = [key for key, pattern in required.items() if pattern.search(text) is None]
    if missing:
        raise CodexProductError(
            f"agent preset {declared_name} is missing required fields: {', '.join(missing)}"
        )


def _validate_linkers(root: Path, value: Any) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise CodexProductError(
            "workflow manifest must declare POSIX and Windows linkers"
        )
    expected = {
        "posix": PurePosixPath("scripts", "link-codex-product.sh"),
        "windows": PurePosixPath("scripts", "link-codex-product.ps1"),
    }
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"platform", "path"}:
            raise CodexProductError("workflow manifest has an invalid linker record")
        platform = item.get("platform")
        if platform not in expected or platform in seen:
            raise CodexProductError(
                f"workflow manifest has an invalid linker platform: {platform!r}"
            )
        relative = _safe_relative(item.get("path"), f"{platform} linker path")
        if relative != expected[str(platform)]:
            raise CodexProductError(
                f"workflow manifest has an unexpected {platform} linker path"
            )
        path = _join(root, relative)
        try:
            content = path.read_bytes()
            text = content.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise CodexProductError(f"cannot read {platform} linker: {path}") from error
        if b"\x00" in content or FORBIDDEN_TEXT_RE.search(text):
            raise CodexProductError(f"{platform} linker is not portable")
        if "kgdistiller codex link" not in text:
            raise CodexProductError(f"{platform} linker does not call the product CLI")
        if platform == "posix" and (
            not content.startswith(b"#!/usr/bin/env sh\n") or b"\r" in content
        ):
            raise CodexProductError(
                "POSIX linker must use an LF-only portable sh shebang"
            )
        seen.add(str(platform))


def load_manifest(explicit_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    root = product_root(explicit_root)
    manifest = _load_json(root / "workflows" / "manifest.json")
    if set(manifest) != {
        "schema",
        "product",
        "version",
        "installation",
        "workflow_guide",
        "linkers",
        "skills",
        "agents",
        "workflows",
    }:
        raise CodexProductError("workflow manifest has unsupported top-level fields")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("product") != "kgdistiller"
    ):
        raise CodexProductError(
            "workflow manifest has an unsupported schema or product"
        )
    if not isinstance(manifest.get("version"), int) or manifest["version"] < 1:
        raise CodexProductError("workflow manifest version is invalid")
    installation = manifest.get("installation")
    if (
        not isinstance(installation, dict)
        or set(installation) != {"product_root", "state"}
        or installation.get("product_root") != "workflow-products/kgdistiller"
        or installation.get("state") != STATE_NAME
    ):
        raise CodexProductError("workflow manifest installation namespace is invalid")
    workflow_guide = _safe_relative(manifest.get("workflow_guide"), "workflow guide")
    if workflow_guide != PurePosixPath("docs", "product-workflows.md"):
        raise CodexProductError("workflow manifest guide path is invalid")
    try:
        guide_text = _join(root, workflow_guide).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CodexProductError("workflow guide is missing or unreadable") from error
    if FORBIDDEN_TEXT_RE.search(guide_text):
        raise CodexProductError(
            "workflow guide contains a host or machine-specific path"
        )
    _validate_linkers(root, manifest.get("linkers"))

    skills = manifest.get("skills")
    if not isinstance(skills, list) or not skills:
        raise CodexProductError("workflow manifest skills must be a non-empty array")
    skill_names: set[str] = set()
    for item in skills:
        if not isinstance(item, dict) or set(item) != {"name", "path"}:
            raise CodexProductError("workflow manifest has an invalid Skill record")
        name = item.get("name")
        if (
            not isinstance(name, str)
            or SKILL_NAME_RE.fullmatch(name) is None
            or name in skill_names
        ):
            raise CodexProductError(
                f"workflow manifest has an invalid Skill name: {name!r}"
            )
        relative = _safe_relative(item.get("path"), f"Skill path for {name}")
        if relative != PurePosixPath("skills", name):
            raise CodexProductError(f"Skill path must be skills/{name}")
        skill_names.add(name)
        _validate_skill(_join(root, relative), name)

    agents = manifest.get("agents")
    if not isinstance(agents, list) or not agents:
        raise CodexProductError("workflow manifest agents must be a non-empty array")
    agent_names: set[str] = set()
    install_names: set[str] = set()
    for item in agents:
        if not isinstance(item, dict) or set(item) != {"name", "path", "install_as"}:
            raise CodexProductError("workflow manifest has an invalid agent record")
        name = item.get("name")
        install_as = item.get("install_as")
        if (
            not isinstance(name, str)
            or AGENT_NAME_RE.fullmatch(name) is None
            or name in agent_names
        ):
            raise CodexProductError(
                f"workflow manifest has an invalid agent name: {name!r}"
            )
        if (
            not isinstance(install_as, str)
            or install_as != f"kgdistiller-{name}.toml"
            or install_as in install_names
        ):
            raise CodexProductError(
                f"agent install name is outside the kgdistiller namespace: {name}"
            )
        relative = _safe_relative(item.get("path"), f"agent path for {name}")
        if relative != PurePosixPath(".codex", "agents", f"{name}.toml"):
            raise CodexProductError(f"agent path must be .codex/agents/{name}.toml")
        agent_names.add(name)
        install_names.add(install_as)
        _validate_agent(_join(root, relative), name)

    workflows = manifest.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        raise CodexProductError("workflow manifest workflows must be a non-empty array")
    workflow_ids: set[str] = set()
    used_skills: set[str] = set()
    used_agents: set[str] = set()
    for workflow in workflows:
        if not isinstance(workflow, dict) or set(workflow) != {
            "id",
            "description",
            "steps",
        }:
            raise CodexProductError("workflow manifest has an invalid workflow record")
        workflow_id = workflow.get("id")
        if (
            not isinstance(workflow_id, str)
            or SKILL_NAME_RE.fullmatch(workflow_id) is None
            or workflow_id in workflow_ids
        ):
            raise CodexProductError(
                f"workflow manifest has an invalid workflow id: {workflow_id!r}"
            )
        if (
            not isinstance(workflow.get("description"), str)
            or not workflow["description"]
        ):
            raise CodexProductError(f"workflow {workflow_id} has no description")
        steps = workflow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise CodexProductError(f"workflow {workflow_id} has no steps")
        step_ids: set[str] = set()
        for step in steps:
            if not isinstance(step, dict) or set(step) != {
                "id",
                "skill",
                "agent",
                "mode",
            }:
                raise CodexProductError(f"workflow {workflow_id} has an invalid step")
            step_id = step.get("id")
            skill = step.get("skill")
            agent = step.get("agent")
            if (
                not isinstance(step_id, str)
                or SKILL_NAME_RE.fullmatch(step_id) is None
                or step_id in step_ids
            ):
                raise CodexProductError(
                    f"workflow {workflow_id} has an invalid step id"
                )
            if skill not in skill_names or agent not in agent_names:
                raise CodexProductError(
                    f"workflow {workflow_id} references an unknown product asset"
                )
            if step.get("mode") not in {"read-only", "author", "transaction", "export"}:
                raise CodexProductError(
                    f"workflow {workflow_id} has an invalid step mode"
                )
            step_ids.add(step_id)
            used_skills.add(str(skill))
            used_agents.add(str(agent))
        workflow_ids.add(workflow_id)
    if used_skills != skill_names:
        raise CodexProductError("every shipped Skill must occur in a product workflow")
    if used_agents != agent_names:
        raise CodexProductError("every shipped agent must occur in a product workflow")
    return root, manifest


def _asset_digest(path: Path) -> str:
    if path.is_symlink():
        path = path.resolve(strict=True)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise CodexProductError(f"managed asset is missing: {path}")
    digest = hashlib.sha256()
    for child in sorted(
        path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()
    ):
        if child.is_symlink():
            raise CodexProductError(f"product asset contains a symbolic link: {child}")
        if not child.is_file():
            continue
        relative = child.relative_to(path).as_posix().encode("utf-8")
        content = child.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _codex_home(explicit: Path | None) -> Path:
    if explicit is not None:
        home = Path(os.path.abspath(explicit.expanduser()))
    elif os.environ.get("CODEX_HOME"):
        home = Path(os.path.abspath(Path(os.environ["CODEX_HOME"]).expanduser()))
    else:
        home = Path(os.path.abspath(Path.home() / ".codex"))
    if home == Path(home.anchor):
        raise CodexProductError("Codex home cannot be a filesystem root")
    if home.exists() and not home.is_dir():
        raise CodexProductError(f"Codex home is not a directory: {home}")
    return home


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    for candidate, parent in ((left, right), (right, left)):
        try:
            candidate.relative_to(parent)
        except ValueError:
            continue
        return True
    return False


def _validate_codex_destination(home: Path) -> None:
    current = Path(home.anchor)
    for part in home.parts[1:]:
        current /= part
        if not _path_present(current):
            break
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise CodexProductError(
                f"cannot inspect Codex destination parent: {current}"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(current):
            raise CodexProductError(
                "Codex destination parents must be ordinary, non-reparse "
                f"directories: {current}"
            )
    for name in (
        "skills",
        "agents",
        "workflow-products",
        RECOVERY_ROOT_NAME,
    ):
        path = home / name
        if not _path_present(path):
            continue
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise CodexProductError(
                f"cannot inspect Codex product namespace parent: {path}"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(path):
            raise CodexProductError(
                "Codex product namespace parents must be ordinary, non-reparse "
                f"directories: {path}"
            )


def _product_inventory_files(root: Path, manifest: dict[str, Any]) -> list[Path]:
    files: set[Path] = {root / "workflows" / "manifest.json"}
    for item in manifest["skills"]:
        skill_root = _join(root, _safe_relative(item["path"], "Skill path"))
        files.update(path for path in skill_root.rglob("*") if path.is_file())
    for item in manifest["agents"]:
        files.add(_join(root, _safe_relative(item["path"], "agent path")))
    for item in manifest["linkers"]:
        files.add(_join(root, _safe_relative(item["path"], "linker path")))
    files.add(_join(root, _safe_relative(manifest["workflow_guide"], "workflow guide")))
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise CodexProductError(f"product inventory contains missing files: {missing}")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _digest_inventory(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for child in files:
        relative = child.relative_to(root).as_posix().encode("utf-8")
        content = child.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _product_digest(root: Path, manifest: dict[str, Any]) -> str:
    return _digest_inventory(root, _product_inventory_files(root, manifest))


def _managed_assets(
    root: Path, manifest: dict[str, Any], home: Path
) -> list[dict[str, Any]]:
    product_target = _safe_relative(
        manifest["installation"]["product_root"],
        "installed product root",
    )
    assets: list[dict[str, Any]] = [
        {
            "kind": "product-root",
            "name": "kgdistiller",
            "source": root,
            "target": _join(home, product_target),
            "target_relative": product_target.as_posix(),
        }
    ]
    for item in manifest["skills"]:
        source_relative = _safe_relative(item["path"], "Skill path")
        target_relative = PurePosixPath("skills", item["name"])
        assets.append(
            {
                "kind": "skill",
                "name": item["name"],
                "source": _join(root, source_relative),
                "target": _join(home, target_relative),
                "target_relative": target_relative.as_posix(),
            }
        )
    for item in manifest["agents"]:
        source_relative = _safe_relative(item["path"], "agent path")
        target_relative = PurePosixPath("agents", item["install_as"])
        assets.append(
            {
                "kind": "agent",
                "name": item["name"],
                "source": _join(root, source_relative),
                "target": _join(home, target_relative),
                "target_relative": target_relative.as_posix(),
            }
        )
    return assets


def _load_state(path: Path) -> dict[str, Any]:
    if not os.path.lexists(path):
        return {"schema": STATE_SCHEMA, "assets": [], "cleanup": []}
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise CodexProductError(f"cannot inspect managed-link state: {path}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(path)
        or metadata.st_nlink != 1
    ):
        raise CodexProductError(
            f"managed-link state must be an ordinary, non-reparse file: {path}"
        )
    state = _load_json(path)
    required = {"schema", "product", "manifest_version", "assets"}
    fields = set(state)
    if fields not in (required, required | {"cleanup"}):
        raise CodexProductError(f"managed-link state has unsupported fields: {path}")
    if state.get("schema") != STATE_SCHEMA or state.get("product") != "kgdistiller":
        raise CodexProductError(f"managed-link state has an unsupported schema: {path}")
    if not isinstance(state.get("assets"), list):
        raise CodexProductError(f"managed-link state assets must be an array: {path}")
    if "cleanup" not in state:
        state["cleanup"] = []
    if not isinstance(state.get("cleanup"), list):
        raise CodexProductError(f"managed-link state cleanup must be an array: {path}")
    version = state.get("manifest_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CodexProductError(
            f"managed-link state manifest version is invalid: {path}"
        )
    return state


def _path_present(path: Path) -> bool:
    return os.path.lexists(str(path))


def _is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return path.is_symlink()
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _is_junction(path: Path) -> bool:
    return os.name == "nt" and not path.is_symlink() and _is_reparse_point(path)


def _remove_exact(path: Path) -> None:
    if _is_junction(path):
        os.rmdir(path)
    elif path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _normalized_link_target(link: Path) -> Path:
    raw = os.readlink(link)
    if raw.startswith(("\\\\?\\", "\\??\\")):
        raw = raw[4:]
    target = Path(raw)
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.abspath(target))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _create_junction(source: Path, staging: Path) -> None:
    if os.name != "nt":
        raise CodexProductError("directory junctions are available only on Windows")
    for value in (str(source), str(staging)):
        if any(
            character in value
            for character in ('"', "&", "|", "<", ">", "^", "%", "\r", "\n")
        ):
            raise CodexProductError(
                "Windows junction path contains unsafe command characters"
            )
    command = f'cmd.exe /d /c mklink /J "{staging}" "{source}"'
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        creationflags=0x08000000,
    )
    if completed.returncode != 0 or not _is_junction(staging):
        _remove_exact(staging)
        raise CodexProductError("Windows directory junction creation failed")


def _copy_product_root(source: Path, staging: Path, manifest: dict[str, Any]) -> None:
    staging.mkdir()
    for path in _product_inventory_files(source, manifest):
        relative = path.relative_to(source)
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _source_digest(asset: dict[str, Any], root: Path, manifest: dict[str, Any]) -> str:
    if asset["kind"] == "product-root":
        return _product_digest(root, manifest)
    return _asset_digest(asset["source"])


def _installed_copy_digest(target: Path, kind: str) -> str:
    if kind == "product-root":
        installed_root, installed_manifest = load_manifest(target)
        return _product_digest(installed_root, installed_manifest)
    return _asset_digest(target)


def _stage_asset(
    asset: dict[str, Any],
    mode: str,
    root: Path,
    manifest: dict[str, Any],
) -> tuple[Path, str]:
    source = asset["source"]
    target = asset["target"]
    staging = target.parent / f".{target.name}.kgdistiller-{uuid.uuid4().hex}"
    if mode == "copy":
        if asset["kind"] == "product-root":
            _copy_product_root(root, staging, manifest)
        elif source.is_dir():
            shutil.copytree(source, staging)
        else:
            shutil.copy2(source, staging)
        return staging, "copy"
    if mode in {"symlink", "auto"}:
        try:
            os.symlink(source, staging, target_is_directory=source.is_dir())
            return staging, "symlink"
        except OSError as symlink_error:
            _remove_exact(staging)
            if mode == "symlink":
                raise CodexProductError(
                    "symbolic link creation failed"
                ) from symlink_error
    if mode == "auto" and source.is_dir() and os.name == "nt":
        _create_junction(source, staging)
        return staging, "junction"
    if mode == "auto" and source.is_file():
        try:
            os.link(source, staging)
        except OSError as hardlink_error:
            raise CodexProductError(
                "live agent link unavailable; use --mode copy for an explicit non-live install"
            ) from hardlink_error
        return staging, "hardlink"
    if mode == "auto":
        raise CodexProductError(
            "live product link unavailable; use --mode copy for an explicit non-live install"
        )
    raise CodexProductError(f"unsupported link mode: {mode}")


def _validate_state_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {
        "kind",
        "name",
        "source",
        "target",
        "mode",
        "digest",
    }:
        raise CodexProductError("managed-link state contains an invalid asset record")
    kind = record.get("kind")
    name = record.get("name")
    target = record.get("target")
    if kind == "skill":
        allowed = (
            isinstance(name, str)
            and SKILL_NAME_RE.fullmatch(name) is not None
            and target == f"skills/{name}"
        )
    elif kind == "agent":
        allowed = (
            isinstance(name, str)
            and AGENT_NAME_RE.fullmatch(name) is not None
            and target == f"agents/kgdistiller-{name}.toml"
        )
    elif kind == "product-root":
        allowed = name == "kgdistiller" and target == "workflow-products/kgdistiller"
    else:
        allowed = False
    if not allowed:
        raise CodexProductError(
            "managed-link state target escapes the kgdistiller namespace"
        )
    if (
        record.get("mode") not in {"copy", "symlink", "junction", "hardlink"}
        or not isinstance(record.get("source"), str)
        or not Path(record["source"]).is_absolute()
        or not isinstance(record.get("digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", record["digest"]) is None
    ):
        raise CodexProductError("managed-link state asset provenance is invalid")
    return record


def _bind_state_source(
    record: dict[str, Any],
    root: Path,
    asset: dict[str, Any] | None = None,
) -> None:
    if asset is not None:
        expected = asset["source"]
    elif record["kind"] == "skill":
        expected = root / "skills" / str(record["name"])
    elif record["kind"] == "agent":
        expected = root / ".codex" / "agents" / f"{record['name']}.toml"
    else:
        expected = root
    observed = Path(record["source"])
    if not _same_path(observed.resolve(), expected.resolve()):
        raise CodexProductError(
            "managed-link state source is outside the active product manifest namespace: "
            f"{observed}"
        )


def _verify_managed_owner(
    record: dict[str, Any],
    target: Path,
    *,
    allow_detached_hardlink_recovery: bool = False,
) -> None:
    if not _path_present(target):
        return
    mode = record["mode"]
    source = Path(record["source"])
    if mode == "copy":
        if _installed_copy_digest(target, str(record["kind"])) != record["digest"]:
            raise CodexProductError(
                f"refusing to replace a modified managed copy: {target}"
            )
        return
    if mode == "symlink":
        if not target.is_symlink() or not _same_path(
            _normalized_link_target(target), source
        ):
            raise CodexProductError(
                f"managed symbolic link has the wrong owner: {target}"
            )
        return
    if mode == "junction":
        if not _is_junction(target) or not _same_path(
            _normalized_link_target(target), source
        ):
            raise CodexProductError(f"managed junction has the wrong owner: {target}")
        return
    if mode == "hardlink":
        same = False
        if source.is_file():
            try:
                same = target.is_file() and os.path.samefile(source, target)
            except OSError:
                same = False
        if same:
            return
        if (
            allow_detached_hardlink_recovery
            and target.is_file()
            and not target.is_symlink()
            and not _is_reparse_point(target)
            and _asset_digest(target) == record["digest"]
        ):
            return
        raise CodexProductError(f"detached managed hardlink fails closed: {target}")
    raise CodexProductError(f"managed asset has an unsupported mode: {target}")


def _write_state(path: Path, state: dict[str, Any]) -> None:
    staging = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    staging.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(staging, path)


def _cleanup_record(record: dict[str, Any], transaction: str) -> dict[str, Any]:
    target = _safe_relative(record["target"], "managed cleanup target")
    backup = PurePosixPath(RECOVERY_ROOT_NAME, transaction, *target.parts)
    return {"asset": copy.deepcopy(record), "backup": backup.as_posix()}


def _validate_cleanup_record(
    value: Any,
    home: Path,
    root: Path,
) -> tuple[dict[str, Any], Path, Path]:
    if not isinstance(value, dict) or set(value) != {"asset", "backup"}:
        raise CodexProductError("managed-link state has an invalid cleanup record")
    asset = _validate_state_record(value["asset"])
    _bind_state_source(asset, root)
    backup_relative = _safe_relative(value["backup"], "managed cleanup backup")
    if len(backup_relative.parts) < 3:
        raise CodexProductError("managed cleanup backup escapes its recovery namespace")
    recovery_name, transaction, *remainder = backup_relative.parts
    expected = PurePosixPath(*_safe_relative(asset["target"], "cleanup target").parts)
    if (
        recovery_name != RECOVERY_ROOT_NAME
        or re.fullmatch(r"[0-9a-f]{32}", transaction) is None
        or PurePosixPath(*remainder) != expected
    ):
        raise CodexProductError("managed cleanup backup escapes its recovery namespace")
    backup = _join(home, backup_relative)
    transaction_root = home / RECOVERY_ROOT_NAME / transaction
    current = home
    for part in backup_relative.parts[:-1]:
        current /= part
        if _path_present(current):
            try:
                metadata = os.lstat(current)
            except OSError as error:
                raise CodexProductError(
                    f"cannot inspect managed cleanup directory: {current}"
                ) from error
            if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(current):
                raise CodexProductError(
                    "managed cleanup directories must be ordinary, non-reparse "
                    f"directories: {current}"
                )
        else:
            break
    return asset, backup, transaction_root


def _prune_empty_recovery(backup: Path, transaction_root: Path, home: Path) -> None:
    recovery_root = home / RECOVERY_ROOT_NAME
    current = backup.parent
    while _same_path(current, transaction_root) or transaction_root in current.parents:
        if not current.is_dir() or _is_reparse_point(current) or any(current.iterdir()):
            break
        parent = current.parent
        current.rmdir()
        if _same_path(current, transaction_root):
            break
        current = parent
    if recovery_root.is_dir() and not any(recovery_root.iterdir()):
        recovery_root.rmdir()


def _complete_committed_cleanup(
    home: Path,
    root: Path,
    state_path: Path,
    state: dict[str, Any],
    *,
    fail_closed: bool,
) -> tuple[dict[str, Any], list[str], list[str]]:
    cleanup = state.get("cleanup") or []
    validated = [
        (*_validate_cleanup_record(value, home, root), value) for value in cleanup
    ]
    warnings: list[str] = []
    recovery_paths: list[str] = []
    for asset, backup, transaction_root, _value in validated:
        if not _path_present(backup):
            continue
        try:
            _verify_managed_owner(
                asset,
                backup,
                allow_detached_hardlink_recovery=True,
            )
            _remove_exact(backup)
            _prune_empty_recovery(backup, transaction_root, home)
        except (CodexProductError, OSError) as error:
            if fail_closed:
                raise CodexProductError(
                    f"committed managed-link cleanup remains pending: {backup}"
                ) from error
            warnings.append(
                "product links were committed, but an owned backup still needs cleanup"
            )
            recovery_paths.append(str(backup))
    if recovery_paths:
        return state, warnings, recovery_paths

    cleared = copy.deepcopy(state)
    cleared["cleanup"] = []
    try:
        _write_state(state_path, cleared)
    except (OSError, UnicodeError) as error:
        if fail_closed:
            raise CodexProductError(
                "committed managed-link cleanup state could not be finalized"
            ) from error
        warnings.append(
            "product links were committed and backups were removed, but cleanup state "
            "will be finalized by the next link"
        )
        return state, warnings, []
    return cleared, warnings, []


def link_product(
    *,
    codex_home: Path | None = None,
    mode: str = "auto",
    source_root: Path | None = None,
) -> dict[str, Any]:
    if mode not in {"auto", "symlink", "copy"}:
        raise CodexProductError(f"unsupported link mode: {mode}")
    root, manifest = load_manifest(source_root)
    home = _codex_home(codex_home)
    if _paths_overlap(home, root):
        raise CodexProductError(
            "Codex home and the kgdistiller product root cannot overlap"
        )
    _validate_codex_destination(home)
    state_path = home / STATE_NAME
    previous = _load_state(state_path)
    home.mkdir(parents=True, exist_ok=True)
    if previous.get("cleanup"):
        previous, _warnings, _recovery_paths = _complete_committed_cleanup(
            home,
            root,
            state_path,
            previous,
            fail_closed=True,
        )
    (home / "skills").mkdir(exist_ok=True)
    (home / "agents").mkdir(exist_ok=True)
    (home / "workflow-products").mkdir(exist_ok=True)
    previous_by_target: dict[str, dict[str, Any]] = {}
    for item in previous.get("assets", []):
        record = _validate_state_record(item)
        if record["target"] in previous_by_target:
            raise CodexProductError("managed-link state contains a duplicate target")
        previous_by_target[record["target"]] = record

    assets = _managed_assets(root, manifest, home)
    assets_by_target = {item["target_relative"]: item for item in assets}
    expected_targets = {item["target_relative"] for item in assets}
    stale = sorted(set(previous_by_target) - expected_targets)
    for asset in assets:
        target = asset["target"]
        target_relative = asset["target_relative"]
        asset["digest"] = _source_digest(asset, root, manifest)
        prior = previous_by_target.get(target_relative)
        if prior is not None:
            if prior.get("kind") != asset["kind"] or prior.get("name") != asset["name"]:
                raise CodexProductError(
                    f"managed-link state identity does not match {target}"
                )
            _bind_state_source(prior, root, asset)
        if _path_present(target):
            if prior is None:
                raise CodexProductError(
                    f"refusing to overwrite unmanaged Codex asset: {target}"
                )
            _verify_managed_owner(
                prior,
                target,
                allow_detached_hardlink_recovery=True,
            )
    for target_relative in stale:
        record = previous_by_target[target_relative]
        _bind_state_source(record, root)
        _verify_managed_owner(
            record,
            _join(home, _safe_relative(target_relative, "stale target")),
            allow_detached_hardlink_recovery=True,
        )

    staged: list[tuple[dict[str, Any], Path, str]] = []
    installed: list[dict[str, Any]] = []
    backups: dict[Path, Path] = {}
    cleanup_records: list[dict[str, Any]] = []
    installed_targets: list[Path] = []
    state_written = False
    postcommit_warnings: list[str] = []
    state: dict[str, Any] | None = None
    transaction = uuid.uuid4().hex
    transaction_root = home / RECOVERY_ROOT_NAME / transaction
    try:
        for asset in assets:
            staging, selected_mode = _stage_asset(asset, mode, root, manifest)
            staged.append((asset, staging, selected_mode))
            installed.append(
                {
                    "kind": asset["kind"],
                    "name": asset["name"],
                    "source": str(asset["source"].resolve()),
                    "target": asset["target_relative"],
                    "mode": selected_mode,
                    "digest": asset["digest"],
                }
            )

        touched: dict[Path, dict[str, Any]] = {}
        for asset in assets:
            if _path_present(asset["target"]):
                touched[asset["target"]] = previous_by_target[asset["target_relative"]]
        for target_relative in stale:
            target = _join(home, _safe_relative(target_relative, "stale target"))
            if _path_present(target):
                touched[target] = previous_by_target[target_relative]
        if touched:
            recovery_root = home / RECOVERY_ROOT_NAME
            if _path_present(recovery_root):
                metadata = os.lstat(recovery_root)
                if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(
                    recovery_root
                ):
                    raise CodexProductError(
                        "managed cleanup root must be an ordinary, non-reparse "
                        f"directory: {recovery_root}"
                    )
            if _path_present(transaction_root):
                raise CodexProductError(
                    f"managed cleanup transaction already exists: {transaction_root}"
                )
            transaction_root.mkdir(parents=True)
        for target, prior in sorted(touched.items(), key=lambda item: str(item[0])):
            cleanup_record = _cleanup_record(prior, transaction)
            _asset, backup, _transaction_root = _validate_cleanup_record(
                cleanup_record,
                home,
                root,
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
            backups[target] = backup
            cleanup_records.append(cleanup_record)
        for asset, staging, _selected_mode in staged:
            os.replace(staging, asset["target"])
            installed_targets.append(asset["target"])

        state = {
            "schema": STATE_SCHEMA,
            "product": "kgdistiller",
            "manifest_version": manifest["version"],
            "assets": sorted(installed, key=lambda item: item["target"]),
            "cleanup": cleanup_records,
        }
        _write_state(state_path, state)
        state_written = True
    except BaseException:
        if not state_written:
            for target in reversed(installed_targets):
                if _path_present(target):
                    _remove_exact(target)
            for target, backup in reversed(list(backups.items())):
                if _path_present(backup):
                    os.replace(backup, target)
                    _prune_empty_recovery(backup, transaction_root, home)
        raise
    finally:
        for _asset, staging, _selected_mode in staged:
            if _path_present(staging):
                if state_written:
                    try:
                        _remove_exact(staging)
                    except OSError:
                        postcommit_warnings.append(
                            "product links were committed, but a staging path still "
                            f"needs cleanup: {staging}"
                        )
                else:
                    _remove_exact(staging)

    if state is None:
        raise CodexProductError("managed-link transaction produced no state")
    cleanup_warnings: list[str] = []
    recovery_paths: list[str] = []
    if cleanup_records:
        state, cleanup_warnings, recovery_paths = _complete_committed_cleanup(
            home,
            root,
            state_path,
            state,
            fail_closed=False,
        )
    warnings = postcommit_warnings + cleanup_warnings

    product_asset = assets_by_target[manifest["installation"]["product_root"]]
    canonical_root = product_asset["target"]
    mode_counts = {
        selected: sum(item["mode"] == selected for item in installed)
        for selected in {item["mode"] for item in installed}
    }
    return {
        "schema": STATE_SCHEMA,
        "status": "linked",
        "committed": True,
        "cleanup_status": "pending" if warnings else "complete",
        "warnings": warnings,
        "recovery_paths": recovery_paths,
        "codex_home": str(home),
        "product_root": str(canonical_root),
        "manifest": str(canonical_root / "workflows" / "manifest.json"),
        "skills": sum(item["kind"] == "skill" for item in installed),
        "agents": sum(item["kind"] == "agent" for item in installed),
        "removed": len(stale),
        "real_time": all(item["mode"] != "copy" for item in installed),
        "modes": dict(sorted(mode_counts.items())),
        "protected": ["AGENTS.md", "config.toml"],
    }


def doctor_product(
    *,
    codex_home: Path | None = None,
    source_only: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    root, manifest = load_manifest(source_root)
    result: dict[str, Any] = {
        "schema": "kgdistiller-codex-doctor-v1",
        "status": "ok",
        "manifest": str(root / "workflows" / "manifest.json"),
        "manifest_version": manifest["version"],
        "skills": len(manifest["skills"]),
        "agents": len(manifest["agents"]),
        "linkers": len(manifest["linkers"]),
        "workflows": len(manifest["workflows"]),
        "installation": "not-checked" if source_only else "linked",
    }
    if source_only:
        return result

    home = _codex_home(codex_home)
    if _paths_overlap(home, root):
        raise CodexProductError(
            "Codex home and the kgdistiller product root cannot overlap"
        )
    _validate_codex_destination(home)
    state_path = home / STATE_NAME
    if not _path_present(state_path):
        raise CodexProductError(f"kgdistiller product is not linked below {home}")
    state = _load_state(state_path)
    if state.get("manifest_version") != manifest["version"]:
        raise CodexProductError(
            "managed-link state does not match the active workflow manifest version"
        )
    cleanup_warnings: list[str] = []
    recovery_paths: list[str] = []
    for value in state.get("cleanup", []):
        asset, backup, _transaction_root = _validate_cleanup_record(
            value,
            home,
            root,
        )
        if _path_present(backup):
            _verify_managed_owner(
                asset,
                backup,
                allow_detached_hardlink_recovery=True,
            )
            recovery_paths.append(str(backup))
        cleanup_warnings.append(
            "a committed product-link cleanup will be finalized by the next link"
        )
    expected_assets = _managed_assets(root, manifest, home)
    expected_by_target = {item["target_relative"]: item for item in expected_assets}
    observed = state.get("assets", [])
    if len(observed) != len(expected_by_target):
        raise CodexProductError(
            "managed-link state does not match the workflow manifest"
        )
    mode_counts: dict[str, int] = {}
    seen_targets: set[str] = set()
    for record in observed:
        record = _validate_state_record(record)
        target_relative = record["target"]
        if target_relative in seen_targets:
            raise CodexProductError("managed-link state contains a duplicate target")
        seen_targets.add(target_relative)
        asset = expected_by_target.get(target_relative)
        if asset is None:
            raise CodexProductError(
                f"managed-link state escapes the product namespace: {target_relative}"
            )
        target = asset["target"]
        source = asset["source"]
        expected_digest = _source_digest(asset, root, manifest)
        _bind_state_source(record, root, asset)
        if not _path_present(target):
            raise CodexProductError(f"managed asset is missing: {target}")
        _verify_managed_owner(record, target)
        if record.get("mode") == "copy" and record.get("digest") != expected_digest:
            raise CodexProductError(
                f"managed copy is non-live and source changed; run codex link again: {source}"
            )
        selected_mode = str(record["mode"])
        mode_counts[selected_mode] = mode_counts.get(selected_mode, 0) + 1
    canonical_root = _join(
        home,
        _safe_relative(
            manifest["installation"]["product_root"], "installed product root"
        ),
    )
    canonical_manifest = canonical_root / "workflows" / "manifest.json"
    canonical_guide = canonical_root / manifest["workflow_guide"]
    if not canonical_manifest.is_file() or not canonical_guide.is_file():
        raise CodexProductError("canonical workflow product root is incomplete")
    canonical_payload = _load_json(canonical_manifest)
    if _canonical_json(canonical_payload) != _canonical_json(manifest):
        raise CodexProductError(
            "canonical workflow manifest does not match the active product"
        )
    result["codex_home"] = str(home)
    result["product_root"] = str(canonical_root)
    result["canonical_manifest"] = str(canonical_manifest)
    result["real_time"] = all(mode != "copy" for mode in mode_counts)
    result["modes"] = dict(sorted(mode_counts.items()))
    result["cleanup_status"] = "pending" if cleanup_warnings else "complete"
    result["warnings"] = cleanup_warnings
    result["recovery_paths"] = recovery_paths
    result["protected"] = ["AGENTS.md", "config.toml"]
    return result
