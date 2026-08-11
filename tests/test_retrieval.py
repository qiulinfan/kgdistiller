from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from kgdistiller.agent import (
    embedding_inventory,
    estimate_tokens,
    index_generation_token,
    install_embedding_records,
    write_agent_index,
)
from kgdistiller.contracts import sha256_json, validate_contract
from kgdistiller.providers import ProviderAdapterRegistry, provider_config_sha256
from kgdistiller.retrieval import (
    MAX_RETRIEVAL_PLAN_BYTES,
    RetrievalError,
    build_context_from_execution,
    execute_retrieval_plan,
    legacy_retrieval_plan,
    load_retrieval_plan,
    _fused_results,
)
from tests.test_agent import fixture_snapshot


def planned_query(*, limit: int = 20, semantic: bool = False) -> dict:
    return {
        "schema": "qlkg-retrieval-plan-v1",
        "question": "How does beta depend on alpha?",
        "namespace": "personal",
        "identity_queries": ["alpha"],
        "lexical_queries": ["countable closure"],
        "semantic_queries": ["measurable foundation"] if semantic else [],
        "graph": {
            "seed_ids": ["alpha"],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 1,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": limit,
    }


def provider_config() -> dict:
    return {
        "adapter": "deterministic-fixture",
        "model": "retrieval-v1",
        "dimensions": 2,
        "base_url": "http://127.0.0.1",
        "credential_env": "UNUSED_RETRIEVAL_KEY",
    }


class QueryOnlyProvider:
    name = "deterministic-fixture"
    model = "retrieval-v1"
    dimensions = 2

    def __init__(self, config: dict, *, failure: Exception | None = None) -> None:
        self.provider_config_sha256 = provider_config_sha256(config)
        self.failure = failure
        self.query_batches: list[list[str]] = []

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.query_batches.append(list(texts))
        if self.failure is not None:
            raise self.failure
        return [[1.0, 0.0] for _ in texts]


class RetrievalPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "knowledge.sqlite"
        write_agent_index(self.database, fixture_snapshot())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install_vectors(self, config: dict | None = None) -> None:
        config = config or provider_config()
        digest = provider_config_sha256(config)
        inventory = embedding_inventory(self.database)
        vectors = {"alpha": [1.0, 0.0], "beta": [0.0, 1.0]}
        records = [
            {
                "namespace": "personal",
                "node_id": node["node_id"],
                "provider": "deterministic-fixture",
                "model": "retrieval-v1",
                "dimensions": 2,
                "embedding_input_schema": "qlkg-node-embedding-text-v1",
                "provider_config_sha256": digest,
                "content_sha256": node["content_sha256"],
                "vector": vectors[node["node_id"]],
            }
            for node in inventory["nodes"]
        ]
        outcome = install_embedding_records(
            self.database,
            records,
            expected_snapshot_sha256=inventory["snapshot_sha256"],
            expected_graph_sha256=inventory["graph_sha256"],
        )
        self.assertEqual("installed", outcome["status"])

    def _registry(self, provider: QueryOnlyProvider, creates: list[str]) -> ProviderAdapterRegistry:
        registry = ProviderAdapterRegistry()

        def factory(profile_name: str, config: dict, credential: str) -> QueryOnlyProvider:
            creates.append(profile_name)
            return provider

        registry.register("deterministic-fixture", factory, requires_credential=False)
        return registry

    def test_planned_hybrid_search_returns_versioned_per_lane_evidence(self) -> None:
        before = self.database.read_bytes()

        execution = execute_retrieval_plan(
            self.database,
            planned_query(),
            expected_graph_sha256="a" * 64,
        )

        self.assertEqual("qlkg-search-execution-v1", execution["schema"])
        self.assertEqual("planned", execution["plan_mode"])
        self.assertEqual("personal", execution["namespace"])
        validate_contract(execution)
        validate_contract(execution["result"])
        lanes = execution["result"]["lanes"]
        self.assertEqual("enabled", lanes["identity"]["status"])
        self.assertEqual("enabled", lanes["lexical"]["status"])
        self.assertEqual("disabled", lanes["semantic"]["status"])
        self.assertEqual("enabled", lanes["graph"]["status"])
        self.assertEqual("enabled", lanes["ppr"]["status"])
        by_id = {item["node_id"]: item for item in execution["result"]["results"]}
        self.assertIn("alpha", by_id)
        self.assertIn("beta", by_id)
        self.assertIn("identity", by_id["alpha"]["lanes"])
        self.assertIn("lexical", by_id["alpha"]["lanes"])
        self.assertEqual("weighted", by_id["alpha"]["fusion"]["method"])
        self.assertIn("graph", by_id["beta"]["lanes"])
        self.assertEqual(before, self.database.read_bytes())

    def test_semantic_queries_use_one_query_only_batch_and_materialized_rows(self) -> None:
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config)
        creates: list[str] = []

        execution = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
            expected_graph_sha256="a" * 64,
        )

        self.assertEqual(["fixture"], creates)
        self.assertEqual([["measurable foundation"]], provider.query_batches)
        self.assertEqual("enabled", execution["result"]["lanes"]["semantic"]["status"])
        self.assertIn("semantic", execution["result"]["results"][0]["lanes"])

    def test_absent_ready_vectors_degrade_without_creating_a_provider(self) -> None:
        config = provider_config()
        provider = QueryOnlyProvider(config)
        creates: list[str] = []

        execution = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        lane = execution["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", lane["status"])
        self.assertEqual("vector-space-unavailable", lane["reason"])
        self.assertEqual([], creates)
        self.assertEqual([], provider.query_batches)

    def test_partial_vector_coverage_is_observably_degraded_but_still_usable(self) -> None:
        config = provider_config()
        digest = provider_config_sha256(config)
        inventory = embedding_inventory(self.database)
        alpha = next(node for node in inventory["nodes"] if node["node_id"] == "alpha")
        install_embedding_records(
            self.database,
            [
                {
                    "namespace": "personal",
                    "node_id": "alpha",
                    "provider": "deterministic-fixture",
                    "model": "retrieval-v1",
                    "dimensions": 2,
                    "embedding_input_schema": "qlkg-node-embedding-text-v1",
                    "provider_config_sha256": digest,
                    "content_sha256": alpha["content_sha256"],
                    "vector": [1.0, 0.0],
                }
            ],
            expected_snapshot_sha256=inventory["snapshot_sha256"],
            expected_graph_sha256=inventory["graph_sha256"],
        )
        provider = QueryOnlyProvider(config)
        creates: list[str] = []

        execution = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        lane = execution["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", lane["status"])
        self.assertEqual("coverage-insufficient", lane["reason"])
        self.assertEqual(1, lane["results"])
        self.assertEqual(["fixture"], creates)
        self.assertEqual([["measurable foundation"]], provider.query_batches)

    def test_existing_other_profile_and_stale_coverage_have_distinct_reasons(self) -> None:
        self._install_vectors()
        alternate = dict(provider_config())
        alternate["model"] = "retrieval-v2"
        provider = QueryOnlyProvider(alternate)
        creates: list[str] = []

        mismatch = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="alternate",
            provider_config=alternate,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        self.assertEqual(
            "profile-mismatch", mismatch["result"]["lanes"]["semantic"]["reason"]
        )
        self.assertEqual([], creates)

        partially_changed = fixture_snapshot()
        partially_changed["nodes"][0]["text"] = "Changed alpha embedding input."
        partially_changed.pop("snapshot_sha256")
        partially_changed["snapshot_sha256"] = sha256_json(partially_changed)
        write_agent_index(self.database, partially_changed)
        partial_provider = QueryOnlyProvider(provider_config())
        partial_creates: list[str] = []
        partial = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=provider_config(),
            provider_registry=self._registry(partial_provider, partial_creates),
            environ={},
        )
        self.assertEqual("degraded", partial["result"]["lanes"]["semantic"]["status"])
        self.assertEqual(
            "coverage-insufficient",
            partial["result"]["lanes"]["semantic"]["reason"],
        )
        self.assertEqual([["measurable foundation"]], partial_provider.query_batches)

        changed = fixture_snapshot()
        for node in changed["nodes"]:
            node["text"] = f"Changed canonical embedding input for {node['id']}."
        changed.pop("snapshot_sha256")
        changed["snapshot_sha256"] = sha256_json(changed)
        write_agent_index(self.database, changed)
        stale_provider = QueryOnlyProvider(provider_config())
        stale_creates: list[str] = []
        insufficient = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=provider_config(),
            provider_registry=self._registry(stale_provider, stale_creates),
            environ={},
        )

        self.assertEqual(
            "coverage-insufficient",
            insufficient["result"]["lanes"]["semantic"]["reason"],
        )
        self.assertEqual([], stale_creates)

    def test_provider_metadata_mismatch_degrades_before_query_call(self) -> None:
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config)
        provider.model = "wrong-space"
        creates: list[str] = []

        execution = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        lane = execution["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", lane["status"])
        self.assertEqual("profile-mismatch", lane["reason"])
        self.assertEqual([], provider.query_batches)

    def test_include_stale_controls_current_vectors_for_review_nodes(self) -> None:
        snapshot = fixture_snapshot()
        for node in snapshot["nodes"]:
            node["properties"]["curation_status"] = "needs-review"
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config)
        creates: list[str] = []
        plan = planned_query(semantic=True)
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"]["seed_ids"] = []

        excluded = execute_retrieval_plan(
            self.database,
            plan,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )
        self.assertEqual(
            "vector-space-unavailable",
            excluded["result"]["lanes"]["semantic"]["reason"],
        )
        self.assertEqual([], creates)

        plan["filters"]["include_stale"] = True
        included = execute_retrieval_plan(
            self.database,
            plan,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )
        self.assertEqual("enabled", included["result"]["lanes"]["semantic"]["status"])
        self.assertEqual([["measurable foundation"]], provider.query_batches)

    def test_query_provider_failure_is_secret_safe_and_does_not_publish(self) -> None:
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config, failure=RuntimeError("SECRET_QUERY_SENTINEL"))
        creates: list[str] = []
        before_token = index_generation_token(self.database)

        execution = execute_retrieval_plan(
            self.database,
            planned_query(semantic=True),
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        rendered = json.dumps(execution, sort_keys=True)
        self.assertNotIn("SECRET_QUERY_SENTINEL", rendered)
        lane = execution["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", lane["status"])
        self.assertEqual("semantic-query-failed", lane["reason"])
        self.assertEqual(before_token, index_generation_token(self.database))

    def test_ambiguous_identity_over_limit_never_selects_one_candidate(self) -> None:
        plan = planned_query(limit=1)
        plan["identity_queries"] = ["Shared concept"]
        plan["lexical_queries"] = ["countable closure"]
        plan["semantic_queries"] = []
        plan["graph"]["seed_ids"] = []

        execution = execute_retrieval_plan(self.database, plan)

        identity = execution["result"]["lanes"]["identity"]
        self.assertEqual("degraded", identity["status"])
        self.assertEqual("ambiguous-identity-result-limit", identity["reason"])
        self.assertEqual(["alpha", "beta"], execution["identity_resolutions"][0]["candidate_ids"])
        self.assertFalse(execution["identity_resolutions"][0]["overflow"])
        self.assertEqual(
            "identity-ambiguity",
            execution["result"]["lanes"]["lexical"]["reason"],
        )
        self.assertEqual([], execution["result"]["results"])

    def test_exact_identity_is_protected_from_cross_lane_score_pressure(self) -> None:
        alpha = {"id": "alpha", "type": "knowledge", "label": "Alpha"}
        beta = {"id": "beta", "type": "knowledge", "label": "Beta"}
        lane_values = {
            "identity": [{"node": alpha, "score": 1.0, "best_raw_score": 1.0}],
            "lexical": [{"node": beta, "score": 1.0, "best_raw_score": 1.0}],
            "semantic": [{"node": beta, "score": 1.0, "best_raw_score": 1.0}],
            "graph": [{"node": beta, "score": 1.0, "best_raw_score": 1.0}],
            "ppr": [{"node": beta, "score": 1.0, "best_raw_score": 1.0}],
        }

        result = _fused_results(
            lane_values,
            limit=1,
            protected_identity_ids={"alpha"},
            seed_evidence={},
            path_evidence={},
        )

        self.assertEqual(["alpha"], [item["node_id"] for item in result])

    def test_semantic_similarity_cannot_collapse_an_over_budget_ambiguity(self) -> None:
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config)
        creates: list[str] = []
        plan = planned_query(limit=1, semantic=True)
        plan["identity_queries"] = ["Shared concept"]
        plan["lexical_queries"] = []
        plan["graph"]["seed_ids"] = []

        execution = execute_retrieval_plan(
            self.database,
            plan,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        self.assertEqual([], creates)
        self.assertEqual([], provider.query_batches)
        lane = execution["result"]["lanes"]["semantic"]
        self.assertEqual("degraded", lane["status"])
        self.assertEqual("identity-ambiguity", lane["reason"])
        self.assertEqual([], execution["result"]["results"])

    def test_identity_overflow_suppresses_unknown_tail_from_automatic_lanes(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["nodes"] = [
            {
                "id": f"node-{index:03d}",
                "type": "knowledge",
                "label": "Overflow Name",
                "text": f"Overflow candidate {index}.",
                "properties": {
                    "aliases": [],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {
                    "authority": "notes/overflow.md",
                    "line": index + 1,
                },
            }
            for index in range(501)
        ]
        snapshot["edges"] = []
        snapshot["references"] = []
        snapshot["graph"]["counts"] = {
            "nodes": 501,
            "edges": 0,
            "references": 0,
        }
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        config = provider_config()
        digest = provider_config_sha256(config)
        inventory = embedding_inventory(self.database)
        install_embedding_records(
            self.database,
            [
                {
                    "namespace": "personal",
                    "node_id": node["node_id"],
                    "provider": "deterministic-fixture",
                    "model": "retrieval-v1",
                    "dimensions": 2,
                    "embedding_input_schema": "qlkg-node-embedding-text-v1",
                    "provider_config_sha256": digest,
                    "content_sha256": node["content_sha256"],
                    "vector": (
                        [1.0, 0.0]
                        if node["node_id"] == "node-500"
                        else [0.0, 1.0]
                    ),
                }
                for node in inventory["nodes"]
            ],
            expected_snapshot_sha256=inventory["snapshot_sha256"],
            expected_graph_sha256=inventory["graph_sha256"],
        )
        plan = planned_query(limit=1, semantic=True)
        plan["identity_queries"] = ["Overflow Name"]
        plan["lexical_queries"] = ["Overflow candidate 500"]
        plan["graph"]["seed_ids"] = []
        provider = QueryOnlyProvider(config)
        creates: list[str] = []

        execution = execute_retrieval_plan(
            self.database,
            plan,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        self.assertTrue(execution["identity_resolutions"][0]["overflow"])
        self.assertEqual([], execution["result"]["results"])
        self.assertEqual(
            "identity-ambiguity",
            execution["result"]["lanes"]["semantic"]["reason"],
        )
        self.assertEqual([], creates)
        self.assertEqual([], provider.query_batches)

    def test_thirty_two_semantic_queries_share_one_bounded_query_batch(self) -> None:
        self._install_vectors()
        config = provider_config()
        provider = QueryOnlyProvider(config)
        creates: list[str] = []
        plan = planned_query(semantic=True)
        plan["semantic_queries"] = [f"semantic query {index}" for index in range(32)]

        execution = execute_retrieval_plan(
            self.database,
            plan,
            embedding_profile="fixture",
            provider_config=config,
            provider_registry=self._registry(provider, creates),
            environ={},
        )

        self.assertEqual(1, len(provider.query_batches))
        self.assertEqual(32, len(provider.query_batches[0]))
        self.assertEqual(32, execution["result"]["lanes"]["semantic"]["queries"])

    def test_generation_change_between_lanes_rejects_mixed_evidence(self) -> None:
        import kgdistiller.retrieval as retrieval_module

        original = retrieval_module.search_index
        replacement = fixture_snapshot()
        replacement["nodes"][0]["label"] = "Renamed alpha"
        replacement.pop("snapshot_sha256", None)
        replacement["snapshot_sha256"] = retrieval_module.sha256_json(replacement)
        called = False

        def concurrent_search(*args: object, **kwargs: object) -> list[dict]:
            nonlocal called
            if not called:
                called = True
                write_agent_index(self.database, replacement)
            return original(*args, **kwargs)

        with patch.object(retrieval_module, "search_index", side_effect=concurrent_search):
            with self.assertRaisesRegex(RetrievalError, "stale-generation"):
                execute_retrieval_plan(self.database, planned_query())

    def test_graph_lane_applies_node_type_filter_to_expansion_results(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["nodes"].append(
            {
                "id": "field-x",
                "type": "field",
                "label": "Field X",
                "text": "A field-only node.",
                "properties": {
                    "aliases": [],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {"authority": "notes/field.md", "line": 1},
            }
        )
        snapshot["edges"].append(
            {
                "source": "beta",
                "relation": "derived-from",
                "target": "field-x",
                "origin": "agent",
                "confidence": "high",
                "evidence": "Fixture field edge.",
                "curation_status": "current",
            }
        )
        snapshot["graph"]["counts"] = {"nodes": 3, "edges": 2, "references": 1}
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        plan = planned_query()
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": ["beta"],
                "edge_types": ["derived-from"],
                "direction": "out",
                "strategy": "bfs",
            }
        )

        execution = execute_retrieval_plan(self.database, plan)

        self.assertEqual(0, execution["result"]["lanes"]["graph"]["results"])
        self.assertNotIn(
            "field-x", {item["node_id"] for item in execution["result"]["results"]}
        )

    def test_ppr_evidence_attributes_each_disconnected_component_to_its_seed(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["nodes"] = [
            {
                "id": node_id,
                "type": "knowledge",
                "label": node_id.replace("-", " ").title(),
                "text": f"Evidence for {node_id}.",
                "properties": {
                    "aliases": [],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {"authority": "notes/ppr.md", "line": index + 1},
            }
            for index, node_id in enumerate(
                ["seed-one", "node-one", "seed-two", "node-two"]
            )
        ]
        snapshot["edges"] = [
            {
                "source": "seed-one",
                "relation": "prerequisite-for",
                "target": "node-one",
                "origin": "agent",
                "confidence": "high",
                "evidence": "First disconnected component.",
                "curation_status": "current",
            },
            {
                "source": "seed-two",
                "relation": "prerequisite-for",
                "target": "node-two",
                "origin": "agent",
                "confidence": "high",
                "evidence": "Second disconnected component.",
                "curation_status": "current",
            },
        ]
        snapshot["references"] = []
        snapshot["graph"]["counts"] = {"nodes": 4, "edges": 2, "references": 0}
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        plan = planned_query(limit=4)
        plan["identity_queries"] = []
        plan["lexical_queries"] = []
        plan["semantic_queries"] = []
        plan["graph"].update(
            {
                "seed_ids": ["seed-one", "seed-two"],
                "strategy": "ppr",
                "max_depth": 1,
            }
        )

        execution = execute_retrieval_plan(self.database, plan)
        by_id = {item["node_id"]: item for item in execution["result"]["results"]}

        for node_id in ("seed-two", "node-two"):
            self.assertEqual(
                [{"lane": "ppr", "seed_id": "seed-two"}],
                by_id[node_id]["seed_evidence"],
            )

    def test_context_uses_the_same_execution_generation_and_budget(self) -> None:
        plan = planned_query()
        execution = execute_retrieval_plan(self.database, plan)
        before_token = index_generation_token(self.database)

        bundle = build_context_from_execution(
            self.database, execution, plan=plan, token_budget=5000
        )

        self.assertEqual("qlkg-context-bundle-v1", bundle["schema"])
        self.assertEqual(execution["snapshot_sha256"], bundle["snapshot_sha256"])
        self.assertEqual("planned", bundle["retrieval"]["policy"]["plan_mode"])
        self.assertEqual(plan["question"], bundle["query"])
        self.assertLessEqual(bundle["budget"]["estimated_tokens"], 5000)
        self.assertEqual(
            estimate_tokens(bundle), bundle["budget"]["estimated_tokens"]
        )
        self.assertEqual(before_token, index_generation_token(self.database))

    def test_context_packs_ambiguous_identity_candidates_atomically(self) -> None:
        plan = planned_query(limit=2)
        plan["identity_queries"] = ["Shared concept"]
        plan["lexical_queries"] = []
        plan["semantic_queries"] = []
        plan["graph"]["seed_ids"] = []
        execution = execute_retrieval_plan(self.database, plan)

        bundle = build_context_from_execution(
            self.database, execution, plan=plan, token_budget=1400
        )

        self.assertEqual(
            execution["identity_resolutions"],
            bundle["retrieval"]["policy"]["identity_resolutions"],
        )
        seeds = set(bundle["seeds"])
        self.assertIn(seeds, (set(), {"alpha", "beta"}))
        if not seeds:
            self.assertIn(
                "ambiguous-group-budget",
                {item["reason"] for item in bundle["omissions"]},
            )

    def test_context_never_treats_a_truncated_ambiguous_set_as_complete(self) -> None:
        plan = planned_query(limit=2)
        plan["identity_queries"] = ["Shared concept"]
        plan["lexical_queries"] = []
        plan["semantic_queries"] = []
        plan["graph"]["seed_ids"] = []
        execution = execute_retrieval_plan(self.database, plan)
        execution["identity_resolutions"][0]["candidate_ids"] = [
            "alpha",
            "beta",
            *[f"candidate-{index}" for index in range(498)],
        ]
        execution["identity_resolutions"][0]["overflow"] = True

        bundle = build_context_from_execution(
            self.database, execution, plan=plan, token_budget=200_000
        )

        self.assertEqual([], bundle["nodes"])
        self.assertEqual([], bundle["seeds"])
        self.assertIn(
            "ambiguous-group-overflow",
            {item["reason"] for item in bundle["omissions"]},
        )

    def test_context_respects_the_plan_stale_edge_filter(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["edges"][0]["curation_status"] = "needs-review"
        snapshot.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        write_agent_index(self.database, snapshot)
        plan = planned_query(limit=2)
        plan["identity_queries"] = ["alpha", "beta"]
        plan["lexical_queries"] = []
        plan["semantic_queries"] = []
        plan["graph"]["seed_ids"] = []

        current_execution = execute_retrieval_plan(self.database, plan)
        current_bundle = build_context_from_execution(
            self.database,
            current_execution,
            plan=plan,
            token_budget=5000,
        )
        self.assertEqual([], current_bundle["edges"])

        plan["filters"]["include_stale"] = True
        stale_execution = execute_retrieval_plan(self.database, plan)
        stale_bundle = build_context_from_execution(
            self.database,
            stale_execution,
            plan=plan,
            token_budget=5000,
        )
        self.assertEqual(1, len(stale_bundle["edges"]))
        self.assertEqual("needs-review", stale_bundle["edges"][0]["curation_status"])

    def test_plan_loader_has_bounded_secret_safe_failures(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_bytes(b'{"secret":"SECRET_PLAN_SENTINEL",')
        with self.assertRaises(RetrievalError) as caught:
            load_retrieval_plan(malformed)
        self.assertEqual("invalid-plan", caught.exception.code)
        self.assertNotIn("SECRET_PLAN_SENTINEL", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

        oversized = self.root / "oversized.json"
        oversized.write_bytes(b"x" * (MAX_RETRIEVAL_PLAN_BYTES + 1))
        with self.assertRaisesRegex(RetrievalError, "plan-too-large"):
            load_retrieval_plan(oversized)

    def test_legacy_input_reports_legacy_mode(self) -> None:
        plan = legacy_retrieval_plan("alpha", limit=2)
        execution = execute_retrieval_plan(self.database, plan, plan_mode="legacy")
        self.assertEqual("legacy", execution["plan_mode"])
        self.assertEqual("qlkg-search-result-v2", execution["result"]["schema"])

    def test_missing_or_stale_index_is_read_only(self) -> None:
        missing = self.root / "missing.sqlite"
        with self.assertRaisesRegex(RetrievalError, "index-unavailable"):
            execute_retrieval_plan(missing, planned_query())
        self.assertFalse(missing.exists())
        self.assertEqual([], list(self.root.glob("missing.sqlite*")))

        before = index_generation_token(self.database)
        with self.assertRaisesRegex(RetrievalError, "stale-index"):
            execute_retrieval_plan(
                self.database,
                planned_query(),
                expected_graph_sha256="b" * 64,
            )
        self.assertEqual(before, index_generation_token(self.database))


if __name__ == "__main__":
    unittest.main()
