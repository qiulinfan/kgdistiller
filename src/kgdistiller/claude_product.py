"""Install and diagnose kgdistiller-owned Claude Code product assets.

The transactional product-link engine lives in ``codex_product``; this module
provides the Claude Code runtime profile, manifest loader, and agent-preset
validation. Installed assets mirror the Codex layout below the Claude Code
home directory (``$CLAUDE_CONFIG_DIR`` when set, otherwise ``.claude`` in the
user profile): Skills below ``skills/``, agent presets below ``agents/``, and
the canonical product root below ``workflow-products/kgdistiller``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .codex_product import (
    AGENT_NAME_RE,
    FORBIDDEN_TEXT_RE,
    STATE_NAME,
    CodexProductError,
    RuntimeProfile,
    _doctor_runtime,
    _join,
    _link_runtime,
    _load_json,
    _safe_relative,
    _validate_linkers,
    _validate_manifest_agents,
    _validate_manifest_skills,
    _validate_skill_markdown,
    _validate_workflows,
    product_root,
)

MANIFEST_SCHEMA = "kgdistiller-claude-workflows-v1"
MANIFEST_RELATIVE = PurePosixPath("workflows", "claude-manifest.json")

# The engine raises one shared fail-closed error type for both runtimes.
ClaudeProductError = CodexProductError


def _validate_claude_agent(path: Path, declared_name: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ClaudeProductError(
            f"cannot read Claude Code agent preset: {path}"
        ) from error
    if FORBIDDEN_TEXT_RE.search(text):
        raise ClaudeProductError(f"agent preset is host-specific: {declared_name}")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ClaudeProductError(f"agent preset has no YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ClaudeProductError(
            f"agent preset frontmatter is not closed: {path}"
        ) from error
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    name = fields.get("name", "")
    if AGENT_NAME_RE.fullmatch(name) is None or name != f"kgdistiller-{declared_name}":
        raise ClaudeProductError(
            f"agent preset name must be kgdistiller-{declared_name}: {path}"
        )
    if not fields.get("description"):
        raise ClaudeProductError(
            f"agent preset {declared_name} has no frontmatter description"
        )
    if not "\n".join(lines[end + 1 :]).strip():
        raise ClaudeProductError(
            f"agent preset {declared_name} has no instruction body"
        )


def load_claude_manifest(
    explicit_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = product_root(explicit_root, manifest_relative=MANIFEST_RELATIVE)
    manifest = _load_json(_join(root, MANIFEST_RELATIVE))
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
        raise ClaudeProductError("workflow manifest has unsupported top-level fields")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("product") != "kgdistiller"
    ):
        raise ClaudeProductError(
            "workflow manifest has an unsupported schema or product"
        )
    if not isinstance(manifest.get("version"), int) or manifest["version"] < 1:
        raise ClaudeProductError("workflow manifest version is invalid")
    installation = manifest.get("installation")
    if (
        not isinstance(installation, dict)
        or set(installation) != {"product_root", "state"}
        or installation.get("product_root") != "workflow-products/kgdistiller"
        or installation.get("state") != STATE_NAME
    ):
        raise ClaudeProductError("workflow manifest installation namespace is invalid")
    workflow_guide = _safe_relative(manifest.get("workflow_guide"), "workflow guide")
    if workflow_guide != PurePosixPath("docs", "product-workflows.md"):
        raise ClaudeProductError("workflow manifest guide path is invalid")
    try:
        guide_text = _join(root, workflow_guide).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ClaudeProductError("workflow guide is missing or unreadable") from error
    if FORBIDDEN_TEXT_RE.search(guide_text):
        raise ClaudeProductError(
            "workflow guide contains a host or machine-specific path"
        )
    _validate_linkers(
        root,
        manifest.get("linkers"),
        expected={
            "posix": PurePosixPath("scripts", "link-claude-product.sh"),
            "windows": PurePosixPath("scripts", "link-claude-product.ps1"),
        },
        required_command="kgdistiller claude link",
    )
    skill_names = _validate_manifest_skills(
        root, manifest.get("skills"), _validate_skill_markdown
    )
    agent_names = _validate_manifest_agents(
        root,
        manifest.get("agents"),
        source_dir=PurePosixPath(".claude", "agents"),
        suffix=".md",
        validator=_validate_claude_agent,
    )
    _validate_workflows(manifest.get("workflows"), skill_names, agent_names)
    return root, manifest


CLAUDE_PROFILE = RuntimeProfile(
    label="Claude Code",
    command="claude",
    home_environment="CLAUDE_CONFIG_DIR",
    home_default=".claude",
    manifest_relative=MANIFEST_RELATIVE,
    state_schema="kgdistiller-claude-links-v1",
    doctor_schema="kgdistiller-claude-doctor-v1",
    home_result_key="claude_home",
    agent_source_dir=PurePosixPath(".claude", "agents"),
    agent_suffix=".md",
    protected=("CLAUDE.md", "settings.json"),
    # ``scripts/link-claude-skills.*`` historically installed bare Skill
    # symlinks; adopting an exact-source match lets the transactional
    # installer take ownership without a manual unlink step.
    adopt_matching_links=True,
    load_manifest=load_claude_manifest,
)


def link_claude_product(
    *,
    claude_home: Path | None = None,
    mode: str = "auto",
    source_root: Path | None = None,
) -> dict[str, Any]:
    return _link_runtime(
        CLAUDE_PROFILE, home=claude_home, mode=mode, source_root=source_root
    )


def doctor_claude_product(
    *,
    claude_home: Path | None = None,
    source_only: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    return _doctor_runtime(
        CLAUDE_PROFILE,
        home=claude_home,
        source_only=source_only,
        source_root=source_root,
    )
