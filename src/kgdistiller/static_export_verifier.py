#!/usr/bin/env python3
"""Verify a kgdistiller static site export using only the Python standard library."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

MANIFEST_SCHEMA = "kgdistiller-static-export-v1"
SITE_GRAPH_SCHEMA = "kgdistiller-site-graph-v1"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_GRAPH_BYTES = 128 * 1024 * 1024
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_SOURCE_FILES = 1_000_000
MAX_PUBLISHED_SOURCES = 100_000
MAX_GRAPH_NODES = 1_000_000
MAX_GRAPH_EDGES = 5_000_000
MAX_GRAPH_REFERENCES = 5_000_000
MAX_DIAGNOSTICS = 1_000_000
MAX_NODE_ID_LENGTH = 256
MAX_RELATION_LENGTH = 256
MAX_AUTHORITY_LENGTH = 4096
MAX_ARTIFACT_PATH_LENGTH = 255
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ARTIFACT_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
REGISTRY_ID_RE = re.compile(r'^\s+id: "([a-z0-9]+(?:-[a-z0-9]+)*)",\s*$', re.MULTILINE)
EXPECTED_ARTIFACT_PATHS = {
    "site-graph": "graph.json",
    "typst-registry": "knowledge-registry.typ",
    "standalone-verifier": "verify_export.py",
}
EXPECTED_BUNDLE_FILES = {"manifest.json", *EXPECTED_ARTIFACT_PATHS.values()}


class ExportVerificationError(ValueError):
    """Raised when a static export fails closed."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ExportVerificationError(f"non-canonical JSON value: {error}") from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_text_bytes(path: Path) -> bytes:
    """Read a bundle text artifact using the portable LF digest boundary."""

    try:
        raw_size = path.stat().st_size
        if raw_size < 1 or raw_size > MAX_ARTIFACT_BYTES * 2:
            raise ExportVerificationError(
                f"artifact has invalid bounded size: {path.name}"
            )
        with path.open("r", encoding="utf-8", newline=None) as handle:
            content = handle.read().encode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ExportVerificationError(
            f"artifact is not valid UTF-8 text: {path.name}"
        ) from error
    if not content or len(content) > MAX_ARTIFACT_BYTES:
        raise ExportVerificationError(f"artifact has invalid bounded size: {path.name}")
    return content


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExportVerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ExportVerificationError(f"non-finite JSON constant: {value}")


