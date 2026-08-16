"""Deterministic qlkg-v3 compilation from Obsidian-native Vault notes."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping

from .cli import (
    GraphState,
    GRAPH_SCHEMA,
    ENTRY_SHARD_LIMIT,
    KnowledgeError,
    edge_key,
    identity_key,
    json_text,
    load_fields,
    load_sources,
    load_state,
    make_agent_snapshot,
    make_artifacts,
    pretty_json,
    sha256_text,
    validate_state,
)
from .contracts import sha256_json, validate_contract
from .native_notes import ConceptNote, NativeNote, TaxonomyNote, parse_native_markdown
from .source_archive import (
    _PinnedDirectory,
    _remove_stage,
    SourceArchiveError,
    SourceEvidenceView,
    current_evidence_view,
    load_source_ledger,
    read_vault_relative_regular,
    replace_vault_relative_regular,
    unlink_vault_relative_regular,
    vault_generation_guard,
)
from .vaults import (
    MAX_ID_BYTES,
    VAULT_ID_RE,
    ManagedMarkdownFile,
    ManagedMarkdownToken,
    Vault,
    VaultError,
    managed_markdown_token,
    load_registry,
    load_vault,
    snapshot_managed_markdown,
    vault_registry_lock,
)


REPORT_SCHEMA = "qlkg-knowledge-report-v1"
MAX_GRAPH_ARTIFACTS = 100_032
MAX_NATIVE_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_NATIVE_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_NATIVE_GRAPH_BYTES = 2 * 1024 * 1024 * 1024
MAX_NATIVE_EDGES = 500_000
MAX_NATIVE_NOTES = 100_000
MAX_NATIVE_NOTE_BYTES = 512 * 1024 * 1024
MAX_GRAPH_TRANSACTION_BYTES = 64 * 1024 * 1024
GRAPH_TRANSACTION_SCHEMA = "kgdistiller-graph-transaction-v1"
GRAPH_TRANSACTION_PATH = ".kgdistiller/build/graph-transaction.json"
_STAGE_NAME_RE = re.compile(r"\.stage-knowledge-[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MISSING_MANIFEST_SHA256 = hashlib.sha256(b"").hexdigest()
_EMPTY_LEDGER_SHA256 = sha256_json({"schema": "kgdistiller-empty-source-ledger-v1"})


def _native_graph_artifact_limit(name: str) -> int:
    """Return the authoritative bound for one portable graph artifact."""

    relative = _graph_relative(name).as_posix()
    if relative == "manifest.json":
        return MAX_NATIVE_MANIFEST_BYTES
    if relative.startswith("entries/"):
        return ENTRY_SHARD_LIMIT
    return MAX_NATIVE_ARTIFACT_BYTES


class NativeCompilerError(RuntimeError):
    """A stable native graph compilation or verification failure."""

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
            "kind": "kgdistiller-knowledge-error",
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class NativeCompilation:
    vault: Vault
    notes: tuple[NativeNote, ...]
    authority_token: ManagedMarkdownToken
    ledger_generation: str | None
    source_registry: dict[str, Any]
    source_registry_text: str
    state: GraphState
    artifacts: dict[str, str]
    diagnostics: dict[str, list[dict[str, Any]]]


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _without_markdown_suffix(authority: str) -> str:
    if not authority.endswith(".md"):
        raise NativeCompilerError(
            "noncanonical-native-path",
            f"managed native-note paths must use the lowercase .md suffix: {authority}",
        )
    return authority[:-3]


def _native_inventory(
    vault: Vault,
) -> tuple[tuple[NativeNote, ...], ManagedMarkdownToken]:
    return _inventory_from_snapshots(vault, snapshot_managed_markdown(vault))


def _inventory_from_snapshots(
    vault: Vault,
    snapshots: tuple[ManagedMarkdownFile, ...],
) -> tuple[tuple[NativeNote, ...], ManagedMarkdownToken]:
    notes: list[NativeNote] = []
    ids: dict[str, str] = {}
    paths: dict[str, str] = {}
    total_bytes = 0
    roots = {
        vault.concept_root: "concept",
        vault.field_root: "field",
        vault.topic_root: "topic",
    }
    for snapshot in snapshots:
        expected = next(
            (
                kind
                for root, kind in roots.items()
                if root == snapshot.path or root in snapshot.path.parents
            ),
            None,
        )
        if expected is None:
            raise NativeCompilerError(
                "wrong-native-root",
                f"native note is outside the configured managed roots: {snapshot.authority}",
            )
        path_without_suffix = _without_markdown_suffix(snapshot.authority)
        folded = _path_key(path_without_suffix)
        collision = paths.get(folded)
        if collision is not None and collision != path_without_suffix:
            raise NativeCompilerError(
                "colliding-native-path",
                "managed note paths collide under Unicode/case normalization",
                details={"first": collision, "second": path_without_suffix},
            )
        paths[folded] = path_without_suffix
        note = parse_native_markdown(
            snapshot.data,
            authority=snapshot.authority,
            path=snapshot.path,
        )
        total_bytes += len(snapshot.data)
        if len(notes) >= MAX_NATIVE_NOTES or total_bytes > MAX_NATIVE_NOTE_BYTES:
            raise NativeCompilerError(
                "native-inventory-too-large",
                "native note inventory exceeds the bounded graph compiler limits",
            )
        if expected == "concept" and not isinstance(note, ConceptNote):
            raise NativeCompilerError(
                "wrong-native-root",
                f"concept root contains a non-concept note: {snapshot.authority}",
            )
        if expected in {"field", "topic"} and (
            not isinstance(note, TaxonomyNote) or note.kind != expected
        ):
            raise NativeCompilerError(
                "wrong-native-root",
                f"{expected} root contains the wrong native-note kind: {snapshot.authority}",
            )
        previous = ids.get(note.id)
        if previous is not None:
            raise NativeCompilerError(
                "duplicate-native-id",
                f"global kgd_id {note.id!r} occurs more than once",
                details={"first": previous, "second": note.authority},
            )
        ids[note.id] = note.authority
        notes.append(note)
    ordered = tuple(sorted(notes, key=lambda item: item.authority))
    _validate_native_identities(ordered)
    return ordered, managed_markdown_token(snapshots)


def _validate_native_identities(notes: tuple[NativeNote, ...]) -> None:
    seen: dict[str, tuple[str, str, str, str]] = {}
    for note in notes:
        identities = (
            ("id", note.id),
            ("label", note.label),
            *(("alias", alias) for alias in note.aliases),
        )
        for role, raw in identities:
            key = identity_key(raw)
            if not key:
                raise NativeCompilerError(
                    "invalid-native-identity",
                    f"concept identity string is empty after normalization: {note.authority}",
                )
            previous = seen.get(key)
            same_canonical_identity = (
                previous is not None
                and previous[0] == note.id
                and {previous[3], role} == {"id", "label"}
            )
            if previous is not None and not same_canonical_identity:
                previous_id, previous_raw, previous_authority, previous_role = previous
                raise NativeCompilerError(
                    "conflicting-native-identity",
                    "concept identity strings collide under the established normalization",
                    details={
                        "first_id": previous_id,
                        "first_value": previous_raw,
                        "first_role": previous_role,
                        "first_authority": previous_authority,
                        "second_id": note.id,
                        "second_value": raw,
                        "second_role": role,
                        "second_authority": note.authority,
                    },
                )
            if previous is None:
                seen[key] = (note.id, raw, note.authority, role)


class _NoteResolver:
    def __init__(self, notes: Iterable[NativeNote]) -> None:
        self._exact: dict[str, NativeNote] = {}
        self._folded: dict[str, str] = {}
        for note in notes:
            target = _without_markdown_suffix(note.authority)
            self._exact[target] = note
            self._folded[_path_key(target)] = target

    def resolve(
        self,
        target: str,
        *,
        source: NativeNote,
        expected: str,
    ) -> NativeNote:
        result = self._exact.get(target)
        if result is None:
            folded = self._folded.get(_path_key(target))
            if folded is not None:
                raise NativeCompilerError(
                    "native-link-case-mismatch",
                    f"link in {source.authority} must use exact path case: {folded}",
                )
            raise NativeCompilerError(
                "missing-native-link",
                f"link in {source.authority} does not resolve: {target}",
            )
        valid = (
            (expected == "concept" and isinstance(result, ConceptNote))
            or (
                expected in {"field", "topic"}
                and isinstance(result, TaxonomyNote)
                and result.kind == expected
            )
        )
        if not valid:
            raise NativeCompilerError(
                "wrong-native-link-kind",
                f"link in {source.authority} must resolve to a {expected} note: {target}",
            )
        return result


def _provenance(note: NativeNote) -> dict[str, Any]:
    return {
        "authority": note.authority,
        "line": note.h1_line,
        "definition_start_line": note.h1_line,
        "definition_end_line": note.end_line,
        "definition_sha256": note.definition_sha256,
        "active": True,
    }


def _contains_edge(source: str, target: str, authority: str) -> dict[str, Any]:
    return {
        "source": source,
        "relation": "contains",
        "target": target,
        "origin": "registry-taxonomy",
        "confidence": "high",
        "evidence": f"typed native taxonomy declaration in {authority}",
    }


def _semantic_edge(
    source: ConceptNote,
    relation: str,
    target: ConceptNote,
    evidence: SourceEvidenceView,
    *,
    declaration_property: str,
    declaration_authority: str,
) -> dict[str, Any]:
    source_id = source.id
    target_id = target.id
    if relation == "contrasts-with" and target_id < source_id:
        source_id, target_id = target_id, source_id
    current = evidence.has_relation(source_id, relation, target_id)
    nodes = {source.id: source, target.id: target}
    edge = {
        "source": source_id,
        "relation": relation,
        "target": target_id,
        "origin": "native-note",
        "confidence": "high",
        "evidence": f"Declared by {declaration_property} in {declaration_authority}",
        "evidence_fingerprints": {
            node_id: nodes[node_id].definition_sha256
            for node_id in sorted({source_id, target_id})
        },
        "curation_status": "current" if current else "needs-review",
    }
    return edge


def _build_graph(
    notes: tuple[NativeNote, ...], evidence: SourceEvidenceView
) -> tuple[GraphState, _NoteResolver]:
    resolver = _NoteResolver(notes)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    concepts = tuple(note for note in notes if isinstance(note, ConceptNote))
    taxonomy = tuple(note for note in notes if isinstance(note, TaxonomyNote))

    for note in taxonomy:
        parents = tuple(
            resolver.resolve(link.target, source=note, expected="field")
            for link in note.parents
        )
        properties: dict[str, Any] = {
            "kind": note.kind,
            "aliases": list(note.aliases),
            "origin": "registry-taxonomy",
            "source_status": "meta",
        }
        if note.kind == "topic":
            properties["fields"] = sorted({parent.id for parent in parents})
        nodes[note.id] = {
            "id": note.id,
            "type": note.kind,
            "label": note.label,
            "text": note.body,
            "properties": properties,
            "provenance": _provenance(note),
        }
        for parent in parents:
            edge = _contains_edge(parent.id, note.id, note.authority)
            key = edge_key(edge)
            if key not in edges and len(edges) >= MAX_NATIVE_EDGES:
                raise NativeCompilerError(
                    "native-graph-too-large",
                    f"compiled graph exceeds {MAX_NATIVE_EDGES} edges",
                )
            edges[key] = edge

    for note in concepts:
        fields = tuple(
            resolver.resolve(link.target, source=note, expected="field")
            for link in note.fields
        )
        topics = tuple(
            resolver.resolve(link.target, source=note, expected="topic")
            for link in note.topics
        )
        effective_field_ids = {
            field.id for field in fields
        } | {
            resolver.resolve(parent.target, source=topic, expected="field").id
            for topic in topics
            for parent in topic.parents
        }
        has_entry = bool(note.body.strip())
        if not has_entry:
            curation_status = "pending"
        elif evidence.has_concept(note.id):
            curation_status = "current"
        else:
            curation_status = "needs-review"
        properties = {
            "kind": "concept",
            "aliases": list(note.aliases),
            "tags": list(note.tags),
            "origin": "authored",
            "source_status": "active",
            "fields": sorted(effective_field_ids),
            "topics": sorted({topic.id for topic in topics}),
            "knowledge_origin": "personal-note",
            "source_format": "markdown",
            "source_name": note.label,
            "curation_status": curation_status,
        }
        if curation_status == "current":
            properties["curated_definition_sha256"] = note.definition_sha256
        nodes[note.id] = {
            "id": note.id,
            "type": "knowledge",
            "label": note.label,
            "text": note.body,
            "properties": properties,
            "provenance": _provenance(note),
        }
        for parent in (*fields, *topics):
            edge = _contains_edge(parent.id, note.id, note.authority)
            key = edge_key(edge)
            if key not in edges and len(edges) >= MAX_NATIVE_EDGES:
                raise NativeCompilerError(
                    "native-graph-too-large",
                    f"compiled graph exceeds {MAX_NATIVE_EDGES} edges",
                )
            edges[key] = edge

    relation_properties = (
        ("prerequisites", "prerequisite-for", True),
        ("implies", "implies", False),
        ("generalizes", "generalizes", False),
        ("contrasts_with", "contrasts-with", False),
        ("derived_from", "derived-from", False),
    )
    for note in concepts:
        for attribute, relation, reverse in relation_properties:
            for link in getattr(note, attribute):
                linked = resolver.resolve(link.target, source=note, expected="concept")
                if linked.id == note.id:
                    raise NativeCompilerError(
                        "native-semantic-self-relation",
                        f"{attribute} in {note.authority} must not target the same concept",
                    )
                source, target = (linked, note) if reverse else (note, linked)
                edge = _semantic_edge(
                    source,
                    relation,
                    target,
                    evidence,
                    declaration_property=f"kgd_{attribute}",
                    declaration_authority=note.authority,
                )
                key = edge_key(edge)
                previous = edges.get(key)
                if previous is not None and relation != "contrasts-with":
                    raise NativeCompilerError(
                        "duplicate-native-relation",
                        f"semantic relation is declared more than once: {key}",
                    )
                if previous is None and len(edges) >= MAX_NATIVE_EDGES:
                    raise NativeCompilerError(
                        "native-graph-too-large",
                        f"compiled graph exceeds {MAX_NATIVE_EDGES} edges",
                    )
                edges[key] = edge
    return GraphState(nodes, edges, [], {}), resolver


def _source_registry(
    vault: Vault,
    notes: tuple[NativeNote, ...],
    resolver: _NoteResolver,
) -> dict[str, Any]:
    fields = sorted(
        (note for note in notes if isinstance(note, TaxonomyNote) and note.kind == "field"),
        key=lambda item: item.id,
    )
    topics = sorted(
        (note for note in notes if isinstance(note, TaxonomyNote) and note.kind == "topic"),
        key=lambda item: item.id,
    )
    topic_records = []
    for note in topics:
        parents = sorted({
            resolver.resolve(link.target, source=note, expected="field").id
            for link in note.parents
        })
        topic_records.append(
            {
                "glob": note.path.relative_to(vault.topic_root).as_posix(),
                "id": note.id,
                "label": note.label,
                "fields": parents,
            }
        )

    def source(kind: str, root_field: str, *, topic_rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": f"{vault.id}:native-{kind}",
            "subject": vault.id,
            "course": kind,
            "knowledge_origin": "personal-note",
            "fields": [],
            "root": str(vault.manifest[root_field]),
            "files": ["**/*.md"],
            "web": "",
            "topics": topic_rows,
        }

    return {
        "schema": "qlkg-sources-v3",
        "fields": [
            {
                "id": note.id,
                "label": note.label,
                "text": note.body,
                "aliases": list(note.aliases),
            }
            for note in fields
        ],
        "sources": [
            source("concepts", "concept_root", topic_rows=[]),
            source("fields", "field_root", topic_rows=[]),
            source("topics", "topic_root", topic_rows=topic_records),
        ],
    }


def compile_native_snapshot(
    vault: Vault,
    snapshots: tuple[ManagedMarkdownFile, ...],
    evidence: SourceEvidenceView,
    *,
    ledger_generation: str | None,
) -> NativeCompilation:
    """Purely compile an already captured note/evidence snapshot."""

    notes, authority_token = _inventory_from_snapshots(vault, snapshots)
    state, resolver = _build_graph(notes, evidence)
    source_registry = _source_registry(vault, notes, resolver)
    source_registry_text = pretty_json(source_registry)
    registry_sha256 = sha256_text(json_text(source_registry))
    source_hashes = {
        note.authority: sha256_text(note.normalized_text)
        for note in notes
    }
    artifacts = make_artifacts(
        state,
        source_hashes,
        registry_sha256=registry_sha256,
    )
    for line in artifacts["nodes.jsonl"].splitlines():
        serialized = json.loads(line)
        node_id = str(serialized["id"])
        state.nodes[node_id]["properties"] = dict(serialized.get("properties") or {})
    if len(artifacts) > MAX_GRAPH_ARTIFACTS:
        raise NativeCompilerError(
            "native-graph-too-large",
            f"compiled graph exceeds {MAX_GRAPH_ARTIFACTS} artifacts",
        )
    diagnostics = json.loads(artifacts["diagnostics.json"])
    if diagnostics["errors"]:
        raise NativeCompilerError(
            "invalid-native-graph",
            "; ".join(item["message"] for item in diagnostics["errors"]),
            details={"diagnostics": diagnostics["errors"][:32]},
        )
    state.manifest = json.loads(artifacts["manifest.json"])
    return NativeCompilation(
        vault=vault,
        notes=notes,
        authority_token=authority_token,
        ledger_generation=ledger_generation,
        source_registry=source_registry,
        source_registry_text=source_registry_text,
        state=state,
        artifacts=artifacts,
        diagnostics=diagnostics,
    )


def compile_vault_overlay(
    vault: Vault | Path | str,
    overlay: Mapping[str, bytes | None],
    evidence: SourceEvidenceView,
    *,
    ledger_generation: str | None,
    snapshots: tuple[ManagedMarkdownFile, ...] | None = None,
) -> NativeCompilation:
    """Compile a staged note overlay without reading or writing live graph files."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    base = snapshot_managed_markdown(selected) if snapshots is None else snapshots
    by_path = {item.authority: item for item in base}
    if len(overlay) > MAX_NATIVE_NOTES:
        raise NativeCompilerError(
            "native-inventory-too-large", "native note overlay exceeds the file bound"
        )
    for authority, data in sorted(overlay.items()):
        relative = _graph_relative(authority)
        if not authority.endswith(".md"):
            raise NativeCompilerError(
                "noncanonical-native-path",
                f"managed native-note paths must use the lowercase .md suffix: {authority}",
            )
        path = selected.root.joinpath(*relative.parts)
        if data is None:
            if authority not in by_path:
                raise NativeCompilerError(
                    "missing-native-note", f"cannot delete missing native note: {authority}"
                )
            del by_path[authority]
            continue
        if not isinstance(data, bytes) or len(data) > MAX_NATIVE_NOTE_BYTES:
            raise NativeCompilerError(
                "native-inventory-too-large", f"native note is too large: {authority}"
            )
        by_path[authority] = ManagedMarkdownFile(
            path=path,
            authority=authority,
            data=data,
            raw_sha256=_sha256_bytes(data),
        )
    ordered = tuple(by_path[key] for key in sorted(by_path))
    return compile_native_snapshot(
        selected,
        ordered,
        evidence,
        ledger_generation=ledger_generation,
    )


