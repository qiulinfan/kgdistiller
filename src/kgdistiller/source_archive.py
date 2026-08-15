"""Immutable, portable source capture and incremental derivation ledger.

The source ledger is an atomic pointer to immutable canonical JSONL artifacts.
This module deliberately owns only capture history and reviewed-derivation
references; graph compilation and ingest mutation belong to later slices.
"""

from __future__ import annotations

import contextlib
import ctypes
import difflib
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, Sequence

from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .vaults import Vault, VaultError, load_vault, locate_file


DOCUMENT_SCHEMA = "qlkg-source-document-v1"
VERSION_SCHEMA = "qlkg-source-version-v1"
DERIVATION_SCHEMA = "qlkg-derivation-v1"
LEDGER_SCHEMA = "qlkg-source-ledger-v1"
REPORT_SCHEMA = "qlkg-source-report-v1"

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_ROWS = 1_000_000
MAX_DIFF_BYTES = 1024 * 1024
MAX_DIFF_LINES = 10_000
MAX_LEDGER_READ_RETRIES = 3
MAX_PATH_BYTES = 4096
ARTIFACT_FILENAMES = {
    "documents": "documents.jsonl",
    "versions": "versions.jsonl",
    "derivations": "derivations.jsonl",
}
EFFECTIVE_DERIVATION_STATUSES = {"committed", "reviewed-empty", "carried-forward"}
DERIVATION_STATUS_ORDER = {
    "planned": 0,
    "committed": 1,
    "reviewed-empty": 2,
    "carried-forward": 3,
    "superseded": 4,
    "failed": 5,
}
FORMAT_SUFFIXES = {".md": "markdown", ".typ": "typst", ".tex": "latex"}
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _anchored_test_hook(label: str, parent: Path, leaf: str) -> None:
    """No-op checkpoint used by deterministic ancestor-swap regressions."""


