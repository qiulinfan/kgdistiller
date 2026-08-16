"""Coherent multi-Vault snapshots and per-generation recall indexes.

The federation layer owns no persisted index.  It hydrates each registered
Vault under that Vault's one generation guard, then caches only immutable
in-memory navigation and lexical postings keyed by the complete Vault token.
"""

from __future__ import annotations

import copy
import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping

from .alignment import empty_alignment_set
from .cli import KnowledgeError, make_agent_snapshot
from .contracts import sha256_json
from .native_compiler import (
    NativeCompilerError,
    _load_live_state_locked,
    _recover_native_transactions_locked,
)
from .native_notes import NativeNoteError, parse_native_markdown
from .query import GraphView, normalize_text, validate_agent_snapshot
from .source_archive import (
    SourceArchiveError,
    SourceEvidenceView,
    SourceLedger,
    current_evidence_view,
    load_source_ledger_metadata,
    read_vault_relative_regular,
    vault_generation_guard,
)
from .vaults import (
    MAX_MANAGED_MARKDOWN_BYTES,
    MAX_MANAGED_MARKDOWN_FILES,
    MAX_REGISTRY_ENTRIES,
    Vault,
    VaultError,
    _discover_managed_markdown,
    load_registry,
    load_vault,
)


MAX_CAPTURE_ATTEMPTS = 3
MAX_INDEX_CACHE_ENTRIES = MAX_REGISTRY_ENTRIES
MAX_INDEX_TERMS_PER_FIELD = 4_096
MAX_QUERY_TERMS = 128
MAX_INDEX_TERM_CHARACTERS = 256
MAX_INDEX_POSTINGS = 5_000_000
MAX_INDEX_ENTRY_DEPTH = 64
MAX_INDEX_ENTRY_VALUES = 100_000
MAX_INDEX_TEXT_BYTES_PER_NODE = 8 * 1024 * 1024
MAX_INDEX_ALIAS_BYTES_PER_NODE = 1024 * 1024
MAX_INDEX_TOTAL_TEXT_BYTES = 512 * 1024 * 1024
MAX_INDEX_CACHE_WEIGHT_BYTES = 512 * 1024 * 1024
MAX_FEDERATION_GRAPH_BYTES = 512 * 1024 * 1024
MAX_FEDERATION_LEDGER_BYTES = 256 * 1024 * 1024
MAX_FEDERATION_INDEX_WEIGHT_BYTES = 512 * 1024 * 1024
MAX_FEDERATION_AUTHORITY_BYTES = 512 * 1024 * 1024
MAX_FEDERATION_AUTHORITY_FILES = 100_000
MAX_FEDERATION_RETAINED_WEIGHT_BYTES = 512 * 1024 * 1024
MAX_FEDERATION_NODES = 250_000
MAX_FEDERATION_EDGES = 1_000_000
MAX_FEDERATION_REFERENCES = 1_000_000
MAX_FEDERATION_DOCUMENTS = 100_000
MAX_FEDERATION_VERSIONS = 250_000
MAX_FEDERATION_DERIVATIONS = 250_000
_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


def _is_cjk_character(character: str) -> bool:
    # `_WORD_RE` already excludes punctuation. After NFKC, wide/full-width
    # word characters reliably cover version-supported Han (including astral
    # extensions), kana, and Hangul without maintaining brittle range tables.
    return unicodedata.east_asian_width(character) in {"W", "F"}


