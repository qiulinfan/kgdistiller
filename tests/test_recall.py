from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from kgdistiller.contracts import canonical_json, sha256_json, validate_contract
import kgdistiller.recall as recall_module
import kgdistiller.cli as cli_module
from kgdistiller.recall import (
    RecallError,
    execute_recall_request,
    make_recall_request,
    recall_children,
    recall_context,
    recall_expand,
    recall_get,
    recall_resolve,
    recall_roots,
    recall_search,
    recall_status,
)
import tests.test_federation as federation_fixture
import tests.test_vault_ingest as ingest_fixture


DIGEST = "a" * 64


def _finalize_fixture_estimate(result: dict) -> None:
    while True:
        size = len(canonical_json(result).encode("utf-8"))
        if result["estimated_tokens"] == size:
            return
        result["estimated_tokens"] = size


def minimal_recall_request(operation: str = "status") -> dict:
    request = {
        "schema": "qlkg-recall-request-v1",
        "operation": operation,
        "vault_ids": [],
        "queries": [],
        "query": None,
        "handle": None,
        "handles": [],
        "scopes": [],
        "direction": "both",
        "edge_types": [],
        "max_depth": 1,
        "limit": 20,
        "token_budget": 6000,
        "include_stale": False,
    }
    if operation == "resolve":
        request["queries"] = ["Measure"]
    elif operation == "search":
        request["query"] = "measure"
    elif operation in {"children", "get"}:
        request["handle"] = "analysis:measure"
    elif operation == "expand":
        request["handles"] = ["analysis:measure"]
    elif operation == "context":
        request["query"] = "measure"
    return request


def minimal_recall_report() -> dict:
    report = {
        "schema": "qlkg-recall-report-v1",
        "operation": "status",
        "status": "complete",
        "generation": DIGEST,
        "registry_generation": DIGEST,
        "vaults": [
            {
                "vault_id": "analysis",
                "label": "Analysis",
                "health": "current",
                "generation": DIGEST,
                "graph_manifest_sha256": DIGEST,
                "graph_sha256": DIGEST,
                "source_ledger_generation_sha256": None,
                "authority_generation_sha256": DIGEST,
                "live_source_generation_sha256": None,
                "counts": {
                    "nodes": 0,
                    "edges": 0,
                    "references": 0,
                    "documents": 0,
                },
                "source_freshness": {
                    "current": 0,
                    "changed": 0,
                    "missing": 0,
                    "unavailable": 0,
                },
            }
        ],
        "incomplete_vaults": [],
        "result": {
            "query": None,
            "resolutions": [],
            "nodes": [],
            "edges": [],
            "evidence": [],
            "omissions": [],
            "truncated": False,
            "estimated_tokens": 0,
        },
    }
    report["generation"] = sha256_json(
        {
            "registry_generation": report["registry_generation"],
            "vaults": [
                {"vault_id": row["vault_id"], "generation": row["generation"]}
                for row in report["vaults"]
            ],
            "incomplete_vaults": [],
        }
    )
    _finalize_fixture_estimate(report["result"])
    return report


