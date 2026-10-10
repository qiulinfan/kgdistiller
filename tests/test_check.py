"""``check``: home, record and draft rules, evidence freshness and ``--fix-lines``."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import records
from kgdistiller.home import LockConflict, lock
from kgdistiller.records import check, fix_lines
from tests.knowledge_fixture import make_record_home, render_record

SOURCE = "notes/a.txt"
LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "It extends a measurable space.",
    "A subspace is closed under addition.",
    "The sum of two subspaces is a subspace.",
    "Unique sentence about dimension.",
]


def node(label: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {SOURCE}\nlines: {lines}\n{extra}".rstrip("\n")


class CheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n")
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n", base="notes")
        self.subspace = self.kb.write_record(
            "subspace", node("Subspace", "4", extra="aliases: [linear subspace]"),
            "A subspace is closed under addition.", ["A subspace is closed under addition."],
        )
        self.measure_space = self.kb.write_record(
            "measure-space", node("Measure space", "2-3"), "A triple.", ["A measure space is a triple (X, M, mu)."],
        )

    def run_check(self, *bases: str) -> dict[str, Any]:
        return check(None, list(bases) or None)

    def errors(self, *bases: str) -> list[tuple[str, str, str]]:
        return [(Path(item["path"]).name, item["rule"], item["message"]) for item in self.run_check(*bases)["errors"]]

    def assert_error(self, name: str, rule: str, fragment: str) -> None:
        found = self.errors()
        self.assertTrue(
            any(item[0] == name and item[1] == rule and fragment in item[2] for item in found),
            f"no {rule} error for {name} containing {fragment!r}: {found}",
        )

    def relation(self, identifier: str, frontmatter_extra: str, *, folder: str = "entries", base: str = "kb", kind: str = "implies") -> Path:
        return self.kb.write_record(
            identifier, node(identifier.title(), "5", kind=kind, extra=frontmatter_extra), "Sum.",
            ["The sum of two subspaces is a subspace."], folder=folder, base=base,
        )


class HomeRulesTest(CheckTestCase):
    def test_clean_bases(self) -> None:
        self.assertEqual({"errors": [], "stale": [], "moved": []}, self.run_check())

    def test_missing_home_is_one_config_error(self) -> None:
        (self.kb.home / "config.json").unlink()
        report = self.run_check()
        self.assertEqual(["config"], [item["rule"] for item in report["errors"]])
        self.assertEqual(str(self.kb.home / "config.json"), report["errors"][0]["path"])

    def test_invalid_config_json(self) -> None:
        (self.kb.home / "config.json").write_text("{bad", encoding="utf-8")
        self.assertEqual("config", self.run_check()["errors"][0]["rule"])

    def test_invalid_type_files_are_type_errors(self) -> None:
        bad = self.kb.home / "types" / "broken.md"
        bad.write_text("---\nnode_kinds: []\n---\nGuidance.\n", encoding="utf-8")
        report = self.run_check()
        self.assertEqual([{"path": str(bad), "rule": "type", "message": report["errors"][0]["message"]}], report["errors"])

    def test_config_is_checked_while_a_type_file_is_broken(self) -> None:
        bad = self.kb.home / "types" / "broken.md"
        bad.write_text("---\nnode_kinds: []\n---\nGuidance.\n", encoding="utf-8")
        config = self.kb.home / "config.json"
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload["extra"] = True
        config.write_text(json.dumps(payload), encoding="utf-8")
        report = self.run_check()
        self.assertEqual([(str(bad), "type"), (str(config), "config")],
                         [(item["path"], item["rule"]) for item in report["errors"]])

    def test_a_glob_naming_an_unknown_type_is_a_type_error(self) -> None:
        self.kb.sources["kb"]["notes/*.typ"] = "missing"
        self.kb.write_config()
        report = self.run_check()
        self.assertEqual([(str(self.kb.home / "config.json"), "type")],
                         [(item["path"], item["rule"]) for item in report["errors"]])
        self.assertIn("names unknown type 'missing'", report["errors"][0]["message"])

    def test_unknown_and_unavailable_bases_are_config_errors(self) -> None:
        self.assertIn("unknown base 'nope'", self.errors("nope")[0][2])
        self.kb.roots["gone"] = self.kb.home.parent / "gone"
        self.kb.sources["gone"] = {}
        self.kb.write_config()
        self.assertEqual([("config.json", "config")], [item[:2] for item in self.errors()])

    def test_a_file_matched_by_two_types_is_a_source_type_error(self) -> None:
        self.kb.types["other"] = {"node_kinds": ["definition"], "guidance": "Other."}
        self.kb.sources["kb"]["notes/a.*"] = "other"
        self.kb.write_config()
        found = self.errors("kb")
        self.assertIn(("a.txt", "source-type"), [item[:2] for item in found])
        self.assertIn(("subspace.md", "source"), [item[:2] for item in found])

    def test_symlinked_knowledge_tree_is_refused(self) -> None:
        root = self.kb.roots["notes"]
        target = self.kb.home.parent / "elsewhere"
        (root / ".knowledge").rename(target)
        (root / ".knowledge").symlink_to(target, target_is_directory=True)
        self.assertIn(("config", "the knowledge tree must not be a symlink"), [item[1:] for item in self.errors()])

    def test_symlinked_record_files_and_folders_are_refused(self) -> None:
        outside = self.kb.home.parent / "outside.md"
        outside.write_text(render_record(node("Outside", "6"), "Text.", ["Unique sentence about dimension."]), encoding="utf-8")
        (self.kb.root / ".knowledge/entries/linked.md").symlink_to(outside)
        drafts = self.kb.home.parent / "drafts-elsewhere"
        drafts.mkdir()
        (self.kb.roots["notes"] / ".knowledge/drafts").symlink_to(drafts, target_is_directory=True)
        self.assert_error("linked.md", "frontmatter", "must be a regular file, not a symlink")
        self.assert_error("drafts", "config", ".knowledge/drafts must not be a symlink")


class RecordRulesTest(CheckTestCase):
    def test_errors_carry_absolute_paths(self) -> None:
        path = self.kb.write_record("bad", node("Bad", "1", kind="unknown-kind"), "Text.", ["Title line"])
        report = self.run_check()
        self.assertEqual(str(path), report["errors"][0]["path"])
        self.assertTrue(Path(report["errors"][0]["path"]).is_absolute())

    def test_id_rules(self) -> None:
        self.kb.write_record("Upper", node("Upper", "1"), "Text.", ["Title line"])
        self.assert_error("Upper.md", "id", "not a valid id")

    def test_frontmatter_rules(self) -> None:
        self.kb.write_record("broken", "label: Broken\nkind: definition", "Text.", ["Title line"])
        self.relation("unknown-role", 'premise: ["[[subspace]]"]\nwitness: ["[[subspace]]"]')
        self.assert_error("broken.md", "frontmatter", "needs a non-empty source")
        self.assert_error("unknown-role.md", "frontmatter", "witness is not a role of implies")

    def test_source_rules(self) -> None:
        self.kb.write_record("unregistered", node("U", "1").replace(SOURCE, "other/a.md"), "Text.", ["Title line"])
        self.kb.write_record("escape", node("E", "1").replace(SOURCE, "../a.txt"), "Text.", ["Title line"])
        self.kb.write_record("past-end", node("P", "6-9"), "Text.", ["Unique sentence about dimension."])
        binary = self.kb.root / "notes/binary.txt"
        binary.write_bytes(b"\xff\xfe broken\n")
        self.kb.write_record("binary", node("B", "1").replace(SOURCE, "notes/binary.txt"), "Text.", ["broken"])
        self.assert_error("unregistered.md", "source", "add a glob")
        self.assert_error("escape.md", "source", "POSIX path relative to the base root")
        self.assert_error("past-end.md", "source", "exceed the 6 lines")
        self.assert_error("binary.md", "source", "not UTF-8")

    def test_a_source_must_not_resolve_outside_the_base(self) -> None:
        outside = self.kb.home.parent / "outside.txt"
        outside.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        (self.kb.root / "notes/link.txt").symlink_to(outside)
        (self.kb.root / "notes/inside.txt").symlink_to(self.kb.root / SOURCE)
        self.kb.write_record("linked", node("L", "1").replace(SOURCE, "notes/link.txt"), "Text.", ["Title line"])
        self.kb.write_record("inside", node("I", "1").replace(SOURCE, "notes/inside.txt"), "Text.", ["Title line"])
        self.assert_error("linked.md", "source", "resolves outside the base root through a symlink")
        self.assertNotIn("inside.md", [item[0] for item in self.errors()])

    def test_kind_rules(self) -> None:
        self.kb.write_record("bad-kind", node("K", "1", kind="lemma"), "Text.", ["Title line"])
        self.kb.write_record("node-roles", node("N", "1", extra='premise: ["[[subspace]]"]'), "Text.", ["Title line"])
        self.relation("empty-relation", "premise: []")
        self.relation("bad-epistemic", 'premise: ["[[subspace]]"]\nepistemic: rumoured')
        self.assert_error("bad-kind.md", "kind", "not a node or relation kind of type math")
        self.assert_error("node-roles.md", "kind", "is a node kind of math")
        self.assert_error("empty-relation.md", "kind", "needs at least one participant")
        self.assert_error("bad-epistemic.md", "kind", "epistemic 'rumoured'")

    def test_link_rules(self) -> None:
        self.relation("dangling", 'premise: ["[[linear-subspace]]"]')
        self.relation("self", 'premise: ["[[self]]"]')
        self.relation("unregistered-base", 'premise: ["[[papers:x]]"]')
        self.relation("foreign-ok", 'premise: ["[[notes:sigma]]"]\nconclusion: ["[[subspace]]", plain term]')
        self.kb.write_record("sigma", node("Sigma", "1"), "Text.", ["Title line"], base="notes")
        self.assert_error("dangling.md", "link", "records named so: kb:subspace")
        self.assert_error("self.md", "link", "links the record itself")
        self.assert_error("unregistered-base.md", "link", "links base papers, which is not registered")
        self.assertNotIn("foreign-ok.md", [item[0] for item in self.errors()])

    def test_quoted_link_errors_are_link_rule(self) -> None:
        self.relation("own-base", 'premise: ["[[kb:subspace]]"]')
        self.relation("unquoted", "premise: [[subspace]]")
        self.assert_error("own-base.md", "link", "prefixes a local link with its own base")
        self.assert_error("unquoted.md", "link", 'quote wikilinks: write "[[subspace]]"')

    def test_body_rule(self) -> None:
        path = self.kb.path("no-evidence")
        path.write_text(f"---\n{node('X', '1')}\n---\nOnly prose.\n", encoding="utf-8")
        self.assert_error("no-evidence.md", "body", "must end with a '## Evidence' section")


class DraftRulesTest(CheckTestCase):
    def test_drafts_may_link_drafts_of_their_base(self) -> None:
        self.relation("first", 'premise: ["[[second]]"]', folder="drafts")
        self.kb.write_record("second", node("Second", "1"), "Text.", ["Title line"], folder="drafts")
        self.assertEqual([], self.errors())

    def test_accepted_records_must_not_link_drafts(self) -> None:
        self.kb.write_record("proposal", node("Proposal", "1"), "Text.", ["Title line"], folder="drafts")
        self.relation("accepted", 'premise: ["[[proposal]]"]')
        self.assert_error("accepted.md", "link", "links the draft [[proposal]]; accept it first")

    def test_foreign_links_resolve_only_to_accepted_records(self) -> None:
        self.kb.write_record("foreign-draft", node("F", "1"), "Text.", ["Title line"], folder="drafts", base="notes")
        self.relation("asks-draft", 'premise: ["[[notes:foreign-draft]]"]', folder="drafts")
        self.assert_error("asks-draft.md", "link", "links a draft of base notes")

    def test_a_draft_id_must_not_exist_in_entries(self) -> None:
        self.kb.write_record("subspace", node("Other", "1"), "Text.", ["Title line"], folder="drafts")
        self.assert_error("subspace.md", "id", "already exists in entries/")

    def test_identical_file_in_entries_is_an_interrupted_accept(self) -> None:
        self.kb.path("subspace", "drafts").parent.mkdir(parents=True, exist_ok=True)
        self.kb.path("subspace", "drafts").write_bytes(self.subspace.read_bytes())
        self.assert_error("subspace.md", "id", "rerun `kgd accept")

    def test_drafts_are_checked_for_freshness(self) -> None:
        draft = self.kb.write_record("stale-draft", node("S", "1"), "Text.", ["Not in the source."], folder="drafts")
        self.assertEqual([str(draft)], self.run_check()["stale"])


class FreshnessTest(CheckTestCase):
    def test_whitespace_is_normalized(self) -> None:
        self.kb.write_record("spaced", node("Spaced", "2-3"), "Text.", ["A  measure space\nis a triple (X, M, mu). It\textends"])
        self.assertEqual({"errors": [], "stale": [], "moved": []}, self.run_check())

    def test_moved_proposes_the_covering_range(self) -> None:
        path = self.kb.write_record(
            "moved", node("Moved", "1"), "Text.", ["is closed under addition.", "The sum of two"]
        )
        self.assertEqual([{"path": str(path), "lines": "4-5"}], self.run_check()["moved"])

    def test_stale_when_missing_or_ambiguous(self) -> None:
        missing = self.kb.write_record("missing", node("Missing", "1"), "Text.", ["Nowhere at all."])
        ambiguous = self.kb.write_record("ambiguous", node("Ambiguous", "6"), "Text.", ["is a"])
        self.assertEqual(sorted([str(missing), str(ambiguous)]), sorted(self.run_check()["stale"]))

    def test_bom_and_crlf_sources_count_lines_consistently(self) -> None:
        source = self.kb.root / "notes/windows.txt"
        source.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(LINES).encode() + b"\r\n")
        self.kb.write_record("windows", node("W", "6").replace(SOURCE, "notes/windows.txt"), "Text.", ["Unique sentence about dimension."])
        self.kb.write_record("bom-first", node("B", "1").replace(SOURCE, "notes/windows.txt"), "Text.", ["Title line"])
        self.assertEqual({"errors": [], "stale": [], "moved": []}, self.run_check())
        self.kb.write_record("beyond", node("X", "7").replace(SOURCE, "notes/windows.txt"), "Text.", ["Title line"])
        self.assert_error("beyond.md", "source", "exceed the 6 lines")


class FixLinesTest(CheckTestCase):
    def test_rewrites_only_the_lines_line(self) -> None:
        text = render_record(node("Moved", "1", extra="aliases: [m]") + "\n# comment", "Text.", ["The sum of two subspaces"])
        path = self.kb.path("moved")
        original = b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode()
        path.write_bytes(original)
        report = fix_lines()
        self.assertEqual([str(path)], report["fixed"])
        self.assertEqual([], report["moved"])
        self.assertEqual([], report["skipped"])
        self.assertEqual(original.replace(b"lines: 1\r\n", b"lines: 5\r\n"), path.read_bytes())
        self.assertEqual({"errors": [], "stale": [], "moved": []}, self.run_check())

    def test_writes_a_range_when_the_quotes_span_lines(self) -> None:
        path = self.kb.write_record("span", node("Span", "1"), "Text.", ["closed under addition. The sum"])
        fix_lines()
        self.assertIn("lines: 4-5\n", path.read_text(encoding="utf-8"))

    def test_a_file_changed_since_the_check_is_skipped(self) -> None:
        path = self.kb.write_record("moved", node("Moved", "1"), "Text.", ["The sum of two subspaces"])
        original_check = records._check

        def check_then_edit(*args: Any) -> Any:
            result = original_check(*args)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return result

        with patch.object(records, "_check", check_then_edit):
            report = fix_lines()
        self.assertEqual([str(path)], report["skipped"])
        self.assertEqual([], report["fixed"])
        self.assertEqual([{"path": str(path), "lines": "5-5"}], report["moved"])
        self.assertIn("lines: 1\n", path.read_text(encoding="utf-8"))

    def test_holds_the_home_lock(self) -> None:
        self.kb.write_record("moved", node("Moved", "1"), "Text.", ["The sum of two subspaces"])
        with lock(), self.assertRaises(LockConflict):
            fix_lines()
        seen: list[bool] = []
        original_check = records._check

        def check_inside_lock(*args: Any) -> Any:
            with self.assertRaises(LockConflict), lock():
                pass
            seen.append(True)
            return original_check(*args)

        with patch.object(records, "_check", check_inside_lock):
            fix_lines()
        self.assertEqual([True], seen)

    def test_reports_like_check_without_a_home(self) -> None:
        (self.kb.home / "config.json").unlink()
        report = fix_lines()
        self.assertEqual(["config"], [item["rule"] for item in report["errors"]])
        self.assertEqual(([], []), (report["fixed"], report["skipped"]))

    def test_json_serializable(self) -> None:
        self.kb.write_record("moved", node("Moved", "1"), "Text.", ["The sum of two subspaces"])
        json.dumps(fix_lines())


if __name__ == "__main__":
    unittest.main()
