from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kgdistiller.contracts import sha256_json
from kgdistiller.derived_cache import DerivedCacheError, ExactInputCache, MAX_RECORD_BYTES


def binding(operation: str = "document-vector") -> dict:
    return {"operation": operation, "projection": "projection-v1", "model": {"revision": "immutable"},
            "inputs": [{"sha256": "a" * 64, "bytes": 10}]}


class ExactInputCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="kgdistiller-exact-input-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache = ExactInputCache(self.root / "cache")

    def test_missing_lookup_creates_nothing_and_values_persist(self) -> None:
        self.assertIsNone(self.cache.get(binding()))
        self.assertFalse(self.cache.cache_dir.exists())
        self.cache.put(binding(), [1.0, 2.0])
        self.assertEqual([1.0, 2.0], ExactInputCache(self.cache.cache_dir).get(binding()))
        self.assertEqual(1, len(list(self.cache.directory.glob("*.json"))))
        self.assertFalse(self.cache.put(binding(), [3.0, 4.0], only_if_absent=True))
        self.assertEqual([1.0, 2.0], self.cache.get(binding()))

    def test_corrupted_digest_wrong_binding_and_duplicate_fields_fail_closed(self) -> None:
        self.cache.put(binding(), [1.0, 2.0])
        path = next(self.cache.directory.glob("*.json"))
        original = path.read_text()
        payload = json.loads(original); payload["value"][0] += 1
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(DerivedCacheError, "invalid-exact-input-cache"):
            self.cache.get(binding())
        payload = json.loads(original); payload["binding"]["projection"] = "another-projection"
        payload.pop("cache_sha256"); payload["cache_sha256"] = sha256_json(payload)
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(DerivedCacheError, "invalid-exact-input-cache"):
            self.cache.get(binding())
        path.write_text(original.replace('{"binding":', '{"schema":"duplicated","binding":', 1))
        with self.assertRaisesRegex(DerivedCacheError, "invalid-exact-input-cache"):
            self.cache.get(binding())

    def test_resigned_values_still_require_typed_finite_bounded_data(self) -> None:
        for operation, good, bad_values in (
            ("document-vector", [1.0, 2.0], ([], [True, 0], [0, 0], [[1]], [1] * 8193)),
            ("pair-score", 0.5, (True, "score", [], [1.0])),
        ):
            self.cache.put(binding(operation), good)
            path = self.cache.directory / self.cache._filename(binding(operation))
            for value in bad_values:
                payload = json.loads(path.read_text())
                payload["value"] = value; payload.pop("cache_sha256")
                payload["cache_sha256"] = sha256_json(payload)
                path.write_text(json.dumps(payload))
                with self.subTest(operation=operation, type=type(value).__name__), self.assertRaises(DerivedCacheError):
                    self.cache.get(binding(operation))
            path.write_text('{"value":NaN}')
            with self.assertRaises(DerivedCacheError):
                self.cache.get(binding(operation))

    def test_record_size_and_invalid_write_values_are_bounded(self) -> None:
        self.cache.put(binding(), [1.0, 2.0])
        path = next(self.cache.directory.glob("*.json"))
        path.write_bytes(b" " * (MAX_RECORD_BYTES + 1))
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())
        for value in ([float("inf")], [float("nan")], [True], [0], [10 ** 1000]):
            with self.subTest(value_type=type(value[0]).__name__), self.assertRaises(DerivedCacheError):
                self.cache.put(binding(), value)

    def test_atomic_failure_keeps_old_record_and_leaves_no_temp_files(self) -> None:
        self.cache.put(binding(), [1.0, 2.0])
        with patch("kgdistiller.derived_cache.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(DerivedCacheError, "unwritable"):
                self.cache.put(binding(), [3.0, 4.0])
        self.assertEqual([1.0, 2.0], self.cache.get(binding()))
        self.assertEqual([], list(self.cache.directory.glob("*.tmp")))

    def test_validated_memory_skips_parsing_but_detects_restored_mtime_tampering(self) -> None:
        self.cache.put(binding("pair-score"), 1.0)
        with patch("kgdistiller.derived_cache.json.loads", side_effect=AssertionError("valid unchanged record should not reparse")):
            self.assertEqual(1.0, self.cache.get(binding("pair-score")))
        path = next(self.cache.directory.glob("*.json")); info = path.stat()
        raw = path.read_text(); changed = raw.replace('"value":1.0', '"value":2.0')
        self.assertEqual(len(raw), len(changed)); self.assertNotEqual(raw, changed)
        path.write_text(changed); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding("pair-score"))

    def test_memory_values_are_not_mutable_shared_authority_and_are_bounded(self) -> None:
        self.cache.put(binding(), [1.0, 2.0])
        value = self.cache.get(binding()); value[0] = 99
        self.assertEqual([1.0, 2.0], self.cache.get(binding()))
        with patch("kgdistiller.derived_cache.MAX_MEMORY_RECORDS", 2):
            for index in range(4):
                input_binding = binding("pair-score"); input_binding["inputs"][0]["bytes"] = index
                self.cache.put(input_binding, float(index))
        self.assertLessEqual(len(self.cache._memory), 2)
        self.assertGreater(self.cache._memory_bytes, 0)
        with patch("kgdistiller.derived_cache.MAX_MEMORY_BYTES", 1):
            self.cache.put(binding("query-vector"), [1.0, 2.0])
        self.assertNotIn(self.cache._filename(binding("query-vector")), self.cache._memory)

    def test_windows_stat_times_preserve_hits_and_detect_restored_mtime_tampering(self) -> None:
        actual_lstat, actual_fstat = Path.lstat, os.fstat
        for change_time_available in (False, True):
            with self.subTest(change_time_available=change_time_available):
                births = {}
                cache = ExactInputCache(self.root / str(change_time_available))

                def metadata(info, *, descriptor=False):
                    fields = {name: getattr(info, name) for name in (
                        "st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
                    birth = births.setdefault((info.st_dev, info.st_ino), info.st_ctime_ns)
                    if change_time_available:
                        fields["st_birthtime_ns"] = birth
                    if not descriptor or not change_time_available:
                        fields["st_ctime_ns"] = birth
                    return SimpleNamespace(**fields)

                with patch("kgdistiller.file_io._WINDOWS", True), \
                        patch("kgdistiller.derived_cache.os.supports_dir_fd", set()), \
                        patch.object(Path, "lstat", lambda path: metadata(actual_lstat(path))), \
                        patch("kgdistiller.file_io.os.fstat", lambda fd: metadata(actual_fstat(fd), descriptor=True)):
                    cache.put(binding("pair-score"), 1.0)
                    self.assertEqual(1.0, ExactInputCache(cache.cache_dir).get(binding("pair-score")))
                    with patch("kgdistiller.derived_cache.json.loads", side_effect=AssertionError("unchanged record should not reparse")):
                        self.assertEqual(1.0, cache.get(binding("pair-score")))
                    path = next(cache.directory.glob("*.json"))
                    info = path.stat()
                    raw = path.read_bytes()
                    changed = raw.replace(b'"value":1.0', b'"value":2.0')
                    self.assertEqual(len(raw), len(changed))
                    self.assertNotEqual(raw, changed)
                    path.write_bytes(changed)
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                    with self.assertRaisesRegex(DerivedCacheError, "invalid-exact-input-cache"):
                        cache.get(binding("pair-score"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX filesystem objects")
    def test_symlink_fifo_and_directory_records_never_follow_or_block(self) -> None:
        self.cache.put(binding(), [1.0, 2.0])
        path = next(self.cache.directory.glob("*.json")); path.unlink()
        os.mkfifo(path)
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())
        path.unlink()
        outside = self.root / "outside"; outside.write_text("private content")
        path.symlink_to(outside)
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())
        with self.assertRaises(DerivedCacheError):
            self.cache.put(binding(), [1.0, 2.0], only_if_absent=True)
        self.assertEqual("private content", outside.read_text())
        path.unlink(); path.mkdir()
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlinks")
    def test_root_and_exact_input_directory_symlinks_are_rejected(self) -> None:
        outside = self.root / "outside"; outside.mkdir()
        self.cache.cache_dir.symlink_to(outside)
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())
        with self.assertRaises(DerivedCacheError):
            self.cache.put(binding(), [1.0, 2.0])
        self.assertEqual([], list(outside.iterdir()))
        self.cache.cache_dir.unlink(); self.cache.cache_dir.mkdir()
        self.cache.directory.symlink_to(outside)
        with self.assertRaises(DerivedCacheError):
            self.cache.get(binding())
        with self.assertRaises(DerivedCacheError):
            self.cache.put(binding(), [1.0, 2.0])
        self.assertEqual([], list(outside.iterdir()))


if __name__ == "__main__":
    unittest.main()