def _load_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ExportVerificationError(
            f"cannot stat JSON artifact: {path.name}"
        ) from error
    if size < 2 or size > limit:
        raise ExportVerificationError(f"JSON artifact has invalid size: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except ExportVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExportVerificationError(f"invalid JSON artifact: {path.name}") from error
    if not isinstance(value, dict):
        raise ExportVerificationError(f"JSON artifact is not an object: {path.name}")
    return value


def _safe_artifact(root: Path, value: Any) -> Path:
    if (
        not isinstance(value, str)
        or len(value) > MAX_ARTIFACT_PATH_LENGTH
        or ARTIFACT_PATH_RE.fullmatch(value) is None
        or "\\" in value
    ):
        raise ExportVerificationError("artifact path must be a portable relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ExportVerificationError(f"unsafe artifact path: {value}")
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as error:
        raise ExportVerificationError(
            f"artifact path escapes export root: {value}"
        ) from error
    return candidate


def _require_authority(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_AUTHORITY_LENGTH
        or value.startswith("/")
        or "\\" in value
        or "//" in value
    ):
        raise ExportVerificationError(f"{label} is not a portable authority path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise ExportVerificationError(f"{label} is not a portable authority path")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ExportVerificationError(f"{label} is not lowercase SHA-256")
    return value


def _require_repository(
    value: Any, label: str, *, nullable: bool = False
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ExportVerificationError(f"{label} is not a bounded HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ExportVerificationError(f"{label} is not a credential-free HTTPS URL")
    return value


def _require_counts(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"nodes", "edges", "references"}:
        raise ExportVerificationError(f"{label} has invalid keys")
    result: dict[str, int] = {}
    maxima = {
        "nodes": MAX_GRAPH_NODES,
        "edges": MAX_GRAPH_EDGES,
        "references": MAX_GRAPH_REFERENCES,
    }
    for key in ("nodes", "edges", "references"):
        count = value.get(key)
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or count > maxima[key]
        ):
            raise ExportVerificationError(f"{label}.{key} is invalid")
        result[key] = count
    return result


def _verify_graph(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, int]:
    if set(payload) != {
        "schema",
        "namespace",
        "source_graph_sha256",
        "counts",
        "nodes",
        "edges",
        "references",
        "diagnostics",
        "graph_sha256",
    }:
        raise ExportVerificationError("graph.json has unsupported top-level fields")
    if (
        payload.get("schema") != SITE_GRAPH_SCHEMA
        or payload.get("namespace") != "public"
    ):
        raise ExportVerificationError(
            "graph.json has an unsupported schema or namespace"
        )
    _require_sha256(payload.get("source_graph_sha256"), "graph.source_graph_sha256")
    claimed = _require_sha256(payload.get("graph_sha256"), "graph.graph_sha256")
    digest_payload = copy.deepcopy(payload)
    digest_payload.pop("graph_sha256", None)
    if claimed != _sha256_json(digest_payload):
        raise ExportVerificationError(
            "graph.graph_sha256 does not match canonical content"
        )

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    references = payload.get("references")
    diagnostics = payload.get("diagnostics")
    if (
        not isinstance(nodes, list)
        or not isinstance(edges, list)
        or not isinstance(references, list)
    ):
        raise ExportVerificationError("graph collections must be arrays")
    if (
        len(nodes) > MAX_GRAPH_NODES
        or len(edges) > MAX_GRAPH_EDGES
        or len(references) > MAX_GRAPH_REFERENCES
    ):
        raise ExportVerificationError("graph collections exceed schema limits")
    if not isinstance(diagnostics, dict) or set(diagnostics) != {
        "errors",
        "warnings",
        "info",
    }:
        raise ExportVerificationError(
            "graph diagnostics must contain errors, warnings, and info"
        )
    counts = _require_counts(payload.get("counts"), "graph.counts")
    observed = {"nodes": len(nodes), "edges": len(edges), "references": len(references)}
    if counts != observed:
        raise ExportVerificationError("graph counts do not match graph collections")

    node_ids: set[str] = set()
    knowledge_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ExportVerificationError("graph contains a non-object node")
        node_id = node.get("id")
        if (
            not isinstance(node_id, str)
            or not node_id
            or len(node_id) > MAX_NODE_ID_LENGTH
            or node_id in node_ids
        ):
            raise ExportVerificationError(
                "graph contains an empty or duplicate node id"
            )
        node_type = node.get("type")
        if not isinstance(node_type, str) or node_type not in (
            "knowledge",
            "field",
            "topic",
        ):
            raise ExportVerificationError(f"graph node has invalid type: {node_id}")
        if not isinstance(node.get("label"), str) or not node["label"]:
            raise ExportVerificationError(f"graph node has no label: {node_id}")
        for field in ("entry", "properties", "provenance"):
            if field in node and not isinstance(node[field], dict):
                raise ExportVerificationError(
                    f"graph node {field} must be an object: {node_id}"
                )
        if "text" in node and not isinstance(node["text"], str):
            raise ExportVerificationError(
                f"graph node text must be a string: {node_id}"
            )
        node_ids.add(node_id)
        if node.get("type") == "knowledge":
            knowledge_ids.add(node_id)
        properties = node.get("properties")
        if isinstance(properties, dict) and "entry_path" in properties:
            raise ExportVerificationError(
                f"graph node exposes a private entry path: {node_id}"
            )

    if [str(node.get("id", "")) for node in nodes] != sorted(node_ids):
        raise ExportVerificationError("graph nodes are not in canonical id order")

    edge_keys: set[tuple[str, str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {"source", "relation", "target"}:
            raise ExportVerificationError(
                "public graph edge must contain only source, relation, and target"
            )
        source = edge.get("source")
        relation = edge.get("relation")
        target = edge.get("target")
        if (
            not isinstance(source, str)
            or not source
            or len(source) > MAX_NODE_ID_LENGTH
            or not isinstance(relation, str)
            or not relation
            or len(relation) > MAX_RELATION_LENGTH
            or not isinstance(target, str)
            or not target
            or len(target) > MAX_NODE_ID_LENGTH
        ):
            raise ExportVerificationError("graph edge has invalid bounded strings")
        if source not in node_ids or target not in node_ids:
            raise ExportVerificationError("graph edge has a missing endpoint")
        key = (source, relation, target)
        if key in edge_keys:
            raise ExportVerificationError("graph edge is empty or duplicated")
        edge_keys.add(key)
    if [
        (
            str(edge.get("source", "")),
            str(edge.get("relation", "")),
            str(edge.get("target", "")),
        )
        for edge in edges
    ] != sorted(edge_keys):
        raise ExportVerificationError("graph edges are not in canonical order")

    source_manifest = manifest.get("source")
    published_hashes = (
        source_manifest.get("published_hashes")
        if isinstance(source_manifest, dict)
        else None
    )
    if not isinstance(published_hashes, dict):
        raise ExportVerificationError("manifest published source hashes are invalid")
    for node in nodes:
        if node.get("type") != "knowledge":
            continue
        provenance = node.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("active") is not True:
            raise ExportVerificationError(
                f"public knowledge node has a private or inactive authority: {node.get('id')}"
            )
        authority = _require_authority(
            provenance.get("authority"),
            f"public knowledge node authority {node.get('id')}",
        )
        if authority not in published_hashes:
            raise ExportVerificationError(
                f"public knowledge node has a private or inactive authority: {node.get('id')}"
            )
    for reference in references:
        if not isinstance(reference, dict):
            raise ExportVerificationError("graph reference is not an object")
        target = reference.get("target")
        authority = reference.get("authority")
        if (
            not isinstance(target, str)
            or not target
            or len(target) > MAX_NODE_ID_LENGTH
        ):
            raise ExportVerificationError("graph reference has an invalid target")
        if target not in node_ids:
            raise ExportVerificationError("graph reference has a missing target")
        authority = _require_authority(authority, "graph reference authority")
        if authority not in published_hashes:
            raise ExportVerificationError(
                "graph reference has a private or missing authority"
            )
    diagnostic_counts: dict[str, int] = {}
    for severity in ("errors", "warnings", "info"):
        values = diagnostics[severity]
        if not isinstance(values, list):
            raise ExportVerificationError(
                f"graph diagnostics.{severity} must be an array"
            )
        if len(values) > MAX_DIAGNOSTICS:
            raise ExportVerificationError(
                f"graph diagnostics.{severity} exceeds the schema limit"
            )
        for value in values:
            if not isinstance(value, dict) or not {"code", "message"} <= set(value):
                raise ExportVerificationError(
                    f"graph diagnostics.{severity} has an invalid record"
                )
            if set(value) - {"code", "message", "source", "node"}:
                raise ExportVerificationError(
                    f"graph diagnostics.{severity} has unsupported fields"
                )
            if (
                not isinstance(value.get("code"), str)
                or not value["code"]
                or len(value["code"]) > MAX_NODE_ID_LENGTH
            ):
                raise ExportVerificationError(
                    f"graph diagnostics.{severity} has an invalid code"
                )
            if not isinstance(value.get("message"), str) or not value["message"]:
                raise ExportVerificationError(
                    f"graph diagnostics.{severity} has an invalid message"
                )
            if "node" in value:
                node = value["node"]
                if (
                    not isinstance(node, str)
                    or not node
                    or len(node) > MAX_NODE_ID_LENGTH
                    or node not in node_ids
                ):
                    raise ExportVerificationError(
                        f"graph diagnostics.{severity} names a private node"
                    )
            if "source" in value:
                source = _require_authority(
                    value["source"],
                    f"graph diagnostics.{severity} source",
                )
                if source not in published_hashes:
                    raise ExportVerificationError(
                        f"graph diagnostics.{severity} names a private source"
                    )
        if values != sorted(values, key=_canonical_json):
            raise ExportVerificationError(
                f"graph diagnostics.{severity} is not canonical"
            )
        diagnostic_counts[f"diagnostic_{severity}"] = len(values)

    graph_manifest = manifest.get("graph")
    if not isinstance(graph_manifest, dict):
        raise ExportVerificationError("manifest graph record is invalid")
    if claimed != graph_manifest.get("public_sha256"):
        raise ExportVerificationError(
            "manifest public graph digest does not match graph.json"
        )
    if counts != _require_counts(
        graph_manifest.get("public_counts"), "manifest.graph.public_counts"
    ):
        raise ExportVerificationError("manifest public counts do not match graph.json")
    if payload.get("source_graph_sha256") != graph_manifest.get("private_sha256"):
        raise ExportVerificationError(
            "graph source digest does not match manifest private graph"
        )
    return {**counts, "knowledge": len(knowledge_ids), **diagnostic_counts}


def _verify_export(path: str | Path) -> dict[str, Any]:
    root_argument = Path(path).expanduser()
    manifest_path = (
        root_argument if root_argument.is_file() else root_argument / "manifest.json"
    )
    root = manifest_path.parent.resolve()
    try:
        bundle_files = {child.name for child in root.iterdir()}
    except OSError as error:
        raise ExportVerificationError(
            "cannot inspect export bundle directory"
        ) from error
    if bundle_files != EXPECTED_BUNDLE_FILES or any(
        not child.is_file() or child.is_symlink() for child in root.iterdir()
    ):
        raise ExportVerificationError("export must be exactly four non-symbolic files")
    manifest = _load_json(manifest_path, MAX_MANIFEST_BYTES)
    expected_manifest_fields = {
        "schema",
        "status",
        "producer",
        "source",
        "visibility",
        "graph",
        "artifacts",
        "export_sha256",
    }
    if "replaces_export_sha256" in manifest:
        expected_manifest_fields.add("replaces_export_sha256")
    if set(manifest) != expected_manifest_fields:
        raise ExportVerificationError("manifest has unsupported top-level fields")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "exported"
    ):
        raise ExportVerificationError("manifest has an unsupported schema or status")
    claimed_export = _require_sha256(
        manifest.get("export_sha256"), "manifest.export_sha256"
    )
    digest_payload = copy.deepcopy(manifest)
    digest_payload.pop("export_sha256", None)
    if claimed_export != _sha256_json(digest_payload):
        raise ExportVerificationError(
            "manifest export_sha256 does not match canonical content"
        )
    if "replaces_export_sha256" in manifest:
        replaced = _require_sha256(
            manifest.get("replaces_export_sha256"),
            "manifest.replaces_export_sha256",
        )
        if replaced == claimed_export:
            raise ExportVerificationError("manifest cannot replace itself")

    producer = manifest.get("producer")
    if (
        not isinstance(producer, dict)
        or set(producer) != {"name", "repository", "version", "commit"}
        or producer.get("name") != "kgdistiller"
        or not isinstance(producer.get("version"), str)
        or not producer["version"]
        or len(producer["version"]) > 128
        or not isinstance(producer.get("commit"), str)
        or GIT_COMMIT_RE.fullmatch(producer["commit"]) is None
    ):
        raise ExportVerificationError("manifest producer is invalid")
    _require_repository(producer.get("repository"), "manifest.producer.repository")

    source = manifest.get("source")
    if not isinstance(source, dict) or set(source) != {
        "repository",
        "revision",
        "digest",
        "files",
        "published_digest",
        "published_files",
        "published_hashes",
    }:
        raise ExportVerificationError("manifest source record is invalid")
    _require_repository(source.get("repository"), "manifest.source.repository")
    revision = source.get("revision")
    if not isinstance(revision, str) or GIT_COMMIT_RE.fullmatch(revision) is None:
        raise ExportVerificationError("manifest source revision is invalid")
    _require_sha256(source.get("digest"), "manifest.source.digest")
    _require_sha256(source.get("published_digest"), "manifest.source.published_digest")
    for field in ("files", "published_files"):
        value = source.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_SOURCE_FILES
        ):
            raise ExportVerificationError(f"manifest source {field} is invalid")
    if source["published_files"] > source["files"]:
        raise ExportVerificationError(
            "published source count exceeds private source count"
        )
    published_hashes = source.get("published_hashes")
    if (
        not isinstance(published_hashes, dict)
        or len(published_hashes) > MAX_SOURCE_FILES
    ):
        raise ExportVerificationError("manifest published source hashes are invalid")
    for authority, digest in published_hashes.items():
        _require_authority(authority, "published source authority")
        _require_sha256(digest, f"published source hash {authority}")
    if source.get("published_files") != len(published_hashes):
        raise ExportVerificationError(
            "published source count does not match published_hashes"
        )
    if source.get("published_digest") != _sha256_json(published_hashes):
        raise ExportVerificationError(
            "published source digest does not match published_hashes"
        )

    visibility = manifest.get("visibility")
    if (
        not isinstance(visibility, dict)
        or set(visibility) != {"policy", "published_sources", "excluded_sources"}
        or visibility.get("policy") != "explicit-publish"
        or not isinstance(visibility.get("published_sources"), list)
        or any(
            not isinstance(value, str) or not value or len(value) > MAX_NODE_ID_LENGTH
            for value in visibility["published_sources"]
        )
        or len(visibility["published_sources"]) > MAX_PUBLISHED_SOURCES
        or len(set(visibility["published_sources"]))
        != len(visibility["published_sources"])
        or not isinstance(visibility.get("excluded_sources"), int)
        or isinstance(visibility.get("excluded_sources"), bool)
        or visibility["excluded_sources"] < 0
        or visibility["excluded_sources"] > MAX_PUBLISHED_SOURCES
    ):
        raise ExportVerificationError("manifest visibility record is invalid")

    graph_manifest = manifest.get("graph")
    if not isinstance(graph_manifest, dict) or set(graph_manifest) != {
        "private_schema",
        "private_sha256",
        "private_counts",
        "public_schema",
        "public_sha256",
        "public_counts",
    }:
        raise ExportVerificationError("manifest graph record is invalid")
    if (
        graph_manifest.get("private_schema") != "kgdistiller-graph-v1"
        or graph_manifest.get("public_schema") != SITE_GRAPH_SCHEMA
    ):
        raise ExportVerificationError("manifest graph schemas are invalid")
    _require_sha256(
        manifest.get("graph", {}).get("private_sha256"), "manifest.graph.private_sha256"
    )
    _require_sha256(
        manifest.get("graph", {}).get("public_sha256"), "manifest.graph.public_sha256"
    )
    _require_counts(
        graph_manifest.get("private_counts"), "manifest.graph.private_counts"
    )
    _require_counts(graph_manifest.get("public_counts"), "manifest.graph.public_counts")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 3:
        raise ExportVerificationError(
            "manifest artifacts must contain exactly three records"
        )
    required_kinds = {"site-graph", "typst-registry", "standalone-verifier"}
    by_kind: dict[str, Path] = {}
    seen_paths: set[str] = set()
    for record in artifacts:
        if not isinstance(record, dict):
            raise ExportVerificationError("manifest contains a non-object artifact")
        kind = record.get("kind")
        raw_path = record.get("path")
        if set(record) != {"kind", "path", "bytes", "sha256"}:
            raise ExportVerificationError(
                "manifest artifact record has unsupported fields"
            )
        if (
            not isinstance(kind, str)
            or kind not in required_kinds
            or kind in by_kind
            or not isinstance(raw_path, str)
            or raw_path in seen_paths
            or raw_path != EXPECTED_ARTIFACT_PATHS.get(str(kind))
        ):
            raise ExportVerificationError(
                "manifest artifact kinds and paths must be unique"
            )
        artifact = _safe_artifact(root, raw_path)
        if not artifact.is_file() or artifact.is_symlink():
            raise ExportVerificationError(
                f"artifact is missing or symbolic: {raw_path}"
            )
        content = _canonical_text_bytes(artifact)
        byte_count = record.get("bytes")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 1
            or byte_count > MAX_ARTIFACT_BYTES
        ):
            raise ExportVerificationError(f"artifact byte count is invalid: {raw_path}")
        if byte_count != len(content):
            raise ExportVerificationError(f"artifact byte count mismatch: {raw_path}")
        if _require_sha256(
            record.get("sha256"), f"artifact {raw_path} sha256"
        ) != _sha256_bytes(content):
            raise ExportVerificationError(f"artifact digest mismatch: {raw_path}")
        by_kind[str(kind)] = artifact
        seen_paths.add(str(raw_path))
    if set(by_kind) != required_kinds:
        raise ExportVerificationError(
            "manifest does not contain exactly the required artifacts"
        )

    graph_payload = _load_json(by_kind["site-graph"], MAX_GRAPH_BYTES)
    counts = _verify_graph(graph_payload, manifest)
    registry = by_kind["typst-registry"].read_text(encoding="utf-8")
    if not registry.startswith("// Generated by kgdistiller.") or "\x00" in registry:
        raise ExportVerificationError("Typst registry is invalid")
    registry_ids = set(REGISTRY_ID_RE.findall(registry))
    knowledge_ids = {
        str(node["id"])
        for node in graph_payload["nodes"]
        if node.get("type") == "knowledge"
    }
    if registry_ids != knowledge_ids:
        raise ExportVerificationError(
            "Typst registry ids do not match public knowledge nodes"
        )

    return {
        "schema": "kgdistiller-static-export-verification-v1",
        "status": "ok",
        "export_sha256": claimed_export,
        "replaces_export_sha256": manifest.get("replaces_export_sha256"),
        "producer_commit": producer.get("commit"),
        "private_graph_sha256": manifest["graph"]["private_sha256"],
        "public_graph_sha256": manifest["graph"]["public_sha256"],
        "counts": counts,
        "artifacts": len(artifacts),
    }


def verify_export(path: str | Path) -> dict[str, Any]:
    """Verify a bundle and normalize malformed value types to one public error."""

    try:
        return _verify_export(path)
    except ExportVerificationError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as error:
        raise ExportVerificationError(
            f"export contains an invalid value type: {error}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="bundle directory or manifest.json")
    args = parser.parse_args(argv)
    try:
        result = verify_export(args.export)
    except (ExportVerificationError, OSError, UnicodeError) as error:
        print(f"verify_export: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