def compile_vault(vault: Vault | Path | str) -> NativeCompilation:
    """Compile one fully validated Vault without installing derived artifacts."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    snapshots = snapshot_managed_markdown(selected)
    ledger = load_source_ledger(selected)
    return compile_native_snapshot(
        selected,
        snapshots,
        current_evidence_view(ledger),
        ledger_generation=ledger.generation_sha256,
    )


def _graph_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise NativeCompilerError(
            "unsafe-native-artifact-path", "native artifact path must be non-empty"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NativeCompilerError(
            "unsafe-native-artifact-path", "native artifact path must be strict UTF-8"
        ) from error
    relative = PurePosixPath(value)
    windows_reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if (
        len(encoded) > 4096
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or relative.is_absolute()
        or relative.as_posix() != value
        or not relative.parts
        or len(relative.parts) > 64
        or any(
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].casefold() in windows_reserved
            for part in relative.parts
        )
    ):
        raise NativeCompilerError(
            "unsafe-native-artifact-path",
            f"native artifact path is not a canonical portable path: {value!r}",
        )
    return relative


def _strict_json_bytes(data: bytes, *, kind: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            data.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NativeCompilerError(
            f"invalid-{kind}", f"malformed {kind}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise NativeCompilerError(f"invalid-{kind}", f"{kind} must be a JSON object")
    return payload


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_bytes(compilation: NativeCompilation) -> dict[str, bytes]:
    return {
        name: content.encode("utf-8")
        for name, content in {
            "sources.json": compilation.source_registry_text,
            **compilation.artifacts,
        }.items()
    }


def _write_system_stage(stage: Path, expected: Mapping[str, bytes]) -> None:
    for name, content in sorted(expected.items()):
        relative = _graph_relative(name)
        path = stage.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())


def _validate_stage_layout(
    compilation: NativeCompilation,
    stage: Path,
    expected: Mapping[str, bytes],
) -> GraphState:
    for name, content in sorted(expected.items()):
        path = stage.joinpath(*_graph_relative(name).parts)
        try:
            installed = path.read_bytes()
        except OSError as error:
            raise NativeCompilerError(
                "native-stage-mismatch",
                f"cannot read staged native artifact: {name}",
            ) from error
        if installed != content:
            raise NativeCompilerError(
                "native-stage-mismatch",
                f"staged native artifact changed during validation: {name}",
            )
    try:
        load_fields(stage / "sources.json")
        load_sources(compilation.vault.root, stage / "sources.json")
        staged_state = load_state(stage)
        staged_snapshot = make_agent_snapshot(
            staged_state, namespace=compilation.vault.id
        )
        expected_snapshot = make_agent_snapshot(
            compilation.state, namespace=compilation.vault.id
        )
    except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise NativeCompilerError(
            "invalid-staged-native-graph",
            f"cannot hydrate staged native graph: {error}",
        ) from error
    registry_sha256 = sha256_text(json_text(compilation.source_registry))
    if staged_state.manifest.get("registry_sha256") != registry_sha256:
        raise NativeCompilerError(
            "invalid-staged-native-sources",
            "staged sources registry does not match the graph manifest",
        )
    if (
        staged_state.manifest.get("graph_sha256")
        != compilation.state.manifest.get("graph_sha256")
        or staged_snapshot != expected_snapshot
    ):
        raise NativeCompilerError(
            "native-stage-hydration-mismatch",
            "hydrated staged native graph differs from the in-memory compilation",
        )
    return staged_state


def _stage_and_validate(compilation: NativeCompilation) -> None:
    expected = _expected_bytes(compilation)
    with tempfile.TemporaryDirectory(prefix="kgdistiller-native-check-") as temporary:
        stage = Path(temporary)
        _write_system_stage(stage, expected)
        _validate_stage_layout(compilation, stage, expected)


def validate_native_compilation(compilation: NativeCompilation) -> None:
    """Validate deterministic graph artifacts entirely in memory."""

    expected = _expected_bytes(compilation)
    if len(expected) > MAX_GRAPH_ARTIFACTS or sum(map(len, expected.values())) > MAX_NATIVE_GRAPH_BYTES:
        raise NativeCompilerError(
            "native-graph-too-large", "compiled native graph exceeds in-memory validation bounds"
        )
    for name, data in expected.items():
        limit = _native_graph_artifact_limit(name)
        if len(data) > limit:
            raise NativeCompilerError(
                "native-artifact-too-large",
                f"compiled native artifact exceeds its publication bound: {name}",
            )
    diagnostics = validate_state(compilation.state)
    if diagnostics != compilation.diagnostics or diagnostics["errors"]:
        raise NativeCompilerError(
            "invalid-native-graph", "compiled native graph diagnostics are inconsistent"
        )
    registry_text = pretty_json(compilation.source_registry)
    if registry_text != compilation.source_registry_text:
        raise NativeCompilerError(
            "invalid-native-sources", "compiled native source registry is not canonical"
        )
    registry_sha256 = sha256_text(json_text(compilation.source_registry))
    source_hashes = dict(compilation.state.manifest.get("source_hashes") or {})
    rebuilt = make_artifacts(
        compilation.state,
        source_hashes,
        registry_sha256=registry_sha256,
    )
    if rebuilt != compilation.artifacts:
        raise NativeCompilerError(
            "native-compilation-mismatch",
            "in-memory native graph does not reproduce its canonical artifacts",
        )
    for name, content in compilation.artifacts.items():
        _graph_relative(name)
        try:
            content.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise NativeCompilerError(
                "invalid-native-artifact", f"native artifact is not strict UTF-8: {name}"
            ) from error


def _allocate_build_stage(vault: Vault) -> Path:
    build = vault.root / ".kgdistiller" / "build"
    with _PinnedDirectory(build) as parent:
        for _ in range(32):
            name = f".stage-knowledge-{uuid.uuid4().hex}"
            try:
                parent.mkdir_leaf(name)
            except FileExistsError:
                continue
            if os.name != "nt":
                os.fsync(parent.dir_fd)
            return build / name
    raise NativeCompilerError(
        "native-stage-name-exhausted", "cannot allocate a native graph stage"
    )


def _write_vault_stage(
    compilation: NativeCompilation,
    stage: Path,
    expected: Mapping[str, bytes],
) -> None:
    stage_relative = stage.relative_to(compilation.vault.root).as_posix()
    for name, content in sorted(expected.items()):
        replace_vault_relative_regular(
            compilation.vault,
            f"{stage_relative}/{_graph_relative(name).as_posix()}",
            content,
            maximum=_native_graph_artifact_limit(name),
        )


def _read_vault_stage(
    vault: Vault,
    stage: Path,
    expected: Mapping[str, bytes],
) -> dict[str, bytes]:
    stage_relative = stage.relative_to(vault.root).as_posix()
    captured: dict[str, bytes] = {}
    for name, content in sorted(expected.items()):
        data = read_vault_relative_regular(
            vault,
            f"{stage_relative}/{_graph_relative(name).as_posix()}",
            maximum=max(1, len(content)),
        )
        if data != content:
            raise NativeCompilerError(
                "native-stage-mismatch",
                f"staged native artifact changed during validation: {name}",
            )
        captured[name] = data
    return captured


def _prepare_vault_stage(compilation: NativeCompilation) -> Path:
    expected = _expected_bytes(compilation)
    stage = _allocate_build_stage(compilation.vault)
    try:
        _write_vault_stage(compilation, stage, expected)
        captured = _read_vault_stage(compilation.vault, stage, expected)
        with tempfile.TemporaryDirectory(prefix="kgdistiller-native-stage-") as temporary:
            hydrated = Path(temporary)
            _write_system_stage(hydrated, captured)
            _validate_stage_layout(compilation, hydrated, captured)
        _native_transaction_hook("after-stage", "")
        return stage
    except BaseException:
        _remove_stage(stage, stage.parent)
        raise


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _discover_tree_files(root: Path) -> tuple[str, ...]:
    if not os.path.lexists(root):
        return ()
    files: list[str] = []
    entries_seen = 0

    def visit(directory: Path, prefix: PurePosixPath | None) -> None:
        nonlocal entries_seen
        try:
            pinned = _PinnedDirectory(directory)
        except (OSError, SourceArchiveError) as error:
            raise NativeCompilerError(
                "unsafe-native-graph", "cannot anchor native graph directory"
            ) from error
        with pinned:
            try:
                names: list[str] = []
                with os.scandir(
                    directory if os.name == "nt" else pinned.dir_fd
                ) as scanner:
                    for entry in scanner:
                        entries_seen += 1
                        if entries_seen > MAX_GRAPH_ARTIFACTS:
                            raise NativeCompilerError(
                                "native-graph-too-large",
                                f"native graph exceeds {MAX_GRAPH_ARTIFACTS} entries",
                            )
                        names.append(entry.name)
            except OSError as error:
                raise NativeCompilerError(
                    "unsafe-native-graph", "cannot enumerate native graph directory"
                ) from error
            for name in sorted(names):
                relative = PurePosixPath(name) if prefix is None else prefix / name
                _graph_relative(relative.as_posix())
                metadata = pinned.lstat_leaf(name)
                if metadata is None:
                    raise NativeCompilerError(
                        "unstable-native-graph",
                        "native graph changed during inventory",
                    )
                path = directory / name
                if stat.S_ISDIR(metadata.st_mode) and not _is_reparse(metadata):
                    visit(path, relative)
                    continue
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or _is_reparse(metadata)
                    or metadata.st_nlink != 1
                ):
                    raise NativeCompilerError(
                        "unsafe-native-graph",
                        f"native graph contains a non-ordinary entry: {relative.as_posix()}",
                    )
                files.append(relative.as_posix())
                if len(files) > MAX_GRAPH_ARTIFACTS:
                    raise NativeCompilerError(
                        "native-graph-too-large",
                        f"native graph exceeds {MAX_GRAPH_ARTIFACTS} artifacts",
                    )
            pinned.verify_current()

    visit(root, None)
    return tuple(sorted(files))


def _capture_live_graph_once(vault: Vault) -> dict[str, bytes]:
    paths = _discover_tree_files(vault.root / ".kgdistiller" / "graph")
    captured: dict[str, bytes] = {}
    total = 0
    for name in paths:
        data = read_vault_relative_regular(
            vault,
            f".kgdistiller/graph/{name}",
            maximum=_native_graph_artifact_limit(name),
        )
        total += len(data)
        if total > MAX_NATIVE_GRAPH_BYTES:
            raise NativeCompilerError(
                "native-graph-too-large",
                f"native graph exceeds {MAX_NATIVE_GRAPH_BYTES} bytes",
            )
        captured[name] = data
    return captured


def _capture_live_graph(vault: Vault) -> dict[str, bytes]:
    first = _capture_live_graph_once(vault)
    second = _capture_live_graph_once(vault)
    first_token = tuple((name, _sha256_bytes(data)) for name, data in sorted(first.items()))
    second_token = tuple((name, _sha256_bytes(data)) for name, data in sorted(second.items()))
    if first_token != second_token:
        raise NativeCompilerError(
            "unstable-native-graph", "native graph paths or bytes changed during capture"
        )
    return first


def _manifest_artifact_names(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    if manifest.get("schema") != GRAPH_SCHEMA:
        raise NativeCompilerError(
            "invalid-native-graph-manifest",
            f"expected {GRAPH_SCHEMA} native graph manifest",
        )
    names = {
        "manifest.json",
        "sources.json",
        "nodes.jsonl",
        "edges.jsonl",
        "references.jsonl",
        "diagnostics.json",
    }
    entry_store = manifest.get("entry_store") or {}
    if not isinstance(entry_store, dict):
        raise NativeCompilerError(
            "invalid-native-graph-manifest", "entry_store must be an object"
        )
    shards = entry_store.get("shards") or []
    if not isinstance(shards, list) or len(shards) > MAX_GRAPH_ARTIFACTS - len(names):
        raise NativeCompilerError(
            "invalid-native-graph-manifest", "entry shard inventory exceeds graph bounds"
        )
    for row in shards:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise NativeCompilerError(
                "invalid-native-graph-manifest", "entry shard record is invalid"
            )
        path = _graph_relative(str(row["path"])).as_posix()
        if not path.startswith("entries/"):
            raise NativeCompilerError(
                "invalid-native-graph-manifest", "entry shard lies outside entries/"
            )
        if path in names:
            raise NativeCompilerError(
                "invalid-native-graph-manifest", "entry shard path occurs more than once"
            )
        names.add(path)
    return tuple(sorted(names))


def _load_live_state_locked(
    vault: Vault,
    *,
    maximum_total_bytes: int | None = None,
    maximum_counts: Mapping[str, int] | None = None,
    usage: dict[str, Any] | None = None,
) -> tuple[GraphState, dict[str, Any], str]:
    total_limit = (
        MAX_NATIVE_GRAPH_BYTES
        if maximum_total_bytes is None
        else min(MAX_NATIVE_GRAPH_BYTES, maximum_total_bytes)
    )
    budget_code = (
        "native-graph-too-large"
        if maximum_total_bytes is None
        else "federation-graph-budget-exceeded"
    )
    budget_message = (
        f"native graph exceeds {MAX_NATIVE_GRAPH_BYTES} bytes"
        if maximum_total_bytes is None
        else "native graph exceeds the remaining federation graph budget"
    )
    if total_limit <= 0:
        raise NativeCompilerError(
            budget_code,
            budget_message,
        )
    try:
        before_bytes = read_vault_relative_regular(
            vault,
            ".kgdistiller/graph/manifest.json",
            maximum=min(
                _native_graph_artifact_limit("manifest.json"), total_limit
            ),
        )
        manifest = _strict_json_bytes(before_bytes, kind="native-graph-manifest")
        if maximum_counts is not None:
            counts = manifest.get("counts")
            if not isinstance(counts, dict):
                raise NativeCompilerError(
                    "invalid-native-graph-manifest",
                    "native graph manifest counts are invalid",
                )
            for name in ("nodes", "edges", "references"):
                value = counts.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise NativeCompilerError(
                        "invalid-native-graph-manifest",
                        "native graph manifest counts are invalid",
                    )
                if value > maximum_counts.get(name, value):
                    raise NativeCompilerError(
                        "federation-graph-budget-exceeded",
                        "native graph exceeds the remaining federation count budget",
                    )
        _native_reader_hook("after-manifest", "manifest.json")
        names = _manifest_artifact_names(manifest)
        shard_inventory = {
            str(row["path"]): row
            for row in ((manifest.get("entry_store") or {}).get("shards") or [])
        }
        captured: dict[str, bytes] = {"manifest.json": before_bytes}
        total = len(before_bytes)
        for name in names:
            if name == "manifest.json":
                continue
            remaining = total_limit - total
            if remaining <= 0:
                raise NativeCompilerError(
                    budget_code,
                    budget_message,
                )
            role_limit = _native_graph_artifact_limit(name)
            declared = shard_inventory.get(name)
            if declared is not None:
                declared_bytes = declared.get("bytes")
                if (
                    isinstance(declared_bytes, bool)
                    or not isinstance(declared_bytes, int)
                    or declared_bytes < 0
                ):
                    raise NativeCompilerError(
                        "invalid-native-graph-manifest",
                        "entry shard byte inventory is invalid",
                    )
                role_limit = min(role_limit, declared_bytes)
            data = read_vault_relative_regular(
                vault,
                f".kgdistiller/graph/{name}",
                maximum=min(role_limit, remaining),
            )
            if declared is not None and len(data) != int(declared["bytes"]):
                raise NativeCompilerError(
                    "invalid-native-graph-manifest",
                    "entry shard byte inventory does not match its artifact",
                )
            total += len(data)
            if total > total_limit:
                raise NativeCompilerError(
                    budget_code,
                    budget_message,
                )
            captured[name] = data
            _native_reader_hook("after-artifact", name)
        _native_reader_hook("before-manifest-recheck", "manifest.json")
        after_bytes = read_vault_relative_regular(
            vault,
            ".kgdistiller/graph/manifest.json",
            maximum=_native_graph_artifact_limit("manifest.json"),
        )
    except SourceArchiveError as error:
        raise NativeCompilerError(
            "invalid-native-graph", f"cannot read native graph safely: {error.message}"
        ) from error
    if before_bytes != after_bytes:
        raise NativeCompilerError(
            "stale-native-graph", "native graph manifest changed while loading"
        )
    with tempfile.TemporaryDirectory(prefix="kgdistiller-native-read-") as temporary:
        stage = Path(temporary)
        _write_system_stage(stage, captured)
        try:
            fields = load_fields(stage / "sources.json")
            sources = load_sources(vault.root, stage / "sources.json")
            if not fields and manifest.get("counts", {}).get("nodes", 0):
                raise KnowledgeError("native sources registry has no field definitions")
            if len(sources) != 3:
                raise KnowledgeError("native sources registry must contain three roots")
            state = load_state(stage)
            make_agent_snapshot(state, namespace=vault.id)
        except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise NativeCompilerError(
                "invalid-native-graph", f"cannot hydrate native graph: {error}"
            ) from error
    registry = _strict_json_bytes(captured["sources.json"], kind="native-sources")
    if captured["sources.json"] != pretty_json(registry).encode("utf-8"):
        raise NativeCompilerError(
            "invalid-native-graph", "native sources registry is not canonical"
        )
    if sha256_text(json_text(registry)) != state.manifest.get("registry_sha256"):
        raise NativeCompilerError(
            "invalid-native-graph", "native sources registry hash does not match manifest"
        )
    if usage is not None:
        usage.clear()
        usage.update(
            {
                "bytes": total,
                "nodes": int(manifest.get("counts", {}).get("nodes", 0)),
                "edges": int(manifest.get("counts", {}).get("edges", 0)),
                "references": int(
                    manifest.get("counts", {}).get("references", 0)
                ),
            }
        )
    rebuilt = make_artifacts(
        state,
        dict(state.manifest.get("source_hashes") or {}),
        registry_sha256=str(state.manifest.get("registry_sha256", "")) or None,
        identity_sha256=str(state.manifest.get("identity_sha256", "")) or None,
        git_revision=str(state.manifest.get("git_revision", "")) or None,
    )
    rebuilt_bytes = {
        name: content.encode("utf-8") for name, content in rebuilt.items()
    }
    graph_bytes = {
        name: data for name, data in captured.items() if name != "sources.json"
    }
    if rebuilt_bytes != graph_bytes:
        raise NativeCompilerError(
            "invalid-native-graph",
            "native graph artifacts are not the canonical hydrated generation",
        )
    return state, manifest, _sha256_bytes(before_bytes)


def _installed_mismatches(compilation: NativeCompilation) -> list[str]:
    expected = _expected_bytes(compilation)
    installed = _capture_live_graph(compilation.vault)
    return sorted(
        name
        for name in set(expected) | set(installed)
        if expected.get(name) != installed.get(name)
    )


def _native_transaction_hook(label: str, path: str) -> None:
    return None


def _native_reader_hook(label: str, path: str) -> None:
    return None


def _file_records(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {
            "path": _graph_relative(name).as_posix(),
            "bytes": len(data),
            "sha256": _sha256_bytes(data),
        }
        for name, data in sorted(files.items())
    ]


def _records_map(records: Any, *, field: str) -> dict[str, tuple[int, str]]:
    if not isinstance(records, list) or len(records) > MAX_GRAPH_ARTIFACTS:
        raise NativeCompilerError(
            "invalid-graph-transaction", f"{field} exceeds the artifact bound"
        )
    result: dict[str, tuple[int, str]] = {}
    ordered_paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise NativeCompilerError(
                "invalid-graph-transaction", f"{field} contains an invalid record"
            )
        path = _graph_relative(record.get("path")).as_posix()
        size = record.get("bytes")
        digest = record.get("sha256")
        if (
            path in result
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > _native_graph_artifact_limit(path)
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
        ):
            raise NativeCompilerError(
                "invalid-graph-transaction", f"{field} contains invalid metadata"
            )
        result[path] = (size, digest)
        ordered_paths.append(path)
    if ordered_paths != sorted(ordered_paths):
        raise NativeCompilerError(
            "invalid-graph-transaction", f"{field} must be sorted by path"
        )
    return result


def _validate_transaction(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "state",
        "vault_id",
        "stage",
        "backup",
        "registry_generation",
        "vault_manifest_sha256",
        "authority_token_sha256",
        "ledger_generation_sha256",
        "old_manifest_sha256",
        "new_manifest_sha256",
        "old_files",
        "new_files",
    }
    if set(payload) != required or payload.get("schema") != GRAPH_TRANSACTION_SCHEMA:
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction has unsupported fields"
        )
    if payload.get("state") not in {"prepared", "rolling-back", "committed"}:
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction state is invalid"
        )
    vault_id = payload.get("vault_id")
    if (
        not isinstance(vault_id, str)
        or not VAULT_ID_RE.fullmatch(vault_id)
        or len(vault_id.encode("utf-8")) > MAX_ID_BYTES
    ):
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction Vault ID is invalid"
        )
    stage = payload.get("stage")
    backup = payload.get("backup")
    if (
        not isinstance(stage, str)
        or not _STAGE_NAME_RE.fullmatch(stage)
        or not isinstance(backup, str)
        or not _STAGE_NAME_RE.fullmatch(backup)
        or stage == backup
    ):
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction stage names are invalid"
        )
    for field in (
        "registry_generation",
        "vault_manifest_sha256",
        "authority_token_sha256",
        "ledger_generation_sha256",
        "old_manifest_sha256",
        "new_manifest_sha256",
    ):
        value = payload.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise NativeCompilerError(
                "invalid-graph-transaction", f"graph transaction {field} is invalid"
            )
    old_records = _records_map(payload.get("old_files"), field="old_files")
    new_records = _records_map(payload.get("new_files"), field="new_files")
    old_manifest = old_records.get("manifest.json")
    expected_old = (
        old_manifest[1] if old_manifest is not None else _MISSING_MANIFEST_SHA256
    )
    if payload["old_manifest_sha256"] != expected_old:
        raise NativeCompilerError(
            "invalid-graph-transaction", "old manifest token does not match old_files"
        )
    new_manifest = new_records.get("manifest.json")
    if new_manifest is None or payload["new_manifest_sha256"] != new_manifest[1]:
        raise NativeCompilerError(
            "invalid-graph-transaction", "new manifest token does not match new_files"
        )
    return payload


def _transaction_bytes(payload: dict[str, Any]) -> bytes:
    _validate_transaction(payload)
    data = json_text(payload).encode("utf-8")
    if len(data) > MAX_GRAPH_TRANSACTION_BYTES:
        raise NativeCompilerError(
            "graph-transaction-too-large",
            f"graph transaction exceeds {MAX_GRAPH_TRANSACTION_BYTES} bytes",
        )
    return data


def _write_transaction(vault: Vault, payload: dict[str, Any]) -> None:
    replace_vault_relative_regular(
        vault,
        GRAPH_TRANSACTION_PATH,
        _transaction_bytes(payload),
        maximum=MAX_GRAPH_TRANSACTION_BYTES,
    )


def _read_transaction(vault: Vault) -> dict[str, Any] | None:
    try:
        data = read_vault_relative_regular(
            vault,
            GRAPH_TRANSACTION_PATH,
            maximum=MAX_GRAPH_TRANSACTION_BYTES,
        )
    except SourceArchiveError as error:
        if error.code == "missing-vault-file":
            return None
        raise NativeCompilerError(
            "invalid-graph-transaction", f"cannot read graph transaction: {error.message}"
        ) from error
    payload = _strict_json_bytes(data, kind="graph-transaction")
    _validate_transaction(payload)
    if data != json_text(payload).encode("utf-8"):
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction is not canonical JSON"
        )
    return payload


def _write_backup(vault: Vault, backup: Path, files: Mapping[str, bytes]) -> None:
    backup_relative = backup.relative_to(vault.root).as_posix()
    for name, data in sorted(files.items()):
        replace_vault_relative_regular(
            vault,
            f"{backup_relative}/{_graph_relative(name).as_posix()}",
            data,
            maximum=_native_graph_artifact_limit(name),
        )


def _load_recorded_files(
    vault: Vault,
    root_name: str,
    records: Any,
) -> dict[str, bytes]:
    metadata = _records_map(records, field="transaction files")
    result: dict[str, bytes] = {}
    total = 0
    for name, (size, digest) in sorted(metadata.items()):
        data = read_vault_relative_regular(
            vault,
            f".kgdistiller/build/{root_name}/{name}",
            maximum=max(1, size),
        )
        if len(data) != size or _sha256_bytes(data) != digest:
            raise NativeCompilerError(
                "invalid-graph-transaction", f"transaction file changed: {name}"
            )
        total += len(data)
        if total > MAX_NATIVE_GRAPH_BYTES:
            raise NativeCompilerError(
                "graph-transaction-too-large", "transaction files exceed graph bounds"
            )
        result[name] = data
    return result


def _write_live_file(vault: Vault, name: str, data: bytes) -> None:
    replace_vault_relative_regular(
        vault,
        f".kgdistiller/graph/{_graph_relative(name).as_posix()}",
        data,
        maximum=_native_graph_artifact_limit(name),
    )


def _remove_live_file(vault: Vault, name: str) -> None:
    unlink_vault_relative_regular(
        vault, f".kgdistiller/graph/{_graph_relative(name).as_posix()}"
    )


def _apply_new_graph(
    vault: Vault,
    old_files: Mapping[str, bytes],
    new_files: Mapping[str, bytes],
) -> None:
    for name in sorted(set(old_files) - set(new_files) - {"manifest.json"}):
        _native_transaction_hook("before-live-delete", name)
        _remove_live_file(vault, name)
        _native_transaction_hook("after-live-delete", name)
    for name in sorted(set(new_files) - {"manifest.json"}):
        _native_transaction_hook("before-live-write", name)
        _write_live_file(vault, name, new_files[name])
        _native_transaction_hook("after-live-write", name)
    _native_transaction_hook("before-manifest", "manifest.json")
    _write_live_file(vault, "manifest.json", new_files["manifest.json"])
    _native_transaction_hook("after-manifest", "manifest.json")


def _restore_old_graph(
    vault: Vault,
    old_files: Mapping[str, bytes],
) -> None:
    _native_transaction_hook("rollback-before", "")
    current = _capture_live_graph(vault)
    if "manifest.json" in current:
        _remove_live_file(vault, "manifest.json")
        _native_transaction_hook("rollback-after-manifest-remove", "manifest.json")
    for name in sorted(set(current) - set(old_files) - {"manifest.json"}):
        _remove_live_file(vault, name)
        _native_transaction_hook("rollback-after-remove", name)
    for name in sorted(set(old_files) - {"manifest.json"}):
        _write_live_file(vault, name, old_files[name])
        _native_transaction_hook("rollback-after-restore", name)
    if "manifest.json" in old_files:
        _write_live_file(vault, "manifest.json", old_files["manifest.json"])
    restored = _capture_live_graph(vault)
    if restored != dict(old_files):
        raise NativeCompilerError(
            "native-rollback-mismatch", "native graph rollback did not restore exact bytes"
        )
    _native_transaction_hook("rollback-after", "")


def _cleanup_transaction(vault: Vault, payload: Mapping[str, Any]) -> None:
    build = vault.root / ".kgdistiller" / "build"
    try:
        unlink_vault_relative_regular(vault, GRAPH_TRANSACTION_PATH)
    except (OSError, SourceArchiveError):
        return
    _native_transaction_hook("after-journal-clear", "")
    for field in ("stage", "backup"):
        name = str(payload[field])
        if _STAGE_NAME_RE.fullmatch(name):
            _remove_stage(build / name, build)


def _reachable_transaction_state(
    current: Mapping[str, bytes],
    payload: Mapping[str, Any],
) -> bool:
    old_records = _records_map(payload["old_files"], field="old_files")
    new_records = _records_map(payload["new_files"], field="new_files")
    touched = set(old_records) | set(new_records)
    if set(current) - touched:
        return False
    current_file_records = _file_records(current)
    if current_file_records in (payload["old_files"], payload["new_files"]):
        return True
    current_records = {
        name: (len(data), _sha256_bytes(data)) for name, data in current.items()
    }
    for name in touched:
        value = current_records.get(name)
        allowed = {
            record
            for record in (old_records.get(name), new_records.get(name))
            if record is not None
        }
        if value is None:
            if (
                payload["state"] != "rolling-back"
                and name in old_records
                and name in new_records
            ):
                return False
        elif value not in allowed:
            return False
    old_manifest = old_records.get("manifest.json")
    new_manifest = new_records["manifest.json"]
    if (
        old_manifest != new_manifest
        and current_records.get("manifest.json") == new_manifest
    ):
        return current_file_records == payload["new_files"]
    return True


def _recover_graph_transaction_locked(vault: Vault) -> None:
    payload = _read_transaction(vault)
    if payload is None:
        return
    if payload["vault_id"] != vault.id:
        raise NativeCompilerError(
            "invalid-graph-transaction", "graph transaction belongs to another Vault"
        )
    if payload["state"] in {"prepared", "rolling-back"}:
        current = _capture_live_graph(vault)
        if _file_records(current) == payload["old_files"]:
            _cleanup_transaction(vault, payload)
            return
        if not _reachable_transaction_state(current, payload):
            raise NativeCompilerError(
                "stale-graph-transaction",
                "prepared graph transaction does not match the live old/new state",
            )
        if payload["state"] == "prepared":
            payload = dict(payload)
            payload["state"] = "rolling-back"
            _write_transaction(vault, payload)
        old_files = _load_recorded_files(vault, payload["backup"], payload["old_files"])
        _restore_old_graph(vault, old_files)
        _cleanup_transaction(vault, payload)
        return
    expected = _records_map(payload["new_files"], field="new_files")
    current = _capture_live_graph(vault)
    if _file_records(current) != payload["new_files"]:
        raise NativeCompilerError(
            "committed-native-graph-mismatch",
            "committed native graph no longer matches its transaction record",
        )
    state, _, manifest_sha256 = _load_live_state_locked(vault)
    if (
        manifest_sha256 != payload["new_manifest_sha256"]
        or state.manifest.get("graph_sha256") is None
        or set(expected) != set(current)
    ):
        raise NativeCompilerError(
            "committed-native-graph-mismatch",
            "committed native graph cannot be verified",
        )
    _cleanup_transaction(vault, payload)


def _recover_native_transactions_locked(vault: Vault) -> None:
    """Recover F3 then F4 while the caller holds the one Vault writer guard."""

    _recover_graph_transaction_locked(vault)
    # Lazy import avoids the native_compiler -> vault_ingest -> native_compiler
    # module cycle while keeping the recovery order in one callable seam.
    from .vault_ingest import VaultIngestError, _recover_locked

    try:
        _recover_locked(vault)
    except VaultIngestError as error:
        raise NativeCompilerError(error.code, error.message) from error


def _authority_token_sha256(token: ManagedMarkdownToken) -> str:
    return sha256_json([[path, digest] for path, digest in token])


def _assert_authority_current(compilation: NativeCompilation) -> None:
    try:
        current = managed_markdown_token(snapshot_managed_markdown(compilation.vault))
        ledger = load_source_ledger(compilation.vault)
    except (VaultError, SourceArchiveError, OSError, UnicodeError, ValueError) as error:
        raise NativeCompilerError(
            "stale-native-authority",
            "native notes or evidence ledger changed during graph compilation",
        ) from error
    if (
        current != compilation.authority_token
        or ledger.generation_sha256 != compilation.ledger_generation
    ):
        raise NativeCompilerError(
            "stale-native-authority",
            "native notes or evidence ledger changed during graph compilation",
        )


def _install(
    compilation: NativeCompilation,
    stage: Path,
    *,
    expected_generation: str,
    final_check: Any,
) -> None:
    expected = _expected_bytes(compilation)
    new_files = _read_vault_stage(compilation.vault, stage, expected)
    old_files = _capture_live_graph(compilation.vault)
    backup = _allocate_build_stage(compilation.vault)
    payload: dict[str, Any] | None = None
    committed = False
    try:
        old_records = _file_records(old_files)
        new_records = _file_records(new_files)
        _write_backup(compilation.vault, backup, old_files)
        if _load_recorded_files(compilation.vault, backup.name, old_records) != old_files:
            raise NativeCompilerError(
                "invalid-graph-transaction", "graph before-image backup changed"
            )
        payload = {
            "schema": GRAPH_TRANSACTION_SCHEMA,
            "state": "prepared",
            "vault_id": compilation.vault.id,
            "stage": stage.name,
            "backup": backup.name,
            "registry_generation": expected_generation,
            "vault_manifest_sha256": sha256_json(compilation.vault.manifest),
            "authority_token_sha256": _authority_token_sha256(
                compilation.authority_token
            ),
            "ledger_generation_sha256": (
                compilation.ledger_generation or _EMPTY_LEDGER_SHA256
            ),
            "old_manifest_sha256": (
                _sha256_bytes(old_files["manifest.json"])
                if "manifest.json" in old_files
                else _MISSING_MANIFEST_SHA256
            ),
            "new_manifest_sha256": _sha256_bytes(new_files["manifest.json"]),
            "old_files": old_records,
            "new_files": new_records,
        }
        _write_transaction(compilation.vault, payload)
        _native_transaction_hook("after-journal", "")
        final_check()
        _native_transaction_hook("after-final-preconditions", "")
        final_check()
        _apply_new_graph(compilation.vault, old_files, new_files)
        _native_transaction_hook("before-final-verify", "")
        installed = _capture_live_graph(compilation.vault)
        if installed != new_files:
            raise NativeCompilerError(
                "native-install-mismatch",
                "installed native graph differs from the validated stage",
            )
        state, _, manifest_sha256 = _load_live_state_locked(compilation.vault)
        if (
            manifest_sha256 != payload["new_manifest_sha256"]
            or state.manifest.get("graph_sha256")
            != compilation.state.manifest.get("graph_sha256")
        ):
            raise NativeCompilerError(
                "native-install-mismatch",
                "installed native graph failed final hydration",
            )
        _native_transaction_hook("after-final-verify", "")
        final_check()
        _native_transaction_hook("before-commit", "")
        final_check()
        payload = dict(payload)
        payload["state"] = "committed"
        _write_transaction(compilation.vault, payload)
        committed = True
        _native_transaction_hook("after-commit", "")
    except BaseException as error:
        if not committed and payload is not None:
            try:
                _native_transaction_hook("before-rollback-state", "")
                payload = dict(payload)
                payload["state"] = "rolling-back"
                _write_transaction(compilation.vault, payload)
                _native_transaction_hook("after-rollback-state", "")
                _restore_old_graph(compilation.vault, old_files)
                _cleanup_transaction(compilation.vault, payload)
            except BaseException as rollback_error:
                raise NativeCompilerError(
                    "native-rollback-failed",
                    "native graph publication failed and exact rollback could not complete",
                    details={
                        "publication_error": str(error),
                        "rollback_error": str(rollback_error),
                    },
                ) from rollback_error
        elif payload is None:
            _remove_stage(backup, backup.parent)
        raise
    if payload is not None:
        _cleanup_transaction(compilation.vault, payload)


def _vault_result(
    compilation: NativeCompilation,
    *,
    changed: bool,
    mismatches: list[str],
) -> dict[str, Any]:
    counts = dict(compilation.state.manifest["counts"])
    return {
        "id": compilation.vault.id,
        "status": "drift" if mismatches else "current",
        "changed": changed,
        "graph_sha256": compilation.state.manifest["graph_sha256"],
        "ledger_generation": compilation.ledger_generation,
        "counts": counts,
        "warnings": len(compilation.diagnostics["warnings"]),
        "needs_review": {
            "nodes": sum(
                (node.get("properties") or {}).get("curation_status") == "needs-review"
                for node in compilation.state.nodes.values()
                if node.get("type") == "knowledge"
            ),
            "edges": sum(
                edge.get("curation_status") == "needs-review"
                for edge in compilation.state.edges.values()
                if edge.get("relation") != "contains"
            ),
        },
        "mismatches": mismatches[:64],
    }


def _select_vaults(
    vault_id: str | None,
    *,
    home: Path | str | None,
) -> tuple[str, tuple[Vault, ...]]:
    if (
        vault_id is not None
        and (
            not VAULT_ID_RE.fullmatch(vault_id)
            or len(vault_id.encode("utf-8")) > MAX_ID_BYTES
        )
    ):
        raise NativeCompilerError(
            "invalid-vault-id", "--vault must be a bounded lowercase Vault ID"
        )
    registry = load_registry(home)
    if vault_id is not None:
        selected = tuple(vault for vault in registry.vaults if vault.id == vault_id)
    else:
        selected = tuple(sorted(registry.vaults, key=lambda vault: vault.id))
    if vault_id is not None and not selected:
        raise NativeCompilerError(
            "vault-not-registered", f"Vault is not registered: {vault_id}"
        )
    if not selected:
        raise NativeCompilerError(
            "no-registered-vaults", "no registered Vault is available for knowledge compilation"
        )
    return registry.generation, selected


def _assert_selection_current(
    expected_generation: str,
    expected_vault: Vault,
    *,
    home: Path | str | None,
) -> Vault:
    """Revalidate the machine-local route without holding its registry lock."""

    try:
        registry = load_registry(home)
    except (VaultError, OSError, UnicodeError, ValueError) as error:
        raise NativeCompilerError(
            "stale-vault-selection",
            "Vault registry route or portable manifest changed during knowledge operation",
            details={"vault_id": expected_vault.id},
        ) from error
    matches = tuple(vault for vault in registry.vaults if vault.id == expected_vault.id)
    if (
        registry.generation != expected_generation
        or len(matches) != 1
        or matches[0].root != expected_vault.root
        or sha256_json(matches[0].manifest) != sha256_json(expected_vault.manifest)
    ):
        raise NativeCompilerError(
            "stale-vault-selection",
            "Vault registry route or portable manifest changed during knowledge operation",
            details={"vault_id": expected_vault.id},
        )
    return matches[0]


@contextlib.contextmanager
def _native_generation_guard(
    vault: Vault,
    *,
    max_attempts: int = 100,
) -> Iterator[None]:
    last_error: SourceArchiveError | None = None
    for attempt in range(max_attempts):
        guard = vault_generation_guard(vault)
        try:
            guard.__enter__()
        except SourceArchiveError as error:
            if error.code != "vault-writer-lock-conflict":
                raise
            last_error = error
        else:
            try:
                yield
            finally:
                guard.__exit__(None, None, None)
            return
        if attempt + 1 < max_attempts:
            time.sleep(0.01)
    assert last_error is not None
    raise last_error


def _recover_before_compile(
    expected_generation: str,
    vault: Vault,
    *,
    home: Path | str | None,
) -> Vault:
    """Recover pending native transactions before reading authority inputs."""

    with _native_generation_guard(vault):
        _recover_native_transactions_locked(vault)
        with vault_registry_lock(home):
            current = _assert_selection_current(
                expected_generation, vault, home=home
            )
            return current


def sync_knowledge(
    vault_id: str | None = None,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Stage, validate, and install deterministic native graphs."""

    generation, vaults = _select_vaults(vault_id, home=home)
    results: list[dict[str, Any]] = []
    for vault in vaults:
        vault = _recover_before_compile(generation, vault, home=home)
        compilation = compile_vault(vault)
        stage = _prepare_vault_stage(compilation)
        try:
            with _native_generation_guard(compilation.vault):
                with vault_registry_lock(home):
                    current = _assert_selection_current(
                        generation, compilation.vault, home=home
                    )
                    _recover_native_transactions_locked(current)
                _assert_authority_current(compilation)
                mismatches = _installed_mismatches(compilation)
                if mismatches:
                    with vault_registry_lock(home):
                        def final_check() -> None:
                            selected = _assert_selection_current(
                                generation, compilation.vault, home=home
                            )
                            if (
                                selected.root != compilation.vault.root
                                or sha256_json(selected.manifest)
                                != sha256_json(compilation.vault.manifest)
                            ):
                                raise NativeCompilerError(
                                    "stale-vault-selection",
                                    "Vault route or manifest changed during publication",
                                )
                            _assert_authority_current(compilation)

                        final_check()
                        _install(
                            compilation,
                            stage,
                            expected_generation=generation,
                            final_check=final_check,
                        )
                else:
                    _assert_selection_current(
                        generation, compilation.vault, home=home
                    )
                    _assert_authority_current(compilation)
        finally:
            marker_exists = os.path.lexists(
                compilation.vault.root / GRAPH_TRANSACTION_PATH
            )
            if not marker_exists:
                _remove_stage(stage, stage.parent)
        results.append(
            _vault_result(
                compilation,
                changed=bool(mismatches),
                mismatches=[],
            )
        )
    return validate_contract({
        "schema": REPORT_SCHEMA,
        "action": "sync",
        "status": "ok",
        "registry_generation": generation,
        "selection": vault_id,
        "vaults": results,
    })


