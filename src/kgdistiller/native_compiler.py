"""Deterministic qlkg-v3 compilation from Obsidian-native Vault notes."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .cli import (
    GraphState,
    KnowledgeError,
    edge_key,
    json_text,
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
    SourceArchiveError,
    SourceEvidenceView,
    current_evidence_view,
    load_source_ledger,
    read_vault_relative_regular,
    replace_vault_relative_regular,
    unlink_vault_relative_regular,
    vault_staging_directory,
    vault_writer_lock,
)
from .vaults import (
    VAULT_ID_RE,
    Vault,
    VaultError,
    iter_managed_markdown,
    load_registry,
    load_vault,
)


REPORT_SCHEMA = "qlkg-knowledge-report-v1"
MAX_GRAPH_ARTIFACTS = 100_032
MAX_NATIVE_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_NATIVE_EDGES = 500_000
MAX_NATIVE_NOTES = 100_000
MAX_NATIVE_NOTE_BYTES = 512 * 1024 * 1024


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


def _native_inventory(vault: Vault) -> tuple[NativeNote, ...]:
    notes: list[NativeNote] = []
    ids: dict[str, str] = {}
    paths: dict[str, str] = {}
    total_bytes = 0
    roots = (
        (vault.concept_root, "concept"),
        (vault.field_root, "field"),
        (vault.topic_root, "topic"),
    )
    for root, expected in roots:
        for snapshot in iter_managed_markdown(vault, root):
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
    return tuple(sorted(notes, key=lambda item: item.authority))


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


def compile_vault(vault: Vault | Path | str) -> NativeCompilation:
    """Compile one fully validated Vault without installing derived artifacts."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    notes = _native_inventory(selected)
    ledger = load_source_ledger(selected)
    evidence = current_evidence_view(ledger)
    state, resolver = _build_graph(notes, evidence)
    source_registry = _source_registry(selected, notes, resolver)
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
        vault=selected,
        notes=notes,
        ledger_generation=ledger.generation_sha256,
        source_registry=source_registry,
        source_registry_text=source_registry_text,
        state=state,
        artifacts=artifacts,
        diagnostics=diagnostics,
    )


def _stage_and_validate(compilation: NativeCompilation) -> None:
    expected = {"sources.json": compilation.source_registry_text, **compilation.artifacts}
    with vault_staging_directory(compilation.vault) as stage:
        stage_root = stage.relative_to(compilation.vault.root).as_posix()
        for name, content in sorted(expected.items()):
            replace_vault_relative_regular(
                compilation.vault,
                f"{stage_root}/{name}",
                content.encode("utf-8"),
                maximum=MAX_NATIVE_ARTIFACT_BYTES,
            )
        for name, content in sorted(expected.items()):
            installed = read_vault_relative_regular(
                compilation.vault,
                f"{stage_root}/{name}",
                maximum=max(1, len(content.encode("utf-8"))),
            )
            if installed != content.encode("utf-8"):
                raise NativeCompilerError(
                    "native-stage-mismatch",
                    f"staged native artifact changed during validation: {name}",
                )
        try:
            staged_state = load_state(stage)
        except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise NativeCompilerError(
                "invalid-staged-native-graph",
                f"cannot hydrate staged native graph: {error}",
            ) from error
        diagnostics = validate_state(staged_state)
        if diagnostics["errors"]:
            raise NativeCompilerError(
                "invalid-staged-native-graph",
                "; ".join(item["message"] for item in diagnostics["errors"]),
            )
        expected_snapshot = make_agent_snapshot(
            compilation.state, namespace=compilation.vault.id
        )
        staged_snapshot = make_agent_snapshot(
            staged_state, namespace=compilation.vault.id
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


def _installed_mismatches(compilation: NativeCompilation) -> list[str]:
    expected = {"sources.json": compilation.source_registry_text, **compilation.artifacts}
    mismatches: list[str] = []
    for name, content in sorted(expected.items()):
        authority = f".kgdistiller/graph/{name}"
        encoded = content.encode("utf-8")
        try:
            installed = read_vault_relative_regular(
                compilation.vault,
                authority,
                maximum=max(1, len(encoded)),
            )
        except SourceArchiveError:
            mismatches.append(name)
            continue
        if installed != encoded:
            mismatches.append(name)
    return mismatches


def _install(compilation: NativeCompilation) -> None:
    current = load_vault(compilation.vault.root, expected_id=compilation.vault.id)
    previous_shards: set[str] = set()
    try:
        previous_manifest = json.loads(
            read_vault_relative_regular(
                current,
                ".kgdistiller/graph/manifest.json",
                maximum=64 * 1024 * 1024,
            ).decode("utf-8", errors="strict")
        )
        previous_shards = {
            str(item["path"])
            for item in ((previous_manifest.get("entry_store") or {}).get("shards") or [])
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and str(item["path"]).startswith("entries/")
        }
    except (SourceArchiveError, UnicodeError, ValueError, TypeError, AttributeError):
        previous_shards = set()
    ordered = {
        "sources.json": compilation.source_registry_text,
        **{
            name: content
            for name, content in compilation.artifacts.items()
            if name != "manifest.json"
        },
        "manifest.json": compilation.artifacts["manifest.json"],
    }
    for name, content in ordered.items():
        replace_vault_relative_regular(
            current,
            f".kgdistiller/graph/{name}",
            content.encode("utf-8"),
            maximum=MAX_NATIVE_ARTIFACT_BYTES,
        )
    current_shards = {
        name for name in compilation.artifacts if name.startswith("entries/")
    }
    for name in sorted(previous_shards - current_shards):
        unlink_vault_relative_regular(current, f".kgdistiller/graph/{name}")
    mismatches = _installed_mismatches(compilation)
    if mismatches:
        raise NativeCompilerError(
            "native-install-mismatch",
            "installed native graph does not match the validated stage",
            details={"artifacts": mismatches[:64]},
        )


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
    if vault_id is not None and not VAULT_ID_RE.fullmatch(vault_id):
        raise NativeCompilerError(
            "invalid-vault-id", "--vault must be a bounded lowercase Vault ID"
        )
    registry = load_registry(home)
    if vault_id is not None:
        selected = tuple(vault for vault in registry.vaults if vault.id == vault_id)
    else:
        selected = registry.vaults
    if vault_id is not None and not selected:
        raise NativeCompilerError(
            "vault-not-registered", f"Vault is not registered: {vault_id}"
        )
    if not selected:
        raise NativeCompilerError(
            "no-registered-vaults", "no registered Vault is available for knowledge compilation"
        )
    if vault_id is None and len(selected) != 1:
        raise NativeCompilerError(
            "vault-selection-required",
            "--vault is required when more than one Vault is registered",
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


def sync_knowledge(
    vault_id: str | None = None,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Stage, validate, and install deterministic native graphs."""

    generation, vaults = _select_vaults(vault_id, home=home)
    results: list[dict[str, Any]] = []
    for vault in vaults:
        with vault_writer_lock(vault):
            current = _assert_selection_current(
                generation, vault, home=home
            )
            compilation = compile_vault(current)
            _stage_and_validate(compilation)
            mismatches = _installed_mismatches(compilation)
            if mismatches:
                _assert_selection_current(
                    generation, compilation.vault, home=home
                )
                _install(compilation)
            _assert_selection_current(
                generation, compilation.vault, home=home
            )
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
        current = _assert_selection_current(generation, vault, home=home)
        compilation = compile_vault(current)
        _stage_and_validate(compilation)
        mismatches = _installed_mismatches(compilation)
        _assert_selection_current(generation, compilation.vault, home=home)
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
    "compile_vault",
    "sync_knowledge",
]