class RecallContractTests(unittest.TestCase):
    def test_closed_recall_contracts_accept_only_operation_consistent_shapes(self) -> None:
        for operation in (
            "status",
            "roots",
            "children",
            "resolve",
            "search",
            "get",
            "expand",
            "context",
        ):
            with self.subTest(operation=operation):
                request = minimal_recall_request(operation)
                self.assertEqual(request, validate_contract(request))

        unknown = minimal_recall_request()
        unknown["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown property"):
            validate_contract(unknown)

        inconsistent = minimal_recall_request("search")
        inconsistent["query"] = None
        with self.assertRaises(ValueError):
            validate_contract(inconsistent)

        ignored_status_control = minimal_recall_request("status")
        ignored_status_control["limit"] = 1
        with self.assertRaises(ValueError):
            validate_contract(ignored_status_control)

        mixed_context = minimal_recall_request("context")
        mixed_context["handles"] = ["analysis:measure"]
        with self.assertRaises(ValueError):
            validate_contract(mixed_context)

        handle_context = minimal_recall_request("context")
        handle_context["query"] = None
        handle_context["handles"] = ["analysis:measure"]
        handle_context["scopes"] = ["analysis:analysis-field"]
        with mock.patch("kgdistiller.recall.capture_federation") as capture:
            with self.assertRaises(RecallError):
                execute_recall_request(handle_context)
        capture.assert_not_called()

        too_many_terms = minimal_recall_request("search")
        too_many_terms["query"] = " ".join(
            f"query{index}" for index in range(129)
        )
        with mock.patch("kgdistiller.recall.capture_federation") as capture:
            with self.assertRaisesRegex(RecallError, "representable term limit"):
                execute_recall_request(too_many_terms)
        capture.assert_not_called()

        too_many_cjk_terms = minimal_recall_request("context")
        too_many_cjk_terms["query"] = "".join(chr(0x4E00 + index) for index in range(130))
        with mock.patch("kgdistiller.recall.capture_federation") as capture:
            with self.assertRaisesRegex(RecallError, "representable term limit"):
                execute_recall_request(too_many_cjk_terms)
        capture.assert_not_called()

        impossible_budget = minimal_recall_request("context")
        impossible_budget["token_budget"] = 1
        with mock.patch("kgdistiller.recall.capture_federation") as capture:
            with self.assertRaisesRegex(RecallError, "token budget"):
                execute_recall_request(impossible_budget)
        capture.assert_not_called()

        whitespace_requests = []
        whitespace_resolve = minimal_recall_request("resolve")
        whitespace_resolve["queries"] = [" \t\n"]
        whitespace_requests.append(whitespace_resolve)
        for operation in ("search", "context"):
            request = minimal_recall_request(operation)
            request["query"] = "\u2003\u00a0"
            whitespace_requests.append(request)
        for request in whitespace_requests:
            with self.subTest(whitespace=request["operation"]):
                with mock.patch("kgdistiller.recall.capture_federation") as capture:
                    with self.assertRaisesRegex(RecallError, "closed v1 contract"):
                        execute_recall_request(request)
                capture.assert_not_called()

    def test_report_and_error_are_closed_and_portable(self) -> None:
        report = minimal_recall_report()
        self.assertEqual(report, validate_contract(report))

        unsafe = copy.deepcopy(report)
        unsafe["result"]["nodes"] = [
            {
                "handle": "analysis:measure",
                "vault_id": "analysis",
                "node_id": "measure",
                "type": "knowledge",
                "label": "Measure",
                "aliases": [],
                "text": None,
                "curation_status": "current",
                "source_status": "active",
                "authority": "../outside.md",
                "parents": [],
                "score": None,
                "lane_evidence": [],
                "depth": None,
            }
        ]
        with self.assertRaises(ValueError):
            validate_contract(unsafe)

        error = {
            "schema": "qlkg-recall-error-v1",
            "error": {
                "code": "invalid-request",
                "message": "recall request is invalid",
                "operation": None,
                "vault_id": None,
                "generation": None,
            },
        }
        self.assertEqual(error, validate_contract(error))

    def test_report_semantics_bind_identities_generations_and_evidence(self) -> None:
        report = minimal_recall_report()
        node = {
            "handle": "analysis:measure",
            "vault_id": "analysis",
            "node_id": "measure",
            "type": "knowledge",
            "label": "Measure",
            "aliases": [],
            "text": None,
            "curation_status": "current",
            "source_status": "active",
            "authority": "Knowledge/Concepts/Measure.md",
            "parents": [],
            "score": None,
            "lane_evidence": [],
            "depth": None,
        }
        report["result"]["nodes"] = [node]
        _finalize_fixture_estimate(report["result"])
        self.assertEqual(report, validate_contract(report))

        bad_handle = copy.deepcopy(report)
        bad_handle["result"]["nodes"][0]["handle"] = "analysis:other"
        with self.assertRaisesRegex(ValueError, "node handle"):
            validate_contract(bad_handle)

        bad_identity = copy.deepcopy(report)
        bad_identity["result"]["nodes"][0]["score"] = 10.0
        bad_identity["result"]["nodes"][0]["lane_evidence"] = [
            {
                "lane": "identity", "rank": 1, "score": 10.0,
                "reason": "exact-id", "match_kind": "alias",
                "matched_fields": [], "matched_terms": [], "scope": None,
                "seed": None, "path": [],
            }
        ]
        with self.assertRaisesRegex(ValueError, "lane evidence"):
            validate_contract(bad_identity)

        bad_graph = copy.deepcopy(report)
        bad_graph["result"]["nodes"][0]["score"] = 10.0
        bad_graph["result"]["nodes"][0]["lane_evidence"] = [
            {
                "lane": "graph", "rank": 1, "score": 10.0,
                "reason": "trusted-edge", "match_kind": None,
                "matched_fields": [], "matched_terms": [], "scope": None,
                "seed": "analysis:analysis-field",
                "path": [
                    {
                        "source": "analysis:analysis-field",
                        "relation": "implies",
                        "target": "analysis:other",
                    }
                ],
            }
        ]
        with self.assertRaisesRegex(ValueError, "lane evidence"):
            validate_contract(bad_graph)

        rank_gap = copy.deepcopy(report)
        rank_gap["result"]["nodes"][0]["score"] = 1000.0
        rank_gap["result"]["nodes"][0]["lane_evidence"] = [
            {
                "lane": "identity", "rank": 500, "score": 1000.0,
                "reason": "exact-id", "match_kind": "id",
                "matched_fields": [], "matched_terms": [], "scope": None,
                "seed": None, "path": [],
            }
        ]
        with self.assertRaisesRegex(ValueError, "fusion order"):
            validate_contract(rank_gap)

        wrong_rank_order = copy.deepcopy(rank_gap)
        wrong_rank_order["result"]["nodes"][0]["lane_evidence"][0]["rank"] = 2
        lower = copy.deepcopy(node)
        lower.update(
            {
                "handle": "analysis:analysis-field",
                "node_id": "analysis-field",
                "type": "field",
                "label": "Analysis",
                "authority": "Knowledge/Fields/Analysis.md",
                "score": 900.0,
                "lane_evidence": [
                    {
                        "lane": "identity", "rank": 1, "score": 900.0,
                        "reason": "exact-label", "match_kind": "label",
                        "matched_fields": [], "matched_terms": [], "scope": None,
                        "seed": None, "path": [],
                    }
                ],
            }
        )
        wrong_rank_order["result"]["nodes"].append(lower)
        with self.assertRaisesRegex(ValueError, "fusion order"):
            validate_contract(wrong_rank_order)

        overlap = minimal_recall_report()
        overlap["status"] = "partial"
        overlap["incomplete_vaults"] = [
            {"vault_id": "analysis", "code": "invalid-vault", "message": "invalid"}
        ]
        overlap["generation"] = sha256_json(
            {
                "registry_generation": overlap["registry_generation"],
                "vaults": [{"vault_id": "analysis", "generation": DIGEST}],
                "incomplete_vaults": [
                    {"vault_id": "analysis", "code": "invalid-vault"}
                ],
            }
        )
        with self.assertRaisesRegex(ValueError, "both complete and incomplete"):
            validate_contract(overlap)

        wrong_generation = minimal_recall_report()
        wrong_generation["generation"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "generation"):
            validate_contract(wrong_generation)

        invalid_resolutions = [
            {
                "query": "missing", "status": "missing", "match_kind": "id",
                "matches": ["missing:node"], "overflow": True,
            },
            {
                "query": "exact", "status": "exact", "match_kind": "alias",
                "matches": ["analysis:measure"], "overflow": False,
            },
            {
                "query": "alias", "status": "alias", "match_kind": "alias",
                "matches": ["analysis:measure", "analysis:analysis-field"],
                "overflow": False,
            },
            {
                "query": "ambiguous", "status": "ambiguous", "match_kind": "id",
                "matches": [], "overflow": False,
            },
        ]
        for resolution in invalid_resolutions:
            with self.subTest(resolution=resolution["query"]):
                forged = minimal_recall_report()
                forged["result"]["resolutions"] = [resolution]
                with self.assertRaisesRegex(ValueError, "resolution"):
                    validate_contract(forged)

        bounded_unknown = minimal_recall_report()
        bounded_unknown["result"]["resolutions"] = [
            {
                "query": "bounded", "status": "ambiguous", "match_kind": None,
                "matches": [], "overflow": True,
            }
        ]
        _finalize_fixture_estimate(bounded_unknown["result"])
        self.assertEqual(bounded_unknown, validate_contract(bounded_unknown))

        evidence = {
            "kind": "concept",
            "handle": "analysis:measure",
            "source": None,
            "relation": None,
            "target": None,
            "document_id": "00000000-0000-0000-0000-000000000001",
            "version_id": "doc:00000000-0000-0000-0000-000000000001:v00000001",
            "source_path": "notes/source.md",
            "format": "markdown",
            "start_line": 1,
            "end_line": 1,
            "start_column": None,
            "end_column": None,
            "excerpt": "evidence",
            "excerpt_sha256": ingest_fixture._sha256(b"evidence"),
        }
        with_evidence = minimal_recall_report()
        with_evidence["result"]["evidence"] = [evidence]
        _finalize_fixture_estimate(with_evidence["result"])
        self.assertEqual(with_evidence, validate_contract(with_evidence))
        bad_digest = copy.deepcopy(with_evidence)
        bad_digest["result"]["evidence"][0]["excerpt_sha256"] = DIGEST
        with self.assertRaisesRegex(ValueError, "excerpt digest"):
            validate_contract(bad_digest)
        half_columns = copy.deepcopy(with_evidence)
        half_columns["result"]["evidence"][0]["start_column"] = 0
        with self.assertRaisesRegex(ValueError, "coordinates"):
            validate_contract(half_columns)

        cross_edge = minimal_recall_report()
        cross_edge["result"]["edges"] = [
            {
                "source": "analysis:measure",
                "relation": "implies",
                "target": "other:measure",
                "evidence": None,
                "curation_status": "current",
                "depth": None,
            }
        ]
        with self.assertRaisesRegex(ValueError, "crosses a Vault"):
            validate_contract(cross_edge)

        hidden_omission = minimal_recall_report()
        hidden_omission["result"]["omissions"] = [
            {"kind": "node", "id": "bounded", "reason": "limit"}
        ]
        with self.assertRaisesRegex(ValueError, "truncated"):
            validate_contract(hidden_omission)

        forged_estimate = minimal_recall_report()
        forged_estimate["result"]["estimated_tokens"] = 0
        with self.assertRaisesRegex(ValueError, "canonical result bytes"):
            validate_contract(forged_estimate)


class RecallOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        federation_fixture.FederationFixture.setUp(self)

    def tearDown(self) -> None:
        federation_fixture.FederationFixture.tearDown(self)

    def _cli(self, *arguments: str) -> tuple[int, dict, dict | None]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        elsewhere = self.root / "arbitrary-cwd"
        elsewhere.mkdir(exist_ok=True)
        previous = Path.cwd()
        try:
            os.chdir(elsewhere)
            with (
                mock.patch.dict(
                    os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
                ),
                mock.patch.object(sys, "argv", ["kgdistiller", *arguments]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli_module.main()
        finally:
            os.chdir(previous)
        return (
            code,
            json.loads(stdout.getvalue()) if stdout.getvalue() else {},
            json.loads(stderr.getvalue()) if stderr.getvalue() else None,
        )

    def test_status_roots_and_children_preserve_multi_parent_taxonomy(self) -> None:
        status = recall_status(home=self.home)
        self.assertEqual("complete", status["status"])
        self.assertEqual(["analysis", "probability"], [row["vault_id"] for row in status["vaults"]])

        roots = recall_roots(home=self.home)
        self.assertEqual(
            [
                "analysis:analysis-field",
                "analysis:probability-field",
                "probability:probability",
            ],
            [row["handle"] for row in roots["result"]["nodes"]],
        )
        left = recall_children("analysis:analysis-field", home=self.home)
        right = recall_children("analysis:probability-field", home=self.home)
        self.assertIn("analysis:measure-topic", [row["handle"] for row in left["result"]["nodes"]])
        topic = next(row for row in right["result"]["nodes"] if row["handle"] == "analysis:measure-topic")
        self.assertEqual(
            ["analysis:analysis-field", "analysis:probability-field"],
            topic["parents"],
        )

    def test_injected_snapshot_is_projected_to_the_requested_vaults(self) -> None:
        snapshot = recall_module.capture_federation(home=self.home)
        report = execute_recall_request(
            make_recall_request("status", vault_ids=["analysis"]),
            snapshot=snapshot,
        )
        self.assertEqual(["analysis"], [row["vault_id"] for row in report["vaults"]])
        self.assertNotEqual(snapshot.generation, report["generation"])
        self.assertEqual(
            sha256_json(
                {
                    "registry_generation": snapshot.registry_generation,
                    "vaults": [
                        {
                            "vault_id": "analysis",
                            "generation": snapshot.by_id["analysis"].generation,
                        }
                    ],
                    "incomplete_vaults": [],
                }
            ),
            report["generation"],
        )
        with mock.patch("kgdistiller.recall.capture_federation") as capture:
            with self.assertRaisesRegex(RecallError, "not present"):
                execute_recall_request(
                    make_recall_request("status", vault_ids=["missing"]),
                    snapshot=snapshot,
                )
        capture.assert_not_called()

    def test_cli_recall_surface_works_from_arbitrary_cwd_and_errors_are_closed(self) -> None:
        cases = (
            ("status", ["recall", "status", "--vault", "analysis"]),
            ("roots", ["recall", "roots", "--vault", "analysis"]),
            ("children", ["recall", "children", "analysis:analysis-field"]),
            ("resolve", ["recall", "resolve", "Measure", "--vault", "analysis", "--include-stale"]),
            ("search", ["recall", "search", "measure", "--vault", "analysis", "--include-stale"]),
            ("get", ["recall", "get", "analysis:measure", "--include-stale"]),
            ("expand", ["recall", "expand", "analysis:analysis-field", "--direction", "outgoing", "--relation", "contains", "--include-stale"]),
            ("context", ["recall", "context", "--handle", "analysis:analysis-field", "--budget", "3000", "--include-stale"]),
        )
        for operation, arguments in cases:
            with self.subTest(operation=operation):
                code, output, error = self._cli(*arguments)
                self.assertEqual(0, code)
                self.assertIsNone(error)
                self.assertEqual("qlkg-recall-report-v1", output["schema"])
                self.assertEqual(operation, output["operation"])

        code, output, error = self._cli("recall", "get", "missing:node")
        self.assertEqual(1, code)
        self.assertEqual({}, output)
        self.assertEqual("qlkg-recall-error-v1", error["schema"])
        self.assertNotIn(str(self.root), canonical_json(error))

    def test_resolve_uses_vault_qualified_ambiguous_identity(self) -> None:
        fresh_only = recall_resolve(["Measure"], home=self.home)
        self.assertEqual("missing", fresh_only["result"]["resolutions"][0]["status"])
        report = recall_resolve(["Measure"], home=self.home, include_stale=True)
        resolution = report["result"]["resolutions"][0]
        self.assertEqual("ambiguous", resolution["status"])
        self.assertEqual(
            ["analysis:measure", "probability:probability-measure"],
            resolution["matches"],
        )
        self.assertTrue(
            all(row["lane_evidence"][0]["reason"] == "exact-label" for row in report["result"]["nodes"])
        )

        batch = recall_resolve(
            ["Measure", "measure-topic"], home=self.home, include_stale=True
        )
        identity_ranks = [
            row["lane_evidence"][0]["rank"] for row in batch["result"]["nodes"]
        ]
        self.assertEqual(len(identity_ranks), len(set(identity_ranks)))

    def test_resolve_keeps_label_and_alias_collisions_within_one_vault(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        view = snapshot.vaults[0].view
        view.labels["collision"] = ("analysis-field",)
        view.aliases["collision"] = ("measure",)
        report = execute_recall_request(
            make_recall_request(
                "resolve", queries=["collision"], include_stale=True
            ),
            snapshot=snapshot,
        )
        resolution = report["result"]["resolutions"][0]
        self.assertEqual("ambiguous", resolution["status"])
        self.assertEqual(
            ["analysis:analysis-field", "analysis:measure"],
            resolution["matches"],
        )

    def test_final_ranking_and_singleton_resolution_are_independently_bounded(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        with mock.patch.object(recall_module, "MAX_RESULT_NODES", 0):
            singleton = execute_recall_request(
                make_recall_request(
                    "resolve",
                    vault_ids=["analysis"],
                    queries=["analysis-field"],
                    include_stale=True,
                ),
                snapshot=snapshot,
            )
        resolution = singleton["result"]["resolutions"][0]
        self.assertEqual("exact", resolution["status"])
        self.assertEqual(["analysis:analysis-field"], resolution["matches"])
        self.assertFalse(resolution["overflow"])
        self.assertEqual([], singleton["result"]["nodes"])
        self.assertTrue(singleton["result"]["truncated"])

        crafted = recall_module._empty_result()
        federated = snapshot.by_id["analysis"]
        for node_id, kind, score, old_rank in (
            ("measure", "id", 1000.0, 500),
            ("analysis-field", "label", 900.0, 1),
        ):
            lane = recall_module._identity_lane(kind, score)
            lane["rank"] = old_rank
            crafted["nodes"].append(
                recall_module._node_dto(
                    federated,
                    node_id,
                    result=crafted,
                    lanes={"identity": lane},
                )
            )
        with mock.patch.object(
            recall_module, "_operation_result", return_value=crafted
        ):
            reranked = execute_recall_request(
                make_recall_request(
                    "search",
                    vault_ids=["analysis"],
                    query="measure",
                    include_stale=True,
                ),
                snapshot=snapshot,
            )
        self.assertEqual(
            {"analysis:measure": 1, "analysis:analysis-field": 2},
            {
                row["handle"]: row["lane_evidence"][0]["rank"]
                for row in reranked["result"]["nodes"]
            },
        )

    def test_scoped_search_uses_cached_postings_and_visible_fusion_lanes(self) -> None:
        recall_status(home=self.home)
        with mock.patch(
            "kgdistiller.federation._build_index",
            wraps=federation_fixture.federation_module._build_index,
        ) as build:
            report = recall_search(
                "countably additive",
                home=self.home,
                scopes=["analysis:analysis-field"],
                include_stale=True,
            )
        self.assertEqual(0, build.call_count)
        handles = [row["handle"] for row in report["result"]["nodes"]]
        self.assertIn("analysis:measure", handles)
        self.assertNotIn("probability:probability-measure", handles)
        measure = next(row for row in report["result"]["nodes"] if row["handle"] == "analysis:measure")
        self.assertEqual(["taxonomy", "lexical"], [row["lane"] for row in measure["lane_evidence"]])
        self.assertEqual(
            measure["score"],
            round(sum(row["score"] for row in measure["lane_evidence"]), 12),
        )
        lexical = measure["lane_evidence"][1]
        self.assertEqual(["body"], lexical["matched_fields"])
        self.assertEqual(["additive", "countably"], lexical["matched_terms"])

        outside = recall_search(
            "Measure",
            home=self.home,
            scopes=["analysis:analysis-field"],
            include_stale=True,
        )
        self.assertNotIn(
            "probability:probability-measure",
            [row["handle"] for row in outside["result"]["nodes"]],
        )
        no_match = recall_search(
            "term-not-present-anywhere",
            home=self.home,
            scopes=["analysis:analysis-field"],
            include_stale=True,
        )
        self.assertEqual([], no_match["result"]["nodes"])

    def test_cjk_phrase_search_uses_shared_bigram_postings(self) -> None:
        self.analysis_measure.write_text(
            federation_fixture._concept(
                "measure",
                "Measure",
                "测度是满足可列可加性的集合函数。",
                topics=["[[Knowledge/Topics/Measure]]"],
            ),
            encoding="utf-8",
        )
        federation_fixture.sync_knowledge("analysis", home=self.home)
        with federation_fixture.federation_module._INDEX_CACHE_LOCK:
            federation_fixture.federation_module._INDEX_CACHE.clear()
        report = recall_search(
            "可列可加性",
            home=self.home,
            vault_ids=["analysis"],
            include_stale=True,
        )
        self.assertIn(
            "analysis:measure", [row["handle"] for row in report["result"]["nodes"]]
        )

    def test_children_apply_the_same_freshness_policy_as_resolve(self) -> None:
        current = recall_children("analysis:measure-topic", home=self.home)
        stale = recall_children(
            "analysis:measure-topic", home=self.home, include_stale=True
        )
        self.assertNotIn("analysis:measure", [row["handle"] for row in current["result"]["nodes"]])
        self.assertIn("analysis:measure", [row["handle"] for row in stale["result"]["nodes"]])

    def test_internal_lane_bounds_truncate_deterministically_and_rank_only_output(self) -> None:
        with mock.patch.object(recall_module, "MAX_LEXICAL_WORK", 1):
            first = recall_search("measure", home=self.home, include_stale=True, limit=1)
            second = recall_search("measure", home=self.home, include_stale=True, limit=1)
        self.assertEqual(first, second)
        self.assertTrue(first["result"]["truncated"])
        self.assertTrue(any(row["id"] == "lexical-candidates" for row in first["result"]["omissions"]))
        for node in first["result"]["nodes"]:
            self.assertTrue(all(row["rank"] <= len(first["result"]["nodes"]) for row in node["lane_evidence"]))

    def test_graph_lane_never_escapes_scope_or_traverses_stale_nodes(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        view = snapshot.vaults[0].view
        measure = view.nodes["measure"]
        measure["properties"]["curation_status"] = "current"
        outside = copy.deepcopy(measure)
        outside["id"] = "outside"
        outside["label"] = "Outside"
        outside["properties"]["curation_status"] = "needs-review"
        downstream = copy.deepcopy(measure)
        downstream["id"] = "downstream"
        downstream["label"] = "Downstream"
        downstream["properties"]["curation_status"] = "current"
        view.nodes["outside"] = outside
        view.nodes["downstream"] = downstream
        first_edge = {
            "source": "measure",
            "relation": "implies",
            "target": "outside",
            "evidence": None,
            "curation_status": "current",
        }
        second_edge = {
            "source": "outside",
            "relation": "implies",
            "target": "downstream",
            "evidence": None,
            "curation_status": "current",
        }
        view.outgoing["measure"] = (first_edge,)
        view.incoming["outside"] = (first_edge,)
        view.outgoing["outside"] = (second_edge,)
        view.incoming["downstream"] = (second_edge,)

        fresh_request = make_recall_request(
            "search", query="Measure", max_depth=2, include_stale=False
        )
        fresh = execute_recall_request(fresh_request, snapshot=snapshot)
        fresh_handles = {row["handle"] for row in fresh["result"]["nodes"]}
        self.assertIn("analysis:measure", fresh_handles)
        self.assertNotIn("analysis:outside", fresh_handles)
        self.assertNotIn("analysis:downstream", fresh_handles)

        scoped_request = make_recall_request(
            "search",
            query="Measure",
            scopes=["analysis:analysis-field"],
            max_depth=2,
            include_stale=True,
        )
        scoped = execute_recall_request(scoped_request, snapshot=snapshot)
        scoped_handles = {row["handle"] for row in scoped["result"]["nodes"]}
        self.assertNotIn("analysis:outside", scoped_handles)
        self.assertNotIn("analysis:downstream", scoped_handles)

    def test_expand_does_not_call_an_incoming_knowledge_parent_a_taxonomy_scope(self) -> None:
        report = recall_expand(
            ["analysis:measure"],
            home=self.home,
            direction="incoming",
            edge_types=["contains"],
            max_depth=1,
            include_stale=True,
        )
        parent = next(
            row
            for row in report["result"]["nodes"]
            if row["handle"] == "analysis:measure-topic"
        )
        self.assertEqual("graph", parent["lane_evidence"][0]["lane"])
        self.assertEqual("trusted-edge", parent["lane_evidence"][0]["reason"])

    def test_context_applies_edge_type_and_freshness_to_edges(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        view = snapshot.vaults[0].view
        edge = {
            "source": "analysis-field",
            "relation": "implies",
            "target": "probability-field",
            "evidence": None,
            "curation_status": "needs-review",
        }
        view.outgoing["analysis-field"] = (*view.outgoing.get("analysis-field", ()), edge)
        view.incoming["probability-field"] = (*view.incoming.get("probability-field", ()), edge)
        handles = ["analysis:analysis-field", "analysis:probability-field"]

        fresh = execute_recall_request(
            make_recall_request(
                "context", handles=handles, edge_types=["implies"], token_budget=8_000
            ),
            snapshot=snapshot,
        )
        self.assertEqual([], fresh["result"]["edges"])
        stale = execute_recall_request(
            make_recall_request(
                "context", handles=handles, edge_types=["implies"],
                token_budget=8_000, include_stale=True,
            ),
            snapshot=snapshot,
        )
        self.assertEqual(["implies"], [row["relation"] for row in stale["result"]["edges"]])
        filtered = execute_recall_request(
            make_recall_request(
                "context", handles=handles, edge_types=["contains"],
                token_budget=8_000, include_stale=True,
            ),
            snapshot=snapshot,
        )
        self.assertEqual([], filtered["result"]["edges"])

    def test_term_and_examined_edge_work_limits_are_explicit(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        view = snapshot.vaults[0].view
        view.nodes["measure"]["text"] = " ".join(
            f"term{index}" for index in range(129)
        )
        index = federation_fixture.federation_module._build_index(view)
        self.assertIn("measure", index.postings["term128"])
        with self.assertRaisesRegex(RecallError, "representable term limit"):
            execute_recall_request(
                make_recall_request(
                    "search",
                    query=" ".join(f"query{index}" for index in range(129)),
                    include_stale=True,
                ),
                snapshot=snapshot,
            )

        contains = {
            "source": "measure",
            "relation": "contains",
            "target": "measure-topic",
            "evidence": None,
            "curation_status": "not-applicable",
        }
        view.outgoing["measure"] = tuple(copy.deepcopy(contains) for _ in range(4))
        with mock.patch.object(recall_module, "MAX_GRAPH_EDGE_WORK", 2):
            bounded = execute_recall_request(
                make_recall_request(
                    "search", query="measure", edge_types=["implies"], include_stale=True
                ),
                snapshot=snapshot,
            )
        self.assertTrue(bounded["result"]["truncated"])
        self.assertTrue(
            any(row["id"] == "graph-expansion" for row in bounded["result"]["omissions"])
        )

    def test_multi_handle_operations_keep_healthy_results_when_one_vault_is_incomplete(self) -> None:
        moved = self.root / "Probability-offline"
        self.probability.rename(moved)
        expanded = recall_expand(
            ["analysis:analysis-field", "probability:probability"],
            home=self.home,
            edge_types=["contains"],
            max_depth=1,
            include_stale=True,
        )
        self.assertEqual("partial", expanded["status"])
        self.assertIn(
            "analysis:analysis-field",
            [row["handle"] for row in expanded["result"]["nodes"]],
        )
        self.assertTrue(
            any(row["reason"] == "incomplete-vault" for row in expanded["result"]["omissions"])
        )

        missing_get = recall_get(
            "probability:probability-measure", home=self.home, include_stale=True
        )
        self.assertEqual("partial", missing_get["status"])
        self.assertEqual([], missing_get["result"]["nodes"])

    def test_context_stops_evidence_generation_at_the_budget(self) -> None:
        snapshot = recall_module.capture_federation(
            home=self.home, vault_ids=["analysis"]
        )
        produced = 0

        def many_rows(selected, captured, **kwargs):
            nonlocal produced
            row = {
                "kind": "concept",
                "handle": "analysis:measure",
                "source": None,
                "relation": None,
                "target": None,
                "document_id": "00000000-0000-0000-0000-000000000001",
                "version_id": "doc:00000000-0000-0000-0000-000000000001:v00000001",
                "source_path": "Sources/measure.md",
                "format": "markdown",
                "start_line": 1,
                "end_line": 1,
                "start_column": None,
                "end_column": None,
                "excerpt": "x" * 1024,
                "excerpt_sha256": ingest_fixture._sha256(b"x" * 1024),
            }
            for _ in range(10_000):
                produced += 1
                yield copy.deepcopy(row), "analysis:measure", "ok"

        request = make_recall_request(
            "context",
            handles=["analysis:measure"],
            include_stale=True,
            token_budget=3_000,
        )
        with mock.patch(
            "kgdistiller.recall._iter_evidence_for_handles", side_effect=many_rows
        ):
            report = execute_recall_request(request, snapshot=snapshot)
        self.assertLess(produced, 10)
        self.assertLessEqual(report["result"]["estimated_tokens"], 3_000)
        self.assertTrue(report["result"]["truncated"])


    def test_get_expand_and_context_use_one_closed_snapshot(self) -> None:
        detail = recall_get("analysis:measure", home=self.home, include_stale=True)
        self.assertIn("countably additive", detail["result"]["nodes"][0]["text"])

        expanded = recall_expand(
            ["analysis:analysis-field"],
            home=self.home,
            edge_types=["contains"],
            max_depth=2,
            include_stale=True,
        )
        self.assertEqual(
            {
                "analysis:analysis-field",
                "analysis:probability-field",
                "analysis:measure-topic",
                "analysis:measure",
            },
            {row["handle"] for row in expanded["result"]["nodes"]},
        )

        request = make_recall_request(
            "context",
            handles=["analysis:measure"],
            include_stale=True,
            token_budget=6000,
        )
        with mock.patch(
            "kgdistiller.recall.capture_federation",
            wraps=recall_module.capture_federation,
        ) as capture:
            context = execute_recall_request(request, home=self.home)
        self.assertEqual(1, capture.call_count)
        self.assertEqual("analysis:measure", context["result"]["nodes"][0]["handle"])
        self.assertLessEqual(context["result"]["estimated_tokens"], 6000)
        self.assertEqual([], context["result"]["evidence"])

    def test_incomplete_vault_and_total_report_bound_are_closed(self) -> None:
        moved = self.root / "Probability-offline"
        self.probability.rename(moved)
        report = recall_status(home=self.home)
        self.assertEqual("partial", report["status"])
        self.assertEqual("probability", report["incomplete_vaults"][0]["vault_id"])
        self.assertNotIn(str(self.root), recall_module.canonical_json(report))

        with mock.patch.object(recall_module, "MAX_RECALL_REPORT_BYTES", 128):
            with self.assertRaisesRegex(RecallError, "byte limit"):
                recall_status(home=self.home, vault_ids=["analysis"])

    def test_forged_duplicate_or_inconsistent_lane_evidence_fails_contract(self) -> None:
        report = recall_search(
            "countably additive",
            home=self.home,
            scopes=["analysis:analysis-field"],
            include_stale=True,
        )
        forged = copy.deepcopy(report)
        node = next(row for row in forged["result"]["nodes"] if row["handle"] == "analysis:measure")
        node["lane_evidence"].append(copy.deepcopy(node["lane_evidence"][-1]))
        node["score"] = round(sum(row["score"] for row in node["lane_evidence"]), 12)
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_contract(forged)


class RecallEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        ingest_fixture.VaultIngestTests.setUp(self)

    def tearDown(self) -> None:
        ingest_fixture.VaultIngestTests.tearDown(self)

    def test_context_reads_verified_evidence_from_portable_archive_without_live_locator(self) -> None:
        source = self.vault_root / "notes/source.md"
        source.parent.mkdir()
        source.write_text("Alpha evidence.\n", encoding="utf-8")
        captured = ingest_fixture.capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: ingest_fixture.uuid.UUID(
                "12345678-1234-4234-8234-123456789abc"
            ),
        )
        version_id = captured["result"]["current_version_id"]
        update = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [
                {"candidate_id": "alpha", "disposition": "reuse"}
            ],
            "concept_ids": ["alpha"],
            "concept_evidence": [
                {
                    "concept_id": "alpha",
                    "spans": [
                        {
                            "version_id": version_id,
                            "start_line": 1,
                            "end_line": 1,
                            "excerpt_sha256": ingest_fixture._sha256(
                                b"Alpha evidence."
                            ),
                        }
                    ],
                }
            ],
            "relation_evidence": [],
        }
        request = ingest_fixture.VaultIngestTests._request(
            self,
            [],
            updates=[update],
            request_id="recall-evidence",
        )
        ingest_fixture.apply_vault_ingest(
            request, request_root=self.root, home=self.home
        )
        source.unlink()

        report = recall_context(
            handles=["test:alpha"], home=self.home, token_budget=6_000
        )
        self.assertEqual("complete", report["status"])
        self.assertEqual("Alpha evidence.", report["result"]["evidence"][0]["excerpt"])
        self.assertEqual(version_id, report["result"]["evidence"][0]["version_id"])
        self.assertEqual("notes/source.md", report["result"]["evidence"][0]["source_path"])

    def test_carried_evidence_uses_the_reviewed_versions_captured_locator(self) -> None:
        source = self.vault_root / "notes/original.md"
        source.parent.mkdir()
        source.write_bytes(b"Alpha evidence.\r\n")
        captured = ingest_fixture.capture_source(
            source,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:00Z",
            uuid_factory=lambda: ingest_fixture.uuid.UUID(
                "12345678-1234-4234-8234-123456789abd"
            ),
        )
        reviewed_version = captured["result"]["current_version_id"]
        update = {
            "version_id": reviewed_version,
            "status": "committed",
            "candidate_dispositions": [
                {"candidate_id": "alpha", "disposition": "reuse"}
            ],
            "concept_ids": ["alpha"],
            "concept_evidence": [
                {
                    "concept_id": "alpha",
                    "spans": [
                        {
                            "version_id": reviewed_version,
                            "start_line": 1,
                            "end_line": 1,
                            "excerpt_sha256": ingest_fixture._sha256(
                                b"Alpha evidence."
                            ),
                        }
                    ],
                }
            ],
            "relation_evidence": [],
        }
        ingest_fixture.apply_vault_ingest(
            ingest_fixture.VaultIngestTests._request(
                self, [], updates=[update], request_id="recall-carried-evidence"
            ),
            request_root=self.root,
            home=self.home,
        )

        moved = self.vault_root / "notes/moved.md"
        source.rename(moved)
        move = ingest_fixture.capture_source(moved, home=self.home)
        self.assertEqual("move", move["result"]["outcome"])
        moved.write_bytes(b"Alpha evidence.\n")
        newline = ingest_fixture.capture_source(
            moved,
            home=self.home,
            clock=lambda: "2026-08-16T00:00:01Z",
        )
        self.assertEqual("capture", newline["result"]["outcome"])

        report = recall_context(
            handles=["test:alpha"], home=self.home, token_budget=6_000
        )
        evidence = report["result"]["evidence"][0]
        self.assertEqual(reviewed_version, evidence["version_id"])
        self.assertEqual("notes/original.md", evidence["source_path"])
        self.assertEqual("Alpha evidence.", evidence["excerpt"])
        self.assertEqual(
            ingest_fixture._sha256(b"Alpha evidence."),
            evidence["excerpt_sha256"],
        )

    def test_context_isolates_one_corrupt_selected_blob_and_keeps_other_evidence(self) -> None:
        bad = self.vault_root / "notes/bad.md"
        good = self.vault_root / "notes/good.md"
        bad.parent.mkdir()
        bad.write_bytes(b"Bad evidence.\n")
        good.write_bytes(b"Good evidence.\n")
        bad_capture = ingest_fixture.capture_source(
            bad,
            home=self.home,
            uuid_factory=lambda: ingest_fixture.uuid.UUID(
                "12345678-1234-4234-8234-123456789abe"
            ),
        )
        good_capture = ingest_fixture.capture_source(
            good,
            home=self.home,
            uuid_factory=lambda: ingest_fixture.uuid.UUID(
                "12345678-1234-4234-8234-123456789abf"
            ),
        )

        def update(version_id: str, excerpt: bytes) -> dict:
            return {
                "version_id": version_id,
                "status": "committed",
                "candidate_dispositions": [
                    {"candidate_id": "alpha", "disposition": "reuse"}
                ],
                "concept_ids": ["alpha"],
                "concept_evidence": [
                    {
                        "concept_id": "alpha",
                        "spans": [
                            {
                                "version_id": version_id,
                                "start_line": 1,
                                "end_line": 1,
                                "excerpt_sha256": ingest_fixture._sha256(excerpt),
                            }
                        ],
                    }
                ],
                "relation_evidence": [],
            }

        ingest_fixture.apply_vault_ingest(
            ingest_fixture.VaultIngestTests._request(
                self,
                [],
                updates=[
                    update(bad_capture["result"]["current_version_id"], b"Bad evidence."),
                    update(good_capture["result"]["current_version_id"], b"Good evidence."),
                ],
                request_id="recall-two-evidence-groups",
            ),
            request_root=self.root,
            home=self.home,
        )
        vault = ingest_fixture.load_vault(self.vault_root, expected_id="test")
        ledger = ingest_fixture.load_source_ledger(vault)
        bad_version = next(
            row
            for row in ledger.versions
            if row["version_id"] == bad_capture["result"]["current_version_id"]
        )
        blob = ledger.sources_root.joinpath(*bad_version["blob_path"].split("/"))
        blob.write_bytes(b"Mad evidence.\n")

        report = recall_context(
            handles=["test:alpha"], home=self.home, token_budget=8_000
        )
        self.assertEqual(
            ["Good evidence."],
            [row["excerpt"] for row in report["result"]["evidence"]],
        )
        self.assertTrue(report["result"]["truncated"])
        self.assertTrue(
            any(
                row == {"kind": "vault", "id": "test", "reason": "incomplete-vault"}
                for row in report["result"]["omissions"]
            )
        )

    def test_selected_blob_read_uses_declared_version_size_as_its_pinned_cap(self) -> None:
        source = self.vault_root / "notes/size.md"
        source.parent.mkdir()
        source.write_bytes(b"Some evidence.\n")
        captured = ingest_fixture.capture_source(source, home=self.home)
        vault = ingest_fixture.load_vault(self.vault_root, expected_id="test")
        ledger = ingest_fixture.load_source_ledger(vault)
        version = copy.deepcopy(
            next(
                row
                for row in ledger.versions
                if row["version_id"] == captured["result"]["current_version_id"]
            )
        )
        version["byte_count"] = 1
        with mock.patch(
            "kgdistiller.source_archive._read_regular",
            wraps=ingest_fixture.source_archive_module._read_regular,
        ) as reader:
            with self.assertRaises(ingest_fixture.SourceArchiveError):
                recall_module.verified_version_text(ledger, version)
        limits = [
            call.kwargs["maximum"]
            for call in reader.call_args_list
            if call.kwargs.get("kind") == "source-blob"
        ]
        self.assertEqual([1], limits)


if __name__ == "__main__":
    unittest.main()
