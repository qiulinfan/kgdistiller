from __future__ import annotations

import copy
import json
import runpy
import unittest
from pathlib import Path

from kgdistiller.home import KnowledgeError, load_type
from kgdistiller.index import index
from kgdistiller.navigation import navigate
from kgdistiller.records import check
from tests.knowledge_fixture import make_record_home, type_document
from tests.test_cli import kgdistiller, output

ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "skills/organize-kgdistiller/scripts/organize.py"))


def topic(identifier: str, label: str) -> dict:
    return {"id": identifier, "label": label, "summary": f"Choose {label} for its definitions and assumptions."}


def organization(identifier: str, parents: list[str], children: list[str]) -> dict:
    return {"id": identifier, "label": identifier, "summary": "Choose the relevant concept or result.",
            "parents": parents, "children": children}


def proposal(topics: list[dict], organizations: list[dict], retire: list[str] | None = None, scope: str = "local") -> dict:
    return {"scope": scope, "reason": "Corpus growth makes the new routes useful.", "topics": topics,
            "organizations": organizations, "retire": retire or []}


class NavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "other"))
        for base in self.kb.roots:
            self.kb.write_source("notes/a.txt", "A sigma algebra is closed under complements.\n"
                                 "A measure is countably additive.\nA variance bounds fluctuations.\n", base=base)
        self.science = {}
        for identifier, label, line, base in (("sigma", "Sigma algebra", 1, "kb"),
                                               ("measure", "Measure", 2, "kb"),
                                               ("variance", "Variance", 3, "other")):
            path = self.kb.write_record(identifier, f"label: {label}\nkind: definition\nsource: notes/a.txt\nlines: {line}",
                                        f"{label} under its stated assumptions.",
                                        [self.kb.roots[base].joinpath("notes/a.txt").read_text().splitlines()[line - 1]], base=base)
            self.science[f"{base}:{identifier}"] = path.read_bytes()
        path = self.kb.write_record("science-relation", "label: Scientific relation\nkind: implies\nsource: notes/a.txt\nlines: 1-2\n"
                                    'premise: ["[[sigma]]"]\nconclusion: ["[[measure]]"]', "The scientific relation remains independent.",
                                    ["A sigma algebra is closed under complements."])
        self.science["kb:science-relation"] = path.read_bytes()
        index()
        HELPER["initialize"]("kb")

    def context(self, base: str = "kb", changed: list[str] | None = None) -> dict:
        return HELPER["context"](base, changed=changed)

    def apply(self, value: dict, *, base: str = "kb", dry_run: bool = False) -> dict:
        return HELPER["apply"](self.context(base), value, dry_run)

    def initial(self) -> dict:
        value = proposal([topic("topic-analysis", "Analysis")],
                         [organization("route-analysis", ["kb:topic-analysis"], list(self.science))], scope="global")
        self.apply(value)
        return value

    def assert_science_preserved(self) -> None:
        for uid, text in self.science.items():
            base, identifier = uid.split(":")
            self.assertEqual(text, self.kb.path(identifier, base=base).read_bytes())

    def test_registered_compiler_source_real_cli_shared_cross_base_leaf_and_rebuild(self) -> None:
        self.initial()
        HELPER["initialize"]("other")
        self.apply(proposal([topic("topic-probability", "Probability")],
                            [organization("route-probability", ["other:topic-probability"], ["other:variance", "kb:sigma"])]),
                   base="other")
        entrances = output(kgdistiller("navigate", cwd=self.kb.root))
        self.assertEqual({"kb:topic-analysis", "other:topic-probability"}, {r["uid"] for r in entrances["records"]})
        first = output(kgdistiller("navigate", "kb:topic-analysis", cwd=self.kb.root))
        second = output(kgdistiller("navigate", "other:topic-probability", cwd=self.kb.root))
        shared = [r for r in first["records"] + second["records"] if r["uid"] == "kb:sigma"]
        self.assertEqual(2, len(shared))
        self.assertEqual({"leaf"}, {r["navigation"] for r in shared})
        self.assertTrue(any(r["uid"] == "kb:science-relation" for r in first["records"]))
        self.assertEqual(4, entrances["diagnostics"]["covered_count"])
        self.assertEqual({"errors": [], "stale": [], "moved": []}, check())
        for uid in ("topic-analysis", "route-analysis"):
            text = self.kb.path(uid).read_text()
            self.assertIn('source: "kgdistiller-navigation.md"', text)
            self.assertIn("Navigation compiler judgment", text)
            self.assertNotIn("source: notes/a.txt", text)
        before = navigate()
        index(rebuild=True)
        self.assertEqual(before, navigate())
        self.assert_science_preserved()

    def test_growth_split_merge_reparent_and_retire_keep_scientific_uid_and_evidence(self) -> None:
        self.initial()
        source = self.kb.root / "notes/a.txt"
        source.write_text(source.read_text() + "An integral accumulates measurable quantities.\n")
        added = self.kb.write_record("integral", "label: Integral\nkind: concept\nsource: notes/a.txt\nlines: 4",
                                    "An integral accumulates measurable quantities.", ["An integral accumulates measurable quantities."])
        self.science["kb:integral"] = added.read_bytes()
        split = proposal([topic("topic-structures", "Measurable structures"), topic("topic-estimates", "Estimates")], [
            organization("route-analysis", ["kb:topic-analysis"], ["kb:topic-structures", "kb:topic-estimates"]),
            organization("route-structures", ["kb:topic-structures"], ["kb:sigma", "kb:measure", "kb:integral", "kb:science-relation"]),
            organization("route-estimates", ["kb:topic-estimates"], ["other:variance", "kb:sigma"]),
        ])
        report = self.apply(split)
        self.assertEqual(["kb:route-analysis"], report["updated"])
        self.assertEqual({"kb:topic-structures", "kb:topic-estimates"},
                         {r["uid"] for r in navigate(["kb:topic-analysis"])["records"]})
        self.assertEqual(2, next(r for r in navigate(["kb:topic-estimates"])["records"] if r["uid"] == "kb:sigma")["parent_count"])
        # Merge the redundant branch, reparent its leaves, retire only navigation.
        merge = proposal([], [organization("route-analysis", ["kb:topic-analysis"], ["kb:topic-structures"]),
                              organization("route-structures", ["kb:topic-structures"], list(self.science))],
                         retire=["kb:topic-estimates", "kb:route-estimates"])
        merged = self.apply(merge)
        self.assertEqual({"kb:topic-estimates", "kb:route-estimates"}, set(merged["retired"]))
        self.assertEqual(0, merged["metrics"]["uncovered_count"])
        self.assertEqual(0, merged["metrics"]["cyclic_topic_count"])
        self.assert_science_preserved()
        self.assertEqual({"errors": [], "stale": [], "moved": []}, check())

    def test_cycles_dangling_lost_coverage_and_scientific_edits_refuse_before_writes(self) -> None:
        self.initial()
        before = self.kb.path("route-analysis").read_bytes()
        source = self.kb.root.joinpath("kgdistiller-navigation.md").read_bytes()
        bad = [
            proposal([], [organization("cycle", ["kb:topic-analysis"], ["kb:topic-analysis"])]),
            proposal([], [organization("missing", ["kb:topic-analysis"], ["kb:no-such-leaf"])]),
            proposal([], [organization("noncanonical", ["kb:topic-analysis"], ["kb:Sigma"])]),
            proposal([], [organization("route-analysis", ["kb:topic-analysis"], ["kb:sigma"])]),
            proposal([topic("sigma", "Overwrite scientific content")], []),
            proposal([], [], retire=["kb:sigma"]),
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(KnowledgeError):
                self.apply(value)
            self.assertEqual(before, self.kb.path("route-analysis").read_bytes())
            self.assertEqual(source, self.kb.root.joinpath("kgdistiller-navigation.md").read_bytes())
        self.assert_science_preserved()

    def test_dry_run_idempotence_and_changed_leaf_requires_route(self) -> None:
        original = self.initial()
        before = {path: path.read_bytes() for path in self.kb.root.joinpath(".knowledge/entries").glob("*.md")}
        source = self.kb.root.joinpath("kgdistiller-navigation.md").read_bytes()
        same = self.apply(original)
        self.assertEqual(([], []), (same["created"], same["updated"]))
        self.assertEqual(source, self.kb.root.joinpath("kgdistiller-navigation.md").read_bytes())
        for path, text in before.items():
            self.assertEqual(text, path.read_bytes())
        future = proposal([topic("topic-next", "Next entrance")],
                          [organization("route-next", ["kb:topic-next"], ["kb:sigma"])])
        self.assertEqual(["kb:topic-next", "kb:route-next"], self.apply(future, dry_run=True)["created"])
        self.assertFalse(self.kb.path("topic-next").exists())
        self.assertEqual(source, self.kb.root.joinpath("kgdistiller-navigation.md").read_bytes())
        self.kb.write_record("new-result", "label: New result\nkind: theorem\nsource: notes/a.txt\nlines: 2",
                             "Newly accepted result.", ["A measure is countably additive."])
        ctx = self.context(changed=["kb:new-result"])
        with self.assertRaisesRegex(KnowledgeError, "route changed knowledge"):
            HELPER["apply"](ctx, proposal([], []))

    def test_one_global_proposal_must_cover_scientific_records_of_all_bases(self) -> None:
        # The source/records are owned by kb, but the single semantic directory
        # must also reach knowledge physically owned by other.
        value = proposal([topic("topic-global", "Shared research")],
                         [organization("global-route", ["kb:topic-global"], ["kb:sigma", "kb:measure", "kb:science-relation"])],
                         scope="global")
        with self.assertRaisesRegex(KnowledgeError, "other:variance"):
            self.apply(value)
        value["organizations"][0]["children"].append("other:variance")
        result = self.apply(value)
        self.assertEqual(4, result["metrics"]["covered_count"])
        self.assert_science_preserved()

    def test_foreign_ingestion_batches_join_existing_shared_topic_and_global_audit_is_small(self) -> None:
        ctx = self.context(changed=["other:variance"])
        HELPER["apply"](ctx, proposal([topic("topic-shared", "Shared research")],
                                    [organization("route-foreign", ["kb:topic-shared"], ["other:variance"])]))
        stable = self.kb.path("topic-shared").read_bytes()
        first_route = self.kb.path("route-foreign").read_bytes()
        remaining = sorted(uid for uid in self.science if uid.startswith("kb:"))
        HELPER["apply"](self.context(changed=remaining),
                        proposal([], [organization("route-local", ["kb:topic-shared"], remaining)]))
        self.assertEqual(stable, self.kb.path("topic-shared").read_bytes())
        self.assertEqual(first_route, self.kb.path("route-foreign").read_bytes())
        audited = HELPER["apply"](self.context(changed=[]), proposal([], [], scope="global"))
        self.assertEqual(([], [], []), (audited["created"], audited["updated"], audited["retired"]))
        self.assertEqual(4, audited["metrics"]["covered_count"])
        self.assertEqual(4, len(navigate(["kb:topic-shared"])["records"]))
        self.assert_science_preserved()

    def test_user_corrections_and_stale_corpus_are_preserved(self) -> None:
        self.initial()
        old = self.context()
        path = self.kb.path("topic-analysis")
        corrected = path.read_text().replace("Choose Analysis", "Owner-specific Analysis", 1)
        path.write_text(corrected)
        with self.assertRaisesRegex(KnowledgeError, "stale context"):
            HELPER["apply"](old, proposal([], []))
        current = self.context()
        self.assertIn("kb:topic-analysis", current["protected"])
        for value in (proposal([topic("topic-analysis", "Replacement")], []),
                      proposal([], [], retire=["kb:topic-analysis"])):
            with self.assertRaisesRegex(KnowledgeError, "owner-edited"):
                HELPER["apply"](current, value)
        self.assertEqual(corrected, path.read_text())
        # Organizing around a protected topic is allowed; it remains byte-identical.
        self.apply(proposal([topic("topic-new-entrance", "New entrance")],
                            [organization("new-route", ["kb:topic-new-entrance"], ["kb:sigma"])]))
        self.assertEqual(corrected, path.read_text())
        ctx = self.context()
        self.kb.path("measure").write_text(self.kb.path("measure").read_text() + "\n")
        with self.assertRaisesRegex(KnowledgeError, "accepted records changed"):
            HELPER["apply"](ctx, proposal([], []))

    def test_retiring_navigation_with_scientific_incoming_reference_refuses(self) -> None:
        self.initial()
        path = self.kb.path("measure")
        path.write_text(path.read_text().replace("kind: definition", 'kind: definition\nrequires: ["[[topic-analysis]]"]'))
        replacement = proposal([topic("topic-replacement", "Replacement entrance")],
                               [organization("route-analysis", ["kb:topic-replacement"], list(self.science))],
                               retire=["kb:topic-analysis"])
        with self.assertRaisesRegex(KnowledgeError, "would fail check"):
            self.apply(replacement)
        self.assertTrue(self.kb.path("topic-analysis").exists())

    def test_navigate_bounded_pages_and_scientific_neighbors_not_projected(self) -> None:
        self.initial()
        first = navigate(["kb:topic-analysis"], limit=2)
        second = navigate(["kb:topic-analysis"], limit=2, offset=2)
        self.assertEqual((2, 2), (len(first["records"]), len(second["records"])))
        self.assertEqual(4, len({r["uid"] for r in first["records"] + second["records"]}))
        self.assertEqual(2, first["page"]["next_offset"])
        self.assertIsNone(second["page"]["next_offset"])
        self.assertEqual([], navigate(["kb:sigma"])["records"])
        scoped = navigate(["kb:topic-analysis"], bases=["kb"])
        self.assertNotIn("other:variance", {r["uid"] for r in scoped["records"]})
        with self.assertRaisesRegex(KnowledgeError, "unknown bases"):
            navigate(bases=["unknown"])

    def test_stale_or_absent_guide_does_not_hide_indexed_navigation(self) -> None:
        self.initial()
        before = navigate()
        self.kb.root.joinpath("kgdistiller-navigation.md").unlink()
        self.assertEqual(before["records"], navigate()["records"])
        self.assertEqual(sorted(self.science), sorted(row["uid"] for row in navigate(["kb:topic-analysis"])["records"]))

    def test_finite_role_conventions_are_validated_and_names_are_data(self) -> None:
        path = self.kb.home / "types/alternate.md"
        spec = {"node_kinds": ["entrance"], "relation_kinds": {"contains": ["container", "member"]},
                "navigation": {"topic_kind": "entrance", "relation_kind": "contains", "parent_role": "container", "child_role": "member"}}
        path.write_text(type_document(spec))
        self.assertEqual(spec["navigation"], load_type(path).navigation)
        for field, value in (("topic_kind", "undeclared"), ("relation_kind", "undeclared"),
                             ("parent_role", "member"), ("child_role", "undeclared")):
            invalid = copy.deepcopy(spec)
            invalid["navigation"][field] = value
            path.write_text(type_document(invalid))
            with self.subTest(field=field), self.assertRaises(KnowledgeError):
                load_type(path)
        path.unlink()
        # Activation uses arbitrary profile words, not hardcoded topic/role names.
        path = self.kb.home / "types/navigation.md"
        path.write_text(type_document(spec))
        self.initial()
        root = navigate()["records"][0]
        self.assertEqual("entrance", root["kind"])
        self.assertEqual(4, len(navigate([root["uid"]])["records"]))

    def test_source_collision_or_broad_extraction_glob_is_not_overwritten(self) -> None:
        path = self.kb.roots["other"] / "kgdistiller-navigation.md"
        path.write_text("Owner text must survive.")
        with self.assertRaisesRegex(KnowledgeError, "already exists"):
            HELPER["initialize"]("other")
        self.assertEqual("Owner text must survive.", path.read_text())
        path.unlink()
        config = self.kb.home / "config.json"
        payload = json.loads(config.read_text())
        payload["bases"]["other"]["sources"]["*.md"] = "math"
        config.write_text(json.dumps(payload))
        before = config.read_bytes()
        with self.assertRaisesRegex(KnowledgeError, "existing non-navigation glob"):
            HELPER["initialize"]("other")
        self.assertEqual(before, config.read_bytes())
        self.assertFalse(path.exists())
        HELPER["initialize"]("other", "kgdistiller-navigation.txt")
        self.assertEqual("navigation", json.loads(config.read_text())["bases"]["other"]["sources"]["kgdistiller-navigation.txt"])


if __name__ == "__main__":
    unittest.main()
