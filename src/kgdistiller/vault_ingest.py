"""Stale-safe transactional ingest for registered native Vault authorities."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence, Union

from . import __version__
from .contracts import (
    ContractError,
    canonical_json,
    finalize_self_digest,
    sha256_json,
    validate_contract,
)
from .native_compiler import (
    NativeCompilation,
    NativeCompilerError,
    _capture_live_graph,
    _expected_bytes,
    _load_live_state_locked,
    _manifest_artifact_names,
    _native_graph_artifact_limit,
    _read_transaction,
    _recover_native_transactions_locked,
    compile_vault_overlay,
    validate_native_compilation,
)
from .native_notes import (
    NativeNoteError,
    merge_native_note_bytes,
    parse_native_markdown,
    validate_complete_native_note_bytes,
)
from .source_archive import (
    MAX_SOURCE_BYTES,
    PreparedSourceGeneration,
    SourceArchiveError,
    SourceEvidenceView,
    SourceLedger,
    _PinnedDirectory,
    _is_link_like,
    _is_reparse,
    _portable_relative,
    _read_regular,
    _remove_stage,
    current_evidence_view,
    install_derivation_generation,
    load_source_ledger,
    load_source_ledger_generation,
    prepare_derivation_generation,
    read_vault_relative_regular,
    replace_vault_relative_regular,
    stage_derivation_generation,
    unlink_vault_relative_regular,
    vault_writer_lock,
)
from .vaults import (
    ManagedMarkdownFile,
    Vault,
    VaultError,
    load_registry,
    load_vault,
    managed_markdown_token,
    snapshot_managed_markdown,
    vault_registry_read_guard,
    vault_registry_lock,
)


REQUEST_SCHEMA = "qlkg-vault-ingest-request-v1"
RECALL_REPORT_SCHEMA = "qlkg-recall-report-v1"
PLAN_SCHEMA = "qlkg-vault-ingest-plan-v1"
RECEIPT_SCHEMA = "qlkg-vault-ingest-receipt-v1"
REPORT_SCHEMA = "qlkg-vault-ingest-report-v1"
ERROR_SCHEMA = "qlkg-vault-ingest-error-v1"
JOURNAL_SCHEMA = "qlkg-vault-ingest-journal-v1"
CAPABILITY = "vault-transactional-ingest-v1"
JOURNAL_PATH = ".kgdistiller/build/vault-ingest-journal.json"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_QUERY_REPORT_BYTES = 16 * 1024 * 1024
MAX_NOTE_BYTES = 8 * 1024 * 1024
MAX_GRAPH_BYTES = 512 * 1024 * 1024
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_RECEIPTS = 100_000
MAX_RECEIPT_SHARDS = 256
MAX_RECEIPT_INVENTORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_CREATED_DIRECTORIES = 8192
MAX_TRANSACTION_TARGETS = 100_512
MAX_TRANSACTION_IMAGE_BYTES = 5 * 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_STAGE_RE = re.compile(r"\.stage-vault-ingest-[0-9a-f]{32}\Z")
FailureInjector = Callable[[str], None]
ReceiptPrecondition = Callable[[Mapping[str, Any]], None]


class VaultIngestError(RuntimeError):
    """Stable closed error for native Vault planning and publication."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "validation",
        diagnostics: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.diagnostics = [dict(item) for item in diagnostics[:32]]

    def payload(self) -> dict[str, Any]:
        def bounded(value: Any, maximum: int, fallback: str) -> str:
            try:
                text = str(value)
            except Exception:
                text = fallback
            if not text:
                text = fallback
            if len(text) > maximum:
                text = text[: maximum - 1] + "…"
            return text

        code = bounded(self.code, 128, "vault-ingest-failed")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code):
            code = "vault-ingest-failed"
        stage = self.stage if self.stage in {
            "request",
            "resolution",
            "validation",
            "planning",
            "recovery",
            "publication",
            "rollback",
        } else "validation"
        diagnostics = []
        for item in self.diagnostics[:32]:
            diagnostics.append(
                {
                    "code": bounded(item.get("code"), 128, "diagnostic"),
                    "message": bounded(
                        item.get("message"), 4096, "ingest diagnostic"
                    ),
                }
            )
        payload = {
            "schema": ERROR_SCHEMA,
            "error": {
                "code": code,
                "message": bounded(self.message, 16384, "Vault ingest failed"),
                "stage": stage,
                "diagnostics": diagnostics,
            },
        }
        try:
            return validate_contract(payload)
        except ContractError:
            # Every public CLI error boundary must remain closed even if a
            # future internal caller supplied a malformed diagnostic value.
            return {
                "schema": ERROR_SCHEMA,
                "error": {
                    "code": "vault-ingest-failed",
                    "message": "Vault ingest failed",
                    "stage": "validation",
                    "diagnostics": [],
                },
            }


@contextlib.contextmanager
def _closed_ingest_errors(*, stage: str):
    try:
        yield
    except VaultIngestError:
        raise
    except RecursionError as error:
        raise VaultIngestError(
            "invalid-nested-input",
            "nested JSON input exceeds the supported structural depth",
            stage=stage,
        ) from error
    except (NativeCompilerError, NativeNoteError, SourceArchiveError, VaultError) as error:
        raise VaultIngestError(
            getattr(error, "code", "vault-ingest-failed"),
            getattr(error, "message", str(error)),
            stage=stage,
        ) from error


@dataclass(frozen=True)
class _RequestInput:
    request: dict[str, Any]
    root: Path
    path: Path | None
    raw_sha256: str | None


@dataclass(frozen=True)
class _Prepared:
    input: _RequestInput
    vault: Vault
    ledger: SourceLedger
    snapshots: tuple[ManagedMarkdownFile, ...]
    overlay: dict[str, bytes | None]
    compilation: NativeCompilation
    query_report_sha256: str
    graph_before: dict[str, bytes]
    graph_before_generation: str | None
    note_before_sha256: str
    note_after_sha256: str
    plan: dict[str, Any]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(data: bytes, *, kind: str) -> dict[str, Any]:
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
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise VaultIngestError(
            f"invalid-{kind}", f"malformed {kind}: {error}", stage="request"
        ) from error
    if not isinstance(payload, dict):
        raise VaultIngestError(
            f"invalid-{kind}", f"{kind} must be a JSON object", stage="request"
        )
    return payload


