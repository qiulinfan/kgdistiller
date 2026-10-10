from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from kgdistiller import knowledge_store
from kgdistiller.knowledge_store import (
    DELTA_SCHEMA,
    StoreError,
    apply_delta,
    fix_lines,
    identity_index,
    load_state,
    validate,
    write_edges,
    write_entry,
)
from tests.knowledge_fixture import make_fixture

SOURCE = "notes/measure.txt"
TEXT = (
    "Chapter one\n"
    "A measure space is a triple\n"
    "with countable additivity.\n"
    "\n"
    "A sigma-algebra is closed under complements.\n"
)
MATH = {"math": {"node_kinds": ["definition", "theorem"], "extraction_guidance": "Named statements."}}


def delta(**lists):
    value = {"schema": DELTA_SCHEMA, "create_entries": [], "update_entries": [],
             "remove_entries": [], "add_edges": [], "remove_edges": []}
    value.update(lists)
    return value


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(self)
        self.root = self.fixture.root
        self.fixture.write_source(SOURCE, TEXT)
        self.measure = self.fixture.add_entry("measure-space", "Measure space", SOURCE, 2, 3,
                                              aliases=["测度空间"])
        self.sigma = self.fixture.add_entry("sigma-algebra", "Sigma-algebra", SOURCE, 5)
        self.fixture.add_edge("sigma-algebra", "prerequisite-for", "measure-space")

    def check(self):
        return validate(load_state(self.root), self.root, self.fixture.registry)

    def codes(self):
        return [error["code"] for error in self.check()["errors"]]


