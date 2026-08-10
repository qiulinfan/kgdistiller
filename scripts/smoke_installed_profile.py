#!/usr/bin/env python3
"""Exercise machine-local profiles through an installed kgdistiller wheel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kgdistiller.agent import write_agent_index
from kgdistiller.contracts import sha256_json


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _generation_markers(database: Path) -> tuple[str, ...]:
    root = database.parent / f".{database.name}.generations"
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_file() and path.name.startswith("current-")
        )
    )


def _run_profile(
    root: Path,
    arguments: list[str],
    environment: dict[str, str],
    sentinels: tuple[str, ...],
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgdistiller",
            "--repo-root",
            str(root),
            *arguments,
            "profile",
            "status",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    transcript = completed.stdout + completed.stderr
    for sentinel in sentinels:
        _require(sentinel not in transcript, "profile command exposed a credential sentinel")
    _require(completed.returncode == 0, "installed profile command failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("installed profile command did not return JSON") from error
    _require(isinstance(payload, dict), "installed profile status was not an object")
    return payload


def _run_embedding(
    root: Path,
    command: str,
    arguments: list[str],
    environment: dict[str, str],
    sentinels: tuple[str, ...],
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgdistiller",
            "--repo-root",
            str(root),
            *arguments,
            "embedding",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    transcript = completed.stdout + completed.stderr
    for sentinel in sentinels:
        _require(sentinel not in transcript, "embedding command exposed a credential sentinel")
    _require(completed.returncode == 0, f"installed embedding {command} failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("installed embedding command did not return JSON") from error
    _require(isinstance(payload, dict), "installed embedding result was not an object")
    return payload


def _embedding_snapshot() -> dict[str, Any]:
    nodes = [
        {
            "id": node_id,
            "type": "knowledge",
            "label": label,
            "text": text,
            "properties": {
                "aliases": [],
                "curation_status": "current",
                "source_status": "active",
            },
            "provenance": {"active": True, "authority": "notes/wheel-smoke.md"},
        }
        for node_id, label, text in (
            ("wheel-alpha", "Wheel alpha", "First installed-wheel concept."),
            ("wheel-beta", "Wheel beta", "Second installed-wheel concept."),
        )
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


def main() -> int:
    primary_secret = "installed-primary-credential-sentinel"
    secondary_secret = "installed-secondary-credential-sentinel"
    sentinels = (primary_secret, secondary_secret)
    with tempfile.TemporaryDirectory(prefix="kgdistiller-wheel-profile-") as temporary:
        root = Path(temporary).resolve()
        profile_path = root / "knowledge/build/local-profile.json"
        profile_path.parent.mkdir(parents=True)
        profile = {
            "schema": "qlkg-local-profile-v1",
            "database": "state/knowledge.sqlite",
            "portable_store": "portable",
            "embedding_profile": "primary",
            "provider_profiles": {
                "primary": {
                    "adapter": "openai-compatible",
                    "model": "installed-primary-model",
                    "dimensions": 3,
                    "base_url": "https://provider.example/v1",
                    "credential_env": "KGDISTILLER_PRIMARY_SMOKE_KEY",
                },
                "secondary": {
                    "adapter": "openai-compatible",
                    "model": "installed-secondary-model",
                    "dimensions": 4,
                    "base_url": "https://provider.example/v1",
                    "credential_env": "KGDISTILLER_SECONDARY_SMOKE_KEY",
                },
                "fixture": {
                    "adapter": "deterministic-fixture",
                    "model": "installed-fixture-model",
                    "dimensions": 3,
                    "base_url": "https://fixture.invalid/v1",
                    "credential_env": "KGDISTILLER_FIXTURE_UNUSED_KEY",
                },
            },
        }
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["KGDISTILLER_PRIMARY_SMOKE_KEY"] = primary_secret
        environment["KGDISTILLER_SECONDARY_SMOKE_KEY"] = secondary_secret

        policy_path = root / "knowledge/embedding-policy.json"
        policy = {
            "schema": "qlkg-embedding-policy-v1",
            "profiles": [
                {
                    "name": "fixture",
                    "provider": "deterministic-fixture",
                    "model": "installed-fixture-model",
                    "dimensions": 3,
                    "required_node_types": ["knowledge"],
                    "minimum_coverage": 1.0,
                    "required": True,
                }
            ],
        }
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        database = profile_path.parent / "state/knowledge.sqlite"
        write_agent_index(database, _embedding_snapshot())

        first = _run_profile(root, [], environment, sentinels)
        second = _run_profile(root, [], environment, sentinels)
        _require(first == second, "fresh profile invocations did not reuse configuration")
        _require(first.get("profile_loaded") is True, "default local profile was not loaded")
        _require(first.get("embedding_profile") == "primary", "primary profile was not selected")
        _require(
            Path(str(first.get("database"))).resolve()
            == (profile_path.parent / "state/knowledge.sqlite").resolve(),
            "profile database path did not resolve from the profile directory",
        )
        _require(
            Path(str(first.get("portable_store"))).resolve()
            == (profile_path.parent / "portable").resolve(),
            "profile store path did not resolve from the profile directory",
        )
        provider = first.get("provider") or {}
        _require(provider.get("status") == "ready", "installed provider status was not ready")
        _require(
            provider.get("credential_available") is True,
            "installed provider did not see its declared credential environment variable",
        )
        _require(
            len(str(provider.get("provider_config_sha256", ""))) == 64,
            "installed provider did not expose its non-secret configuration digest",
        )

        overridden = _run_profile(
            root,
            [
                "--database",
                "override/index.sqlite",
                "--store",
                "override/store",
                "--embedding-profile",
                "secondary",
            ],
            environment,
            sentinels,
        )
        _require(
            Path(str(overridden.get("database"))).resolve()
            == (root / "override/index.sqlite").resolve(),
            "installed CLI database override was not authoritative",
        )
        _require(
            Path(str(overridden.get("portable_store"))).resolve()
            == (root / "override/store").resolve(),
            "installed CLI store override was not authoritative",
        )
        _require(
            overridden.get("embedding_profile") == "secondary",
            "installed CLI embedding-profile override was not authoritative",
        )
        _require(
            overridden.get("sources")
            == {
                "database": "cli",
                "embedding_profile": "cli",
                "portable_store": "cli",
            },
            "installed CLI did not report deterministic override precedence",
        )

        before = _run_embedding(root, "status", [], environment, sentinels)
        _require(before.get("profiles", [{}])[0].get("missing") == 2,
                 "installed embedding status did not report missing vectors")
        first_sync = _run_embedding(
            root,
            "sync",
            ["--embedding-profile", "fixture"],
            environment,
            sentinels,
        )
        installed_generation = _generation_markers(database)
        _require(installed_generation,
                 "installed embedding sync published no observable generation marker")
        second_sync = _run_embedding(
            root,
            "sync",
            ["--embedding-profile", "fixture"],
            environment,
            sentinels,
        )
        _require(first_sync.get("installed") == 2,
                 "installed embedding sync did not install both vectors")
        _require(second_sync.get("status") == "unchanged",
                 "installed embedding second sync was not unchanged")
        _require(second_sync.get("attempts") == 0 and second_sync.get("batches") == 0,
                 "installed embedding second sync called a document provider")
        _require(_generation_markers(database) == installed_generation,
                 "installed embedding no-op published a new generation")
        after = _run_embedding(root, "status", [], environment, sentinels)
        _require(after.get("profiles", [{}])[0].get("ready") == 2,
                 "installed embedding status did not report ready vectors")

    print("installed profile and embedding smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
