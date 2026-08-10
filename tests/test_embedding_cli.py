from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.agent import resolve_agent_index_path, write_agent_index  # noqa: E402
from kgdistiller.contracts import sha256_json  # noqa: E402


def fixture_snapshot() -> dict[str, Any]:
    nodes = [
        {
            "id": "alpha",
            "type": "knowledge",
            "label": "Alpha",
            "text": "The first active fixture concept.",
            "properties": {
                "aliases": ["First fixture"],
                "source_status": "active",
                "curation_status": "current",
            },
            "provenance": {"active": True, "authority": "notes/fixture.md"},
        },
        {
            "id": "beta",
            "type": "knowledge",
            "label": "Beta",
            "text": "The second active fixture concept.",
            "properties": {
                "aliases": [],
                "source_status": "active",
                "curation_status": "current",
            },
            "provenance": {"active": True, "authority": "notes/fixture.md"},
        },
    ]
    snapshot: dict[str, Any] = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "personal",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": "1" * 64,
            "counts": {"nodes": len(nodes), "edges": 0, "references": 0},
        },
        "nodes": nodes,
        "edges": [],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def policy_payload() -> dict[str, Any]:
    return {
        "schema": "qlkg-embedding-policy-v1",
        "profiles": [
            {
                "name": "primary",
                "provider": "deterministic-fixture",
                "model": "fixture-primary-v1",
                "dimensions": 4,
                "required_node_types": ["knowledge"],
                "minimum_coverage": 1.0,
                "required": True,
            },
            {
                "name": "secondary",
                "provider": "deterministic-fixture",
                "model": "fixture-secondary-v1",
                "dimensions": 3,
                "required_node_types": ["knowledge"],
                "minimum_coverage": 0.5,
                "required": False,
            },
        ],
    }


def local_profile_payload() -> dict[str, Any]:
    def provider(model: str, dimensions: int) -> dict[str, Any]:
        return {
            "adapter": "deterministic-fixture",
            "model": model,
            "dimensions": dimensions,
            "base_url": "https://fixture.invalid/v1",
            "credential_env": "EMBEDDING_TOKEN",
        }

    return {
        "schema": "qlkg-local-profile-v1",
        "database": "knowledge.sqlite",
        "portable_store": "portable",
        "embedding_profile": "primary",
        "provider_profiles": {
            "primary": provider("fixture-primary-v1", 4),
            "secondary": provider("fixture-secondary-v1", 3),
        },
    }


class EmbeddingCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="kgdistiller-embedding-cli-test-"
        )
        self.root = Path(self.temporary.name)
        self.profile_path = self.root / "knowledge/build/local-profile.json"
        self.database = self.profile_path.parent / "knowledge.sqlite"
        self.policy_path = self.root / "knowledge/embedding-policy.json"
        self.profile_path.parent.mkdir(parents=True)
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(
            json.dumps(local_profile_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.policy_path.write_text(
            json.dumps(policy_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        write_agent_index(self.database, fixture_snapshot())
        self.environment = dict(os.environ)
        existing_path = self.environment.get("PYTHONPATH", "")
        self.environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(REPO_ROOT / "src"), existing_path)
            if part
        )
        self.environment["EMBEDDING_TOKEN"] = "cli-secret-sentinel"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self,
        *arguments: str,
        expected_returncode: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.root),
                *arguments,
            ],
            cwd=self.root,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            expected_returncode,
            completed.returncode,
            msg=f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
        )
        stream = completed.stdout if expected_returncode == 0 else completed.stderr
        payload = json.loads(stream)
        self.assertNotIn("cli-secret-sentinel", completed.stdout)
        self.assertNotIn("cli-secret-sentinel", completed.stderr)
        return completed, payload

    @staticmethod
    def profile_names(status: dict[str, Any]) -> set[str]:
        profiles = status["profiles"]
        if isinstance(profiles, dict):
            return set(profiles)
        return {str(profile["name"]) for profile in profiles}

    def embedding_rows(self) -> list[tuple[str, str, int, bytes]]:
        connection = sqlite3.connect(resolve_agent_index_path(self.database))
        try:
            return [
                (str(provider), str(model), int(dimensions), bytes(vector))
                for provider, model, dimensions, vector in connection.execute(
                    """
                    SELECT provider, model, dimensions, vector
                    FROM embeddings
                    ORDER BY provider, model, node_id
                    """
                )
            ]
        finally:
            connection.close()

    def test_status_is_repeatable_and_reports_every_policy_profile(self) -> None:
        _, first = self.run_cli("embedding", "status")
        _, second = self.run_cli(
            "embedding", "status", "--namespace", "personal"
        )

        self.assertEqual(first, second)
        self.assertEqual({"primary", "secondary"}, self.profile_names(first))
        self.assertEqual([], self.embedding_rows())

    def test_status_does_not_create_a_missing_local_index(self) -> None:
        profile = local_profile_payload()
        profile["database"] = "missing.sqlite"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        missing = self.profile_path.parent / "missing.sqlite"

        _, error = self.run_cli(
            "embedding", "status", expected_returncode=1
        )

        self.assertEqual("embedding-inventory-unavailable", error["code"])
        self.assertFalse(missing.exists())
        self.assertFalse(
            (missing.parent / f".{missing.name}.generations").exists()
        )

    def test_sync_uses_selected_profile_and_second_invocation_is_noop(self) -> None:
        _, first = self.run_cli(
            "embedding",
            "sync",
            "--batch-size",
            "1",
            "--max-retries",
            "0",
            "--max-nodes",
            "2",
            "--namespace",
            "personal",
        )
        first_generation = resolve_agent_index_path(self.database)
        first_rows = self.embedding_rows()
        _, second = self.run_cli("embedding", "sync")

        self.assertEqual(2, len(first_rows))
        self.assertEqual({"fixture-primary-v1"}, {row[1] for row in first_rows})
        self.assertEqual(first_rows, self.embedding_rows())
        self.assertEqual(first_generation, resolve_agent_index_path(self.database))
        self.assertEqual("installed", first["status"])
        self.assertEqual(2, first["embedded"])
        self.assertEqual(2, first["batches"])
        self.assertIn("profiles", first)
        self.assertEqual("unchanged", second["status"])
        self.assertEqual(0, second["embedded"])
        self.assertEqual(0, second["batches"])
        self.assertEqual(0, second["attempts"])
        self.assertEqual("ready", second["embedding_status"]["readiness"])

    def test_explicit_policy_and_repeated_profiles_override_default(self) -> None:
        custom_policy = self.root / "config/custom-policy.json"
        custom_policy.parent.mkdir(parents=True)
        custom_policy.write_text(
            self.policy_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

        self.run_cli(
            "--embedding-policy",
            "config/custom-policy.json",
            "embedding",
            "sync",
            "--profile",
            "primary",
            "--profile",
            "secondary",
        )

        rows = self.embedding_rows()
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {"fixture-primary-v1", "fixture-secondary-v1"},
            {row[1] for row in rows},
        )

    def test_global_embedding_profile_override_is_sync_default(self) -> None:
        self.run_cli(
            "--embedding-profile",
            "secondary",
            "embedding",
            "sync",
        )

        rows = self.embedding_rows()
        self.assertEqual(2, len(rows))
        self.assertEqual({"fixture-secondary-v1"}, {row[1] for row in rows})

    def test_embedding_failures_are_structured_and_secret_safe(self) -> None:
        _, invalid_bound = self.run_cli(
            "embedding",
            "sync",
            "--batch-size",
            "0",
            expected_returncode=1,
        )
        self.assertEqual("kgdistiller-embedding-error", invalid_bound["kind"])
        self.assertIn("code", invalid_bound)

        _, invalid_syntax = self.run_cli(
            "embedding",
            "sync",
            "--max-retries",
            "not-a-number",
            expected_returncode=1,
        )
        self.assertEqual("kgdistiller-embedding-error", invalid_syntax["kind"])
        self.assertEqual("invalid-work-budget", invalid_syntax["code"])

        _, missing_policy = self.run_cli(
            "--embedding-policy",
            "config/missing-policy.json",
            "embedding",
            "status",
            expected_returncode=1,
        )
        self.assertEqual("kgdistiller-embedding-error", missing_policy["kind"])
        self.assertIn("code", missing_policy)

    def test_status_does_not_require_or_create_the_selected_provider(self) -> None:
        profile = local_profile_payload()
        primary_config = profile["provider_profiles"]["primary"]
        primary_config["adapter"] = "openai-compatible"
        primary_config["base_url"] = "https://provider.invalid/v1"
        primary_config["credential_env"] = "MISSING_EMBEDDING_TOKEN"
        policy = policy_payload()
        policy["profiles"][0]["provider"] = "openai-compatible"
        self.profile_path.write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )
        self.policy_path.write_text(
            json.dumps(policy, ensure_ascii=False), encoding="utf-8"
        )
        self.environment.pop("MISSING_EMBEDDING_TOKEN", None)

        _, status = self.run_cli("embedding", "status")
        self.assertEqual({"primary", "secondary"}, self.profile_names(status))

        _, failure = self.run_cli(
            "embedding", "sync", expected_returncode=1
        )
        self.assertEqual("kgdistiller-embedding-error", failure["kind"])
        self.assertEqual("missing-credential", failure["code"])


if __name__ == "__main__":
    unittest.main()
