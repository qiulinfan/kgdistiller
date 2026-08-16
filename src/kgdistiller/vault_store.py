"""Portable, content-bound native Vault stores.

The v3 store is deliberately separate from the legacy static ``qlkg-store-v2``
export.  It inventories the native authority, current source ledger, complete
native graph, durable ingest receipts, and fixed Git scaffolding without
embedding a machine-local path.
"""

from __future__ import annotations

import hashlib
import ctypes
import contextlib
import errno
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractError,
    MAX_VAULT_STORE_BYTES,
    canonical_json,
    finalize_self_digest,
    sha256_json,
    validate_contract,
    _validate_portable_path,
)
from .native_compiler import (
    GRAPH_TRANSACTION_PATH,
    MAX_GRAPH_ARTIFACTS,
    MAX_NATIVE_GRAPH_BYTES,
    NativeCompilerError,
    _capture_live_graph,
    _expected_bytes,
    _load_live_state_locked,
    _manifest_artifact_names,
    _native_graph_artifact_limit,
    _recover_native_transactions_locked,
    compile_vault,
    validate_native_compilation,
)
from .native_notes import NativeNoteError, parse_native_markdown
from .source_archive import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES as MAX_SOURCE_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    SourceArchiveError,
    SourceLedger,
    _PinnedDirectory,
    current_evidence_view,
    load_source_ledger,
    read_vault_relative_regular,
    replace_vault_relative_regular,
    vault_generation_guard,
)
from .vault_ingest import (
    JOURNAL_PATH as VAULT_INGEST_JOURNAL_PATH,
    MAX_RECEIPT_BYTES,
    VaultIngestError,
    _capture_receipt_inventory,
    _derivation_summaries,
    _validated_receipt,
)
from .vaults import (
    MAX_MANAGED_MARKDOWN_BYTES,
    Vault,
    VaultError,
    _is_filesystem_root,
    _is_link_or_reparse,
    _lstat,
    _same_path,
    load_registry,
    load_vault,
    snapshot_managed_markdown,
    vault_registry_read_guard,
)


STORE_SCHEMA = "qlkg-vault-store-v3"
REPORT_SCHEMA = "qlkg-vault-store-report-v1"
STORE_PATH = ".kgdistiller/store.json"
MAX_STORE_MANIFEST_BYTES = 512 * 1024 * 1024
MAX_STORE_FILES = 1_300_050
MAX_LOCAL_STORE_DIRECTORIES = 1_300_050
MAX_LOCAL_DIRECTORY_PATH_BYTES = 256 * 1024 * 1024
MAX_CAPTURE_RETRIES = 2
MAX_STAGE_MARKER_BYTES = 4096
STAGE_MARKER_LEAF = ".kgdistiller-stage.json"
GITATTRIBUTES = b"* text=auto eol=lf\nsources/blobs/** -text\n"
BUILD_GITIGNORE = b"*\n!.gitignore\n"
GITKEEP = b""
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GRAPH_FIXED = {
    "manifest.json",
    "sources.json",
    "nodes.jsonl",
    "edges.jsonl",
    "references.jsonl",
    "diagnostics.json",
}
_EXCLUDED_AUTHORITY_COMPONENTS = {".git", ".obsidian", ".kgdistiller"}


def _vault_store_hook(label: str, path: str) -> None:
    """No-op deterministic checkpoint used by bounded crash/race tests."""


