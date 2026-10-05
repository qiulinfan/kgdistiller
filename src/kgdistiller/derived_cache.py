"""Small, typed exact-input caches; never an authority or an identity index."""

from __future__ import annotations

import json
import copy
import math
import os
import stat
import uuid
from contextlib import contextmanager
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .contracts import canonical_json, sha256_json


EXACT_INPUT_CACHE_SCHEMA = "kgdistiller-exact-input-cache-v1"
MAX_RECORD_BYTES = 256 * 1024
MAX_VECTOR_DIMENSIONS = 8192
MAX_MEMORY_RECORDS = 16384
MAX_MEMORY_BYTES = 16 * 1024 * 1024
OPERATIONS = {"document-vector", "query-vector", "pair-score"}


class DerivedCacheError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _validate_value(operation: str, value: Any) -> None:
    values = value if operation != "pair-score" else [value]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_VECTOR_DIMENSIONS:
        raise ValueError("invalid exact-input value shape")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
        raise ValueError("invalid exact-input value type")
    if any(not math.isfinite(item) for item in values):
        raise ValueError("nonfinite exact-input value")
    if operation != "pair-score" and (not math.isfinite(math.hypot(*values)) or math.hypot(*values) == 0):
        raise ValueError("invalid exact-input vector norm")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cache field")
        result[key] = value
    return result


