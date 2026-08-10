from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import kgdistiller.embedding as embedding_module  # noqa: E402
from kgdistiller.agent import (  # noqa: E402
    embedding_inventory,
    resolve_agent_index_path,
    write_agent_index,
)
from kgdistiller.contracts import sha256_json  # noqa: E402
from kgdistiller.embedding import (  # noqa: E402
    EMBEDDING_INPUT_SCHEMA,
    MAX_EMBEDDING_POLICY_BYTES,
    EmbeddingError,
    embedding_status,
    load_embedding_policy,
    sync_embeddings,
)
from kgdistiller.providers import ProviderError, provider_config_sha256  # noqa: E402


def provider_config(
    *,
    adapter: str = "deterministic-fixture",
    model: str = "fixture-v1",
    dimensions: int = 4,
    base_url: str = "https://fixture.example/v1",
) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "model": model,
        "dimensions": dimensions,
        "base_url": base_url,
        "credential_env": "FIXTURE_EMBEDDING_KEY",
    }


def embedding_policy(
    *profiles: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "qlkg-embedding-policy-v1",
        "profiles": list(profiles),
    }


def policy_profile(
    name: str = "primary",
    *,
    provider: str = "deterministic-fixture",
    model: str = "fixture-v1",
    dimensions: int = 4,
    node_types: list[str] | None = None,
    minimum_coverage: float = 1.0,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "required_node_types": node_types or ["knowledge"],
        "minimum_coverage": minimum_coverage,
        "required": required,
    }


def agent_snapshot(
    count: int,
    *,
    graph_marker: str = "initial",
) -> dict[str, Any]:
    nodes = [
        {
            "id": f"node-{index:04d}",
            "type": "knowledge",
            "label": f"Node {index}",
            "text": f"Canonical fixture text {index}",
            "properties": {
                "aliases": [],
                "curation_status": "current",
                "source_status": "active",
            },
            "provenance": {"authority": "notes/fixture.md", "line": index + 1},
        }
        for index in range(count)
    ]
    payload = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "personal",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": hashlib.sha256(graph_marker.encode("utf-8")).hexdigest(),
            "counts": {"nodes": count, "edges": 0, "references": 0},
        },
        "nodes": nodes,
        "edges": [],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def refinalize_snapshot(snapshot: dict[str, Any], graph_marker: str) -> None:
    snapshot["graph"]["sha256"] = hashlib.sha256(
        graph_marker.encode("utf-8")
    ).hexdigest()
    snapshot.pop("snapshot_sha256", None)
    snapshot["snapshot_sha256"] = sha256_json(snapshot)


class RecordingProvider:
    def __init__(
        self,
        owner: "RecordingRegistry",
        profile_name: str,
        config: dict[str, Any],
    ) -> None:
        self._owner = owner
        self.profile_name = profile_name
        self.name = str(config["adapter"])
        self.model = str(config["model"])
        self.dimensions = int(config["dimensions"])
        self.provider_config_sha256 = provider_config_sha256(config)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._owner.calls.append(list(texts))
        if self._owner.started is not None:
            self._owner.started.set()
        if self._owner.release is not None:
            if not self._owner.release.wait(10):
                raise RuntimeError("fixture provider release timed out")
        if self._owner.failures:
            raise self._owner.failures.pop(0)
        if self._owner.response is not None:
            return self._owner.response(texts, len(self._owner.calls))
        return [self._owner.vector(text) for text in texts]