if os.name == "nt":
    from ctypes import wintypes

    _WIN_INVALID_HANDLE = ctypes.c_void_p(-1).value
    _WIN_GENERIC_READ = 0x80000000
    _WIN_GENERIC_WRITE = 0x40000000
    _WIN_DELETE = 0x00010000
    _WIN_FILE_LIST_DIRECTORY = 0x00000001
    _WIN_FILE_READ_ATTRIBUTES = 0x00000080
    _WIN_SYNCHRONIZE = 0x00100000
    _WIN_SHARE_READ = 0x00000001
    _WIN_SHARE_WRITE = 0x00000002
    _WIN_SHARE_DELETE = 0x00000004
    _WIN_CREATE_NEW = 1
    _WIN_OPEN_EXISTING = 3
    _WIN_OPEN_ALWAYS = 4
    _WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _WIN_FILE_ATTRIBUTE_NORMAL = 0x00000080
    _WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WIN_FILE_RENAME_INFO = 3

    class _WinAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    class _WinRenameInfo(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _GetFileInformationByHandleEx = _kernel32.GetFileInformationByHandleEx
    _GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _GetFileInformationByHandleEx.restype = wintypes.BOOL
    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _SetFileInformationByHandle = _kernel32.SetFileInformationByHandle
    _SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _SetFileInformationByHandle.restype = wintypes.BOOL


def _win_error(message: str) -> OSError:
    error = ctypes.get_last_error()
    return OSError(error, f"{message}: {ctypes.FormatError(error)}")


def _win_handle_path(handle: int) -> Path:
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    length = _GetFinalPathNameByHandleW(handle, buffer, size, 0)
    if length == 0 or length >= size:
        raise _win_error("cannot resolve pinned handle")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(os.path.abspath(value))


def _win_attributes(handle: int) -> int:
    info = _WinAttributeTagInfo()
    if not _GetFileInformationByHandleEx(
        handle, 9, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise _win_error("cannot inspect pinned handle")
    return int(info.FileAttributes)


def _win_open_handle(
    path: Path,
    *,
    desired_access: int,
    share_mode: int,
    disposition: int,
    directory: bool,
) -> int:
    flags = _WIN_FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _WIN_FILE_FLAG_BACKUP_SEMANTICS
    else:
        flags |= _WIN_FILE_ATTRIBUTE_NORMAL
    handle = _CreateFileW(
        str(path),
        desired_access,
        share_mode,
        None,
        disposition,
        flags,
        None,
    )
    if handle == _WIN_INVALID_HANDLE or handle is None:
        raise _win_error(f"cannot anchor path {path}")
    return int(handle)


def _win_close(handle: int) -> None:
    if handle not in (0, _WIN_INVALID_HANDLE):
        _CloseHandle(handle)


def _win_rename_handle(handle: int, destination: Path) -> None:
    encoded = str(destination).encode("utf-16-le")
    offset = _WinRenameInfo.FileName.offset
    buffer = ctypes.create_string_buffer(offset + len(encoded) + 2)
    info = ctypes.cast(buffer, ctypes.POINTER(_WinRenameInfo)).contents
    info.Flags = 1
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
    if not _SetFileInformationByHandle(
        handle, _WIN_FILE_RENAME_INFO, buffer, offset + len(encoded)
    ):
        raise _win_error("cannot atomically replace anchored file")


class _PinnedDirectory:
    """Retain an ordinary, non-reparse directory chain for leaf operations."""

    def __init__(self, path: Path | str, *, create: bool = False) -> None:
        absolute = Path(os.path.abspath(os.fspath(path)))
        if not absolute.is_absolute() or not absolute.anchor:
            raise SourceArchiveError("unsafe-ledger-path", "anchored path must be absolute")
        self.path = absolute
        self._posix_fds: list[int] = []
        self._posix_links: list[tuple[int, str, int, Path]] = []
        self._win_handles: list[tuple[int, Path]] = []
        try:
            if os.name == "nt":
                self._open_windows(create=create)
            else:
                self._open_posix(create=create)
            self.verify_current()
        except BaseException:
            self.close()
            raise

    @property
    def dir_fd(self) -> int:
        if os.name == "nt" or not self._posix_fds:
            raise RuntimeError("directory fd is unavailable")
        return self._posix_fds[-1]

    @property
    def win_handle(self) -> int:
        if os.name != "nt" or not self._win_handles:
            raise RuntimeError("Windows directory handle is unavailable")
        return self._win_handles[-1][0]

    def _open_posix(self, *, create: bool) -> None:
        anchor = Path(self.path.anchor)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(anchor, flags)
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            os.close(root_fd)
            raise SourceArchiveError("unsafe-ledger-path", "filesystem anchor is not a directory")
        self._posix_fds.append(root_fd)
        current_path = anchor
        for part in self.path.relative_to(anchor).parts:
            parent_fd = self._posix_fds[-1]
            try:
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                if not create:
                    raise SourceArchiveError("missing-ledger-artifact", "anchored directory is missing")
                os.mkdir(part, dir_fd=parent_fd)
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            opened = os.fstat(child_fd)
            current = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(opened, current)
            ):
                os.close(child_fd)
                raise SourceArchiveError("unsafe-ledger-path", "anchored directory changed during open")
            current_path = current_path / part
            self._posix_fds.append(child_fd)
            self._posix_links.append((parent_fd, part, child_fd, current_path))

    def _open_windows(self, *, create: bool) -> None:
        anchor = Path(self.path.anchor)
        desired = _WIN_FILE_LIST_DIRECTORY | _WIN_FILE_READ_ATTRIBUTES | _WIN_SYNCHRONIZE
        share = _WIN_SHARE_READ | _WIN_SHARE_WRITE
        handle = _win_open_handle(
            anchor,
            desired_access=desired,
            share_mode=share,
            disposition=_WIN_OPEN_EXISTING,
            directory=True,
        )
        self._win_handles.append((handle, anchor))
        current = anchor
        for part in self.path.relative_to(anchor).parts:
            candidate = current / part
            try:
                handle = _win_open_handle(
                    candidate,
                    desired_access=desired,
                    share_mode=share,
                    disposition=_WIN_OPEN_EXISTING,
                    directory=True,
                )
            except OSError:
                if not create:
                    raise SourceArchiveError("missing-ledger-artifact", "anchored directory is missing")
                try:
                    os.mkdir(candidate)
                except FileExistsError:
                    pass
                handle = _win_open_handle(
                    candidate,
                    desired_access=desired,
                    share_mode=share,
                    disposition=_WIN_OPEN_EXISTING,
                    directory=True,
                )
            attributes = _win_attributes(handle)
            if (
                not attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                or attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
                or not _same_path(_win_handle_path(handle), candidate)
            ):
                _win_close(handle)
                raise SourceArchiveError("unsafe-ledger-path", "anchored directory is reparse or redirected")
            self._win_handles.append((handle, candidate))
            current = candidate

    def verify_current(self) -> None:
        if os.name == "nt":
            for handle, expected in self._win_handles:
                attributes = _win_attributes(handle)
                metadata = _lstat(expected)
                if (
                    not attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                    or attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
                    or metadata is None
                    or not stat.S_ISDIR(metadata.st_mode)
                    or _is_link_like(expected, metadata)
                    or not _same_path(_win_handle_path(handle), expected)
                ):
                    raise SourceArchiveError("unsafe-ledger-path", "pinned directory chain changed")
            return
        anchor_fd = self._posix_fds[0]
        anchor_metadata = os.stat(self.path.anchor, follow_symlinks=False)
        if not os.path.samestat(os.fstat(anchor_fd), anchor_metadata):
            raise SourceArchiveError("unsafe-ledger-path", "filesystem anchor changed")
        for parent_fd, part, child_fd, _ in self._posix_links:
            try:
                current = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as error:
                raise SourceArchiveError("unsafe-ledger-path", "pinned directory was removed") from error
            opened = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(opened, current)
            ):
                raise SourceArchiveError("unsafe-ledger-path", "pinned directory chain changed")

    def checkpoint(self, label: str, leaf: str) -> None:
        _anchored_test_hook(label, self.path, leaf)
        self.verify_current()

    def lstat_leaf(self, leaf: str) -> os.stat_result | None:
        self.checkpoint("before-leaf-stat", leaf)
        if os.name == "nt":
            return _lstat(self.path / leaf)
        try:
            return os.stat(leaf, dir_fd=self.dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def open_existing_file(self, leaf: str, *, writable: bool = False) -> int:
        self.checkpoint("before-leaf-open", leaf)
        if os.name != "nt":
            flags = (
                (os.O_RDWR if writable else os.O_RDONLY)
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            return os.open(leaf, flags, dir_fd=self.dir_fd)
        desired = (
            (_WIN_GENERIC_READ | _WIN_GENERIC_WRITE if writable else _WIN_GENERIC_READ)
            | _WIN_FILE_READ_ATTRIBUTES
        )
        share = _WIN_SHARE_READ | _WIN_SHARE_WRITE
        if not writable:
            share |= _WIN_SHARE_DELETE
        handle = _win_open_handle(
            self.path / leaf,
            desired_access=desired,
            share_mode=share,
            disposition=_WIN_OPEN_EXISTING,
            directory=False,
        )
        try:
            attributes = _win_attributes(handle)
            if (
                attributes & (_WIN_FILE_ATTRIBUTE_DIRECTORY | _WIN_FILE_ATTRIBUTE_REPARSE_POINT)
                or not _same_path(_win_handle_path(handle), self.path / leaf)
            ):
                raise SourceArchiveError("unsafe-ledger-path", "anchored file is reparse or redirected")
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                handle,
                (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
            handle = 0
            return descriptor
        finally:
            _win_close(handle)

    def create_file(self, leaf: str, *, writable: bool = True, delete_access: bool = False) -> int:
        self.checkpoint("before-leaf-create", leaf)
        if os.name != "nt":
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            return os.open(leaf, flags, 0o600, dir_fd=self.dir_fd)
        desired = _WIN_GENERIC_WRITE | _WIN_FILE_READ_ATTRIBUTES
        if delete_access:
            desired |= _WIN_DELETE
        handle = _win_open_handle(
            self.path / leaf,
            desired_access=desired,
            share_mode=_WIN_SHARE_READ | _WIN_SHARE_WRITE,
            disposition=_WIN_CREATE_NEW,
            directory=False,
        )
        try:
            attributes = _win_attributes(handle)
            if (
                attributes & (_WIN_FILE_ATTRIBUTE_DIRECTORY | _WIN_FILE_ATTRIBUTE_REPARSE_POINT)
                or not _same_path(_win_handle_path(handle), self.path / leaf)
            ):
                raise SourceArchiveError("unsafe-ledger-path", "created file is reparse or redirected")
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                handle, os.O_WRONLY | getattr(os, "O_BINARY", 0)
            )
            handle = 0
            return descriptor
        finally:
            _win_close(handle)

    def open_lock_file(self, leaf: str) -> int:
        self.checkpoint("before-leaf-lock-open", leaf)
        if os.name != "nt":
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            return os.open(leaf, flags, 0o600, dir_fd=self.dir_fd)
        handle = _win_open_handle(
            self.path / leaf,
            desired_access=(
                _WIN_GENERIC_READ | _WIN_GENERIC_WRITE | _WIN_FILE_READ_ATTRIBUTES
            ),
            share_mode=_WIN_SHARE_READ | _WIN_SHARE_WRITE,
            disposition=_WIN_OPEN_ALWAYS,
            directory=False,
        )
        try:
            attributes = _win_attributes(handle)
            if (
                attributes & (_WIN_FILE_ATTRIBUTE_DIRECTORY | _WIN_FILE_ATTRIBUTE_REPARSE_POINT)
                or not _same_path(_win_handle_path(handle), self.path / leaf)
            ):
                raise SourceArchiveError("unsafe-ledger-path", "writer lock is reparse or redirected")
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                handle, os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
            handle = 0
            return descriptor
        finally:
            _win_close(handle)

    def mkdir_leaf(self, leaf: str) -> None:
        self.checkpoint("before-leaf-mkdir", leaf)
        if os.name == "nt":
            os.mkdir(self.path / leaf)
        else:
            os.mkdir(leaf, dir_fd=self.dir_fd)

    def unlink_leaf(self, leaf: str, *, directory: bool = False) -> None:
        self.checkpoint("before-leaf-unlink", leaf)
        if os.name == "nt":
            if directory:
                os.rmdir(self.path / leaf)
            else:
                os.unlink(self.path / leaf)
        elif directory:
            os.rmdir(leaf, dir_fd=self.dir_fd)
        else:
            os.unlink(leaf, dir_fd=self.dir_fd)

    def replace_leaf(self, source: str, destination: str, source_fd: int) -> None:
        self.checkpoint("before-leaf-replace", destination)
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(source_fd)
            _win_rename_handle(handle, self.path / destination)
        else:
            current = os.stat(source, dir_fd=self.dir_fd, follow_symlinks=False)
            if not os.path.samestat(os.fstat(source_fd), current):
                raise SourceArchiveError("unsafe-ledger-path", "manifest temporary changed")
            os.replace(
                source,
                destination,
                src_dir_fd=self.dir_fd,
                dst_dir_fd=self.dir_fd,
            )

    def close(self) -> None:
        for descriptor in reversed(self._posix_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._posix_fds.clear()
        for handle, _ in reversed(self._win_handles):
            _win_close(handle)
        self._win_handles.clear()

    def __enter__(self) -> "_PinnedDirectory":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
VERSION_RE = re.compile(r"^doc:(?P<document>[^:]+):v(?P<sequence>[0-9]{8})$")
RFC3339_Z_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


class SourceArchiveError(RuntimeError):
    """A stable structured source-archive failure."""

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
            "kind": "kgdistiller-source-error",
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class _GenerationChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    raw: bytes
    normalized_text: str
    raw_sha256: str
    normalized_text_sha256: str
    byte_count: int
    format: str


@dataclass(frozen=True)
class SourceLedger:
    """One fully validated immutable source-ledger generation."""

    sources_root: Path
    manifest: dict[str, Any] | None
    generation_sha256: str | None
    documents: tuple[dict[str, Any], ...]
    versions: tuple[dict[str, Any], ...]
    derivations: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SourceEvidenceView:
    """Current effective concept and relation evidence from one validated ledger."""

    generation_sha256: str | None
    concept_ids: frozenset[str]
    relations: frozenset[tuple[str, str, str]]

    def has_concept(self, concept_id: str) -> bool:
        return concept_id in self.concept_ids

    def has_relation(self, source: str, relation: str, target: str) -> bool:
        key = (
            (min(source, target), relation, max(source, target))
            if relation == "contrasts-with"
            else (source, relation, target)
        )
        return key in self.relations


@dataclass(frozen=True)
class _ResolvedSource:
    vault: Vault
    path: Path
    relative_path: str
    registry_generation: str
    vault_manifest_sha256: str


def normalize_source_text(text: str) -> str:
    """Normalize only CRLF and bare CR line endings to LF."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_reparse(metadata: os.stat_result) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & marker)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _anchored_lstat(path: Path) -> os.stat_result | None:
    try:
        with _PinnedDirectory(path.parent) as parent:
            return parent.lstat_leaf(path.name)
    except SourceArchiveError as error:
        if error.code == "missing-ledger-artifact":
            return None
        raise


def _is_link_like(path: Path, metadata: os.stat_result | None = None) -> bool:
    metadata = metadata if metadata is not None else _lstat(path)
    return bool(
        metadata is not None
        and (stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata))
    )


def _path_identity(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _same_path(left: Path | str, right: Path | str) -> bool:
    return _path_identity(left) == _path_identity(right)


def _contains(root: Path, candidate: Path, *, allow_equal: bool = False) -> bool:
    try:
        common = os.path.commonpath((_path_identity(root), _path_identity(candidate)))
    except ValueError:
        return False
    equal = os.path.normcase(common) == os.path.normcase(_path_identity(root))
    return equal and (allow_equal or not _same_path(root, candidate))


def _portable_relative(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise SourceArchiveError("unsafe-ledger-path", f"{field} must be a non-empty path")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise SourceArchiveError("unsafe-ledger-path", f"{field} is not valid UTF-8") from error
    if size > MAX_PATH_BYTES or "\0" in value or "\\" in value:
        raise SourceArchiveError("unsafe-ledger-path", f"{field} is not a bounded portable path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise SourceArchiveError("unsafe-ledger-path", f"{field} is not a canonical relative path")
    if any(any(ord(character) < 32 or ord(character) == 127 for character in part) for part in relative.parts):
        raise SourceArchiveError("unsafe-ledger-path", f"{field} contains control characters")
    if any(
        part.endswith((" ", "."))
        or any(character in '<>:"|?*' for character in part)
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        for part in relative.parts
    ):
        raise SourceArchiveError("unsafe-ledger-path", f"{field} is not portable across supported hosts")
    return relative.parts


def _ensure_directory(
    root: Path,
    parts: Sequence[str],
    *,
    create: bool,
    field: str,
) -> Path:
    for part in parts:
        if part in {"", ".", ".."} or "/" in part or "\\" in part:
            raise SourceArchiveError("unsafe-ledger-path", f"{field} contains an unsafe component")
    selected = root.joinpath(*parts)
    try:
        with _PinnedDirectory(selected, create=create):
            pass
    except SourceArchiveError:
        raise
    except OSError as error:
        raise SourceArchiveError("unsafe-ledger-path", f"cannot anchor {field}") from error
    return selected


def _read_regular(
    root: Path,
    parts: Sequence[str],
    *,
    maximum: int,
    kind: str,
    single_link: bool = True,
) -> bytes:
    if not parts:
        raise SourceArchiveError("unsafe-ledger-path", f"{kind} path is empty")
    parent_path = root.joinpath(*parts[:-1])
    leaf = str(parts[-1])
    try:
        pinned = _PinnedDirectory(parent_path)
    except (OSError, SourceArchiveError) as error:
        if isinstance(error, SourceArchiveError):
            raise
        raise SourceArchiveError(f"invalid-{kind}", f"cannot anchor {kind}") from error
    with pinned:
        metadata = pinned.lstat_leaf(leaf)
        if metadata is None:
            raise SourceArchiveError(f"missing-{kind}", f"missing {kind}")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_link_like(parent_path / leaf, metadata)
            or (single_link and metadata.st_nlink != 1)
            or metadata.st_size > maximum
        ):
            raise SourceArchiveError(f"invalid-{kind}", f"{kind} is not a bounded ordinary file")
        try:
            descriptor = pinned.open_existing_file(leaf)
        except OSError as error:
            raise SourceArchiveError(f"invalid-{kind}", f"cannot safely open {kind}") from error
        try:
            opened = os.fstat(descriptor)
            current = pinned.lstat_leaf(leaf)
            if (
                current is None
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or _is_reparse(opened)
                or _is_link_like(parent_path / leaf, current)
                or not os.path.samestat(opened, current)
                or (single_link and (opened.st_nlink != 1 or current.st_nlink != 1))
                or opened.st_size > maximum
            ):
                raise SourceArchiveError(f"invalid-{kind}", f"{kind} changed during open")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise SourceArchiveError(f"{kind}-too-large", f"{kind} exceeds {maximum} bytes")
            after = os.fstat(descriptor)
            final = pinned.lstat_leaf(leaf)
            if (
                final is None
                or not os.path.samestat(opened, after)
                or not os.path.samestat(after, final)
                or after.st_size != total
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
            ):
                raise SourceArchiveError(f"unstable-{kind}", f"{kind} changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)


def read_vault_relative_regular(
    vault: Vault | Path | str,
    relative_path: str,
    *,
    maximum: int,
) -> bytes:
    """Read one bounded Vault-relative ordinary file through pinned ancestors."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise SourceArchiveError(
            "invalid-vault-file-limit", "Vault file limit must be a positive integer"
        )
    parts = _portable_relative(relative_path, field="Vault-relative file")
    return _read_regular(
        selected.root,
        parts,
        maximum=maximum,
        kind="vault-file",
        single_link=True,
    )


def replace_vault_relative_regular(
    vault: Vault | Path | str,
    relative_path: str,
    content: bytes,
    *,
    maximum: int,
) -> None:
    """Atomically replace one Vault-relative file through pinned ancestors."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    if (
        not isinstance(content, bytes)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum < 1
        or len(content) > maximum
    ):
        raise SourceArchiveError(
            "vault-file-too-large", f"Vault file exceeds {maximum} bytes"
        )
    parts = _portable_relative(relative_path, field="Vault-relative file")
    parent_path = _ensure_directory(
        selected.root,
        parts[:-1],
        create=True,
        field="Vault-relative file parent",
    )
    leaf = parts[-1]
    destination = parent_path / leaf
    with _PinnedDirectory(parent_path) as parent:
        existing = parent.lstat_leaf(leaf)
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode)
            or _is_link_like(destination, existing)
            or existing.st_nlink != 1
        ):
            raise SourceArchiveError(
                "invalid-vault-file", "existing Vault file is not an ordinary single-link file"
            )
        descriptor = -1
        temporary_name = ""
        for _ in range(32):
            temporary_name = f".{leaf}-{uuid.uuid4().hex}"
            try:
                descriptor = parent.create_file(temporary_name, delete_access=True)
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise SourceArchiveError(
                "vault-file-stage-exhausted", "cannot allocate a Vault file stage"
            )
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
            parent.replace_leaf(temporary_name, leaf, descriptor)
        except BaseException:
            try:
                parent.unlink_leaf(temporary_name)
            except (FileNotFoundError, OSError, SourceArchiveError):
                pass
            raise
        finally:
            os.close(descriptor)
        installed = parent.lstat_leaf(leaf)
        if installed is None or not stat.S_ISREG(installed.st_mode) or installed.st_nlink != 1:
            raise SourceArchiveError(
                "invalid-vault-file", "installed Vault file is not an ordinary single-link file"
            )
        if os.name != "nt":
            os.fsync(parent.dir_fd)


def unlink_vault_relative_regular(
    vault: Vault | Path | str,
    relative_path: str,
) -> None:
    """Remove one exact ordinary Vault-relative file through pinned ancestors."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    parts = _portable_relative(relative_path, field="Vault-relative file")
    parent_path = selected.root.joinpath(*parts[:-1])
    try:
        pinned = _PinnedDirectory(parent_path)
    except SourceArchiveError as error:
        if error.code == "missing-ledger-artifact":
            return
        raise
    with pinned:
        leaf = parts[-1]
        metadata = pinned.lstat_leaf(leaf)
        if metadata is None:
            return
        path = parent_path / leaf
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_link_like(path, metadata)
            or metadata.st_nlink != 1
        ):
            raise SourceArchiveError(
                "invalid-vault-file", "refusing to unlink a non-ordinary Vault file"
            )
        pinned.unlink_leaf(leaf)
        if os.name != "nt":
            os.fsync(pinned.dir_fd)


@contextlib.contextmanager
def vault_staging_directory(vault: Vault | Path | str) -> Iterator[Path]:
    """Yield one pinned disposable directory below ``.kgdistiller/build``."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    build = _ensure_directory(
        selected.root,
        (".kgdistiller", "build"),
        create=False,
        field="Vault build directory",
    )
    stage: Path | None = None
    with _PinnedDirectory(build) as parent:
        for _ in range(32):
            name = f".stage-knowledge-{uuid.uuid4().hex}"
            try:
                parent.mkdir_leaf(name)
            except FileExistsError:
                continue
            stage = build / name
            break
        if stage is None:
            raise SourceArchiveError(
                "stage-name-exhausted", "cannot allocate a native graph staging directory"
            )
        guard = _PinnedDirectory(stage)
        try:
            yield stage
            guard.verify_current()
            parent.verify_current()
        finally:
            guard.close()
            _remove_stage(stage, build)


def _strict_json(data: bytes, *, kind: str) -> Any:
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
        return json.loads(
            data.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SourceArchiveError(f"invalid-{kind}", f"malformed {kind}: {error}") from error


def _contract(payload: Any, schema: str, *, kind: str) -> dict[str, Any]:
    try:
        validated = validate_contract(payload)
    except ContractError as error:
        raise SourceArchiveError(f"invalid-{kind}", str(error)) from error
    if validated.get("schema") != schema:
        raise SourceArchiveError(f"invalid-{kind}", f"expected {schema}")
    return validated


def _format_for_path(path: Path) -> str:
    result = FORMAT_SUFFIXES.get(path.suffix.casefold())
    if result is None:
        raise SourceArchiveError("unsupported-source-format", "source format must be Markdown, Typst, or LaTeX")
    return result


def _read_source(path: Path) -> SourceSnapshot:
    try:
        raw = _read_regular(
            path.parent,
            (path.name,),
            maximum=MAX_SOURCE_BYTES,
            kind="source",
            single_link=False,
        )
    except SourceArchiveError as error:
        if error.code == "missing-source":
            raise SourceArchiveError("source-not-found", "source file no longer exists") from error
        raise
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SourceArchiveError("invalid-source-utf8", "source is not strict UTF-8") from error
    normalized = normalize_source_text(text)
    return SourceSnapshot(
        raw=raw,
        normalized_text=normalized,
        raw_sha256=_sha256_bytes(raw),
        normalized_text_sha256=_sha256_bytes(normalized.encode("utf-8")),
        byte_count=len(raw),
        format=_format_for_path(path),
    )


def _parse_jsonl(data: bytes, *, schema: str, kind: str) -> list[dict[str, Any]]:
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise SourceArchiveError(f"noncanonical-{kind}", f"{kind} must end with LF")
    lines = data.split(b"\n")[:-1]
    if len(lines) > MAX_ARTIFACT_ROWS:
        raise SourceArchiveError(f"invalid-{kind}", f"{kind} has too many rows")
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line:
            raise SourceArchiveError(f"noncanonical-{kind}", f"{kind} contains a blank row")
        payload = _strict_json(line, kind=kind)
        row = _contract(payload, schema, kind=kind)
        if canonical_json(row).encode("utf-8") != line:
            raise SourceArchiveError(f"noncanonical-{kind}", f"{kind} row is not canonical JSON")
        rows.append(row)
    return rows


def _canonical_jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json(dict(row)).encode("utf-8") + b"\n" for row in rows)


def _read_manifest(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = root / "manifest.json"
    if _anchored_lstat(path) is None:
        return None, None
    data = _read_regular(root, ("manifest.json",), maximum=MAX_MANIFEST_BYTES, kind="source-manifest")
    payload = _strict_json(data, kind="source-manifest")
    manifest = _contract(payload, LEDGER_SCHEMA, kind="source-manifest")
    if canonical_json(manifest).encode("utf-8") != data:
        raise SourceArchiveError("noncanonical-source-manifest", "source manifest is not canonical JSON")
    return manifest, _sha256_bytes(data)


def _artifact_rows(
    root: Path,
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    generation = str(manifest["generation_sha256"])
    if manifest["generation_path"] != f"generations/{generation}":
        raise SourceArchiveError("invalid-source-manifest", "generation_path does not match generation digest")
    artifacts = manifest["artifacts"]
    if sha256_json(artifacts) != generation:
        raise SourceArchiveError("invalid-source-manifest", "generation digest does not match artifact inventory")
    generation_dir = _ensure_directory(
        root, ("generations", generation), create=False, field="source generation"
    )
    parsed: dict[str, list[dict[str, Any]]] = {}
    schemas = {
        "documents": DOCUMENT_SCHEMA,
        "versions": VERSION_SCHEMA,
        "derivations": DERIVATION_SCHEMA,
    }
    for name, filename in ARTIFACT_FILENAMES.items():
        record = artifacts[name]
        if record["path"] != filename or "/" in record["path"] or "\\" in record["path"]:
            raise SourceArchiveError("invalid-source-manifest", f"invalid {name} artifact path")
        data = _read_regular(
            generation_dir,
            (filename,),
            maximum=MAX_ARTIFACT_BYTES,
            kind=f"source-{name}",
        )
        if len(data) != record["bytes"] or _sha256_bytes(data) != record["sha256"]:
            raise SourceArchiveError("invalid-source-artifact", f"{name} artifact inventory does not match bytes")
        rows = _parse_jsonl(data, schema=schemas[name], kind=f"source-{name}")
        if len(rows) != record["rows"]:
            raise SourceArchiveError("invalid-source-artifact", f"{name} artifact row count does not match")
        parsed[name] = rows
    return parsed["documents"], parsed["versions"], parsed["derivations"]


def _blob_bytes(blob_roots: Sequence[Path], version: Mapping[str, Any]) -> bytes:
    parts = _portable_relative(version["blob_path"], field="version blob_path")
    expected = ("blobs", "sha256", version["raw_sha256"][:2], version["raw_sha256"])
    if tuple(parts) != expected:
        raise SourceArchiveError("invalid-source-ledger", "version blob_path does not match raw digest")
    last_error: SourceArchiveError | None = None
    for root in blob_roots:
        if _anchored_lstat(root.joinpath(*parts)) is None:
            continue
        try:
            return _read_regular(root, parts, maximum=MAX_SOURCE_BYTES, kind="source-blob")
        except SourceArchiveError as error:
            last_error = error
            break
    if last_error is not None:
        raise last_error
    raise SourceArchiveError("missing-source-blob", "source ledger references a missing blob")


def extract_evidence_excerpt(
    normalized_text: str,
    span: Mapping[str, Any],
    *,
    expected_version_id: str,
) -> str:
    """Extract and bounds-check one evidence span from normalized source text."""

    if span.get("version_id") != expected_version_id:
        raise SourceArchiveError("invalid-evidence-span", "evidence span references the wrong version")
    start_line = span.get("start_line")
    end_line = span.get("end_line")
    if (
        not isinstance(start_line, int)
        or isinstance(start_line, bool)
        or not isinstance(end_line, int)
        or isinstance(end_line, bool)
    ):
        raise SourceArchiveError("invalid-evidence-span", "evidence line coordinates must be integers")
    lines = normalized_text.split("\n")
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise SourceArchiveError("invalid-evidence-span", "evidence line coordinates are out of bounds")
    has_start = "start_column" in span
    has_end = "end_column" in span
    if has_start != has_end:
        raise SourceArchiveError("invalid-evidence-span", "evidence columns must occur together")
    if not has_start:
        excerpt = "\n".join(lines[start_line - 1 : end_line])
    else:
        start_column = span.get("start_column")
        end_column = span.get("end_column")
        if (
            not isinstance(start_column, int)
            or isinstance(start_column, bool)
            or not isinstance(end_column, int)
            or isinstance(end_column, bool)
            or start_column < 0
            or end_column < 0
            or start_column > len(lines[start_line - 1])
            or end_column > len(lines[end_line - 1])
            or (start_line == end_line and end_column <= start_column)
        ):
            raise SourceArchiveError("invalid-evidence-span", "evidence column coordinates are out of bounds or reversed")
        if start_line == end_line:
            excerpt = lines[start_line - 1][start_column:end_column]
        else:
            selected = [lines[start_line - 1][start_column:]]
            selected.extend(lines[start_line:end_line - 1])
            selected.append(lines[end_line - 1][:end_column])
            excerpt = "\n".join(selected)
    if not excerpt:
        raise SourceArchiveError("invalid-evidence-span", "evidence excerpt must not be empty")
    return excerpt


def verify_evidence_span(
    normalized_text: str,
    span: Mapping[str, Any],
    *,
    expected_version_id: str,
) -> str:
    """Return an exact excerpt after verifying its lowercase SHA-256 digest."""

    excerpt = extract_evidence_excerpt(
        normalized_text, span, expected_version_id=expected_version_id
    )
    if span.get("excerpt_sha256") != _sha256_bytes(excerpt.encode("utf-8")):
        raise SourceArchiveError("invalid-evidence-span", "evidence excerpt digest does not match")
    return excerpt


def _effective_derivation(
    version_id: str,
    derivations: Mapping[str, Mapping[str, Any]],
    versions: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    seen: set[str] = set()
    current = version_id
    for _ in range(len(versions) + 1):
        if current in seen:
            raise SourceArchiveError("invalid-source-ledger", "derivation inheritance cycle")
        seen.add(current)
        row = derivations.get(current)
        if row is None:
            return None
        if row["status"] in {"committed", "reviewed-empty"}:
            return row
        if row["status"] != "carried-forward":
            return None
        inherited = row["inherited_from_version_id"]
        if inherited not in versions:
            raise SourceArchiveError("invalid-source-ledger", "carry row references an unknown version")
        current = str(inherited)
    raise SourceArchiveError("invalid-source-ledger", "derivation inheritance exceeds ledger bounds")


def _index_derivations(
    derivations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    effective: dict[str, Mapping[str, Any]] = {}
    failed: set[str] = set()
    for row in derivations:
        version_id = str(row["version_id"])
        status = str(row["status"])
        if status == "failed":
            failed.add(version_id)
        if status not in EFFECTIVE_DERIVATION_STATUSES:
            continue
        if version_id in effective:
            raise SourceArchiveError(
                "invalid-source-ledger",
                "more than one effective derivation row references a version",
            )
        effective[version_id] = row
    return effective, failed


def _derived_status(
    document_id: str,
    current_version_id: str,
    versions: Mapping[str, Mapping[str, Any]],
    derivations: Mapping[str, Mapping[str, Any]],
    failed_versions: set[str] | None = None,
) -> str:
    effective = _effective_derivation(current_version_id, derivations, versions)
    if effective is not None:
        return "reviewed-empty" if effective["status"] == "reviewed-empty" else "distilled"
    if failed_versions is not None and current_version_id in failed_versions:
        return "failed"
    current_sequence = int(versions[current_version_id]["sequence"])
    for version in versions.values():
        if (
            version["document_id"] == document_id
            and int(version["sequence"]) < current_sequence
            and _effective_derivation(str(version["version_id"]), derivations, versions) is not None
        ):
            return "stale"
    return "captured"


def _validate_rows(
    documents: Sequence[dict[str, Any]],
    versions: Sequence[dict[str, Any]],
    derivations: Sequence[dict[str, Any]],
    *,
    blob_roots: Sequence[Path],
) -> None:
    if list(documents) != sorted(documents, key=lambda item: item["document_id"]):
        raise SourceArchiveError("noncanonical-source-documents", "document rows are not deterministically sorted")
    document_by_id: dict[str, dict[str, Any]] = {}
    path_keys: dict[str, str] = {}
    for document in documents:
        document_id = str(document["document_id"])
        if document_id in document_by_id:
            raise SourceArchiveError("invalid-source-ledger", "duplicate document_id")
        _portable_relative(document["path"], field="document path")
        key = str(document["path"]).casefold()
        if key in path_keys:
            raise SourceArchiveError("invalid-source-ledger", "duplicate case-insensitive current document path")
        path_keys[key] = document_id
        document_by_id[document_id] = document

    if list(versions) != sorted(versions, key=lambda item: (item["document_id"], item["sequence"])):
        raise SourceArchiveError("noncanonical-source-versions", "version rows are not deterministically sorted")
    version_by_id: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in document_by_id}
    blob_cache: dict[str, tuple[bytes, str]] = {}
    for version in versions:
        version_id = str(version["version_id"])
        document_id = str(version["document_id"])
        if version_id in version_by_id:
            raise SourceArchiveError("invalid-source-ledger", "duplicate version_id")
        if document_id not in document_by_id:
            raise SourceArchiveError("invalid-source-ledger", "version references an unknown document")
        match = VERSION_RE.fullmatch(version_id)
        if (
            match is None
            or match.group("document") != document_id
            or int(match.group("sequence")) != version["sequence"]
        ):
            raise SourceArchiveError("invalid-source-ledger", "version_id does not match its document and sequence")
        _portable_relative(version["captured_path"], field="captured source path")
        if not RFC3339_Z_RE.fullmatch(str(version["captured_at"])):
            raise SourceArchiveError("invalid-source-ledger", "capture timestamp is not RFC3339 Z")
        try:
            datetime.fromisoformat(str(version["captured_at"])[:-1] + "+00:00")
        except ValueError as error:
            raise SourceArchiveError("invalid-source-ledger", "capture timestamp is not a real UTC time") from error
        raw_sha = str(version["raw_sha256"])
        cached = blob_cache.get(raw_sha)
        if cached is None:
            raw = _blob_bytes(blob_roots, version)
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise SourceArchiveError("invalid-source-blob", "source blob is not strict UTF-8") from error
            normalized = normalize_source_text(text)
            cached = (raw, _sha256_bytes(normalized.encode("utf-8")))
            blob_cache[raw_sha] = cached
        raw, normalized_sha = cached
        if (
            len(raw) != version["byte_count"]
            or _sha256_bytes(raw) != raw_sha
            or normalized_sha != version["normalized_text_sha256"]
        ):
            raise SourceArchiveError("invalid-source-blob", "source blob does not match version metadata")
        version_by_id[version_id] = version
        grouped[document_id].append(version)

    for document_id, document_versions in grouped.items():
        if not document_versions:
            raise SourceArchiveError("invalid-source-ledger", "document has no versions")
        for index, version in enumerate(document_versions, start=1):
            expected_predecessor = None if index == 1 else document_versions[index - 2]["version_id"]
            if version["sequence"] != index or version["predecessor_version_id"] != expected_predecessor:
                raise SourceArchiveError("invalid-source-ledger", "version sequence or predecessor is not contiguous")
        current = document_versions[-1]
        document = document_by_id[document_id]
        if (
            document["current_version_id"] != current["version_id"]
            or document["format"] != current["format"]
            or document["normalized_text_sha256"] != current["normalized_text_sha256"]
        ):
            raise SourceArchiveError("invalid-source-ledger", "document does not match its highest version")

    version_order = {item["version_id"]: (item["document_id"], item["sequence"]) for item in versions}

    def derivation_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            *version_order.get(str(item["version_id"]), ("~", 0)),
            DERIVATION_STATUS_ORDER.get(str(item["status"]), 99),
            canonical_json(dict(item)),
        )

    if list(derivations) != sorted(derivations, key=derivation_key):
        raise SourceArchiveError("noncanonical-source-derivations", "derivation rows are not deterministically sorted")
    seen_derivations: set[str] = set()
    for derivation in derivations:
        version_id = str(derivation["version_id"])
        version = version_by_id.get(version_id)
        if version is None:
            raise SourceArchiveError("invalid-source-ledger", "derivation references an unknown version")
        row_identity = canonical_json(derivation)
        if row_identity in seen_derivations:
            raise SourceArchiveError("invalid-source-ledger", "duplicate derivation row")
        seen_derivations.add(row_identity)
        candidates = derivation["candidate_dispositions"]
        candidate_ids = [item["candidate_id"] for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise SourceArchiveError("invalid-source-ledger", "duplicate candidate disposition")
        concept_ids = list(derivation["concept_ids"])
        concept_evidence = derivation["concept_evidence"]
        evidence_ids = [item["concept_id"] for item in concept_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SourceArchiveError("invalid-source-ledger", "duplicate concept evidence record")
        status = derivation["status"]
        if status == "committed" and set(evidence_ids) != set(concept_ids):
            raise SourceArchiveError("invalid-source-ledger", "committed concepts must each have evidence")
        if any(item not in concept_ids for item in evidence_ids):
            raise SourceArchiveError("invalid-source-ledger", "concept evidence references an unlisted concept")
        raw = blob_cache[str(version["raw_sha256"])][0]
        normalized = normalize_source_text(raw.decode("utf-8", errors="strict"))
        for record in (*concept_evidence, *derivation["relation_evidence"]):
            for span in record["spans"]:
                verify_evidence_span(normalized, span, expected_version_id=version_id)
    derivation_by_version, failed_versions = _index_derivations(derivations)

    for version_id, derivation in derivation_by_version.items():
        if derivation["status"] != "carried-forward":
            continue
        version = version_by_id[version_id]
        inherited_id = derivation["inherited_from_version_id"]
        inherited = version_by_id.get(str(inherited_id))
        if (
            inherited is None
            or inherited_id != version["predecessor_version_id"]
            or inherited["document_id"] != version["document_id"]
            or inherited["sequence"] >= version["sequence"]
            or inherited["normalized_text_sha256"] != version["normalized_text_sha256"]
        ):
            raise SourceArchiveError("invalid-source-ledger", "carry row must reference the same-digest immediate predecessor")
        terminal = _effective_derivation(version_id, derivation_by_version, version_by_id)
        if terminal is None:
            raise SourceArchiveError("invalid-source-ledger", "carry row does not resolve to reviewed data")

    for document_id, document in document_by_id.items():
        expected = _derived_status(
            document_id,
            str(document["current_version_id"]),
            version_by_id,
            derivation_by_version,
            failed_versions,
        )
        if document["status"] != expected:
            raise SourceArchiveError("invalid-source-ledger", "document lifecycle status is inconsistent")


def _read_generation(
    root: Path,
    manifest: dict[str, Any],
    *,
    blob_roots: Sequence[Path],
) -> SourceLedger:
    documents, versions, derivations = _artifact_rows(root, manifest)
    _validate_rows(documents, versions, derivations, blob_roots=blob_roots)
    return SourceLedger(
        sources_root=root,
        manifest=manifest,
        generation_sha256=manifest["generation_sha256"],
        documents=tuple(documents),
        versions=tuple(versions),
        derivations=tuple(derivations),
    )


def _load_ledger_once(vault: Vault) -> SourceLedger:
    root = vault.root / ".kgdistiller" / "sources"
    manifest, before = _read_manifest(root)
    if manifest is None:
        if _anchored_lstat(root / "manifest.json") is not None:
            raise _GenerationChanged()
        return SourceLedger(root, None, None, (), (), ())
    try:
        ledger = _read_generation(root, manifest, blob_roots=(root,))
    except SourceArchiveError:
        current_path = root / "manifest.json"
        if _anchored_lstat(current_path) is None:
            raise _GenerationChanged()
        current_bytes = _read_regular(
            root,
            ("manifest.json",),
            maximum=MAX_MANIFEST_BYTES,
            kind="source-manifest",
        )
        if _sha256_bytes(current_bytes) != before:
            raise _GenerationChanged()
        raise
    path = root / "manifest.json"
    if _anchored_lstat(path) is None:
        raise _GenerationChanged()
    after_bytes = _read_regular(root, ("manifest.json",), maximum=MAX_MANIFEST_BYTES, kind="source-manifest")
    if _sha256_bytes(after_bytes) != before:
        raise _GenerationChanged()
    return ledger


def load_source_ledger(vault: Vault | Path | str) -> SourceLedger:
    """Load and fully validate one stable source-ledger generation."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    for _ in range(MAX_LEDGER_READ_RETRIES):
        try:
            return _load_ledger_once(selected)
        except _GenerationChanged:
            continue
    raise SourceArchiveError(
        "stale-source-generation",
        f"source manifest changed during {MAX_LEDGER_READ_RETRIES} bounded read attempts",
    )


def current_evidence_view(ledger: SourceLedger) -> SourceEvidenceView:
    """Project current-document evidence through validated carry chains."""

    _, versions, derivations = _version_maps(ledger)
    concept_ids: set[str] = set()
    relations: set[tuple[str, str, str]] = set()
    for document in ledger.documents:
        effective = _effective_derivation(
            str(document["current_version_id"]), derivations, versions
        )
        if effective is None or effective["status"] != "committed":
            continue
        concept_ids.update(
            str(record["concept_id"]) for record in effective["concept_evidence"]
        )
        for record in effective["relation_evidence"]:
            source = str(record["source"])
            relation = str(record["relation"])
            target = str(record["target"])
            if relation == "contrasts-with":
                source, target = sorted((source, target))
            relations.add((source, relation, target))
    return SourceEvidenceView(
        generation_sha256=ledger.generation_sha256,
        concept_ids=frozenset(concept_ids),
        relations=frozenset(relations),
    )


def _acquire_lock(handle: Any) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise SourceArchiveError("vault-writer-lock-conflict", "another process holds the Vault writer lock") from error


def _release_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _vault_writer_lock(vault: Vault) -> Iterator[None]:
    build = vault.root / ".kgdistiller" / "build"
    _ensure_directory(vault.root, (".kgdistiller", "build"), create=False, field="Vault build directory")
    lock_path = build / "writer.lock"
    with _PinnedDirectory(build) as parent:
        metadata = parent.lstat_leaf("writer.lock")
        if metadata is not None and (
            not stat.S_ISREG(metadata.st_mode)
            or _is_link_like(lock_path, metadata)
            or metadata.st_nlink != 1
        ):
            raise SourceArchiveError("invalid-vault-writer-lock", "Vault writer lock is not an ordinary single-link file")
        descriptor = -1
        acquired = False
        handle: Any = None
        try:
            try:
                descriptor = parent.open_lock_file("writer.lock")
            except OSError as error:
                raise SourceArchiveError(
                    "invalid-vault-writer-lock", "cannot safely open Vault writer lock"
                ) from error
            opened = os.fstat(descriptor)
            current = parent.lstat_leaf("writer.lock")
            if (
                current is None
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or _is_reparse(opened)
                or _is_link_like(lock_path, current)
                or opened.st_ino == 0
                or current.st_ino == 0
                or not os.path.samestat(opened, current)
                or opened.st_nlink != 1
                or current.st_nlink != 1
            ):
                raise SourceArchiveError("invalid-vault-writer-lock", "Vault writer lock changed during open")
            handle = os.fdopen(descriptor, "r+b")
            descriptor = -1
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            _acquire_lock(handle)
            acquired = True
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            elif handle is not None:
                if acquired:
                    _release_lock(handle)
                handle.close()


def _resolve_source(file: Path | str, home: Path | str | None) -> _ResolvedSource:
    try:
        report = locate_file(file, home=home)
        result = report["result"]
        vault = load_vault(result["vault"]["path"], expected_id=result["vault"]["id"])
    except VaultError as error:
        raise SourceArchiveError(error.code, error.message, details=error.details) from error
    return _ResolvedSource(
        vault=vault,
        path=Path(result["file"]),
        relative_path=str(result["relative_path"]),
        registry_generation=str(report["registry_generation"]),
        vault_manifest_sha256=sha256_json(vault.manifest),
    )


def _recheck_resolution(resolved: _ResolvedSource, home: Path | str | None) -> None:
    try:
        current = _resolve_source(resolved.path, home)
    except SourceArchiveError as error:
        raise SourceArchiveError("stale-source-registration", "source registration or inclusion changed") from error
    if (
        current.registry_generation != resolved.registry_generation
        or current.vault.id != resolved.vault.id
        or not _same_path(current.vault.root, resolved.vault.root)
        or current.relative_path != resolved.relative_path
        or current.vault_manifest_sha256 != resolved.vault_manifest_sha256
    ):
        raise SourceArchiveError("stale-source-registration", "source registration token changed")


def _version_maps(
    ledger: SourceLedger,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    effective, _ = _index_derivations(ledger.derivations)
    return (
        {item["document_id"]: item for item in ledger.documents},
        {item["version_id"]: item for item in ledger.versions},
        {key: dict(value) for key, value in effective.items()},
    )


def _document_for_path(ledger: SourceLedger, path: str) -> dict[str, Any] | None:
    matches = [item for item in ledger.documents if str(item["path"]).casefold() == path.casefold()]
    if len(matches) > 1:
        raise SourceArchiveError("invalid-source-ledger", "current document path is ambiguous")
    return matches[0] if matches else None


def _version_text(ledger: SourceLedger, version: Mapping[str, Any]) -> str:
    raw = _blob_bytes((ledger.sources_root,), version)
    try:
        return normalize_source_text(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as error:
        raise SourceArchiveError("invalid-source-blob", "source blob is not strict UTF-8") from error


def _bounded_diff(
    before: str,
    after: str,
    *,
    from_version_id: str | None,
    to_version_id: str,
) -> dict[str, Any]:
    iterator = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=from_version_id or "empty",
        tofile=to_version_id,
        n=3,
        lineterm="\n",
    )
    pieces: list[str] = []
    used = 0
    truncated = False
    for piece in iterator:
        if len(pieces) >= MAX_DIFF_LINES:
            truncated = True
            break
        encoded = piece.encode("utf-8")
        remaining = MAX_DIFF_BYTES - used
        if len(encoded) > remaining:
            if remaining > 0:
                prefix = encoded[:remaining]
                while prefix:
                    try:
                        decoded = prefix.decode("utf-8", errors="strict")
                        break
                    except UnicodeDecodeError as error:
                        prefix = prefix[: error.start]
                else:
                    decoded = ""
                if decoded:
                    pieces.append(decoded)
                    used += len(decoded.encode("utf-8"))
            truncated = True
            break
        pieces.append(piece)
        used += len(encoded)
    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "text": "".join(pieces),
        "truncated": truncated,
        "emitted_lines": len(pieces),
        "max_bytes": MAX_DIFF_BYTES,
        "max_lines": MAX_DIFF_LINES,
    }


def _effective_concepts(
    version_id: str | None,
    versions: Mapping[str, Mapping[str, Any]],
    derivations: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    if version_id is None:
        return []
    effective = _effective_derivation(version_id, derivations, versions)
    return sorted(str(item) for item in effective["concept_ids"]) if effective is not None else []


def _timestamp(clock: Callable[[], datetime | str] | None) -> str:
    value: datetime | str
    value = datetime.now(timezone.utc) if clock is None else clock()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise SourceArchiveError("invalid-clock", "capture clock must return an aware UTC datetime")
        value = value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        value = value.replace(".000000Z", "Z")
    if not isinstance(value, str) or not RFC3339_Z_RE.fullmatch(value):
        raise SourceArchiveError("invalid-clock", "capture clock must return RFC3339 UTC with Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise SourceArchiveError("invalid-clock", "capture clock returned an invalid time") from error
    return value


def _document_uuid(factory: Callable[[], uuid.UUID | str] | None) -> str:
    value = uuid.uuid4() if factory is None else factory()
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError) as error:
        raise SourceArchiveError("invalid-document-id", "UUID factory returned an invalid UUID") from error
    return str(parsed)


def _build_generation(
    documents: Sequence[dict[str, Any]],
    versions: Sequence[dict[str, Any]],
    derivations: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    ordered_documents = sorted(documents, key=lambda item: item["document_id"])
    ordered_versions = sorted(versions, key=lambda item: (item["document_id"], item["sequence"]))
    order = {item["version_id"]: (item["document_id"], item["sequence"]) for item in ordered_versions}
    ordered_derivations = sorted(
        derivations,
        key=lambda item: (
            *order[item["version_id"]],
            DERIVATION_STATUS_ORDER[item["status"]],
            canonical_json(item),
        ),
    )
    rows = {
        "documents": ordered_documents,
        "versions": ordered_versions,
        "derivations": ordered_derivations,
    }
    contents = {name: _canonical_jsonl(value) for name, value in rows.items()}
    artifacts = {
        name: {
            "path": ARTIFACT_FILENAMES[name],
            "bytes": len(contents[name]),
            "rows": len(rows[name]),
            "sha256": _sha256_bytes(contents[name]),
        }
        for name in ("documents", "versions", "derivations")
    }
    generation = sha256_json(artifacts)
    manifest = _contract(
        {
            "schema": LEDGER_SCHEMA,
            "generation_sha256": generation,
            "generation_path": f"generations/{generation}",
            "artifacts": artifacts,
        },
        LEDGER_SCHEMA,
        kind="source-manifest",
    )
    return manifest, contents


def _write_fsync(path: Path, content: bytes) -> None:
    with _PinnedDirectory(path.parent) as parent:
        descriptor = parent.create_file(path.name)
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextlib.contextmanager
def vault_writer_lock(vault: Vault | Path | str) -> Iterator[None]:
    """Serialize one bounded Vault mutation with the established writer lock."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    with _vault_writer_lock(selected):
        yield


@contextlib.contextmanager
def vault_generation_guard(vault: Vault | Path | str) -> Iterator[None]:
    """Exclusively guard one flat Vault graph generation for a bounded read."""

    selected = vault if isinstance(vault, Vault) else load_vault(vault)
    with _vault_writer_lock(selected):
        yield


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    with _PinnedDirectory(path) as pinned:
        os.fsync(pinned.dir_fd)


def _remove_stage(stage: Path, sources_root: Path) -> None:
    def remove_contents(directory: Path) -> None:
        with _PinnedDirectory(directory) as pinned:
            if os.name == "nt":
                names = [entry.name for entry in os.scandir(directory)]
            else:
                names = [entry.name for entry in os.scandir(pinned.dir_fd)]
            for name in names:
                metadata = pinned.lstat_leaf(name)
                if metadata is None:
                    continue
                child = directory / name
                if stat.S_ISDIR(metadata.st_mode) and not _is_link_like(child, metadata):
                    remove_contents(child)
                    pinned.unlink_leaf(name, directory=True)
                else:
                    pinned.unlink_leaf(name, directory=False)

    try:
        if (
            stage.parent == sources_root
            and stage.name.startswith(".stage-")
            and _contains(sources_root, stage)
        ):
            remove_contents(stage)
            with _PinnedDirectory(sources_root) as parent:
                parent.unlink_leaf(stage.name, directory=True)
    except (OSError, SourceArchiveError):
        pass


def _make_stage_directory(sources_root: Path) -> Path:
    with _PinnedDirectory(sources_root) as parent:
        for _ in range(32):
            name = f".stage-{uuid.uuid4().hex}"
            try:
                parent.mkdir_leaf(name)
            except FileExistsError:
                continue
            stage = sources_root / name
            with _PinnedDirectory(stage):
                pass
            return stage
    raise SourceArchiveError("stage-name-exhausted", "cannot allocate source staging directory")


def _stage_generation(
    sources_root: Path,
    manifest: dict[str, Any],
    contents: Mapping[str, bytes],
    snapshot: SourceSnapshot | None,
) -> Path:
    stage = _make_stage_directory(sources_root)
    generation_dir = stage / "generations" / manifest["generation_sha256"]
    _ensure_directory(
        stage,
        ("generations", str(manifest["generation_sha256"])),
        create=True,
        field="staged source generation",
    )
    for name, filename in ARTIFACT_FILENAMES.items():
        _write_fsync(generation_dir / filename, contents[name])
    _fsync_directory(generation_dir)
    if snapshot is not None:
        blob_dir = stage / "blobs" / "sha256" / snapshot.raw_sha256[:2]
        _ensure_directory(
            stage,
            ("blobs", "sha256", snapshot.raw_sha256[:2]),
            create=True,
            field="staged source blob directory",
        )
        _write_fsync(blob_dir / snapshot.raw_sha256, snapshot.raw)
        _fsync_directory(blob_dir)
    _write_fsync(stage / "manifest.json", canonical_json(manifest).encode("utf-8"))
    _fsync_directory(stage)
    return stage


def _install_file_once(staged: Path, destination: Path, *, kind: str) -> None:
    content = _read_regular(
        staged.parent,
        (staged.name,),
        maximum=MAX_SOURCE_BYTES,
        kind=f"staged-{kind}",
    )
    with _PinnedDirectory(destination.parent) as parent:
        existing = parent.lstat_leaf(destination.name)
        if existing is not None:
            if (
                not stat.S_ISREG(existing.st_mode)
                or _is_link_like(destination, existing)
                or existing.st_nlink != 1
            ):
                raise SourceArchiveError(f"invalid-{kind}", f"existing immutable {kind} is unsafe")
            return
        try:
            descriptor = parent.create_file(destination.name)
        except FileExistsError:
            existing = parent.lstat_leaf(destination.name)
            if (
                existing is None
                or not stat.S_ISREG(existing.st_mode)
                or _is_link_like(destination, existing)
                or existing.st_nlink != 1
            ):
                raise SourceArchiveError(f"invalid-{kind}", f"existing immutable {kind} is unsafe")
            return
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        except OSError as error:
            raise SourceArchiveError(f"invalid-{kind}", f"cannot install immutable {kind}") from error
        finally:
            os.close(descriptor)


def _install_generation(
    sources_root: Path,
    stage: Path,
    manifest: Mapping[str, Any],
    snapshot: SourceSnapshot | None,
) -> None:
    if snapshot is not None:
        blob_parent = _ensure_directory(
            sources_root,
            ("blobs", "sha256", snapshot.raw_sha256[:2]),
            create=True,
            field="source blob directory",
        )
        blob = blob_parent / snapshot.raw_sha256
        _install_file_once(
            stage / "blobs" / "sha256" / snapshot.raw_sha256[:2] / snapshot.raw_sha256,
            blob,
            kind="source-blob",
        )
        installed = _read_regular(
            sources_root,
            ("blobs", "sha256", snapshot.raw_sha256[:2], snapshot.raw_sha256),
            maximum=MAX_SOURCE_BYTES,
            kind="source-blob",
        )
        if installed != snapshot.raw:
            raise SourceArchiveError("invalid-source-blob", "existing immutable blob has different bytes")
        _fsync_directory(blob_parent)

    generations = _ensure_directory(
        sources_root, ("generations",), create=True, field="source generations directory"
    )
    generation = str(manifest["generation_sha256"])
    destination = generations / generation
    created = False
    with _PinnedDirectory(generations) as parent:
        metadata = parent.lstat_leaf(generation)
        if metadata is None:
            try:
                parent.mkdir_leaf(generation)
                created = True
            except FileExistsError:
                pass
            except OSError as error:
                raise SourceArchiveError("invalid-source-generation", "cannot install source generation") from error
        metadata = parent.lstat_leaf(generation)
        if (
            metadata is None
            or not stat.S_ISDIR(metadata.st_mode)
            or _is_link_like(destination, metadata)
        ):
            raise SourceArchiveError("invalid-source-generation", "immutable generation path is unsafe")
        with _PinnedDirectory(destination):
            pass
    if not created:
        _read_generation(sources_root, dict(manifest), blob_roots=(sources_root,))
        return
    staged_generation = stage / "generations" / generation
    for filename in ARTIFACT_FILENAMES.values():
        _install_file_once(staged_generation / filename, destination / filename, kind="source-artifact")
    _fsync_directory(destination)
    _fsync_directory(generations)
    _read_generation(sources_root, dict(manifest), blob_roots=(sources_root,))


def _atomic_replace_manifest(sources_root: Path, manifest: Mapping[str, Any]) -> None:
    with _PinnedDirectory(sources_root) as parent:
        existing = parent.lstat_leaf("manifest.json")
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode)
            or _is_link_like(sources_root / "manifest.json", existing)
            or existing.st_nlink != 1
        ):
            raise SourceArchiveError("invalid-source-manifest", "live source manifest is unsafe")
        descriptor = -1
        temporary_name = ""
        for _ in range(32):
            temporary_name = f".manifest-{uuid.uuid4().hex}"
            try:
                descriptor = parent.create_file(
                    temporary_name, delete_access=True
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise SourceArchiveError(
                "manifest-name-exhausted", "cannot allocate manifest temporary"
            )
        content = canonical_json(dict(manifest)).encode("utf-8")
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
            current = parent.lstat_leaf("manifest.json")
            if current is not None and (
                not stat.S_ISREG(current.st_mode)
                or _is_link_like(sources_root / "manifest.json", current)
                or current.st_nlink != 1
            ):
                raise SourceArchiveError(
                    "invalid-source-manifest",
                    "live source manifest changed into an unsafe file",
                )
            parent.replace_leaf(temporary_name, "manifest.json", descriptor)
        finally:
            os.close(descriptor)
            try:
                if parent.lstat_leaf(temporary_name) is not None:
                    parent.unlink_leaf(temporary_name)
            except (OSError, SourceArchiveError):
                pass
    _fsync_directory(sources_root)


def _capture_test_hook(label: str, resolved: _ResolvedSource) -> None:
    """No-op checkpoint used only for deterministic race-injection tests."""


def _publish(
    resolved: _ResolvedSource,
    home: Path | str | None,
    documents: Sequence[dict[str, Any]],
    versions: Sequence[dict[str, Any]],
    derivations: Sequence[dict[str, Any]],
    *,
    snapshot: SourceSnapshot,
    stage_blob: bool,
    expected_ledger_generation: str | None,
) -> str:
    sources_root = resolved.vault.root / ".kgdistiller" / "sources"
    manifest, contents = _build_generation(documents, versions, derivations)
    stage = _stage_generation(sources_root, manifest, contents, snapshot if stage_blob else None)
    try:
        candidate = _read_generation(
            stage,
            _read_manifest(stage)[0] or {},
            blob_roots=(stage, sources_root),
        )
        if candidate.generation_sha256 != manifest["generation_sha256"]:
            raise SourceArchiveError("invalid-source-generation", "staged generation failed validation")
        _recheck_resolution(resolved, home)
        current = _read_source(resolved.path)
        if current.raw_sha256 != snapshot.raw_sha256 or current.byte_count != snapshot.byte_count:
            raise SourceArchiveError("stale-live-source", "live source changed before publication")
        _install_generation(sources_root, stage, manifest, snapshot if stage_blob else None)
        _capture_test_hook("before-final-recheck", resolved)
        _recheck_resolution(resolved, home)
        final = _read_source(resolved.path)
        if final.raw_sha256 != snapshot.raw_sha256 or final.byte_count != snapshot.byte_count:
            raise SourceArchiveError("stale-live-source", "live source changed before manifest publication")
        live_manifest, _ = _read_manifest(sources_root)
        live_generation = (
            None if live_manifest is None else str(live_manifest["generation_sha256"])
        )
        if live_generation != expected_ledger_generation:
            raise SourceArchiveError(
                "stale-source-generation",
                "source ledger generation changed before manifest publication",
            )
        _atomic_replace_manifest(sources_root, manifest)
        return str(manifest["generation_sha256"])
    finally:
        _remove_stage(stage, sources_root)


def _report(
    action: str,
    resolved: _ResolvedSource,
    ledger_generation: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    return _contract(
        {
            "schema": REPORT_SCHEMA,
            "action": action,
            "status": "ok",
            "vault_id": resolved.vault.id,
            "registry_generation": resolved.registry_generation,
            "ledger_generation": ledger_generation,
            "result": result,
        },
        REPORT_SCHEMA,
        kind="source-report",
    )


def _capture_result(
    *,
    outcome: str,
    document: Mapping[str, Any],
    version: Mapping[str, Any],
    semantic_changed: bool,
    diff: dict[str, Any] | None,
    affected: Sequence[str],
) -> dict[str, Any]:
    return {
        "kind": "source-capture",
        "outcome": outcome,
        "document_id": document["document_id"],
        "path": document["path"],
        "format": document["format"],
        "semantic_changed": semantic_changed,
        "effective_status": document["status"],
        "predecessor_version_id": version["predecessor_version_id"],
        "current_version_id": version["version_id"],
        "diff": diff,
        "affected_concept_ids": list(affected),
    }


def capture_source(
    file: Path | str,
    *,
    home: Path | str | None = None,
    clock: Callable[[], datetime | str] | None = None,
    uuid_factory: Callable[[], uuid.UUID | str] | None = None,
) -> dict[str, Any]:
    """Capture one included live source into its owning Vault."""

    resolved = _resolve_source(file, home)
    with _vault_writer_lock(resolved.vault):
        _recheck_resolution(resolved, home)
        ledger = load_source_ledger(resolved.vault)
        snapshot = _read_source(resolved.path)
        documents = [dict(item) for item in ledger.documents]
        versions = [dict(item) for item in ledger.versions]
        derivations = [dict(item) for item in ledger.derivations]
        _, version_by_id, derivation_by_version = _version_maps(ledger)
        document = _document_for_path(ledger, resolved.relative_path)

        if document is not None:
            current = version_by_id[str(document["current_version_id"])]
            if current["raw_sha256"] == snapshot.raw_sha256:
                if document["path"] != resolved.relative_path:
                    updated = dict(document)
                    updated["path"] = resolved.relative_path
                    documents = [updated if item["document_id"] == document["document_id"] else item for item in documents]
                    generation = _publish(
                        resolved,
                        home,
                        documents,
                        versions,
                        derivations,
                        snapshot=snapshot,
                        stage_blob=False,
                        expected_ledger_generation=ledger.generation_sha256,
                    )
                    return _report(
                        "capture",
                        resolved,
                        generation,
                        _capture_result(
                            outcome="move",
                            document=updated,
                            version=current,
                            semantic_changed=False,
                            diff=None,
                            affected=[],
                        ),
                    )
                return _report(
                    "capture",
                    resolved,
                    ledger.generation_sha256,
                    _capture_result(
                        outcome="no_op",
                        document=document,
                        version=current,
                        semantic_changed=False,
                        diff=None,
                        affected=[],
                    ),
                )
        else:
            move_candidates: list[dict[str, Any]] = []
            for existing in ledger.documents:
                current_version = version_by_id[str(existing["current_version_id"])]
                old_live_path = resolved.vault.root.joinpath(*PurePosixPath(existing["path"]).parts)
                if current_version["raw_sha256"] == snapshot.raw_sha256 and _anchored_lstat(old_live_path) is None:
                    move_candidates.append(existing)
            if len(move_candidates) > 1:
                raise SourceArchiveError("ambiguous-source-move", "multiple absent documents match the live source bytes")
            if move_candidates:
                previous = move_candidates[0]
                current = version_by_id[str(previous["current_version_id"])]
                updated = dict(previous)
                updated["path"] = resolved.relative_path
                documents = [updated if item["document_id"] == previous["document_id"] else item for item in documents]
                generation = _publish(
                    resolved,
                    home,
                    documents,
                    versions,
                    derivations,
                    snapshot=snapshot,
                    stage_blob=False,
                    expected_ledger_generation=ledger.generation_sha256,
                )
                return _report(
                    "capture",
                    resolved,
                    generation,
                    _capture_result(
                        outcome="move",
                        document=updated,
                        version=current,
                        semantic_changed=False,
                        diff=None,
                        affected=[],
                    ),
                )

        if document is None:
            document_id = _document_uuid(uuid_factory)
            if any(item["document_id"] == document_id for item in documents):
                raise SourceArchiveError("duplicate-document-id", "UUID factory returned an existing document identity")
            predecessor = None
            sequence = 1
        else:
            document_id = str(document["document_id"])
            predecessor = version_by_id[str(document["current_version_id"])]
            sequence = int(predecessor["sequence"]) + 1
        if sequence > 99_999_999:
            raise SourceArchiveError("source-version-overflow", "source version sequence exceeds eight digits")
        version_id = f"doc:{document_id}:v{sequence:08d}"
        version = {
            "schema": VERSION_SCHEMA,
            "version_id": version_id,
            "document_id": document_id,
            "sequence": sequence,
            "raw_sha256": snapshot.raw_sha256,
            "normalized_text_sha256": snapshot.normalized_text_sha256,
            "blob_path": f"blobs/sha256/{snapshot.raw_sha256[:2]}/{snapshot.raw_sha256}",
            "captured_path": resolved.relative_path,
            "format": snapshot.format,
            "byte_count": snapshot.byte_count,
            "captured_at": _timestamp(clock),
            "predecessor_version_id": predecessor["version_id"] if predecessor is not None else None,
        }
        _contract(version, VERSION_SCHEMA, kind="source-version")
        versions.append(version)
        semantic_changed = predecessor is None or predecessor["normalized_text_sha256"] != snapshot.normalized_text_sha256
        affected = _effective_concepts(
            predecessor["version_id"] if predecessor is not None else None,
            version_by_id,
            derivation_by_version,
        ) if semantic_changed else []
        if (
            predecessor is not None
            and not semantic_changed
            and _effective_derivation(str(predecessor["version_id"]), derivation_by_version, version_by_id) is not None
        ):
            carry = {
                "schema": DERIVATION_SCHEMA,
                "version_id": version_id,
                "graph_generation_sha256": None,
                "candidate_dispositions": [],
                "concept_ids": [],
                "concept_evidence": [],
                "relation_evidence": [],
                "status": "carried-forward",
                "inherited_from_version_id": predecessor["version_id"],
                "ingest_receipt_sha256": None,
            }
            _contract(carry, DERIVATION_SCHEMA, kind="source-derivation")
            derivations.append(carry)

        all_version_by_id = {item["version_id"]: item for item in versions}
        all_derivation_by_version, failed_versions = _index_derivations(derivations)
        status = _derived_status(
            document_id,
            version_id,
            all_version_by_id,
            all_derivation_by_version,
            failed_versions,
        )
        new_document = {
            "schema": DOCUMENT_SCHEMA,
            "document_id": document_id,
            "path": resolved.relative_path,
            "format": snapshot.format,
            "normalized_text_sha256": snapshot.normalized_text_sha256,
            "current_version_id": version_id,
            "status": status,
        }
        _contract(new_document, DOCUMENT_SCHEMA, kind="source-document")
        if document is None:
            documents.append(new_document)
        else:
            documents = [new_document if item["document_id"] == document_id else item for item in documents]
        diff = None
        if semantic_changed:
            before = "" if predecessor is None else _version_text(ledger, predecessor)
            diff = _bounded_diff(
                before,
                snapshot.normalized_text,
                from_version_id=predecessor["version_id"] if predecessor is not None else None,
                to_version_id=version_id,
            )
        generation = _publish(
            resolved,
            home,
            documents,
            versions,
            derivations,
            snapshot=snapshot,
            stage_blob=True,
            expected_ledger_generation=ledger.generation_sha256,
        )
        return _report(
            "capture",
            resolved,
            generation,
            _capture_result(
                outcome="capture",
                document=new_document,
                version=version,
                semantic_changed=semantic_changed,
                diff=diff,
                affected=affected,
            ),
        )


def source_status(
    file: Path | str,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Report live-versus-current source freshness without taking a writer lock."""

    resolved = _resolve_source(file, home)
    ledger = load_source_ledger(resolved.vault)
    snapshot = _read_source(resolved.path)
    document = _document_for_path(ledger, resolved.relative_path)
    if document is None:
        result = {
            "kind": "source-status",
            "outcome": "uncaptured",
            "document_id": None,
            "path": resolved.relative_path,
            "format": snapshot.format,
            "raw_changed": True,
            "semantic_changed": True,
            "effective_status": "captured",
            "predecessor_version_id": None,
            "current_version_id": None,
        }
        return _report("status", resolved, ledger.generation_sha256, result)
    _, version_by_id, derivation_by_version = _version_maps(ledger)
    current = version_by_id[str(document["current_version_id"])]
    raw_changed = current["raw_sha256"] != snapshot.raw_sha256
    semantic_changed = current["normalized_text_sha256"] != snapshot.normalized_text_sha256
    if semantic_changed:
        outcome = "semantic-change"
        effective_status = "stale" if _effective_derivation(current["version_id"], derivation_by_version, version_by_id) is not None else document["status"]
    elif raw_changed:
        outcome = "raw-changed"
        effective_status = document["status"]
    else:
        outcome = "current"
        effective_status = document["status"]
    result = {
        "kind": "source-status",
        "outcome": outcome,
        "document_id": document["document_id"],
        "path": resolved.relative_path,
        "format": document["format"],
        "raw_changed": raw_changed,
        "semantic_changed": semantic_changed,
        "effective_status": effective_status,
        "predecessor_version_id": current["predecessor_version_id"],
        "current_version_id": current["version_id"],
    }
    return _report("status", resolved, ledger.generation_sha256, result)


def diff_source(
    file: Path | str,
    *,
    from_version: str | None = None,
    to_version: str | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Return a bounded three-context-line diff between archived versions."""

    resolved = _resolve_source(file, home)
    ledger = load_source_ledger(resolved.vault)
    document = _document_for_path(ledger, resolved.relative_path)
    if document is None:
        raise SourceArchiveError("source-not-captured", "source has no captured document identity")
    _, version_by_id, derivation_by_version = _version_maps(ledger)
    current_id = str(document["current_version_id"])
    to_id = to_version or current_id
    target = version_by_id.get(to_id)
    if target is None or target["document_id"] != document["document_id"]:
        raise SourceArchiveError("invalid-source-version", "--to must identify a version of this document")
    from_id = from_version if from_version is not None else target["predecessor_version_id"]
    predecessor: Mapping[str, Any] | None = None
    if from_id is not None:
        predecessor = version_by_id.get(str(from_id))
        if predecessor is None or predecessor["document_id"] != document["document_id"]:
            raise SourceArchiveError("invalid-source-version", "--from must identify a version of this document")
    before = "" if predecessor is None else _version_text(ledger, predecessor)
    after = _version_text(ledger, target)
    diff = _bounded_diff(before, after, from_version_id=str(from_id) if from_id is not None else None, to_version_id=to_id)
    result = {
        "kind": "source-diff",
        "document_id": document["document_id"],
        "path": document["path"],
        "format": target["format"],
        "semantic_changed": target["normalized_text_sha256"] != (predecessor["normalized_text_sha256"] if predecessor is not None else _sha256_bytes(b"")),
        "effective_status": document["status"],
        "predecessor_version_id": str(from_id) if from_id is not None else None,
        "current_version_id": to_id,
        "diff": diff,
        "affected_concept_ids": _effective_concepts(str(from_id) if from_id is not None else None, version_by_id, derivation_by_version),
    }
    return _report("diff", resolved, ledger.generation_sha256, result)


__all__ = [
    "DERIVATION_SCHEMA",
    "DOCUMENT_SCHEMA",
    "LEDGER_SCHEMA",
    "REPORT_SCHEMA",
    "VERSION_SCHEMA",
    "SourceArchiveError",
    "SourceEvidenceView",
    "SourceLedger",
    "capture_source",
    "current_evidence_view",
    "diff_source",
    "extract_evidence_excerpt",
    "load_source_ledger",
    "normalize_source_text",
    "read_vault_relative_regular",
    "replace_vault_relative_regular",
    "source_status",
    "unlink_vault_relative_regular",
    "vault_generation_guard",
    "vault_staging_directory",
    "vault_writer_lock",
    "verify_evidence_span",
]
