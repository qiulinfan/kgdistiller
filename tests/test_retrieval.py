from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.contracts import sha256_json  # noqa: E402
from kgdistiller.query import GraphView, estimate_tokens  # noqa: E402
from kgdistiller.retrieval import (  # noqa: E402
    CONTEXT_SCHEMA,
    RETRIEVAL_PLAN_SCHEMA,
    SEARCH_EXECUTION_SCHEMA,
    SEARCH_RESULT_SCHEMA,
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    legacy_retrieval_plan,
    load_retrieval_plan,
)
from tests.test_query import fixture_nodes, fixture_snapshot, snapshot_with  # noqa: E402


def retrieval_plan() -> dict:
    return {
        "schema": "kgdistiller-retrieval-plan-v1",
        "question": "How does a measure depend on a sigma algebra?",
        "namespace": "personal",
        "identity_queries": ["西格玛代数"],
        "lexical_queries": ["countably additive"],
        "graph": {
            "seed_ids": [],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 2,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


class RetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = GraphView.from_snapshot(fixture_snapshot())

    def test_v1_plan_forbids_semantic_queries_and_is_bounded(self) -> None:
        plan = legacy_retrieval_plan("measure")
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
        self.assertEqual("degraded", execution["result"]["lanes"]["ppr"]["status"])
        self.assertEqual(
            "not-converged", execution["result"]["lanes"]["ppr"]["reason"]
        )
        self.assertEqual("sigma-algebra", execution["result"]["results"][0]["node_id"])
        measure = next(row for row in execution["result"]["results"] if row["node_id"] == "measure")
        self.assertIn("lexical", measure["lanes"])
        self.assertIn("graph", measure["lanes"])
        self.assertIn("ppr", measure["lanes"])

    def test_graph_lane_filters_neighbor_type_staleness_and_orphan_status(self) -> None:
        seed = copy.deepcopy(fixture_nodes()[0])
        active = copy.deepcopy(fixture_nodes()[1])
        field = copy.deepcopy(active)
        field.update({"id": "field-node", "type": "field", "label": "Field node"})
        stale = copy.deepcopy(active)
        stale.update({"id": "stale-node", "label": "Stale node"})
        stale["properties"]["curation_status"] = "needs-review"
        orphan = copy.deepcopy(active)
        orphan.update({"id": "orphan-node", "label": "Orphan node"})
        orphan["properties"]["source_status"] = "orphaned"
        orphan["provenance"]["active"] = False
        edges = [
            {
                "source": seed["id"],
                "relation": "derived-from",
                "target": node["id"],
                "evidence": f"Seed reaches {node['id']}.",
                "curation_status": "current",
            }
            for node in (active, field, stale, orphan)
        ]
        view = GraphView.from_snapshot(
            snapshot_with([seed, active, field, stale, orphan], edges)
        )
        plan = retrieval_plan()
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": [seed["id"]],
                "edge_types": ["derived-from"],
                "max_depth": 1,
                "strategy": "bfs",
            }
        )

        default = execute_retrieval_plan(view, plan)
        plan["filters"].update({"include_stale": True, "include_orphaned": True})
        inclusive = execute_retrieval_plan(view, plan)

        self.assertEqual(
            {"sigma-algebra", "measure"},
            {row["node_id"] for row in default["result"]["results"]},
        )
        self.assertEqual(
            {"sigma-algebra", "measure", "stale-node", "orphan-node"},
            {row["node_id"] for row in inclusive["result"]["results"]},
        )
        self.assertNotIn(
            "field-node", {row["node_id"] for row in inclusive["result"]["results"]}
        )

    def test_identity_duplicates_keep_best_rank_and_exact_precedes_alias(self) -> None:
        alpha = copy.deepcopy(fixture_nodes()[0])
        alpha.update({"id": "alpha", "label": "Alpha"})
        alpha["properties"]["aliases"] = ["Alpha alias"]
        beta = copy.deepcopy(fixture_nodes()[1])
        beta.update({"id": "beta", "label": "Beta"})
        beta["properties"]["aliases"] = ["Beta alias"]
        view = GraphView.from_snapshot(snapshot_with([alpha, beta], []))
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
        nodes = []
        for index in range(129):
            node = copy.deepcopy(fixture_nodes()[0])
            node.update({"id": f"node-{index:03d}", "label": f"Node {index:03d}"})
            node["properties"]["aliases"] = []
            nodes.append(node)
        view = GraphView.from_snapshot(snapshot_with(nodes, []))
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
        nodes = []
        for index, node_id in enumerate(("seed-one", "node-one", "seed-two", "node-two")):
            node = copy.deepcopy(fixture_nodes()[index % len(fixture_nodes())])
            node.update({"id": node_id, "label": node_id.replace("-", " ").title()})
            node["properties"]["aliases"] = []
            nodes.append(node)
        edges = [
            {
                "source": "seed-one",
                "relation": "prerequisite-for",
                "target": "node-one",
                "evidence": "First component.",
                "curation_status": "current",
            },
            {
                "source": "seed-two",
                "relation": "prerequisite-for",
                "target": "node-two",
                "evidence": "Second component.",
                "curation_status": "current",
            },
        ]
        view = GraphView.from_snapshot(snapshot_with(nodes, edges))
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
        self.assertEqual(self.view.snapshot["snapshot_sha256"], bundle["snapshot_sha256"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 2000)
        stale = dict(execution)
        stale["snapshot_sha256"] = "f" * 64
        with self.assertRaisesRegex(RetrievalError, "another graph generation"):
            build_context_from_execution(self.view, stale, plan=plan)

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

        different_plan = copy.deepcopy(plan)
        different_plan["question"] = "A different question"
        with self.assertRaisesRegex(RetrievalError, "does not belong"):
            build_context_from_execution(
                self.view, execution, plan=different_plan, token_budget=2000
            )

    def test_context_token_estimate_reaches_fixed_point_before_budget_check(self) -> None:
        plan = retrieval_plan()
        plan["question"] = "q" * 500
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {"seed_ids": [], "edge_types": [], "max_depth": 0, "strategy": "bfs"}
        )
        execution = execute_retrieval_plan(self.view, plan)

        with self.assertRaisesRegex(RetrievalError, "budget-too-small"):
            build_context_from_execution(
                self.view, execution, plan=plan, token_budget=1048
            )
        bundle = build_context_from_execution(
            self.view, execution, plan=plan, token_budget=1049
        )
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 1049)

    def test_context_obeys_plan_edge_types_and_stale_policy(self) -> None:
        snapshot = fixture_snapshot()
        field = {
            "id": "measure-theory",
            "type": "field",
            "label": "Measure theory",
            "text": "",
            "properties": {"aliases": []},
        }
        snapshot["nodes"].append(field)
        snapshot["edges"].append(
            {
                "source": field["id"],
                "relation": "contains",
                "target": "measure",
                "evidence": "Taxonomy membership.",
                "curation_status": "current",
            }
        )
        snapshot["graph"]["counts"]["nodes"] += 1
        snapshot["graph"]["counts"]["edges"] += 1
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        view = GraphView.from_snapshot(snapshot)
        plan = retrieval_plan()
        plan["identity_queries"] = ["Measure theory", "Measure"]
        plan["lexical_queries"] = []
        plan["filters"]["node_types"] = ["field", "knowledge"]
        plan["graph"].update(
            {"seed_ids": [], "edge_types": [], "max_depth": 0, "strategy": "bfs"}
        )

        execution = execute_retrieval_plan(view, plan)
        empty = build_context_from_execution(
            view, execution, plan=plan, token_budget=5000
        )
        self.assertEqual([], empty["edges"])

        plan["graph"]["edge_types"] = ["contains"]
        execution = execute_retrieval_plan(view, plan)
        taxonomy = build_context_from_execution(
            view, execution, plan=plan, token_budget=5000
        )
        self.assertEqual(["contains"], [edge["relation"] for edge in taxonomy["edges"]])

        stale_snapshot = copy.deepcopy(snapshot)
        stale_snapshot["edges"][0]["curation_status"] = "needs-review"
        stale_snapshot.pop("snapshot_sha256")
        stale_snapshot["snapshot_sha256"] = sha256_json(stale_snapshot)
        stale_view = GraphView.from_snapshot(stale_snapshot)
        plan["identity_queries"] = ["Sigma algebra", "Measure"]
        plan["graph"]["edge_types"] = ["prerequisite-for"]
        execution = execute_retrieval_plan(stale_view, plan)
        current_only = build_context_from_execution(
            stale_view, execution, plan=plan, token_budget=5000
        )
        self.assertEqual([], current_only["edges"])

        plan["filters"]["include_stale"] = True
        execution = execute_retrieval_plan(stale_view, plan)
        with_stale = build_context_from_execution(
            stale_view, execution, plan=plan, token_budget=5000
        )
        self.assertEqual(
            ["needs-review"],
            [edge["curation_status"] for edge in with_stale["edges"]],
        )


if __name__ == "__main__":
    unittest.main()