class RecordingRegistry:
    def __init__(
        self,
        *,
        response: Callable[[list[str], int], list[list[float]]] | None = None,
        failures: list[Exception] | None = None,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.response = response
        self.failures = list(failures or [])
        self.started = started
        self.release = release
        self.calls: list[list[str]] = []
        self.create_calls: list[tuple[str, dict[str, Any], Any]] = []
        self._dimensions = 0

    def create(
        self,
        profile_name: str,
        config: dict[str, Any],
        *,
        environ: Any = None,
    ) -> RecordingProvider:
        copied = dict(config)
        self.create_calls.append((profile_name, copied, environ))
        self._dimensions = int(copied["dimensions"])
        return RecordingProvider(self, profile_name, copied)

    def vector(self, text: str) -> list[float]:
        return [1.0 + ((len(text) + index) % 7) / 10.0 for index in range(self._dimensions)]


def physical_vector(database: Path, node_id: str) -> bytes:
    connection = sqlite3.connect(resolve_agent_index_path(database))
    try:
        row = connection.execute(
            "SELECT vector FROM embeddings WHERE node_id = ? ORDER BY provider_config_sha256",
            (node_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"missing vector for {node_id}")
        return bytes(row[0])
    finally:
        connection.close()


class EmbeddingStatusTest(unittest.TestCase):
    def test_policy_loader_is_bounded_and_validates_contract(self) -> None:
        policy = embedding_policy(policy_profile())
        with tempfile.TemporaryDirectory(prefix="kgdistiller-policy-test-") as temporary:
            root = Path(temporary)
            valid = root / "policy.json"
            valid.write_text(json.dumps(policy), encoding="utf-8")
            self.assertEqual(policy, load_embedding_policy(valid))

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (MAX_EMBEDDING_POLICY_BYTES + 1))
            with self.assertRaises(EmbeddingError) as raised:
                load_embedding_policy(oversized)
            self.assertEqual("embedding-policy-too-large", raised.exception.code)

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaises(EmbeddingError) as raised:
                load_embedding_policy(malformed)
            self.assertEqual("invalid-embedding-policy", raised.exception.code)

        def endless_profiles() -> Any:
            while True:
                yield "primary"

        with self.assertRaises(EmbeddingError) as raised:
            embedding_status(
                Path("unused.sqlite"),
                policy,
                {"primary": provider_config()},
                profile_names=endless_profiles(),
            )
        self.assertEqual("invalid-embedding-request", raised.exception.code)

        with self.assertRaises(EmbeddingError) as raised:
            embedding_status(Path("unused.sqlite"), policy, [])  # type: ignore[arg-type]
        self.assertEqual("invalid-embedding-request", raised.exception.code)

    def test_status_classifies_five_states_and_groups_coverage(self) -> None:
        primary_config = provider_config()
        empty_config = provider_config(model="empty-v1")
        primary_digest = provider_config_sha256(primary_config)

        def node(
            node_id: str,
            node_type: str,
            text: str,
            *,
            source_status: str = "active",
            provenance_active: bool = True,
        ) -> dict[str, Any]:
            return {
                "node_id": node_id,
                "type": node_type,
                "properties": {
                    "curation_status": "current",
                    "source_status": source_status,
                },
                "provenance": {"active": provenance_active},
                "text": text,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }

        nodes = [
            node("k-ready", "knowledge", "ready text"),
            node("k-stale", "knowledge", "stale current text"),
            node("k-missing", "knowledge", "missing text"),
            node("k-invalid", "knowledge", "invalid text"),
            node("k-wrong", "knowledge", "wrong binding text"),
            node("f-ready", "field", "field text"),
            node("orphan", "knowledge", "orphan text", source_status="orphaned"),
            node("inactive", "knowledge", "inactive text", provenance_active=False),
            node("empty", "knowledge", ""),
        ]
        by_id = {item["node_id"]: item for item in nodes}

        def record(
            node_id: str,
            *,
            content_sha256: str | None = None,
            dimensions: int = 4,
            config_digest: str = primary_digest,
            vector_valid: bool = True,
            provider: str = "deterministic-fixture",
            model: str = "fixture-v1",
        ) -> dict[str, Any]:
            return {
                "node_id": node_id,
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
                "embedding_input_schema": EMBEDDING_INPUT_SCHEMA,
                "provider_config_sha256": config_digest,
                "content_sha256": content_sha256 or by_id[node_id]["content_sha256"],
                "vector_valid": vector_valid,
            }

        inventory = {
            "snapshot_sha256": "1" * 64,
            "graph_sha256": "2" * 64,
            "nodes": nodes,
            "records": [
                record("k-ready"),
                record("k-stale", content_sha256="3" * 64),
                record("k-invalid", vector_valid=False),
                record("k-wrong", dimensions=3, config_digest="4" * 64),
                record("f-ready"),
                record(
                    "orphan",
                    provider="unmanaged-provider",
                    model="unmanaged-model",
                    config_digest="5" * 64,
                ),
            ],
        }
        policy = embedding_policy(
            policy_profile(
                node_types=["knowledge", "field"], minimum_coverage=0.3
            ),
            policy_profile(
                "empty",
                model="empty-v1",
                node_types=["topic"],
            ),
        )
        configs = {"primary": primary_config, "empty": empty_config}
        os.environ["FIXTURE_EMBEDDING_KEY"] = "secret-status-sentinel"
        try:
            with patch.object(embedding_module, "_read_inventory", return_value=inventory):
                status = embedding_status(Path("unused.sqlite"), policy, configs)
        finally:
            os.environ.pop("FIXTURE_EMBEDDING_KEY", None)

        profiles = {profile["name"]: profile for profile in status["profiles"]}
        primary = profiles["primary"]
        self.assertEqual(6, primary["eligible"])
        self.assertEqual(2, primary["ready"])
        self.assertEqual(3, primary["missing"])
        self.assertEqual(1, primary["stale"])
        self.assertEqual(2, primary["vector_records"]["incompatible"])
        self.assertEqual(
            primary["eligible"],
            primary["ready"] + primary["missing"] + primary["stale"],
        )
        self.assertAlmostEqual(1.0 / 3.0, primary["coverage"])
        self.assertEqual("ready", primary["readiness"])
        by_type = {item["node_type"]: item for item in primary["node_types"]}
        self.assertEqual(5, by_type["knowledge"]["eligible"])
        self.assertEqual(1, by_type["field"]["ready"])
        self.assertEqual("not-applicable", profiles["empty"]["readiness"])
        self.assertIsNone(profiles["empty"]["coverage"])
        self.assertEqual("ready", status["readiness"])
        self.assertEqual(1, status["unmanaged"]["records"])
        rendered = json.dumps(status, sort_keys=True)
        self.assertNotIn("ready text", rendered)
        self.assertNotIn("secret-status-sentinel", rendered)

    def test_real_inventory_excludes_explicitly_inactive_nodes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="kgdistiller-inactive-status-test-"
        ) as temporary:
            database = Path(temporary) / "knowledge.sqlite"
            snapshot = agent_snapshot(1)
            snapshot["nodes"][0]["provenance"]["active"] = False
            snapshot.pop("snapshot_sha256")
            snapshot["snapshot_sha256"] = sha256_json(snapshot)
            write_agent_index(database, snapshot)

            status = embedding_status(
                database,
                embedding_policy(policy_profile()),
                {"primary": provider_config()},
            )

        profile = status["profiles"][0]
        self.assertEqual(0, profile["eligible"])
        self.assertEqual("not-applicable", profile["readiness"])


class EmbeddingSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-embedding-test-")
        self.database = Path(self.temporary.name) / "knowledge.sqlite"
        self.config = provider_config()
        self.configs = {"primary": self.config}
        self.policy = embedding_policy(policy_profile())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_257_nodes_are_batched_and_second_sync_is_zero_call_zero_publish(self) -> None:
        write_agent_index(self.database, agent_snapshot(257))
        registry = RecordingRegistry()

        first = sync_embeddings(
            self.database,
            self.policy,
            self.configs,
            registry=registry,
            batch_size=128,
            max_nodes=300,
        )

        self.assertEqual([128, 128, 1], [len(batch) for batch in registry.calls])
        self.assertTrue(
            all(
                sum(len(text.encode("utf-8")) for text in batch) <= 1024 * 1024
                for batch in registry.calls
            )
        )
        self.assertEqual(257, first["installed"])
        self.assertEqual(3, first["batches"])
        published = resolve_agent_index_path(self.database)

        unused_registry = RecordingRegistry()
        second = sync_embeddings(
            self.database,
            self.policy,
            self.configs,
            registry=unused_registry,
            batch_size=128,
            max_nodes=300,
        )

        self.assertEqual("unchanged", second["status"])
        self.assertEqual(0, second["embedded"])
        self.assertEqual([], unused_registry.create_calls)
        self.assertEqual([], unused_registry.calls)
        self.assertEqual(published, resolve_agent_index_path(self.database))
        self.assertEqual(
            257,
            embedding_status(self.database, self.policy, self.configs)["profiles"][0][
                "ready"
            ],
        )

    def test_not_applicable_sync_needs_no_provider_configuration(self) -> None:
        write_agent_index(self.database, agent_snapshot(1))
        policy = embedding_policy(policy_profile(node_types=["topic"]))
        registry = RecordingRegistry()

        result = sync_embeddings(
            self.database,
            policy,
            {},
            registry=registry,
        )

        self.assertEqual("unchanged", result["status"])
        self.assertEqual("not-applicable", result["embedding_status"]["readiness"])
        self.assertEqual([], registry.create_calls)

    def test_one_node_change_is_stale_and_preserves_other_vector_bytes(self) -> None:
        snapshot = agent_snapshot(2)
        write_agent_index(self.database, snapshot)
        sync_embeddings(
            self.database,
            self.policy,
            self.configs,
            registry=RecordingRegistry(),
        )
        unchanged_before = physical_vector(self.database, "node-0001")

        snapshot["nodes"][0]["text"] = "Changed canonical text"
        refinalize_snapshot(snapshot, "one-node-change")
        write_agent_index(self.database, snapshot)
        status = embedding_status(self.database, self.policy, self.configs)["profiles"][0]

        self.assertEqual(1, status["ready"])
        self.assertEqual(1, status["stale"])
        self.assertEqual(0, status["missing"])
        self.assertEqual(
            unchanged_before, physical_vector(self.database, "node-0001")
        )

        registry = RecordingRegistry()
        result = sync_embeddings(
            self.database,
            self.policy,
            self.configs,
            registry=registry,
        )
        self.assertEqual(1, result["embedded"])
        self.assertEqual([["Node 0\nChanged canonical text"]], registry.calls)
        self.assertEqual(
            unchanged_before, physical_vector(self.database, "node-0001")
        )
        refreshed = embedding_status(self.database, self.policy, self.configs)["profiles"][0]
        self.assertEqual(2, refreshed["ready"])
        self.assertEqual(0, refreshed["stale"])

    def test_provider_config_switch_is_missing_and_incompatible_then_syncs(self) -> None:
        policy = embedding_policy(
            policy_profile(provider="openai-compatible", model="switch-v1")
        )
        config_a = provider_config(
            adapter="openai-compatible",
            model="switch-v1",
            base_url="https://a.example/v1",
        )
        config_b = provider_config(
            adapter="openai-compatible",
            model="switch-v1",
            base_url="https://b.example/v1",
        )
        write_agent_index(self.database, agent_snapshot(1))
        sync_embeddings(
            self.database,
            policy,
            {"primary": config_a},
            registry=RecordingRegistry(),
        )

        switched = embedding_status(
            self.database, policy, {"primary": config_b}
        )["profiles"][0]
        self.assertEqual(1, switched["missing"])
        self.assertEqual(1, switched["vector_records"]["incompatible"])
        registry = RecordingRegistry()
        sync_embeddings(
            self.database,
            policy,
            {"primary": config_b},
            registry=registry,
        )
        self.assertEqual(1, len(registry.calls))
        current = embedding_status(
            self.database, policy, {"primary": config_b}
        )["profiles"][0]
        self.assertEqual(1, current["ready"])
        self.assertEqual(0, current["missing"])
        self.assertEqual(0, current["vector_records"]["incompatible"])

    def test_bad_second_batch_installs_nothing(self) -> None:
        write_agent_index(self.database, agent_snapshot(129))
        original = resolve_agent_index_path(self.database)

        def response(texts: list[str], call: int) -> list[list[float]]:
            if call == 2:
                return [[float("nan"), 1.0, 1.0, 1.0] for _ in texts]
            return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

        registry = RecordingRegistry(response=response)
        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                self.database,
                self.policy,
                self.configs,
                registry=registry,
                batch_size=128,
                max_nodes=200,
            )
        self.assertEqual("invalid-response", raised.exception.code)
        self.assertEqual([128, 1], [len(batch) for batch in registry.calls])
        self.assertEqual(original, resolve_agent_index_path(self.database))
        self.assertEqual([], embedding_inventory(self.database)["records"])

    def test_retry_codes_and_retry_bound(self) -> None:
        write_agent_index(self.database, agent_snapshot(1))
        registry = RecordingRegistry(
            failures=[
                ProviderError("provider-timeout", "embedding provider timed out"),
                ProviderError("provider-unavailable", "embedding provider unavailable"),
            ]
        )
        result = sync_embeddings(
            self.database,
            self.policy,
            self.configs,
            registry=registry,
            max_retries=2,
        )
        self.assertEqual(3, result["attempts"])
        self.assertEqual(3, len(registry.calls))

        other = Path(self.temporary.name) / "bounded.sqlite"
        write_agent_index(other, agent_snapshot(1, graph_marker="bounded"))
        bounded = RecordingRegistry(
            failures=[
                ProviderError("provider-timeout", "embedding provider timed out"),
                ProviderError("provider-timeout", "embedding provider timed out"),
                ProviderError("provider-timeout", "embedding provider timed out"),
            ]
        )
        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                other,
                self.policy,
                self.configs,
                registry=bounded,
                max_retries=1,
            )
        self.assertEqual("provider-timeout", raised.exception.code)
        self.assertEqual(2, len(bounded.calls))
        self.assertEqual([], embedding_inventory(other)["records"])

        invalid = Path(self.temporary.name) / "invalid.sqlite"
        write_agent_index(invalid, agent_snapshot(1, graph_marker="invalid"))
        invalid_registry = RecordingRegistry(
            failures=[
                ProviderError(
                    "invalid-response", "secret-provider-response-sentinel"
                )
            ]
        )
        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                invalid,
                self.policy,
                self.configs,
                registry=invalid_registry,
                max_retries=2,
            )
        self.assertEqual("invalid-response", raised.exception.code)
        self.assertNotIn("secret-provider-response-sentinel", str(raised.exception))
        self.assertNotIn(
            "secret-provider-response-sentinel",
            json.dumps(raised.exception.payload()),
        )
        self.assertEqual(1, len(invalid_registry.calls))
        self.assertEqual([], embedding_inventory(invalid)["records"])

    def test_adversarial_provider_values_are_mapped_to_stable_errors(self) -> None:
        class AdversarialFloat(float):
            def __float__(self) -> float:
                raise RuntimeError("secret-numeric-sentinel")

        write_agent_index(self.database, agent_snapshot(1))
        numeric_registry = RecordingRegistry(
            response=lambda texts, call: [
                [AdversarialFloat(1.0), 1.0, 1.0, 1.0] for _ in texts
            ]
        )
        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                self.database,
                self.policy,
                self.configs,
                registry=numeric_registry,
            )
        self.assertEqual("invalid-response", raised.exception.code)
        self.assertNotIn("secret-numeric-sentinel", str(raised.exception))
        self.assertEqual([], embedding_inventory(self.database)["records"])

        class AdversarialCode:
            def __str__(self) -> str:
                raise RuntimeError("secret-code-sentinel")

        other = Path(self.temporary.name) / "adversarial-code.sqlite"
        write_agent_index(other, agent_snapshot(1, graph_marker="adversarial-code"))
        provider_error = ProviderError("provider-timeout", "safe")
        provider_error.code = AdversarialCode()  # type: ignore[assignment]
        code_registry = RecordingRegistry(failures=[provider_error])
        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                other,
                self.policy,
                self.configs,
                registry=code_registry,
                max_retries=0,
            )
        self.assertEqual("provider-unavailable", raised.exception.code)
        rendered = json.dumps(raised.exception.payload(), sort_keys=True)
        self.assertNotIn("secret-code-sentinel", rendered)
        self.assertEqual([], embedding_inventory(other)["records"])

    def test_work_budgets_fail_before_provider_creation(self) -> None:
        write_agent_index(self.database, agent_snapshot(11))
        probes = (
            {"max_nodes": 10},
            {"max_input_bytes": 10},
            {"max_vector_bytes": 10},
            {"batch_size": 2, "max_batches": 5},
        )
        for options in probes:
            with self.subTest(options=options):
                registry = RecordingRegistry()
                with self.assertRaises(EmbeddingError) as raised:
                    sync_embeddings(
                        self.database,
                        self.policy,
                        self.configs,
                        registry=registry,
                        **options,
                    )
                self.assertEqual("work-budget-exceeded", raised.exception.code)
                self.assertEqual([], registry.create_calls)
                self.assertEqual([], registry.calls)
        self.assertEqual([], embedding_inventory(self.database)["records"])

    def test_sync_rejects_profiles_that_share_one_portable_logical_key(self) -> None:
        write_agent_index(self.database, agent_snapshot(1))
        policy = embedding_policy(
            policy_profile("primary"),
            policy_profile("secondary"),
        )
        configs = {
            "primary": provider_config(),
            "secondary": provider_config(base_url="https://other.example/v1"),
        }
        registry = RecordingRegistry()

        with self.assertRaises(EmbeddingError) as raised:
            sync_embeddings(
                self.database,
                policy,
                configs,
                registry=registry,
                profile_names=["primary", "secondary"],
            )

        self.assertEqual("conflicting-embedding-profiles", raised.exception.code)
        self.assertEqual([], registry.create_calls)

        disjoint_policy = embedding_policy(
            policy_profile("primary", node_types=["knowledge"]),
            policy_profile("secondary", node_types=["topic"]),
        )
        disjoint_registry = RecordingRegistry()
        result = sync_embeddings(
            self.database,
            disjoint_policy,
            configs,
            registry=disjoint_registry,
            profile_names=["primary", "secondary"],
        )
        self.assertEqual("installed", result["status"])
        self.assertEqual(1, len(disjoint_registry.create_calls))

    def test_graph_change_during_provider_work_rejects_stale_batch(self) -> None:
        snapshot = agent_snapshot(1)
        write_agent_index(self.database, snapshot)
        started = threading.Event()
        release = threading.Event()
        registry = RecordingRegistry(started=started, release=release)
        failures: list[BaseException] = []

        def worker() -> None:
            try:
                sync_embeddings(
                    self.database,
                    self.policy,
                    self.configs,
                    registry=registry,
                )
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(started.wait(5), "provider did not start")
        snapshot["nodes"][0]["text"] = "Graph changed while provider was running"
        refinalize_snapshot(snapshot, "race-change")
        write_agent_index(self.database, snapshot)
        graph_generation = resolve_agent_index_path(self.database)
        release.set()
        thread.join(10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], EmbeddingError)
        self.assertEqual("stale-generation", getattr(failures[0], "code", None))
        self.assertEqual(graph_generation, resolve_agent_index_path(self.database))
        inventory = embedding_inventory(self.database)
        self.assertEqual(snapshot["snapshot_sha256"], inventory["snapshot_sha256"])
        self.assertEqual([], inventory["records"])

    def test_graph_change_after_install_never_returns_a_mixed_receipt(self) -> None:
        write_agent_index(self.database, agent_snapshot(1))
        replacement = agent_snapshot(1, graph_marker="post-install-change")
        replacement["nodes"][0]["text"] = "Changed immediately after installation"
        refinalize_snapshot(replacement, "post-install-change")
        original_install = embedding_module._install_records

        def install_then_replace(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = original_install(*args, **kwargs)
            write_agent_index(self.database, replacement)
            return result

        with patch.object(
            embedding_module,
            "_install_records",
            side_effect=install_then_replace,
        ):
            with self.assertRaises(EmbeddingError) as raised:
                sync_embeddings(
                    self.database,
                    self.policy,
                    self.configs,
                    registry=RecordingRegistry(),
                )

        self.assertEqual("stale-generation", raised.exception.code)
        current = embedding_status(self.database, self.policy, self.configs)
        self.assertEqual(replacement["snapshot_sha256"], current["snapshot_sha256"])
        self.assertEqual(1, current["profiles"][0]["stale"])

    def test_competing_config_install_never_returns_false_success(self) -> None:
        write_agent_index(self.database, agent_snapshot(1))
        policy = embedding_policy(
            policy_profile(provider="openai-compatible", model="race-v1")
        )
        config_a = provider_config(
            adapter="openai-compatible",
            model="race-v1",
            base_url="https://race-a.example/v1",
        )
        config_b = provider_config(
            adapter="openai-compatible",
            model="race-v1",
            base_url="https://race-b.example/v1",
        )
        original_install = embedding_module._install_records
        competed = False

        def install_with_competitor(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal competed
            result = original_install(*args, **kwargs)
            if not competed:
                competed = True
                sync_embeddings(
                    self.database,
                    policy,
                    {"primary": config_b},
                    registry=RecordingRegistry(),
                )
            return result

        with patch.object(
            embedding_module,
            "_install_records",
            side_effect=install_with_competitor,
        ):
            with self.assertRaises(EmbeddingError) as raised:
                sync_embeddings(
                    self.database,
                    policy,
                    {"primary": config_a},
                    registry=RecordingRegistry(),
                )

        self.assertEqual("stale-generation", raised.exception.code)
        current_b = embedding_status(
            self.database, policy, {"primary": config_b}
        )["profiles"][0]
        current_a = embedding_status(
            self.database, policy, {"primary": config_a}
        )["profiles"][0]
        self.assertEqual(1, current_b["ready"])
        self.assertEqual(1, current_a["missing"])


if __name__ == "__main__":
    unittest.main()
