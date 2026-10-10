from __future__ import annotations

import math
import unittest

from kgdistiller.contracts import canonical_json, validate_contract
from kgdistiller.query import (
    CONTEXT_SCHEMA,
    GraphView,
    QueryError,
    context,
    estimate_tokens,
    expand,
    get,
    personalized_pagerank,
    query_status,
    resolve_concepts,
    search,
)
from tests.knowledge_fixture import edge_record, make_fixture, memory_view, node_record


def fixture_nodes() -> list[dict]:
    return [
        node_record("sigma-algebra", "Sigma algebra",
                    "A collection closed under countable union and complement.",
                    aliases=["σ-algebra", "西格玛代数"]),
        node_record("measure", "Measure", "A countably additive set function.", aliases=["测度"]),
        node_record("absolute-continuity", "Absolute continuity",
                    "Absolute continuity (AC) is defined relative to a measure."),
    ]


def fixture_edges() -> list[dict]:
    return [
        edge_record("sigma-algebra", "prerequisite-for", "measure",
                    evidence="A measure is defined on a sigma algebra."),
        edge_record("measure", "prerequisite-for", "absolute-continuity",
                    evidence="Absolute continuity compares measures."),
    ]


def labelled(entry_id: str, text: str, label: str = "Equal label") -> dict:
    return node_record(entry_id, label, text)


class QueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = memory_view(fixture_nodes(), fixture_edges())

    def test_explicit_chinese_and_english_aliases_resolve_identity(self) -> None:
        resolved = resolve_concepts(self.view, ["SIGMA ALGEBRA", "西格玛代数", "测度", "measure"])
        self.assertEqual(["exact", "alias", "alias", "exact"], [row["status"] for row in resolved])
        self.assertEqual(["label", "alias", "alias", "id"], [row["match_kind"] for row in resolved])
        self.assertEqual("sigma-algebra", resolved[1]["candidate_ids"][0])
        self.assertTrue(all(row["identity_authority"] for row in resolved))
        self.assertEqual(".knowledge/entries/measure.md", resolved[2]["matches"][0]["entry"])

    def test_lexical_overlap_never_creates_identity(self) -> None:
        lexical = resolve_concepts(self.view, ["countably additive"])[0]
        self.assertEqual("missing", lexical["status"])
        self.assertFalse(lexical["identity_authority"])
        result = search(self.view, "countably additive")[0]
        self.assertEqual("measure", result["node"]["id"])
        self.assertFalse(result["reasons"][0]["identity_authority"])

    def test_search_indexes_every_entry_text_field_including_evidence(self) -> None:
        node = node_record(
            "measure", "Authored handle", evidence="background " * 160 + "latequote",
            kind="definitionkind", summary="A summarytoken.", context="A contexttoken.",
            role="A roletoken.", prerequisites=["prereqtoken"], pending_prerequisites=["pendingtoken"],
            common_confusions=["Distinct from an estimator."], open_questions=["questiontoken?"],
        )
        view = memory_view([node])
        for query in ("latequote", "definitionkind", "summarytoken", "contexttoken", "roletoken",
                      "prereqtoken", "pendingtoken", "estimator", "questiontoken"):
            with self.subTest(query=query):
                result = search(view, query)[0]
                self.assertEqual("measure", result["node"]["id"])
                self.assertEqual("lexical", result["reasons"][0]["method"])
                self.assertEqual("missing", resolve_concepts(view, [query])[0]["status"])
        self.assertEqual([], search(view, "notes"))
        self.assertEqual([], search(view, "absent " * 128 + "latequote"))

    def test_search_bm25_prefers_rare_terms_to_common_terms(self) -> None:
        view = memory_view([
            labelled(f"term-{index}", "common " + ("rare" if index == 3 else "filler"))
            for index in range(4)
        ])
        self.assertEqual("term-3", search(view, "common rare")[0]["node"]["id"])
        rare_score = search(view, "rare")[0]["reasons"][0]["score"]
        common_score = search(view, "common")[0]["reasons"][0]["score"]
        self.assertGreater(rare_score, common_score)

    def test_search_bm25_normalizes_length_and_saturates_repetition(self) -> None:
        view = memory_view([
            labelled("short", "target " + "filler " * 19),
            labelled("long", "target " + "filler " * 99),
            labelled("repeated", "target " * 10 + "filler " * 10),
        ])
        results = search(view, "target")
        scores = {row["node"]["id"]: row["reasons"][0]["score"] for row in results}
        self.assertGreater(scores["short"], scores["long"])
        self.assertGreater(scores["repeated"], scores["short"])
        self.assertLess(scores["repeated"], 3 * scores["short"])
        self.assertEqual(["repeated", "short", "long"], [row["node"]["id"] for row in results])

    def test_search_normalizes_unicode_and_compound_words_with_stable_ties(self) -> None:
        view = memory_view([labelled(node_id, "Ｃｒｏｓｓ-entropy σ-algebra") for node_id in ("tie-b", "tie-a")])
        for query in ("cross entropy", "cross-entropy", "σ algebra"):
            with self.subTest(query=query):
                self.assertEqual(["tie-a", "tie-b"], [row["node"]["id"] for row in search(view, query)])

    def test_search_matches_cjk_sub_words_inside_longer_terms(self) -> None:
        view = memory_view([
            node_record("measure-theory", "Measure theory", "测度论研究可测空间上的集合函数。"),
            node_record("probability", "Probability", "概率空间与随机变量。"),
        ])
        for query in ("测度", "度论", "测度论"):
            with self.subTest(query=query):
                self.assertEqual(["measure-theory"], [row["node"]["id"] for row in search(view, query)])
        self.assertEqual(["measure-theory"], [row["node"]["id"] for row in search(view, "measure theory")])
        self.assertEqual([], search(view, "theorem"))

    def test_bfs_get_and_ppr_use_accepted_edges(self) -> None:
        neighborhood = expand(self.view, ["sigma-algebra"], direction="out",
                              edge_types=["prerequisite-for"], max_depth=2)
        self.assertEqual(["sigma-algebra", "measure", "absolute-continuity"],
                         [row["node"]["id"] for row in neighborhood["nodes"]])
        record = get(self.view, "measure")
        self.assertEqual({"node", "incoming", "outgoing"}, set(record))
        self.assertEqual(2, len(record["outgoing"]) + len(record["incoming"]))
        ranking = personalized_pagerank(self.view, {"sigma-algebra": 1.0}, edge_types=["prerequisite-for"])
        self.assertEqual({"sigma-algebra", "measure", "absolute-continuity"},
                         {row["node"]["id"] for row in ranking["results"]})
        for bad in ("Measure", "missing"):
            with self.subTest(bad=bad), self.assertRaises(QueryError):
                get(self.view, bad)

    def test_ppr_honors_direction_and_reports_only_accepted_seeds(self) -> None:
        outgoing = personalized_pagerank(
            self.view, {"sigma-algebra": 2.0, "measure": 0.0, "missing": 1.0},
            edge_types=["prerequisite-for"], direction="out",
        )
        incoming = personalized_pagerank(self.view, {"sigma-algebra": 1.0},
                                         edge_types=["prerequisite-for"], direction="in")
        outgoing_scores = {row["node"]["id"]: row["score"] for row in outgoing["results"]}
        incoming_scores = {row["node"]["id"]: row["score"] for row in incoming["results"]}
        self.assertEqual({"sigma-algebra": 2.0}, outgoing["seeds"])
        self.assertGreater(outgoing_scores["measure"], 0.0)
        self.assertNotIn("measure", incoming_scores)
        with self.assertRaisesRegex(QueryError, "1 to 128"):
            personalized_pagerank(self.view, {f"seed-{index}": 1.0 for index in range(129)})

    def test_ppr_omits_zero_score_disconnected_nodes(self) -> None:
        view = memory_view(
            [labelled(node_id, node_id, node_id.title()) for node_id in ("seed-one", "node-one", "seed-two", "node-two")],
            [edge_record("seed-one", "prerequisite-for", "node-one"),
             edge_record("seed-two", "prerequisite-for", "node-two")],
        )
        ranking = personalized_pagerank(view, {"seed-one": 1.0}, edge_types=["prerequisite-for"])
        self.assertEqual({"seed-one", "node-one"}, {row["node"]["id"] for row in ranking["results"]})

    def test_ppr_default_converges_on_two_cycle_with_error_certificate(self) -> None:
        view = memory_view(fixture_nodes()[:2], [
            fixture_edges()[0], edge_record("measure", "implies", "sigma-algebra", evidence="Cycle fixture only."),
        ])
        ranking = personalized_pagerank(view, {"sigma-algebra": 1.0})
        scores = {row["node"]["id"]: row["score"] for row in ranking["results"]}
        actual_error = abs(scores["sigma-algebra"] - 1 / 1.85) + abs(scores["measure"] - .85 / 1.85)
        self.assertTrue(ranking["converged"])
        self.assertGreater(ranking["iterations"], 60)
        self.assertLessEqual(ranking["iterations"], 256)
        self.assertEqual(ranking["iterations"], ranking["used_iterations"])
        self.assertLessEqual(ranking["l1_residual"], 1e-10)
        self.assertLessEqual(actual_error, ranking["stationary_error_bound"] + 1e-14)
        self.assertAlmostEqual(1.0, ranking["probability_mass"], places=14)
        self.assertEqual(2, ranking["allowed_edge_count"])
        approximate = personalized_pagerank(view, {"sigma-algebra": 1.0}, max_iterations=1)
        self.assertFalse(approximate["converged"])
        self.assertEqual(1, approximate["used_iterations"])
        self.assertGreater(approximate["l1_residual"], 1e-10)
        self.assertTrue(math.isfinite(approximate["stationary_error_bound"]))
        self.assertEqual(2, len(approximate["results"]))

    def test_ppr_dangling_and_disconnected_nodes_preserve_seed_mass(self) -> None:
        view = memory_view(fixture_nodes(), [fixture_edges()[0]])
        ranking = personalized_pagerank(view, {"sigma-algebra": 1.0})
        scores = {row["node"]["id"]: row["score"] for row in ranking["results"]}
        self.assertEqual({"sigma-algebra", "measure"}, set(scores))
        self.assertEqual(2, ranking["reachable_node_count"])
        self.assertEqual(1, ranking["allowed_edge_count"])
        self.assertAlmostEqual(1.0, sum(scores.values()), places=14)
        self.assertAlmostEqual(1 / 1.85, scores["sigma-algebra"], places=9)
        self.assertAlmostEqual(.85 / 1.85, scores["measure"], places=9)
        isolated = personalized_pagerank(view, {"absolute-continuity": 1.0})
        self.assertEqual(1, isolated["reachable_node_count"])
        self.assertEqual(0, isolated["allowed_edge_count"])
        self.assertEqual(1.0, isolated["results"][0]["score"])
        self.assertEqual(0.0, isolated["l1_residual"])

    def test_ppr_seed_weight_scaling_avoids_overflow_and_underflow(self) -> None:
        view = memory_view(fixture_nodes()[:2])
        for weight in (1e308, 5e-324):
            with self.subTest(weight=weight):
                ranking = personalized_pagerank(view, {"sigma-algebra": weight, "measure": weight})
                self.assertEqual([.5, .5], [row["score"] for row in ranking["results"]])
                self.assertEqual(1.0, ranking["probability_mass"])
                self.assertTrue(ranking["converged"])
                self.assertEqual(0.0, ranking["l1_residual"])
        for damping in (5e-324, math.nextafter(1.0, 0.0)):
            ranking = personalized_pagerank(view, {"sigma-algebra": 1e308, "measure": 1e-308}, damping=damping)
            self.assertEqual(1.0, ranking["probability_mass"])
            self.assertTrue(math.isfinite(ranking["stationary_error_bound"]))

    def test_ppr_rejects_nonfinite_and_boolean_numeric_parameters(self) -> None:
        invalid = (
            {"damping": float("nan")}, {"damping": float("inf")}, {"damping": True},
            {"damping": 0.0}, {"damping": 1.0}, {"damping": 10 ** 1000},
            {"tolerance": float("nan")}, {"tolerance": float("inf")}, {"tolerance": True},
            {"tolerance": 0.0}, {"tolerance": 10 ** 1000},
            {"max_iterations": True}, {"max_iterations": 1.5}, {"max_iterations": 0},
            {"max_depth": True}, {"max_depth": -1}, {"max_depth": 9},
            {"edge_policy": "reviewed"},
        )
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(QueryError):
                personalized_pagerank(self.view, {"sigma-algebra": 1.0}, **policy)
        for weight in (True, False, float("nan"), float("inf"), "1", 10 ** 1000):
            with self.subTest(weight=weight), self.assertRaisesRegex(QueryError, "finite non-boolean"):
                personalized_pagerank(self.view, {"sigma-algebra": weight})

    def test_ppr_max_depth_bounds_the_induced_graph_and_boundary_mass(self) -> None:
        zero = personalized_pagerank(self.view, {"sigma-algebra": 1.0}, max_depth=0)
        one = personalized_pagerank(self.view, {"sigma-algebra": 1.0}, max_depth=1)
        full = personalized_pagerank(self.view, {"sigma-algebra": 1.0})
        reverse = personalized_pagerank(self.view, {"absolute-continuity": 1.0}, max_depth=1, direction="in")
        self.assertEqual(["sigma-algebra"], [row["node"]["id"] for row in zero["results"]])
        self.assertEqual(0, zero["allowed_edge_count"])
        self.assertEqual(1.0, zero["results"][0]["score"])
        self.assertEqual({"sigma-algebra", "measure"}, {row["node"]["id"] for row in one["results"]})
        self.assertEqual(1, one["allowed_edge_count"])
        self.assertEqual(3, full["reachable_node_count"])
        self.assertEqual({"absolute-continuity", "measure"}, {row["node"]["id"] for row in reverse["results"]})
        self.assertEqual("induced-subgraph-dangling-to-seeds", one["policy"]["depth_boundary"])
        for ranking in (zero, one, full, reverse):
            self.assertAlmostEqual(1.0, ranking["probability_mass"], places=14)
            self.assertTrue(ranking["converged"])

    def test_graph_high_confidence_gate_reads_declared_confidence(self) -> None:
        view = memory_view(
            [labelled(node_id, node_id, node_id.title()) for node_id in ("seed", "high", "unverified")],
            [edge_record("seed", "implies", "high"),
             edge_record("seed", "implies", "unverified", confidence="unverified")],
        )
        every = expand(view, ["seed"], direction="out")
        gated = expand(view, ["seed"], direction="out", edge_policy="high-confidence")
        ppr = personalized_pagerank(view, {"seed": 1.0}, edge_policy="high-confidence")
        self.assertEqual({"seed", "high", "unverified"}, {row["node"]["id"] for row in every["nodes"]})
        self.assertEqual("all", every["policy"]["edge_policy"])
        self.assertEqual({"seed", "high"}, {row["node"]["id"] for row in gated["nodes"]})
        self.assertEqual({"seed", "high"}, {row["node"]["id"] for row in ppr["results"]})
        self.assertEqual("declared-high-with-evidence", gated["policy"]["confidence_gate"])
        self.assertEqual(1, ppr["allowed_edge_count"])

    def test_contrasts_are_symmetric_and_paths_preserve_authored_direction(self) -> None:
        edge = edge_record("sigma-algebra", "contrasts-with", "measure", evidence="Fixture comparison.")
        view = memory_view(fixture_nodes()[:2], [edge])
        for seed, direction, traversal in (("measure", "out", "incoming"), ("sigma-algebra", "in", "outgoing")):
            with self.subTest(seed=seed, direction=direction):
                result = expand(view, [seed], direction=direction)
                other = next(row for row in result["nodes"] if not row["seed"])
                self.assertEqual(2, len(result["nodes"]))
                self.assertEqual([edge], result["edges"])
                self.assertEqual([{"source": "sigma-algebra", "relation": "contrasts-with",
                                   "target": "measure", "direction": traversal}], other["path"])
                ppr = personalized_pagerank(view, {seed: 1.0}, direction=direction, max_depth=1)
                self.assertEqual(2, ppr["reachable_node_count"])
                self.assertEqual(1, ppr["allowed_edge_count"])
        with self.assertRaisesRegex(QueryError, "edge_policy"):
            expand(view, ["measure"], edge_policy="reviewed")

    def test_cjk_token_estimate_is_utf8_conservative_and_self_consistent(self) -> None:
        value = {"text": "测度" * 20}
        serialized = canonical_json(value)
        self.assertEqual(len(serialized.encode("utf-8")), estimate_tokens(value))
        self.assertGreater(estimate_tokens(value), len(serialized))
        bundle = context(self.view, ["measure"], token_budget=2000)
        self.assertEqual(CONTEXT_SCHEMA, bundle["schema"])
        self.assertEqual({"schema", "nodes", "edges", "omissions", "budget"}, set(bundle))
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 2000)

    def test_context_filters_edges_by_relation(self) -> None:
        edges = [*fixture_edges(), edge_record("sigma-algebra", "implies", "measure", evidence="Fixture implication.")]
        view = memory_view(fixture_nodes(), edges)
        selected = ["sigma-algebra", "measure"]
        self.assertEqual([], context(view, selected, edge_types=[], token_budget=5000)["edges"])
        prerequisite = context(view, selected, edge_types=["prerequisite-for"], token_budget=5000)
        implication = context(view, selected, edge_types=["implies"], token_budget=5000)
        self.assertEqual(["prerequisite-for"], [edge["relation"] for edge in prerequisite["edges"]])
        self.assertEqual(["implies"], [edge["relation"] for edge in implication["edges"]])
        one = estimate_tokens(context(view, selected[:1], token_budget=5000))
        tight = context(view, selected, token_budget=one + 80)
        self.assertEqual(["sigma-algebra"], [node["id"] for node in tight["nodes"]])
        self.assertEqual([{"kind": "node", "id": "measure", "reason": "token-budget"}], tight["omissions"])


class LoadedViewTest(unittest.TestCase):
    def test_status_counts_entries_and_relations(self) -> None:
        fixture = make_fixture(self)
        fixture.write_source("notes/measure.txt", "Sigma algebra.\nMeasure.\nContinuity.\n")
        fixture.add_entry("sigma-algebra", "Sigma algebra", "notes/measure.txt", 1)
        fixture.add_entry("measure", "Measure", "notes/measure.txt", 2)
        fixture.add_entry("absolute-continuity", "Absolute continuity", "notes/measure.txt", 3)
        for edge in fixture_edges():
            fixture.add_edge(edge["source"], edge["relation"], edge["target"])
        view = GraphView.load(fixture.root, fixture.registry)
        status = query_status(view)
        self.assertEqual(status, validate_contract(status))
        self.assertEqual({"schema": "kgdistiller-query-status-v1",
                          "counts": {"entries": 3, "edges": 2},
                          "relations": {"prerequisite-for": 2}}, status)


if __name__ == "__main__":
    unittest.main()