class VaultStoreError(RuntimeError):
    """Stable closed failure for portable Vault store operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def payload(self) -> dict[str, str]:
        return {
            "kind": "kgdistiller-vault-store-error",
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class _CapturedStore:
    vault: Vault
    manifest: dict[str, Any]
    ledger: SourceLedger


@dataclass(frozen=True)
class _OwnedLeaf:
    path: str
    metadata: os.stat_result
    bytes: int
    sha256: str


@dataclass(frozen=True)
class _OwnedDirectory:
    path: Path
    metadata: os.stat_result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_record(path: str, data: bytes) -> dict[str, Any]:
    return {"path": path, "bytes": len(data), "sha256": _sha256(data)}


def _strict_json(data: bytes, *, kind: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            data.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise VaultStoreError(f"invalid-{kind}", f"{kind} is not closed canonical JSON") from error
    if not isinstance(payload, dict):
        raise VaultStoreError(f"invalid-{kind}", f"{kind} must be a JSON object")
    return payload


def _stable_pinned_file(
    parent: _PinnedDirectory,
    parent_path: Path,
    leaf: str,
    *,
    maximum: int,
    links: int,
) -> tuple[bytes, os.stat_result]:
    before = parent.lstat_leaf(leaf)
    if before is None or (
        not stat.S_ISREG(before.st_mode)
        or _is_link_or_reparse(parent_path / leaf, before)
        or before.st_nlink != links
        or before.st_size > maximum
    ):
        raise VaultStoreError("unsafe-vault-store", "portable file metadata is unsafe")
    try:
        descriptor = parent.open_existing_file(leaf)
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError("unsafe-vault-store", "portable file could not be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat_leaf(leaf)
        if (
            current is None
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, current)
            or opened.st_nlink != links
            or current.st_nlink != links
        ):
            raise VaultStoreError("unstable-vault-store", "portable file changed during open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise VaultStoreError("vault-store-too-large", "portable file exceeds its byte bound")
        after = os.fstat(descriptor)
        final = parent.lstat_leaf(leaf)
        if (
            final is None
            or not os.path.samestat(opened, after)
            or not os.path.samestat(after, final)
            or after.st_nlink != links
            or final.st_nlink != links
            or after.st_size != total
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise VaultStoreError("unstable-vault-store", "portable file changed while being read")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _verify_opened_file_exact(
    parent: _PinnedDirectory,
    parent_path: Path,
    leaf: str,
    descriptor: int,
    expected: bytes,
    *,
    initial: os.stat_result | None = None,
) -> os.stat_result:
    """Bind exact bytes and the live leaf name to one retained descriptor."""

    before = parent.lstat_leaf(leaf)
    opened = os.fstat(descriptor)
    if (
        before is None
        or not stat.S_ISREG(before.st_mode)
        or _is_link_or_reparse(parent_path / leaf, before)
        or before.st_nlink != 1
        or opened.st_nlink != 1
        or opened.st_size != len(expected)
        or not os.path.samestat(before, opened)
        or (initial is not None and not os.path.samestat(initial, opened))
    ):
        raise VaultStoreError(
            "store-stage-conflict", "portable store stage identity changed"
        )
    os.lseek(descriptor, 0, os.SEEK_SET)
    offset = 0
    while offset < len(expected):
        chunk = os.read(descriptor, min(64 * 1024, len(expected) - offset))
        if not chunk or chunk != expected[offset : offset + len(chunk)]:
            raise VaultStoreError(
                "store-stage-conflict", "portable store stage content changed"
            )
        offset += len(chunk)
    if os.read(descriptor, 1):
        raise VaultStoreError(
            "store-stage-conflict", "portable store stage content changed"
        )
    after = os.fstat(descriptor)
    current = parent.lstat_leaf(leaf)
    if (
        current is None
        or not os.path.samestat(opened, after)
        or not os.path.samestat(after, current)
        or after.st_nlink != 1
        or current.st_nlink != 1
        or after.st_size != len(expected)
        or after.st_mtime_ns != opened.st_mtime_ns
        or after.st_ctime_ns != opened.st_ctime_ns
    ):
        raise VaultStoreError(
            "store-stage-conflict", "portable store stage changed while verified"
        )
    return after


def _normalize_noreplace_in_parent(
    parent: _PinnedDirectory,
    parent_path: Path,
    leaf: str,
    expected: bytes,
    temporary: str,
) -> tuple[bool, os.stat_result | None]:
    """Normalize the exact reachable states of one deterministic install."""

    destination = parent.lstat_leaf(leaf)
    staged = parent.lstat_leaf(temporary)
    if destination is not None and staged is not None:
        if os.name == "nt" or not os.path.samestat(destination, staged):
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold has an unreachable staged state"
            )
        staged_bytes, staged_opened = _stable_pinned_file(
            parent,
            parent_path,
            temporary,
            maximum=max(1, len(expected)),
            links=2,
        )
        destination_bytes, destination_opened = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=max(1, len(expected)),
            links=2,
        )
        if (
            staged_bytes != expected
            or destination_bytes != expected
            or not os.path.samestat(staged_opened, destination_opened)
        ):
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold linked state is not exact"
            )
        if not parent.cleanup_owned_leaf_raw(temporary, staged_opened):
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold linked stage changed"
            )
        final = parent.lstat_leaf(leaf)
        if final is None or final.st_nlink != 1 or not os.path.samestat(
            destination_opened, final
        ):
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold linked state did not normalize"
            )
        parent.verify_current()
        return True, final
    if staged is not None:
        staged_bytes, staged_opened = _stable_pinned_file(
            parent,
            parent_path,
            temporary,
            maximum=max(1, len(expected)),
            links=1,
        )
        if staged_bytes != expected:
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold stage has different bytes"
            )
        if not parent.cleanup_owned_leaf_raw(temporary, staged_opened):
            raise VaultStoreError(
                "scaffold-stage-conflict", "portable scaffold stage changed"
            )
    if destination is not None:
        destination_bytes, destination_opened = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=max(1, len(expected)),
            links=1,
        )
        if destination_bytes != expected:
            raise VaultStoreError(
                "scaffold-content-conflict", "portable scaffold exists with different bytes"
            )
        parent.verify_current()
        return True, destination_opened
    parent.verify_current()
    return False, None


def _install_noreplace_in_parent(
    parent: _PinnedDirectory,
    parent_path: Path,
    relative: str,
    expected: bytes,
    *,
    temporary: str,
) -> _OwnedLeaf | None:
    leaf = PurePosixPath(relative).name
    descriptor = -1
    installed = False
    try:
        exists, _ = _normalize_noreplace_in_parent(
            parent, parent_path, leaf, expected, temporary
        )
        if exists:
            return None
        descriptor = parent.create_file(
            temporary, delete_access=True, readable=True
        )
        offset = 0
        while offset < len(expected):
            offset += os.write(descriptor, expected[offset:])
        os.fsync(descriptor)
        _vault_store_hook("after-install-temp-fsync", relative)
        parent.install_leaf_noreplace(
            temporary,
            leaf,
            descriptor,
            expected_content=expected,
        )
        installed = True
        _vault_store_hook("after-install-noreplace", relative)
        data, metadata = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=max(1, len(expected)),
            links=1,
        )
        if data != expected:
            raise VaultStoreError(
                "scaffold-install-failed", "portable scaffold install changed"
            )
        parent.verify_current()
        installed = False
        return _OwnedLeaf(relative, metadata, len(expected), _sha256(expected))
    except BaseException:
        if descriptor >= 0:
            try:
                source_metadata = os.fstat(descriptor)
                if installed:
                    parent.cleanup_owned_leaf_raw(leaf, source_metadata)
                parent.cleanup_owned_leaf_raw(temporary, source_metadata)
            except (OSError, SourceArchiveError):
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _authority_record_matches(
    data: bytes, record: Mapping[str, Any]
) -> bool:
    normalized = _normalized_authority_bytes(data)
    return (
        len(normalized) == int(record["normalized_bytes"])
        and _sha256(normalized) == str(record["normalized_sha256"])
    )


def _authority_stage_temporary(
    relative: str, record: Mapping[str, Any]
) -> str:
    identity = (
        relative.encode("utf-8", errors="strict")
        + b"\0"
        + str(record["normalized_sha256"]).encode("ascii", errors="strict")
    )
    return f".kgd-{_sha256(identity)[:32]}.tmp"


def _install_authority_noreplace_in_parent(
    parent: _PinnedDirectory,
    parent_path: Path,
    relative: str,
    current_data: bytes,
    record: Mapping[str, Any],
    *,
    temporary: str,
) -> _OwnedLeaf | None:
    """Resume/install one newline-semantic authority artifact safely."""

    leaf = PurePosixPath(relative).name
    destination = parent.lstat_leaf(leaf)
    staged = parent.lstat_leaf(temporary)
    if destination is not None and staged is not None:
        if os.name == "nt" or not os.path.samestat(destination, staged):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "authority artifact has an unreachable staged state",
            )
        staged_data, staged_opened = _stable_pinned_file(
            parent,
            parent_path,
            temporary,
            maximum=MAX_MANAGED_MARKDOWN_BYTES,
            links=2,
        )
        destination_data, destination_opened = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=MAX_MANAGED_MARKDOWN_BYTES,
            links=2,
        )
        if (
            staged_data != destination_data
            or not _authority_record_matches(staged_data, record)
            or not os.path.samestat(staged_opened, destination_opened)
        ):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "linked authority stage does not match its normalized record",
            )
        if not parent.cleanup_owned_leaf_raw(temporary, staged_opened):
            raise VaultStoreError(
                "snapshot-stage-conflict", "linked authority stage changed"
            )
        final = parent.lstat_leaf(leaf)
        if (
            final is None
            or final.st_nlink != 1
            or not os.path.samestat(destination_opened, final)
        ):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "linked authority stage did not normalize",
            )
        parent.verify_current()
        return None
    if destination is not None:
        destination_data, _ = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=MAX_MANAGED_MARKDOWN_BYTES,
            links=1,
        )
        if not _authority_record_matches(destination_data, record):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "existing authority artifact does not match its normalized record",
            )
        parent.verify_current()
        return None

    descriptor = -1
    installed = False
    expected_raw = current_data
    try:
        if staged is not None:
            staged_data, staged_metadata = _stable_pinned_file(
                parent,
                parent_path,
                temporary,
                maximum=MAX_MANAGED_MARKDOWN_BYTES,
                links=1,
            )
            if not _authority_record_matches(staged_data, record):
                raise VaultStoreError(
                    "snapshot-stage-conflict",
                    "authority temporary does not match its normalized record",
                )
            expected_raw = staged_data
            descriptor = parent.open_existing_file(temporary, delete_access=True)
            _verify_opened_file_exact(
                parent,
                parent_path,
                temporary,
                descriptor,
                expected_raw,
                initial=staged_metadata,
            )
        else:
            if not _authority_record_matches(current_data, record):
                raise VaultStoreError(
                    "snapshot-stage-conflict",
                    "live authority artifact does not match its normalized record",
                )
            descriptor = parent.create_file(
                temporary, delete_access=True, readable=True
            )
            offset = 0
            while offset < len(expected_raw):
                offset += os.write(descriptor, expected_raw[offset:])
            os.fsync(descriptor)
            _vault_store_hook("after-install-temp-fsync", relative)
        parent.install_leaf_noreplace(
            temporary,
            leaf,
            descriptor,
            expected_content=expected_raw,
        )
        installed = True
        _vault_store_hook("after-install-noreplace", relative)
        final_data, final_metadata = _stable_pinned_file(
            parent,
            parent_path,
            leaf,
            maximum=MAX_MANAGED_MARKDOWN_BYTES,
            links=1,
        )
        if not _authority_record_matches(final_data, record):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "installed authority artifact changed normalized content",
            )
        parent.verify_current()
        installed = False
        return _OwnedLeaf(
            relative,
            final_metadata,
            len(final_data),
            _sha256(final_data),
        )
    except BaseException:
        if descriptor >= 0:
            try:
                source_metadata = os.fstat(descriptor)
                if installed:
                    parent.cleanup_owned_leaf_raw(leaf, source_metadata)
                parent.cleanup_owned_leaf_raw(temporary, source_metadata)
            except (OSError, SourceArchiveError):
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _install_noreplace(
    vault: Vault | Path,
    relative: str,
    expected: bytes,
    *,
    temporary: str,
) -> _OwnedLeaf | None:
    root = vault.root if isinstance(vault, Vault) else vault
    parts = PurePosixPath(relative).parts
    parent_path = root.joinpath(*parts[:-1])
    try:
        with _PinnedDirectory(parent_path) as parent:
            return _install_noreplace_in_parent(
                parent, parent_path, relative, expected, temporary=temporary
            )
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "scaffold-install-failed", "portable scaffold could not be installed safely"
        ) from error


def _rollback_owned_leaves(
    vault: Vault | Path, owned: Sequence[_OwnedLeaf]
) -> None:
    root = vault.root if isinstance(vault, Vault) else vault
    for item in reversed(tuple(owned)):
        parts = PurePosixPath(item.path).parts
        parent_path = root.joinpath(*parts[:-1])
        try:
            with _PinnedDirectory(parent_path) as parent:
                current = parent.lstat_leaf(parts[-1])
                if (
                    current is not None
                    and stat.S_ISREG(current.st_mode)
                    and not _is_link_or_reparse(parent_path / parts[-1], current)
                    and current.st_nlink == 1
                    and os.path.samestat(item.metadata, current)
                ):
                    data, opened = _stable_pinned_file(
                        parent,
                        parent_path,
                        parts[-1],
                        maximum=max(1, item.bytes),
                        links=1,
                    )
                    if (
                        len(data) == item.bytes
                        and _sha256(data) == item.sha256
                        and os.path.samestat(opened, item.metadata)
                    ):
                        parent.cleanup_owned_leaf_raw(parts[-1], opened)
        except (OSError, SourceArchiveError, VaultStoreError):
            pass


def _rollback_owned_directories(owned: Sequence[_OwnedDirectory]) -> None:
    for item in sorted(owned, key=lambda row: len(row.path.parts), reverse=True):
        parent_path = item.path.parent
        leaf = item.path.name
        try:
            with _PinnedDirectory(parent_path) as parent:
                current = parent.lstat_leaf(leaf)
                if (
                    current is None
                    or not stat.S_ISDIR(current.st_mode)
                    or _is_link_or_reparse(item.path, current)
                    or not os.path.samestat(item.metadata, current)
                ):
                    continue

                def still_owned(pinned: _PinnedDirectory, name: str) -> None:
                    latest = pinned.lstat_leaf(name)
                    if (
                        latest is None
                        or not stat.S_ISDIR(latest.st_mode)
                        or _is_link_or_reparse(item.path, latest)
                        or not os.path.samestat(item.metadata, latest)
                    ):
                        raise VaultStoreError(
                            "snapshot-stage-conflict",
                            "snapshot stage directory changed during cleanup",
                        )

                parent.unlink_leaf(leaf, directory=True, before_unlink=still_owned)
                if os.name != "nt":
                    os.fsync(parent.dir_fd)
                parent.verify_current()
        except (OSError, SourceArchiveError, VaultStoreError):
            pass


def _store_bytes(manifest: Mapping[str, Any]) -> bytes:
    data = canonical_json(dict(manifest)).encode("utf-8")
    if len(data) > MAX_STORE_MANIFEST_BYTES:
        raise VaultStoreError(
            "store-manifest-too-large", "portable Vault store manifest exceeds its byte bound"
        )
    return data


def _record_digest(record: Mapping[str, Any]) -> str:
    return str(record["sha256"])


def _record_size(record: Mapping[str, Any]) -> int:
    if "normalized_bytes" in record:
        return int(record["normalized_bytes"])
    return int(record["bytes"])


def _normalized_authority_bytes(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise VaultStoreError(
            "invalid-authority-utf8", "native authority note is not strict UTF-8"
        ) from error
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _read_authority_record(vault: Vault, record: Mapping[str, Any]) -> bytes:
    data = read_vault_relative_regular(
        vault,
        str(record["path"]),
        maximum=MAX_MANAGED_MARKDOWN_BYTES,
    )
    normalized = _normalized_authority_bytes(data)
    if (
        len(normalized) != int(record["normalized_bytes"])
        or _sha256(normalized) != str(record["normalized_sha256"])
    ):
        raise VaultStoreError(
            "store-artifact-mismatch",
            "native authority note does not match its normalized inventory",
        )
    return data


def _read_record(vault: Vault, record: Mapping[str, Any]) -> bytes:
    if "normalized_sha256" in record:
        return _read_authority_record(vault, record)
    expected_size = int(record["bytes"])
    data = read_vault_relative_regular(
        vault,
        str(record["path"]),
        maximum=max(1, expected_size),
    )
    if len(data) != expected_size or _sha256(data) != _record_digest(record):
        raise VaultStoreError(
            "store-artifact-mismatch", "portable Vault artifact does not match its inventory"
        )
    return data


def _vault_manifest_record(vault: Vault) -> tuple[dict[str, Any], str]:
    data = read_vault_relative_regular(
        vault, ".kgdistiller/vault.json", maximum=1024 * 1024
    )
    if b"\r" in data:
        raise VaultStoreError(
            "noncanonical-vault-manifest",
            "vault.json must use LF-only bytes for portable Git checkout",
        )
    try:
        current = validate_contract(_strict_json(data, kind="vault-manifest"))
    except (ContractError, RecursionError) as error:
        raise VaultStoreError(
            "invalid-vault-manifest", "vault.json is not a closed Vault manifest"
        ) from error
    if current != vault.manifest:
        raise VaultStoreError(
            "stale-vault-manifest",
            "vault.json changed after the Vault generation was selected",
        )
    return _file_record(".kgdistiller/vault.json", data), sha256_json(vault.manifest)


def _validate_authority_store_path(path: str) -> None:
    try:
        _validate_portable_path(path, field="authority path")
    except ContractError as error:
        raise VaultStoreError(
            "nonportable-authority-root",
            "authority path is not portable for a native Vault store",
        ) from error
    if any(
        part.casefold() in _EXCLUDED_AUTHORITY_COMPONENTS
        for part in PurePosixPath(path).parts
    ):
        raise VaultStoreError(
            "nonportable-authority-root",
            "authority path enters an excluded local-state directory",
        )


def _authority_inventory(
    vault: Vault,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, int]:
    roots = [
        {"kind": "concept", "path": vault.concept_root.relative_to(vault.root).as_posix()},
        {"kind": "field", "path": vault.field_root.relative_to(vault.root).as_posix()},
        {"kind": "topic", "path": vault.topic_root.relative_to(vault.root).as_posix()},
    ]
    for root in roots:
        _validate_authority_store_path(str(root["path"]))
    folded_roots = [
        tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in PurePosixPath(str(root["path"])).parts
        )
        for root in roots
    ]
    for index, left in enumerate(folded_roots):
        for right in folded_roots[index + 1 :]:
            common = min(len(left), len(right))
            if left[:common] == right[:common]:
                raise VaultStoreError(
                    "nonportable-authority-root",
                    "authority roots overlap on a portable filesystem",
                )
    root_kinds = {item["path"]: item["kind"] for item in roots}
    artifacts: list[dict[str, Any]] = []
    raw_bytes = 0
    for snapshot in snapshot_managed_markdown(vault):
        _validate_authority_store_path(snapshot.authority)
        raw_bytes += len(snapshot.data)
        kind = next(
            (
                value
                for root, value in root_kinds.items()
                if snapshot.authority.startswith(root + "/")
            ),
            None,
        )
        if kind is None:
            raise VaultStoreError(
                "invalid-authority-inventory", "managed note lies outside its configured root"
            )
        try:
            note = parse_native_markdown(
                snapshot.data, authority=snapshot.authority, path=snapshot.path
            )
        except NativeNoteError as error:
            raise VaultStoreError(error.code, "native authority note is invalid") from error
        artifacts.append(
            {
                "path": snapshot.authority,
                "kind": kind,
                "normalized_bytes": len(note.normalized_text.encode("utf-8")),
                "normalized_sha256": _sha256(note.normalized_text.encode("utf-8")),
            }
        )
    artifacts.sort(key=lambda item: item["path"])
    projection = [
        {
            "path": item["path"],
            "kind": item["kind"],
            "normalized_bytes": item["normalized_bytes"],
            "normalized_sha256": item["normalized_sha256"],
        }
        for item in artifacts
    ]
    return roots, artifacts, sha256_json(projection), raw_bytes


def _source_inventory(
    vault: Vault, ledger: SourceLedger
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], str]:
    if ledger.manifest is None:
        return None, [], [], sha256_json([])
    generation = str(ledger.generation_sha256)
    manifest_data = read_vault_relative_regular(
        vault,
        ".kgdistiller/sources/manifest.json",
        maximum=MAX_SOURCE_MANIFEST_BYTES,
    )
    if (
        manifest_data != canonical_json(ledger.manifest).encode("utf-8")
        or ledger.manifest.get("generation_sha256") != ledger.generation_sha256
    ):
        raise VaultStoreError(
            "stale-source-generation",
            "source manifest changed after the ledger generation was selected",
        )
    manifest_record = _file_record(
        ".kgdistiller/sources/manifest.json", manifest_data
    )
    artifacts: list[dict[str, Any]] = []
    for name in ("documents", "versions", "derivations"):
        source_record = ledger.manifest["artifacts"][name]
        relative = (
            f".kgdistiller/sources/generations/{generation}/"
            f"{source_record['path']}"
        )
        data = read_vault_relative_regular(
            vault,
            relative,
            maximum=max(1, min(MAX_ARTIFACT_BYTES, int(source_record["bytes"]))),
        )
        record = _file_record(relative, data)
        if (
            record["bytes"] != source_record["bytes"]
            or record["sha256"] != source_record["sha256"]
        ):
            raise VaultStoreError(
                "source-artifact-mismatch", "current source generation changed during capture"
            )
        artifacts.append(record)
    artifacts.sort(key=lambda item: item["path"])

    blobs: list[dict[str, Any]] = []
    by_path: dict[str, Mapping[str, Any]] = {}
    for version in ledger.versions:
        path = f".kgdistiller/sources/{version['blob_path']}"
        previous = by_path.setdefault(path, version)
        if (
            previous["raw_sha256"] != version["raw_sha256"]
            or previous["byte_count"] != version["byte_count"]
        ):
            raise VaultStoreError(
                "invalid-source-ledger", "shared source blob metadata is inconsistent"
            )
    for path, version in sorted(by_path.items()):
        data = read_vault_relative_regular(
            vault, path, maximum=max(1, min(MAX_SOURCE_BYTES, int(version["byte_count"])))
        )
        record = _file_record(path, data)
        if (
            record["bytes"] != version["byte_count"]
            or record["sha256"] != version["raw_sha256"]
        ):
            raise VaultStoreError(
                "source-blob-mismatch", "referenced source blob changed during capture"
            )
        blobs.append(record)
    inventory = sorted([manifest_record, *artifacts, *blobs], key=lambda item: item["path"])
    return manifest_record, artifacts, blobs, sha256_json(inventory)


def _semantic_graph_matches_evidence(state: Any, ledger: SourceLedger) -> bool:
    evidence = current_evidence_view(ledger)
    current_concepts: set[str] = set()
    for node_id, node in state.nodes.items():
        if node.get("type") != "knowledge":
            continue
        properties = node.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        text = str(node.get("text", ""))
        expected = (
            "pending"
            if not text.strip()
            else "current"
            if evidence.has_concept(str(node_id))
            else "needs-review"
        )
        if properties.get("curation_status") != expected:
            return False
        if expected == "current":
            current_concepts.add(str(node_id))
    if current_concepts != set(evidence.concept_ids):
        return False

    evidence_contains = {
        item for item in evidence.relations if item[1] == "contains"
    }
    evidence_semantic = {
        item for item in evidence.relations if item[1] != "contains"
    }
    graph_contains: set[tuple[str, str, str]] = set()
    current_relations: set[tuple[str, str, str]] = set()
    for edge in state.edges.values():
        relation = str(edge.get("relation", ""))
        if relation == "contains":
            graph_contains.add(
                (
                    str(edge.get("source", "")),
                    relation,
                    str(edge.get("target", "")),
                )
            )
            continue
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        expected = (
            "current"
            if evidence.has_relation(source, relation, target)
            else "needs-review"
        )
        if edge.get("curation_status") != expected:
            return False
        if expected == "current":
            if relation == "contrasts-with":
                source, target = sorted((source, target))
            current_relations.add((source, relation, target))
    if current_relations != evidence_semantic:
        return False
    allowed_contains_types = {
        ("field", "topic"),
        ("field", "knowledge"),
        ("topic", "knowledge"),
    }
    for source, relation, target in evidence_contains:
        if (source, relation, target) not in graph_contains:
            return False
        source_node = state.nodes.get(source)
        target_node = state.nodes.get(target)
        if source_node is None or target_node is None or (
            str(source_node.get("type", "")), str(target_node.get("type", ""))
        ) not in allowed_contains_types:
            return False
    return True


def _graph_inventory(
    vault: Vault, authority: Sequence[Mapping[str, Any]], ledger: SourceLedger
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        state, manifest, manifest_sha256 = _load_live_state_locked(vault)
        captured = _capture_live_graph(vault)
        names = _manifest_artifact_names(manifest)
        compilation = compile_vault(vault)
        validate_native_compilation(compilation)
    except (NativeCompilerError, VaultError, SourceArchiveError) as error:
        raise VaultStoreError(
            getattr(error, "code", "invalid-native-graph"),
            "native graph cannot be verified",
        ) from error
    if tuple(sorted(captured)) != names or captured != _expected_bytes(compilation):
        raise VaultStoreError(
            "stale-native-graph", "native graph is not the byte-exact authority compilation"
        )
    if not _semantic_graph_matches_evidence(compilation.state, ledger):
        raise VaultStoreError(
            "stale-native-graph",
            "native graph is not closed over effective source evidence",
        )
    expected_source_hashes = {
        str(item["path"]): str(item["normalized_sha256"]) for item in authority
    }
    if dict(state.manifest.get("source_hashes") or {}) != expected_source_hashes:
        raise VaultStoreError(
            "stale-native-graph", "native graph source hashes do not match native authority"
        )
    records = [
        _file_record(f".kgdistiller/graph/{name}", data)
        for name, data in sorted(captured.items())
    ]
    graph = {
        "artifacts": records,
        "manifest_sha256": manifest_sha256,
        "graph_sha256": str(state.manifest["graph_sha256"]),
        "registry_sha256": str(state.manifest["registry_sha256"]),
        "source_hashes_sha256": sha256_json(expected_source_hashes),
        "inventory_sha256": sha256_json(records),
    }
    return graph, records


def _receipt_inventory(
    vault: Vault, ledger: SourceLedger
) -> tuple[list[dict[str, Any]], str]:
    wanted: dict[str, list[Mapping[str, Any]]] = {}
    for row in ledger.derivations:
        receipt_sha256 = row.get("ingest_receipt_sha256")
        if row["status"] in {"committed", "reviewed-empty"} and receipt_sha256 is not None:
            wanted.setdefault(str(receipt_sha256), []).append(row)
    seen_wanted: set[str] = set()
    try:
        first, _ = _capture_receipt_inventory(vault, matching_request_id=None)
        records: list[dict[str, Any]] = []
        for name, token_digest in first:
            if name.endswith("/"):
                continue
            relative = f".kgdistiller/receipts/sha256/{name}"
            data = read_vault_relative_regular(
                vault, relative, maximum=MAX_RECEIPT_BYTES
            )
            receipt = _validated_receipt(data, expected_path=relative)
            if receipt["vault_id"] != vault.id or _sha256(data) != token_digest:
                raise VaultStoreError(
                    "invalid-receipt-store", "durable receipt inventory changed during capture"
                )
            receipt_digest = str(receipt["receipt_sha256"])
            if receipt_digest in wanted:
                receipt_summaries = {
                    str(item["version_id"]): item
                    for item in receipt["after"]["derivations"]
                }
                for row in wanted[receipt_digest]:
                    projection = {
                        key: row[key]
                        for key in (
                            "version_id",
                            "status",
                            "candidate_dispositions",
                            "concept_ids",
                            "concept_evidence",
                            "relation_evidence",
                        )
                    }
                    expected_summary = _derivation_summaries([projection])[0]
                    if (
                        receipt["after"]["graph_generation_sha256"]
                        != row["graph_generation_sha256"]
                        or receipt_summaries.get(str(row["version_id"]))
                        != expected_summary
                    ):
                        raise VaultStoreError(
                            "invalid-derivation-receipt-binding",
                            "effective source derivation does not match its durable receipt",
                        )
                seen_wanted.add(receipt_digest)
            records.append(
                {
                    "path": relative,
                    "bytes": len(data),
                    "sha256": _sha256(data),
                    "receipt_sha256": receipt_digest,
                }
            )
        second, _ = _capture_receipt_inventory(vault, matching_request_id=None)
    except (VaultIngestError, SourceArchiveError) as error:
        raise VaultStoreError(
            getattr(error, "code", "invalid-receipt-store"),
            "durable receipt store cannot be verified",
        ) from error
    if first != second:
        raise VaultStoreError(
            "stale-receipt-store", "durable receipt store changed during capture"
        )
    if set(wanted) != seen_wanted:
        raise VaultStoreError(
            "missing-derivation-receipt",
            "effective source derivation references a missing durable receipt",
        )
    records.sort(key=lambda item: item["path"])
    return records, sha256_json(records)


def _scaffold_inventory(
    roots: Sequence[Mapping[str, Any]],
    authority: Sequence[Mapping[str, Any]],
    *,
    source_present: bool,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    contents = {
        ".kgdistiller/.gitattributes": GITATTRIBUTES,
        ".kgdistiller/build/.gitignore": BUILD_GITIGNORE,
    }
    for root in roots:
        path = str(root["path"])
        if not any(str(item["path"]).startswith(path + "/") for item in authority):
            contents[f"{path}/.gitkeep"] = GITKEEP
    if not source_present:
        contents[".kgdistiller/sources/.gitkeep"] = GITKEEP
    records = [_file_record(path, data) for path, data in sorted(contents.items())]
    return records, contents


def _manifest_records(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = [manifest["vault"]["manifest"]]
    records.extend(manifest["authority"]["artifacts"])
    source = manifest["source"]
    if source["manifest"] is not None:
        records.append(source["manifest"])
    records.extend(source["artifacts"])
    records.extend(source["blobs"])
    records.extend(manifest["graph"]["artifacts"])
    records.extend(manifest["receipts"]["artifacts"])
    records.extend(manifest["scaffolds"])
    return records


def _capture_store(vault: Vault, *, layout: str) -> _CapturedStore:
    try:
        ledger = load_source_ledger(vault)
        vault_record, vault_manifest_sha256 = _vault_manifest_record(vault)
        roots, authority, authority_generation, authority_raw_bytes = _authority_inventory(vault)
        source_manifest, source_artifacts, blobs, source_inventory = _source_inventory(
            vault, ledger
        )
        graph, _ = _graph_inventory(vault, authority, ledger)
        receipts, receipt_inventory = _receipt_inventory(vault, ledger)
        scaffolds, _ = _scaffold_inventory(
            roots, authority, source_present=ledger.manifest is not None
        )
        if layout == "in-place":
            for scaffold in scaffolds:
                _read_record(vault, scaffold)
    except VaultStoreError:
        raise
    except (VaultError, SourceArchiveError, NativeCompilerError, VaultIngestError) as error:
        raise VaultStoreError(
            getattr(error, "code", "vault-store-capture-failed"),
            "portable Vault capture failed closed",
        ) from error

    source = {
        "manifest": source_manifest,
        "generation_sha256": ledger.generation_sha256,
        "artifacts": source_artifacts,
        "blobs": blobs,
        "inventory_sha256": source_inventory,
    }
    receipt_section = {
        "count": len(receipts),
        "artifacts": receipts,
        "inventory_sha256": receipt_inventory,
    }
    scaffold_digest = sha256_json(scaffolds)
    content_projection = {
        "vault_manifest_sha256": vault_manifest_sha256,
        "authority_generation_sha256": authority_generation,
        "source_inventory_sha256": source_inventory,
        "graph_inventory_sha256": graph["inventory_sha256"],
        "receipt_inventory_sha256": receipt_inventory,
        "scaffold_inventory_sha256": scaffold_digest,
    }
    paths = sorted(
        [str(item["path"]) for item in [
            vault_record,
            *authority,
            *([] if source_manifest is None else [source_manifest]),
            *source_artifacts,
            *blobs,
            *graph["artifacts"],
            *receipts,
            *scaffolds,
        ]] + [STORE_PATH]
    )
    manifest = finalize_self_digest(
        {
            "schema": STORE_SCHEMA,
            "generator": "kgdistiller",
            "layout": layout,
            "vault": {
                "id": vault.id,
                "manifest": vault_record,
                "manifest_sha256": vault_manifest_sha256,
            },
            "authority": {
                "roots": roots,
                "artifacts": authority,
                "generation_sha256": authority_generation,
            },
            "source": source,
            "graph": graph,
            "receipts": receipt_section,
            "scaffolds": scaffolds,
            "managed_paths": paths,
            "content_generation_sha256": sha256_json(content_projection),
        },
        "store_sha256",
    )
    try:
        validate_contract(manifest)
    except (ContractError, RecursionError) as error:
        raise VaultStoreError(
            "invalid-vault-store", "generated portable Vault store is not closed"
        ) from error
    declared_bytes = sum(_record_size(item) for item in _manifest_records(manifest))
    actual_bytes = len(_store_bytes(manifest)) + declared_bytes - sum(
        int(item["normalized_bytes"]) for item in authority
    ) + authority_raw_bytes
    if len(paths) > MAX_STORE_FILES or actual_bytes > MAX_VAULT_STORE_BYTES:
        raise VaultStoreError(
            "vault-store-too-large", "portable Vault inventory exceeds its aggregate bounds"
        )
    _store_bytes(manifest)
    return _CapturedStore(vault=vault, manifest=manifest, ledger=ledger)


def _pending_transaction(vault: Vault) -> bool:
    for relative in (GRAPH_TRANSACTION_PATH, VAULT_INGEST_JOURNAL_PATH):
        metadata = _lstat(vault.root.joinpath(*PurePosixPath(relative).parts))
        if metadata is not None:
            return True
    return False


def _report(action: str, captured: _CapturedStore) -> dict[str, Any]:
    manifest = captured.manifest
    ledger = captured.ledger
    report = {
        "schema": REPORT_SCHEMA,
        "action": action,
        "status": "verified",
        "artifact_schema": STORE_SCHEMA,
        "layout": manifest["layout"],
        "vault_id": manifest["vault"]["id"],
        "store_sha256": manifest["store_sha256"],
        "content_generation_sha256": manifest["content_generation_sha256"],
        "source_generation_sha256": manifest["source"]["generation_sha256"],
        "graph_sha256": manifest["graph"]["graph_sha256"],
        "counts": {
            "authority": len(manifest["authority"]["artifacts"]),
            "documents": len(ledger.documents),
            "versions": len(ledger.versions),
            "derivations": len(ledger.derivations),
            "blobs": len(manifest["source"]["blobs"]),
            "graph_artifacts": len(manifest["graph"]["artifacts"]),
            "receipts": len(manifest["receipts"]["artifacts"]),
        },
    }
    try:
        return validate_contract(report)
    except ContractError as error:
        raise VaultStoreError("invalid-vault-store-report", "Vault store report is not closed") from error


def _load_store_manifest(vault: Vault) -> tuple[dict[str, Any], bytes]:
    try:
        data = read_vault_relative_regular(
            vault, STORE_PATH, maximum=MAX_STORE_MANIFEST_BYTES
        )
    except SourceArchiveError as error:
        raise VaultStoreError("missing-vault-store", "portable Vault store manifest is missing") from error
    payload = _strict_json(data, kind="vault-store")
    try:
        manifest = validate_contract(payload)
    except (ContractError, RecursionError) as error:
        raise VaultStoreError("invalid-vault-store", "portable Vault store manifest is invalid") from error
    if manifest.get("schema") != STORE_SCHEMA or data != _store_bytes(manifest):
        raise VaultStoreError("invalid-vault-store", "portable Vault store manifest is not canonical")
    if manifest["vault"]["id"] != vault.id:
        raise VaultStoreError("vault-store-id-mismatch", "portable Vault store belongs to another Vault")
    return manifest, data


def _verify_declared_files(
    vault: Vault, manifest: Mapping[str, Any]
) -> tuple[tuple[str, str], ...]:
    token: list[tuple[str, str]] = []
    total = len(_store_bytes(manifest))
    if total > MAX_VAULT_STORE_BYTES:
        raise VaultStoreError(
            "vault-store-too-large", "portable Vault inventory exceeds its aggregate byte bound"
        )
    for record in sorted(_manifest_records(manifest), key=lambda item: item["path"]):
        data = _read_record(vault, record)
        total += len(data)
        if total > MAX_VAULT_STORE_BYTES:
            raise VaultStoreError(
                "vault-store-too-large", "portable Vault inventory exceeds its aggregate byte bound"
            )
        if "receipt_sha256" in record:
            receipt = _validated_receipt(data, expected_path=str(record["path"]))
            if receipt["receipt_sha256"] != record["receipt_sha256"]:
                raise VaultStoreError(
                    "invalid-receipt-store", "durable receipt identity does not match its inventory"
                )
        token.append((str(record["path"]), _sha256(data)))
    return tuple(token)


def _walk_tree(
    root: Path,
    *,
    ignore_git: bool,
    budget: dict[str, int] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    files: list[str] = []
    directories: list[str] = []
    folded: dict[str, str] = {}
    pending: list[tuple[Path, int]] = [(root, 0)]
    remaining = (
        {
            "files": MAX_STORE_FILES,
            "directories": MAX_LOCAL_STORE_DIRECTORIES,
            "directory_path_bytes": MAX_LOCAL_DIRECTORY_PATH_BYTES,
            "bytes": MAX_VAULT_STORE_BYTES,
        }
        if budget is None
        else budget
    )
    while pending:
        directory, depth = pending.pop()
        if depth > 64:
            raise VaultStoreError(
                "vault-store-too-deep", "portable Vault directory depth exceeds its bound"
            )
        try:
            with _PinnedDirectory(directory) as pinned:
                with os.scandir(directory if os.name == "nt" else pinned.dir_fd) as scanner:
                    entries: list[tuple[str, os.stat_result, str]] = []
                    for entry in scanner:
                        name = entry.name
                        if directory == root and ignore_git and name == ".git":
                            continue
                        path = directory / name
                        metadata = pinned.lstat_leaf(name)
                        if metadata is None:
                            raise VaultStoreError(
                                "unstable-vault-store",
                                "portable Vault entry disappeared during inventory",
                            )
                        relative = path.relative_to(root).as_posix()
                        try:
                            _validate_portable_path(
                                relative, field="portable Vault entry"
                            )
                        except ContractError as error:
                            raise VaultStoreError(
                                "unsafe-vault-store",
                                "portable Vault entry path is not host-neutral",
                            ) from error
                        folded_key = unicodedata.normalize("NFC", relative).casefold()
                        previous = folded.setdefault(folded_key, relative)
                        if previous != relative:
                            raise VaultStoreError(
                                "colliding-vault-store-paths",
                                "portable Vault paths collide on a supported filesystem",
                            )
                        if _is_link_or_reparse(path, metadata):
                            raise VaultStoreError(
                                "unsafe-vault-store",
                                "portable Vault contains a symlink or reparse point",
                            )
                        if stat.S_ISDIR(metadata.st_mode):
                            remaining["directories"] -= 1
                            remaining["directory_path_bytes"] -= len(
                                relative.encode("utf-8", errors="strict")
                            )
                            if (
                                remaining["directories"] < 0
                                or remaining["directory_path_bytes"] < 0
                            ):
                                raise VaultStoreError(
                                    "vault-store-too-large",
                                    "portable Vault tree exceeds its directory bound",
                                )
                            kind = "directory"
                        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                            remaining["files"] -= 1
                            if remaining["files"] < 0:
                                raise VaultStoreError(
                                    "vault-store-too-large",
                                    "portable Vault tree exceeds its file bound",
                                )
                            remaining["bytes"] -= int(metadata.st_size)
                            if remaining["bytes"] < 0:
                                raise VaultStoreError(
                                    "vault-store-too-large",
                                    "portable Vault tree exceeds its aggregate byte bound",
                                )
                            kind = "file"
                        else:
                            raise VaultStoreError(
                                "unsafe-vault-store",
                                "portable Vault contains a non-ordinary file",
                            )
                        entries.append((name, metadata, kind))
                for name, metadata, kind in sorted(entries, key=lambda item: item[0]):
                    path = directory / name
                    relative = path.relative_to(root).as_posix()
                    if kind == "directory":
                        directories.append(relative)
                        pending.append((path, depth + 1))
                    else:
                        files.append(relative)
                pinned.verify_current()
        except VaultStoreError:
            raise
        except (OSError, SourceArchiveError) as error:
            raise VaultStoreError("unsafe-vault-store", "portable Vault tree cannot be read safely") from error
    return tuple(sorted(files)), tuple(sorted(directories))


def _parent_directories(paths: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            result.add(PurePosixPath(*parts[:index]).as_posix())
    return result


def _pending_token(vault: Vault) -> tuple[tuple[str, bool], ...]:
    result: list[tuple[str, bool]] = []
    for relative in (GRAPH_TRANSACTION_PATH, VAULT_INGEST_JOURNAL_PATH):
        metadata = _lstat(vault.root.joinpath(*PurePosixPath(relative).parts))
        result.append((relative, metadata is not None))
    return tuple(result)


def _shallow_namespace(
    path: Path, *, maximum_entries: int, label: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        with _PinnedDirectory(path) as parent:
            with os.scandir(path if os.name == "nt" else parent.dir_fd) as scanner:
                for entry in scanner:
                    if len(result) >= maximum_entries:
                        raise VaultStoreError(
                            "vault-store-inventory-mismatch",
                            f"{label} contains too many top-level entries",
                        )
                    try:
                        _validate_portable_path(entry.name, field=label)
                    except ContractError as error:
                        raise VaultStoreError(
                            "unsafe-vault-store",
                            f"{label} contains a nonportable entry",
                        ) from error
                    metadata = parent.lstat_leaf(entry.name)
                    if metadata is None or _is_link_or_reparse(
                        path / entry.name, metadata
                    ):
                        raise VaultStoreError(
                            "unsafe-vault-store", f"{label} contains an unsafe entry"
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        kind = "directory"
                    elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                        kind = "file"
                    else:
                        raise VaultStoreError(
                            "unsafe-vault-store",
                            f"{label} contains a non-ordinary entry",
                        )
                    folded = unicodedata.normalize("NFC", entry.name).casefold()
                    if any(
                        unicodedata.normalize("NFC", existing).casefold() == folded
                        for existing in result
                    ):
                        raise VaultStoreError(
                            "colliding-vault-store-paths",
                            f"{label} contains colliding entries",
                        )
                    result[entry.name] = kind
            parent.verify_current()
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "unsafe-vault-store", f"{label} could not be read safely"
        ) from error
    return result


def _capture_store_namespace(
    vault: Vault,
    manifest: Mapping[str, Any],
    *,
    require_store: bool = True,
    allow_missing_scaffolds: bool = False,
) -> tuple[tuple[str, str], ...]:
    metadata = _shallow_namespace(
        vault.root / ".kgdistiller",
        maximum_entries=8,
        label="Vault metadata directory",
    )
    required = {
        "vault.json": "file",
        "build": "directory",
        "graph": "directory",
        "sources": "directory",
    }
    if not allow_missing_scaffolds:
        required[".gitattributes"] = "file"
    if require_store:
        required["store.json"] = "file"
    allowed = dict(required)
    allowed[".gitattributes"] = "file"
    allowed["store.json"] = "file"
    allowed["receipts"] = "directory"
    if any(metadata.get(name) != kind for name, kind in required.items()) or any(
        name not in allowed or allowed[name] != kind
        for name, kind in metadata.items()
    ):
        raise VaultStoreError(
            "vault-store-inventory-mismatch",
            "Vault metadata namespace contains an untracked entry",
        )
    receipt_namespace: dict[str, str] = {}
    if metadata.get("receipts") == "directory":
        receipt_namespace = _shallow_namespace(
            vault.root / ".kgdistiller" / "receipts",
            maximum_entries=1,
            label="durable receipt namespace",
        )
        if any(
            name != "sha256" or kind != "directory"
            for name, kind in receipt_namespace.items()
        ):
            raise VaultStoreError(
                "vault-store-inventory-mismatch",
                "durable receipt namespace contains an untracked entry",
            )
    if manifest["receipts"]["artifacts"] and (
        metadata.get("receipts") != "directory"
        or receipt_namespace.get("sha256") != "directory"
    ):
        raise VaultStoreError(
            "vault-store-inventory-mismatch", "durable receipt namespace is missing"
        )

    sources = _shallow_namespace(
        vault.root / ".kgdistiller" / "sources",
        maximum_entries=4,
        label="source archive directory",
    )
    if manifest["source"]["manifest"] is None:
        required_sources = (
            {} if allow_missing_scaffolds else {".gitkeep": "file"}
        )
        allowed_sources = {
            **required_sources,
            "generations": "directory",
            "blobs": "directory",
        }
    else:
        required_sources = {
            "manifest.json": "file",
            "generations": "directory",
            "blobs": "directory",
        }
        allowed_sources = {**required_sources, ".gitkeep": "file"}
    if any(
        sources.get(name) != kind for name, kind in required_sources.items()
    ) or any(
        name not in allowed_sources or allowed_sources[name] != kind
        for name, kind in sources.items()
    ):
        raise VaultStoreError(
            "vault-store-inventory-mismatch",
            "source archive namespace contains an untracked entry",
        )
    if ".gitkeep" in sources:
        with _PinnedDirectory(vault.root / ".kgdistiller" / "sources") as parent:
            gitkeep, _ = _stable_pinned_file(
                parent,
                parent.path,
                ".gitkeep",
                maximum=1,
                links=1,
            )
            if gitkeep:
                raise VaultStoreError(
                    "vault-store-inventory-mismatch",
                    "source scaffold .gitkeep is not empty",
                )
    local_tokens: list[tuple[str, str]] = [
        ("metadata", f".kgdistiller/{name}:{kind}")
        for name, kind in metadata.items()
    ]
    local_tokens.extend(
        ("source-namespace", f".kgdistiller/sources/{name}:{kind}")
        for name, kind in sources.items()
    )
    local_tokens.extend(
        ("receipt-namespace", f".kgdistiller/receipts/{name}:{kind}")
        for name, kind in receipt_namespace.items()
    )

    return tuple(sorted(local_tokens))


def _capture_controlled_inventory(
    vault: Vault,
    manifest: Mapping[str, Any],
    *,
    require_store: bool = True,
    allow_missing_scaffolds: bool = False,
) -> tuple[tuple[str, str], ...]:
    namespace = _capture_store_namespace(
        vault,
        manifest,
        require_store=require_store,
        allow_missing_scaffolds=allow_missing_scaffolds,
    )
    authority_roots = {
        str(item["path"]) for item in manifest["authority"]["roots"]
    }
    authority_expected = {
        str(item["path"]) for item in manifest["authority"]["artifacts"]
    } | (set() if allow_missing_scaffolds else {
        str(item["path"])
        for item in manifest["scaffolds"]
        if any(
            str(item["path"]) == f"{root}/.gitkeep"
            for root in authority_roots
        )
    })
    actual_authority: set[str] = set()
    controlled: list[tuple[str, str]] = list(namespace)
    budget = {
        "files": MAX_STORE_FILES,
        "directories": MAX_LOCAL_STORE_DIRECTORIES,
        "directory_path_bytes": MAX_LOCAL_DIRECTORY_PATH_BYTES,
        "bytes": MAX_VAULT_STORE_BYTES,
    }
    for root_record in manifest["authority"]["roots"]:
        root = vault.root.joinpath(*PurePosixPath(str(root_record["path"])).parts)
        files, directories = _walk_tree(
            root, ignore_git=False, budget=budget
        )
        expected_within = {
            path.removeprefix(f"{root_record['path']}/")
            for path in authority_expected
            if path.startswith(f"{root_record['path']}/")
        }
        required_directories = _parent_directories(tuple(expected_within))
        actual_within = set(files)
        stale_scaffolds = actual_within - expected_within
        if stale_scaffolds == {".gitkeep"}:
            with _PinnedDirectory(root) as parent:
                stale_bytes, _ = _stable_pinned_file(
                    parent,
                    root,
                    ".gitkeep",
                    maximum=1,
                    links=1,
                )
                if stale_bytes:
                    raise VaultStoreError(
                        "vault-store-inventory-mismatch",
                        "authority scaffold .gitkeep is not empty",
                    )
        elif stale_scaffolds:
            raise VaultStoreError(
                "vault-store-inventory-mismatch",
                "managed authority root contains an untracked file",
            )
        if not expected_within.issubset(actual_within) or not required_directories.issubset(
            set(directories)
        ):
            raise VaultStoreError(
                "vault-store-inventory-mismatch", "managed authority root layout is not exact"
            )
        controlled.extend(
            ("directory", f"{root_record['path']}/{path}") for path in directories
        )
        for path in files:
            full_path = f"{root_record['path']}/{path}"
            if path in expected_within:
                actual_authority.add(full_path)
            controlled.append(("file", f"{root_record['path']}/{path}"))
    if actual_authority != authority_expected:
        raise VaultStoreError(
            "vault-store-inventory-mismatch", "managed authority roots contain an untracked file"
        )

    graph_expected = {
        str(item["path"]).removeprefix(".kgdistiller/graph/")
        for item in manifest["graph"]["artifacts"]
    }
    graph_files, graph_directories = _walk_tree(
        vault.root / ".kgdistiller" / "graph",
        ignore_git=False,
        budget=budget,
    )
    if set(graph_files) != graph_expected or set(graph_directories) != _parent_directories(
        tuple(graph_expected)
    ):
        raise VaultStoreError(
            "vault-store-inventory-mismatch", "native graph contains an untracked artifact"
        )
    controlled.extend(("file", f".kgdistiller/graph/{path}") for path in graph_files)
    controlled.extend(
        ("directory", f".kgdistiller/graph/{path}") for path in graph_directories
    )

    source = manifest["source"]
    if source["generation_sha256"] is not None:
        generation = str(source["generation_sha256"])
        generation_root = vault.root / ".kgdistiller" / "sources" / "generations" / generation
        expected_generation = {
            str(item["path"]).removeprefix(
                f".kgdistiller/sources/generations/{generation}/"
            )
            for item in source["artifacts"]
        }
        source_files, source_directories = _walk_tree(
            generation_root, ignore_git=False, budget=budget
        )
        if set(source_files) != expected_generation or set(
            source_directories
        ) != _parent_directories(tuple(expected_generation)):
            raise VaultStoreError(
                "vault-store-inventory-mismatch", "current source generation contains an untracked artifact"
            )
        controlled.extend(
            (
                "file",
                f".kgdistiller/sources/generations/{generation}/{path}",
            )
            for path in source_files
        )

    receipt_files = {
        str(item["path"]).removeprefix(".kgdistiller/receipts/sha256/")
        for item in manifest["receipts"]["artifacts"]
    }
    receipt_root = vault.root / ".kgdistiller" / "receipts" / "sha256"
    if _lstat(receipt_root) is not None:
        actual_receipts, receipt_directories = _walk_tree(
            receipt_root, ignore_git=False, budget=budget
        )
        required_receipt_directories = _parent_directories(tuple(receipt_files))
        actual_receipt_directories = set(receipt_directories)
        if (
            set(actual_receipts) != receipt_files
            or not required_receipt_directories.issubset(
                actual_receipt_directories
            )
            or any(
                re.fullmatch(r"[0-9a-f]{2}", path) is None
                for path in actual_receipt_directories
            )
        ):
            raise VaultStoreError(
                "vault-store-inventory-mismatch", "durable receipt tree is not exact"
            )
        controlled.extend(
            ("file", f".kgdistiller/receipts/sha256/{path}")
            for path in actual_receipts
        )
        controlled.extend(
            ("directory", f".kgdistiller/receipts/sha256/{path}")
            for path in receipt_directories
        )
    elif receipt_files:
        raise VaultStoreError(
            "vault-store-inventory-mismatch", "durable receipt tree is missing"
        )
    for item in manifest["scaffolds"]:
        path = str(item["path"])
        target = vault.root.joinpath(*PurePosixPath(path).parts)
        if _lstat(target) is None:
            if not allow_missing_scaffolds:
                raise VaultStoreError(
                    "vault-store-inventory-mismatch",
                    "required portable scaffold is missing",
                )
            controlled.append(("missing-scaffold", path))
            continue
        data = _read_record(vault, item)
        controlled.append(("file", f"{path}:{_sha256(data)}"))
    controlled.append(("file", STORE_PATH))
    return tuple(sorted(set(controlled)))


def _verify_vault_store(target: Path | str) -> dict[str, Any]:
    """Purely read and verify one portable native Vault store."""

    try:
        vault = load_vault(target)
    except VaultError as error:
        raise VaultStoreError(error.code, "target is not a valid portable Vault") from error
    first_pending = _pending_token(vault)
    if any(present for _, present in first_pending):
        raise VaultStoreError(
            "pending-native-transaction",
            "portable Vault has a pending native transaction and was not modified",
        )
    first_manifest, first_bytes = _load_store_manifest(vault)
    first_controlled = _capture_controlled_inventory(vault, first_manifest)
    first_token = _verify_declared_files(vault, first_manifest)
    captured = _capture_store(vault, layout=str(first_manifest["layout"]))
    if captured.manifest != first_manifest:
        raise VaultStoreError(
            "vault-store-content-mismatch", "portable Vault content does not match store.json"
        )
    _vault_store_hook("between-verify-passes", "")
    second_controlled = _capture_controlled_inventory(vault, first_manifest)
    second_pending = _pending_token(vault)
    second_token = _verify_declared_files(vault, first_manifest)
    second_manifest, second_bytes = _load_store_manifest(vault)
    if (
        first_token != second_token
        or first_controlled != second_controlled
        or first_pending != second_pending
        or any(present for _, present in second_pending)
        or first_bytes != second_bytes
        or second_manifest != first_manifest
    ):
        raise VaultStoreError(
            "unstable-vault-store", "portable Vault changed during bounded verification"
        )
    return _report("verify", captured)


def verify_vault_store(target: Path | str) -> dict[str, Any]:
    """Purely read and verify one portable native Vault store, failing closed."""

    try:
        return _verify_vault_store(target)
    except VaultStoreError:
        raise
    except (
        OSError,
        ContractError,
        NativeCompilerError,
        NativeNoteError,
        SourceArchiveError,
        VaultError,
        VaultIngestError,
        RecursionError,
    ) as error:
        raise VaultStoreError(
            "vault-store-verify-failed",
            "portable Vault verification failed closed",
        ) from error


def _scaffold_contents(captured: _CapturedStore) -> dict[str, bytes]:
    _, contents = _scaffold_inventory(
        captured.manifest["authority"]["roots"],
        captured.manifest["authority"]["artifacts"],
        source_present=captured.manifest["source"]["manifest"] is not None,
    )
    return contents


def _ensure_scaffolds(
    vault: Vault,
    roots: Sequence[Mapping[str, Any]],
    authority: Sequence[Mapping[str, Any]],
    *,
    source_present: bool,
) -> list[_OwnedLeaf]:
    _, contents = _scaffold_inventory(
        roots, authority, source_present=source_present
    )
    owned: list[_OwnedLeaf] = []
    try:
        for relative, expected in sorted(contents.items()):
            temporary = f".store-scaffold-{_sha256(expected + relative.encode('utf-8'))[:24]}"
            created = _install_noreplace(
                vault, relative, expected, temporary=temporary
            )
            if created is not None:
                owned.append(created)
            _vault_store_hook("after-scaffold", relative)
    except BaseException:
        _rollback_owned_leaves(vault, owned)
        raise
    return owned


def _read_store_pointer(vault: Vault) -> bytes | None:
    metadata = _lstat(vault.root / STORE_PATH)
    if metadata is None:
        return None
    try:
        return read_vault_relative_regular(
            vault, STORE_PATH, maximum=MAX_STORE_MANIFEST_BYTES
        )
    except SourceArchiveError as error:
        raise VaultStoreError(
            "unsafe-vault-store", "existing store.json is not an ordinary bounded file"
        ) from error


def _classify_store_pointer(
    vault: Vault, *, old: bytes | None, new: bytes
) -> str:
    try:
        current = _read_store_pointer(vault)
    except VaultStoreError:
        return "third"
    if current == new:
        return "new"
    if current == old:
        return "old"
    return "third"


def _cleanup_store_stage(vault: Vault, manifest: Mapping[str, Any]) -> bool:
    data = _store_bytes(manifest)
    leaf = f".store-{manifest['store_sha256']}.json"
    inner = f".{leaf}.write"
    parent_path = vault.root / ".kgdistiller" / "build"
    try:
        with _PinnedDirectory(parent_path) as parent:
            exists, metadata = _normalize_noreplace_in_parent(
                parent, parent_path, leaf, data, inner
            )
            if not exists:
                return True
            if metadata is None:
                return False
            staged, opened = _stable_pinned_file(
                parent,
                parent_path,
                leaf,
                maximum=max(1, len(data)),
                links=1,
            )
            if staged != data or not os.path.samestat(metadata, opened):
                return False
            removed = parent.cleanup_owned_leaf_raw(leaf, opened)
            parent.verify_current()
            return removed
    except (OSError, SourceArchiveError, VaultStoreError):
        return False


def _cleanup_store_stage_retained(
    parent: _PinnedDirectory,
    parent_path: Path,
    temporary: str,
    data: bytes,
) -> bool:
    try:
        exists, metadata = _normalize_noreplace_in_parent(
            parent,
            parent_path,
            temporary,
            data,
            f".{temporary}.write",
        )
        if not exists:
            parent.verify_current()
            return True
        if metadata is None:
            return False
        staged, opened = _stable_pinned_file(
            parent,
            parent_path,
            temporary,
            maximum=max(1, len(data)),
            links=1,
        )
        if staged != data or not os.path.samestat(metadata, opened):
            return False
        removed = parent.cleanup_owned_leaf_raw(temporary, opened)
        parent.verify_current()
        return removed
    except (OSError, SourceArchiveError, VaultStoreError):
        return False


def _retained_store_pointer(
    parent: _PinnedDirectory, parent_path: Path
) -> bytes | None:
    current = parent.lstat_leaf("store.json")
    if current is None:
        return None
    data, _ = _stable_pinned_file(
        parent,
        parent_path,
        "store.json",
        maximum=MAX_STORE_MANIFEST_BYTES,
        links=1,
    )
    return data


def _assert_scaffolds_current(vault: Vault, manifest: Mapping[str, Any]) -> None:
    for record in manifest["scaffolds"]:
        _read_record(vault, record)


def _publish_store_manifest(
    vault: Vault,
    manifest: Mapping[str, Any],
    *,
    expected_old: bytes | None,
) -> None:
    # The store pointer is the sole replaceable live file in F6. Its temporary
    # image remains under excluded build state rather than beside portable data.
    data = _store_bytes(manifest)
    temporary = f".store-{manifest['store_sha256']}.json"
    temporary_relative = f".kgdistiller/build/{temporary}"
    try:
        _install_noreplace(
            vault,
            temporary_relative,
            data,
            temporary=f".{temporary}.write",
        )
    except Exception as error:
        if isinstance(error, VaultStoreError):
            raise
        raise VaultStoreError(
            "store-publication-failed",
            "portable store stage could not be prepared safely",
        ) from error

    source_path = vault.root / ".kgdistiller" / "build"
    destination_path = vault.root / ".kgdistiller"
    try:
        with _PinnedDirectory(source_path) as source_parent:
            with _PinnedDirectory(destination_path) as destination_parent:
                try:
                    staged, staged_metadata = _stable_pinned_file(
                        source_parent,
                        source_path,
                        temporary,
                        maximum=max(1, len(data)),
                        links=1,
                    )
                    if staged != data:
                        raise VaultStoreError(
                            "store-stage-conflict",
                            "portable store stage changed before publication",
                        )
                    source_descriptor = source_parent.open_existing_file(
                        temporary, delete_access=True
                    )
                    try:
                        _verify_opened_file_exact(
                            source_parent,
                            source_path,
                            temporary,
                            source_descriptor,
                            data,
                            initial=staged_metadata,
                        )
                        _assert_scaffolds_current(vault, manifest)
                        _vault_store_hook("before-store-replace", STORE_PATH)

                        # A writer-unaware editor can mutate native authority
                        # while the deterministic store stage is being
                        # prepared. Re-capture every controlled generation
                        # after the last callback, not just the fixed
                        # scaffolds, and then proceed directly to the retained
                        # source/destination CAS below without another hook.
                        recaptured = _capture_store(vault, layout="in-place")
                        if recaptured.manifest != manifest:
                            raise VaultStoreError(
                                "stale-vault-store",
                                "Vault content changed immediately before publication",
                            )
                        _capture_controlled_inventory(
                            vault, recaptured.manifest, require_store=False
                        )

                        # The hook boundary is followed by the final source and
                        # destination checks with no further callback before the
                        # native rename. The source descriptor stays retained.
                        source_metadata = _verify_opened_file_exact(
                            source_parent,
                            source_path,
                            temporary,
                            source_descriptor,
                            data,
                            initial=staged_metadata,
                        )
                        current_bytes = _retained_store_pointer(
                            destination_parent, destination_path
                        )
                        if current_bytes != expected_old:
                            raise VaultStoreError(
                                "stale-vault-store",
                                "store.json changed before publication",
                            )
                        if current_bytes == data:
                            os.close(source_descriptor)
                            source_descriptor = -1
                            if not source_parent.cleanup_owned_leaf_raw(
                                temporary, source_metadata
                            ):
                                raise VaultStoreError(
                                    "store-stage-conflict",
                                    "portable store stage changed during cleanup",
                                )
                            source_parent.verify_current()
                            destination_parent.verify_current()
                            return
                        source_current = source_parent.lstat_leaf(temporary)
                        if source_current is None or not os.path.samestat(
                            source_metadata, source_current
                        ):
                            raise VaultStoreError(
                                "store-stage-conflict",
                                "portable store stage changed immediately before publication",
                            )
                        if os.name == "nt":
                            import msvcrt
                            from . import source_archive as archive_module

                            archive_module._win_rename_handle(
                                msvcrt.get_osfhandle(source_descriptor),
                                vault.root / STORE_PATH,
                            )
                        else:
                            os.replace(
                                temporary,
                                "store.json",
                                src_dir_fd=source_parent.dir_fd,
                                dst_dir_fd=destination_parent.dir_fd,
                            )
                            os.fsync(source_parent.dir_fd)
                            os.fsync(destination_parent.dir_fd)
                    finally:
                        if source_descriptor >= 0:
                            os.close(source_descriptor)
                    _vault_store_hook("after-store-replace", STORE_PATH)
                    installed, _ = _stable_pinned_file(
                        destination_parent,
                        destination_path,
                        "store.json",
                        maximum=max(1, len(data)),
                        links=1,
                    )
                    if installed != data:
                        raise VaultStoreError(
                            "store-publication-failed", "published store.json changed"
                        )
                    source_parent.verify_current()
                    destination_parent.verify_current()
                except Exception as error:
                    try:
                        source_parent.verify_current()
                        destination_parent.verify_current()
                    except (OSError, SourceArchiveError) as ancestry_error:
                        raise VaultStoreError(
                            "store-publication-uncertain",
                            "store publication ancestry changed and was not reclassified",
                        ) from ancestry_error
                    try:
                        current = _retained_store_pointer(
                            destination_parent, destination_path
                        )
                    except VaultStoreError:
                        current = object()
                    if current == data:
                        cleaned = _cleanup_store_stage_retained(
                            source_parent, source_path, temporary, data
                        )
                        if not cleaned:
                            raise VaultStoreError(
                                "store-publication-uncertain",
                                "store.json is new but its retained stage could not be cleaned",
                            ) from error
                        source_parent.verify_current()
                        destination_parent.verify_current()
                        return
                    if current == expected_old:
                        cleaned = _cleanup_store_stage_retained(
                            source_parent, source_path, temporary, data
                        )
                        if not cleaned:
                            raise VaultStoreError(
                                "store-publication-rollback-failed",
                                "store.json stayed old but its retained stage could not be cleaned",
                            ) from error
                        source_parent.verify_current()
                        destination_parent.verify_current()
                        if isinstance(error, VaultStoreError):
                            raise error
                        raise VaultStoreError(
                            "store-publication-failed",
                            "store.json could not be published atomically",
                        ) from error
                    raise VaultStoreError(
                        "store-publication-uncertain",
                        "store.json entered an unrecognized publication state",
                    ) from error
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "store-publication-uncertain",
            "store publication parents could not be retained safely",
        ) from error


def _selected_vault(vault_id: str, home: Path | str | None) -> tuple[Any, Vault]:
    registry = load_registry(home, validate_vaults=False)
    matches = [item for item in registry.registrations if item.id == vault_id]
    if len(matches) != 1:
        raise VaultStoreError("vault-not-registered", "requested Vault is not registered")
    try:
        vault = load_vault(matches[0].path, expected_id=vault_id)
    except VaultError as error:
        raise VaultStoreError(error.code, "registered Vault cannot be loaded safely") from error
    return registry, vault


def _portable_absolute_components(path: Path | str) -> tuple[str, ...]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return tuple(
        unicodedata.normalize("NFC", part).casefold() for part in absolute.parts
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_parts = _portable_absolute_components(left)
    right_parts = _portable_absolute_components(right)
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def _portable_leaf_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _external_destination(
    output: Path | str,
    *,
    registry: Any,
    vault: Vault,
    require_absent: bool,
) -> Path:
    raw = os.fspath(output)
    if "\0" in raw:
        raise VaultStoreError("unsafe-snapshot-output", "snapshot output contains a NUL byte")
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise VaultStoreError(
            "unsafe-snapshot-output", "snapshot output is not valid UTF-8"
        ) from error
    if len(encoded) > 4096:
        raise VaultStoreError(
            "unsafe-snapshot-output", "snapshot output exceeds its path bound"
        )
    destination = Path(os.path.abspath(raw))
    if _is_filesystem_root(destination) or not destination.name:
        raise VaultStoreError(
            "unsafe-snapshot-output", "snapshot output must not be a filesystem root"
        )
    protected = [registry.home, vault.root]
    protected.extend(item.path for item in registry.registrations)
    if any(_paths_overlap(destination, Path(path)) for path in protected):
        raise VaultStoreError(
            "unsafe-snapshot-output",
            "snapshot output overlaps a Vault or registry root",
        )
    try:
        with _PinnedDirectory(destination.parent) as parent:
            current = parent.lstat_leaf(destination.name)
            if require_absent and current is not None:
                raise VaultStoreError(
                    "snapshot-output-exists", "snapshot output already exists"
                )
            parent.verify_current()
            if any(_paths_overlap(destination, Path(path)) for path in protected):
                raise VaultStoreError(
                    "unsafe-snapshot-output",
                    "snapshot output overlaps a Vault or registry root",
                )
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "unsafe-snapshot-output", "snapshot output parent is not safely anchored"
        ) from error
    return destination


def _ensure_snapshot_directory(path: Path) -> _OwnedDirectory | None:
    parent_path = path.parent
    leaf = path.name
    created = False
    try:
        with _PinnedDirectory(parent_path) as parent:
            current = parent.lstat_leaf(leaf)
            if current is None:
                try:
                    parent.mkdir_leaf(leaf)
                    created = True
                    if os.name != "nt":
                        os.fsync(parent.dir_fd)
                except FileExistsError:
                    created = False
                current = parent.lstat_leaf(leaf)
            if (
                current is None
                or not stat.S_ISDIR(current.st_mode)
                or _is_link_or_reparse(path, current)
            ):
                raise VaultStoreError(
                    "unsafe-snapshot-stage",
                    "snapshot stage directory is not an ordinary directory",
                )
            parent.verify_current()
        with _PinnedDirectory(path) as directory:
            directory.verify_current()
        return _OwnedDirectory(path, current) if created else None
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "snapshot-stage-create-failed",
            "snapshot stage directory could not be created safely",
        ) from error


def _snapshot_stage_name(destination: Path) -> str:
    identity = _sha256(
        _portable_leaf_identity(destination.name).encode("utf-8", errors="strict")
    )
    # The closed marker still binds the complete destination identity. A
    # 96-bit name keeps the retained stage usable under the legacy Windows
    # MAX_PATH boundary while collisions remain fail-closed by marker content.
    return f".kgdistiller-store-{identity[:24]}.stage"


def _snapshot_bootstrap_name(stage_leaf: str) -> str:
    return f"{stage_leaf}.bootstrap"


def _snapshot_manifest_matches(
    candidate: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    return bool(
        candidate.get("store_sha256") == expected["store_sha256"]
        and candidate.get("layout") == "snapshot-copy"
        and candidate.get("vault", {}).get("id") == expected["vault"]["id"]
        and candidate.get("content_generation_sha256")
        == expected["content_generation_sha256"]
    )


def _snapshot_stage_marker(
    destination: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    return finalize_self_digest(
        {
            "schema": "qlkg-vault-store-stage-v1",
            "destination_leaf_sha256": _sha256(
                _portable_leaf_identity(destination.name).encode(
                    "utf-8", errors="strict"
                )
            ),
            "vault_id": manifest["vault"]["id"],
            "store_sha256": manifest["store_sha256"],
            "content_generation_sha256": manifest["content_generation_sha256"],
        },
        "marker_sha256",
    )


def _snapshot_stage_marker_bytes(
    destination: Path, manifest: Mapping[str, Any]
) -> bytes:
    data = canonical_json(_snapshot_stage_marker(destination, manifest)).encode("utf-8")
    if len(data) > MAX_STAGE_MARKER_BYTES:
        raise VaultStoreError(
            "snapshot-stage-marker-too-large",
            "snapshot stage ownership marker exceeds its byte bound",
        )
    return data


def _stage_guard_metadata(stage: _PinnedDirectory) -> os.stat_result:
    if os.name != "nt":
        return os.fstat(stage.dir_fd)
    metadata = _lstat(stage.path)
    if metadata is None:
        raise VaultStoreError(
            "snapshot-stage-conflict", "snapshot stage root disappeared"
        )
    return metadata


def _stage_marker_matches(
    stage: _PinnedDirectory,
    destination: Path,
    manifest: Mapping[str, Any],
) -> bool:
    metadata = stage.lstat_leaf(STAGE_MARKER_LEAF)
    if metadata is None:
        return False
    try:
        data, _ = _stable_pinned_file(
            stage,
            stage.path,
            STAGE_MARKER_LEAF,
            maximum=MAX_STAGE_MARKER_BYTES,
            links=1,
        )
    except (OSError, SourceArchiveError, VaultStoreError) as error:
        raise VaultStoreError(
            "snapshot-stage-unowned",
            "preexisting snapshot stage marker is unsafe",
        ) from error
    if data != _snapshot_stage_marker_bytes(destination, manifest):
        raise VaultStoreError(
            "snapshot-stage-generation-conflict",
            "preexisting snapshot stage belongs to another generation",
        )
    return True


def _snapshot_file_bytes(
    source: Vault,
    record: Mapping[str, Any],
    scaffold_contents: Mapping[str, bytes],
) -> bytes:
    path = str(record["path"])
    if path in scaffold_contents:
        return scaffold_contents[path]
    return _read_record(source, record)


def _anchored_snapshot_file(
    parent: _PinnedDirectory,
    relative: str,
    record: Mapping[str, Any] | None,
    *,
    manifest: Mapping[str, Any],
) -> tuple[int, str, os.stat_result]:
    leaf = PurePosixPath(relative).name
    if relative == STORE_PATH:
        expected = _store_bytes(manifest)
        maximum = max(1, len(expected))
    elif record is not None and "normalized_sha256" in record:
        expected = None
        maximum = MAX_MANAGED_MARKDOWN_BYTES
    elif record is not None:
        expected = None
        maximum = max(1, int(record["bytes"]))
    else:
        raise VaultStoreError(
            "snapshot-stage-mismatch", "snapshot contains an undeclared file"
        )
    data, metadata = _stable_pinned_file(
        parent, parent.path, leaf, maximum=maximum, links=1
    )
    if relative == STORE_PATH:
        if data != expected:
            raise VaultStoreError(
                "snapshot-stage-mismatch", "snapshot store.json is not exact"
            )
    elif record is not None and "normalized_sha256" in record:
        normalized = _normalized_authority_bytes(data)
        if (
            len(normalized) != int(record["normalized_bytes"])
            or _sha256(normalized) != record["normalized_sha256"]
        ):
            raise VaultStoreError(
                "snapshot-stage-mismatch", "snapshot authority note is not exact"
            )
    elif record is not None and (
        len(data) != int(record["bytes"]) or _sha256(data) != record["sha256"]
    ):
        raise VaultStoreError(
            "snapshot-stage-mismatch", "snapshot artifact is not exact"
        )
    if record is not None and "receipt_sha256" in record:
        receipt = _validated_receipt(data, expected_path=relative)
        if receipt["receipt_sha256"] != record["receipt_sha256"]:
            raise VaultStoreError(
                "snapshot-stage-mismatch", "snapshot receipt identity is not exact"
            )
    return len(data), _sha256(data), metadata


def _anchored_snapshot_pass(
    root: _PinnedDirectory,
    manifest: Mapping[str, Any],
    *,
    destination: Path,
    allow_marker: bool,
) -> tuple[tuple[Any, ...], ...]:
    records = {
        str(record["path"]): record for record in _manifest_records(manifest)
    }
    expected_files = set(manifest["managed_paths"])
    if allow_marker:
        expected_files.add(STAGE_MARKER_LEAF)
    expected_directories = _parent_directories(tuple(expected_files))
    seen_files: set[str] = set()
    seen_directories: set[str] = set()
    tokens: list[tuple[Any, ...]] = []
    remaining_entries = len(expected_files) + len(expected_directories)
    marker_allowance = (
        len(_snapshot_stage_marker_bytes(destination, manifest))
        if allow_marker
        else 0
    )
    remaining_bytes = MAX_VAULT_STORE_BYTES + marker_allowance
    folded: dict[str, str] = {}

    def walk(directory: _PinnedDirectory, prefix: str, depth: int) -> None:
        nonlocal remaining_entries, remaining_bytes
        if depth > 64:
            raise VaultStoreError(
                "vault-store-too-deep", "snapshot stage directory depth exceeds its bound"
            )
        directory.verify_current()
        try:
            with os.scandir(directory.path if os.name == "nt" else directory.dir_fd) as scan:
                names: list[str] = []
                for entry in scan:
                    remaining_entries -= 1
                    if remaining_entries < 0:
                        raise VaultStoreError(
                            "vault-store-too-large",
                            "snapshot stage exceeds its entry bound",
                        )
                    names.append(entry.name)
                names.sort()
        except OSError as error:
            raise VaultStoreError(
                "unsafe-snapshot-stage", "snapshot stage could not be inventoried"
            ) from error
        for name in names:
            relative = name if not prefix else f"{prefix}/{name}"
            try:
                _validate_portable_path(relative, field="snapshot stage entry")
            except ContractError as error:
                raise VaultStoreError(
                    "unsafe-snapshot-stage", "snapshot stage path is not host-neutral"
                ) from error
            folded_key = unicodedata.normalize("NFC", relative).casefold()
            previous = folded.setdefault(folded_key, relative)
            if previous != relative:
                raise VaultStoreError(
                    "snapshot-stage-mismatch", "snapshot stage paths collide"
                )
            metadata = directory.lstat_leaf(name)
            if metadata is None or _is_link_or_reparse(directory.path / name, metadata):
                raise VaultStoreError(
                    "unsafe-snapshot-stage", "snapshot stage entry is unsafe"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if relative not in expected_directories:
                    raise VaultStoreError(
                        "snapshot-stage-mismatch", "snapshot stage has an extra directory"
                    )
                child = directory.open_child(name)
                try:
                    opened = _stage_guard_metadata(child)
                    if not os.path.samestat(metadata, opened):
                        raise VaultStoreError(
                            "snapshot-stage-conflict", "snapshot directory changed during open"
                        )
                    seen_directories.add(relative)
                    tokens.append(
                        (
                            "directory",
                            relative,
                            int(opened.st_dev),
                            int(opened.st_ino),
                            int(opened.st_mtime_ns),
                            int(opened.st_ctime_ns),
                        )
                    )
                    walk(child, relative, depth + 1)
                finally:
                    child.close()
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise VaultStoreError(
                    "unsafe-snapshot-stage", "snapshot stage file is not ordinary"
                )
            if relative not in expected_files:
                raise VaultStoreError(
                    "snapshot-stage-mismatch", "snapshot stage has an extra file"
                )
            if relative == STAGE_MARKER_LEAF:
                data, opened = _stable_pinned_file(
                    directory,
                    directory.path,
                    name,
                    maximum=MAX_STAGE_MARKER_BYTES,
                    links=1,
                )
                if data != _snapshot_stage_marker_bytes(destination, manifest):
                    raise VaultStoreError(
                        "snapshot-stage-generation-conflict",
                        "snapshot stage marker belongs to another generation",
                    )
                size, digest = len(data), _sha256(data)
            else:
                size, digest, opened = _anchored_snapshot_file(
                    directory,
                    relative,
                    records.get(relative),
                    manifest=manifest,
                )
            remaining_bytes -= size
            if remaining_bytes < 0:
                raise VaultStoreError(
                    "vault-store-too-large", "snapshot stage exceeds its byte bound"
                )
            seen_files.add(relative)
            tokens.append(
                (
                    "file",
                    relative,
                    digest,
                    size,
                    int(opened.st_dev),
                    int(opened.st_ino),
                    int(opened.st_mtime_ns),
                    int(opened.st_ctime_ns),
                )
            )
        directory.verify_current()

    walk(root, "", 0)
    if seen_files != expected_files or seen_directories != expected_directories:
        raise VaultStoreError(
            "snapshot-stage-mismatch", "snapshot stage inventory is incomplete"
        )
    root.verify_current()
    return tuple(sorted(tokens))


def _verify_anchored_snapshot(
    root: _PinnedDirectory,
    manifest: Mapping[str, Any],
    *,
    destination: Path,
    allow_marker: bool,
) -> tuple[tuple[Any, ...], ...]:
    first = _anchored_snapshot_pass(
        root, manifest, destination=destination, allow_marker=allow_marker
    )
    second = _anchored_snapshot_pass(
        root, manifest, destination=destination, allow_marker=allow_marker
    )
    if first != second:
        raise VaultStoreError(
            "unstable-snapshot-stage", "snapshot stage changed between verification passes"
        )
    return second


def _open_retained_stage_directory(
    stage: _PinnedDirectory,
    relative: str,
    identities: Mapping[str, os.stat_result],
) -> tuple[contextlib.ExitStack, _PinnedDirectory]:
    stack = contextlib.ExitStack()
    current = stage
    prefix: list[str] = []
    try:
        for part in (() if relative in {"", "."} else PurePosixPath(relative).parts):
            prefix.append(part)
            key = PurePosixPath(*prefix).as_posix()
            child = current.open_child(part)
            stack.enter_context(child)
            opened = _stage_guard_metadata(child)
            expected = identities.get(key)
            if expected is None or not os.path.samestat(expected, opened):
                raise VaultStoreError(
                    "snapshot-stage-conflict",
                    "snapshot stage directory identity changed",
                )
            current = child
        return stack, current
    except BaseException:
        stack.close()
        raise


def _ensure_retained_stage_directory(
    stage: _PinnedDirectory,
    relative: str,
    identities: dict[str, os.stat_result],
) -> None:
    path = PurePosixPath(relative)
    parent_relative = path.parent.as_posix()
    stack, parent = _open_retained_stage_directory(
        stage,
        "" if parent_relative == "." else parent_relative,
        identities,
    )
    with stack:
        before = parent.lstat_leaf(path.name)
        if before is None:
            try:
                parent.mkdir_leaf(path.name)
                if os.name != "nt":
                    os.fsync(parent.dir_fd)
            except FileExistsError:
                pass
        child = parent.open_child(path.name)
        try:
            identities[relative] = _stage_guard_metadata(child)
            child.verify_current()
        finally:
            child.close()


def _fsync_retained_stage_directories(
    stage: _PinnedDirectory,
    identities: Mapping[str, os.stat_result],
) -> None:
    for relative in sorted(
        identities,
        key=lambda item: (len(PurePosixPath(item).parts), item),
        reverse=True,
    ):
        if relative == "":
            continue
        stack, directory = _open_retained_stage_directory(stage, relative, identities)
        with stack:
            if os.name != "nt":
                os.fsync(directory.dir_fd)
            directory.verify_current()
    if os.name != "nt":
        os.fsync(stage.dir_fd)
    stage.verify_current()


def _validate_snapshot_bootstrap_entries(
    bootstrap: _PinnedDirectory, *, marker_installed: bool
) -> None:
    """Reject third-party bootstrap content before the directory can move."""

    bootstrap.verify_current()
    allowed = {STAGE_MARKER_LEAF, f".{STAGE_MARKER_LEAF}.write"}
    names: set[str] = set()
    try:
        with os.scandir(
            bootstrap.path if os.name == "nt" else bootstrap.dir_fd
        ) as scanner:
            for entry in scanner:
                if entry.name not in allowed or len(names) >= 2:
                    raise VaultStoreError(
                        "snapshot-stage-unowned",
                        "snapshot bootstrap contains an unowned entry",
                    )
                names.add(entry.name)
    except OSError as error:
        raise VaultStoreError(
            "snapshot-stage-unowned",
            "snapshot bootstrap could not be inventoried safely",
        ) from error
    if marker_installed and names != {STAGE_MARKER_LEAF}:
        raise VaultStoreError(
            "snapshot-stage-unowned",
            "snapshot bootstrap contains an unowned entry",
        )
    bootstrap.verify_current()


def _bootstrap_snapshot_stage(
    output_parent: _PinnedDirectory,
    destination: Path,
    stage_leaf: str,
    manifest: Mapping[str, Any],
) -> tuple[_PinnedDirectory, os.stat_result]:
    bootstrap_leaf = _snapshot_bootstrap_name(stage_leaf)
    marker = _snapshot_stage_marker_bytes(destination, manifest)
    existing = output_parent.lstat_leaf(bootstrap_leaf)
    if existing is None:
        try:
            output_parent.mkdir_leaf(bootstrap_leaf)
            if os.name != "nt":
                os.fsync(output_parent.dir_fd)
        except FileExistsError:
            pass
        existing = output_parent.lstat_leaf(bootstrap_leaf)
    if existing is None or (
        not stat.S_ISDIR(existing.st_mode)
        or _is_link_or_reparse(destination.parent / bootstrap_leaf, existing)
    ):
        raise VaultStoreError(
            "snapshot-stage-unowned", "snapshot bootstrap is not an ordinary directory"
        )
    bootstrap = output_parent.open_child(bootstrap_leaf)
    bootstrap_metadata = _stage_guard_metadata(bootstrap)
    try:
        if existing is not None and not os.path.samestat(existing, bootstrap_metadata):
            raise VaultStoreError(
                "snapshot-stage-conflict", "snapshot bootstrap changed during open"
            )
        _vault_store_hook("after-snapshot-bootstrap-mkdir", bootstrap_leaf)
        _validate_snapshot_bootstrap_entries(bootstrap, marker_installed=False)
        _install_noreplace_in_parent(
            bootstrap,
            bootstrap.path,
            STAGE_MARKER_LEAF,
            marker,
            temporary=f".{STAGE_MARKER_LEAF}.write",
        )
        _validate_snapshot_bootstrap_entries(bootstrap, marker_installed=True)
        if os.name != "nt":
            os.fsync(bootstrap.dir_fd)
        bootstrap.verify_current()
        _vault_store_hook("before-snapshot-bootstrap-install", stage_leaf)
        _validate_snapshot_bootstrap_entries(bootstrap, marker_installed=True)
        if not _stage_marker_matches(bootstrap, destination, manifest):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "snapshot bootstrap marker changed before installation",
            )
        bootstrap.verify_current()
    finally:
        bootstrap.close()
    try:
        _rename_directory_noreplace(
            output_parent,
            destination.parent,
            bootstrap_leaf,
            stage_leaf,
            before_hook=None,
            after_hook="after-snapshot-bootstrap-install",
            expected_source=bootstrap_metadata,
        )
    except Exception as error:
        old = output_parent.lstat_leaf(bootstrap_leaf)
        new = output_parent.lstat_leaf(stage_leaf)
        if (
            old is None
            and new is not None
            and os.path.samestat(bootstrap_metadata, new)
        ):
            pass
        elif old is not None and new is None and os.path.samestat(bootstrap_metadata, old):
            raise VaultStoreError(
                "snapshot-stage-create-failed",
                "snapshot bootstrap could not be installed",
            ) from error
        else:
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "snapshot bootstrap entered an unrecognized state",
            ) from error
    stage = output_parent.open_child(stage_leaf)
    metadata = _stage_guard_metadata(stage)
    if not os.path.samestat(bootstrap_metadata, metadata) or not _stage_marker_matches(
        stage, destination, manifest
    ):
        stage.close()
        raise VaultStoreError(
            "snapshot-stage-conflict", "installed snapshot stage marker changed"
        )
    return stage, metadata


def _prepare_snapshot_stage(
    captured: _CapturedStore,
    destination: Path,
    output_parent: _PinnedDirectory,
) -> tuple[Path, _PinnedDirectory, os.stat_result, bool]:
    manifest = captured.manifest
    stage_leaf = _snapshot_stage_name(destination)
    stage_path = destination.parent / stage_leaf
    current = output_parent.lstat_leaf(stage_leaf)
    if current is None:
        stage, metadata = _bootstrap_snapshot_stage(
            output_parent, destination, stage_leaf, manifest
        )
        return stage_path, stage, metadata, False
    if (
        not stat.S_ISDIR(current.st_mode)
        or _is_link_or_reparse(stage_path, current)
    ):
        raise VaultStoreError(
            "snapshot-stage-unowned", "preexisting snapshot stage is not ordinary"
        )
    stage = output_parent.open_child(stage_leaf)
    metadata = _stage_guard_metadata(stage)
    if not os.path.samestat(current, metadata):
        stage.close()
        raise VaultStoreError(
            "snapshot-stage-conflict", "snapshot stage changed during open"
        )
    if _stage_marker_matches(stage, destination, manifest):
        return stage_path, stage, metadata, False
    # A markerless stage is trusted only if it is already a complete exact
    # snapshot for this generation (the crash point after marker removal).
    try:
        output_parent.verify_current()
        _verify_anchored_snapshot(
            stage,
            manifest,
            destination=destination,
            allow_marker=False,
        )
        stage.verify_current()
        output_parent.verify_current()
    except (OSError, SourceArchiveError, VaultStoreError) as error:
        stage.close()
        raise VaultStoreError(
            "snapshot-stage-unowned",
            "markerless snapshot stage is not a complete captured generation",
        ) from error
    return stage_path, stage, metadata, True


def _build_snapshot_stage(
    captured: _CapturedStore,
    destination: Path,
    output_parent: _PinnedDirectory,
) -> tuple[Path, _PinnedDirectory, os.stat_result]:
    manifest = captured.manifest
    stage_path, stage, stage_metadata, complete = _prepare_snapshot_stage(
        captured, destination, output_parent
    )
    if complete:
        return stage_path, stage, stage_metadata
    identities: dict[str, os.stat_result] = {"": stage_metadata}
    try:
        directories = sorted(
            _parent_directories(manifest["managed_paths"]),
            key=lambda item: (len(PurePosixPath(item).parts), item),
        )
        for relative in directories:
            _ensure_retained_stage_directory(stage, relative, identities)
            _vault_store_hook("after-snapshot-stage-mkdir", relative)

        _, scaffold_contents = _scaffold_inventory(
            manifest["authority"]["roots"],
            manifest["authority"]["artifacts"],
            source_present=manifest["source"]["manifest"] is not None,
        )
        records = {
            str(record["path"]): record for record in _manifest_records(manifest)
        }
        for relative in sorted(records):
            data = _snapshot_file_bytes(
                captured.vault, records[relative], scaffold_contents
            )
            parent_relative = PurePosixPath(relative).parent.as_posix()
            stack, parent = _open_retained_stage_directory(
                stage,
                "" if parent_relative == "." else parent_relative,
                identities,
            )
            with stack:
                if "normalized_sha256" in records[relative]:
                    _install_authority_noreplace_in_parent(
                        parent,
                        parent.path,
                        relative,
                        data,
                        records[relative],
                        temporary=_authority_stage_temporary(
                            relative, records[relative]
                        ),
                    )
                else:
                    _install_noreplace_in_parent(
                        parent,
                        parent.path,
                        relative,
                        data,
                        temporary=f".kgd-{_sha256(relative.encode('utf-8') + data)[:32]}.tmp",
                    )
            _vault_store_hook("after-snapshot-stage-file", relative)
        store_data = _store_bytes(manifest)
        store_parent_relative = PurePosixPath(STORE_PATH).parent.as_posix()
        stack, store_parent = _open_retained_stage_directory(
            stage, store_parent_relative, identities
        )
        with stack:
            _install_noreplace_in_parent(
                store_parent,
                store_parent.path,
                STORE_PATH,
                store_data,
                temporary=f".store-{manifest['store_sha256']}.tmp",
            )
        _fsync_retained_stage_directories(stage, identities)
        output_parent.verify_current()
        _verify_anchored_snapshot(
            stage,
            manifest,
            destination=destination,
            allow_marker=True,
        )
        stage.verify_current()
        marker_data, marker_metadata = _stable_pinned_file(
            stage,
            stage.path,
            STAGE_MARKER_LEAF,
            maximum=MAX_STAGE_MARKER_BYTES,
            links=1,
        )
        marker_data_again, marker_metadata_again = _stable_pinned_file(
            stage,
            stage.path,
            STAGE_MARKER_LEAF,
            maximum=MAX_STAGE_MARKER_BYTES,
            links=1,
        )
        if (
            marker_data != _snapshot_stage_marker_bytes(destination, manifest)
            or marker_data_again != marker_data
            or not os.path.samestat(marker_metadata, marker_metadata_again)
            or marker_metadata.st_mtime_ns != marker_metadata_again.st_mtime_ns
            or marker_metadata.st_ctime_ns != marker_metadata_again.st_ctime_ns
            or not stage.cleanup_owned_leaf_raw(
                STAGE_MARKER_LEAF, marker_metadata_again
            )
        ):
            raise VaultStoreError(
                "snapshot-stage-conflict", "snapshot stage marker changed before completion"
            )
        if os.name != "nt":
            os.fsync(stage.dir_fd)
        _vault_store_hook("after-snapshot-stage-complete", stage_path.name)
        output_parent.verify_current()
        _verify_anchored_snapshot(
            stage,
            manifest,
            destination=destination,
            allow_marker=False,
        )
        stage.verify_current()
        output_parent.verify_current()
        return stage_path, stage, stage_metadata
    except (OSError, SourceArchiveError) as error:
        stage.close()
        raise VaultStoreError(
            "snapshot-stage-conflict",
            "snapshot stage ancestry changed during retained construction",
        ) from error
    except BaseException:
        stage.close()
        raise


def _rename_directory_noreplace(
    parent: _PinnedDirectory,
    parent_path: Path,
    source: str,
    destination: str,
    *,
    before_hook: str | None = "before-snapshot-directory-install",
    after_hook: str = "after-snapshot-directory-install",
    expected_source: os.stat_result | None = None,
) -> None:
    source_metadata = parent.lstat_leaf(source)
    if (
        source_metadata is None
        or not stat.S_ISDIR(source_metadata.st_mode)
        or _is_link_or_reparse(parent_path / source, source_metadata)
        or (
            expected_source is not None
            and not os.path.samestat(expected_source, source_metadata)
        )
    ):
        raise VaultStoreError(
            "unsafe-snapshot-stage", "snapshot stage changed before installation"
        )
    if parent.lstat_leaf(destination) is not None:
        raise FileExistsError(destination)
    if before_hook is not None:
        _vault_store_hook(before_hook, destination)
    if os.name == "nt":
        from . import source_archive as archive_module

        handle = archive_module._win_open_handle(
            parent_path / source,
            desired_access=(
                archive_module._WIN_DELETE
                | archive_module._WIN_FILE_READ_ATTRIBUTES
                | archive_module._WIN_SYNCHRONIZE
            ),
            share_mode=(
                archive_module._WIN_SHARE_READ | archive_module._WIN_SHARE_WRITE
            ),
            disposition=archive_module._WIN_OPEN_EXISTING,
            directory=True,
        )
        try:
            current_source = parent.lstat_leaf(source)
            if current_source is None or not os.path.samestat(
                source_metadata, current_source
            ):
                raise VaultStoreError(
                    "snapshot-stage-conflict", "snapshot stage changed during handle open"
                )
            if parent.lstat_leaf(destination) is not None:
                raise FileExistsError(destination)
            archive_module._win_rename_handle(
                handle,
                parent_path / destination,
                replace_if_exists=False,
            )
        finally:
            archive_module._win_close(handle)
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise VaultStoreError(
                "snapshot-install-unsupported",
                "filesystem does not expose atomic directory no-replace",
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        current_source = parent.lstat_leaf(source)
        if current_source is None or not os.path.samestat(
            source_metadata, current_source
        ):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "snapshot stage changed immediately before installation",
            )
        if parent.lstat_leaf(destination) is not None:
            raise FileExistsError(destination)
        result = renameat2(
            parent.dir_fd,
            os.fsencode(source),
            parent.dir_fd,
            os.fsencode(destination),
            1,
        )
        if result != 0:
            code = ctypes.get_errno()
            if code in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(code, "snapshot output exists", destination)
            raise OSError(code, os.strerror(code), destination)
    elif sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(library, "renameatx_np", None)
        if renameatx_np is None:
            raise VaultStoreError(
                "snapshot-install-unsupported",
                "filesystem does not expose atomic directory no-replace",
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        current_source = parent.lstat_leaf(source)
        if current_source is None or not os.path.samestat(
            source_metadata, current_source
        ):
            raise VaultStoreError(
                "snapshot-stage-conflict",
                "snapshot stage changed immediately before installation",
            )
        if parent.lstat_leaf(destination) is not None:
            raise FileExistsError(destination)
        result = renameatx_np(
            parent.dir_fd,
            os.fsencode(source),
            parent.dir_fd,
            os.fsencode(destination),
            0x00000004,
        )
        if result != 0:
            code = ctypes.get_errno()
            if code in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(code, "snapshot output exists", destination)
            raise OSError(code, os.strerror(code), destination)
    else:
        raise VaultStoreError(
            "snapshot-install-unsupported",
            "filesystem does not expose atomic directory no-replace",
        )
    _vault_store_hook(after_hook, destination)
    installed = parent.lstat_leaf(destination)
    remaining = parent.lstat_leaf(source)
    if (
        installed is None
        or remaining is not None
        or not stat.S_ISDIR(installed.st_mode)
        or _is_link_or_reparse(parent_path / destination, installed)
        or not os.path.samestat(source_metadata, installed)
    ):
        raise VaultStoreError(
            "snapshot-install-uncertain",
            "snapshot directory entered an unrecognized installation state",
        )
    if os.name != "nt":
        os.fsync(parent.dir_fd)
    parent.verify_current()


def _install_snapshot_stage(
    stage: Path,
    destination: Path,
    output_parent: _PinnedDirectory,
    stage_guard: _PinnedDirectory,
    expected_stage: os.stat_result,
    manifest: Mapping[str, Any],
) -> None:
    try:
        expected = output_parent.lstat_leaf(stage.name)
        if (
            expected is None
            or not stat.S_ISDIR(expected.st_mode)
            or _is_link_or_reparse(stage, expected)
            or not os.path.samestat(expected_stage, expected)
            or not os.path.samestat(expected_stage, _stage_guard_metadata(stage_guard))
        ):
            raise VaultStoreError(
                "unsafe-snapshot-stage", "snapshot stage changed before installation"
            )
        try:
            output_parent.verify_current()
            _verify_anchored_snapshot(
                stage_guard,
                manifest,
                destination=destination,
                allow_marker=False,
            )
            stage_guard.verify_current()
            current_stage = output_parent.lstat_leaf(stage.name)
            if (
                current_stage is None
                or not os.path.samestat(expected, current_stage)
            ):
                raise VaultStoreError(
                    "snapshot-stage-conflict",
                    "snapshot stage no longer matches the captured generation",
                )
            output_parent.verify_current()
            if os.name == "nt":
                # Transfer the stage identity from the non-delete-sharing read
                # handle to the delete-capable native rename handle below.
                stage_guard.close()
            _rename_directory_noreplace(
                output_parent,
                destination.parent,
                stage.name,
                destination.name,
                before_hook=None,
                expected_source=expected_stage,
            )
            installed_guard = output_parent.open_child(destination.name)
            try:
                installed_metadata = _stage_guard_metadata(installed_guard)
                if not os.path.samestat(expected_stage, installed_metadata):
                    raise VaultStoreError(
                        "snapshot-install-uncertain",
                        "installed snapshot identity differs from its verified stage",
                    )
                _verify_anchored_snapshot(
                    installed_guard,
                    manifest,
                    destination=destination,
                    allow_marker=False,
                )
            finally:
                installed_guard.close()
            output_parent.verify_current()
        except Exception as error:
            # Classify the uncertain native call through the same retained
            # parent.  No lexical reopen is allowed to infer ownership.
            staged = output_parent.lstat_leaf(stage.name)
            installed = output_parent.lstat_leaf(destination.name)
            old = (
                staged is not None
                and installed is None
                and stat.S_ISDIR(staged.st_mode)
                and not _is_link_or_reparse(stage, staged)
                and os.path.samestat(expected, staged)
            )
            new = (
                staged is None
                and installed is not None
                and stat.S_ISDIR(installed.st_mode)
                and not _is_link_or_reparse(destination, installed)
                and os.path.samestat(expected, installed)
            )
            if new:
                try:
                    output_parent.verify_current()
                    installed_guard = output_parent.open_child(destination.name)
                    try:
                        installed_metadata = _stage_guard_metadata(installed_guard)
                        if not os.path.samestat(expected_stage, installed_metadata):
                            raise VaultStoreError(
                                "snapshot-install-uncertain",
                                "installed snapshot identity differs from its verified stage",
                            )
                        _verify_anchored_snapshot(
                            installed_guard,
                            manifest,
                            destination=destination,
                            allow_marker=False,
                        )
                    finally:
                        installed_guard.close()
                    output_parent.verify_current()
                except (OSError, SourceArchiveError, VaultStoreError) as verify_error:
                    raise VaultStoreError(
                        "snapshot-install-uncertain",
                        "installed snapshot could not be verified after an uncertain rename",
                    ) from verify_error
                return
            if old:
                if isinstance(error, VaultStoreError):
                    raise error
                raise VaultStoreError(
                    "snapshot-install-failed",
                    "snapshot directory could not be installed atomically",
                ) from error
            if staged is not None and installed is not None:
                raise VaultStoreError(
                    "snapshot-output-exists",
                    "snapshot output appeared before installation",
                ) from error
            raise VaultStoreError(
                "snapshot-install-uncertain",
                "snapshot directory entered an unrecognized installation state",
            ) from error
    except VaultStoreError:
        raise
    except (OSError, SourceArchiveError) as error:
        raise VaultStoreError(
            "snapshot-install-uncertain",
            "snapshot installation state could not be classified safely",
        ) from error


def _snapshot_copy(
    registry: Any, vault: Vault, output: Path | str
) -> dict[str, Any]:
    stage: Path | None = None
    stage_guard: _PinnedDirectory | None = None
    with vault_generation_guard(vault):
        try:
            _recover_native_transactions_locked(vault)
        except NativeCompilerError as error:
            raise VaultStoreError(
                error.code, "pending native transaction recovery failed closed"
            ) from error
        captured = _capture_store(vault, layout="snapshot-copy")
        captured_controlled = _capture_controlled_inventory(
            vault,
            captured.manifest,
            require_store=False,
            allow_missing_scaffolds=True,
        )
        with vault_registry_read_guard(registry.home):
            current = load_registry(registry.home, validate_vaults=False)
            registrations = {item.id: item.path for item in current.registrations}
            if (
                current.generation != registry.generation
                or vault.id not in registrations
                or not _same_path(registrations[vault.id], vault.root)
            ):
                raise VaultStoreError(
                    "stale-vault-selection", "Vault registration changed during snapshot"
                )
            destination = _external_destination(
                output, registry=current, vault=vault, require_absent=True
            )
            stage_candidate = destination.parent / _snapshot_stage_name(destination)
            _external_destination(
                stage_candidate,
                registry=current,
                vault=vault,
                require_absent=False,
            )
            bootstrap_candidate = destination.parent / _snapshot_bootstrap_name(
                stage_candidate.name
            )
            _external_destination(
                bootstrap_candidate,
                registry=current,
                vault=vault,
                require_absent=False,
            )
            with _PinnedDirectory(destination.parent) as output_parent:
                if output_parent.lstat_leaf(destination.name) is not None:
                    raise VaultStoreError(
                        "snapshot-output-exists", "snapshot output already exists"
                    )
                output_parent.verify_current()
                stage, stage_guard, stage_metadata = _build_snapshot_stage(
                    captured, destination, output_parent
                )
                try:
                    output_parent.verify_current()
                    _vault_store_hook(
                        "before-snapshot-directory-install", destination.name
                    )
                    final = _capture_store(vault, layout="snapshot-copy")
                    if final.manifest != captured.manifest:
                        raise VaultStoreError(
                            "stale-vault-store", "Vault content changed during snapshot copy"
                        )
                    final_controlled = _capture_controlled_inventory(
                        vault,
                        final.manifest,
                        require_store=False,
                        allow_missing_scaffolds=True,
                    )
                    if final_controlled != captured_controlled:
                        raise VaultStoreError(
                            "stale-vault-store",
                            "Vault controlled namespace changed during snapshot copy",
                        )
                    if output_parent.lstat_leaf(destination.name) is not None:
                        raise VaultStoreError(
                            "snapshot-output-exists",
                            "snapshot output appeared before installation",
                        )
                    output_parent.verify_current()
                    _install_snapshot_stage(
                        stage,
                        destination,
                        output_parent,
                        stage_guard,
                        stage_metadata,
                        final.manifest,
                    )
                finally:
                    stage_guard.close()
    return _report("snapshot", final)


def _snapshot_vault_store(
    vault_id: str,
    *,
    output: Path | str | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Capture a portable in-place store or a no-clobber snapshot copy."""

    registry, vault = _selected_vault(vault_id, home)
    if output is not None:
        return _snapshot_copy(registry, vault, output)
    with vault_generation_guard(vault):
        try:
            _recover_native_transactions_locked(vault)
        except NativeCompilerError as error:
            raise VaultStoreError(
                error.code, "pending native transaction recovery failed closed"
            ) from error
        old_store = _read_store_pointer(vault)
        owned: list[_OwnedLeaf] = []
        published = False
        final: _CapturedStore | None = None
        try:
            roots, authority, _, _ = _authority_inventory(vault)
            ledger = load_source_ledger(vault)
            owned = _ensure_scaffolds(
                vault,
                roots,
                authority,
                source_present=ledger.manifest is not None,
            )
            _vault_store_hook("after-scaffolds", "")
            captured = _capture_store(vault, layout="in-place")
            with vault_registry_read_guard(registry.home):
                current = load_registry(registry.home, validate_vaults=False)
                registrations = {item.id: item.path for item in current.registrations}
                if (
                    current.generation != registry.generation
                    or vault_id not in registrations
                    or not _same_path(registrations[vault_id], vault.root)
                ):
                    raise VaultStoreError(
                        "stale-vault-selection", "Vault registration changed during snapshot"
                    )
                final = _capture_store(vault, layout="in-place")
                if final.manifest != captured.manifest:
                    raise VaultStoreError(
                        "stale-vault-store", "Vault content changed during snapshot capture"
                    )
                _publish_store_manifest(
                    vault, final.manifest, expected_old=old_store
                )
                published = True
        except BaseException as error:
            if not published:
                new_store = None if final is None else _store_bytes(final.manifest)
                state = (
                    _classify_store_pointer(vault, old=old_store, new=new_store)
                    if new_store is not None
                    else (
                        "old"
                        if _read_store_pointer(vault) == old_store
                        else "third"
                    )
                )
                if state == "old":
                    _rollback_owned_leaves(vault, owned)
                elif state == "new":
                    published = True
            if isinstance(error, VaultStoreError) or not isinstance(error, Exception):
                raise
            raise VaultStoreError(
                "vault-store-snapshot-failed",
                "portable Vault snapshot failed closed",
            ) from error
    if final is None:
        raise VaultStoreError(
            "vault-store-snapshot-failed",
            "portable Vault snapshot did not produce a final generation",
        )
    return _report("snapshot", final)


def snapshot_vault_store(
    vault_id: str,
    *,
    output: Path | str | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Capture a portable Vault store through a closed public boundary."""

    try:
        return _snapshot_vault_store(vault_id, output=output, home=home)
    except VaultStoreError:
        raise
    except (
        OSError,
        ContractError,
        NativeCompilerError,
        NativeNoteError,
        SourceArchiveError,
        VaultError,
        VaultIngestError,
        RecursionError,
    ) as error:
        raise VaultStoreError(
            "vault-store-snapshot-failed",
            "portable Vault snapshot failed closed",
        ) from error


__all__ = [
    "STORE_PATH",
    "STORE_SCHEMA",
    "REPORT_SCHEMA",
    "VaultStoreError",
    "snapshot_vault_store",
    "verify_vault_store",
]
