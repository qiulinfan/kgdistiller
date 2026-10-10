"""The kgdistiller home: config.json, types/, base registration, base lookup and the lock."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import home as home_module
from kgdistiller.cli import main
from kgdistiller.home import (
    Base,
    DocumentType,
    KnowledgeError,
    LockConflict,
    add_base,
    home_directory,
    list_bases,
    load_home,
    load_type,
    lock,
    registered_bases,
    remove_base,
    resolve_base,
)
from tests.knowledge_fixture import use_temporary_home

TYPE_TEXT = "---\nnode_kinds: [definition, theorem]\n---\nNodes are definitions and theorems.\n"
SYMLINKS = unittest.skipIf(os.name == "nt", "directory symlinks need extra privileges on Windows")


class HomeTestCase(unittest.TestCase):
    """Every test runs against a temporary KGDISTILLER_HOME and a temporary user home."""

    def setUp(self) -> None:
        self.home = use_temporary_home(self)
        self.tmp = self.home.parent
        self.user = self.tmp / "user"
        self.user.mkdir()
        environment = patch.dict(os.environ, {"HOME": str(self.user), "USERPROFILE": str(self.user)})
        environment.start()
        self.addCleanup(environment.stop)

    def directory(self, relative: str) -> Path:
        path = self.tmp / relative
        path.mkdir(parents=True, exist_ok=True)
        return path

    def config(self) -> dict[str, Any]:
        return json.loads((self.home / "config.json").read_text(encoding="utf-8"))

    def create_home(self) -> None:
        """Lay out an empty home directly, as `base add` does on first use."""
        (self.home / "types").mkdir(parents=True, exist_ok=True)
        if not (self.home / "config.json").exists():
            (self.home / "config.json").write_text('{"bases": {}, "embedding": null}\n', encoding="utf-8")

    def write_config(self, payload: Any) -> None:
        self.create_home()
        (self.home / "config.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_type(self, name: str, text: str = TYPE_TEXT) -> Path:
        self.create_home()
        path = self.home / "types" / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path


class HomeDirectoryTest(HomeTestCase):
    def test_environment_overrides_the_default(self) -> None:
        self.assertEqual(home_directory(), self.home)
        with patch.dict(os.environ):
            del os.environ["KGDISTILLER_HOME"]
            self.assertEqual(home_directory(), Path.home() / ".knowledge")
        with patch.dict(os.environ, {"KGDISTILLER_HOME": "~/kh"}):
            self.assertEqual(home_directory(), Path.home() / "kh")

    def test_relative_home_is_refused(self) -> None:
        for value in ("relative/home", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"KGDISTILLER_HOME": value}), \
                    self.assertRaisesRegex(KnowledgeError, "absolute"):
                home_directory()

    def test_reads_without_a_home_refuse_and_create_nothing(self) -> None:
        reads = (
            list_bases,
            registered_bases,
            lambda: load_home(self.home),
            lambda: resolve_base(None, self.tmp),
            lambda: resolve_base("notes", self.tmp),
            lambda: remove_base("notes"),
        )
        for read in reads:
            with self.subTest(read=read), self.assertRaisesRegex(KnowledgeError, "kgd base add"):
                read()
        with self.assertRaises(KnowledgeError), lock():
            pass
        self.assertFalse(self.home.exists())


class HomeCreationTest(HomeTestCase):
    def test_first_base_add_creates_config_types_and_gitignore(self) -> None:
        add_base(self.directory("kb"))
        self.assertEqual(self.config()["embedding"], None)
        self.assertEqual(sorted(self.config()["bases"]), ["kb"])
        self.assertTrue((self.home / "types").is_dir())
        self.assertEqual((self.home / ".gitignore").read_bytes(), b"index.sqlite*\nlock\n")

    def test_base_add_keeps_existing_home_files(self) -> None:
        self.write_config({"bases": {}, "embedding": "some/model"})
        (self.home / ".gitignore").write_text("custom\n", encoding="utf-8")
        add_base(self.directory("kb"))
        self.assertEqual(self.config()["embedding"], "some/model")
        self.assertEqual((self.home / ".gitignore").read_text(encoding="utf-8"), "custom\n")
        load_home(self.home)

    def test_config_writes_are_atomic(self) -> None:
        add_base(self.directory("kb"))
        self.assertEqual(sorted(path.name for path in self.home.iterdir()),
                         [".gitignore", "config.json", "lock", "types"])
        before = (self.home / "config.json").read_bytes()
        with patch.object(home_module.os, "replace", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            add_base(self.directory("other"))
        self.assertEqual((self.home / "config.json").read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()),
                         [".gitignore", "config.json", "lock", "types"])


class ConfigValidationTest(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_type("notes-type")
        self.root = self.directory("kb")

    def base_config(self, **changes: Any) -> dict[str, Any]:
        entry = {"path": str(self.root), "sources": {"notes/*.md": "notes-type"}, **changes}
        return {"bases": {"kb": entry}, "embedding": None}

    def assert_invalid(self, payload: Any, message: str) -> None:
        self.write_config(payload)
        with self.assertRaisesRegex(KnowledgeError, message):
            load_home(self.home)

    def test_valid_config_loads(self) -> None:
        self.write_config(self.base_config())
        loaded = load_home(self.home)
        self.assertEqual(loaded.bases["kb"].root, self.root)
        self.assertEqual(loaded.bases["kb"].sources, {"notes/*.md": "notes-type"})
        self.assertEqual(loaded.types["notes-type"].node_kinds, ("definition", "theorem"))
        self.assertIsNone(loaded.embedding)

    def test_shape_errors(self) -> None:
        self.assert_invalid({**self.base_config(), "extra": 1}, "exactly the keys bases and embedding")
        self.assert_invalid({"bases": {}}, "exactly the keys bases and embedding")
        self.assert_invalid({"bases": {}, "embedding": ""}, "embedding")
        self.assert_invalid(self.base_config(extra=True), "exactly the keys path and sources")
        self.assert_invalid({"bases": {"My Notes": self.base_config()["bases"]["kb"]}, "embedding": None},
                            "base name")
        self.assert_invalid(self.base_config(path="relative/kb"), "path must be")

    def test_bad_globs(self) -> None:
        for pattern in ("/abs/*.md", "../x/*.md", "notes/../x.md", "a\\b.md", "", ".knowledge/entries/*.md",
                        "notes//a.md", "./notes/*.md"):
            with self.subTest(pattern=pattern):
                self.assert_invalid(self.base_config(sources={pattern: "notes-type"}), "glob")

    def test_unknown_type(self) -> None:
        self.assert_invalid(self.base_config(sources={"notes/*.md": "missing"}), "unknown type 'missing'")

    def test_invalid_json(self) -> None:
        self.create_home()
        (self.home / "config.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "invalid JSON"):
            load_home(self.home)

    def test_nested_roots_and_home_inside_a_root(self) -> None:
        inner = self.directory("kb/inner")
        payload = self.base_config()
        payload["bases"]["inner"] = {"path": str(inner), "sources": {}}
        self.assert_invalid(payload, "lies inside base kb")
        payload = self.base_config(path=str(self.tmp))
        self.assert_invalid(payload, "home .* lies inside base kb")


class TypeLoadingTest(HomeTestCase):
    def load(self, text: str, name: str = "sample") -> DocumentType:
        path = self.directory("types-under-test") / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return load_type(path)

    def test_full_type_is_parsed(self) -> None:
        loaded = self.load(
            "---\nnode_kinds: [definition, theorem]\nrelation_kinds:\n  implies: [premise, conclusion]\n"
            "  equivalent: [side]\nepistemic: [proved, stated]\n---\n\nGuidance text.\n\n"
        )
        self.assertEqual(loaded.name, "sample")
        self.assertEqual(loaded.node_kinds, ("definition", "theorem"))
        self.assertEqual(loaded.relation_kinds, {"implies": ("premise", "conclusion"), "equivalent": ("side",)})
        self.assertEqual(loaded.epistemic, ("proved", "stated"))
        self.assertEqual(loaded.guidance, "Guidance text.")

    def test_base_loader_keeps_strings(self) -> None:
        loaded = self.load("---\nnode_kinds: [on, yes, 1]\n---\nBody.\n")
        self.assertEqual(loaded.node_kinds, ("on", "yes", "1"))
        self.assertEqual(loaded.relation_kinds, {})
        self.assertEqual(loaded.epistemic, ())

    def test_invalid_types(self) -> None:
        cases = {
            "missing node_kinds": ("---\nepistemic: [proved]\n---\nBody.\n", "node_kinds must be a non-empty list"),
            "empty node_kinds": ("---\nnode_kinds: []\n---\nBody.\n", "node_kinds must be a non-empty list"),
            "duplicate kinds": ("---\nnode_kinds: [a, a]\n---\nBody.\n", "must not repeat"),
            "node and relation": ("---\nnode_kinds: [a]\nrelation_kinds:\n  a: [x]\n---\nBody.\n",
                                  "both a node kind and a relation kind"),
            "dotted role": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: [x.y]\n---\nBody.\n", "invalid slug"),
            "uppercase role": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: [Role]\n---\nBody.\n", "invalid slug"),
            "digit role": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: [1st]\n---\nBody.\n", "invalid slug"),
            "fixed key role": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: [subject, source]\n---\nBody.\n",
                               "'source' of r is a fixed record key"),
            "empty roles": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: []\n---\nBody.\n", "non-empty list"),
            "duplicate roles": ("---\nnode_kinds: [a]\nrelation_kinds:\n  r: [x, x]\n---\nBody.\n", "must not repeat"),
            "duplicate epistemic": ("---\nnode_kinds: [a]\nepistemic: [p, p]\n---\nBody.\n", "must not repeat"),
            "empty body": ("---\nnode_kinds: [a]\n---\n  \n\n", "body"),
            "unknown key": ("---\nnode_kinds: [a]\nguidance: x\n---\nBody.\n", "unknown frontmatter key"),
            "no frontmatter": ("node_kinds: [a]\n", "frontmatter"),
            "unterminated": ("---\nnode_kinds: [a]\nBody.\n", "frontmatter"),
            "not a mapping": ("---\n- a\n---\nBody.\n", "mapping"),
            "bad yaml": ("---\nnode_kinds: [a\n---\nBody.\n", "invalid frontmatter"),
        }
        for label, (text, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(KnowledgeError, message):
                self.load(text)

    def test_type_name_must_be_a_slug(self) -> None:
        for name in ("Math_Notes", "-x"):
            with self.subTest(name=name), self.assertRaisesRegex(KnowledgeError, "slug"):
                self.load(TYPE_TEXT, name)

    def test_home_loads_every_type(self) -> None:
        self.write_type("good")
        self.write_type("broken", "---\nnode_kinds: []\n---\nBody.\n")
        self.write_config({"bases": {}, "embedding": None})
        with self.assertRaisesRegex(KnowledgeError, "broken"):
            load_home(self.home)


class BaseAddTest(HomeTestCase):
    def test_default_name_is_the_basename(self) -> None:
        root = self.directory("notes")
        result = add_base(root)
        self.assertEqual(result["home"], str(self.home))
        self.assertEqual(result["added"], {"name": "notes", "path": root.as_posix(), "root": str(root),
                                           "available": True, "records": 0, "drafts": 0})
        self.assertEqual(self.config(), {"bases": {"notes": {"path": root.as_posix(), "sources": {}}},
                                         "embedding": None})
        self.assertTrue((root / ".knowledge/entries").is_dir())
        self.assertEqual([path.name for path in (root / ".knowledge").iterdir()], ["entries"])
        self.assertEqual((self.home / ".gitignore").read_bytes(), b"index.sqlite*\nlock\n")

    def test_non_slug_basename_needs_a_name(self) -> None:
        root = self.directory("My Notes")
        with self.assertRaisesRegex(KnowledgeError, "--name"):
            add_base(root)
        self.assertFalse(self.home.exists())
        self.assertEqual(add_base(root, name="my-notes")["added"]["name"], "my-notes")
        with self.assertRaisesRegex(KnowledgeError, "must match"):
            add_base(self.directory("other"), name="Other")

    def test_unicode_path_with_name(self) -> None:
        root = self.directory("笔记/数学")
        add_base(root, name="math")
        self.assertEqual(self.config()["bases"]["math"]["path"], root.as_posix())
        self.assertEqual(resolve_base(None, root).name, "math")

    def test_path_under_the_user_home_is_stored_with_a_tilde(self) -> None:
        root = self.user / "Desktop" / "kb"
        root.mkdir(parents=True)
        result = add_base(root)
        self.assertEqual(result["added"]["path"], "~/Desktop/kb")
        self.assertEqual(self.config()["bases"]["kb"]["path"], "~/Desktop/kb")
        self.assertEqual(load_home(self.home).bases["kb"].root, root)

    def test_relative_path_resolves_against_the_cwd(self) -> None:
        root = self.directory("kb")
        previous = Path.cwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, previous)
        self.assertEqual(add_base(Path("kb"))["added"]["root"], str(root))

    def test_duplicates_and_nesting_are_refused(self) -> None:
        outer = self.directory("area/outer")
        inner = self.directory("area/outer/inner")
        add_base(outer)
        before = self.config()
        refusals = (
            (lambda: add_base(self.directory("elsewhere"), name="outer"), "already registered"),
            (lambda: add_base(outer, name="again"), "already registered as base outer"),
            (lambda: add_base(inner), "inside the root of base outer"),
            (lambda: add_base(self.tmp / "area"), "contains the root of base outer"),
            (lambda: add_base(self.tmp / "missing"), "not an existing directory"),
        )
        for action, message in refusals:
            with self.subTest(message=message), self.assertRaisesRegex(KnowledgeError, message):
                action()
        self.assertEqual(self.config(), before)

    @SYMLINKS
    def test_equal_root_through_a_symlink_is_refused(self) -> None:
        root = self.directory("kb")
        add_base(root)
        alias = self.tmp / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(KnowledgeError, "already registered as base kb"):
            add_base(alias, name="alias")

    def test_home_inside_the_root_is_refused(self) -> None:
        root = self.directory("kb")
        for home in (root / "home", root / ".knowledge"):
            with self.subTest(home=home), patch.dict(os.environ, {"KGDISTILLER_HOME": str(home)}):
                with self.assertRaisesRegex(KnowledgeError, "must not lie inside the base root"):
                    add_base(root)
                self.assertFalse(home.exists())

    @SYMLINKS
    def test_symlinked_knowledge_directory_is_refused(self) -> None:
        root = self.directory("kb")
        (root / ".knowledge").symlink_to(self.directory("elsewhere"), target_is_directory=True)
        with self.assertRaisesRegex(KnowledgeError, "must not be a symlink"):
            add_base(root)
        self.assertFalse(self.home.exists())


class BaseRemoveAndListTest(HomeTestCase):
    def test_remove_keeps_files(self) -> None:
        root = self.directory("kb")
        add_base(root)
        entry = root / ".knowledge/entries/x.md"
        entry.write_text("kept\n", encoding="utf-8")
        result = remove_base("kb")
        self.assertEqual(result["removed"], {"name": "kb", "path": root.as_posix(), "root": str(root)})
        self.assertEqual(self.config(), {"bases": {}, "embedding": None})
        self.assertEqual(entry.read_text(encoding="utf-8"), "kept\n")
        with self.assertRaisesRegex(KnowledgeError, r"unknown base 'kb'; registered bases: \(none\)"):
            remove_base("kb")

    def test_list_reports_availability_records_and_drafts(self) -> None:
        first = self.directory("first")
        second = self.directory("second")
        add_base(first)
        add_base(second)
        for name in ("a", "b"):
            (first / ".knowledge/entries" / f"{name}.md").write_text("x\n", encoding="utf-8")
        (first / ".knowledge/entries/notes.txt").write_text("x\n", encoding="utf-8")
        (first / ".knowledge/drafts").mkdir()
        (first / ".knowledge/drafts/c.md").write_text("x\n", encoding="utf-8")
        shutil.rmtree(second)
        result = list_bases()
        self.assertEqual(result["home"], str(self.home))
        self.assertEqual(
            [(item["name"], item["available"], item["records"], item["drafts"]) for item in result["bases"]],
            [("first", True, 2, 1), ("second", False, None, None)],
        )
        with self.assertRaisesRegex(KnowledgeError, "base second is unavailable"):
            resolve_base("second", self.tmp)


class BaseLookupTest(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.directory("kb")
        self.other = self.directory("other")
        add_base(self.root)
        add_base(self.other)

    def test_lookup_uses_realpaths(self) -> None:
        unresolved = Path(tempfile.gettempdir()) / self.tmp.name / "kb" / "notes"
        self.assertEqual(resolve_base(None, unresolved).root, self.root)

    def test_nested_directory_resolves_without_an_upward_walk(self) -> None:
        nested = self.directory("kb/notes/deep/er")
        self.assertEqual(resolve_base(None, nested).name, "kb")
        self.assertEqual(resolve_base(None, self.root / "notes/file.md").name, "kb")
        for outside in (self.tmp, self.home):
            with self.subTest(outside=outside), self.assertRaisesRegex(KnowledgeError, "not inside"):
                resolve_base(None, outside)

    @SYMLINKS
    def test_symlinked_cwd_into_the_root_resolves(self) -> None:
        link = self.tmp / "link"
        link.symlink_to(self.directory("kb/notes"), target_is_directory=True)
        self.assertEqual(resolve_base(None, link).name, "kb")

    def test_cwd_outside_every_root_refuses(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "not inside any registered base root; "
                                    "registered bases: kb, other; pass --base NAME"):
            resolve_base(None, self.tmp)

    def test_named_base_wins_over_the_cwd(self) -> None:
        self.assertEqual(resolve_base("other", self.root).name, "other")
        self.assertEqual(resolve_base("kb", self.tmp).name, "kb")
        with self.assertRaisesRegex(KnowledgeError, "unknown base 'missing'; registered bases: kb, other"):
            resolve_base("missing", self.root)


class GlobTypeTest(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.directory("kb")
        for relative in ("notes/a.md", "notes/sub/b.md", "notes/sub/deep/c.md", "notes/.hidden.md",
                         "notes/.obsidian/d.md", ".knowledge/entries/e.md", "top.md"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("text\n", encoding="utf-8")
        self.directory("kb/notes/folder.md")
        guidance = "Guidance."
        self.types = {name: DocumentType(name, ("concept",), {}, (), guidance) for name in ("t", "u")}

    def base(self, sources: dict[str, str]) -> Base:
        return Base("kb", str(self.root), self.root, sources, self.types)

    def test_star_stays_in_one_segment(self) -> None:
        mapping, conflicts = self.base({"notes/*.md": "t"}).source_types()
        self.assertEqual(mapping, {"notes/a.md": "t"})
        self.assertEqual(conflicts, {})

    def test_double_star_spans_directories_but_never_hidden_ones(self) -> None:
        mapping, _ = self.base({"notes/**/*.md": "t"}).source_types()
        self.assertEqual(sorted(mapping), ["notes/a.md", "notes/sub/b.md", "notes/sub/deep/c.md"])
        mapping, _ = self.base({"**/*": "t"}).source_types()
        self.assertEqual(sorted(mapping), ["notes/a.md", "notes/sub/b.md", "notes/sub/deep/c.md", "top.md"])

    def test_same_type_twice_is_fine(self) -> None:
        base = self.base({"notes/*.md": "t", "notes/a.md": "t"})
        self.assertEqual(base.source_types(), ({"notes/a.md": "t"}, {}))
        self.assertEqual(base.type_of("notes/a.md").name, "t")

    def test_two_types_conflict(self) -> None:
        base = self.base({"notes/**/*.md": "t", "notes/a.md": "u"})
        mapping, conflicts = base.source_types()
        self.assertEqual(conflicts, {"notes/a.md": ["t", "u"]})
        self.assertNotIn("notes/a.md", mapping)
        self.assertEqual(base.type_of(Path("notes/sub/b.md")).name, "t")
        with self.assertRaisesRegex(KnowledgeError, "several types: t, u"):
            base.type_of("notes/a.md")

    def test_unregistered_file_names_the_config(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, r"not a registered source of base kb; .*bases\.kb\.sources"
                                    r" in \$KGDISTILLER_HOME/config\.json"):
            self.base({"notes/*.md": "t"}).type_of("top.md")


LOCK_HOLDER = """
import sys
from kgdistiller.home import lock
with lock():
    print("locked", flush=True)
    sys.stdin.readline()
