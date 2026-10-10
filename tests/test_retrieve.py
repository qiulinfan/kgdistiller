"""Read primitives: ``search`` lanes and fusion, filters, ``resolve``, ``get``, ``neighbors``, ``browse`` and ``pack``."""

from __future__ import annotations

import os
import sqlite3
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller import retrieve
from kgdistiller.adapters import sentence_transformers as adapter
from kgdistiller.home import KnowledgeError
from kgdistiller.index import index
from kgdistiller.retrieve import (
    Filters,
    browse,
    compact_json,
    get,
    name_candidates,
    neighbors,
    pack,
    resolve,
    search,
)
from tests.knowledge_fixture import (
    FAKE_DIMENSION,
    FakeEncoder,
    fake_encoder,
    make_record_home,
)

REAL_ENCODER = adapter.encoder

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


class DenseLaneTest(RetrieveTestCase):
    MODEL = "fake/model"
    QUERY = "a countably additive measure space"

    def setUp(self) -> None:
        super().setUp()
        self.kb.embedding = self.MODEL
        self.kb.write_config()
        context = fake_encoder()
        self.encoders = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.assertEqual(7, index()["embedded"])
        self.encoders.loads.clear()

    def dense(self, query: str = QUERY, filters: Filters = retrieve.NO_FILTERS) -> list[str]:
        connection = retrieve.open_read_only()
        self.addCleanup(connection.close)
        return retrieve._dense(connection, query, filters, self.MODEL)

    def expected_order(self, query: str) -> list[str]:
        """A pure-Python dot-product ranking of the fake vectors of every embedded row; ties by uid."""
        connection = retrieve.open_read_only()
        self.addCleanup(connection.close)
        unpack = struct.Struct(f"<{FAKE_DIMENSION}f").unpack
        wanted = unpack(FakeEncoder.vector(query))
        scores = {
            uid: sum(a * b for a, b in zip(unpack(FakeEncoder.vector(text)), wanted, strict=True))
            for uid, text in connection.execute("SELECT uid, text FROM record WHERE vec IS NOT NULL")
        }
        return sorted(scores, key=lambda uid: (-scores[uid], uid))

    def test_three_lanes_and_rank_keys(self) -> None:
        result = search("measure space")
        self.assertEqual(["lexical", "dense", "name"], result["lanes"])
        self.assertTrue(result["results"])
        for item in result["results"]:
            self.assertEqual(["lexical", "dense", "name"], list(item["ranks"]))
        self.assertEqual([self.MODEL], self.encoders.loads)

    def test_dense_order_is_the_exact_dot_product(self) -> None:
        expected = self.expected_order(self.QUERY)
        self.assertEqual(7, len(expected))
        self.assertEqual(expected, self.dense())
        ranks = {item["uid"]: item["ranks"]["dense"] for item in search(self.QUERY)["results"]}
        self.assertEqual({uid: position for position, uid in enumerate(expected, 1)}, ranks)

    def test_query_is_encoded_with_the_indexed_model(self) -> None:
        self.kb.embedding = "other/model"
        self.kb.write_config()
        result = search("measure space")
        self.assertIn("dense", result["lanes"])
        self.assertTrue(result["lag"]["embedding_changed"])
        self.assertEqual([self.MODEL], self.encoders.loads)

    def test_filters_restrict_the_dense_lane(self) -> None:
        cases = {
            "base": (Filters(base=("notes",)), {"notes:measure"}),
            "kind": (Filters(kind=("concept",)), {"kb:measurement"}),
            "class": (Filters(class_=("relation",)), {"kb:space-implies-probability"}),
            "source": (Filters(source=("notes/b",)), {"kb:sigma-algebra"}),
        }
        for name, (filters, expected) in cases.items():
            with self.subTest(filter=name):
                self.assertEqual(expected, set(self.dense(filters=filters)))
                dense = {item["uid"] for item in search(self.QUERY, filters=filters)["results"] if item["ranks"]["dense"]}
                self.assertEqual(expected, dense)

    def test_unembedded_rows_are_absent(self) -> None:
        path = self.kb.path("measure")
        path.write_text(path.read_text(encoding="utf-8").replace("A countably additive", "An additive"), encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(1, index(embed=False)["unembedded"])
        self.assertNotIn("kb:measure", self.dense())
        self.assertEqual(6, len(self.dense()))
        result = search("measure")
        self.assertEqual(1, result["lag"]["unembedded"])
        self.assertIsNone(next(item for item in result["results"] if item["uid"] == "kb:measure")["ranks"]["dense"])

    def test_dense_false_skips_the_lane_and_the_model(self) -> None:
        result = search("measure space", dense=False)
        self.assertEqual(["lexical", "name"], result["lanes"])
        self.assertEqual(["lexical", "name"], list(result["results"][0]["ranks"]))
        self.assertEqual([], self.encoders.loads)

    def test_missing_retrieval_extra(self) -> None:
        for name, value in (("encoder", REAL_ENCODER), ("_resident", None)):
            patcher = patch.object(adapter, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaises(KnowledgeError) as caught:
                search("measure space")
            self.assertEqual(["lexical", "name"], search("measure space", dense=False)["lanes"])
        message = str(caught.exception)
        self.assertIn("--no-dense", message)
        self.assertIn("install kgdistiller[retrieval]", message)
        self.assertIn(self.MODEL, message)

    def test_missing_numpy(self) -> None:
        with patch.dict(sys.modules, {"numpy": None}), self.assertRaises(KnowledgeError) as caught:
            search("measure space")
        self.assertIn("install kgdistiller[retrieval] or pass --no-dense", str(caught.exception))
        self.assertEqual([], self.encoders.loads)

    def test_model_load_failure_names_no_dense(self) -> None:
        def offline(model: str) -> adapter.Encoder:
            raise adapter.EmbeddingError(f"cannot load embedding model {model}: not in the cached files")

        with patch.object(adapter, "encoder", offline), self.assertRaises(KnowledgeError) as caught:
            search("measure space")
        message = str(caught.exception)
        self.assertIn("cannot load embedding model fake/model", message)
        self.assertIn("--no-dense (MCP: no_dense)", message)

    def test_an_index_without_vectors_needs_neither_numpy_nor_the_model(self) -> None:
        self.kb.embedding = "other/model"
        self.kb.write_config()
        self.assertEqual(7, index(embed=False)["unembedded"])
        with patch.dict(sys.modules, {"numpy": None}):
            result = search("measure space")
        self.assertEqual(["lexical", "dense", "name"], result["lanes"])
        self.assertTrue(all(item["ranks"]["dense"] is None for item in result["results"]))
        self.assertEqual([], self.encoders.loads)

    def test_one_read_snapshot_per_call(self) -> None:
        """A commit between reading meta.embedding and the vectors is invisible to the call."""
        real = retrieve.meta_embedding

        def then_index_clears_vectors(connection: object) -> str:
            model = real(connection)
            writer = sqlite3.connect(self.kb.home / "index.sqlite", isolation_level=None)
            writer.execute("UPDATE record SET vec = NULL")
            writer.close()
            return model

        with patch.object(retrieve, "meta_embedding", then_index_clears_vectors):
            result = search(self.QUERY)
        self.assertEqual(7, sum(item["ranks"]["dense"] is not None for item in result["results"]))
        self.assertEqual([], self.dense())


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


# ---------------------------------------------------------------- graph and navigation

GRAPH_LINES = [
    "Graph fixture",
    "A is defined from B and a loose term.",
    "B is defined from A.",
    "C needs D.",
    "D is basic.",
    "X is a theorem.",
    "Y is a theorem.",
    "X implies Y.",
    "X contrasts with Y, witnessed by the implication.",
    "X and Y are equivalent.",
    "An example uses X in Z.",
    "Linear probing, the second sense.",
    "Linear probing resolves collisions.",
    "A linear probing hash table stores keys in one array.",
    "A big record.",
]
SUB_LINES = ["Sub fixture", "H needs D."]
NO_LAG = {"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": False}


class GraphTestCase(unittest.TestCase):
    """A requires cycle, a requires chain, relations over x and y (one citing another), a missing target and homonyms."""

    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source("notes/g.txt", "\n".join(GRAPH_LINES) + "\n")
        self.kb.write_source("notes/sub/h.txt", "\n".join(SUB_LINES) + "\n")
        self.kb.write_source("notes/g.txt", "Notes fixture\nX in notes.\n", base="notes")
        records = [
            ("a", "A", 2, "definition", 'requires: ["[[b]]", loose term]'),
            ("b", "B", 3, "definition", 'requires: ["[[a]]"]'),
            ("c", "C", 4, "definition", 'requires: ["[[d]]"]'),
            ("d", "D", 5, "definition", ""),
            ("x", "X", 6, "theorem", ""),
            ("y", "Y", 7, "theorem", ""),
            ("r1", "X implies Y", 8, "implies", 'premise: ["[[x]]"]\nconclusion: ["[[y]]"]'),
            ("r2", "X contrasts with Y", 9, "contrasts", 'subject: ["[[x]]"]\ncontrast: ["[[y]]"]\nwitness: ["[[r1]]"]'),
            ("r3", "X equivalent to Y", 10, "equivalent", 'side: ["[[x]]", "[[y]]"]'),
            ("e1", "Example of X", 11, "example", 'uses: ["[[x]]"]\nsetting: ["[[z]]"]'),
            ("linear-probing-2", "Linear probing", 12, "definition", ""),
            ("linear-probing", "Linear probing", 13, "definition", ""),
            ("linear-probing-hash-table", "Linear probing hash table", 14, "concept", ""),
            ("big", "Big", 15, "concept", ""),
        ]
        for identifier, label, line, kind, extra in records:
            body = "Long. " * 400 if identifier == "big" else f"{label}."
            self.kb.write_record(identifier, node(label, str(line), source="notes/g.txt", kind=kind, extra=extra),
                                 body, [GRAPH_LINES[line - 1]])
        self.kb.write_record("h", node("H", "2", source="notes/sub/h.txt", extra='requires: ["[[d]]"]'), "H.", ["H needs D."])
        self.kb.write_record("x", node("X", "2", source="notes/g.txt", kind="theorem"), "X in notes.", ["X in notes."], base="notes")
        report = index()
        self.assertEqual({}, {name: counts["unparseable"] for name, counts in report["bases"].items() if counts["unparseable"]})


def edge(source: str, role: str, target: str, depth: int, *, term: bool = False) -> dict[str, object]:
    return {"from": source, "role": role, ("term" if term else "to"): target, "depth": depth}


class NeighborsTest(GraphTestCase):
    def test_requires_closure_ends_a_cycle_at_the_depth_limit(self) -> None:
        result = neighbors(["kb:a"], roles=("requires",), direction="out", depth=3)
        self.assertEqual(["edges", "records", "missing", "lag"], list(result))
        self.assertEqual(
            [edge("kb:a", "requires", "kb:b", 1), edge("kb:a", "requires", "loose term", 1, term=True),
             edge("kb:b", "requires", "kb:a", 2)],
            result["edges"],
        )
        self.assertEqual(
            {"kb:a": {"label": "A", "kind": "definition", "class": "node", "exists": True},
             "kb:b": {"label": "B", "kind": "definition", "class": "node", "exists": True}},
            result["records"],
        )
        self.assertEqual([], result["missing"])
        self.assertEqual(NO_LAG, result["lag"])

    def test_depth_one_cuts_the_closure(self) -> None:
        result = neighbors(["kb:a"], roles=("requires",), depth=1)
        self.assertEqual(
            [edge("kb:a", "requires", "kb:b", 1), edge("kb:a", "requires", "loose term", 1, term=True)],
            result["edges"],
        )
        self.assertEqual(["kb:a", "kb:b"], list(result["records"]))

    def test_claim_closure_across_relation_to_relation_links(self) -> None:
        citing = neighbors(["kb:x"], direction="in")
        self.assertEqual(
            [edge("kb:e1", "uses", "kb:x", 1), edge("kb:r1", "premise", "kb:x", 1),
             edge("kb:r2", "subject", "kb:x", 1), edge("kb:r3", "side", "kb:x", 1)],
            citing["edges"],
        )
        closure = neighbors(["kb:x"], direction="both", depth=2)
        self.assertEqual(
            citing["edges"] + [
                edge("kb:e1", "setting", "kb:z", 2), edge("kb:r1", "conclusion", "kb:y", 2),
                edge("kb:r2", "contrast", "kb:y", 2), edge("kb:r2", "witness", "kb:r1", 2),
                edge("kb:r3", "side", "kb:y", 2),
            ],
            closure["edges"],
        )
        self.assertEqual({"label": None, "kind": None, "class": None, "exists": False}, closure["records"]["kb:z"])
        self.assertEqual({"label": "Y", "kind": "theorem", "class": "node", "exists": True}, closure["records"]["kb:y"])
        self.assertEqual({"kb:x", "kb:y", "kb:z", "kb:r1", "kb:r2", "kb:r3", "kb:e1"}, set(closure["records"]))

    def test_applications_by_kind(self) -> None:
        result = neighbors(["kb:x"], direction="in", filters=Filters(kind=("example",)))
        self.assertEqual([edge("kb:e1", "uses", "kb:x", 1)], result["edges"])
        self.assertEqual(["kb:x", "kb:e1"], list(result["records"]))

    def test_the_role_restriction_holds_at_every_hop(self) -> None:
        result = neighbors(["kb:x"], roles=("premise",), direction="both", depth=2)
        self.assertEqual([edge("kb:r1", "premise", "kb:x", 1)], result["edges"])
        self.assertEqual(["kb:x", "kb:r1"], list(result["records"]))

    def test_unknown_and_ambiguous_starts(self) -> None:
        result = neighbors(["kb:nothing", "kb:d", "kb:nothing"])
        self.assertEqual(["kb:nothing"], result["missing"])
        self.assertEqual([], result["edges"])
        self.assertEqual(["kb:d"], list(result["records"]))
        with self.assertRaises(KnowledgeError) as caught:
            neighbors(["x"])
        self.assertIn("kb:x, notes:x", str(caught.exception))

    def test_bad_direction_or_depth(self) -> None:
        for arguments in ({"direction": "sideways"}, {"depth": 0}):
            with self.subTest(**arguments), self.assertRaises(KnowledgeError):
                neighbors(["kb:a"], **arguments)


class BrowseTest(GraphTestCase):
    def test_bases_with_counts_and_availability(self) -> None:
        result = browse()
        self.assertEqual(["bases", "lag"], list(result))
        self.assertEqual(
            [{"name": "kb", "available": True, "records": 15, "relations": 4, "pending": 1},
             {"name": "notes", "available": True, "records": 1, "relations": 0, "pending": 0}],
            result["bases"],
        )
        self.kb.roots["notes"].rename(self.kb.roots["notes"].with_name("moved"))
        moved = browse()
        self.assertEqual([True, False], [base["available"] for base in moved["bases"]])
        self.assertEqual(["notes"], moved["lag"]["unavailable_bases"])

    def test_base_root_and_directory(self) -> None:
        root = browse("kb")
        self.assertEqual(["base", "dir", "entries", "lag"], list(root))
        self.assertEqual(("kb", ""), (root["base"], root["dir"]))
        self.assertEqual([{"path": "notes/", "type": "dir", "records": 15, "relations": 4, "pending": 1}], root["entries"])
        directory = browse("kb:notes/")
        self.assertEqual("notes/", directory["dir"])
        self.assertEqual(
            [{"path": "notes/g.txt", "type": "source", "records": 14, "relations": 4, "pending": 1},
             {"path": "notes/sub/", "type": "dir", "records": 1, "relations": 0, "pending": 0}],
            directory["entries"],
        )
        self.assertEqual([], browse("kb:elsewhere/")["entries"])

    def test_source_file_by_kind_with_pending_terms(self) -> None:
        result = browse("kb:notes/g.txt")
        self.assertEqual(["base", "source", "kinds", "pending", "lag"], list(result))
        self.assertEqual(
            ["concept", "contrasts", "definition", "equivalent", "example", "implies", "theorem"],
            [group["kind"] for group in result["kinds"]],
        )
        definitions = next(group["records"] for group in result["kinds"] if group["kind"] == "definition")
        self.assertEqual(
            ["kb:a", "kb:b", "kb:c", "kb:d", "kb:linear-probing-2", "kb:linear-probing"],
            [record["uid"] for record in definitions],
        )
        self.assertEqual(
            {"uid": "kb:a", "label": "A", "class": "node", "lines": "2-2", "understanding": "unknown", "gloss": "A.",
             "links": {"requires": [{"uid": "kb:b", "label": "B"}, {"term": "loose term"}]}},
            definitions[0],
        )
        example = next(group["records"][0] for group in result["kinds"] if group["kind"] == "example")
        self.assertEqual(
            {"setting": [{"uid": "kb:z", "label": None}], "uses": [{"uid": "kb:x", "label": "X"}]}, example["links"]
        )
        self.assertEqual([{"term": "loose term", "owners": [{"uid": "kb:a", "role": "requires"}]}], result["pending"])
        self.assertEqual({"base": "kb", "source": "notes/none.txt", "kinds": [], "pending": [], "lag": NO_LAG},
                         browse("kb:notes/none.txt"))

    def test_a_kind_filter_lists_records_with_every_link(self) -> None:
        result = browse(filters=Filters(kind=("contrasts",)))
        self.assertEqual(["records", "lag"], list(result))
        self.assertEqual(
            [{"uid": "kb:r2", "label": "X contrasts with Y", "kind": "contrasts", "class": "relation", "base": "kb",
              "source": "notes/g.txt", "lines": "9-9", "understanding": "unknown", "epistemic": None,
              "gloss": "X contrasts with Y.",
              "links": {"contrast": [{"uid": "kb:y", "label": "Y"}], "subject": [{"uid": "kb:x", "label": "X"}],
                        "witness": [{"uid": "kb:r1", "label": "X implies Y"}]}}],
            result["records"],
        )
        scoped = browse("kb:notes/sub/", filters=Filters(kind=("definition",)))
        self.assertEqual(["kb:h"], [record["uid"] for record in scoped["records"]])
        self.assertEqual(
            ["kb:a", "kb:b", "kb:c", "kb:d", "kb:linear-probing-2", "kb:linear-probing", "kb:h"],
            [record["uid"] for record in browse("kb", filters=Filters(kind=("definition",)))["records"]],
        )
        self.assertEqual(["kb:x", "kb:y"], [record["uid"] for record in browse("kb:notes/g.txt", filters=Filters(kind=("theorem",)))["records"]])

    def test_unknown_base(self) -> None:
        with self.assertRaises(KnowledgeError) as caught:
            browse("nope")
        self.assertIn("unknown base 'nope'", str(caught.exception))

    def test_a_class_filter_changes_the_counts(self) -> None:
        relations = Filters(class_=("relation",))
        self.assertEqual(
            [{"name": "kb", "available": True, "records": 4, "relations": 4, "pending": 0},
             {"name": "notes", "available": True, "records": 0, "relations": 0, "pending": 0}],
            browse(filters=relations)["bases"],
        )
        self.assertEqual(
            [{"path": "notes/g.txt", "type": "source", "records": 4, "relations": 4, "pending": 0}],
            browse("kb:notes/", filters=relations)["entries"],
        )


def packed(result: dict[str, object]) -> list[str]:
    return [record["uid"] for record in result["records"]]  # type: ignore[index, union-attr]


def packed_record(uid: str) -> dict[str, object]:
    record = get([uid])["records"][0]
    del record["in"], record["search_terms"]
    return record


class PackTest(GraphTestCase):
    def test_budget_skips_a_record_that_does_not_fit(self) -> None:
        sizes = {uid: len(compact_json(packed_record(uid)).encode("utf-8")) for uid in ("kb:c", "kb:big", "kb:d")}
        budget = 2 + sizes["kb:c"] + 1 + sizes["kb:d"]
        self.assertGreater(sizes["kb:big"], sizes["kb:d"])
        result = pack(["kb:c", "kb:big", "kb:d"], budget=budget, requires_depth=0)
        self.assertEqual(["records", "bytes", "budget", "gaps", "lag"], list(result))
        self.assertEqual(["kb:c", "kb:d"], packed(result))
        self.assertEqual([{"reason": "over-budget", "uid": "kb:big"}], result["gaps"])
        self.assertEqual(len(compact_json(result["records"]).encode("utf-8")), result["bytes"])
        self.assertEqual((budget, budget), (result["bytes"], result["budget"]))
        self.assertEqual(NO_LAG, result["lag"])

    def test_requires_closure_is_breadth_first(self) -> None:
        self.assertEqual(["kb:c", "kb:a", "kb:d", "kb:b"], packed(pack(["kb:c", "kb:a"], requires_depth=1)))
        shallow = pack(["kb:c", "kb:a", "c"], requires_depth=0)
        self.assertEqual(["kb:c", "kb:a"], packed(shallow))
        self.assertEqual(
            [{"reason": "not-packed", "from": "kb:c", "role": "requires", "uid": "kb:d"},
             {"reason": "not-packed", "from": "kb:a", "role": "requires", "uid": "kb:b"},
             {"reason": "pending", "from": "kb:a", "role": "requires", "term": "loose term"}],
            shallow["gaps"],
        )

    def test_shared_relations_by_packed_participants(self) -> None:
        result = pack(["kb:x", "kb:y", "kb:r1"])
        self.assertEqual(["kb:x", "kb:y", "kb:r1", "kb:r2", "kb:r3"], packed(result))
        self.assertEqual([], result["gaps"])
        self.assertEqual(["kb:x", "kb:r1", "kb:r2"], packed(pack(["kb:x", "kb:r1"])))
        self.assertEqual(["kb:x"], packed(pack(["kb:x"])))

    def test_gap_reasons_and_order(self) -> None:
        result = pack(["kb:nothing", "kb:a", "kb:e1"], requires_depth=0)
        self.assertEqual(["kb:a", "kb:e1"], packed(result))
        self.assertEqual(
            [{"reason": "unknown-uid", "uid": "kb:nothing"},
             {"reason": "not-packed", "from": "kb:a", "role": "requires", "uid": "kb:b"},
             {"reason": "pending", "from": "kb:a", "role": "requires", "term": "loose term"},
             {"reason": "missing-target", "from": "kb:e1", "role": "setting", "uid": "kb:z"},
             {"reason": "not-packed", "from": "kb:e1", "role": "uses", "uid": "kb:x"}],
            result["gaps"],
        )

    def test_records_are_get_records_without_in_and_search_terms(self) -> None:
        result = pack(["kb:r2", "kb:a"], requires_depth=0)
        for record in result["records"]:
            self.assertNotIn("in", record)
            self.assertNotIn("search_terms", record)
            self.assertEqual(packed_record(record["uid"]), record)

    def test_filters_restrict_only_what_pack_adds(self) -> None:
        self.assertEqual(["kb:h", "kb:d"], packed(pack(["kb:h"])))
        restricted = pack(["kb:h", "kb:c"], filters=Filters(source=("notes/sub/",)))
        self.assertEqual(["kb:h", "kb:c"], packed(restricted))
        self.assertEqual(
            [{"reason": "not-packed", "from": "kb:h", "role": "requires", "uid": "kb:d"},
             {"reason": "not-packed", "from": "kb:c", "role": "requires", "uid": "kb:d"}],
            restricted["gaps"],
        )

    def test_bad_budget_or_depth(self) -> None:
        for arguments in ({"budget": 0}, {"requires_depth": -1}):
            with self.subTest(**arguments), self.assertRaises(KnowledgeError):
                pack(["kb:a"], **arguments)


class HomonymTest(GraphTestCase):
    def test_senses_in_line_order_and_longer_labels_as_mentions(self) -> None:
        term = resolve(["Linear probing"])["terms"][0]
        self.assertEqual(["kb:linear-probing-2", "kb:linear-probing"], [item["uid"] for item in term["senses"]])
        self.assertEqual(["12-12", "13-13"], [item["lines"] for item in term["senses"]])
        self.assertEqual(["kb:linear-probing-hash-table"], [item["uid"] for item in term["mentions"]])


if __name__ == "__main__":
    unittest.main()
