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
        return check(list(bases) or None)

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
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

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

    def test_a_base_naming_an_unknown_type_is_a_type_error(self) -> None:
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

    def test_a_base_of_several_types_needs_each_records_type(self) -> None:
        self.kb.types["other"] = {"node_kinds": {"definition": []}, "guidance": "Other."}
        self.kb.sources["kb"]["other"] = "other"
        self.kb.write_config()
        self.kb.write_record("typed", node("Typed", "4", extra="type: other"), "Text.", ["A subspace is closed under addition."])
        self.kb.write_record("unknown", node("Unknown", "4", extra="type: papers"), "Text.", ["A subspace is closed under addition."])
        found = self.errors("kb")
        several = "base kb has several document types (math, other); declare the record's type: one of them"
        self.assertIn(("subspace.md", "type", several), found)
        self.assertIn(("unknown.md", "type", "type 'papers' is not a document type of base kb (math, other)"), found)
        self.assertNotIn("typed.md", [item[0] for item in found])

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
        (self.kb.root / "other").mkdir()
        (self.kb.root / "other/a.pdf").write_text("Title line\n", encoding="utf-8")
        (self.kb.root / ".hidden").mkdir()
        (self.kb.root / ".hidden/a.md").write_text("Title line\n", encoding="utf-8")
        (self.kb.root / "other/a.md").write_text("Title line\n", encoding="utf-8")
        self.kb.write_record("unregistered", node("U", "1").replace(SOURCE, "other/a.pdf"), "Text.", ["Title line"])
        self.kb.write_record("hidden", node("H", "1").replace(SOURCE, ".hidden/a.md"), "Text.", ["Title line"])
        self.kb.write_record("any-markdown", node("M", "1").replace(SOURCE, "other/a.md"), "Text.", ["Title line"])
        self.kb.write_record("renamed", node("R", "1").replace(SOURCE, "notes/renamed.txt"), "Text.", ["Title line"])
        self.kb.write_record("escape", node("E", "1").replace(SOURCE, "../a.txt"), "Text.", ["Title line"])
        self.kb.write_record("past-end", node("P", "6-9"), "Text.", ["Unique sentence about dimension."])
        binary = self.kb.root / "notes/binary.txt"
        binary.write_bytes(b"\xff\xfe broken\n")
        self.kb.write_record("binary", node("B", "1").replace(SOURCE, "notes/binary.txt"), "Text.", ["broken"])
        self.assert_error("unregistered.md", "source", "is not a source of base kb")
        self.assert_error("renamed.md", "source", "source notes/renamed.txt does not exist in base kb")
        self.assert_error("escape.md", "source", "POSIX path relative to the base root")
        self.assert_error("past-end.md", "source", "exceed the 6 lines")
        self.assert_error("binary.md", "source", "not UTF-8")
        self.assert_error("hidden.md", "source", "is not a source of base kb")
        self.assertNotIn("any-markdown.md", [item[0] for item in self.errors()])

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
        self.relation("one-participant", 'premise: ["[[subspace]]"]\nconclusion: []')
        self.relation("two-in-one-role", 'premise: ["[[subspace]]", "[[measure-space]]"]')
        self.relation("bad-epistemic", 'premise: ["[[subspace]]"]\nconclusion: ["[[measure-space]]"]\nepistemic: rumoured')
        self.assert_error("bad-kind.md", "kind", "not a node or relation kind of type math")
        self.assert_error("node-roles.md", "frontmatter", "premise is not a role of definition; it declares no roles")
        two = "the relation implies binds at least two values; a result the source states is a node whose roles link what it is about"
        self.assert_error("empty-relation.md", "kind", two)
        self.assert_error("one-participant.md", "kind", two)
        self.assert_error("bad-epistemic.md", "kind", "epistemic 'rumoured'")
        found = self.errors()
        self.assertNotIn("two-in-one-role.md", [item[0] for item in found])
        self.assertNotIn(("bad-epistemic.md", "kind", two), found)

    def test_a_node_kind_may_declare_roles(self) -> None:
        math = self.kb.types["math"]
        self.kb.types["math"] = {**math, "node_kinds": {"definition": [], "theorem": ["about"], "concept": []}}
        self.kb.write_config()
        self.kb.write_record("about-one", node("A", "1", kind="theorem", extra='about: ["[[subspace]]"]'), "Text.", ["Title line"])
        self.kb.write_record(
            "about-two", node("B", "1", kind="theorem", extra='about: ["[[subspace]]", "[[measure-space]]"]'),
            "Text.", ["Title line"],
        )
        self.kb.write_record("about-none", node("C", "1", kind="theorem"), "Text.", ["Title line"])
        self.kb.write_record(
            "undeclared", node("D", "1", kind="theorem", extra='about: ["[[subspace]]"]\npremise: ["[[subspace]]"]'),
            "Text.", ["Title line"],
        )
        self.kb.write_record("about-dangling", node("E", "1", kind="theorem", extra='about: ["[[nowhere]]"]'), "Text.", ["Title line"])
        self.assertEqual(
            [("about-dangling.md", "link"), ("about-none.md", "kind"), ("undeclared.md", "frontmatter")],
            sorted(item[:2] for item in self.errors()),
        )
        self.assert_error("about-none.md", "kind", "a theorem links what it is about in about; capture a concept no record holds first")
        self.assert_error("about-dangling.md", "link", "about[0]: dangling link [[nowhere]]")
        self.assert_error("undeclared.md", "frontmatter", "premise is not a role of theorem; its roles are about")

    def test_class_follows_the_kind_not_the_frontmatter(self) -> None:
        math = self.kb.types["math"]
        self.kb.types["math"] = {**math, "relation_kinds": {**math["relation_kinds"], "characterizes": ["about"]}}
        self.kb.write_config()
        self.kb.write_record(
            "characterization", node("C", "1", kind="characterizes", extra='about: ["[[subspace]]"]'), "Text.", ["Title line"],
        )
        self.assert_error("characterization.md", "kind",
                          "the relation characterizes binds at least two values; a result the source states is a node whose roles link what it is about")
        relations = dict(math["relation_kinds"])
        self.kb.types["math"] = {
            **math, "node_kinds": {"definition": [], "theorem": [], "concept": [], "characterizes": ["about"]},
            "relation_kinds": relations,
        }
        self.kb.write_config()
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

    def test_link_rules(self) -> None:
        self.relation("dangling", 'premise: ["[[linear-subspace]]"]')
        self.relation("self", 'premise: ["[[self]]"]')
        self.relation("unregistered-base", 'premise: ["[[papers:x]]"]')
        self.relation("foreign-ok", 'premise: ["[[notes:sigma]]"]\nconclusion: ["[[subspace]]", "[[measure-space]]"]')
        self.kb.write_record("sigma", node("Sigma", "1"), "Text.", ["Title line"], base="notes")
        self.assert_error("dangling.md", "link", "records named so: kb:subspace")
        self.assert_error("self.md", "link", "links the record itself")
        self.assert_error("unregistered-base.md", "link", "links base papers, which is not registered")
        self.assertNotIn("foreign-ok.md", [item[0] for item in self.errors()])

    def test_a_plain_value_is_not_a_link(self) -> None:
        self.relation("plain-term", 'premise: ["[[subspace]]"]\nconclusion: [plain term]')
        self.kb.write_record("plain-requires", node("P", "1", extra="requires: [dimension]"), "Text.", ["Title line"])
        self.assert_error("plain-term.md", "link", "'plain term' is not a link")
        self.assert_error("plain-requires.md", "link", "'dimension' is not a link")

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
        self.relation("first", 'premise: ["[[second]]"]\nconclusion: ["[[subspace]]"]', folder="drafts")
        self.kb.write_record("second", node("Second", "1"), "Text.", ["Title line"], folder="drafts")
        self.assertEqual([], self.errors())

    def test_accepted_records_must_not_link_drafts(self) -> None:
        self.kb.write_record("proposal", node("Proposal", "1"), "Text.", ["Title line"], folder="drafts")
        self.relation("accepted", 'premise: ["[[proposal]]"]')
        self.assert_error("accepted.md", "link", "links the draft [[proposal]]; accept it first")

    def test_a_draft_links_drafts_of_every_base_and_an_accepted_record_none(self) -> None:
        self.kb.write_record("other-draft", node("F", "1"), "Text.", ["Title line"], folder="drafts", base="notes")
        both = 'premise: ["[[notes:other-draft]]"]\nconclusion: ["[[subspace]]"]'
        self.relation("asks-draft", both, folder="drafts")
        self.relation("accepted", both)
        self.assertEqual([("accepted.md", "link")], sorted(item[:2] for item in self.errors()))
        self.assert_error("accepted.md", "link", "links the draft [[notes:other-draft]]; accept it first")

    def test_a_draft_with_an_accepted_id_is_an_update_checked_like_any_draft(self) -> None:
        self.kb.write_record(
            "subspace", node("Subspace", "4", extra="aliases: [linear subspace]"),
            "A subspace is closed under addition and scaling.", ["A subspace is closed under addition."],
            folder="drafts",
        )
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())
        self.kb.write_record("subspace", node("Subspace", "1", kind="lemma"), "Text.", ["Nowhere."], folder="drafts")
        report = self.run_check()
        self.assertEqual([str(self.kb.path("subspace", "drafts"))], report["stale"])
        self.assert_error("subspace.md", "kind", "kind 'lemma'")

    def test_a_record_drawn_into_a_draft_checks_clean_before_any_edit(self) -> None:
        draft = self.kb.path("subspace", "drafts")
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_bytes(self.subspace.read_bytes())
        self.assertEqual([], self.run_check()["errors"])

    def test_drafts_are_checked_for_freshness(self) -> None:
        draft = self.kb.write_record("stale-draft", node("S", "1"), "Text.", ["Not in the source."], folder="drafts")
        self.assertEqual([str(draft)], self.run_check()["stale"])


