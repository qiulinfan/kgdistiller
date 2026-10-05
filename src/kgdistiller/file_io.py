"""Regular-file signatures across path and descriptor stat implementations."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

_WINDOWS = os.name == "nt"


def file_signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def same_file_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    if not _WINDOWS:
        return file_signature(left) == file_signature(right)
    # Windows path stat retains creation-time ctime, while descriptor stat on
    # newer Python exposes ChangeTime. Compare identity and birthtime instead.
    return (file_signature(left)[:4] == file_signature(right)[:4]
            and getattr(left, "st_birthtime_ns", None) == getattr(right, "st_birthtime_ns", None))


def descriptor_signature(descriptor: int) -> tuple[int, ...]:
    info = os.fstat(descriptor)
    signature = file_signature(info)
    if not _WINDOWS:
        return signature
    # Windows timestamps do not prove content equality, even when descriptor
    # stat exposes ChangeTime. Check content before reusing a parsed record.
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    digest = hashlib.sha256()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        for chunk in iter(lambda: os.read(descriptor, 65536), b""):
            digest.update(chunk)
        if file_signature(os.fstat(descriptor)) != signature:
            raise ValueError("file changed while collecting its signature")
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)
    return signature + (int.from_bytes(digest.digest(), "big"),)


def path_signature(path: Path, info: os.stat_result) -> tuple[int, ...]:
    if not _WINDOWS:
        return file_signature(info)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not same_file_metadata(info, opened):
            raise ValueError("file changed while opening")
        signature = descriptor_signature(descriptor)
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or not same_file_metadata(info, current):
            raise ValueError("file path changed while collecting its signature")
        return signature
    finally:
        os.close(descriptor)