def check_knowledge(
    vault_id: str | None = None,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Rebuild and compare every installed native artifact byte-for-byte."""

    generation, vaults = _select_vaults(vault_id, home=home)
    results: list[dict[str, Any]] = []
    for vault in vaults:
        vault = _recover_before_compile(generation, vault, home=home)
        compilation = compile_vault(vault)
        _stage_and_validate(compilation)
        with _native_generation_guard(compilation.vault):
            with vault_registry_lock(home):
                current = _assert_selection_current(
                    generation, compilation.vault, home=home
                )
                _recover_native_transactions_locked(current)
            _assert_authority_current(compilation)
            mismatches = _installed_mismatches(compilation)
            _assert_selection_current(generation, compilation.vault, home=home)
            _assert_authority_current(compilation)
        results.append(
            _vault_result(
                compilation,
                changed=False,
                mismatches=mismatches,
            )
        )
    status = "failed" if any(item["status"] == "drift" for item in results) else "ok"
    return validate_contract({
        "schema": REPORT_SCHEMA,
        "action": "check",
        "status": status,
        "registry_generation": generation,
        "selection": vault_id,
        "vaults": results,
    })


__all__ = [
    "REPORT_SCHEMA",
    "NativeCompilation",
    "NativeCompilerError",
    "check_knowledge",
    "compile_native_snapshot",
    "compile_vault",
    "compile_vault_overlay",
    "sync_knowledge",
    "validate_native_compilation",
]