class LabelRuleTest(CheckTestCase):
    def test_a_label_names_one_concept_across_every_base(self) -> None:
        self.kb.write_record("subspace-2", node("subspace", "5"), "The sum.", ["The sum of two subspaces is a subspace."],
                             base="notes")
        self.assert_error("subspace-2.md", "label", "label 'subspace' is already the label of kb:subspace")
        self.assert_error("subspace.md", "label", "is already the label of notes:subspace-2")
        self.assert_error("subspace-2.md", "label", "as in 'subspace (a)'")

    def test_the_source_name_tells_a_concept_of_another_meaning_apart(self) -> None:
        self.kb.write_record("subspace-a", node("Subspace (a)", "5"), "The sum.", ["The sum of two subspaces is a subspace."],
                             base="notes")
        self.assertEqual([], self.errors())

    def test_drafts_are_compared_with_records_and_drafts_but_a_replacement_with_its_own_record(self) -> None:
        self.kb.write_record("subspace", node("Subspace", "4"), "Changed.", ["A subspace is closed under addition."],
                             folder="drafts")
        self.assertEqual([], self.errors())
        self.kb.write_record("dimension", node("Dimension", "6"), "D.", ["Unique sentence about dimension."], folder="drafts")
        self.kb.write_record("dimension-2", node("dimension", "6"), "D.", ["Unique sentence about dimension."],
                             folder="drafts", base="notes")
        self.assert_error("dimension.md", "label", "is already the label of notes:dimension-2")
        self.kb.write_record("measure", node("Measure space", "2"), "M.", ["A measure space is a triple (X, M, mu)."],
                             folder="drafts")
        self.assert_error("measure.md", "label", "is already the label of kb:measure-space")

    def test_a_web_page_gives_its_last_path_segment(self) -> None:
        self.assertEqual("BERT (language model)", records.label_suffix("https://en.wikipedia.org/wiki/BERT_(language_model)#History"))
        self.assertEqual("2010.11929", records.label_suffix("https://arxiv.org/abs/2010.11929"))
        self.assertEqual("Autoencoder&VAE", records.label_suffix("notes/ml/Autoencoder&VAE.md"))