def file_signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Include ctime so restoring mtime after an edit cannot retain a hit."""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ExactInputCache:
    """Persist one value per exact operation/model/projection/input binding.

    POSIX reads and replacements stay relative to no-follow directory handles.
    The portable path also rejects symlinks and checks regular-file identity.
    No directory or record is created merely by a missing lookup.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.directory = self.cache_dir / "exact-inputs-v1"
        self._memory: OrderedDict[str, tuple[tuple[int, ...], Any, int]] = OrderedDict()
        self._memory_bytes = 0

    def _remember(self, filename: str, signature: tuple[int, ...], value: Any, size: int) -> None:
        cost = max(size, len(value) * 40 if isinstance(value, list) else 256)
        previous = self._memory.pop(filename, None)
        if previous is not None:
            self._memory_bytes -= previous[2]
        if cost > MAX_MEMORY_BYTES:
            return
        self._memory[filename] = (signature, copy.deepcopy(value), cost)
        self._memory_bytes += cost
        while len(self._memory) > MAX_MEMORY_RECORDS or self._memory_bytes > MAX_MEMORY_BYTES:
            _, (_, _, removed) = self._memory.popitem(last=False)
            self._memory_bytes -= removed

    def _filename(self, binding: dict[str, Any]) -> str:
        operation = binding.get("operation")
        if operation not in OPERATIONS:
            raise DerivedCacheError("invalid-exact-input-cache", "exact-input operation is invalid")
        return f"{operation}-{sha256_json(binding)}.json"

    @contextmanager
    def _directory(self, *, create: bool):
        descriptors: list[int] = []
        try:
            if create:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                info = self.cache_dir.lstat()
            except FileNotFoundError:
                yield None
                return
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("cache root is not a real directory")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            relative = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
            if relative:
                root_fd = os.open(self.cache_dir, flags)
                descriptors.append(root_fd)
                try:
                    child_fd = os.open("exact-inputs-v1", flags, dir_fd=root_fd)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    try:
                        os.mkdir("exact-inputs-v1", dir_fd=root_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open("exact-inputs-v1", flags, dir_fd=root_fd)
                descriptors.append(child_fd)
                if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                    raise ValueError("exact-input path is not a directory")
                yield child_fd
            else:
                if create:
                    self.directory.mkdir(exist_ok=True)
                try:
                    child_info = self.directory.lstat()
                except FileNotFoundError:
                    yield None
                    return
                if not stat.S_ISDIR(child_info.st_mode):
                    raise ValueError("exact-input path is not a real directory")
                yield False
        except DerivedCacheError:
            raise
        except (OSError, ValueError) as error:
            code = "exact-input-cache-unwritable" if create else "invalid-exact-input-cache"
            raise DerivedCacheError(code, "exact-input cache directory is unavailable or unsafe") from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def get(self, binding: dict[str, Any]) -> Any | None:
        filename = self._filename(binding)
        try:
            with self._directory(create=False) as descriptor:
                if descriptor is None:
                    return None
                path = self.directory / filename
                try:
                    info = os.stat(filename, dir_fd=descriptor, follow_symlinks=False) if descriptor is not False else path.lstat()
                except FileNotFoundError:
                    removed = self._memory.pop(filename, None)
                    if removed is not None:
                        self._memory_bytes -= removed[2]
                    return None
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                    raise ValueError("cache record size/type")
                signature = file_signature(info)
                remembered = self._memory.get(filename)
                if remembered is not None and remembered[0] == signature:
                    self._memory.move_to_end(filename)
                    return copy.deepcopy(remembered[1])
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    handle_fd = os.open(filename, flags, dir_fd=descriptor) if descriptor is not False else os.open(path, flags)
                except FileNotFoundError:
                    return None
                with os.fdopen(handle_fd, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_RECORD_BYTES:
                        raise ValueError("cache record size/type")
                    if file_signature(opened) != signature:
                        raise ValueError("cache record changed while opening")
                    raw = handle.read(MAX_RECORD_BYTES + 1)
                    if file_signature(os.fstat(handle.fileno())) != signature:
                        raise ValueError("cache record changed while reading")
                if len(raw) > MAX_RECORD_BYTES:
                    raise ValueError("cache record size")
                payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                if not isinstance(payload, dict) or set(payload) != {"schema", "binding", "value", "cache_sha256"}:
                    raise ValueError("cache record fields")
                if payload["schema"] != EXACT_INPUT_CACHE_SCHEMA or payload["binding"] != binding:
                    raise ValueError("cache record binding")
                if payload["cache_sha256"] != sha256_json({key: value for key, value in payload.items() if key != "cache_sha256"}):
                    raise ValueError("cache record digest")
                _validate_value(binding["operation"], payload["value"])
                self._remember(filename, signature, payload["value"], len(raw))
                return payload["value"]
        except DerivedCacheError:
            raise
        except Exception as error:
            raise DerivedCacheError("invalid-exact-input-cache", "exact-input cache is malformed, changed, or unsafe") from error

    def put(self, binding: dict[str, Any], value: Any, *, only_if_absent: bool = False) -> bool:
        filename = self._filename(binding)
        try:
            _validate_value(binding["operation"], value)
            payload = {"schema": EXACT_INPUT_CACHE_SCHEMA, "binding": binding, "value": value}
            payload["cache_sha256"] = sha256_json(payload)
            raw = canonical_json(payload).encode("utf-8")
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError("cache record exceeds byte limit")
        except Exception as error:
            raise DerivedCacheError("invalid-exact-input-cache", "exact-input cache value is invalid or oversized") from error
        temporary = f".exact-{uuid.uuid4().hex}.tmp"
        with self._directory(create=True) as descriptor:
            try:
                if only_if_absent:
                    try:
                        existing = os.stat(filename, dir_fd=descriptor, follow_symlinks=False) if descriptor is not False else (self.directory / filename).lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        if not stat.S_ISREG(existing.st_mode):
                            raise DerivedCacheError("invalid-exact-input-cache", "existing exact-input record is unsafe")
                        return False
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(temporary, flags, 0o600, dir_fd=descriptor) if descriptor is not False else os.open(self.directory / temporary, flags, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                if descriptor is not False:
                    os.replace(temporary, filename, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                else:
                    os.replace(self.directory / temporary, self.directory / filename)
                info = os.stat(filename, dir_fd=descriptor, follow_symlinks=False) if descriptor is not False else (self.directory / filename).lstat()
                self._remember(filename, file_signature(info), value, len(raw))
                return True
            except OSError as error:
                raise DerivedCacheError("exact-input-cache-unwritable", "exact-input cache could not be written atomically") from error
            finally:
                try:
                    if descriptor is not False:
                        os.unlink(temporary, dir_fd=descriptor)
                    else:
                        (self.directory / temporary).unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