"""


class LockTest(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.home.mkdir(parents=True)

    def test_second_holder_in_another_process_conflicts(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", LOCK_HOLDER], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(LockConflict, "holds the home lock") as caught, lock():
                pass
            self.assertIsInstance(caught.exception, KnowledgeError)
            with self.assertRaises(LockConflict):
                add_base(self.directory("kb"))
        finally:
            child.communicate("\n", timeout=30)
        self.assertEqual(child.returncode, 0)
        with lock() as path:
            self.assertEqual(path, self.home / "lock")

    def test_first_base_add_creates_the_home_files_only_under_the_lock(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", LOCK_HOLDER], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaises(LockConflict):
                add_base(self.directory("kb"))
            self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["lock"])
        finally:
            child.communicate("\n", timeout=30)
        self.assertEqual(child.returncode, 0)
        add_base(self.directory("kb"))
        self.assertEqual(sorted(self.config()["bases"]), ["kb"])

    def test_lock_file_is_never_truncated(self) -> None:
        with lock():
            pass
        self.assertEqual((self.home / "lock").read_bytes(), b"\0")
        (self.home / "lock").write_bytes(b"left alone")
        with lock():
            pass
        self.assertEqual((self.home / "lock").read_bytes(), b"left alone")


class BaseCommandTest(HomeTestCase):
    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", *arguments]), redirect_stdout(stdout), \
                redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_add_list_rm(self) -> None:
        root = self.directory("kb")
        status, out, err = self.run_main("base", "list")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("kgd base add", err)
        status, out, err = self.run_main("base", "add", str(root), "--name", "kb")
        self.assertEqual(status, 0, err)
        self.assertEqual(json.loads(out)["added"]["name"], "kb")
        status, out, err = self.run_main("base", "list")
        self.assertEqual(status, 0, err)
        self.assertEqual(json.loads(out)["bases"][0]["root"], str(root))
        status, out, err = self.run_main("base", "add", str(root), "--name", "again")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("already registered", err)
        status, out, err = self.run_main("base", "rm", "kb")
        self.assertEqual(status, 0, err)
        self.assertEqual(json.loads(out)["removed"]["name"], "kb")
        self.assertEqual(json.loads(out)["dangling"], [])
        status, out, err = self.run_main("base", "rm", "kb")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("unknown base", err)

    def test_list_adds_index_counts_and_lag(self) -> None:
        kb = self.registered_pair()
        status, out, err = self.run_main("base", "list")
        self.assertEqual(status, 0, err)
        listed = {item["name"]: item for item in json.loads(out)["bases"]}
        self.assertEqual(
            {"name", "path", "root", "available", "records", "drafts", "indexed", "lag"}, set(listed["kb"])
        )
        self.assertEqual((listed["kb"]["records"], listed["kb"]["drafts"], listed["kb"]["indexed"]), (1, 1, 0))
        self.assertEqual(listed["kb"]["lag"], {"changed_files": 1, "unavailable_bases": [], "unembedded": 0,
                                               "embedding_changed": False})
        status, _, err = self.run_main("index")
        self.assertEqual(status, 0, err)
        listed = {item["name"]: item for item in json.loads(self.run_main("base", "list")[1])["bases"]}
        self.assertEqual((listed["kb"]["indexed"], listed["kb"]["lag"]["changed_files"]), (1, 0))
        self.assertEqual((listed["other"]["indexed"], listed["other"]["records"]), (1, 1))
        shutil.rmtree(kb)
        listed = {item["name"]: item for item in json.loads(self.run_main("base", "list")[1])["bases"]}
        self.assertEqual((listed["kb"]["available"], listed["kb"]["records"], listed["kb"]["lag"]["unavailable_bases"]),
                         (False, None, ["kb"]))

    def test_rm_lists_the_foreign_links_left_dangling(self) -> None:
        self.registered_pair()
        status, out, err = self.run_main("base", "rm", "kb")
        self.assertEqual(status, 0, err)
        self.assertEqual(json.loads(out)["dangling"], [{
            "path": str(self.tmp / "other/.knowledge/entries/uses-measure.md"),
            "role": "requires",
            "value": "[[kb:measure]]",
        }])
        self.assertTrue((self.tmp / "kb/.knowledge/entries/measure.md").is_file())

    def registered_pair(self) -> Path:
        """Bases kb and other; other's record requires kb's record through a foreign link."""
        roots = {name: self.directory(name) for name in ("kb", "other")}
        self.write_type("notes-type")
        for name, root in roots.items():
            add_base(root)
            (root / "notes").mkdir()
            (root / "notes/a.md").write_text("Title\nA measure is additive.\n", encoding="utf-8")
        payload = self.config()
        for name in roots:
            payload["bases"][name]["sources"] = {"notes/*.md": "notes-type"}
        self.write_config(payload)
        record = "---\nlabel: {label}\nkind: definition\nsource: notes/a.md\nlines: 2\n{extra}---\nProse.\n\n## Evidence\n\n> A measure\n"
        (roots["kb"] / ".knowledge/entries/measure.md").write_text(record.format(label="Measure", extra=""), encoding="utf-8")
        (roots["kb"] / ".knowledge/drafts").mkdir()
        (roots["kb"] / ".knowledge/drafts/draft.md").write_text(record.format(label="Draft", extra=""), encoding="utf-8")
        (roots["other"] / ".knowledge/entries/uses-measure.md").write_text(
            record.format(label="Uses measure", extra='requires: ["[[kb:measure]]", plain term]\n'), encoding="utf-8")
        return roots["kb"]


if __name__ == "__main__":
    unittest.main()