class ForeignRulesTest(CheckTestCase):
    URL = "https://example.com/paper"

    def foreign(self, identifier: str, kind: str = "concept", extra: str = "", quotes: tuple[str, ...] = ("Quoted elsewhere.",),
                folder: str = "entries", base: str = "kb") -> Path:
        frontmatter = f"label: {identifier.title()}\nkind: {kind}\nsource: {self.URL}\n{extra}".rstrip("\n")
        return self.kb.write_record(identifier, frontmatter, "Text.", list(quotes), folder=folder, base=base)

    def test_a_foreign_record_has_no_freshness(self) -> None:
        self.foreign("attention", quotes=("Not in any local source.",))
        self.foreign("draft-attention", quotes=("Nor this.",), folder="drafts")
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

    def test_fix_lines_leaves_a_foreign_record_alone(self) -> None:
        path = self.foreign("attention")
        before = path.read_bytes()
        report = fix_lines()
        self.assertEqual(([], [], []), (report["fixed"], report["skipped"], report["moved"]))
        self.assertEqual(before, path.read_bytes())

    def test_a_foreign_record_with_lines_is_a_frontmatter_error(self) -> None:
        self.foreign("lined", extra="lines: 2")
        self.assert_error("lined.md", "frontmatter", "a foreign record (its source is a web address) has no lines")

    def test_a_non_web_address_is_still_a_local_path(self) -> None:
        self.kb.write_record("ftp", node("F", "1").replace(SOURCE, "ftp://example.com/a"), "Text.", ["Title line"])
        self.assert_error("ftp.md", "source", "POSIX path relative to the base root or a web address")

    def test_the_kind_must_be_declared_by_a_type_of_the_base(self) -> None:
        self.foreign("lemma", kind="lemma")
        self.foreign("node-roles", extra='premise: ["[[subspace]]"]')
        self.foreign("implication", kind="implies", extra='premise: ["[[subspace]]"]\nconclusion: ["[[measure-space]]"]\nepistemic: stated')
        self.foreign("bad-epistemic", kind="implies",
                     extra='premise: ["[[subspace]]"]\nconclusion: ["[[measure-space]]"]\nepistemic: rumoured')
        self.foreign("bad-role", kind="implies", extra='witness: ["[[subspace]]"]')
        self.assert_error("lemma.md", "kind", "kind 'lemma' is not a node or relation kind of type math")
        self.assert_error("node-roles.md", "frontmatter", "premise is not a role of concept; it declares no roles")
        self.assert_error("bad-epistemic.md", "kind", "epistemic 'rumoured' is not allowed for implies (proved, stated)")
        self.assert_error("bad-role.md", "frontmatter", "witness is not a role of implies")
        self.assertNotIn("implication.md", [item[0] for item in self.errors()])

    def test_only_the_types_registered_for_the_base_count(self) -> None:
        self.kb.types["papers"] = {"node_kinds": {"model": []}, "guidance": "Papers."}
        self.kb.write_config()
        self.foreign("unregistered-kind", kind="model", extra="type: papers")
        self.assert_error("unregistered-kind.md", "type", "type 'papers' is not a document type of base kb (math)")
        self.kb.sources["kb"]["papers/*.pdf"] = "papers"
        self.kb.write_config()
        self.assertNotIn("unregistered-kind.md", [item[0] for item in self.errors()])

    def test_a_kind_declared_differently_by_two_types_is_ambiguous(self) -> None:
        self.kb.types["papers"] = {
            "node_kinds": {"concept": []},
            "relation_kinds": {"implies": ["because"]},
            "epistemic": ["claimed"],
            "guidance": "Papers.",
        }
        self.kb.sources["kb"]["papers/*.pdf"] = "papers"
        self.kb.write_config()
        self.foreign("shared-node", extra="type: math")
        self.foreign("claimed", kind="concept", extra="type: papers\nepistemic: claimed")
        self.foreign("because", kind="implies", extra='type: papers\nbecause: ["[[subspace]]", "[[measure-space]]"]')
        names = [item[0] for item in self.errors()]
        self.assertNotIn("shared-node.md", names)
        self.assertNotIn("claimed.md", names)
        self.assertNotIn("because.md", names)
        self.assertIn(("config.json", "type", "kind 'implies' is declared differently by the types math, papers of base kb"),
                      self.errors())


    def test_foreign_records_link_and_are_linked_like_any_record(self) -> None:
        self.foreign("attention", extra='requires: ["[[subspace]]", "[[notes:sigma]]"]')
        self.kb.write_record("sigma", node("Sigma", "1"), "Text.", ["Title line"], base="notes")
        self.relation("uses-attention", 'premise: ["[[attention]]"]\nconclusion: ["[[notes:sigma]]"]')
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())
        self.foreign("dangling", extra='requires: ["[[nowhere]]"]')
        self.assert_error("dangling.md", "link", "dangling link [[nowhere]]")