def _absolute_lexical(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _read_external(path: Path, *, maximum: int, kind: str) -> bytes:
    absolute = _absolute_lexical(path)
    try:
        return _read_regular(
            absolute.parent,
            (absolute.name,),
            maximum=maximum,
            kind=kind,
            single_link=True,
        )
    except SourceArchiveError as error:
        raise VaultIngestError(
            f"invalid-{kind}", error.message, stage="request"
        ) from error


def _load_request(
    value: Path | str | Mapping[str, Any], *, request_root: Path | str | None = None
) -> _RequestInput:
    if isinstance(value, Mapping):
        request = dict(value)
        try:
            canonical_request = canonical_json(request).encode(
                "utf-8", errors="strict"
            )
        except (ContractError, UnicodeError, RecursionError) as error:
            raise VaultIngestError(
                "invalid-request",
                "mapping request is not canonical strict UTF-8 JSON",
                stage="request",
            ) from error
        if len(canonical_request) > MAX_REQUEST_BYTES:
            raise VaultIngestError(
                "request-too-large",
                f"vault ingest request exceeds {MAX_REQUEST_BYTES} bytes",
                stage="request",
            )
        root = _absolute_lexical(request_root or Path.cwd())
        path = None
        raw_sha = None
    else:
        path = _absolute_lexical(value)
        data = _read_external(path, maximum=MAX_REQUEST_BYTES, kind="vault-ingest-request")
        request = _strict_json(data, kind="vault-ingest-request")
        root = path.parent
        raw_sha = _sha256(data)
    try:
        request = validate_contract(request)
    except (ContractError, RecursionError) as error:
        raise VaultIngestError(
            "invalid-request", str(error), stage="request"
        ) from error
    if request.get("schema") != REQUEST_SCHEMA:
        raise VaultIngestError(
            "unsupported-request-schema", f"expected {REQUEST_SCHEMA}", stage="request"
        )
    capabilities = request["capabilities"]
    if CAPABILITY not in capabilities:
        raise VaultIngestError(
            "unsupported-capability",
            f"request must require {CAPABILITY}",
            stage="request",
        )
    if request["alignment_mutations"]:
        raise VaultIngestError(
            "unsupported-alignment-mutation",
            "the minimal F4 transaction rejects non-empty alignment mutation",
            stage="request",
        )
    if not request["note_patches"] and not request["derivation_updates"]:
        raise VaultIngestError(
            "empty-request", "native Vault ingest requires at least one change", stage="request"
        )
    note_paths = [str(item["path"]) for item in request["note_patches"]]
    version_ids = [str(item["version_id"]) for item in request["derivation_updates"]]
    if len(note_paths) != len(set(note_paths)) or len(version_ids) != len(set(version_ids)):
        raise VaultIngestError(
            "invalid-request", "note paths and derivation versions must be unique", stage="request"
        )
    for patch in request["note_patches"]:
        if patch["operation"] == "write":
            content = str(patch["content"]).encode("utf-8")
            if len(content) > MAX_NOTE_BYTES or _sha256(content) != patch["content_sha256"]:
                raise VaultIngestError(
                    "invalid-note-content",
                    f"note content digest or bound is invalid: {patch['path']}",
                    stage="request",
                )
    return _RequestInput(request, root, path, raw_sha)


def _query_report(input_value: _RequestInput) -> tuple[dict[str, Any], str]:
    reference = input_value.request["query_report"]
    try:
        parts = _portable_relative(reference["path"], field="query report path")
        data = _read_regular(
            input_value.root,
            parts,
            maximum=MAX_QUERY_REPORT_BYTES,
            kind="query-report",
            single_link=True,
        )
    except SourceArchiveError as error:
        raise VaultIngestError(
            "invalid-query-report",
            "query report is not a safe bounded regular file",
            stage="request",
        ) from error
    digest = _sha256(data)
    if digest != reference["sha256"]:
        raise VaultIngestError(
            "stale-query-report", "query report bytes do not match the request", stage="request"
        )
    report = _strict_json(data, kind="query-report")
    try:
        report = validate_contract(report)
    except (ContractError, RecursionError) as error:
        raise VaultIngestError(
            "invalid-query-report",
            "query report does not satisfy the closed qlkg-recall-report-v1 contract",
            stage="request",
        ) from error
    if report.get("schema") != RECALL_REPORT_SCHEMA:
        raise VaultIngestError(
            "invalid-query-report",
            "query report does not satisfy the closed qlkg-recall-report-v1 contract",
            stage="request",
        )
    request = input_value.request
    vault_id = str(request["vault_id"])
    vault_rows = [
        item for item in report["vaults"] if item.get("vault_id") == vault_id
    ]
    incomplete_rows = [
        item
        for item in report["incomplete_vaults"]
        if item.get("vault_id") == vault_id
    ]
    base = request["base"]
    applicable = report["registry_generation"] == request["registry_generation"]
    if base["graph_generation_sha256"] is None:
        applicable = (
            applicable
            and not vault_rows
            and len(incomplete_rows) == 1
            and incomplete_rows[0]["code"]
            in {"invalid-native-graph", "stale-native-graph"}
        )
    else:
        applicable = applicable and len(vault_rows) == 1 and not incomplete_rows
    if applicable and vault_rows:
        card = vault_rows[0]
        expected_vault_generation = sha256_json(
            {
                "vault_manifest_sha256": request["vault_manifest_sha256"],
                "graph_manifest_sha256": card["graph_manifest_sha256"],
                "graph_sha256": card["graph_sha256"],
                "source_ledger_generation_sha256": card[
                    "source_ledger_generation_sha256"
                ],
                "authority_generation_sha256": card[
                    "authority_generation_sha256"
                ],
            }
        )
        applicable = (
            card["graph_sha256"] == base["graph_generation_sha256"]
            and card["source_ledger_generation_sha256"]
            == base["source_ledger_generation_sha256"]
            and card["authority_generation_sha256"]
            == base["note_inventory_sha256"]
            and card["generation"] == expected_vault_generation
        )
    if not applicable:
        raise VaultIngestError(
            "stale-query-report",
            "query report does not bind the request's target Vault generation",
            stage="request",
        )
    return report, digest


def _registered_vault(
    vault_id: str, home: Path | str | None
) -> tuple[Any, Vault]:
    try:
        registry = load_registry(home, validate_vaults=False)
        matches = [item for item in registry.registrations if item.id == vault_id]
        if len(matches) != 1:
            raise VaultIngestError(
                "vault-not-registered",
                f"Vault is not uniquely registered: {vault_id}",
                stage="resolution",
            )
        vault = load_vault(matches[0].path, expected_id=vault_id)
    except VaultIngestError:
        raise
    except (VaultError, OSError, UnicodeError, ValueError) as error:
        raise VaultIngestError(
            "invalid-vault-selection", str(error), stage="resolution"
        ) from error
    return registry, vault


def _resolve_vault(request: Mapping[str, Any], home: Path | str | None) -> Vault:
    registry, vault = _registered_vault(str(request["vault_id"]), home)
    if registry.generation != request["registry_generation"]:
        raise VaultIngestError(
            "stale-registry-generation",
            "Vault registry generation differs from the request",
            stage="resolution",
        )
    if sha256_json(vault.manifest) != request["vault_manifest_sha256"]:
        raise VaultIngestError(
            "stale-vault-manifest",
            "portable Vault manifest differs from the request",
            stage="resolution",
        )
    return vault


def _note_token(token: Sequence[tuple[str, str]]) -> str:
    return sha256_json([[path, digest] for path, digest in token])


def _graph_snapshot(vault: Vault) -> tuple[dict[str, bytes], str | None]:
    files = _capture_live_graph(vault)
    if not files:
        return files, None
    state, manifest, _ = _load_live_state_locked(vault)
    generation = manifest.get("graph_sha256")
    if generation != state.manifest.get("graph_sha256") or not isinstance(generation, str):
        raise VaultIngestError(
            "invalid-native-graph", "live graph generation cannot be verified"
        )
    return files, generation


def _note_overlay(
    request: Mapping[str, Any],
    snapshots: tuple[ManagedMarkdownFile, ...],
) -> dict[str, bytes | None]:
    existing = {item.authority: item for item in snapshots}
    overlay: dict[str, bytes | None] = {}
    deleted_ids: dict[str, str] = {}
    created_ids: dict[str, str] = {}
    for patch in request["note_patches"]:
        authority = str(patch["path"])
        _portable_relative(authority, field="native note path")
        current = existing.get(authority)
        expected = patch["expected_raw_sha256"]
        if current is None and expected is not None:
            raise VaultIngestError(
                "stale-note-inventory", f"expected native note is missing: {authority}"
            )
        if current is not None and expected != current.raw_sha256:
            raise VaultIngestError(
                "stale-note-inventory", f"native note differs from the request: {authority}"
            )
        if patch["operation"] == "delete":
            if current is None:
                raise VaultIngestError(
                    "stale-note-inventory", f"cannot delete missing native note: {authority}"
                )
            try:
                deleted_ids[
                    parse_native_markdown(current.data, authority=authority).id
                ] = authority
            except NativeNoteError as error:
                raise VaultIngestError(error.code, error.message) from error
            overlay[authority] = None
            continue
        desired = str(patch["content"]).encode("utf-8")
        try:
            if current is None:
                note = validate_complete_native_note_bytes(desired, authority=authority)
                created_ids[note.id] = authority
                overlay[authority] = desired
            else:
                parse_native_markdown(desired, authority=authority)
                overlay[authority] = merge_native_note_bytes(
                    current.data, desired, authority=authority
                )
        except NativeNoteError as error:
            raise VaultIngestError(error.code, error.message) from error
    moved = sorted(set(deleted_ids) & set(created_ids))
    if moved:
        node_id = moved[0]
        raise VaultIngestError(
            "unsupported-native-note-move",
            "F4 v1 rejects cross-path delete/create moves so user frontmatter cannot be lost: "
            f"{deleted_ids[node_id]} -> {created_ids[node_id]}",
        )
    return overlay


def _overlay_token(
    snapshots: tuple[ManagedMarkdownFile, ...], overlay: Mapping[str, bytes | None]
) -> str:
    rows = {item.authority: item.raw_sha256 for item in snapshots}
    for path, data in overlay.items():
        if data is None:
            rows.pop(path, None)
        else:
            rows[path] = _sha256(data)
    return _note_token(tuple(sorted(rows.items())))


def _verify_live_sources(vault: Vault, ledger: SourceLedger, updates: Sequence[Mapping[str, Any]]) -> None:
    versions = {str(item["version_id"]): item for item in ledger.versions}
    documents = {str(item["current_version_id"]): item for item in ledger.documents}
    for update in updates:
        version_id = str(update["version_id"])
        version = versions.get(version_id)
        document = documents.get(version_id)
        if version is None or document is None:
            raise VaultIngestError(
                "stale-source-version", "derivation update is not for a current source version"
            )
        try:
            raw = read_vault_relative_regular(
                vault, str(document["path"]), maximum=MAX_SOURCE_BYTES
            )
        except SourceArchiveError as error:
            raise VaultIngestError(
                "stale-live-source", error.message, stage="validation"
            ) from error
        if _sha256(raw) != version["raw_sha256"] or len(raw) != version["byte_count"]:
            raise VaultIngestError(
                "stale-live-source",
                f"live source differs from captured version: {document['path']}",
            )


def _validation_rows(*stages: str) -> list[dict[str, str]]:
    return [{"stage": stage, "status": "passed"} for stage in stages]


def _validate_evidence_graph_closure(
    evidence: SourceEvidenceView, compilation: NativeCompilation
) -> None:
    """Require every effective reviewed fact to exist in the final authority graph."""

    nodes = compilation.state.nodes
    edges = compilation.state.edges
    for concept_id in sorted(evidence.concept_ids):
        node = nodes.get(concept_id)
        if node is None or node.get("type") != "knowledge":
            raise VaultIngestError(
                "missing-reviewed-concept",
                f"committed evidence references no final concept note: {concept_id}",
                stage="planning",
            )
    for source, relation, target in sorted(evidence.relations):
        edge = edges.get((source, relation, target))
        if edge is None:
            raise VaultIngestError(
                "missing-reviewed-relation",
                "committed relation evidence does not match the final compiled graph: "
                f"{source} {relation} {target}",
                stage="planning",
            )
        if relation == "contains":
            endpoint_types = (
                (nodes.get(source) or {}).get("type"),
                (nodes.get(target) or {}).get("type"),
            )
            if endpoint_types not in {
                ("field", "topic"),
                ("field", "knowledge"),
                ("topic", "knowledge"),
            }:
                raise VaultIngestError(
                    "invalid-reviewed-relation",
                    "contains evidence has invalid final endpoint types",
                    stage="planning",
                )


def _prepare(input_value: _RequestInput, *, home: Path | str | None) -> _Prepared:
    request = input_value.request
    _, query_sha = _query_report(input_value)
    vault = _resolve_vault(request, home)
    try:
        if _load_journal(vault) is not None or _read_transaction(vault) is not None:
            raise VaultIngestError(
                "pending-vault-transaction",
                "Vault has a pending transaction that apply must recover first",
                stage="planning",
            )
        snapshots = snapshot_managed_markdown(vault)
        ledger = load_source_ledger(vault)
        graph_before, graph_generation = _graph_snapshot(vault)
    except VaultIngestError:
        raise
    except (VaultError, SourceArchiveError, NativeCompilerError) as error:
        raise VaultIngestError(
            getattr(error, "code", "invalid-vault-state"),
            getattr(error, "message", str(error)),
        ) from error
    before_note = _note_token(managed_markdown_token(snapshots))
    base = request["base"]
    if (
        ledger.generation_sha256 != base["source_ledger_generation_sha256"]
        or graph_generation != base["graph_generation_sha256"]
        or before_note != base["note_inventory_sha256"]
    ):
        raise VaultIngestError(
            "stale-vault-state",
            "source ledger, graph, or native note inventory differs from the request",
        )
    _verify_live_sources(vault, ledger, request["derivation_updates"])
    overlay = _note_overlay(request, snapshots)
    canonical_updates = _derivation_summaries(request["derivation_updates"])
    try:
        if canonical_updates:
            dummy = prepare_derivation_generation(
                ledger,
                canonical_updates,
                graph_generation_sha256="0" * 64,
                ingest_receipt_sha256="0" * 64,
            )
            evidence = current_evidence_view(dummy.ledger)
            prospective_generation = dummy.ledger.generation_sha256
        else:
            evidence = current_evidence_view(ledger)
            prospective_generation = ledger.generation_sha256
        compilation = compile_vault_overlay(
            vault,
            overlay,
            evidence,
            ledger_generation=prospective_generation,
            snapshots=snapshots,
        )
        validate_native_compilation(compilation)
        _validate_evidence_graph_closure(evidence, compilation)
    except (SourceArchiveError, NativeCompilerError, NativeNoteError) as error:
        raise VaultIngestError(
            getattr(error, "code", "invalid-prospective-state"),
            getattr(error, "message", str(error)),
            stage="planning",
        ) from error
    after_note = _note_token(compilation.authority_token)
    graph_after = str(compilation.state.manifest["graph_sha256"])
    plan = finalize_self_digest(
        {
            "schema": PLAN_SCHEMA,
            "plan_sha256": "0" * 64,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "vault_id": vault.id,
            "registry_generation": request["registry_generation"],
            "vault_manifest_sha256": request["vault_manifest_sha256"],
            "before": {
                "source_ledger_generation_sha256": ledger.generation_sha256,
                "graph_generation_sha256": graph_generation,
                "note_inventory_sha256": before_note,
            },
            "after": {
                "graph_generation_sha256": graph_after,
                "note_inventory_sha256": after_note,
            },
            "changes": {
                "note_paths": sorted(overlay),
                "derivation_version_ids": sorted(
                    str(item["version_id"]) for item in request["derivation_updates"]
                ),
            },
            "validations": _validation_rows(
                "request", "bindings", "notes", "evidence", "graph", "recompile"
            ),
            "status": "ready",
        },
        "plan_sha256",
    )
    validate_contract(plan)
    return _Prepared(
        input=input_value,
        vault=vault,
        ledger=ledger,
        snapshots=snapshots,
        overlay=overlay,
        compilation=compilation,
        query_report_sha256=query_sha,
        graph_before=graph_before,
        graph_before_generation=graph_generation,
        note_before_sha256=before_note,
        note_after_sha256=after_note,
        plan=plan,
    )


def _assert_prepared_current(prepared: _Prepared, *, home: Path | str | None) -> None:
    current = _resolve_vault(prepared.input.request, home)
    if current.root != prepared.vault.root:
        raise VaultIngestError(
            "stale-vault-selection", "registered Vault path changed during ingest"
        )
    if prepared.input.path is not None:
        data = _read_external(
            prepared.input.path,
            maximum=MAX_REQUEST_BYTES,
            kind="vault-ingest-request",
        )
        if _sha256(data) != prepared.input.raw_sha256:
            raise VaultIngestError(
                "stale-request", "request file bytes changed during ingest"
            )
    _, query_sha = _query_report(prepared.input)
    try:
        ledger = load_source_ledger(current)
        snapshots = snapshot_managed_markdown(current)
        _, graph_generation = _graph_snapshot(current)
    except (VaultError, SourceArchiveError, NativeCompilerError) as error:
        raise VaultIngestError(
            getattr(error, "code", "stale-vault-state"),
            getattr(error, "message", str(error)),
        ) from error
    if (
        query_sha != prepared.query_report_sha256
        or ledger.generation_sha256 != prepared.ledger.generation_sha256
        or _note_token(managed_markdown_token(snapshots)) != prepared.note_before_sha256
        or graph_generation != prepared.graph_before_generation
    ):
        raise VaultIngestError(
            "stale-vault-state", "an ingest precondition changed during validation"
        )
    _verify_live_sources(current, ledger, prepared.input.request["derivation_updates"])


def _assert_after_current(
    prepared: _Prepared,
    source: PreparedSourceGeneration | None,
    compilation: NativeCompilation,
    targets: Sequence["_Target"],
    *,
    home: Path | str | None,
) -> None:
    current = _resolve_vault(prepared.input.request, home)
    if current.root != prepared.vault.root:
        raise VaultIngestError(
            "stale-vault-selection", "registered Vault path changed during publication"
        )
    if prepared.input.path is not None:
        request_bytes = _read_external(
            prepared.input.path,
            maximum=MAX_REQUEST_BYTES,
            kind="vault-ingest-request",
        )
        if _sha256(request_bytes) != prepared.input.raw_sha256:
            raise VaultIngestError("stale-request", "request bytes changed during publication")
    _, query_sha = _query_report(prepared.input)
    try:
        ledger = load_source_ledger(current)
        snapshots = snapshot_managed_markdown(current)
        graph_files, graph_generation = _graph_snapshot(current)
    except (VaultError, SourceArchiveError, NativeCompilerError) as error:
        raise VaultIngestError(
            getattr(error, "code", "stale-after-state"),
            getattr(error, "message", str(error)),
        ) from error
    expected_ledger = prepared.ledger if source is None else source.ledger
    expected_graph = _expected_bytes(compilation)
    if (
        query_sha != prepared.query_report_sha256
        or ledger.generation_sha256 != expected_ledger.generation_sha256
        or ledger.documents != expected_ledger.documents
        or ledger.versions != expected_ledger.versions
        or ledger.derivations != expected_ledger.derivations
        or _note_token(managed_markdown_token(snapshots)) != prepared.note_after_sha256
        or graph_generation != compilation.state.manifest["graph_sha256"]
        or graph_files != expected_graph
        or not all(_current_matches(current, target, target.new) for target in targets)
    ):
        raise VaultIngestError(
            "stale-after-state",
            "published Vault bytes do not match the fully validated transaction",
            stage="publication",
        )
    _assert_live_temporaries_absent(current, targets, stage="publication")
    _verify_live_sources(current, ledger, prepared.input.request["derivation_updates"])


def plan_vault_ingest(
    request: Path | str | Mapping[str, Any],
    *,
    home: Path | str | None = None,
    request_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build and recheck a deterministic plan without changing Vault bytes."""

    with _closed_ingest_errors(stage="planning"):
        input_value = _load_request(request, request_root=request_root)
        prepared = _prepare(input_value, home=home)
        _complete_preparation(prepared)
        _assert_prepared_current(prepared, home=home)
        return prepared.plan


def _sorted_records(values: Sequence[Mapping[str, Any]], *keys: str) -> list[dict[str, Any]]:
    rows = [json.loads(canonical_json(dict(item))) for item in values]
    return sorted(rows, key=lambda item: tuple(str(item[key]) for key in keys) + (canonical_json(item),))


def _derivation_summaries(updates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for update in updates:
        relation_evidence: list[dict[str, Any]] = []
        for raw_record in update["relation_evidence"]:
            record = json.loads(canonical_json(dict(raw_record)))
            if record["relation"] == "contrasts-with":
                record["source"], record["target"] = sorted(
                    (str(record["source"]), str(record["target"]))
                )
            relation_evidence.append(record)
        summaries.append(
            {
                "version_id": str(update["version_id"]),
                "status": str(update["status"]),
                "candidate_dispositions": _sorted_records(
                    update["candidate_dispositions"], "candidate_id", "disposition"
                ),
                "concept_ids": sorted(str(item) for item in update["concept_ids"]),
                "concept_evidence": _sorted_records(
                    update["concept_evidence"], "concept_id"
                ),
                "relation_evidence": _sorted_records(
                    relation_evidence, "source", "relation", "target"
                ),
            }
        )
    return sorted(summaries, key=lambda item: item["version_id"])


def _prepared_note_images(prepared: _Prepared) -> list[dict[str, Any]]:
    before = {item.authority: item.data for item in prepared.snapshots}
    return [
        {
            "path": path,
            "before_raw_sha256": (
                None if before.get(path) is None else _sha256(before[path])
            ),
            "after_raw_sha256": (
                None
                if prepared.overlay[path] is None
                else _sha256(prepared.overlay[path] or b"")
            ),
        }
        for path in sorted(prepared.overlay)
    ]


def _receipt_payload(prepared: _Prepared) -> dict[str, Any]:
    request = prepared.input.request
    receipt = finalize_self_digest(
        {
            "schema": RECEIPT_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "receipt_sha256": "0" * 64,
            "vault_id": prepared.vault.id,
            "engine": {
                "name": "kgdistiller",
                "version": __version__,
                "capabilities": [CAPABILITY],
                "graph_schema": "qlkg-v3",
            },
            "before": {
                "registry_generation": request["registry_generation"],
                "vault_manifest_sha256": request["vault_manifest_sha256"],
                "source_ledger_generation_sha256": prepared.ledger.generation_sha256,
                "graph_generation_sha256": prepared.graph_before_generation,
                "note_inventory_sha256": prepared.note_before_sha256,
                "query_report_sha256": prepared.query_report_sha256,
            },
            "after": {
                "graph_generation_sha256": prepared.compilation.state.manifest[
                    "graph_sha256"
                ],
                "note_inventory_sha256": prepared.note_after_sha256,
                "derivations": _derivation_summaries(request["derivation_updates"]),
            },
            "changes": {
                "notes": _prepared_note_images(prepared),
                "derivation_version_ids": prepared.plan["changes"][
                    "derivation_version_ids"
                ],
            },
            "validations": _validation_rows(
                "request", "bindings", "notes", "evidence", "graph", "recompile"
            ),
            "warnings": [],
            "status": "committed",
        },
        "receipt_sha256",
    )
    validate_contract(receipt)
    data = canonical_json(receipt).encode("utf-8") + b"\n"
    if len(data) > MAX_RECEIPT_BYTES:
        raise VaultIngestError(
            "receipt-too-large", f"canonical receipt exceeds {MAX_RECEIPT_BYTES} bytes"
        )
    return receipt


def _final_generation(
    prepared: _Prepared, receipt: Mapping[str, Any]
) -> tuple[PreparedSourceGeneration | None, NativeCompilation]:
    try:
        canonical_updates = receipt["after"]["derivations"]
        if canonical_updates:
            source = prepare_derivation_generation(
                prepared.ledger,
                canonical_updates,
                graph_generation_sha256=str(
                    prepared.compilation.state.manifest["graph_sha256"]
                ),
                ingest_receipt_sha256=str(receipt["receipt_sha256"]),
            )
            evidence = current_evidence_view(source.ledger)
            ledger_generation = source.ledger.generation_sha256
        else:
            source = None
            evidence = current_evidence_view(prepared.ledger)
            ledger_generation = prepared.ledger.generation_sha256
        recompiled = compile_vault_overlay(
            prepared.vault,
            prepared.overlay,
            evidence,
            ledger_generation=ledger_generation,
            snapshots=prepared.snapshots,
        )
        validate_native_compilation(recompiled)
        _validate_evidence_graph_closure(evidence, recompiled)
    except (SourceArchiveError, NativeCompilerError, NativeNoteError) as error:
        raise VaultIngestError(
            getattr(error, "code", "invalid-final-state"),
            getattr(error, "message", str(error)),
            stage="planning",
        ) from error
    if _expected_bytes(recompiled) != _expected_bytes(prepared.compilation):
        raise VaultIngestError(
            "receipt-ledger-graph-cycle",
            "receipt-bound final ledger changed the prospective graph generation",
            stage="planning",
        )
    return source, recompiled


def _complete_preparation(
    prepared: _Prepared,
) -> tuple[dict[str, Any], PreparedSourceGeneration | None, NativeCompilation]:
    """Close E→G→receipt→final-ledger→same-G before any Vault write."""

    receipt = _receipt_payload(prepared)
    source, recompiled = _final_generation(prepared, receipt)
    return receipt, source, recompiled


def receipt_relative_path(receipt_sha256: str) -> str:
    """Return the fixed portable content-addressed receipt identity."""

    if not isinstance(receipt_sha256, str) or not _SHA256_RE.fullmatch(receipt_sha256):
        raise VaultIngestError("invalid-receipt", "receipt digest is not lowercase SHA-256")
    return (
        f".kgdistiller/receipts/sha256/{receipt_sha256[:2]}/"
        f"{receipt_sha256}.json"
    )


def _receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    return canonical_json(dict(receipt)).encode("utf-8") + b"\n"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(path), os.fspath(root)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _read_pinned_output_leaf(
    parent: _PinnedDirectory,
    path: Path,
    *,
    maximum: int,
    allowed_links: frozenset[int] = frozenset({1}),
) -> tuple[os.stat_result | None, bytes | None]:
    def stable(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            os.path.samestat(first, second)
            and first.st_mode == second.st_mode
            and first.st_nlink == second.st_nlink
            and first.st_size == second.st_size
            and first.st_mtime_ns == second.st_mtime_ns
            and first.st_ctime_ns == second.st_ctime_ns
        )

    def safe(metadata: os.stat_result) -> bool:
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink in allowed_links
            and metadata.st_size <= maximum
            and not _is_link_like(path, metadata)
        )

    metadata = parent.lstat_leaf(path.name)
    if metadata is None:
        return None, None
    if not safe(metadata):
        raise VaultIngestError(
            "unsafe-output-artifact",
            "ingest output leaf is not a bounded ordinary file",
            stage="publication",
        )
    descriptor = parent.open_existing_file(path.name)
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat_leaf(path.name)
        if (
            current is None
            or not safe(opened)
            or not safe(current)
            or not stable(metadata, opened)
            or not stable(opened, current)
        ):
            raise VaultIngestError(
                "unsafe-output-artifact",
                "ingest output leaf changed while opening",
                stage="publication",
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        final = parent.lstat_leaf(path.name)
        if (
            len(data) > maximum
            or final is None
            or not safe(after)
            or not safe(final)
            or not stable(opened, after)
            or not stable(after, final)
            or after.st_size != len(data)
        ):
            raise VaultIngestError(
                "unsafe-output-artifact",
                "ingest output leaf changed while reading",
                stage="publication",
            )
        return final, data
    finally:
        os.close(descriptor)


def _write_ingest_artifact_locked(
    path: Path | str,
    payload: Mapping[str, Any],
    *,
    request: Path | str,
    home: Path | str | None = None,
) -> None:
    """Atomically write one closed plan/receipt to an existing pinned directory."""

    try:
        validated = validate_contract(dict(payload))
    except ContractError as error:
        raise VaultIngestError("invalid-output-artifact", str(error), stage="request") from error
    if validated.get("schema") not in {PLAN_SCHEMA, RECEIPT_SCHEMA}:
        raise VaultIngestError(
            "invalid-output-artifact",
            "only a vault ingest plan or receipt may be written",
            stage="request",
        )
    data = canonical_json(validated).encode("utf-8") + b"\n"
    if len(data) > MAX_RECEIPT_BYTES:
        raise VaultIngestError(
            "output-artifact-too-large", "ingest output exceeds the artifact bound"
        )
    destination = _absolute_lexical(path)
    input_value = _load_request(request)
    registry = load_registry(home, validate_vaults=False)
    if validated["request_sha256"] != input_value.request["request_sha256"]:
        raise VaultIngestError(
            "stale-output-artifact",
            "ingest artifact does not belong to the current request bytes",
            stage="publication",
        )
    protected_roots = [registry.home, *(item.path for item in registry.registrations)]
    query_parts = _portable_relative(
        input_value.request["query_report"]["path"], field="query report path"
    )
    query_path = input_value.root.joinpath(*query_parts)
    protected_files = tuple(
        item for item in (input_value.path, query_path) if item is not None
    )
    if any(_path_is_within(destination, root) for root in protected_roots) or any(
        os.path.normcase(os.fspath(destination))
        == os.path.normcase(os.fspath(_absolute_lexical(item)))
        for item in protected_files
    ):
        raise VaultIngestError(
            "unsafe-output-artifact",
            "ingest output must be outside every Vault, registry home, request, and query input",
            stage="publication",
        )
    payload_sha256 = _sha256(data)
    leaf_sha256 = _sha256(destination.name.encode("utf-8", errors="strict"))
    temporary = f".kgd-ingest-{leaf_sha256[:16]}-{payload_sha256[:16]}.tmp"

    def assert_inputs_current() -> None:
        current_input = _load_request(request)
        if (
            current_input.path is None
            or input_value.path is None
            or current_input.raw_sha256 != input_value.raw_sha256
            or current_input.request["request_sha256"]
            != input_value.request["request_sha256"]
        ):
            raise VaultIngestError(
                "stale-output-artifact",
                "ingest request bytes changed before output publication",
                stage="publication",
            )
        _query_report(current_input)

    assert_inputs_current()
    protected_metadata: list[os.stat_result] = []
    for protected, maximum in (
        (input_value.path, MAX_REQUEST_BYTES),
        (query_path, MAX_QUERY_REPORT_BYTES),
    ):
        assert protected is not None
        absolute = _absolute_lexical(protected)
        with _PinnedDirectory(absolute.parent) as protected_parent:
            metadata, _ = _read_pinned_output_leaf(
                protected_parent, absolute, maximum=maximum
            )
            if metadata is None:
                raise VaultIngestError(
                    "stale-output-artifact",
                    "ingest request or query input disappeared",
                    stage="publication",
                )
            protected_metadata.append(metadata)

    try:
        with _PinnedDirectory(destination.parent) as parent:
            temporary_metadata = parent.lstat_leaf(temporary)
            if temporary_metadata is not None:
                if (
                    not stat.S_ISREG(temporary_metadata.st_mode)
                    or _is_link_like(destination.parent / temporary, temporary_metadata)
                    or temporary_metadata.st_size > len(data)
                ):
                    raise VaultIngestError(
                        "unsafe-output-artifact",
                        "deterministic ingest output temporary is unsafe",
                        stage="publication",
                    )
                try:
                    stable_temporary, temporary_data = _read_pinned_output_leaf(
                        parent,
                        destination.parent / temporary,
                        maximum=len(data),
                        allowed_links=frozenset({1, 2}),
                    )
                except VaultIngestError:
                    raise
                assert stable_temporary is not None and temporary_data is not None
                current_destination = parent.lstat_leaf(destination.name)
                linked_install = bool(
                    current_destination is not None
                    and stat.S_ISREG(current_destination.st_mode)
                    and not _is_link_like(destination, current_destination)
                    and current_destination.st_nlink == 2
                    and stable_temporary.st_nlink == 2
                    and os.path.samestat(current_destination, stable_temporary)
                )
                if linked_install:
                    if temporary_data != data:
                        raise VaultIngestError(
                            "unsafe-output-artifact",
                            "linked ingest output temporary is not an exact reachable image",
                            stage="publication",
                        )
                elif (
                    stable_temporary.st_nlink != 1
                    or not data.startswith(temporary_data)
                ):
                    raise VaultIngestError(
                        "unsafe-output-artifact",
                        "ingest output temporary is not an exact payload prefix",
                        stage="publication",
                    )
                if not parent.cleanup_owned_leaf_raw(
                    temporary, stable_temporary
                ):
                    raise VaultIngestError(
                        "unsafe-output-artifact",
                        "ingest output temporary changed before exact cleanup",
                        stage="publication",
                    )
                if parent.lstat_leaf(temporary) is not None:
                    raise VaultIngestError(
                        "unsafe-output-artifact",
                        "ingest output temporary remained after exact cleanup",
                        stage="publication",
                    )

            current, current_data = _read_pinned_output_leaf(
                parent, destination, maximum=MAX_RECEIPT_BYTES
            )
            if current is not None and any(
                os.path.samestat(current, metadata)
                for metadata in protected_metadata
            ):
                raise VaultIngestError(
                    "unsafe-output-artifact",
                    "ingest output aliases a protected request or query input",
                    stage="publication",
                )
            if current is not None:
                if current_data != data:
                    raise VaultIngestError(
                        "output-exists",
                        "ingest output already exists with different bytes",
                        stage="publication",
                    )
                assert_inputs_current()
                parent.verify_current()
                return

            descriptor = parent.create_file(
                temporary, delete_access=True, readable=True
            )
            written: os.stat_result | None = None
            try:
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
                written = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(written.st_mode)
                    or written.st_nlink != 1
                    or written.st_size != len(data)
                ):
                    raise VaultIngestError(
                        "unsafe-output-artifact",
                        "ingest output temporary file failed validation",
                        stage="publication",
                    )
                parent.install_leaf_noreplace(
                    temporary,
                    destination.name,
                    descriptor,
                    expected_content=data,
                    before_install=assert_inputs_current,
                    after_install=lambda: (
                        _vault_ingest_hook(
                            "after-output-replace", os.fspath(destination)
                        ),
                        assert_inputs_current(),
                    ),
                )
            except BaseException:
                if written is None:
                    try:
                        written = os.fstat(descriptor)
                    except OSError:
                        pass
                os.close(descriptor)
                descriptor = -1
                if written is not None:
                    try:
                        parent.cleanup_owned_leaf_raw(temporary, written)
                    except OSError:
                        pass
                raise
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
    except (VaultIngestError, OSError, SourceArchiveError) as error:
        if isinstance(error, VaultIngestError):
            raise
        if isinstance(error, SourceArchiveError):
            raise VaultIngestError(
                "unsafe-output-artifact",
                error.message,
                stage="publication",
            ) from error
        if isinstance(error, FileExistsError):
            raise VaultIngestError(
                "output-exists",
                "ingest output appeared during no-clobber publication",
                stage="publication",
            ) from error
        raise VaultIngestError(
            "output-artifact-write-failed", str(error), stage="publication"
        ) from error


def write_ingest_artifact(
    path: Path | str,
    payload: Mapping[str, Any],
    *,
    request: Path | str,
    home: Path | str | None = None,
) -> None:
    """Write an external artifact while registry containment remains pinned."""

    with _closed_ingest_errors(stage="publication"):
        with vault_registry_read_guard(home):
            _write_ingest_artifact_locked(
                path, payload, request=request, home=home
            )


def preflight_ingest_output(
    path: Path | str,
    payload: Mapping[str, Any],
    *,
    request: Path | str,
    home: Path | str | None = None,
) -> None:
    """Read-only CLI apply preflight for an absent external receipt leaf."""

    with _closed_ingest_errors(stage="publication"):
        with vault_registry_read_guard(home):
            try:
                receipt = validate_contract(dict(payload))
            except ContractError as error:
                raise VaultIngestError(
                    "invalid-output-artifact", str(error), stage="request"
                ) from error
            if receipt.get("schema") != RECEIPT_SCHEMA:
                raise VaultIngestError(
                    "invalid-output-artifact",
                    "CLI apply output preflight requires a receipt",
                    stage="request",
                )
            data = _receipt_bytes(receipt)
            input_value = _load_request(request)
            _query_report(input_value)
            registry = load_registry(home, validate_vaults=False)
            destination = _absolute_lexical(path)
            query_parts = _portable_relative(
                input_value.request["query_report"]["path"],
                field="query report path",
            )
            query_path = input_value.root.joinpath(*query_parts)
            protected_roots = [
                registry.home,
                *(item.path for item in registry.registrations),
            ]
            protected_files = tuple(
                item for item in (input_value.path, query_path) if item is not None
            )
            if any(
                _path_is_within(destination, root) for root in protected_roots
            ) or any(
                os.path.normcase(os.fspath(destination))
                == os.path.normcase(os.fspath(_absolute_lexical(item)))
                for item in protected_files
            ):
                raise VaultIngestError(
                    "unsafe-output-artifact",
                    "external receipt output must be outside protected inputs and Vaults",
                    stage="publication",
                )
            protected_metadata: list[os.stat_result] = []
            for protected, maximum in (
                (input_value.path, MAX_REQUEST_BYTES),
                (query_path, MAX_QUERY_REPORT_BYTES),
            ):
                assert protected is not None
                absolute = _absolute_lexical(protected)
                with _PinnedDirectory(absolute.parent) as protected_parent:
                    metadata, _ = _read_pinned_output_leaf(
                        protected_parent, absolute, maximum=maximum
                    )
                    if metadata is None:
                        raise VaultIngestError(
                            "stale-output-artifact",
                            "ingest request or query input disappeared",
                            stage="publication",
                        )
                    protected_metadata.append(metadata)
            with _PinnedDirectory(destination.parent) as parent:
                metadata, current_data = _read_pinned_output_leaf(
                    parent, destination, maximum=MAX_RECEIPT_BYTES
                )
                if metadata is not None:
                    if any(
                        os.path.samestat(metadata, protected)
                        for protected in protected_metadata
                    ):
                        raise VaultIngestError(
                            "unsafe-output-artifact",
                            "external receipt output aliases a protected input",
                            stage="publication",
                        )
                    if current_data != data:
                        raise VaultIngestError(
                            "output-exists",
                            "external receipt output exists with different bytes",
                            stage="publication",
                        )
                parent.verify_current()


def _receipt_note_records(
    receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    records = tuple(dict(item) for item in receipt["changes"]["notes"])
    paths = [str(item["path"]) for item in records]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise VaultIngestError(
            "invalid-receipt",
            "receipt note image records must be uniquely sorted by path",
            stage="recovery",
        )
    return records


def _validated_receipt(data: bytes, *, expected_path: str) -> dict[str, Any]:
    payload = _strict_json(data, kind="vault-ingest-receipt")
    try:
        receipt = validate_contract(payload)
    except (ContractError, RecursionError) as error:
        raise VaultIngestError("invalid-receipt", str(error)) from error
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise VaultIngestError("invalid-receipt", f"expected {RECEIPT_SCHEMA}")
    _receipt_note_records(receipt)
    expected = receipt_relative_path(str(receipt["receipt_sha256"]))
    if expected != expected_path or data != _receipt_bytes(receipt):
        raise VaultIngestError(
            "invalid-receipt", "stored receipt path or bytes are not canonical"
        )
    return receipt


def _capture_receipt_inventory(
    vault: Vault, *, matching_request_id: str | None
) -> tuple[tuple[tuple[str, str], ...], tuple[dict[str, Any], ...]]:
    root = vault.root / ".kgdistiller" / "receipts" / "sha256"
    try:
        pinned_root = _PinnedDirectory(root)
    except SourceArchiveError as error:
        if error.code == "missing-ledger-artifact":
            return (), ()
        raise VaultIngestError("invalid-receipt-store", error.message) from error
    receipts: list[dict[str, Any]] = []
    token: list[tuple[str, str]] = []
    receipt_count = 0
    receipt_bytes = 0
    with pinned_root:
        try:
            first_names: list[str] = []
            with os.scandir(
                root if os.name == "nt" else pinned_root.dir_fd
            ) as scanner:
                for entry in scanner:
                    first_names.append(entry.name)
                    if len(first_names) > MAX_RECEIPT_SHARDS:
                        raise VaultIngestError(
                            "receipt-store-too-large",
                            f"receipt store exceeds {MAX_RECEIPT_SHARDS} shards",
                        )
        except OSError as error:
            raise VaultIngestError(
                "invalid-receipt-store", "cannot enumerate receipt store"
            ) from error
        for first in sorted(first_names):
            if not re.fullmatch(r"[0-9a-f]{2}", first):
                raise VaultIngestError(
                    "invalid-receipt-store", "receipt store contains an unsafe entry"
                )
            token.append((f"{first}/", "directory"))
            metadata = pinned_root.lstat_leaf(first)
            directory = root / first
            if metadata is None or not stat.S_ISDIR(metadata.st_mode):
                raise VaultIngestError(
                    "invalid-receipt-store", "receipt shard is not an ordinary directory"
                )
            try:
                pinned = _PinnedDirectory(directory)
            except SourceArchiveError as error:
                raise VaultIngestError("invalid-receipt-store", error.message) from error
            with pinned:
                try:
                    names: list[str] = []
                    with os.scandir(
                        directory if os.name == "nt" else pinned.dir_fd
                    ) as scanner:
                        for entry in scanner:
                            receipt_count += 1
                            if receipt_count > MAX_RECEIPTS:
                                raise VaultIngestError(
                                    "receipt-store-too-large",
                                    f"receipt store exceeds {MAX_RECEIPTS} files",
                                )
                            names.append(entry.name)
                except OSError as error:
                    raise VaultIngestError(
                        "invalid-receipt-store", "cannot enumerate receipt shard"
                    ) from error
                for name in sorted(names):
                    match = re.fullmatch(r"([0-9a-f]{64})\.json", name)
                    if match is None or not match.group(1).startswith(first):
                        raise VaultIngestError(
                            "invalid-receipt-store", "receipt shard contains an unsafe entry"
                        )
                    relative = (
                        f".kgdistiller/receipts/sha256/{first}/{name}"
                    )
                    data = read_vault_relative_regular(
                        vault, relative, maximum=MAX_RECEIPT_BYTES
                    )
                    receipt_bytes += len(data)
                    if receipt_bytes > MAX_RECEIPT_INVENTORY_BYTES:
                        raise VaultIngestError(
                            "receipt-store-too-large",
                            "receipt store exceeds its total byte bound",
                        )
                    receipt = _validated_receipt(
                        data, expected_path=relative
                    )
                    if receipt["vault_id"] != vault.id:
                        raise VaultIngestError(
                            "invalid-receipt-store",
                            "receipt store contains a receipt for another Vault",
                        )
                    token.append((f"{first}/{name}", _sha256(data)))
                    if (
                        receipt["request_id"] == matching_request_id
                        and len(receipts) < 2
                    ):
                        receipts.append(receipt)
                pinned.verify_current()
            pinned_root.verify_current()
        pinned_root.verify_current()
    return tuple(token), tuple(receipts)


def _receipt_inventory(
    vault: Vault, *, matching_request_id: str | None = None
) -> tuple[dict[str, Any], ...]:
    first, _ = _capture_receipt_inventory(
        vault, matching_request_id=matching_request_id
    )
    _vault_ingest_hook("between-receipt-inventory-scans", "")
    second, receipts = _capture_receipt_inventory(
        vault, matching_request_id=matching_request_id
    )
    if first != second:
        raise VaultIngestError(
            "stale-receipt-store",
            "receipt store changed while taking a bounded inventory",
            stage="recovery",
        )
    return receipts


def _existing_receipt(
    vault: Vault, request: Mapping[str, Any]
) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for receipt in _receipt_inventory(
        vault, matching_request_id=str(request["request_id"])
    ):
        if receipt["request_id"] != request["request_id"]:
            continue
        if receipt["request_sha256"] != request["request_sha256"]:
            raise VaultIngestError(
                "request-id-conflict",
                "request_id is already bound to different canonical request content",
            )
        if found is not None and found["receipt_sha256"] != receipt["receipt_sha256"]:
            raise VaultIngestError(
                "ambiguous-request-receipt",
                "more than one receipt is bound to the same request",
            )
        found = receipt
    return found


def _historical_receipt_ledger_generation(
    vault: Vault, receipt: Mapping[str, Any]
) -> str | None:
    updates = [dict(item) for item in receipt["after"]["derivations"]]
    changed_ids = list(receipt["changes"]["derivation_version_ids"])
    if changed_ids != sorted(str(item["version_id"]) for item in updates):
        raise VaultIngestError(
            "invalid-receipt",
            "receipt derivation summary does not match its changed version inventory",
            stage="recovery",
        )
    before_generation = receipt["before"]["source_ledger_generation_sha256"]
    if not updates:
        if before_generation is not None:
            load_source_ledger_generation(vault, str(before_generation))
        return None if before_generation is None else str(before_generation)
    if before_generation is None:
        raise VaultIngestError(
            "invalid-receipt",
            "receipt with derivation changes has no source-ledger before generation",
            stage="recovery",
        )
    try:
        before = load_source_ledger_generation(vault, str(before_generation))
        reconstructed = prepare_derivation_generation(
            before,
            updates,
            graph_generation_sha256=str(
                receipt["after"]["graph_generation_sha256"]
            ),
            ingest_receipt_sha256=str(receipt["receipt_sha256"]),
        )
        installed = load_source_ledger_generation(
            vault, str(reconstructed.ledger.generation_sha256)
        )
    except SourceArchiveError as error:
        raise VaultIngestError(
            "invalid-receipt-history", error.message, stage="recovery"
        ) from error
    if (
        installed.documents != reconstructed.ledger.documents
        or installed.versions != reconstructed.ledger.versions
        or installed.derivations != reconstructed.ledger.derivations
    ):
        raise VaultIngestError(
            "invalid-receipt-history",
            "installed historical source generation differs from its receipt",
            stage="recovery",
        )
    return installed.generation_sha256


@dataclass(frozen=True)
class _Target:
    path: str
    old: bytes | None
    new: bytes | None
    backup_path: str | None
    staged_path: str | None
    temporary_path: str


@dataclass(frozen=True)
class _RecordedTarget:
    path: str
    old_bytes: int | None
    old_sha256: str | None
    new_bytes: int | None
    new_sha256: str | None
    backup_path: str | None
    staged_path: str | None
    temporary_path: str


_AnyTarget = Union[_Target, _RecordedTarget]


def _live_temporary_path(stage_name: str, index: int, target_path: str) -> str:
    if not _STAGE_RE.fullmatch(stage_name) or not 0 <= index < 100_512:
        raise VaultIngestError(
            "invalid-transaction-stage", "transaction temporary identity is invalid"
        )
    parent = PurePosixPath(target_path).parent
    temporary_leaf = (
        f".kgd-vault-ingest-{stage_name.removeprefix('.stage-vault-ingest-')}"
        f"-{index:06d}.tmp"
    )
    result = (
        temporary_leaf
        if parent == PurePosixPath(".")
        else (parent / temporary_leaf).as_posix()
    )
    try:
        _portable_relative(result, field="transaction live temporary")
    except SourceArchiveError as error:
        raise VaultIngestError(
            "invalid-transaction-stage", error.message
        ) from error
    return result


def _allocate_transaction_stage(vault: Vault) -> Path:
    build = vault.root / ".kgdistiller" / "build"
    try:
        with _PinnedDirectory(build) as parent:
            for _ in range(32):
                name = f".stage-vault-ingest-{uuid.uuid4().hex}"
                try:
                    parent.mkdir_leaf(name)
                except FileExistsError:
                    continue
                if os.name != "nt":
                    os.fsync(parent.dir_fd)
                return build / name
    except SourceArchiveError as error:
        raise VaultIngestError("invalid-transaction-stage", error.message) from error
    raise VaultIngestError(
        "transaction-stage-exhausted", "cannot allocate Vault ingest transaction stage"
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    with _PinnedDirectory(path) as pinned:
        os.fsync(pinned.dir_fd)


def _fsync_transaction_stage(stage: Path) -> None:
    """Durably order stage directory entries before the prepared journal."""

    if os.name == "nt":
        return
    for directory in (stage / "backup", stage / "new", stage, stage.parent):
        try:
            _fsync_directory(directory)
        except SourceArchiveError as error:
            if error.code != "missing-ledger-artifact":
                raise


def _fsync_created_directories(
    vault: Vault, directories: Sequence[Mapping[str, str]]
) -> None:
    """Durably order newly published live directory chains before commit."""

    if os.name == "nt":
        return
    paths = {
        vault.root.joinpath(
            *_portable_relative(str(item["path"]), field="created directory")
        )
        for item in directories
    }
    for directory in sorted(paths, key=lambda item: (-len(item.parts), os.fspath(item))):
        _fsync_directory(directory)
    for parent in sorted(
        {item.parent for item in paths},
        key=lambda item: (-len(item.parts), os.fspath(item)),
    ):
        _fsync_directory(parent)


def _read_optional(vault: Vault, path: str, *, maximum: int) -> bytes | None:
    try:
        return read_vault_relative_regular(vault, path, maximum=max(1, maximum))
    except SourceArchiveError as error:
        if error.code in {"missing-vault-file", "missing-ledger-artifact"}:
            return None
        raise VaultIngestError("invalid-transaction-target", error.message) from error


def _target_limit(path: str) -> int:
    if path.endswith(".md") and not path.startswith(".kgdistiller/"):
        return MAX_NOTE_BYTES
    if path == ".kgdistiller/sources/manifest.json":
        return 1024 * 1024
    if path.startswith(".kgdistiller/receipts/"):
        return MAX_RECEIPT_BYTES
    if path.startswith(".kgdistiller/graph/"):
        return _native_graph_artifact_limit(
            path.removeprefix(".kgdistiller/graph/")
        )
    return MAX_GRAPH_BYTES


def _desired_targets(
    prepared: _Prepared,
    source: PreparedSourceGeneration | None,
    compilation: NativeCompilation,
    receipt: Mapping[str, Any],
) -> dict[str, bytes | None]:
    desired: dict[str, bytes | None] = dict(prepared.overlay)
    if source is not None:
        desired[".kgdistiller/sources/manifest.json"] = canonical_json(
            source.manifest
        ).encode("utf-8")
    graph = _expected_bytes(compilation)
    for name in set(prepared.graph_before) | set(graph):
        desired[f".kgdistiller/graph/{name}"] = graph.get(name)
    receipt_path = receipt_relative_path(str(receipt["receipt_sha256"]))
    desired[receipt_path] = _receipt_bytes(receipt)
    return desired


def _known_before(prepared: _Prepared) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {
        item.authority: item.data for item in prepared.snapshots
    }
    result.update(
        {
            f".kgdistiller/graph/{name}": data
            for name, data in prepared.graph_before.items()
        }
    )
    if prepared.ledger.manifest is not None:
        result[".kgdistiller/sources/manifest.json"] = canonical_json(
            prepared.ledger.manifest
        ).encode("utf-8")
    return result


def _stage_targets(
    vault: Vault,
    stage: Path,
    desired: Mapping[str, bytes | None],
    known_before: Mapping[str, bytes | None],
) -> tuple[_Target, ...]:
    if not 1 <= len(desired) <= MAX_TRANSACTION_TARGETS:
        raise VaultIngestError(
            "transaction-too-large", "transaction target count is outside its bound"
        )
    stage_relative = stage.relative_to(vault.root).as_posix()
    targets: list[_Target] = []
    total_images = 0
    for index, path in enumerate(sorted(desired)):
        _portable_relative(path, field="transaction target")
        temporary_path = _live_temporary_path(stage.name, index, path)
        maximum = _target_limit(path)
        old = _read_optional(vault, path, maximum=maximum)
        if path in known_before and old != known_before[path]:
            raise VaultIngestError(
                "stale-vault-state", f"transaction target changed before staging: {path}"
            )
        if path not in known_before and old is not None:
            raise VaultIngestError(
                "unexpected-transaction-target", f"unexpected live target exists: {path}"
            )
        new = desired[path]
        total_images += len(old or b"") + len(new or b"")
        if total_images > MAX_TRANSACTION_IMAGE_BYTES:
            raise VaultIngestError(
                "transaction-too-large",
                "transaction images exceed the total transaction bound",
            )
        backup_path = None
        staged_path = None
        if old is not None:
            backup_path = f"{stage_relative}/backup/{index:06d}"
            replace_vault_relative_regular(
                vault, backup_path, old, maximum=max(1, maximum)
            )
        if new is not None:
            if len(new) > maximum:
                raise VaultIngestError(
                    "transaction-target-too-large", f"transaction target is too large: {path}"
                )
            staged_path = f"{stage_relative}/new/{index:06d}"
            replace_vault_relative_regular(
                vault, staged_path, new, maximum=max(1, maximum)
            )
        targets.append(
            _Target(path, old, new, backup_path, staged_path, temporary_path)
        )
    for target in targets:
        if target.backup_path is not None and _read_optional(
            vault, target.backup_path, maximum=max(1, len(target.old or b""))
        ) != target.old:
            raise VaultIngestError(
                "invalid-transaction-stage", "transaction backup changed during staging"
            )
        if target.staged_path is not None and _read_optional(
            vault, target.staged_path, maximum=max(1, len(target.new or b""))
        ) != target.new:
            raise VaultIngestError(
                "invalid-transaction-stage", "transaction new file changed during staging"
            )
    return tuple(targets)


def _target_record(target: _Target) -> dict[str, Any]:
    return {
        "path": target.path,
        "existed": target.old is not None,
        "old_bytes": None if target.old is None else len(target.old),
        "old_sha256": None if target.old is None else _sha256(target.old),
        "new_bytes": None if target.new is None else len(target.new),
        "new_sha256": None if target.new is None else _sha256(target.new),
        "backup_path": target.backup_path,
        "staged_path": target.staged_path,
        "temporary_path": target.temporary_path,
    }


def _validate_target_image_bounds(
    records: Sequence[Mapping[str, Any]],
    *,
    code: str,
    stage: str,
) -> tuple[str, ...]:
    if not 1 <= len(records) <= MAX_TRANSACTION_TARGETS:
        raise VaultIngestError(
            code, "transaction target count is outside its bound", stage=stage
        )
    paths: list[str] = []
    total_images = 0
    for record in records:
        path = str(record["path"])
        try:
            _portable_relative(path, field="transaction target")
        except SourceArchiveError as error:
            raise VaultIngestError(code, error.message, stage=stage) from error
        paths.append(path)
        limit = _target_limit(path)
        for field in ("old_bytes", "new_bytes"):
            size = record[field]
            if size is None:
                continue
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or size > limit
            ):
                raise VaultIngestError(
                    code,
                    f"transaction image exceeds the target-specific bound: {path}",
                    stage=stage,
                )
            total_images += size
            if total_images > MAX_TRANSACTION_IMAGE_BYTES:
                raise VaultIngestError(
                    code,
                    "transaction images exceed the total transaction bound",
                    stage=stage,
                )
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise VaultIngestError(
            code, "transaction targets are not in canonical path order", stage=stage
        )
    return tuple(paths)


def _missing_parent_directories(vault: Vault, path: str) -> tuple[str, ...]:
    parts = _portable_relative(path, field="transaction target")[:-1]
    current = vault.root
    missing = False
    result: list[str] = []
    for index, part in enumerate(parts):
        candidate = current / part
        relative = PurePosixPath(*parts[: index + 1]).as_posix()
        if missing:
            result.append(relative)
            current = candidate
            continue
        try:
            with _PinnedDirectory(current) as parent:
                metadata = parent.lstat_leaf(part)
        except SourceArchiveError as error:
            raise VaultIngestError(
                "invalid-transaction-target", error.message
            ) from error
        if metadata is None:
            missing = True
            result.append(relative)
        elif not stat.S_ISDIR(metadata.st_mode):
            raise VaultIngestError(
                "invalid-transaction-target",
                f"transaction target parent is not an ordinary directory: {relative}",
            )
        else:
            try:
                with _PinnedDirectory(candidate):
                    pass
            except SourceArchiveError as error:
                raise VaultIngestError(
                    "invalid-transaction-target", error.message
                ) from error
        current = candidate
    return tuple(result)


def _planned_directories(
    vault: Vault, desired: Mapping[str, bytes | None]
) -> tuple[str, ...]:
    directories = {
        directory
        for path, data in desired.items()
        if data is not None
        for directory in _missing_parent_directories(vault, path)
    }
    if len(directories) > MAX_CREATED_DIRECTORIES:
        raise VaultIngestError(
            "transaction-too-large", "transaction creates too many directories"
        )
    return tuple(sorted(directories, key=lambda item: (len(PurePosixPath(item).parts), item)))


def _create_planned_directories(
    vault: Vault,
    journal: Mapping[str, Any],
    *,
    failure_injector: FailureInjector | None,
) -> dict[str, Any]:
    """Create each live parent by no-clobber and durably record ownership.

    The journal starts with an empty ``created_directories`` list.  A directory
    becomes rollback-owned only after its exact creation has been followed by a
    durable journal rewrite.  A crash in that narrow interval may leave a
    harmless empty directory, but recovery never guesses ownership and can
    therefore never delete a directory won by another actor.
    """

    current = dict(journal)
    created = [dict(item) for item in current["created_directories"]]
    created_paths = {str(item["path"]) for item in created}
    for relative in current["planned_directories"]:
        if relative in created_paths:
            continue
        parts = _portable_relative(relative, field="planned transaction directory")
        parent_path = vault.root.joinpath(*parts[:-1])
        try:
            with _PinnedDirectory(parent_path) as parent:
                try:
                    parent.mkdir_leaf(parts[-1])
                except FileExistsError as error:
                    raise VaultIngestError(
                        "concurrent-directory-change",
                        "planned transaction directory appeared before publication: "
                        + relative,
                        stage="publication",
                    ) from error
                created_metadata = parent.lstat_leaf(parts[-1])
                if created_metadata is None or not stat.S_ISDIR(
                    created_metadata.st_mode
                ):
                    raise VaultIngestError(
                        "unsafe-transaction-directory",
                        "created transaction directory is not an ordinary directory: "
                        + relative,
                        stage="publication",
                    )
                with _PinnedDirectory(parent_path / parts[-1]) as child:
                    if os.name != "nt":
                        os.fsync(child.dir_fd)
                parent.verify_current()
                if os.name != "nt":
                    os.fsync(parent.dir_fd)
                final_metadata = parent.lstat_leaf(parts[-1])
                if (
                    final_metadata is None
                    or not stat.S_ISDIR(final_metadata.st_mode)
                    or not os.path.samestat(created_metadata, final_metadata)
                ):
                    raise VaultIngestError(
                        "unsafe-transaction-directory",
                        "created transaction directory changed before ownership recording: "
                        + relative,
                        stage="publication",
                    )
        except VaultIngestError:
            raise
        except (OSError, SourceArchiveError) as error:
            raise VaultIngestError(
                "transaction-directory-create-failed",
                f"cannot safely create planned transaction directory: {relative}",
                stage="publication",
            ) from error
        _invoke(
            failure_injector,
            "after-directory-create-before-journal",
            relative,
        )
        identity = {
            "path": relative,
            "device": str(final_metadata.st_dev),
            "inode": str(final_metadata.st_ino),
        }
        candidate = finalize_self_digest(
            {**current, "created_directories": [*created, identity]},
            "journal_sha256",
        )
        validate_contract(candidate)
        _write_journal(vault, candidate)
        current = candidate
        created.append(identity)
        created_paths.add(relative)
    return current


def _journal_payload(
    prepared: _Prepared,
    source: PreparedSourceGeneration | None,
    receipt: Mapping[str, Any],
    stage: Path,
    targets: Sequence[_Target],
    planned_directories: Sequence[str],
    created_directories: Sequence[Mapping[str, str]],
    *,
    state: str,
) -> dict[str, Any]:
    after_ledger = (
        prepared.ledger.generation_sha256
        if source is None
        else source.ledger.generation_sha256
    )
    target_records = [_target_record(item) for item in targets]
    _validate_target_image_bounds(
        target_records, code="invalid-generated-journal", stage="publication"
    )
    payload = finalize_self_digest(
        {
            "schema": JOURNAL_SCHEMA,
            "journal_sha256": "0" * 64,
            "state": state,
            "vault_id": prepared.vault.id,
            "request_id": prepared.input.request["request_id"],
            "request_sha256": prepared.input.request["request_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "stage": stage.name,
            "registry_generation": prepared.input.request["registry_generation"],
            "vault_manifest_sha256": prepared.input.request["vault_manifest_sha256"],
            "before": {
                "source_ledger_generation_sha256": prepared.ledger.generation_sha256,
                "graph_generation_sha256": prepared.graph_before_generation,
                "note_inventory_sha256": prepared.note_before_sha256,
            },
            "after": {
                "source_ledger_generation_sha256": after_ledger,
                "graph_generation_sha256": prepared.compilation.state.manifest[
                    "graph_sha256"
                ],
                "note_inventory_sha256": prepared.note_after_sha256,
            },
            "planned_directories": list(planned_directories),
            "created_directories": [dict(item) for item in created_directories],
            "targets": target_records,
        },
        "journal_sha256",
    )
    validate_contract(payload)
    return payload


def _journal_bytes(payload: Mapping[str, Any]) -> bytes:
    data = canonical_json(dict(payload)).encode("utf-8")
    if len(data) > MAX_JOURNAL_BYTES:
        raise VaultIngestError(
            "journal-too-large", f"transaction journal exceeds {MAX_JOURNAL_BYTES} bytes"
        )
    return data


def _write_journal(vault: Vault, payload: Mapping[str, Any]) -> None:
    replace_vault_relative_regular(
        vault,
        JOURNAL_PATH,
        _journal_bytes(payload),
        maximum=MAX_JOURNAL_BYTES,
        after_replace=lambda: _vault_ingest_hook(
            f"after-{payload['state']}-journal-replace", JOURNAL_PATH
        ),
    )


def _managed_note_target_allowed(vault: Vault, path: str) -> bool:
    candidate = vault.root.joinpath(*PurePosixPath(path).parts)
    return any(
        root in candidate.parents
        for root in (vault.concept_root, vault.field_root, vault.topic_root)
    ) and path.endswith(".md")


def _receipt_note_paths(
    vault: Vault, receipt: Mapping[str, Any]
) -> tuple[str, ...]:
    note_paths = tuple(
        str(record["path"]) for record in _receipt_note_records(receipt)
    )
    if any(not _managed_note_target_allowed(vault, path) for path in note_paths):
        raise VaultIngestError(
            "invalid-journal",
            "receipt note changes are outside the managed native-note authorities",
            stage="recovery",
        )
    return note_paths


def _journal_target_allowed(vault: Vault, path: str, receipt_sha256: str) -> bool:
    if path in {
        ".kgdistiller/sources/manifest.json",
        receipt_relative_path(receipt_sha256),
    }:
        return True
    if path.startswith(".kgdistiller/graph/"):
        name = path.removeprefix(".kgdistiller/graph/")
        return name in {
            "manifest.json",
            "sources.json",
            "nodes.jsonl",
            "edges.jsonl",
            "references.jsonl",
            "diagnostics.json",
        } or name.startswith("entries/")
    return _managed_note_target_allowed(vault, path)


def _created_directory_allowed(
    vault: Vault, directory: str, target_parent_directories: set[str]
) -> bool:
    try:
        _portable_relative(directory, field="journal created directory")
    except SourceArchiveError:
        return False
    prefixes = [
        vault.concept_root.relative_to(vault.root).as_posix(),
        vault.field_root.relative_to(vault.root).as_posix(),
        vault.topic_root.relative_to(vault.root).as_posix(),
    ]
    below_managed = any(directory.startswith(prefix + "/") for prefix in prefixes)
    graph_directory = directory == ".kgdistiller/graph" or directory.startswith(
        ".kgdistiller/graph/"
    )
    receipt_directory = directory in {
        ".kgdistiller/receipts",
        ".kgdistiller/receipts/sha256",
    } or bool(re.fullmatch(r"\.kgdistiller/receipts/sha256/[0-9a-f]{2}", directory))
    return (
        below_managed or graph_directory or receipt_directory
    ) and directory in target_parent_directories


def _load_journal(vault: Vault) -> dict[str, Any] | None:
    data = _read_optional(vault, JOURNAL_PATH, maximum=MAX_JOURNAL_BYTES)
    if data is None:
        return None
    payload = _strict_json(data, kind="vault-ingest-journal")
    try:
        journal = validate_contract(payload)
    except (ContractError, RecursionError) as error:
        raise VaultIngestError(
            "invalid-journal", str(error), stage="recovery"
        ) from error
    if journal.get("schema") != JOURNAL_SCHEMA or data != _journal_bytes(journal):
        raise VaultIngestError(
            "invalid-journal", "transaction journal is not canonical", stage="recovery"
        )
    if journal["vault_id"] != vault.id or not _STAGE_RE.fullmatch(journal["stage"]):
        raise VaultIngestError(
            "invalid-journal", "transaction journal belongs to another Vault", stage="recovery"
        )
    target_paths = _validate_target_image_bounds(
        journal["targets"], code="invalid-journal", stage="recovery"
    )
    seen = set(target_paths)
    stage_prefix = f".kgdistiller/build/{journal['stage']}"
    for index, record in enumerate(journal["targets"]):
        path = str(record["path"])
        if not _journal_target_allowed(
            vault, path, str(journal["receipt_sha256"])
        ):
            raise VaultIngestError(
                "invalid-journal", "journal contains an unmanaged target", stage="recovery"
            )
        expected_backup = (
            f"{stage_prefix}/backup/{index:06d}" if record["existed"] else None
        )
        expected_staged = (
            f"{stage_prefix}/new/{index:06d}"
            if record["new_sha256"] is not None
            else None
        )
        expected_temporary = _live_temporary_path(
            str(journal["stage"]), index, path
        )
        if (
            record["backup_path"] != expected_backup
            or record["staged_path"] != expected_staged
            or record["temporary_path"] != expected_temporary
        ):
            raise VaultIngestError(
                "invalid-journal", "journal staging identity does not match target order", stage="recovery"
            )
        if record["existed"] != (record["old_sha256"] is not None) or (
            record["old_sha256"] is None
        ) != (record["old_bytes"] is None):
            raise VaultIngestError(
                "invalid-journal", "journal before-image metadata is inconsistent", stage="recovery"
            )
        if (record["new_sha256"] is None) != (record["new_bytes"] is None):
            raise VaultIngestError(
                "invalid-journal", "journal new-image metadata is inconsistent", stage="recovery"
            )
    records = {str(item["path"]): item for item in journal["targets"]}
    graph_manifest = records.get(".kgdistiller/graph/manifest.json")
    if graph_manifest is None or graph_manifest["new_sha256"] is None:
        raise VaultIngestError(
            "invalid-journal",
            "journal has no new graph manifest",
            stage="recovery",
        )

    def graph_names(field_prefix: str) -> set[str]:
        path_field = "backup_path" if field_prefix == "old" else "staged_path"
        data = _recorded_bytes(
            vault,
            graph_manifest[path_field],
            graph_manifest[f"{field_prefix}_bytes"],
            graph_manifest[f"{field_prefix}_sha256"],
        )
        if data is None:
            return set()
        manifest = _strict_json(data, kind="journal-graph-manifest")
        try:
            return {
                f".kgdistiller/graph/{name}"
                for name in _manifest_artifact_names(manifest)
            }
        except NativeCompilerError as error:
            raise VaultIngestError(
                "invalid-journal", error.message, stage="recovery"
            ) from error

    expected_graph_targets = graph_names("old") | graph_names("new")
    actual_graph_targets = {
        path for path in records if path.startswith(".kgdistiller/graph/")
    }
    if actual_graph_targets != expected_graph_targets:
        raise VaultIngestError(
            "invalid-journal",
            "journal graph targets do not match old/new manifest inventories",
            stage="recovery",
        )
    receipt_path = receipt_relative_path(str(journal["receipt_sha256"]))
    receipt_record = records.get(receipt_path)
    if (
        receipt_record is None
        or receipt_record["old_sha256"] is not None
        or receipt_record["new_sha256"] is None
    ):
        raise VaultIngestError(
            "invalid-journal",
            "journal does not install its immutable content-addressed receipt",
            stage="recovery",
        )
    receipt_bytes = _recorded_bytes(
        vault,
        receipt_record["staged_path"],
        receipt_record["new_bytes"],
        receipt_record["new_sha256"],
    )
    assert receipt_bytes is not None
    receipt = _validated_receipt(receipt_bytes, expected_path=receipt_path)
    if (
        receipt["vault_id"] != journal["vault_id"]
        or receipt["request_id"] != journal["request_id"]
        or receipt["request_sha256"] != journal["request_sha256"]
        or receipt["receipt_sha256"] != journal["receipt_sha256"]
        or receipt["before"]["registry_generation"]
        != journal["registry_generation"]
        or receipt["before"]["vault_manifest_sha256"]
        != journal["vault_manifest_sha256"]
        or receipt["before"]["source_ledger_generation_sha256"]
        != journal["before"]["source_ledger_generation_sha256"]
        or receipt["before"]["graph_generation_sha256"]
        != journal["before"]["graph_generation_sha256"]
        or receipt["before"]["note_inventory_sha256"]
        != journal["before"]["note_inventory_sha256"]
        or receipt["after"]["graph_generation_sha256"]
        != journal["after"]["graph_generation_sha256"]
        or receipt["after"]["note_inventory_sha256"]
        != journal["after"]["note_inventory_sha256"]
    ):
        raise VaultIngestError(
            "invalid-journal",
            "journal bindings do not match its canonical receipt",
            stage="recovery",
        )
    note_records = _receipt_note_records(receipt)
    note_paths = _receipt_note_paths(vault, receipt)
    for note in note_records:
        record = records.get(str(note["path"]))
        if (
            record is None
            or record["old_sha256"] != note["before_raw_sha256"]
            or record["new_sha256"] != note["after_raw_sha256"]
        ):
            raise VaultIngestError(
                "invalid-journal",
                "journal note images do not match its immutable receipt",
                stage="recovery",
            )
    expected_after_source = _historical_receipt_ledger_generation(vault, receipt)
    if journal["after"]["source_ledger_generation_sha256"] != expected_after_source:
        raise VaultIngestError(
            "invalid-journal",
            "journal after source generation is not the receipt-determined projection",
            stage="recovery",
        )
    expected_targets = {
        *expected_graph_targets,
        receipt_path,
        *note_paths,
    }
    before_source = journal["before"]["source_ledger_generation_sha256"]
    after_source = journal["after"]["source_ledger_generation_sha256"]
    if before_source != after_source:
        expected_targets.add(".kgdistiller/sources/manifest.json")
    if set(records) != expected_targets:
        raise VaultIngestError(
            "invalid-journal",
            "journal targets do not exactly match its receipt and old/new manifests",
            stage="recovery",
        )
    target_parent_directories: set[str] = set()
    for path in seen:
        prefix = ""
        for part in PurePosixPath(path).parts[:-1]:
            prefix = part if not prefix else f"{prefix}/{part}"
            target_parent_directories.add(prefix)
    planned = journal["planned_directories"]
    created = journal["created_directories"]
    created_paths = [str(item["path"]) for item in created]
    expected_planned = sorted(
        planned, key=lambda item: (len(PurePosixPath(item).parts), item)
    )
    if (
        planned != expected_planned
        or len(planned) != len(set(planned))
        or any(
            not _created_directory_allowed(
                vault, item, target_parent_directories
            )
            for item in planned
        )
        or created_paths != planned[: len(created_paths)]
    ):
        raise VaultIngestError(
            "invalid-journal",
            "journal planned-directory inventory is unsafe",
            stage="recovery",
        )
    if len(created_paths) != len(set(created_paths)):
        raise VaultIngestError(
            "invalid-journal",
            "journal created-directory inventory is unsafe",
            stage="recovery",
        )
    return journal


def _recorded_bytes(
    vault: Vault,
    path: str | None,
    size: int | None,
    digest: str | None,
) -> bytes | None:
    if path is None:
        if size is not None or digest is not None:
            raise VaultIngestError(
                "invalid-journal", "journal image metadata is inconsistent", stage="recovery"
            )
        return None
    if size is None or digest is None:
        raise VaultIngestError(
            "invalid-journal", "journal image metadata is incomplete", stage="recovery"
        )
    data = _read_optional(vault, path, maximum=max(1, size))
    if data is None or len(data) != size or _sha256(data) != digest:
        raise VaultIngestError(
            "invalid-journal", "journal staged image bytes changed", stage="recovery"
        )
    return data


def _targets_from_journal(
    vault: Vault, journal: Mapping[str, Any]
) -> tuple[_RecordedTarget, ...]:
    targets: list[_RecordedTarget] = []
    for record in journal["targets"]:
        old = _recorded_bytes(
            vault,
            record["backup_path"],
            record["old_bytes"],
            record["old_sha256"],
        )
        del old
        new = _recorded_bytes(
            vault,
            record["staged_path"],
            record["new_bytes"],
            record["new_sha256"],
        )
        del new
        targets.append(
            _RecordedTarget(
                str(record["path"]),
                record["old_bytes"],
                record["old_sha256"],
                record["new_bytes"],
                record["new_sha256"],
                record["backup_path"],
                record["staged_path"],
                str(record["temporary_path"]),
            )
        )
    return tuple(targets)


def _target_image_bytes(
    vault: Vault, target: _AnyTarget, *, side: str
) -> bytes | None:
    if side not in {"old", "new"}:
        raise AssertionError(f"invalid target image side: {side}")
    if isinstance(target, _Target):
        return target.old if side == "old" else target.new
    return _recorded_bytes(
        vault,
        target.backup_path if side == "old" else target.staged_path,
        target.old_bytes if side == "old" else target.new_bytes,
        target.old_sha256 if side == "old" else target.new_sha256,
    )


def _classify_live_target(
    vault: Vault, target: _AnyTarget
) -> tuple[bool, bool]:
    data = _read_optional(vault, target.path, maximum=_target_limit(target.path))
    if isinstance(target, _Target):
        result = (data == target.old, data == target.new)
    elif data is None:
        result = (target.old_sha256 is None, target.new_sha256 is None)
    else:
        size = len(data)
        digest = _sha256(data)
        result = (
            target.old_sha256 is not None
            and target.old_bytes == size
            and target.old_sha256 == digest,
            target.new_sha256 is not None
            and target.new_bytes == size
            and target.new_sha256 == digest,
        )
    del data
    return result


def _target_matches_side(vault: Vault, target: _AnyTarget, *, side: str) -> bool:
    matches_old, matches_new = _classify_live_target(vault, target)
    return matches_old if side == "old" else matches_new


def _validate_controlled_image(
    vault: Vault,
    journal: Mapping[str, Any],
    targets: Sequence[_AnyTarget],
    *,
    side: str,
) -> None:
    """Fully hydrate transaction-controlled state before destroying recovery data."""

    if side not in {"before", "after"}:
        raise AssertionError(f"invalid transaction image side: {side}")
    image_side = "old" if side == "before" else "new"
    for target in targets:
        if not _target_matches_side(vault, target, side=image_side):
            raise VaultIngestError(
                "transaction-image-mismatch",
                f"{side} transaction image changed: {target.path}",
                stage="recovery",
            )

    receipt_path = receipt_relative_path(str(journal["receipt_sha256"]))
    receipt_target = next(
        (target for target in targets if target.path == receipt_path), None
    )
    if receipt_target is None:
        raise VaultIngestError(
            "invalid-journal", "transaction receipt image is missing", stage="recovery"
        )
    receipt_bytes = _target_image_bytes(vault, receipt_target, side="new")
    if receipt_bytes is None:
        raise VaultIngestError(
            "invalid-journal", "transaction receipt image is missing", stage="recovery"
        )
    receipt = _validated_receipt(receipt_bytes, expected_path=receipt_path)
    del receipt_bytes
    if side == "after":
        live_receipt = read_vault_relative_regular(
            vault, receipt_path, maximum=MAX_RECEIPT_BYTES
        )
        _validated_receipt(live_receipt, expected_path=receipt_path)

    for note in _receipt_note_records(receipt):
        path = str(note["path"])
        note_target = next(
            (target for target in targets if target.path == path), None
        )
        if note_target is None:
            raise VaultIngestError(
                "invalid-journal", "transaction note target is missing", stage="recovery"
            )
        data = _target_image_bytes(vault, note_target, side=image_side)
        expected_digest = note[
            "before_raw_sha256" if side == "before" else "after_raw_sha256"
        ]
        if (data is None) != (expected_digest is None) or (
            data is not None and _sha256(data) != expected_digest
        ):
            raise VaultIngestError(
                "invalid-transaction-note",
                "transaction note bytes do not match the immutable receipt",
                stage="recovery",
            )
        if data is not None:
            try:
                parse_native_markdown(data, authority=str(path))
            except NativeNoteError as error:
                raise VaultIngestError(
                    "invalid-transaction-note", error.message, stage="recovery"
                ) from error
        del data

    graph_generation = journal[side]["graph_generation_sha256"]
    try:
        graph_files = _capture_live_graph(vault)
        if graph_generation is None:
            if graph_files:
                raise VaultIngestError(
                    "transaction-graph-mismatch",
                    f"{side} graph should be absent",
                    stage="recovery",
                )
        else:
            state, manifest, _ = _load_live_state_locked(vault)
            if (
                manifest.get("graph_sha256") != graph_generation
                or state.manifest.get("graph_sha256") != graph_generation
            ):
                raise VaultIngestError(
                    "transaction-graph-mismatch",
                    f"{side} graph generation does not match the journal",
                    stage="recovery",
                )
        if (
            journal["before"]["source_ledger_generation_sha256"]
            != journal["after"]["source_ledger_generation_sha256"]
        ):
            ledger = load_source_ledger(vault)
            if ledger.generation_sha256 != journal[side][
                "source_ledger_generation_sha256"
            ]:
                raise VaultIngestError(
                    "transaction-source-mismatch",
                    f"{side} source generation does not match the journal",
                    stage="recovery",
                )
    except VaultIngestError:
        raise
    except (SourceArchiveError, NativeCompilerError, NativeNoteError) as error:
        raise VaultIngestError(
            getattr(error, "code", "invalid-transaction-image"),
            getattr(error, "message", str(error)),
            stage="recovery",
        ) from error


def _current_matches(vault: Vault, target: _Target, expected: bytes | None) -> bool:
    current = _read_optional(vault, target.path, maximum=_target_limit(target.path))
    return current == expected


def _target_side_metadata(
    target: _AnyTarget, *, side: str
) -> tuple[int | None, str | None]:
    if side not in {"old", "new"}:
        raise AssertionError(f"invalid target image side: {side}")
    if isinstance(target, _Target):
        data = target.old if side == "old" else target.new
        return (None, None) if data is None else (len(data), _sha256(data))
    if side == "old":
        return target.old_bytes, target.old_sha256
    return target.new_bytes, target.new_sha256


def _pinned_target_matches_side(
    parent: _PinnedDirectory,
    leaf: str,
    target: _AnyTarget,
    *,
    side: str,
) -> bool:
    """Check one expected image through the retained publication parent."""

    expected_size, expected_digest = _target_side_metadata(target, side=side)
    metadata = parent.lstat_leaf(leaf)
    if expected_size is None or expected_digest is None:
        return metadata is None
    if (
        metadata is None
        or not stat.S_ISREG(metadata.st_mode)
        or _is_link_like(parent.path / leaf, metadata)
        or metadata.st_nlink != 1
        or metadata.st_size != expected_size
    ):
        return False
    try:
        descriptor = parent.open_existing_file(leaf)
    except (OSError, SourceArchiveError):
        return False
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat_leaf(leaf)
        if (
            current is None
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or _is_reparse(opened)
            or _is_link_like(parent.path / leaf, current)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_size != expected_size
            or current.st_size != expected_size
            or not os.path.samestat(opened, current)
        ):
            return False
        digest = hashlib.sha256()
        total = 0
        while total <= expected_size:
            chunk = os.read(
                descriptor,
                min(64 * 1024, expected_size + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        final = parent.lstat_leaf(leaf)
        return bool(
            total == expected_size
            and digest.hexdigest() == expected_digest
            and final is not None
            and stat.S_ISREG(after.st_mode)
            and stat.S_ISREG(final.st_mode)
            and not _is_reparse(after)
            and not _is_link_like(parent.path / leaf, final)
            and after.st_nlink == 1
            and final.st_nlink == 1
            and os.path.samestat(opened, after)
            and os.path.samestat(after, final)
            and after.st_size == expected_size
            and after.st_mtime_ns == opened.st_mtime_ns
            and after.st_ctime_ns == opened.st_ctime_ns
        )
    except (OSError, SourceArchiveError):
        return False
    finally:
        os.close(descriptor)


def _write_target(
    vault: Vault,
    target: _AnyTarget,
    data: bytes | None,
    *,
    expected_side: str,
    failure_injector: FailureInjector | None = None,
) -> None:
    path = target.path

    def assert_expected(parent: _PinnedDirectory, leaf: str) -> None:
        if not _pinned_target_matches_side(
            parent, leaf, target, side=expected_side
        ):
            raise VaultIngestError(
                "concurrent-target-change",
                "transaction target changed immediately before publication: "
                + target.path,
                stage="publication",
            )

    if data is None:
        try:
            unlink_vault_relative_regular(
                vault,
                path,
                before_unlink=lambda parent, leaf: (
                    _invoke(
                        failure_injector, "after-live-temp-fsync", target.path
                    )
                    if failure_injector is not None
                    else None,
                    assert_expected(parent, leaf),
                ),
            )
        except SourceArchiveError as error:
            raise VaultIngestError(
                "transaction-target-write-failed", error.message, stage="publication"
            ) from error
        return
    try:
        target_path = PurePosixPath(path)
        temporary_path = PurePosixPath(target.temporary_path)
        if temporary_path.parent != target_path.parent:
            raise VaultIngestError(
                "invalid-transaction-stage",
                "transaction live temporary is not a target sibling",
                stage="publication",
            )
        replace_vault_relative_regular(
            vault,
            path,
            data,
            maximum=max(1, _target_limit(path)),
            temporary_leaf=temporary_path.name,
            after_fsync=(
                None
                if failure_injector is None
                else lambda: _invoke(
                    failure_injector, "after-live-temp-fsync", target.path
                )
            ),
            before_replace=assert_expected,
            no_replace=_target_side_metadata(
                target, side=expected_side
            )[0]
            is None,
            create_parent=False,
        )
    except FileExistsError as error:
        raise VaultIngestError(
            "concurrent-target-change",
            "transaction target appeared during no-clobber publication: "
            + target.path,
            stage="publication",
        ) from error
    except SourceArchiveError as error:
        raise VaultIngestError(
            "transaction-target-write-failed", error.message, stage="publication"
        ) from error


def _temporary_prefix_matches(data: bytes, expected: bytes | None) -> bool:
    return expected is not None and len(data) <= len(expected) and expected.startswith(data)


def _live_temporary_metadata(
    vault: Vault, target: _AnyTarget
) -> os.stat_result | None:
    parts = _portable_relative(
        target.temporary_path, field="transaction live temporary"
    )
    parent_path = vault.root.joinpath(*parts[:-1])
    try:
        pinned = _PinnedDirectory(parent_path)
    except SourceArchiveError as error:
        if error.code == "missing-ledger-artifact":
            return None
        raise VaultIngestError(
            "unreachable-transaction-temporary", error.message, stage="recovery"
        ) from error
    with pinned:
        metadata = pinned.lstat_leaf(parts[-1])
        if metadata is None:
            return None
        path = parent_path / parts[-1]
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_link_like(path, metadata)
            or metadata.st_nlink not in {1, 2}
            or metadata.st_size > _target_limit(target.path)
        ):
            raise VaultIngestError(
                "unreachable-transaction-temporary",
                "transaction live temporary is not an ordinary bounded single-link file",
                stage="recovery",
            )
        pinned.verify_current()
        return metadata


def _cleanup_linked_publication_temporary(
    vault: Vault, target: _AnyTarget, *, installed_side: str, remove: bool
) -> bool:
    """Consume the one POSIX no-clobber link state after a hard crash."""

    if installed_side not in {"old", "new"}:
        raise AssertionError(f"invalid linked image side: {installed_side}")
    other_side = "new" if installed_side == "old" else "old"
    expected_size, expected_digest = _target_side_metadata(
        target, side=installed_side
    )
    absent_size, absent_digest = _target_side_metadata(target, side=other_side)
    if (
        absent_size is not None
        or absent_digest is not None
        or expected_size is None
        or expected_digest is None
    ):
        return False
    temporary_parts = _portable_relative(
        target.temporary_path, field="transaction live temporary"
    )
    target_parts = _portable_relative(target.path, field="transaction target")
    if temporary_parts[:-1] != target_parts[:-1]:
        return False
    parent_path = vault.root.joinpath(*target_parts[:-1])
    try:
        pinned = _PinnedDirectory(parent_path)
    except SourceArchiveError:
        return False
    with pinned:
        try:
            temporary, data = _read_pinned_output_leaf(
                pinned,
                parent_path / temporary_parts[-1],
                maximum=max(1, expected_size),
                allowed_links=frozenset({2}),
            )
        except VaultIngestError:
            return False
        final = pinned.lstat_leaf(target_parts[-1])
        if (
            temporary is None
            or data is None
            or final is None
            or not stat.S_ISREG(final.st_mode)
            or _is_link_like(parent_path / target_parts[-1], final)
            or final.st_nlink != 2
            or temporary.st_size != expected_size
            or final.st_size != expected_size
            or not os.path.samestat(temporary, final)
            or temporary.st_mtime_ns != final.st_mtime_ns
            or temporary.st_ctime_ns != final.st_ctime_ns
            or _sha256(data) != expected_digest
        ):
            return False
        if not remove:
            return True
        if not pinned.cleanup_owned_leaf_raw(temporary_parts[-1], temporary):
            return False
        remaining = pinned.lstat_leaf(target_parts[-1])
        return bool(
            pinned.lstat_leaf(temporary_parts[-1]) is None
            and remaining is not None
            and os.path.samestat(final, remaining)
            and remaining.st_nlink == 1
        )


def _cleanup_prefix_publication_temporary(
    vault: Vault, target: _AnyTarget, expected: bytes
) -> bool:
    parts = _portable_relative(
        target.temporary_path, field="transaction live temporary"
    )
    parent_path = vault.root.joinpath(*parts[:-1])
    try:
        pinned = _PinnedDirectory(parent_path)
    except SourceArchiveError:
        return False
    with pinned:
        try:
            metadata, data = _read_pinned_output_leaf(
                pinned,
                parent_path / parts[-1],
                maximum=max(1, _target_limit(target.path)),
            )
        except VaultIngestError:
            return False
        if (
            metadata is None
            or data is None
            or not _temporary_prefix_matches(data, expected)
            or not pinned.cleanup_owned_leaf_raw(parts[-1], metadata)
        ):
            return False
        return pinned.lstat_leaf(parts[-1]) is None


def _clean_live_temporaries(
    vault: Vault,
    targets: Sequence[_AnyTarget],
    *,
    state: str,
) -> None:
    if state not in {"prepared", "rolling-back", "committed"}:
        raise VaultIngestError(
            "invalid-journal", "transaction temporary phase is invalid", stage="recovery"
        )
    ordered = tuple(sorted(targets, key=_publication_order))
    temporary_metadata = [
        _live_temporary_metadata(vault, target) for target in ordered
    ]
    present = [
        index for index, metadata in enumerate(temporary_metadata) if metadata is not None
    ]
    if len(present) > 1:
        raise VaultIngestError(
            "unreachable-transaction-temporary",
            "more than one transaction live temporary is present",
            stage="recovery",
        )
    linked_index = next(
        (
            index
            for index in present
            if temporary_metadata[index] is not None
            and temporary_metadata[index].st_nlink == 2
        ),
        None,
    )
    if state == "committed":
        if present:
            raise VaultIngestError(
                "unreachable-transaction-state",
                "committed transaction is not the exact all-new image",
                stage="recovery",
            )
    linked_side = "new" if state == "prepared" else "old"
    if linked_index is not None and (
        state not in {"prepared", "rolling-back"}
        or not _cleanup_linked_publication_temporary(
            vault,
            ordered[linked_index],
            installed_side=linked_side,
            remove=False,
        )
    ):
        raise VaultIngestError(
            "unreachable-transaction-temporary",
            "linked publication temporary is not an exact reachable no-clobber state",
            stage="recovery",
        )

    matches: list[tuple[bool, bool]] = []
    for index, target in enumerate(ordered):
        if index == linked_index:
            matches.append(
                (True, False) if linked_side == "old" else (False, True)
            )
        else:
            matches.append(_classify_live_target(vault, target))
    if state == "committed":
        if any(not matches_new for _, matches_new in matches):
            raise VaultIngestError(
                "unreachable-transaction-state",
                "committed transaction is not the exact all-new image",
                stage="recovery",
            )
        return

    lower = 0
    upper = len(ordered)
    for index, ((matches_old, matches_new), target) in enumerate(
        zip(matches, ordered)
    ):
        if not matches_old and not matches_new:
            raise VaultIngestError(
                "unreachable-transaction-state",
                "transaction target is neither its old nor new image: " + target.path,
                stage="recovery",
            )
        if not matches_old:
            lower = max(lower, index + 1)
        if not matches_new:
            upper = min(upper, index)
    if lower > upper:
        raise VaultIngestError(
            "unreachable-transaction-state",
            "transaction targets are not a reachable publication prefix",
            stage="recovery",
        )
    if not present:
        return
    index = present[0]
    target = ordered[index]
    metadata = temporary_metadata[index]
    assert metadata is not None
    if linked_index is not None:
        required_linked_cut = index + 1 if state == "prepared" else index
        if (
            index != linked_index
            or not lower <= required_linked_cut <= upper
            or not _cleanup_linked_publication_temporary(
                vault,
                target,
                installed_side=linked_side,
                remove=True,
            )
        ):
            raise VaultIngestError(
                "unreachable-transaction-temporary",
                "linked publication temporary is not an exact reachable no-clobber state",
                stage="recovery",
            )
        return
    required_cut = index if state == "prepared" else index + 1
    expected = _target_image_bytes(
        vault, target, side="new" if state == "prepared" else "old"
    )
    if (
        not lower <= required_cut <= upper
        or expected is None
        or not _cleanup_prefix_publication_temporary(vault, target, expected)
    ):
        raise VaultIngestError(
            "unreachable-transaction-temporary",
            "transaction live temporary is not at its reachable phase boundary",
            stage="recovery",
        )
    del expected
    if _live_temporary_metadata(vault, target) is not None:
        raise VaultIngestError(
            "transaction-temporary-cleanup-failed",
            "transaction live temporary remained after exact cleanup",
            stage="recovery",
        )


def _assert_live_temporaries_absent(
    vault: Vault, targets: Sequence[_AnyTarget], *, stage: str
) -> None:
    for target in targets:
        if _read_optional(
            vault, target.temporary_path, maximum=max(1, _target_limit(target.path))
        ) is not None:
            raise VaultIngestError(
                "transaction-temporary-present",
                "transaction live temporary remains after publication",
                stage=stage,
            )


def _publication_order(target: _AnyTarget) -> tuple[int, str]:
    path = target.path
    if not path.startswith(".kgdistiller/"):
        return (10, path)
    if path.startswith(".kgdistiller/receipts/"):
        return (20, path)
    if path == ".kgdistiller/sources/manifest.json":
        return (30, path)
    if path == ".kgdistiller/graph/manifest.json":
        return (50, path)
    return (40, path)


def _vault_ingest_hook(label: str, path: str) -> None:
    """No-op checkpoint for deterministic failure/crash regression tests."""


def _invoke(injector: FailureInjector | None, label: str, path: str = "") -> None:
    _vault_ingest_hook(label, path)
    if injector is not None:
        injector(label)


def _restore_targets(vault: Vault, targets: Sequence[_AnyTarget]) -> None:
    for target in reversed(tuple(sorted(targets, key=_publication_order))):
        matches_old, matches_new = _classify_live_target(vault, target)
        if matches_old:
            continue
        if not matches_new:
            raise VaultIngestError(
                "rollback-conflict",
                "transaction target contains bytes written by another actor; "
                f"manual recovery is required: {target.path}",
                stage="rollback",
            )
        old = _target_image_bytes(vault, target, side="old")
        _write_target(vault, target, old, expected_side="new")
        del old
        if not _target_matches_side(vault, target, side="old"):
            raise VaultIngestError(
                "rollback-mismatch",
                f"rollback did not restore exact target bytes: {target.path}",
                stage="rollback",
            )


def _remove_created_directories(
    vault: Vault, directories: Sequence[Mapping[str, str]]
) -> None:
    for record in reversed(tuple(directories)):
        relative = str(record["path"])
        parts = _portable_relative(relative, field="journal created directory")
        parent_path = vault.root.joinpath(*parts[:-1])
        expected_device = int(record["device"])
        expected_inode = int(record["inode"])

        def owned(metadata: os.stat_result | None) -> bool:
            return bool(
                metadata is not None
                and stat.S_ISDIR(metadata.st_mode)
                and not _is_link_like(parent_path / parts[-1], metadata)
                and metadata.st_dev == expected_device
                and metadata.st_ino == expected_inode
            )

        def assert_owned(current_parent: _PinnedDirectory, leaf: str) -> None:
            if not owned(current_parent.lstat_leaf(leaf)):
                raise SourceArchiveError(
                    "unsafe-ledger-path",
                    "created directory identity changed before cleanup",
                )

        try:
            pinned = _PinnedDirectory(parent_path)
        except SourceArchiveError:
            continue
        with pinned:
            metadata = pinned.lstat_leaf(parts[-1])
            if not owned(metadata):
                continue
            try:
                with _PinnedDirectory(parent_path / parts[-1]):
                    pass
                pinned.unlink_leaf(
                    parts[-1],
                    directory=True,
                    before_unlink=assert_owned,
                )
                if os.name != "nt":
                    os.fsync(pinned.dir_fd)
            except OSError:
                # Empty managed scaffolding is best-effort cleanup.  A deeper
                # unrecorded mkdir or third-party entry makes this directory
                # non-empty; retaining that whole subtree is safer than
                # guessing ownership and does not retain authority bytes from
                # this transaction.
                try:
                    with _PinnedDirectory(parent_path / parts[-1]):
                        pass
                except SourceArchiveError:
                    pass
                continue
            except SourceArchiveError:
                continue


def _cleanup_transaction(vault: Vault, journal: Mapping[str, Any]) -> bool:
    try:
        unlink_vault_relative_regular(vault, JOURNAL_PATH)
    except (SourceArchiveError, OSError):
        return False
    stage = vault.root / ".kgdistiller" / "build" / str(journal["stage"])
    _remove_stage(stage, stage.parent)
    # Once the durable journal is gone, an unreferenced build-stage remnant is
    # disposable orphan workspace. A False result therefore always means the
    # recovery journal itself is still present.
    return True


def _rollback_controlled_transaction(
    vault: Vault,
    journal: Mapping[str, Any],
    targets: Sequence[_AnyTarget],
) -> None:
    if journal["state"] == "prepared":
        _clean_live_temporaries(vault, targets, state="prepared")
        rolling = finalize_self_digest(
            {**dict(journal), "state": "rolling-back"}, "journal_sha256"
        )
        _write_journal(vault, rolling)
    else:
        rolling = dict(journal)
    _clean_live_temporaries(vault, targets, state="rolling-back")
    _restore_targets(vault, targets)
    _assert_live_temporaries_absent(vault, targets, stage="rollback")
    _remove_created_directories(vault, rolling["created_directories"])
    _validate_controlled_image(vault, rolling, targets, side="before")
    if not _cleanup_transaction(vault, rolling):
        raise VaultIngestError(
            "rollback-cleanup-pending",
            "rolled-back transaction cleanup could not complete",
            stage="recovery",
        )


def _recover_locked(vault: Vault) -> bool:
    journal = _load_journal(vault)
    if journal is None:
        return False
    targets = _targets_from_journal(vault, journal)
    if journal["state"] == "committed":
        _clean_live_temporaries(vault, targets, state="committed")
        _validate_controlled_image(vault, journal, targets, side="after")
        if not _cleanup_transaction(vault, journal):
            raise VaultIngestError(
                "committed-cleanup-pending",
                "committed Vault transaction cleanup could not complete",
                stage="recovery",
            )
        return True
    try:
        _rollback_controlled_transaction(vault, journal, targets)
    except VaultIngestError:
        raise
    except Exception as error:
        raise VaultIngestError(
            "rollback-failed",
            f"prepared transaction could not restore exact old bytes: {error}",
            stage="rollback",
        ) from error
    return True


def recover_vault_ingest(vault: Vault | Path | str) -> bool:
    """Recover pending F3/F4 journals under the single Vault writer lock."""

    try:
        selected = vault if isinstance(vault, Vault) else load_vault(vault)
    except (VaultError, OSError, UnicodeError, ValueError) as error:
        raise VaultIngestError(
            getattr(error, "code", "invalid-vault-selection"),
            getattr(error, "message", str(error)),
            stage="recovery",
        ) from error
    try:
        with vault_writer_lock(selected):
            pending_f4 = _load_journal(selected) is not None
            _recover_native_transactions_locked(selected)
            return pending_f4
    except VaultIngestError:
        raise
    except (NativeCompilerError, SourceArchiveError, VaultError) as error:
        raise VaultIngestError(
            getattr(error, "code", "transaction-recovery-failed"),
            getattr(error, "message", str(error)),
            stage="recovery",
        ) from error


def _apply_targets(
    prepared: _Prepared,
    targets: Sequence[_Target],
    *,
    failure_injector: FailureInjector | None,
) -> None:
    for target in sorted(targets, key=_publication_order):
        _invoke(failure_injector, "before-target", target.path)
        if not _current_matches(prepared.vault, target, target.old):
            raise VaultIngestError(
                "concurrent-target-change",
                "transaction target changed after final validation: " + target.path,
                stage="publication",
            )
        _write_target(
            prepared.vault,
            target,
            target.new,
            expected_side="old",
            failure_injector=failure_injector,
        )
        _invoke(failure_injector, "after-target", target.path)
        if not _current_matches(prepared.vault, target, target.new):
            raise VaultIngestError(
                "concurrent-target-change",
                "transaction target changed during publication: " + target.path,
                stage="publication",
            )
        _assert_live_temporaries_absent(
            prepared.vault, (target,), stage="publication"
        )
    if not all(_current_matches(prepared.vault, target, target.new) for target in targets):
        raise VaultIngestError(
            "publication-mismatch",
            "installed Vault targets differ from validated staged bytes",
            stage="publication",
        )


def _apply_locked(
    input_value: _RequestInput,
    *,
    home: Path | str | None,
    failure_injector: FailureInjector | None,
    receipt_precondition: ReceiptPrecondition | None,
) -> tuple[dict[str, Any], str, str | None, bool]:
    _, initial = _registered_vault(str(input_value.request["vault_id"]), home)
    with vault_writer_lock(initial):
        try:
            _recover_native_transactions_locked(initial)
        except (NativeCompilerError, SourceArchiveError, VaultError) as error:
            raise VaultIngestError(
                getattr(error, "code", "transaction-recovery-failed"),
                getattr(error, "message", str(error)),
                stage="recovery",
            ) from error
        existing: dict[str, Any] | None = None
        historical_generation: str | None = None
        with vault_registry_lock(home):
            _, selected = _registered_vault(
                str(input_value.request["vault_id"]), home
            )
            if selected.root != initial.root:
                raise VaultIngestError(
                    "stale-vault-selection", "registered Vault path changed before apply"
                )
            existing = _existing_receipt(selected, input_value.request)
            if existing is not None:
                historical_generation = _historical_receipt_ledger_generation(
                    selected, existing
                )
            else:
                selected = _resolve_vault(input_value.request, home)
        if existing is not None:
            if receipt_precondition is not None:
                receipt_precondition(existing)
            return existing, "already-committed", historical_generation, True

        prepared = _prepare(input_value, home=home)
        receipt, source, compilation = _complete_preparation(prepared)
        if receipt_precondition is not None:
            receipt_precondition(receipt)
        _assert_prepared_current(prepared, home=home)
        if source is not None:
            try:
                source_stage = stage_derivation_generation(prepared.vault, source)
                try:
                    install_derivation_generation(prepared.vault, source, source_stage)
                    _invoke(failure_injector, "after-source-generation-install")
                finally:
                    _remove_stage(source_stage, source_stage.parent)
            except SourceArchiveError as error:
                raise VaultIngestError(error.code, error.message, stage="publication") from error
        _assert_prepared_current(prepared, home=home)

        desired = _desired_targets(prepared, source, compilation, receipt)
        known = _known_before(prepared)
        planned_directories = _planned_directories(prepared.vault, desired)
        stage = _allocate_transaction_stage(prepared.vault)
        journal: dict[str, Any] | None = None
        prepared_journal: dict[str, Any] | None = None
        targets: tuple[_Target, ...] = ()
        committed = False
        commit_uncertain = False
        cleanup_allowed = True
        prepared_journal_maybe_live = False
        try:
            targets = _stage_targets(prepared.vault, stage, desired, known)
            _fsync_transaction_stage(stage)
        except Exception:
            _remove_stage(stage, stage.parent)
            raise
        late_result: tuple[dict[str, Any], str, str | None, bool] | None = None

        class _LateReceiptFound(BaseException):
            pass

        @contextlib.contextmanager
        def publication_registry() -> Any:
            try:
                with vault_registry_lock(home):
                    yield
            except _LateReceiptFound:
                return

        with publication_registry():
            try:
                _assert_prepared_current(prepared, home=home)
                late_receipt = _existing_receipt(
                    prepared.vault, input_value.request
                )
                if late_receipt is not None:
                    late_result = (
                        late_receipt,
                        "already-committed",
                        _historical_receipt_ledger_generation(
                            prepared.vault, late_receipt
                        ),
                        True,
                    )
                    _remove_stage(stage, stage.parent)
                    raise _LateReceiptFound()
                prepared_journal = _journal_payload(
                    prepared,
                    source,
                    receipt,
                    stage,
                    targets,
                    planned_directories,
                    (),
                    state="prepared",
                )
                _journal_bytes(prepared_journal)
                prepared_journal_maybe_live = True
                _write_journal(prepared.vault, prepared_journal)
                durable_prepared = _load_journal(prepared.vault)
                if durable_prepared != prepared_journal:
                    raise VaultIngestError(
                        "prepared-journal-mismatch",
                        "prepared transaction journal did not read back exactly",
                        stage="publication",
                    )
                journal = durable_prepared
                _invoke(failure_injector, "after-transaction-stage")
                _invoke(failure_injector, "after-journal")
                _assert_prepared_current(prepared, home=home)
                _invoke(failure_injector, "after-final-preconditions")
                journal = _create_planned_directories(
                    prepared.vault,
                    journal,
                    failure_injector=failure_injector,
                )
                prepared_journal = journal
                created_directories = tuple(journal["created_directories"])
                _apply_targets(
                    prepared,
                    targets,
                    failure_injector=failure_injector,
                )
                _assert_after_current(
                    prepared,
                    source,
                    compilation,
                    targets,
                    home=home,
                )
                _invoke(failure_injector, "after-postconditions")
                _invoke(failure_injector, "before-commit")
                _fsync_created_directories(
                    prepared.vault, created_directories
                )
                _assert_after_current(
                    prepared,
                    source,
                    compilation,
                    targets,
                    home=home,
                )
                committed_payload = _journal_payload(
                    prepared,
                    source,
                    receipt,
                    stage,
                    targets,
                    planned_directories,
                    created_directories,
                    state="committed",
                )
                try:
                    _write_journal(prepared.vault, committed_payload)
                except Exception as commit_error:
                    commit_uncertain = True
                    try:
                        durable = _load_journal(prepared.vault)
                    except Exception as reload_error:
                        raise VaultIngestError(
                            "commit-journal-uncertain",
                            "commit journal write failed and its durable state is invalid",
                            stage="publication",
                            diagnostics=[
                                {"code": "commit-write", "message": str(commit_error)},
                                {"code": "commit-reload", "message": str(reload_error)},
                            ],
                        ) from reload_error
                    if durable == committed_payload:
                        journal = durable
                        committed = True
                        try:
                            _write_journal(prepared.vault, committed_payload)
                            if _load_journal(prepared.vault) != committed_payload:
                                raise VaultIngestError(
                                    "commit-journal-uncertain",
                                    "retried commit journal did not read back exactly",
                                    stage="publication",
                                )
                        except Exception as retry_error:
                            cleanup_allowed = False
                            raise VaultIngestError(
                                "commit-journal-uncertain",
                                "visible committed journal could not be durably rewritten",
                                stage="publication",
                                diagnostics=[
                                    {"code": "commit-write", "message": str(commit_error)},
                                    {"code": "commit-retry", "message": str(retry_error)},
                                ],
                            ) from retry_error
                        commit_uncertain = False
                    elif durable == prepared_journal:
                        journal = durable
                        commit_uncertain = False
                        raise commit_error
                    else:
                        raise VaultIngestError(
                            "commit-journal-uncertain",
                            "commit journal write failed with neither exact prepared nor committed state",
                            stage="publication",
                        ) from commit_error
                else:
                    journal = committed_payload
                    committed = True
                _invoke(failure_injector, "after-commit")
                _validate_controlled_image(
                    prepared.vault, journal, targets, side="after"
                )
            except Exception as publication_error:
                if commit_uncertain:
                    raise
                if journal is not None and not committed:
                    try:
                        _rollback_controlled_transaction(
                            prepared.vault, journal, targets
                        )
                    except VaultIngestError as rollback_error:
                        if rollback_error.code == "rollback-conflict" or (
                            isinstance(publication_error, VaultIngestError)
                            and publication_error.code
                            == "concurrent-target-change"
                            and rollback_error.code
                            == "unreachable-transaction-state"
                        ):
                            raise VaultIngestError(
                                "rollback-conflict",
                                "transaction target contains bytes written by another actor; "
                                "manual recovery is required",
                                stage="rollback",
                            ) from rollback_error
                        raise VaultIngestError(
                            "rollback-failed",
                            "Vault ingest failed and exact rollback could not complete",
                            stage="rollback",
                            diagnostics=[
                                {
                                    "code": "publication-error",
                                    "message": str(publication_error),
                                },
                                {
                                    "code": "rollback-error",
                                    "message": str(rollback_error),
                                },
                            ],
                        ) from rollback_error
                    except Exception as rollback_error:
                        raise VaultIngestError(
                            "rollback-failed",
                            "Vault ingest failed and exact rollback could not complete",
                            stage="rollback",
                            diagnostics=[
                                {
                                    "code": "publication-error",
                                    "message": str(publication_error),
                                },
                                {"code": "rollback-error", "message": str(rollback_error)},
                            ],
                        ) from rollback_error
                elif journal is None and not prepared_journal_maybe_live:
                    _remove_stage(stage, stage.parent)
                raise
        if late_result is not None:
            if receipt_precondition is not None:
                receipt_precondition(late_result[0])
            return late_result
        assert journal is not None
        _validate_controlled_image(
            prepared.vault, journal, targets, side="after"
        )
        cleanup = cleanup_allowed and _cleanup_transaction(prepared.vault, journal)
        ledger_generation = (
            prepared.ledger.generation_sha256
            if source is None
            else source.ledger.generation_sha256
        )
        return receipt, "committed", ledger_generation, cleanup


def apply_vault_ingest(
    request: Path | str | Mapping[str, Any],
    *,
    home: Path | str | None = None,
    request_root: Path | str | None = None,
    failure_injector: FailureInjector | None = None,
    receipt_precondition: ReceiptPrecondition | None = None,
) -> dict[str, Any]:
    """Validate and atomically apply one action-independent native request."""

    with _closed_ingest_errors(stage="publication"):
        input_value = _load_request(request, request_root=request_root)
        receipt, _, _, _ = _apply_locked(
            input_value,
            home=home,
            failure_injector=failure_injector,
            receipt_precondition=receipt_precondition,
        )
        return receipt


def _report(
    *,
    action: str,
    prepared_plan: Mapping[str, Any] | None,
    receipt: Mapping[str, Any] | None,
    request: Mapping[str, Any],
    outcome: str,
    ledger_generation: str | None,
    cleanup: bool,
) -> dict[str, Any]:
    if receipt is None:
        assert prepared_plan is not None
        graph = str(prepared_plan["after"]["graph_generation_sha256"])
        notes = str(prepared_plan["after"]["note_inventory_sha256"])
        plan_sha = str(prepared_plan["plan_sha256"])
        receipt_sha = None
        receipt_path = None
        warnings: list[str] = []
        cleanup_status = "not-applicable"
    else:
        graph = str(receipt["after"]["graph_generation_sha256"])
        notes = str(receipt["after"]["note_inventory_sha256"])
        plan_sha = None
        receipt_sha = str(receipt["receipt_sha256"])
        receipt_path = receipt_relative_path(receipt_sha)
        warnings = [] if cleanup else ["committed transaction cleanup remains pending"]
        cleanup_status = "complete" if cleanup else "pending"
    return validate_contract(
        {
            "schema": REPORT_SCHEMA,
            "action": action,
            "status": "ok",
            "outcome": outcome,
            "vault_id": request["vault_id"],
            "registry_generation": request["registry_generation"],
            "vault_manifest_sha256": request["vault_manifest_sha256"],
            "request_sha256": request["request_sha256"],
            "plan_sha256": plan_sha,
            "receipt_sha256": receipt_sha,
            "receipt_path": receipt_path,
            "graph_generation_sha256": graph,
            "source_ledger_generation_sha256": ledger_generation,
            "note_inventory_sha256": notes,
            "cleanup_status": cleanup_status,
            "warnings": warnings,
        }
    )


def plan_vault_ingest_report(
    request: Path | str | Mapping[str, Any],
    *,
    home: Path | str | None = None,
    request_root: Path | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the closed stdout report plus its canonical plan artifact."""

    with _closed_ingest_errors(stage="planning"):
        input_value = _load_request(request, request_root=request_root)
        prepared = _prepare(input_value, home=home)
        _complete_preparation(prepared)
        _assert_prepared_current(prepared, home=home)
        report = _report(
            action="plan",
            prepared_plan=prepared.plan,
            receipt=None,
            request=input_value.request,
            outcome="planned",
            ledger_generation=None,
            cleanup=True,
        )
        return report, prepared.plan


def apply_vault_ingest_report(
    request: Path | str | Mapping[str, Any],
    *,
    home: Path | str | None = None,
    request_root: Path | str | None = None,
    failure_injector: FailureInjector | None = None,
    receipt_precondition: ReceiptPrecondition | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the closed stdout report plus its canonical receipt artifact."""

    with _closed_ingest_errors(stage="publication"):
        input_value = _load_request(request, request_root=request_root)
        receipt, outcome, ledger_generation, cleanup = _apply_locked(
            input_value,
            home=home,
            failure_injector=failure_injector,
            receipt_precondition=receipt_precondition,
        )
        report = _report(
            action="apply",
            prepared_plan=None,
            receipt=receipt,
            request=input_value.request,
            outcome=outcome,
            ledger_generation=ledger_generation,
            cleanup=cleanup,
        )
        return report, receipt


__all__ = [
    "CAPABILITY",
    "ERROR_SCHEMA",
    "JOURNAL_SCHEMA",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "REPORT_SCHEMA",
    "REQUEST_SCHEMA",
    "VaultIngestError",
    "apply_vault_ingest",
    "apply_vault_ingest_report",
    "plan_vault_ingest",
    "plan_vault_ingest_report",
    "preflight_ingest_output",
    "receipt_relative_path",
    "recover_vault_ingest",
    "write_ingest_artifact",
]
