"""Read primitives: ``search`` lanes and fusion, filters, ``resolve`` and ``get``."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller import retrieve
from kgdistiller.home import KnowledgeError
from kgdistiller.index import index
from kgdistiller.retrieve import Filters, get, name_candidates, resolve, search
from tests.knowledge_fixture import make_record_home

LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "A measure is countably additive.",
    "测度论研究可测空间。",
    "A probability space has total mass one.",
]


def node(label: str, lines: str, *, source: str = "notes/a.txt", kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {source}\nlines: {lines}\n{extra}".rstrip("\n")


class RetrieveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        for base in ("kb", "notes"):
            self.kb.write_source("notes/a.txt", "\n".join(LINES) + "\n", base=base)
        self.kb.write_source("notes/b/c.txt", "\n".join(LINES) + "\n")
        self.kb.write_record(
            "measure-space",
            node("Measure space", "2", extra='aliases: [测度空间]\nunderstanding: understood\n'
                                          'requires: ["[[sigma-algebra]]", "[[measure]]", measurable space]'),
            "三元组 (X, M, μ)。不要混淆。", ["A measure space"],
        )
        self.kb.write_record("measure", node("Measure", "3", extra="aliases: [测度]"), "A countably additive set function.", ["A measure"])
        self.kb.write_record("sigma-algebra", node("σ-algebra", "1", source="notes/b/c.txt"), "A family of sets.", ["Title"])
        self.kb.write_record("measurement", node("Measurement", "1", kind="concept"), "Reading an instrument.", ["Title"])
        self.kb.write_record(
            "space-implies-probability",
            node("Measure space implies probability space", "5", kind="implies",
                 extra='premise: ["[[measure-space]]"]\nconclusion: ["[[probability-space]]", total mass]\nepistemic: stated'),
            "Normalizing the measure gives a probability space.", ["A probability space"],
        )
        self.kb.write_record("analysis", node("Analysis", "4", kind="theorem"), "测度论研究可测空间。", ["测度论"])
        self.kb.write_record("measure", node("Measure", "3", kind="theorem"), "A homonym in another base.", ["A measure"], base="notes")
        index()


class SearchTest(RetrieveTestCase):
    def test_output_shape_and_name_lane_first(self) -> None:
        result = search("measure space")
        self.assertEqual(["query", "lanes", "lag", "results"], list(result))
        self.assertEqual(["lexical", "name"], result["lanes"])
        self.assertEqual({"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": False}, result["lag"])
        top = result["results"][0]
        self.assertEqual("kb:measure-space", top["uid"])
        self.assertEqual(1, top["ranks"]["name"])
        self.assertEqual(["lexical", "name"], list(top["ranks"]))
        self.assertEqual(
            ["uid", "class", "kind", "label", "base", "source", "lines", "understanding", "epistemic", "gloss",
             "ranks", "requires", "participants", "in", "truncated"],
            list(top),
        )
        self.assertEqual(
            {"class": "node", "kind": "definition", "label": "Measure space", "base": "kb", "source": "notes/a.txt",
             "lines": "2-2", "understanding": "understood", "epistemic": None, "gloss": "三元组 (X, M, μ)。"},
            {key: top[key] for key in ("class", "kind", "label", "base", "source", "lines", "understanding", "epistemic", "gloss")},
        )
        self.assertEqual(
            [{"uid": "kb:sigma-algebra", "label": "σ-algebra"}, {"uid": "kb:measure", "label": "Measure"}, {"term": "measurable space"}],
            top["requires"],
        )
        self.assertEqual({}, top["participants"])
        self.assertEqual([{"uid": "kb:space-implies-probability", "kind": "implies", "role": "premise"}], top["in"])
        self.assertEqual({"requires": 0, "participants": 0, "in": 0}, top["truncated"])

    def test_relation_participants(self) -> None:
        relation = next(item for item in search("probability")["results"] if item["uid"] == "kb:space-implies-probability")
        self.assertEqual("relation", relation["class"])
        self.assertEqual("stated", relation["epistemic"])
        self.assertEqual(
            {"conclusion": [{"uid": "kb:probability-space", "label": None}, {"term": "total mass"}],
             "premise": [{"uid": "kb:measure-space", "label": "Measure space"}]},
            relation["participants"],
        )

    def test_name_lane_ranks_exact_then_longer_then_labels(self) -> None:
        connection = retrieve.open_read_only()
        self.addCleanup(connection.close)
        ranked = retrieve._name(connection, "measure space", Filters())
        self.assertEqual(["kb:measure-space", "kb:measure", "notes:measure"], ranked)

    def test_name_candidates_include_word_runs_and_cjk_substrings(self) -> None:
        self.assertEqual({"a", "b", "c", "a b", "b c", "a b c"}, name_candidates("A b-C"))
        self.assertTrue({"测度空间", "测", "空间", "测度", "x 测度空间", "x"} <= name_candidates("x 测度空间"))
        self.assertEqual(set(), name_candidates("∑"))
        many = name_candidates(" ".join(f"w{number}" for number in range(20)))
        self.assertEqual(12, max(len(key.split()) for key in many))

    def test_rrf_fusion_and_uid_tie_break(self) -> None:
        with patch.object(retrieve, "_lexical", lambda *args: ["kb:measure", "kb:analysis", "kb:sigma-algebra"]), \
             patch.object(retrieve, "_name", lambda *args: ["kb:analysis", "kb:measure"]):
            results = search("anything")["results"]
        self.assertEqual(["kb:analysis", "kb:measure", "kb:sigma-algebra"], [item["uid"] for item in results])
        self.assertEqual({"lexical": 2, "name": 1}, results[0]["ranks"])
        self.assertEqual({"lexical": 3, "name": None}, results[2]["ranks"])

    def test_limit(self) -> None:
        self.assertEqual(1, len(search("measure", limit=1)["results"]))
        with self.assertRaises(KnowledgeError):
            search("measure", limit=0)

    def test_cjk_query_hits_lexical_and_name_lanes(self) -> None:
        results = {item["uid"]: item for item in search("测度")["results"]}
        self.assertEqual(1, results["kb:measure"]["ranks"]["name"])
        self.assertIsNotNone(results["kb:analysis"]["ranks"]["lexical"])
        self.assertIsNone(results["kb:analysis"]["ranks"]["name"])
        longer = search("测度空间的定义")["results"]
        self.assertEqual("kb:measure-space", longer[0]["uid"])

    def test_filters_apply_in_every_lane(self) -> None:
        def uids(**filters: tuple[str, ...]) -> list[str]:
            return [item["uid"] for item in search("measure space probability", filters=Filters(**filters))["results"]]

        self.assertEqual(["notes:measure"], uids(base=("notes",)))
        self.assertEqual(["kb:space-implies-probability"], uids(class_=("relation",)))
        self.assertEqual(["kb:measurement"], [item["uid"] for item in search("measurement reading", filters=Filters(kind=("concept",)))["results"]])
        self.assertEqual(["kb:measure-space"], uids(understanding=("understood",)))
        self.assertEqual([], uids(source=("notes/b/",)))
        self.assertEqual(["kb:sigma-algebra"], [item["uid"] for item in search("family", filters=Filters(source=("notes/b",)))["results"]])
        both = set(uids(kind=("implies", "theorem")))
        self.assertEqual({"kb:space-implies-probability", "notes:measure"}, both)
        self.assertEqual({"notes:measure"}, set(uids(kind=("implies", "theorem"), base=("notes",))))
        with self.assertRaises(KnowledgeError):
            Filters(class_=("edge",))
        with self.assertRaises(KnowledgeError):
            Filters(understanding=("mastered",))

    def test_link_lists_are_capped(self) -> None:
        values = ", ".join(f'"[[hub-{number}]]"' for number in range(14))
        self.kb.write_record("hub", node("Hub", "1", extra=f"requires: [{values}]"), "Hub.", ["Title"])
        uses = ", ".join(f"term {number}" for number in range(15))
        self.kb.write_record("example-of-hub", node("Example of hub", "1", kind="example", extra=f"uses: [{uses}]\nsetting: [plane]"), "E.", ["Title"])
        for number in range(13):
            self.kb.write_record(f"needs-hub-{number}", node(f"Needs {number}", "1", extra='requires: ["[[hub]]"]'), "N.", ["Title"])
        index()
        results = {item["uid"]: item for item in search("hub", limit=40)["results"]}
        hub = results["kb:hub"]
        self.assertEqual((12, 12), (len(hub["requires"]), len(hub["in"])))
        self.assertEqual({"requires": 2, "participants": 0, "in": 1}, hub["truncated"])
        example = results["kb:example-of-hub"]
        self.assertEqual((12, 1), (len(example["participants"]["uses"]), len(example["participants"]["setting"])))
        self.assertEqual(3, example["truncated"]["participants"])

    def test_lag_reports_changed_files_after_an_edit(self) -> None:
        path = self.kb.path("measure")
        path.write_text(path.read_text(encoding="utf-8").replace("Measure", "Measure!"), encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(1, search("measure")["lag"]["changed_files"])
        self.assertEqual(1, resolve(["measure"])["lag"]["changed_files"])
        self.assertEqual(1, get(["kb:measure"])["lag"]["changed_files"])

    def test_missing_index(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.kb.home / 'index.sqlite'}{suffix}").unlink(missing_ok=True)
        with self.assertRaises(KnowledgeError) as caught:
            search("measure")
        self.assertIn("run `kgd index`", str(caught.exception))


class ResolveTest(RetrieveTestCase):
    def test_senses_mentions_and_pending(self) -> None:
        result = resolve(["Measure", "measurable space"])
        self.assertEqual(["terms", "lag"], list(result))
        measure, measurable = result["terms"]
        self.assertEqual("measure", measure["key"])
        self.assertEqual(["kb:measure", "notes:measure"], [item["uid"] for item in measure["senses"]])
        self.assertEqual(
            {"uid": "kb:measure", "label": "Measure", "kind": "definition", "class": "node", "base": "kb",
             "source": "notes/a.txt", "lines": "3-3", "gloss": "A countably additive set function."},
            measure["senses"][0],
        )
        self.assertEqual(["kb:measure-space", "kb:space-implies-probability"], [item["uid"] for item in measure["mentions"]])
        self.assertEqual([], measure["pending"])
        self.assertEqual([], measurable["senses"])
        self.assertEqual([{"owner": "kb:measure-space", "role": "requires", "term": "measurable space"}], measurable["pending"])

    def test_cjk_terms_match_substrings(self) -> None:
        term = resolve(["测度"])["terms"][0]
        self.assertEqual(["kb:measure"], [item["uid"] for item in term["senses"]])
        self.assertEqual(["kb:measure-space"], [item["uid"] for item in term["mentions"]])

    def test_filters(self) -> None:
        term = resolve(["measure"], Filters(base=("notes",)))["terms"][0]
        self.assertEqual(["notes:measure"], [item["uid"] for item in term["senses"]])
        self.assertEqual([], term["mentions"])
        self.assertEqual([], resolve(["∑"])["terms"][0]["senses"])


class GetTest(RetrieveTestCase):
    def test_complete_record_with_links(self) -> None:
        result = get(["kb:space-implies-probability", "kb:nothing", "measure-space"])
        self.assertEqual(["records", "missing", "lag"], list(result))
        self.assertEqual(["kb:nothing"], result["missing"])
        relation, node_record = result["records"]
        self.assertEqual(
            [{"role": "conclusion", "pos": 0, "uid": "kb:probability-space", "label": None, "exists": False},
             {"role": "conclusion", "pos": 1, "term": "total mass"},
             {"role": "premise", "pos": 0, "uid": "kb:measure-space", "label": "Measure space", "exists": True}],
            relation["out"],
        )
        self.assertEqual(["A probability space"], relation["evidence"])
        self.assertEqual("Normalizing the measure gives a probability space.", relation["body"])
        self.assertEqual("", relation["search_terms"])
        self.assertEqual(["测度空间"], node_record["aliases"])
        self.assertEqual("requires", node_record["out"][-1]["role"])
        self.assertEqual(
            [{"uid": "kb:space-implies-probability", "label": "Measure space implies probability space", "kind": "implies", "role": "premise"}],
            node_record["in"],
        )
        self.assertNotIn("source_text", node_record)

    def test_source_lines_read_live(self) -> None:
        record = get(["kb:measure"], source_lines=1)["records"][0]
        self.assertEqual("2\tA measure space is a triple (X, M, mu).\n3\tA measure is countably additive.\n4\t测度论研究可测空间。", record["source_text"])
        self.assertEqual("1\tTitle line\n2\tA measure space is a triple (X, M, mu).", get(["kb:sigma-algebra"], source_lines=1)["records"][0]["source_text"])
        (self.kb.root / "notes/a.txt").unlink()
        self.assertIsNone(get(["kb:measure"], source_lines=0)["records"][0]["source_text"])

    def test_source_lines_never_leave_the_registered_sources(self) -> None:
        outside = self.kb.home.parent / "private.txt"
        outside.write_text("secret line\nsecond secret\n", encoding="utf-8")
        (self.kb.root / "notes/link.txt").symlink_to(outside)
        (self.kb.root / "unregistered.md").write_text("Title line\n", encoding="utf-8")
        for identifier, source in (
            ("traversal", "../private.txt"),
            ("absolute", str(outside)),
            ("escaping-link", "notes/link.txt"),
            ("unregistered", "unregistered.md"),
        ):
            self.kb.write_record(identifier, node(identifier, "1", source=source), "Text.", ["secret line"])
        index()
        for identifier in ("traversal", "absolute", "escaping-link", "unregistered"):
            record = get([f"kb:{identifier}"], source_lines=1)["records"][0]
            self.assertEqual("kb:" + identifier, record["uid"])
            self.assertIsNone(record["source_text"], identifier)

    def test_a_broken_document_type_does_not_stop_reads_or_index(self) -> None:
        (self.kb.home / "types/math.md").write_text("---\nnode_kinds: definition\n---\nGuidance.\n", encoding="utf-8")
        self.assertEqual("kb:measure-space", search("measure space")["results"][0]["uid"])
        self.assertEqual(["kb:measure"], [sense["uid"] for sense in resolve(["测度"])["terms"][0]["senses"]])
        self.assertIsNotNone(get(["kb:measure"], source_lines=0)["records"][0]["source_text"])
        self.assertEqual({}, {name: counts["unparseable"] for name, counts in index()["bases"].items() if counts["unparseable"]})

    def test_bare_ids(self) -> None:
        self.assertEqual("kb:sigma-algebra", get(["Sigma-Algebra"])["records"][0]["uid"])
        with self.assertRaises(KnowledgeError) as caught:
            get(["measure"])
        self.assertIn("kb:measure, notes:measure", str(caught.exception))
        self.assertEqual(["nothing"], get(["nothing"])["missing"])


if __name__ == "__main__":
    unittest.main()