class BaseKindTest(CheckTestCase):
    """A base's document types must agree on each kind they share: node or relation, and its roles."""

    def type_errors(self) -> list[dict[str, Any]]:
        """The type errors of the home: a record of a base of several types that declares none is not one."""
        return [item for item in self.run_check()["errors"]
                if item["rule"] == "type" and item["path"] == str(self.kb.home / "config.json")]

    def register(self, spec: dict[str, Any], base: str = "kb") -> None:
        self.kb.types["papers"] = {"guidance": "Papers.", **spec}
        self.kb.sources[base]["papers/*.pdf"] = "papers"
        self.kb.write_config()

    def test_agreeing_types_are_clean(self) -> None:
        self.register({"node_kinds": {"concept": [], "model": []}, "relation_kinds": {"implies": ["premise", "conclusion"]}})
        self.assertEqual([], self.type_errors())

    def test_node_versus_relation_kind(self) -> None:
        self.register({"node_kinds": {"implies": []}})
        self.assertEqual(
            [{"path": str(self.kb.home / "config.json"), "rule": "type",
              "message": "kind 'implies' is declared differently by the types math, papers of base kb"}],
            self.type_errors(),
        )

    def test_different_roles(self) -> None:
        self.register({"node_kinds": {"concept": ["about"]}, "relation_kinds": {"equivalent": ["left", "right"]}})
        self.assertEqual(
            ["kind 'concept' is declared differently by the types math, papers of base kb",
             "kind 'equivalent' is declared differently by the types math, papers of base kb"],
            [item["message"] for item in self.type_errors()],
        )

    def test_types_of_different_bases_may_differ(self) -> None:
        self.kb.sources["notes"] = {}
        self.register({"node_kinds": {"implies": []}}, base="notes")
        self.assertEqual([], self.type_errors())


