"""The source registry: root safety, pattern admission and single ownership."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kgdistiller.sources import (
    SOURCE_SCHEMA,
    KnowledgeError,
    glob_matches_path,
    load_sources,
    source_for_path,
)


class LoadSourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="kgdistiller-sources-")
        self.addCleanup(directory.cleanup)
        base = Path(directory.name).resolve()
        self.root = base / "project"
        self.outside = base / "outside"
        (self.root / "notes").mkdir(parents=True)
        self.outside.mkdir()
        self.registry = self.root / ".knowledge/sources.json"
        self.registry.parent.mkdir()

    def write(self, payload: Any) -> None:
        self.registry.write_text(json.dumps(payload), encoding="utf-8")

    def load(self, *sources: dict[str, Any], **extra: Any):
        self.write({"schema": SOURCE_SCHEMA, "sources": list(sources), **extra})
        return load_sources(self.root, self.registry)

    def rejects(self, message: str, *sources: dict[str, Any], **extra: Any) -> None:
        with self.assertRaisesRegex(KnowledgeError, message):
            self.load(*sources, **extra)

    def test_a_valid_source_resolves_inside_the_repository(self) -> None:
        (spec,) = self.load({"id": "local:notes", "root": "notes", "files": ["**/*.txt"]})
        self.assertEqual((spec.id, spec.root, spec.patterns, spec.document_type),
                         ("local:notes", self.root / "notes", ("**/*.txt",), ""))

    def test_registry_shape_errors(self) -> None:
        self.write({"schema": "unexpected", "sources": []})
        with self.assertRaisesRegex(KnowledgeError, "expected kgdistiller-sources-v1"):
            load_sources(self.root, self.registry)
        self.rejects("unknown source registry key 'unexpected'", unexpected=[])
        self.write({"schema": SOURCE_SCHEMA, "sources": {}})
        with self.assertRaisesRegex(KnowledgeError, "sources must be a list"):
            load_sources(self.root, self.registry)
        self.rejects("entries must be objects", "notes")

    def test_source_identity_and_key_errors(self) -> None:
        good = {"id": "a", "root": "notes", "files": ["*"]}
        self.rejects("duplicate or empty source id: ''", {**good, "id": ""})
        self.rejects("duplicate or empty source id: 'a'", good, good)
        self.rejects("unknown key 'unexpected' in source 'a'", {**good, "unexpected": True})

    def test_roots_must_be_portable_and_inside_the_repository(self) -> None:
        for root, message in (
            ("", "has no portable relative root"),
            (str(self.root / "notes"), "portable relative path"),
            ("../outside", "portable relative path"),
            ("notes/../../outside", "portable relative path"),
            (".knowledge", "outside the .knowledge/ tree"),
            ("notes/.knowledge", "outside the .knowledge/ tree"),
            ("missing", "missing source root"),
        ):
            with self.subTest(root=root):
                self.rejects(message, {"id": "a", "root": root, "files": ["*"]})

    def test_symlinked_roots_are_rejected_inside_and_outside(self) -> None:
        (self.root / "escape").symlink_to(self.outside, target_is_directory=True)
        self.rejects("escapes repository", {"id": "a", "root": "escape", "files": ["*"]})
        (self.root / "alias").symlink_to(self.root / "notes", target_is_directory=True)
        self.rejects("must not traverse a symlink", {"id": "a", "root": "alias", "files": ["*"]})
        (self.root / "notes/inner").mkdir()
        (self.root / "via").symlink_to(self.root / "notes", target_is_directory=True)
        self.rejects("must not traverse a symlink", {"id": "a", "root": "via/inner", "files": ["*"]})

    def test_patterns_must_be_a_nonempty_list_of_strings(self) -> None:
        for files in (None, [], "*", [""], [3]):
            with self.subTest(files=files):
                source = {"id": "a", "root": "notes"}
                if files is not None:
                    source["files"] = files
                self.rejects("has no bounded file patterns", source)

    def test_document_types_must_be_declared(self) -> None:
        source = {"id": "a", "root": "notes", "files": ["*"], "document_type": "math"}
        with self.assertRaises(KnowledgeError):
            self.load(source)
        profile = {"math": {"node_kinds": ["definition"], "extraction_guidance": "Definitions."}}
        (spec,) = self.load(source, document_types=profile)
        self.assertEqual(spec.document_type, "math")


class GlobTest(unittest.TestCase):
    def test_double_star_matches_zero_or_more_segments(self) -> None:
        for path, pattern, expected in (
            ("a.txt", "**/*.txt", True),
            ("x/y/a.txt", "**/*.txt", True),
            ("x/y/a.txt", "x/**/a.txt", True),
            ("x/a.txt", "x/**/a.txt", True),
            ("x/y/a.txt", "x/**", True),
            ("a.txt", "**", True),
            ("x/y/a.md", "**/*.txt", False),
        ):
            with self.subTest(path=path, pattern=pattern):
                self.assertIs(glob_matches_path(Path(path), pattern), expected)

    def test_single_star_stays_within_one_segment(self) -> None:
        self.assertTrue(glob_matches_path(Path("a.tex"), "*.tex"))
        self.assertFalse(glob_matches_path(Path("chapters/a.tex"), "*.tex"))
        self.assertTrue(glob_matches_path(Path("chapters/a.tex"), "chapters/*.tex"))
        self.assertFalse(glob_matches_path(Path("chapters/x/a.tex"), "chapters/*.tex"))
        self.assertTrue(glob_matches_path(Path("main.tex"), "ma?n.tex"))
        self.assertFalse(glob_matches_path(Path("Main.tex"), "main.tex"))


class SourceForPathTest(unittest.TestCase):
    def test_exactly_one_registered_source_must_admit_a_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-owner-") as folder:
            root = Path(folder).resolve()
            (root / "notes/.knowledge").mkdir(parents=True)
            registry = root / "sources.json"
            registry.write_text(json.dumps({"schema": SOURCE_SCHEMA, "sources": [
                {"id": "tex", "root": "notes", "files": ["**/*.tex"]},
                {"id": "all", "root": "notes", "files": ["**/*.txt", "shared.md"]},
                {"id": "shared", "root": "notes", "files": ["shared.md"]},
            ]}), encoding="utf-8")
            specs = load_sources(root, registry)
            self.assertEqual(source_for_path(specs, root / "notes/a/b.tex").id, "tex")
            self.assertEqual(source_for_path(specs, root / "notes/a.txt").id, "all")
            with self.assertRaisesRegex(KnowledgeError, r"several registered sources: .*\(all, shared\)"):
                source_for_path(specs, root / "notes/shared.md")
            for path in (root / "notes/a.typ", root / "elsewhere.tex", root / "notes/.knowledge/x.txt"):
                with self.subTest(path=path), self.assertRaisesRegex(KnowledgeError, "not admitted"):
                    source_for_path(specs, path)


if __name__ == "__main__":
    unittest.main()
