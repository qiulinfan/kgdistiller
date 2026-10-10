from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.query import CONTEXT_SCHEMA, context, estimate_tokens
from kgdistiller.retrieval import (
    RETRIEVAL_PLAN_SCHEMA,
    SEARCH_EXECUTION_SCHEMA,
    SEARCH_RESULT_SCHEMA,
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    load_retrieval_plan,
    query_retrieval_plan,
)
from tests.knowledge_fixture import edge_record, memory_view, node_record
from tests.test_query import fixture_edges, fixture_nodes


def retrieval_plan() -> dict:
    return {
        "schema": "kgdistiller-retrieval-plan-v1",
        "question": "How does a measure depend on a sigma algebra?",
        "identity_queries": ["西格玛代数"],
        "lexical_queries": ["countably additive"],
        "graph": {
            "seed_ids": [],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 2,
            "strategy": "hybrid",
        },
        "limit": 20,
    }


class RetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = memory_view(fixture_nodes(), fixture_edges())

    def test_v1_plan_forbids_semantic_queries_and_is_bounded(self) -> None:
        plan = query_retrieval_plan("measure")
        self.assertEqual(RETRIEVAL_PLAN_SCHEMA, plan["schema"])
        self.assertNotIn("semantic_queries", plan)
        invalid = retrieval_plan() | {"semantic_queries": []}
        with self.assertRaisesRegex(RetrievalError, "requires exactly"):
            execute_retrieval_plan(self.view, invalid)

        with tempfile.TemporaryDirectory(prefix="kgdistiller-plan-") as raw:
            path = Path(raw) / "plan.json"
            path.write_text(json.dumps(retrieval_plan()), encoding="utf-8")
            self.assertEqual(retrieval_plan(), load_retrieval_plan(path))

    def test_identity_lexical_graph_ppr_fusion_and_ambiguity_metadata(self) -> None:
        execution = execute_retrieval_plan(self.view, retrieval_plan())

        self.assertEqual(SEARCH_EXECUTION_SCHEMA, execution["schema"])
        self.assertEqual(SEARCH_RESULT_SCHEMA, execution["result"]["schema"])
        self.assertEqual("alias", execution["identity_resolutions"][0]["status"])
        self.assertTrue(execution["identity_resolutions"][0]["identity_authority"])
        self.assertNotIn("semantic", execution["result"]["lanes"])
        self.assertEqual("enabled", execution["result"]["lanes"]["ppr"]["status"])
        self.assertNotIn("reason", execution["result"]["lanes"]["ppr"])
        self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
        measure = next(row for row in execution["result"]["results"] if row["node_id"] == "measure")
        self.assertIn("lexical", measure["lanes"])
        self.assertIn("graph", measure["lanes"])
        self.assertIn("ppr", measure["lanes"])

    def test_identity_duplicates_keep_best_rank_and_exact_precedes_alias(self) -> None:
        view = memory_view([
            node_record("alpha", "Alpha", aliases=["Alpha alias"]),
            node_record("beta", "Beta", aliases=["Beta alias"]),
        ])
        plan = retrieval_plan()
        plan["lexical_queries"] = []
        plan["graph"].update(
            {"seed_ids": [], "edge_types": [], "max_depth": 0, "strategy": "bfs"}
        )

        plan["identity_queries"] = ["Alpha alias", "Beta", "Alpha"]
        duplicate = execute_retrieval_plan(view, plan)
        duplicate_rows = {
            row["node_id"]: row for row in duplicate["result"]["results"]
        }
        self.assertEqual(1, duplicate_rows["alpha"]["lanes"]["identity"]["rank"])
        self.assertEqual(2, duplicate_rows["beta"]["lanes"]["identity"]["rank"])

        plan["identity_queries"] = ["Beta alias", "Alpha"]
        ordered = execute_retrieval_plan(view, plan)
        self.assertEqual(
            ["alpha", "beta"],
            [row["node_id"] for row in ordered["result"]["results"]],
        )

    def test_combined_graph_seeds_over_128_fail_instead_of_truncating(self) -> None:
        nodes = [node_record(f"node-{index:03d}", f"Node {index:03d}") for index in range(129)]
        view = memory_view(nodes)
        plan = retrieval_plan()
        plan["identity_queries"] = ["Node 128"]
        plan["lexical_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": [node["id"] for node in nodes[:128]],
                "edge_types": [],
                "max_depth": 0,
                "strategy": "bfs",
            }
        )
        plan["limit"] = 500

        with self.assertRaisesRegex(RetrievalError, "exceeds 128"):
            execute_retrieval_plan(view, plan)

    def test_ppr_evidence_uses_the_seeds_accepted_by_ppr(self) -> None:
        plan = retrieval_plan()
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": ["sigma-algebra", "measure"],
                "strategy": "ppr",
            }
        )
        ranking = {
            "seeds": {"measure": 1.0},
            "results": [
                {"rank": 1, "score": 1.0, "node": self.view.nodes["measure"]}
            ],
        }

        with patch("kgdistiller.retrieval.personalized_pagerank", return_value=ranking):
            execution = execute_retrieval_plan(self.view, plan)

        result = execution["result"]
        self.assertEqual(1, result["lanes"]["ppr"]["seeds"])
        self.assertEqual(
            [{"lane": "ppr", "seed_id": "measure"}],
            result["results"][0]["seed_evidence"],
        )

    def test_ppr_evidence_is_attributed_within_disconnected_components(self) -> None:
        nodes = [
            node_record(node_id, node_id.replace("-", " ").title())
            for node_id in ("seed-one", "node-one", "seed-two", "node-two")
        ]
        edges = [
            edge_record("seed-one", "prerequisite-for", "node-one", evidence="First component."),
            edge_record("seed-two", "prerequisite-for", "node-two", evidence="Second component."),
        ]
        view = memory_view(nodes, edges)
        plan = retrieval_plan()
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": ["seed-one", "seed-two"],
                "edge_types": ["prerequisite-for"],
                "strategy": "ppr",
            }
        )

        execution = execute_retrieval_plan(view, plan)
        by_id = {
            row["node_id"]: row for row in execution["result"]["results"]
        }

        for suffix in ("one", "two"):
            expected = [{"lane": "ppr", "seed_id": f"seed-{suffix}"}]
            self.assertEqual(expected, by_id[f"seed-{suffix}"]["seed_evidence"])
            self.assertEqual(expected, by_id[f"node-{suffix}"]["seed_evidence"])

    def test_context_preserves_question_generation_and_budget(self) -> None:
        plan = retrieval_plan()
        execution = execute_retrieval_plan(self.view, plan)
        bundle = build_context_from_execution(
            self.view, execution, plan=plan, token_budget=2000
        )

        self.assertEqual(plan["question"], bundle["question"])
        self.assertEqual(CONTEXT_SCHEMA, bundle["schema"])
        self.assertEqual("kgdistiller-context-bundle-v1", bundle["schema"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 2000)
        smaller = memory_view([node for node in fixture_nodes() if node["id"] != "measure"])
        with self.assertRaisesRegex(RetrievalError, "stale-execution"):
            build_context_from_execution(smaller, execution, plan=plan)

    def test_context_rejects_nested_result_tampering_and_plan_mismatch(self) -> None:
        plan = retrieval_plan()
        execution = execute_retrieval_plan(self.view, plan)
        tampered = copy.deepcopy(execution)
        tampered["result"]["lanes"]["semantic"] = {
            "status": "enabled",
            "queries": 1,
            "results": 1,
        }
        with self.assertRaisesRegex(RetrievalError, "invalid-execution"):
            build_context_from_execution(self.view, tampered, plan=plan)
        with self.assertRaisesRegex(RetrievalError, "invalid-plan"):
            build_context_from_execution(self.view, execution, plan=plan | {"filters": {}})

    def test_context_token_estimate_reaches_fixed_point_before_budget_check(self) -> None:
        plan = retrieval_plan()
        plan["question"] = "q" * 748
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {"seed_ids": [], "edge_types": [], "max_depth": 0, "strategy": "bfs"}
        )
        execution = execute_retrieval_plan(self.view, plan)

        def fits(budget: int) -> bool:
            try:
                build_context_from_execution(self.view, execution, plan=plan, token_budget=budget)
            except RetrievalError as error:
                self.assertIn("budget-too-small", str(error))
                return False
            return True

        minimum = next(budget for budget in range(900, 1100) if fits(budget))
        self.assertTrue(all(fits(budget) for budget in range(minimum, minimum + 20)))
        bundle = build_context_from_execution(
            self.view, execution, plan=plan, token_budget=minimum
        )
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        self.assertEqual(minimum, bundle["budget"]["estimated_tokens"])

    def test_context_metadata_preserves_complete_node_over_omission_details(self) -> None:
        selected = node_record(
            "selected-node", "Shared selected", "The conditions must remain complete.",
            evidence="A complete definition. " + "x" * 3200,
            prerequisites=["The domain is a sigma algebra.", "The function is countably additive."],
        )
        nodes = [selected]
        for index in range(24):
            omitted = copy.deepcopy(selected)
            omitted.update({"id": f"oversized-{index:02d}", "label": f"Shared omitted {index}", "evidence": "y" * 7000})
            nodes.append(omitted)
        view = memory_view(nodes)
        plan = retrieval_plan()
        plan["question"] = "What exact conditions apply? " + "q" * 600
        plan["identity_queries"] = ["selected-node"]
        plan["lexical_queries"] = ["Shared"]
        plan["graph"].update(edge_types=[], max_depth=0, strategy="bfs")
        plan["limit"] = 25
        execution = execute_retrieval_plan(view, plan)
        before_metadata = context(view, [row["node_id"] for row in execution["result"]["results"]], token_budget=6000)
        self.assertEqual(["selected-node"], [node["id"] for node in before_metadata["nodes"]])
        self.assertGreater(len(before_metadata["omissions"]), 10)

        bundle = build_context_from_execution(view, execution, plan=plan, token_budget=6000)

        self.assertEqual([view.nodes["selected-node"]], bundle["nodes"])
        self.assertEqual(selected["prerequisites"], bundle["nodes"][0]["prerequisites"])
        self.assertEqual(plan["question"], bundle["question"])
        self.assertEqual(SEARCH_EXECUTION_SCHEMA, bundle["search_execution_schema"])
        self.assertEqual(SEARCH_RESULT_SCHEMA, bundle["search_result_schema"])
        self.assertTrue(bundle["omissions"])
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        self.assertLessEqual(estimate_tokens(bundle), 6000)

    def test_context_skips_node_that_cannot_fit_with_required_bindings(self) -> None:
        large = node_record("large-node", "Measure", evidence="x" * 5450)
        small = node_record("small-node", "Measure", evidence="A complete smaller definition.",
                            prerequisites=["Finite measure.", "Same measurable domain."])
        view = memory_view([large, small])
        plan = retrieval_plan()
        plan["question"] = "Preserve every condition. " + "q" * 700
        plan["identity_queries"] = ["large-node", "small-node"]
        plan["lexical_queries"] = []
        plan["graph"].update(edge_types=[], max_depth=0, strategy="bfs")
        execution = execute_retrieval_plan(view, plan)
        self.assertEqual(["large-node"], [node["id"] for node in context(view, ["large-node", "small-node"], token_budget=6000)["nodes"]])

        bundle = build_context_from_execution(view, execution, plan=plan, token_budget=6000)

        self.assertEqual([view.nodes["small-node"]], bundle["nodes"])
        self.assertIn({"kind": "node", "id": "large-node", "reason": "token-budget"}, bundle["omissions"])
        self.assertEqual(plan["question"], bundle["question"])
        self.assertLessEqual(estimate_tokens(bundle), 6000)

    def test_context_omission_details_do_not_block_later_fitting_node(self) -> None:
        small = node_record("late-fitting-node", "Measure", evidence="x" * 3700,
                            prerequisites=["Finite measure.", "Same measurable domain."])
        nodes = []
        for index in range(20):
            large = copy.deepcopy(small)
            large.update({"id": f"oversized-{index:02d}", "evidence": "y" * 7000})
            nodes.append(large)
        nodes.append(small)
        view = memory_view(nodes)
        plan = retrieval_plan()
        plan["question"] = "Preserve the late candidate's conditions. " + "q" * 600
        plan["identity_queries"] = [node["id"] for node in nodes]
        plan["lexical_queries"] = []
        plan["graph"].update(edge_types=[], max_depth=0, strategy="bfs")
        plan["limit"] = len(nodes)
        execution = execute_retrieval_plan(view, plan)

        bundle = build_context_from_execution(view, execution, plan=plan, token_budget=6000)

        self.assertEqual([view.nodes["late-fitting-node"]], bundle["nodes"])
        self.assertTrue(bundle["omissions"])
        self.assertEqual(plan["question"], bundle["question"])
        self.assertLessEqual(estimate_tokens(bundle), 6000)

    def test_context_impossible_content_keeps_omission_or_fails_explicitly(self) -> None:
        oversized = node_record("oversized-node", "Measure", evidence="x" * 8000,
                                prerequisites=["Do not truncate this condition."])
        view = memory_view([oversized])
        plan = retrieval_plan()
        plan["identity_queries"] = ["oversized-node"]
        plan["lexical_queries"] = []
        plan["graph"].update(edge_types=[], max_depth=0, strategy="bfs")
        execution = execute_retrieval_plan(view, plan)
        bundle = build_context_from_execution(view, execution, plan=plan, token_budget=1000)
        self.assertEqual([], bundle["nodes"])
        self.assertIn({"kind": "node", "id": "oversized-node", "reason": "token-budget"}, bundle["omissions"])
        self.assertLessEqual(estimate_tokens(bundle), 1000)

        empty_plan = copy.deepcopy(plan)
        empty_plan["identity_queries"] = []
        empty_execution = execute_retrieval_plan(view, empty_plan)
        header = build_context_from_execution(view, empty_execution, plan=empty_plan, token_budget=1000)
        while True:
            size = estimate_tokens(header)
            if header["budget"] == {"token_budget": size, "estimated_tokens": size}:
                break
            header["budget"] = {"token_budget": size, "estimated_tokens": size}
        with self.assertRaisesRegex(RetrievalError, "budget-too-small"):
            build_context_from_execution(view, execution, plan=plan, token_budget=size)

    def test_context_metadata_budget_is_utf8_bounded_at_decimal_boundaries(self) -> None:
        plan = retrieval_plan()
        plan["question"] = "哪些定义条件必须完整保留？" * 8
        execution = execute_retrieval_plan(self.view, plan)
        for budget in (99, 100, 999, 1000, 1001, 1999, 2000, 9999, 10000, 10001):
            with self.subTest(budget=budget):
                try:
                    bundle = build_context_from_execution(self.view, execution, plan=plan, token_budget=budget)
                except RetrievalError as error:
                    self.assertEqual("context-failed", error.code)
                    self.assertIn("budget-too-small", str(error))
                    continue
                self.assertEqual(budget, bundle["budget"]["token_budget"])
                self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
                self.assertLessEqual(estimate_tokens(bundle), budget)
                self.assertEqual(plan["question"], bundle["question"])
                for node in bundle["nodes"]:
                    self.assertEqual(self.view.nodes[node["id"]], node)

    def test_context_obeys_plan_edge_types(self) -> None:
        view = memory_view(fixture_nodes(), [
            *fixture_edges(),
            edge_record("sigma-algebra", "implies", "measure", evidence="Fixture implication."),
        ])
        plan = retrieval_plan()
        plan["identity_queries"] = ["Sigma algebra", "Measure"]
        plan["lexical_queries"] = []
        plan["graph"].update(
            {"seed_ids": [], "edge_types": [], "max_depth": 0, "strategy": "bfs"}
        )

        execution = execute_retrieval_plan(view, plan)
        empty = build_context_from_execution(view, execution, plan=plan, token_budget=5000)
        self.assertEqual([], empty["edges"])

        plan["graph"]["edge_types"] = ["implies"]
        execution = execute_retrieval_plan(view, plan)
        implication = build_context_from_execution(view, execution, plan=plan, token_budget=5000)
        self.assertEqual(["implies"], [edge["relation"] for edge in implication["edges"]])


if __name__ == "__main__":
    unittest.main()
