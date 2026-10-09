"""User-owned extraction profiles from the knowledge source registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _name(value: Any, label: str) -> str:
    from .cli import KnowledgeError

    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or any(ord(character) < 32 for character in value)):
        raise KnowledgeError(f"{label} must be nonempty single-line text without surrounding whitespace")
    return value


def parse_document_types(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate profiles without supplying classifications or extraction defaults."""
    from .cli import KnowledgeError

    profiles = payload.get("document_types", {})
    if not isinstance(profiles, dict):
        raise KnowledgeError("document_types must be an object keyed by user-defined names")
    result = {}
    for name, profile in profiles.items():
        _name(name, "document type name")
        if not isinstance(profile, dict) or set(profile) != {"node_kinds", "extraction_guidance"}:
            raise KnowledgeError(f"document type {name!r} requires node_kinds and extraction_guidance")
        kinds = profile["node_kinds"]
        if not isinstance(kinds, list) or not kinds:
            raise KnowledgeError(f"document type {name!r} node_kinds must be a nonempty list")
        kinds = [_name(kind, f"document type {name!r} node kind") for kind in kinds]
        if len(kinds) != len(set(kinds)):
            raise KnowledgeError(f"document type {name!r} node_kinds must be unique")
        guidance = profile["extraction_guidance"]
        if not isinstance(guidance, str) or not guidance.strip():
            raise KnowledgeError(f"document type {name!r} extraction_guidance must be nonempty text")
        result[name] = {"node_kinds": kinds, "extraction_guidance": guidance}
    return result


def load_document_types(registry: Path) -> dict[str, dict[str, Any]]:
    from .cli import SOURCE_SCHEMA, KnowledgeError, read_json

    payload = read_json(registry, {})
    if not isinstance(payload, dict) or payload.get("schema") != SOURCE_SCHEMA:
        raise KnowledgeError(f"expected {SOURCE_SCHEMA} source registry: {registry}")
    return parse_document_types(payload)


def validate_document_type(value: Any, profiles: dict[str, dict[str, Any]]) -> str:
    """Validate an explicitly selected profile; callers handle omitted values."""
    from .cli import KnowledgeError

    name = _name(value, "document_type")
    if name not in profiles:
        raise KnowledgeError(f"unknown document_type: {name!r}; register its extraction profile first")
    return name


def validate_node_kind(kind: Any, document_type: str, profiles: dict[str, dict[str, Any]]) -> str:
    """Check a reviewed semantic kind against an explicitly selected profile."""
    from .cli import KnowledgeError

    value = _name(kind, "knowledge node kind")
    if document_type:
        validate_document_type(document_type, profiles)
        if value not in profiles[document_type]["node_kinds"]:
            raise KnowledgeError(f"node kind {value!r} is not allowed by document type {document_type!r}")
    return value
