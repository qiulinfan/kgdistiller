from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import (  # noqa: E402
    GRAPH_SCHEMA,
    GraphState,
    make_artifacts,
    write_artifacts,
)
from kgdistiller.alignment import (  # noqa: E402
    ALIGNMENT_REPORT_SCHEMA,
    ALIGNMENT_SCHEMA,
    make_reviewed_mapping,
)
from kgdistiller.contracts import canonical_json, sha256_json  # noqa: E402
from kgdistiller.contracts import validate_contract  # noqa: E402
from kgdistiller.query import (  # noqa: E402
    COMPARISON_SCHEMA,
    CONTEXT_SCHEMA,
    PROPOSAL_SCHEMA,
    SNAPSHOT_SCHEMA,
    GraphView,
    QueryError,
    align,
    compare,
    context,
    estimate_tokens,
    expand,
    get,
    personalized_pagerank,
    query_status,
    resolve_concepts,
    search,
    propose,
)


def fixture_nodes() -> list[dict]:
    return [
        {
            "id": "sigma-algebra",
            "type": "knowledge",
            "label": "Sigma algebra",
            "text": "A collection closed under countable union and complement.",
            "entry": {"summary": "A measurable-set system."},
            "properties": {
                "aliases": ["σ-algebra", "西格玛代数"],
                "curation_status": "current",
                "source_status": "active",
            },
            "provenance": {"authority": "notes/measure.md", "line": 2},
        },
        {
            "id": "measure",
            "type": "knowledge",
            "label": "Measure",
            "text": "A countably additive set function.",
            "properties": {
                "aliases": ["测度"],
                "curation_status": "current",
                "source_status": "active",
            },
            "provenance": {"authority": "notes/measure.md", "line": 6},
        },
        {
            "id": "absolute-continuity",
            "type": "knowledge",
            "label": "Absolute continuity",
            "text": "Absolute continuity (AC) is defined relative to a measure.",
            "properties": {
                "aliases": [],
                "curation_status": "current",
                "source_status": "active",
            },
            "provenance": {"authority": "notes/measure.md", "line": 10},
        },
    ]


def fixture_edges() -> list[dict]:
    return [
        {
            "source": "sigma-algebra",
            "relation": "prerequisite-for",
            "target": "measure",
            "evidence": "A measure is defined on a sigma algebra.",
            "curation_status": "current",
        },
        {
            "source": "measure",
            "relation": "prerequisite-for",
            "target": "absolute-continuity",
            "evidence": "Absolute continuity compares measures.",
            "curation_status": "current",
        },
    ]