class FreshnessTest(CheckTestCase):
    def test_whitespace_is_normalized(self) -> None:
        self.kb.write_record("spaced", node("Spaced", "2-3"), "Text.", ["A  measure space\nis a triple (X, M, mu). It\textends"])
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

    def test_moved_proposes_the_covering_range(self) -> None:
        path = self.kb.write_record(
            "moved", node("Moved", "1"), "Text.", ["is closed under addition.", "The sum of two"]
        )
        self.assertEqual([{"path": str(path), "lines": "4-5"}], self.run_check()["moved"])

    def test_several_ranges_are_fresh_and_distant_quotes_move_to_separate_ranges(self) -> None:
        self.kb.write_record("apart", node("Apart", "2, 6"), "Text.",
                             ["A measure space is a triple", "Unique sentence about dimension."])
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())
        path = self.kb.write_record("distant", node("Distant", "1"), "Text.",
                                    ["A measure space is a triple", "Unique sentence about dimension."])
        self.assertEqual([{"path": str(path), "lines": "2-2, 6-6"}], self.run_check()["moved"])
        straddling = self.kb.write_record("straddling", node("Straddling", "2, 6"), "Text.",
                                          ["It extends a measurable space."])
        self.assertEqual([{"path": str(straddling), "lines": "3-3"}], self.run_check()["moved"][1:])

    def test_stale_when_missing_or_ambiguous(self) -> None:
        missing = self.kb.write_record("missing", node("Missing", "1"), "Text.", ["Nowhere at all."])
        ambiguous = self.kb.write_record("ambiguous", node("Ambiguous", "6"), "Text.", ["is a"])
        self.assertEqual(sorted([str(missing), str(ambiguous)]), sorted(self.run_check()["stale"]))

    def test_bom_and_crlf_sources_count_lines_consistently(self) -> None:
        source = self.kb.root / "notes/windows.txt"
        source.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(LINES).encode() + b"\r\n")
        self.kb.write_record("windows", node("W", "6").replace(SOURCE, "notes/windows.txt"), "Text.", ["Unique sentence about dimension."])
        self.kb.write_record("bom-first", node("B", "1").replace(SOURCE, "notes/windows.txt"), "Text.", ["Title line"])
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())
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
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

    def test_writes_a_range_when_the_quotes_span_lines(self) -> None:
        path = self.kb.write_record("span", node("Span", "1"), "Text.", ["closed under addition. The sum"])
        fix_lines()
        self.assertIn("lines: 4-5\n", path.read_text(encoding="utf-8"))

    def test_writes_several_ranges_for_distant_quotes(self) -> None:
        path = self.kb.write_record("distant", node("Distant", "1"), "Text.",
                                    ["A measure space is a triple", "Unique sentence about dimension."])
        fix_lines()
        self.assertIn("lines: 2, 6\n", path.read_text(encoding="utf-8"))
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, self.run_check())

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