class LoadAndValidateTest(StoreTestCase):
    def test_current_store_is_valid(self) -> None:
        state = load_state(self.root)
        self.assertEqual(sorted(state.entries), ["measure-space", "sigma-algebra"])
        self.assertEqual(list(state.edges), [("sigma-algebra", "prerequisite-for", "measure-space")])
        self.assertEqual(self.check(), {"errors": [], "stale": []})
        self.assertEqual(identity_index(state)["测度空间"], "measure-space")

    def test_entry_file_name_must_match_its_id(self) -> None:
        entries = self.root / ".knowledge/entries"
        os.replace(entries / "measure-space.md", entries / "other.md")
        with self.assertRaisesRegex(StoreError, "file name must be measure-space.md"):
            load_state(self.root)

    def test_symlinked_store_paths_fail_closed(self) -> None:
        entries = self.root / ".knowledge/entries"
        real = self.root / "real-entries"
        os.replace(entries, real)
        entries.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(StoreError, "must not be a symlink"):
            load_state(self.root)
        entries.unlink()
        os.replace(real, entries)
        outside = self.root / "outside.md"
        os.replace(entries / "sigma-algebra.md", outside)
        (entries / "sigma-algebra.md").symlink_to(outside)
        with self.assertRaisesRegex(StoreError, "not an ordinary file"):
            load_state(self.root)
        errors: list = []
        self.assertEqual(sorted(load_state(self.root, errors).entries), ["measure-space"])
        self.assertEqual([error["code"] for error in errors], ["invalid-entry"])
        (entries / "sigma-algebra.md").unlink()
        os.replace(outside, entries / "sigma-algebra.md")
        edges = self.root / ".knowledge/edges.jsonl"
        os.replace(edges, self.root / "edges.jsonl")
        edges.symlink_to(self.root / "edges.jsonl")
        with self.assertRaisesRegex(StoreError, "must not be a symlink"):
            load_state(self.root)

    def test_cited_sources_must_not_traverse_a_symlink(self) -> None:
        (self.root / "notes/linked").symlink_to(self.root / "notes", target_is_directory=True)
        self.fixture.write_entry({**self.sigma, "source": "notes/linked/measure.txt"})
        report = self.check()
        self.assertEqual([error["code"] for error in report["errors"]], ["missing-source"])
        self.assertIn("symlink", report["errors"][0]["message"])

    def test_collecting_load_reports_every_malformed_file(self) -> None:
        entries = self.root / ".knowledge/entries"
        text = (entries / "measure-space.md").read_text(encoding="utf-8")
        (entries / "measure-space.md").write_text(text.replace("understanding: unknown", "understanding: maybe"),
                                                  encoding="utf-8")
        os.replace(entries / "sigma-algebra.md", entries / "renamed.md")
        edges = self.root / ".knowledge/edges.jsonl"
        edges.write_text(edges.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
        with self.assertRaises(StoreError):
            load_state(self.root)
        errors: list = []
        state = load_state(self.root, errors)
        self.assertEqual(state.entries, {})
        self.assertEqual(len(state.edges), 1)
        self.assertEqual([error["code"] for error in errors], ["invalid-entry", "invalid-entry", "invalid-edge"])
        self.assertIn("renamed.md: file name must be sigma-algebra.md", errors[1]["message"])
        self.assertIn("edges.jsonl:2", errors[2]["message"])

    def test_edges_need_the_exact_field_set_and_a_known_relation(self) -> None:
        path = self.root / ".knowledge/edges.jsonl"
        edge = self.fixture.edge("sigma-algebra", "implies", "measure-space")
        for broken in ({**edge, "unexpected": "x"}, {**edge, "relation": "unknown-relation"}, {**edge, "evidence": ""}):
            with self.subTest(broken=broken):
                path.write_text(json.dumps(broken) + "\n", encoding="utf-8")
                with self.assertRaises(StoreError):
                    load_state(self.root)

    def test_label_and_alias_collisions(self) -> None:
        self.fixture.add_entry("other", "Other", SOURCE, 1, aliases=["MEASURE SPACE"])
        self.assertEqual(self.codes(), ["identity-collision"])
        with self.assertRaises(StoreError):
            identity_index(load_state(self.root))

    def test_kind_follows_the_registered_document_type(self) -> None:
        self.assertEqual(self.codes(), [])
        self.fixture.document_types = MATH
        self.fixture.sources[0]["document_type"] = "math"
        self.fixture.write_registry()
        self.assertEqual(self.codes(), ["kind-not-allowed", "kind-not-allowed"])
        self.fixture.write_entry({**self.measure, "kind": "definition"})
        self.fixture.write_entry({**self.sigma, "kind": "theorem"})
        self.assertEqual(self.codes(), [])

    def test_sources_must_exist_and_be_admitted_by_exactly_one_source(self) -> None:
        self.fixture.write_entry({**self.sigma, "source": "notes/missing.txt"})
        self.assertEqual(self.codes(), ["missing-source"])
        self.fixture.write_source("elsewhere/a.txt", TEXT)
        self.fixture.write_entry({**self.sigma, "source": "elsewhere/a.txt"})
        self.assertEqual(self.codes(), ["source-not-registered"])
        self.fixture.sources.append({"id": "local:all", "root": "notes", "files": ["*.txt"]})
        self.fixture.write_registry()
        self.fixture.write_entry(self.sigma)
        self.assertEqual(self.codes(), ["source-not-registered", "source-not-registered"])

    def test_any_extension_is_plain_text(self) -> None:
        for name in ("a.md", "b.typ", "c.tex", "d"):
            self.fixture.write_source(f"notes/{name}", "#kn[X] --[[X]]-- \\kn{X}\n")
            self.fixture.add_entry(f"x-{name[0]}", f"X {name}", f"notes/{name}", 1)
        self.assertEqual(self.check(), {"errors": [], "stale": []})

    def test_non_utf8_source_is_reported(self) -> None:
        (self.root / SOURCE).write_bytes(b"\xff\xfe broken")
        self.assertEqual(set(self.codes()), {"missing-source"})

    def test_out_of_bounds_lines(self) -> None:
        self.fixture.write_source(SOURCE, "A measure space is a triple\n")
        report = self.check()
        self.assertIn("line-range", [error["code"] for error in report["errors"]])

    def test_dangling_edges_and_prerequisite_cycles(self) -> None:
        self.fixture.add_edge("measure-space", "prerequisite-for", "sigma-algebra")
        self.assertEqual(self.codes(), ["cycle"])
        self.fixture.edges.pop()
        self.fixture.add_edge("measure-space", "implies", "ghost")
        self.assertEqual(self.codes(), ["dangling-edge"])

    def test_self_edges_are_allowed_except_for_prerequisites(self) -> None:
        self.fixture.add_edge("measure-space", "contrasts-with", "measure-space")
        self.assertEqual(self.codes(), [])
        self.fixture.add_edge("measure-space", "prerequisite-for", "measure-space")
        self.assertEqual(self.codes(), ["cycle"])


class EvidenceStalenessTest(StoreTestCase):
    def test_whitespace_reflow_keeps_an_entry_current(self) -> None:
        self.fixture.write_source(SOURCE, TEXT.replace("a triple\nwith", "a   triple with\n "))
        self.assertEqual(self.check()["stale"], [])

    def test_moved_evidence_is_reported_and_fixed(self) -> None:
        self.fixture.write_source(SOURCE, "New preface\n\n" + TEXT)
        stale = self.check()["stale"]
        self.assertEqual([(item["entry"], item["status"], item["new_line_start"], item["new_line_end"])
                          for item in stale],
                         [("measure-space", "moved", 4, 5), ("sigma-algebra", "moved", 7, 7)])
        state = load_state(self.root)
        before = (self.root / ".knowledge/entries/measure-space.md").read_text(encoding="utf-8")
        fixed = fix_lines(state, self.root, self.fixture.registry)
        self.assertEqual(len(fixed), 2)
        self.assertEqual(self.check(), {"errors": [], "stale": []})
        after = (self.root / ".knowledge/entries/measure-space.md").read_text(encoding="utf-8")
        self.assertEqual(after, before.replace("line_start: 2\nline_end: 3", "line_start: 4\nline_end: 5"))

    def test_stale_and_ambiguous_evidence_are_reported_not_fixed(self) -> None:
        self.fixture.write_source(SOURCE, TEXT.replace("countable", "finite") + "A sigma-algebra is closed under complements.\n")
        stale = {item["entry"]: item for item in self.check()["stale"]}
        self.assertEqual(stale["measure-space"]["status"], "stale")
        self.assertNotIn("sigma-algebra", stale)
        self.fixture.write_source(SOURCE, "x\n" + TEXT + "A sigma-algebra is closed under complements.\n")
        stale = {item["entry"]: item for item in self.check()["stale"]}
        self.assertEqual(stale["sigma-algebra"]["status"], "ambiguous")
        self.assertEqual(stale["sigma-algebra"]["candidates"],
                         [{"line_start": 6, "line_end": 6}, {"line_start": 7, "line_end": 7}])
        fixed = fix_lines(load_state(self.root), self.root, self.fixture.registry)
        self.assertEqual([item["entry"] for item in fixed], ["measure-space"])
        self.assertEqual(load_state(self.root).entries["sigma-algebra"]["line_start"], 5)

    def test_staleness_is_not_an_error(self) -> None:
        self.fixture.write_source(SOURCE, "rewritten\n" * 6)
        report = self.check()
        self.assertEqual(report["errors"], [])
        self.assertEqual({item["status"] for item in report["stale"]}, {"stale"})


class ApplyDeltaTest(StoreTestCase):
    def apply(self, **lists):
        return apply_delta(load_state(self.root), delta(**lists), self.root, self.fixture.registry)

    def test_create_update_remove_and_edges(self) -> None:
        chapter = self.fixture.entry("chapter-one", "Chapter one", SOURCE, 1, kind="note")
        renamed = {**self.measure, "label": "Measure triple", "aliases": ["测度空间", "Measure space"]}
        after, changes = self.apply(
            create_entries=[chapter],
            update_entries=[{"expected_label": "Measure space", "entry": renamed}],
            add_edges=[self.fixture.edge("chapter-one", "implies", "measure-space")],
        )
        self.assertEqual(changes, {
            "entries_created": ["chapter-one"],
            "entries_updated": ["measure-space"],
            "entries_removed": [],
            "aliases_changed": ["measure-space"],
            "edges_added": [{"source": "chapter-one", "relation": "implies", "target": "measure-space"}],
            "edges_removed": [],
        })
        self.assertEqual(after.entries["measure-space"]["label"], "Measure triple")
        _, changes = apply_delta(after, delta(
            remove_entries=[{"id": "chapter-one", "expected_label": "Chapter one"}],
            remove_edges=[{"source": "chapter-one", "relation": "implies", "target": "measure-space"}],
        ), self.root, self.fixture.registry)
        self.assertEqual(changes["entries_removed"], ["chapter-one"])
        self.assertEqual(changes["edges_removed"],
                         [{"source": "chapter-one", "relation": "implies", "target": "measure-space"}])
        self.assertEqual(sorted(load_state(self.root).entries), ["measure-space", "sigma-algebra"])

    def assert_code(self, code, **lists):
        with self.assertRaises(StoreError) as caught:
            self.apply(**lists)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_semantic_validation_codes(self) -> None:
        self.assert_code("label-mismatch", update_entries=[{"expected_label": "Old", "entry": self.measure}])
        self.assert_code("missing-entry", update_entries=[{"expected_label": "Ghost",
                                                           "entry": {**self.measure, "id": "ghost"}}])
        self.assert_code("missing-entry", remove_entries=[{"id": "ghost", "expected_label": "Ghost"}])
        self.assert_code("label-mismatch", remove_entries=[{"id": "measure-space", "expected_label": "Other"}])
        self.assert_code("entry-exists", create_entries=[self.measure])
        self.assert_code("identity-collision", create_entries=[
            self.fixture.entry("measure-space-2", "测度空间", SOURCE, 2, 3)])
        self.assert_code("stale-evidence", create_entries=[{**self.fixture.entry("x", "X", SOURCE, 1),
                                                            "evidence": "Different text"}])
        self.assert_code("line-range", create_entries=[{**self.fixture.entry("x", "X", SOURCE, 1), "line_end": 99}])
        self.assert_code("missing-source", create_entries=[{**self.fixture.entry("x", "X", SOURCE, 1),
                                                            "source": "notes/none.txt"}])
        self.fixture.write_source("outside/a.txt", TEXT)
        self.assert_code("source-not-registered", create_entries=[{**self.fixture.entry("x", "X", SOURCE, 1),
                                                                   "source": "outside/a.txt"}])
        self.assert_code("dangling-edge", remove_entries=[{"id": "sigma-algebra", "expected_label": "Sigma-algebra"}])
        self.assert_code("dangling-edge", add_edges=[self.fixture.edge("ghost", "implies", "sigma-algebra")])
        self.assert_code("cycle", add_edges=[self.fixture.edge("measure-space", "prerequisite-for", "sigma-algebra")])
        self.assert_code("missing-edge", remove_edges=[{"source": "a", "relation": "implies", "target": "b"}])
        self.assert_code("invalid-request", create_entries=[{**self.measure, "id": "Bad Id"}])
        self.assert_code("invalid-request", add_edges=[{**self.fixture.edge("a", "contains", "b")}])
        self.assert_code("invalid-request", create_entries=[self.measure],
                         remove_entries=[{"id": "measure-space", "expected_label": "Measure space"}])
        self.fixture.document_types = MATH
        self.fixture.sources[0]["document_type"] = "math"
        self.fixture.write_registry()
        self.assert_code("kind-not-allowed", create_entries=[self.fixture.entry("x", "X", SOURCE, 1, kind="note")])

    def test_apply_delta_never_writes(self) -> None:
        before = sorted((path.name, path.read_bytes()) for path in (self.root / ".knowledge").rglob("*") if path.is_file())
        self.apply(create_entries=[self.fixture.entry("chapter-one", "Chapter one", SOURCE, 1)])
        after = sorted((path.name, path.read_bytes()) for path in (self.root / ".knowledge").rglob("*") if path.is_file())
        self.assertEqual(before, after)


class AtomicWriteTest(StoreTestCase):
    def test_writes_are_atomic_and_canonical(self) -> None:
        state = load_state(self.root)
        path = self.root / ".knowledge/entries/measure-space.md"
        original = path.read_bytes()
        with patch.object(knowledge_store.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            write_entry(self.root, {**self.measure, "summary": "Changed."})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(sorted(item.name for item in path.parent.iterdir()),
                         ["measure-space.md", "sigma-algebra.md"])
        edges = list(state.edges.values()) + [self.fixture.edge("measure-space", "implies", "measure-space")]
        write_edges(self.root, reversed(edges))
        lines = (self.root / ".knowledge/edges.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["source"] for line in lines], ["measure-space", "sigma-algebra"])
        self.assertEqual(lines[0], json.dumps(json.loads(lines[0]), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