def fixture_snapshot() -> dict:
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "namespace": "personal",
        "graph": {
            "schema": GRAPH_SCHEMA,
            "sha256": "a" * 64,
            "counts": {"nodes": 3, "edges": 2, "references": 1},
        },
        "nodes": fixture_nodes(),
        "edges": fixture_edges(),
        "references": [
            {
                "id": "ref-measure",
                "target": "measure",
                "authority": "notes/continuity.typ",
                "line": 4,
                "context": "uses measure",
            }
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def snapshot_with(nodes: list[dict], edges: list[dict]) -> dict:
    payload = fixture_snapshot()
    payload["nodes"] = copy.deepcopy(nodes)
    payload["edges"] = copy.deepcopy(edges)
    payload["references"] = []
    payload["graph"]["counts"] = {
        "nodes": len(nodes),
        "edges": len(edges),
        "references": 0,
    }
    payload.pop("snapshot_sha256")
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def candidate_snapshot_with(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    payload = snapshot_with(nodes, edges or [])
    payload["namespace"] = "paper:demo"
    payload.pop("snapshot_sha256")
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def alignment_mapping(
    candidate: dict,
    target: dict,
    *,
    predicate: str,
    status: str,
    subject_namespace: str = "paper:demo",
) -> dict:
    return make_reviewed_mapping(
        subject_namespace=subject_namespace,
        subject_node=candidate,
        predicate=predicate,
        object_namespace="personal",
        object_node=target,
        status=status,
        justification=f"Fixture {status} {predicate} decision.",
        evidence=f"Fixture evidence for {status} {predicate}.",
    )


def write_fixture_graph(root: Path) -> Path:
    graph = root / "knowledge" / "graph"
    nodes = fixture_nodes()
    edges = fixture_edges()
    state = GraphState(
        {node["id"]: copy.deepcopy(node) for node in nodes},
        {(edge["source"], edge["relation"], edge["target"]): copy.deepcopy(edge) for edge in edges},
        [
            {
                "id": "ref-measure",
                "target": "measure",
                "authority": "notes/continuity.typ",
                "line": 4,
                "context": "uses measure",
            }
        ],
        {},
    )
    write_artifacts(graph, make_artifacts(state, {}))
    return graph


class QueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = GraphView.from_snapshot(fixture_snapshot())

    def test_forged_snapshot_rejects_node_output_bounds(self) -> None:
        for field, value, message in (
            ("id", "a" * 257, "node ID"),
            ("label", "L" * 1025, "node label"),
        ):
            with self.subTest(field=field):
                node = copy.deepcopy(fixture_nodes()[0])
                node[field] = value
                snapshot = snapshot_with([node], [])
                with self.assertRaisesRegex(QueryError, message):
                    GraphView.from_snapshot(snapshot)

    def test_query_view_refuses_unknown_snapshot_graph_and_alignment_contracts(
        self,
    ) -> None:
        legacy_snapshot = fixture_snapshot()
        legacy_snapshot["schema"] = "legacy-agent-snapshot-v0"
        legacy_snapshot.pop("snapshot_sha256")
        legacy_snapshot["snapshot_sha256"] = sha256_json(legacy_snapshot)
        with self.assertRaisesRegex(QueryError, SNAPSHOT_SCHEMA):
            GraphView.from_snapshot(legacy_snapshot)

        legacy_graph = fixture_snapshot()
        legacy_graph["graph"]["schema"] = "legacy-graph-v0"
        legacy_graph.pop("snapshot_sha256")
        legacy_graph["snapshot_sha256"] = sha256_json(legacy_graph)
        with self.assertRaisesRegex(QueryError, GRAPH_SCHEMA):
            GraphView.from_snapshot(legacy_graph)

        with self.assertRaisesRegex(ValueError, ALIGNMENT_SCHEMA):
            GraphView.from_snapshot(
                fixture_snapshot(),
                alignments={"schema": "legacy-alignments-v0", "mappings": []},
            )

    def test_snapshot_requires_source_grounded_candidate_nodes(self) -> None:
        node = copy.deepcopy(fixture_nodes()[0])
        node.pop("provenance")
        snapshot = snapshot_with([node], [])
        snapshot["namespace"] = "paper:demo"
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        with self.assertRaisesRegex(QueryError, "bounded provenance"):
            GraphView.from_snapshot(snapshot)

        personal = snapshot_with([node], [])
        with self.assertRaisesRegex(QueryError, "bounded provenance"):
            GraphView.from_snapshot(personal)

    def test_snapshot_allows_personal_taxonomy_nodes_without_authority(self) -> None:
        field = {
            "id": "analysis",
            "type": "field",
            "label": "Analysis",
            "text": "",
            "properties": {"aliases": []},
        }
        view = GraphView.from_snapshot(snapshot_with([field], []))
        self.assertEqual("field", view.nodes["analysis"]["type"])

    def test_snapshot_rejects_malformed_provenance_and_status(self) -> None:
        for target, key, value, message in (
            ("provenance", "active", "yes", "provenance.active"),
            ("properties", "source_status", "fresh", "source status"),
            ("properties", "curation_status", 7, "curation status"),
        ):
            with self.subTest(target=target, key=key):
                node = copy.deepcopy(fixture_nodes()[0])
                node[target][key] = value
                with self.assertRaisesRegex(QueryError, message):
                    GraphView.from_snapshot(snapshot_with([node], []))

    def test_forged_snapshot_rejects_unsafe_graph_record_shapes(self) -> None:
        def resign(payload: dict) -> dict:
            payload.pop("snapshot_sha256", None)
            payload["snapshot_sha256"] = sha256_json(payload)
            return payload

        cases = (
            (
                "node-type",
                lambda payload: payload["nodes"][0].__setitem__("type", "document"),
                "unsupported type",
            ),
            (
                "node-properties",
                lambda payload: payload["nodes"][0].__setitem__("properties", []),
                "properties must be an object",
            ),
            (
                "edge-relation",
                lambda payload: payload["edges"][0].__setitem__("relation", "related-to"),
                "invalid edge",
            ),
            (
                "edge-evidence",
                lambda payload: payload["edges"][0].__setitem__("evidence", ""),
                "has no evidence",
            ),
            (
                "reference-id",
                lambda payload: payload["references"][0].__setitem__("id", ""),
                "reference ID",
            ),
            (
                "reference-authority",
                lambda payload: payload["references"][0].__setitem__("authority", ""),
                "authority is invalid",
            ),
            (
                "reference-location",
                lambda payload: payload["references"][0].pop("line"),
                "no bounded source location",
            ),
            (
                "diagnostics",
                lambda payload: payload["diagnostics"]["warnings"].append(
                    {"message": "missing code"}
                ),
                "diagnostics are invalid",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                payload = fixture_snapshot()
                mutate(payload)
                with self.assertRaisesRegex(QueryError, message):
                    GraphView.from_snapshot(resign(payload))

    def test_snapshot_collection_limits_are_enforced_before_view_construction(self) -> None:
        with patch("kgdistiller.query.MAX_SNAPSHOT_NODES", 2):
            with self.assertRaisesRegex(QueryError, "deterministic graph limits"):
                GraphView.from_snapshot(fixture_snapshot())

    def test_explicit_chinese_and_english_aliases_resolve_identity(self) -> None:
        resolved = resolve_concepts(
            self.view, ["SIGMA ALGEBRA", "西格玛代数", "测度"]
        )
        self.assertEqual(["exact", "alias", "alias"], [row["status"] for row in resolved])
        self.assertEqual("sigma-algebra", resolved[1]["candidate_ids"][0])
        self.assertTrue(all(row["identity_authority"] for row in resolved))

    def test_scoped_alias_and_lexical_overlap_never_create_identity(self) -> None:
        scoped = resolve_concepts(self.view, ["AC"])[0]
        lexical = resolve_concepts(self.view, ["countably additive"])[0]

        self.assertEqual("missing", scoped["status"])
        self.assertFalse(scoped["identity_authority"])
        self.assertEqual("scoped-alias", scoped["ranked_candidates"][0]["method"])
        self.assertEqual("missing", lexical["status"])
        self.assertFalse(lexical["identity_authority"])
        self.assertEqual("measure", search(self.view, "countably additive")[0]["node"]["id"])
        self.assertFalse(search(self.view, "countably additive")[0]["reasons"][0]["identity_authority"])

    def test_candidate_aliases_retrieve_but_never_establish_identity(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[1])
        candidate.update({"id": "paper-measure", "label": "Unrelated candidate"})
        candidate["properties"]["aliases"] = ["测度"]

        report = align(self.view, candidate_snapshot_with([candidate]))
        row = report["alignments"][0]

        self.assertEqual("unmatched", row["status"])
        self.assertIsNone(row["matched_target_id"])
        measure = next(
            item for item in row["candidates"] if item["target"]["id"] == "measure"
        )
        alias_signals = [
            signal
            for signal in measure["signals"]
            if signal.get("probe_source") == "candidate-alias"
        ]
        self.assertTrue(alias_signals)
        self.assertTrue(
            all(not signal["identity_authority"] for signal in alias_signals)
        )
        self.assertEqual("candidate", report["results"][0]["status"])

    def test_candidate_canonical_label_may_use_target_global_alias(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[1])
        candidate.update({"id": "paper-measure", "label": "测度"})
        candidate["properties"]["aliases"] = []

        report = align(self.view, candidate_snapshot_with([candidate]))

        self.assertEqual("matched", report["alignments"][0]["status"])
        self.assertEqual("measure", report["alignments"][0]["matched_target_id"])

    def test_fresh_reviewed_exact_alignment_establishes_identity(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[1])
        candidate.update({"id": "paper-measure", "label": "Paper-specific name"})
        candidate["properties"]["aliases"] = []
        mapping = alignment_mapping(
            candidate,
            self.view.nodes["measure"],
            predicate="exact-match",
            status="reviewed",
        )
        view = GraphView.from_snapshot(
            fixture_snapshot(),
            alignments={"schema": ALIGNMENT_SCHEMA, "mappings": [mapping]},
        )

        report = align(view, candidate_snapshot_with([candidate]))

        self.assertEqual("matched", report["alignments"][0]["status"])
        self.assertEqual("measure", report["alignments"][0]["matched_target_id"])
        self.assertTrue(
            report["results"][0]["registry_evidence"][0]["identity_authority"]
        )

    def test_fresh_negative_registry_decisions_override_nonreviewed_probes(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[0])
        candidate["properties"]["target_id"] = "measure"
        rejected_exact = alignment_mapping(
            candidate,
            self.view.nodes["measure"],
            predicate="exact-match",
            status="rejected",
        )
        reviewed_different = alignment_mapping(
            candidate,
            self.view.nodes["sigma-algebra"],
            predicate="different-from",
            status="reviewed",
        )
        registry = {
            "schema": ALIGNMENT_SCHEMA,
            "mappings": [reviewed_different, rejected_exact],
        }
        reversed_registry = {
            "schema": ALIGNMENT_SCHEMA,
            "mappings": list(reversed(registry["mappings"])),
        }
        snapshot = candidate_snapshot_with([candidate])
        report = align(
            GraphView.from_snapshot(fixture_snapshot(), alignments=registry), snapshot
        )
        reversed_report = align(
            GraphView.from_snapshot(
                fixture_snapshot(), alignments=reversed_registry
            ),
            snapshot,
        )
        row = report["alignments"][0]

        self.assertEqual(report, reversed_report)
        self.assertEqual("unmatched", row["status"])
        self.assertIsNone(row["matched_target_id"])
        self.assertEqual(
            ["measure", "sigma-algebra"], row["rejected_target_ids"]
        )
        self.assertEqual(
            row["rejected_target_ids"],
            report["results"][0]["rejected_target_ids"],
        )
        self.assertEqual(
            sorted(item["mapping_id"] for item in row["registry_evidence"]),
            [item["mapping_id"] for item in row["registry_evidence"]],
        )
        self.assertTrue(
            all(item["decision_fresh"] for item in row["registry_evidence"])
        )
        self.assertTrue(
            {"measure", "sigma-algebra"}.isdisjoint(
                item["target"]["id"] for item in row["candidates"]
            )
        )

    def test_stale_negative_mapping_does_not_suppress_identity(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[1])
        mapping = alignment_mapping(
            candidate,
            self.view.nodes["measure"],
            predicate="exact-match",
            status="rejected",
        )
        for endpoint in ("subject", "object"):
            with self.subTest(endpoint=endpoint):
                stale = copy.deepcopy(mapping)
                stale[endpoint]["node_sha256"] = "0" * 64
                view = GraphView.from_snapshot(
                    fixture_snapshot(),
                    alignments={
                        "schema": ALIGNMENT_SCHEMA,
                        "mappings": [stale],
                    },
                )

                report = align(view, candidate_snapshot_with([candidate]))
                row = report["alignments"][0]

                self.assertEqual("matched", row["status"])
                self.assertEqual("measure", row["matched_target_id"])
                self.assertEqual([], row["rejected_target_ids"])
                self.assertFalse(row["registry_evidence"][0]["decision_fresh"])

    def test_alignment_digest_binds_align_compare_and_proposal(self) -> None:
        candidate = copy.deepcopy(fixture_nodes()[1])
        candidate.update({"id": "paper-measure", "label": "Paper measure"})
        candidate["properties"]["aliases"] = []
        candidate_snapshot = candidate_snapshot_with([candidate])
        unrelated = copy.deepcopy(candidate)
        unrelated.update({"id": "unrelated", "label": "Unrelated"})
        registry_mapping = alignment_mapping(
            unrelated,
            self.view.nodes["measure"],
            predicate="exact-match",
            status="rejected",
            subject_namespace="paper:other",
        )
        changed_view = GraphView.from_snapshot(
            fixture_snapshot(),
            alignments={
                "schema": ALIGNMENT_SCHEMA,
                "mappings": [registry_mapping],
            },
        )

        baseline = align(self.view, candidate_snapshot)
        changed = align(changed_view, candidate_snapshot)
        comparison = compare(changed_view, candidate_snapshot)
        proposal = propose(changed_view, candidate_snapshot)

        self.assertEqual(ALIGNMENT_REPORT_SCHEMA, changed["schema"])
        self.assertEqual("kgdistiller-alignment-report-v1", changed["schema"])
        self.assertEqual(COMPARISON_SCHEMA, comparison["schema"])
        self.assertEqual("kgdistiller-graph-comparison-v1", comparison["schema"])
        self.assertEqual(PROPOSAL_SCHEMA, proposal["schema"])
        self.assertEqual("kgdistiller-agent-proposal-v1", proposal["schema"])
        self.assertEqual(baseline["alignments"], changed["alignments"])
        self.assertNotEqual(
            baseline["alignment_sha256"], changed["alignment_sha256"]
        )
        self.assertNotEqual(baseline["report_sha256"], changed["report_sha256"])
        self.assertEqual(
            sha256_json(changed_view.alignments), changed["alignment_sha256"]
        )
        self.assertEqual(
            changed["alignment_sha256"], comparison["alignment_sha256"]
        )
        self.assertEqual(
            changed["alignment_sha256"], proposal["alignment_sha256"]
        )

    def test_v1_comparison_and_proposal_are_bounded_review_outputs(self) -> None:
        matched = copy.deepcopy(fixture_nodes()[0])
        unmatched = copy.deepcopy(fixture_nodes()[1])
        unmatched.update({"id": "paper-only", "label": "Paper-only concept"})
        unmatched["properties"]["aliases"] = []
        edge = {
            "source": matched["id"],
            "relation": "derived-from",
            "target": unmatched["id"],
            "evidence": "The paper directly derives the new concept.",
            "curation_status": "current",
        }
        snapshot = candidate_snapshot_with([matched, unmatched], [edge])

        comparison = compare(self.view, snapshot)
        proposal = propose(self.view, snapshot)

        self.assertEqual(
            {"sigma-algebra": "matched", "paper-only": "unmatched"},
            {
                row["candidate"]["id"]: row["status"]
                for row in comparison["results"]
            },
        )
        self.assertEqual(["missing"], [row["status"] for row in comparison["edges"]])
        self.assertTrue(
            all(
                "missing" not in row and "conflicts" not in row
                for row in comparison["results"]
            )
        )
        self.assertEqual(comparison["results"], proposal["results"])
        self.assertEqual("review-new-node", proposal["operations"][0]["action"])
        self.assertFalse(proposal["delta_ready"])

    def test_bfs_get_and_ppr_use_trusted_edges(self) -> None:
        neighborhood = expand(
            self.view,
            ["sigma-algebra"],
            direction="out",
            edge_types=["prerequisite-for"],
            max_depth=2,
        )
        self.assertEqual(
            ["sigma-algebra", "measure", "absolute-continuity"],
            [row["node"]["id"] for row in neighborhood["nodes"]],
        )
        self.assertEqual(2, len(get(self.view, "measure")["outgoing"]) + len(get(self.view, "measure")["incoming"]))
        ranking = personalized_pagerank(
            self.view,
            {"sigma-algebra": 1.0},
            edge_types=["prerequisite-for"],
        )
        self.assertEqual(
            {"sigma-algebra", "measure", "absolute-continuity"},
            {row["node"]["id"] for row in ranking["results"]},
        )

    def test_bfs_applies_type_stale_and_orphan_filters_to_neighbors(self) -> None:
        seed = copy.deepcopy(fixture_nodes()[0])
        seed["id"] = "seed"
        seed["label"] = "Seed"
        active = copy.deepcopy(fixture_nodes()[1])
        active["id"] = "active-node"
        active["label"] = "Active node"
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
                "source": "seed",
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

        default = expand(
            view,
            ["seed"],
            node_types=["knowledge"],
            edge_types=["derived-from"],
        )
        inclusive = expand(
            view,
            ["seed"],
            node_types=["knowledge"],
            edge_types=["derived-from"],
            include_stale=True,
            include_orphaned=True,
        )

        self.assertEqual(
            {"seed", "active-node"},
            {row["node"]["id"] for row in default["nodes"]},
        )
        self.assertEqual(
            {"seed", "active-node", "stale-node", "orphan-node"},
            {row["node"]["id"] for row in inclusive["nodes"]},
        )
        self.assertNotIn("field-node", {edge["target"] for edge in inclusive["edges"]})

    def test_ppr_honors_direction_and_reports_only_accepted_seeds(self) -> None:
        outgoing = personalized_pagerank(
            self.view,
            {"sigma-algebra": 2.0, "measure": 0.0, "missing": 1.0},
            edge_types=["prerequisite-for"],
            direction="out",
        )
        incoming = personalized_pagerank(
            self.view,
            {"sigma-algebra": 1.0},
            edge_types=["prerequisite-for"],
            direction="in",
        )
        outgoing_scores = {
            row["node"]["id"]: row["score"] for row in outgoing["results"]
        }
        incoming_scores = {
            row["node"]["id"]: row["score"] for row in incoming["results"]
        }

        self.assertEqual({"sigma-algebra": 2.0}, outgoing["seeds"])
        self.assertGreater(outgoing_scores["measure"], 0.0)
        self.assertNotIn("measure", incoming_scores)
        with self.assertRaisesRegex(QueryError, "1 to 128"):
            personalized_pagerank(
                self.view, {f"seed-{index}": 1.0 for index in range(129)}
            )

    def test_ppr_omits_zero_score_disconnected_nodes(self) -> None:
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
                "evidence": "Disconnected component.",
                "curation_status": "current",
            },
        ]
        view = GraphView.from_snapshot(snapshot_with(nodes, edges))

        ranking = personalized_pagerank(
            view, {"seed-one": 1.0}, edge_types=["prerequisite-for"]
        )

        self.assertEqual(
            {"seed-one", "node-one"},
            {row["node"]["id"] for row in ranking["results"]},
        )

    def test_include_orphaned_is_explicit_across_query_lanes(self) -> None:
        orphan = copy.deepcopy(fixture_nodes()[0])
        orphan.update({"id": "orphan-node", "label": "Orphan token"})
        orphan["properties"]["source_status"] = "orphaned"
        orphan["provenance"]["active"] = False
        view = GraphView.from_snapshot(snapshot_with([orphan], []))

        self.assertEqual([], search(view, "orphan token"))
        self.assertEqual(
            "orphan-node",
            search(view, "orphan token", include_orphaned=True)[0]["node"]["id"],
        )
        with self.assertRaisesRegex(QueryError, "positive graph seed"):
            personalized_pagerank(view, {"orphan-node": 1.0})
        self.assertEqual(
            {"orphan-node": 1.0},
            personalized_pagerank(
                view, {"orphan-node": 1.0}, include_orphaned=True
            )["seeds"],
        )

    def test_cjk_token_estimate_is_utf8_conservative_and_self_consistent(self) -> None:
        value = {"text": "测度" * 20}
        serialized = canonical_json(value)
        self.assertEqual(len(serialized.encode("utf-8")), estimate_tokens(value))
        self.assertGreater(estimate_tokens(value), len(serialized))

        bundle = context(self.view, ["measure"], token_budget=2000)
        self.assertEqual(CONTEXT_SCHEMA, bundle["schema"])
        self.assertEqual("kgdistiller-context-bundle-v1", bundle["schema"])
        self.assertEqual(estimate_tokens(bundle), bundle["budget"]["estimated_tokens"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 2000)

    def test_context_filters_nodes_and_edges_by_explicit_policy(self) -> None:
        field_node = {
            "id": "field-node",
            "type": "field",
            "label": "Field node",
            "text": "",
            "properties": {"aliases": []},
        }
        nodes = fixture_nodes() + [field_node]
        edges = fixture_edges()
        edges.append(
            {
                "source": "field-node",
                "relation": "contains",
                "target": "measure",
                "evidence": "Taxonomy membership.",
                "curation_status": "current",
            }
        )
        view = GraphView.from_snapshot(snapshot_with(nodes, edges))
        selected = ["sigma-algebra", "measure", "field-node"]

        self.assertEqual(
            [], context(view, selected, edge_types=[], token_budget=5000)["edges"]
        )
        prerequisite = context(
            view,
            selected,
            edge_types=["prerequisite-for"],
            token_budget=5000,
        )
        taxonomy = context(
            view, selected, edge_types=["contains"], token_budget=5000
        )
        self.assertEqual(
            ["prerequisite-for"], [edge["relation"] for edge in prerequisite["edges"]]
        )
        self.assertEqual(["contains"], [edge["relation"] for edge in taxonomy["edges"]])

        stale_edges = copy.deepcopy(edges)
        stale_edges[0]["curation_status"] = "needs-review"
        stale_view = GraphView.from_snapshot(
            snapshot_with(nodes, stale_edges)
        )
        self.assertEqual(
            [],
            context(
                stale_view,
                selected,
                edge_types=["prerequisite-for"],
                token_budget=5000,
            )["edges"],
        )
        self.assertEqual(
            ["needs-review"],
            [
                edge["curation_status"]
                for edge in context(
                    stale_view,
                    selected,
                    edge_types=["prerequisite-for"],
                    include_stale=True,
                    token_budget=5000,
                )["edges"]
            ],
        )

        field = copy.deepcopy(fixture_nodes()[1])
        field.update({"id": "field-node", "type": "field", "label": "Field node"})
        orphan = copy.deepcopy(fixture_nodes()[2])
        orphan.update({"id": "orphan-node", "label": "Orphan node"})
        orphan["properties"]["source_status"] = "orphaned"
        orphan["provenance"]["active"] = False
        filtered_view = GraphView.from_snapshot(
            snapshot_with([fixture_nodes()[0], field, orphan], [])
        )
        filtered = context(
            filtered_view,
            ["sigma-algebra", "field-node", "orphan-node"],
            node_types=["knowledge"],
            token_budget=5000,
        )
        inclusive = context(
            filtered_view,
            ["sigma-algebra", "field-node", "orphan-node"],
            node_types=["knowledge"],
            include_orphaned=True,
            token_budget=5000,
        )
        self.assertEqual(["sigma-algebra"], [node["id"] for node in filtered["nodes"]])
        self.assertEqual(
            ["sigma-algebra", "orphan-node"],
            [node["id"] for node in inclusive["nodes"]],
        )

    def test_loaded_status_is_fresh_json_memory_view(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-query-") as raw:
            graph = write_fixture_graph(Path(raw))
            view = GraphView.load(graph)
            status = query_status(view)

        self.assertIn("json-memory", status["capabilities"])
        self.assertEqual(status, validate_contract(status))
        self.assertEqual(view.snapshot["graph"]["sha256"], status["graph_sha256"])
        self.assertEqual({"nodes": 3, "edges": 2, "references": 1}, status["counts"])

    def test_generation_change_discards_mixed_view(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-query-") as raw:
            graph = write_fixture_graph(Path(raw))
            manifest = __import__("json").loads((graph / "manifest.json").read_text(encoding="utf-8"))
            changed = copy.deepcopy(manifest)
            changed["graph_sha256"] = "f" * 64
            with patch(
                "kgdistiller.query._manifest_payload",
                side_effect=[manifest, changed],
            ):
                with self.assertRaisesRegex(QueryError, "generation changed"):
                    GraphView.load(graph, max_attempts=1)


if __name__ == "__main__":
    unittest.main()