def _word_tokens(value: str) -> Iterator[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for match in _WORD_RE.finditer(normalized):
        word = match.group(0)
        start = 0
        while start < len(word):
            cjk = _is_cjk_character(word[start])
            end = start + 1
            while end < len(word) and _is_cjk_character(word[end]) == cjk:
                end += 1
            run = word[start:end]
            if cjk:
                if len(run) == 1:
                    yield run
                else:
                    yield from (run[index : index + 2] for index in range(len(run) - 1))
            else:
                token = normalize_text(run)
                if token:
                    yield token
            start = end


class FederationError(RuntimeError):
    """Stable failure before a coherent federated generation is exposed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LexicalDocument:
    label_terms: frozenset[str]
    alias_terms: frozenset[str]
    body_terms: frozenset[str]
    normalized_label: str
    normalized_aliases: str


@dataclass(frozen=True)
class VaultIndex:
    """One bounded immutable navigation/lexical index for a Vault generation."""

    documents: Mapping[str, LexicalDocument]
    postings: Mapping[str, tuple[str, ...]]
    parents: Mapping[str, tuple[str, ...]]
    children: Mapping[str, tuple[str, ...]]
    roots: tuple[str, ...]
    weight_bytes: int


@dataclass(frozen=True)
class FederatedVault:
    vault: Vault
    view: GraphView
    ledger: SourceLedger
    evidence: SourceEvidenceView
    index: VaultIndex
    generation: str
    card: dict[str, Any]
    graph_bytes: int
    ledger_bytes: int
    authority_bytes: int
    authority_files: int
    ledger_rows: Mapping[str, int]
    retained_weight: int


@dataclass(frozen=True)
class FederationSnapshot:
    registry_generation: str
    generation: str
    vaults: tuple[FederatedVault, ...]
    incomplete_vaults: tuple[dict[str, str], ...]

    @property
    def by_id(self) -> dict[str, FederatedVault]:
        return {item.vault.id: item for item in self.vaults}


_INDEX_CACHE: "OrderedDict[tuple[str, str], VaultIndex]" = OrderedDict()
_INDEX_CACHE_LOCK = threading.Lock()


def qualified_handle(vault_id: str, node_id: str) -> str:
    return f"{vault_id}:{node_id}"


def _bounded_terms(
    value: str,
    *,
    max_terms: int,
    error_code: str,
    message: str,
) -> frozenset[str]:
    terms: set[str] = set()
    for token in _word_tokens(value):
        if len(token) > MAX_INDEX_TERM_CHARACTERS:
            raise FederationError(error_code, message)
        if token not in terms and len(terms) >= max_terms:
            raise FederationError(error_code, message)
        terms.add(token)
    return frozenset(terms)


def _index_terms(value: str) -> frozenset[str]:
    return _bounded_terms(
        value,
        max_terms=MAX_INDEX_TERMS_PER_FIELD,
        error_code="recall-index-too-large",
        message="Vault lexical field exceeds its representable term limit",
    )


def query_terms(value: str) -> frozenset[str]:
    """Return the same bounded tokens used by the immutable postings index."""

    return _bounded_terms(
        value,
        max_terms=MAX_QUERY_TERMS,
        error_code="recall-query-too-large",
        message="recall query exceeds its representable term limit",
    )


def _body_terms(text: str, entry: Any) -> tuple[frozenset[str], int]:
    terms: set[str] = set()
    stack: list[tuple[Iterator[Any], int]] = [(iter((text, entry)), 0)]
    values = 0
    encoded_bytes = 0
    while stack:
        iterator, depth = stack[-1]
        try:
            value = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        values += 1
        if values > MAX_INDEX_ENTRY_VALUES or depth > MAX_INDEX_ENTRY_DEPTH:
            raise FederationError(
                "recall-index-too-large",
                "Vault lexical entry exceeds bounded structural limits",
            )
        if isinstance(value, str):
            if len(value) > MAX_INDEX_TEXT_BYTES_PER_NODE:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical entry exceeds its bounded text limit",
                )
            encoded_bytes += len(value.encode("utf-8", errors="strict"))
            if encoded_bytes > MAX_INDEX_TEXT_BYTES_PER_NODE:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical entry exceeds its bounded text limit",
                )
            terms.update(_index_terms(value))
            if len(terms) > MAX_INDEX_TERMS_PER_FIELD:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical field exceeds its representable term limit",
                )
        elif isinstance(value, Mapping):
            if len(value) > MAX_INDEX_ENTRY_VALUES - values:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical entry exceeds bounded structural limits",
                )
            stack.append((iter(value.values()), depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_INDEX_ENTRY_VALUES - values:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical entry exceeds bounded structural limits",
                )
            stack.append((iter(value), depth + 1))
    return frozenset(terms), encoded_bytes


def _build_index(
    view: GraphView,
    *,
    maximum_weight_bytes: int = MAX_INDEX_CACHE_WEIGHT_BYTES,
) -> VaultIndex:
    documents: dict[str, LexicalDocument] = {}
    postings: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    posting_count = 0
    indexed_text_bytes = 0
    weight_bytes = 0
    for node_id in sorted(view.nodes):
        node = view.nodes[node_id]
        properties = node.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        alias_values = [str(item) for item in properties.get("aliases", [])]
        alias_bytes = sum(
            len(item.encode("utf-8", errors="strict")) for item in alias_values
        )
        if alias_bytes > MAX_INDEX_ALIAS_BYTES_PER_NODE:
            raise FederationError(
                "recall-index-too-large",
                "Vault aliases exceed the bounded per-node text limit",
            )
        aliases = " ".join(alias_values)
        label = str(node.get("label", ""))
        label_bytes = len(label.encode("utf-8", errors="strict"))
        body_terms, body_bytes = _body_terms(
            str(node.get("text", "")), node.get("entry") or {}
        )
        indexed_text_bytes += label_bytes + alias_bytes + body_bytes
        if indexed_text_bytes > MAX_INDEX_TOTAL_TEXT_BYTES:
            raise FederationError(
                "recall-index-too-large",
                "Vault lexical index exceeds its bounded total text limit",
            )
        weight_bytes += label_bytes + alias_bytes + body_bytes
        if weight_bytes > maximum_weight_bytes:
            raise FederationError(
                "federation-index-budget-exceeded",
                "Vault index exceeds the remaining federation index budget",
            )
        record = LexicalDocument(
            label_terms=_index_terms(label),
            alias_terms=_index_terms(aliases),
            body_terms=body_terms,
            normalized_label=normalize_text(label),
            normalized_aliases=normalize_text(aliases),
        )
        documents[node_id] = record
        for term in sorted(record.label_terms | record.alias_terms | record.body_terms):
            posting_count += 1
            if posting_count > MAX_INDEX_POSTINGS:
                raise FederationError(
                    "recall-index-too-large",
                    "Vault lexical index exceeds the bounded posting count",
                )
            postings[term].append(node_id)
            weight_bytes += (
                len(term.encode("utf-8"))
                + len(node_id.encode("utf-8"))
                + 64
            )
            if weight_bytes > maximum_weight_bytes:
                raise FederationError(
                    "federation-index-budget-exceeded",
                    "Vault index exceeds the remaining federation index budget",
                )

    for edge in view.edges:
        if edge.get("relation") != "contains":
            continue
        source = str(edge["source"])
        target = str(edge["target"])
        children[source].append(target)
        parents[target].append(source)
        weight_bytes += len(source.encode("utf-8")) + len(target.encode("utf-8")) + 32
        if weight_bytes > maximum_weight_bytes:
            raise FederationError(
                "federation-index-budget-exceeded",
                "Vault index exceeds the remaining federation index budget",
            )
    normalized_parents = {
        node_id: tuple(sorted(set(values))) for node_id, values in parents.items()
    }
    normalized_children = {
        node_id: tuple(sorted(set(values))) for node_id, values in children.items()
    }
    roots = tuple(
        node_id
        for node_id, node in sorted(view.nodes.items())
        if node.get("type") in {"field", "topic"}
        and not any(
            view.nodes.get(parent_id, {}).get("type") in {"field", "topic"}
            for parent_id in normalized_parents.get(node_id, ())
        )
    )
    return VaultIndex(
        documents=MappingProxyType(documents),
        postings=MappingProxyType(
            {key: tuple(value) for key, value in sorted(postings.items())}
        ),
        parents=MappingProxyType(normalized_parents),
        children=MappingProxyType(normalized_children),
        roots=roots,
        weight_bytes=weight_bytes,
    )


def _cached_index(
    vault_id: str,
    generation: str,
    view: GraphView,
    *,
    maximum_weight_bytes: int = MAX_INDEX_CACHE_WEIGHT_BYTES,
) -> VaultIndex:
    key = (vault_id, generation)
    with _INDEX_CACHE_LOCK:
        existing = _INDEX_CACHE.get(key)
        if existing is not None:
            if existing.weight_bytes > maximum_weight_bytes:
                raise FederationError(
                    "federation-index-budget-exceeded",
                    "cached Vault index exceeds the remaining federation index budget",
                )
            _INDEX_CACHE.move_to_end(key)
            return existing
    built = _build_index(view, maximum_weight_bytes=maximum_weight_bytes)
    with _INDEX_CACHE_LOCK:
        existing = _INDEX_CACHE.get(key)
        if existing is not None:
            if existing.weight_bytes > maximum_weight_bytes:
                raise FederationError(
                    "federation-index-budget-exceeded",
                    "cached Vault index exceeds the remaining federation index budget",
                )
            _INDEX_CACHE.move_to_end(key)
            return existing
        _INDEX_CACHE[key] = built
        cache_weight = sum(item.weight_bytes for item in _INDEX_CACHE.values())
        while (
            len(_INDEX_CACHE) > MAX_INDEX_CACHE_ENTRIES
            or cache_weight > MAX_INDEX_CACHE_WEIGHT_BYTES
        ):
            _, removed = _INDEX_CACHE.popitem(last=False)
            cache_weight -= removed.weight_bytes
    return built


def _capture_authority_state_once(
    vault: Vault,
    *,
    maximum_bytes: int,
    maximum_files: int,
) -> tuple[tuple[tuple[str, str], ...], dict[str, str], int, int]:
    roots = (vault.concept_root, vault.field_root, vault.topic_root)
    discovered = tuple(
        sorted(
            (path for root in roots for path in _discover_managed_markdown(vault, root)),
            key=lambda path: path.relative_to(vault.root).as_posix(),
        )
    )
    if len(discovered) > maximum_files:
        raise FederationError(
            "federation-authority-budget-exceeded",
            "Vault authority exceeds the remaining federation file budget",
        )
    raw_token: list[tuple[str, str]] = []
    normalized_hashes: dict[str, str] = {}
    total_bytes = 0
    for path in discovered:
        authority = path.relative_to(vault.root).as_posix()
        if unicodedata.normalize("NFC", authority) != authority:
            raise VaultError(
                "noncanonical-managed-path",
                "managed Markdown paths must use Unicode NFC",
            )
        remaining = maximum_bytes - total_bytes
        if remaining < 0:
            raise FederationError(
                "federation-authority-budget-exceeded",
                "Vault authority exceeds the remaining federation byte budget",
            )
        data = read_vault_relative_regular(
            vault,
            authority,
            maximum=min(MAX_MANAGED_MARKDOWN_BYTES, remaining),
        )
        total_bytes += len(data)
        note = parse_native_markdown(
            data,
            authority=authority,
            path=path,
        )
        raw_token.append((authority, hashlib.sha256(data).hexdigest()))
        normalized_hashes[authority] = hashlib.sha256(
            note.normalized_text.encode("utf-8")
        ).hexdigest()
        data = b""
    return (
        tuple(raw_token),
        dict(sorted(normalized_hashes.items())),
        total_bytes,
        len(discovered),
    )


def _authority_state(
    vault: Vault,
    *,
    maximum_bytes: int,
    maximum_files: int,
) -> tuple[str, dict[str, str], int, int]:
    first = _capture_authority_state_once(
        vault, maximum_bytes=maximum_bytes, maximum_files=maximum_files
    )
    second = _capture_authority_state_once(
        vault, maximum_bytes=maximum_bytes, maximum_files=maximum_files
    )
    if first[:2] != second[:2]:
        raise VaultError(
            "unstable-native-inventory",
            "managed Markdown paths or contents changed during the stable digest capture",
        )
    token = sha256_json([[path, digest] for path, digest in first[0]])
    return token, first[1], first[2], first[3]


def _semantic_graph_matches_evidence(
    view: GraphView, evidence: SourceEvidenceView
) -> bool:
    current_concepts: set[str] = set()
    for node_id, node in view.nodes.items():
        if node.get("type") != "knowledge":
            continue
        properties = node.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        text = str(node.get("text", ""))
        expected = (
            "pending"
            if not text.strip()
            else "current"
            if evidence.has_concept(node_id)
            else "needs-review"
        )
        if properties.get("curation_status") != expected:
            return False
        if expected == "current":
            current_concepts.add(node_id)
    if current_concepts != set(evidence.concept_ids):
        return False
    current_relations: set[tuple[str, str, str]] = set()
    for edge in view.edges:
        relation = str(edge.get("relation", ""))
        if relation == "contains":
            continue
        expected = (
            "current"
            if evidence.has_relation(
                str(edge.get("source", "")),
                relation,
                str(edge.get("target", "")),
            )
            else "needs-review"
        )
        if edge.get("curation_status") != expected:
            return False
        if expected == "current":
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if relation == "contrasts-with":
                source, target = sorted((source, target))
            current_relations.add((source, relation, target))
    return current_relations == set(evidence.relations)


def _capture_one(
    vault: Vault,
    *,
    maximum_graph_bytes: int = MAX_FEDERATION_GRAPH_BYTES,
    maximum_ledger_bytes: int = MAX_FEDERATION_LEDGER_BYTES,
    maximum_index_weight: int = MAX_FEDERATION_INDEX_WEIGHT_BYTES,
    maximum_authority_bytes: int = MAX_FEDERATION_AUTHORITY_BYTES,
    maximum_authority_files: int = MAX_FEDERATION_AUTHORITY_FILES,
    maximum_retained_weight: int = MAX_FEDERATION_RETAINED_WEIGHT_BYTES,
    maximum_counts: Mapping[str, int] | None = None,
) -> FederatedVault:
    graph_usage: dict[str, Any] = {}
    if maximum_retained_weight <= 0:
        raise FederationError(
            "federation-retained-budget-exceeded",
            "federated recall exceeds its retained-memory weight budget",
        )
    with vault_generation_guard(vault):
        selected = load_vault(vault.root, expected_id=vault.id)
        _recover_native_transactions_locked(selected)
        selected = load_vault(selected.root, expected_id=selected.id)
        state, manifest, manifest_sha256 = _load_live_state_locked(
            selected,
            maximum_total_bytes=min(
                maximum_graph_bytes, maximum_retained_weight // 4
            ),
            maximum_counts=maximum_counts,
            usage=graph_usage,
        )
        graph_weight = (
            int(graph_usage["bytes"]) * 4
            + int(graph_usage["nodes"]) * 1024
            + int(graph_usage["edges"]) * 512
            + int(graph_usage["references"]) * 512
        )
        if graph_weight >= maximum_retained_weight:
            raise FederationError(
                "federation-retained-budget-exceeded",
                "Vault graph exceeds the remaining federation retained-memory budget",
            )
        ledger_byte_limit = min(
            maximum_ledger_bytes,
            (maximum_retained_weight - graph_weight) // 4,
        )
        ledger = load_source_ledger_metadata(
            selected,
            maximum_artifact_bytes=ledger_byte_limit,
            maximum_artifact_rows=(
                {
                    name: maximum_counts[name]
                    for name in ("documents", "versions", "derivations")
                    if name in maximum_counts
                }
                if maximum_counts is not None
                else None
            ),
        )
        ledger_bytes = (
            sum(
                int(record["bytes"])
                for record in ledger.manifest["artifacts"].values()
            )
            if ledger.manifest is not None
            else 0
        )
        ledger_rows = {
            "documents": len(ledger.documents),
            "versions": len(ledger.versions),
            "derivations": len(ledger.derivations),
        }
        ledger_weight = (
            ledger_bytes * 4
            + ledger_rows["documents"] * 512
            + ledger_rows["versions"] * 512
            + ledger_rows["derivations"] * 1024
        )
        if graph_weight + ledger_weight >= maximum_retained_weight:
            raise FederationError(
                "federation-retained-budget-exceeded",
                "Vault metadata exceeds the remaining federation retained-memory budget",
            )
        evidence = current_evidence_view(ledger)
        authority_generation, source_hashes, authority_bytes, authority_files = (
            _authority_state(
                selected,
                maximum_bytes=maximum_authority_bytes,
                maximum_files=maximum_authority_files,
            )
        )
        authority_weight = authority_files * 256
        if graph_weight + ledger_weight + authority_weight >= maximum_retained_weight:
            raise FederationError(
                "federation-retained-budget-exceeded",
                "Vault authority inventory exceeds the remaining federation retained-memory budget",
            )
        if source_hashes != dict(sorted((manifest.get("source_hashes") or {}).items())):
            raise FederationError(
                "stale-native-graph",
                "native graph does not match the current authority inventory",
            )
        snapshot = make_agent_snapshot(state, namespace=selected.id)
        validate_agent_snapshot(snapshot)
        view = GraphView._from_snapshot(
            selected.root / ".kgdistiller" / "graph",
            snapshot,
            empty_alignment_set(),
            generation=manifest_sha256,
            source_hashes=source_hashes,
        )
        if not _semantic_graph_matches_evidence(view, evidence):
            raise FederationError(
                "stale-source-ledger",
                "native graph does not match the current source evidence ledger",
            )
        graph_sha256 = str(manifest.get("graph_sha256", ""))
        generation = sha256_json(
            {
                "vault_manifest_sha256": sha256_json(selected.manifest),
                "graph_manifest_sha256": manifest_sha256,
                "graph_sha256": graph_sha256,
                "source_ledger_generation_sha256": ledger.generation_sha256,
                "authority_generation_sha256": authority_generation,
            }
        )
        counts = dict(manifest.get("counts") or {})
        card = {
            "vault_id": selected.id,
            "label": selected.label,
            "health": "current",
            "generation": generation,
            "graph_manifest_sha256": manifest_sha256,
            "graph_sha256": graph_sha256,
            "source_ledger_generation_sha256": ledger.generation_sha256,
            "authority_generation_sha256": authority_generation,
            "live_source_generation_sha256": None,
            "counts": {
                "nodes": int(counts.get("nodes", 0)),
                "edges": int(counts.get("edges", 0)),
                "references": int(counts.get("references", 0)),
                "documents": len(ledger.documents),
            },
            "source_freshness": {
                "current": 0,
                "changed": 0,
                "missing": 0,
                "unavailable": len(ledger.documents),
            },
        }
    # The validated view and ledger are immutable copies.  Building a large
    # postings index must not extend the writer-blocking generation guard.
    index = _cached_index(
        selected.id,
        generation,
        view,
        maximum_weight_bytes=min(
            maximum_index_weight,
            maximum_retained_weight - graph_weight - ledger_weight - authority_weight,
        ),
    )
    retained_weight = graph_weight + ledger_weight + authority_weight + index.weight_bytes
    return FederatedVault(
        vault=selected,
        view=view,
        ledger=ledger,
        evidence=evidence,
        index=index,
        generation=generation,
        card=card,
        graph_bytes=int(graph_usage["bytes"]),
        ledger_bytes=ledger_bytes,
        authority_bytes=authority_bytes,
        authority_files=authority_files,
        ledger_rows=MappingProxyType(ledger_rows),
        retained_weight=retained_weight,
    )


def _incomplete(vault_id: str, error: BaseException) -> dict[str, str]:
    code = getattr(error, "code", "invalid-vault-generation")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code):
        code = "invalid-vault-generation"
    if code in {"missing-vault", "vault-id-mismatch", "invalid-vault-layout", "invalid-vault"}:
        message = "registered Vault is missing or invalid"
    elif code in {"vault-writer-lock-conflict", "registry-lock-conflict"}:
        message = "registered Vault is busy; retry recall"
    elif code.startswith("stale-"):
        message = "registered Vault has a stale derived generation"
    else:
        message = "registered Vault could not provide a coherent recall generation"
    return {"vault_id": vault_id, "code": code[:64], "message": message}


def _registry_identity(registry: Any) -> tuple[tuple[str, str], ...]:
    return tuple((item.id, str(item.path)) for item in registry.registrations)


def capture_federation(
    *,
    home: Path | str | None = None,
    vault_ids: Iterable[str] = (),
    max_attempts: int = MAX_CAPTURE_ATTEMPTS,
) -> FederationSnapshot:
    """Capture registered Vaults without ever holding registry and Vault locks together."""

    if not 1 <= max_attempts <= 10:
        raise FederationError("invalid-capture-attempts", "capture attempts must be between 1 and 10")
    requested = tuple(sorted(set(vault_ids)))
    if len(requested) > MAX_REGISTRY_ENTRIES:
        raise FederationError("too-many-vaults", "recall selects too many Vaults")
    last_error: BaseException | None = None
    for _ in range(max_attempts):
        try:
            before = load_registry(home, validate_vaults=False)
        except (VaultError, OSError, UnicodeError, ValueError) as error:
            raise FederationError(
                "invalid-vault-registry", "Vault registry is unavailable or invalid"
            ) from error
        registration_by_id = {item.id: item for item in before.registrations}
        selected_ids = requested or tuple(sorted(registration_by_id))
        vaults: list[FederatedVault] = []
        incomplete: list[dict[str, str]] = []
        used_graph_bytes = 0
        used_ledger_bytes = 0
        used_index_weight = 0
        used_authority_bytes = 0
        used_authority_files = 0
        used_retained_weight = 0
        used_counts = {
            "nodes": 0,
            "edges": 0,
            "references": 0,
            "documents": 0,
            "versions": 0,
            "derivations": 0,
        }
        for vault_id in selected_ids:
            registration = registration_by_id.get(vault_id)
            if registration is None:
                incomplete.append(
                    {
                        "vault_id": vault_id,
                        "code": "vault-not-registered",
                        "message": "requested Vault is not registered",
                    }
                )
                continue
            try:
                vault = load_vault(registration.path, expected_id=vault_id)
                captured = _capture_one(
                    vault,
                    maximum_graph_bytes=MAX_FEDERATION_GRAPH_BYTES
                    - used_graph_bytes,
                    maximum_ledger_bytes=MAX_FEDERATION_LEDGER_BYTES
                    - used_ledger_bytes,
                    maximum_index_weight=MAX_FEDERATION_INDEX_WEIGHT_BYTES
                    - used_index_weight,
                    maximum_authority_bytes=MAX_FEDERATION_AUTHORITY_BYTES
                    - used_authority_bytes,
                    maximum_authority_files=MAX_FEDERATION_AUTHORITY_FILES
                    - used_authority_files,
                    maximum_retained_weight=MAX_FEDERATION_RETAINED_WEIGHT_BYTES
                    - used_retained_weight,
                    maximum_counts={
                        "nodes": MAX_FEDERATION_NODES - used_counts["nodes"],
                        "edges": MAX_FEDERATION_EDGES - used_counts["edges"],
                        "references": MAX_FEDERATION_REFERENCES
                        - used_counts["references"],
                        "documents": MAX_FEDERATION_DOCUMENTS
                        - used_counts["documents"],
                        "versions": MAX_FEDERATION_VERSIONS
                        - used_counts["versions"],
                        "derivations": MAX_FEDERATION_DERIVATIONS
                        - used_counts["derivations"],
                    },
                )
                vaults.append(captured)
                used_graph_bytes += captured.graph_bytes
                used_ledger_bytes += captured.ledger_bytes
                used_index_weight += captured.index.weight_bytes
                used_authority_bytes += captured.authority_bytes
                used_authority_files += captured.authority_files
                used_retained_weight += captured.retained_weight
                for name in ("nodes", "edges", "references", "documents"):
                    used_counts[name] += int(captured.card["counts"][name])
                for name in ("versions", "derivations"):
                    used_counts[name] += int(captured.ledger_rows[name])
            except (
                FederationError,
                NativeCompilerError,
                NativeNoteError,
                SourceArchiveError,
                VaultError,
                KnowledgeError,
                OSError,
                RecursionError,
                UnicodeError,
                ValueError,
            ) as error:
                incomplete.append(_incomplete(vault_id, error))
        try:
            after = load_registry(home, validate_vaults=False)
        except (VaultError, OSError, UnicodeError, ValueError) as error:
            last_error = error
            continue
        if (
            before.generation != after.generation
            or _registry_identity(before) != _registry_identity(after)
        ):
            last_error = FederationError(
                "stale-vault-registry", "Vault registry changed during recall capture"
            )
            continue
        vaults.sort(key=lambda item: item.vault.id)
        incomplete.sort(key=lambda item: (item["vault_id"], item["code"]))
        generation = sha256_json(
            {
                "registry_generation": before.generation,
                "vaults": [
                    {"vault_id": item.vault.id, "generation": item.generation}
                    for item in vaults
                ],
                "incomplete_vaults": [
                    {"vault_id": item["vault_id"], "code": item["code"]}
                    for item in incomplete
                ],
            }
        )
        return FederationSnapshot(
            registry_generation=before.generation,
            generation=generation,
            vaults=tuple(vaults),
            incomplete_vaults=tuple(copy.deepcopy(incomplete)),
        )
    raise FederationError(
        "stale-vault-registry",
        "Vault registry changed during every bounded recall capture attempt",
    ) from last_error


def project_federation(
    snapshot: FederationSnapshot,
    vault_ids: Iterable[str],
) -> FederationSnapshot:
    """Project one coherent snapshot to an explicit bounded Vault selection."""

    requested = tuple(sorted(set(vault_ids)))
    if not requested:
        return snapshot
    complete = {item.vault.id: item for item in snapshot.vaults}
    incomplete = {item["vault_id"]: item for item in snapshot.incomplete_vaults}
    missing = [
        vault_id
        for vault_id in requested
        if vault_id not in complete and vault_id not in incomplete
    ]
    if missing:
        raise FederationError(
            "snapshot-vault-unavailable",
            "requested Vault is not present in the supplied federation snapshot",
        )
    selected_vaults = tuple(complete[vault_id] for vault_id in requested if vault_id in complete)
    selected_incomplete = tuple(
        copy.deepcopy(incomplete[vault_id])
        for vault_id in requested
        if vault_id in incomplete
    )
    generation = sha256_json(
        {
            "registry_generation": snapshot.registry_generation,
            "vaults": [
                {"vault_id": item.vault.id, "generation": item.generation}
                for item in selected_vaults
            ],
            "incomplete_vaults": [
                {"vault_id": item["vault_id"], "code": item["code"]}
                for item in selected_incomplete
            ],
        }
    )
    return FederationSnapshot(
        registry_generation=snapshot.registry_generation,
        generation=generation,
        vaults=selected_vaults,
        incomplete_vaults=selected_incomplete,
    )


__all__ = [
    "FederatedVault",
    "FederationError",
    "FederationSnapshot",
    "LexicalDocument",
    "VaultIndex",
    "capture_federation",
    "project_federation",
    "qualified_handle",
    "query_terms",
]
