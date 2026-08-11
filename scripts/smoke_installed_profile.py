#!/usr/bin/env python3
"""Exercise machine-local profiles through an installed kgdistiller wheel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from kgdistiller.agent import write_agent_index
from kgdistiller.contracts import sha256_json, validate_contract


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


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


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


def _run_agent(
    root: Path,
    command: str,
    arguments: list[str],
    environment: dict[str, str],
    sentinels: tuple[str, ...],
    *,
    expected_status: int = 0,
    database: Optional[Path] = None,
) -> dict[str, Any]:
    database_arguments = [] if database is None else ["--database", str(database)]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgdistiller",
            "--repo-root",
            str(root),
            "--embedding-profile",
            "fixture",
            *database_arguments,
            "agent",
            command,
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    transcript = completed.stdout + completed.stderr
    for sentinel in sentinels:
        _require(
            sentinel not in transcript,
            "retrieval command exposed a credential sentinel",
        )
    _require(
        completed.returncode == expected_status,
        f"installed agent {command} returned {completed.returncode}, expected {expected_status}",
    )
    encoded = completed.stdout if expected_status == 0 else completed.stderr
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise RuntimeError("installed retrieval command did not return JSON") from error
    _require(isinstance(payload, dict), "installed retrieval result was not an object")
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


def _retrieval_plan(*, semantic_queries: list[str]) -> dict[str, Any]:
    return {
        "schema": "qlkg-retrieval-plan-v1",
        "question": "How can an installed wheel retrieve wheel alpha?",
        "namespace": "personal",
        "identity_queries": ["wheel-alpha"],
        "lexical_queries": ["installed wheel concept"],
        "semantic_queries": semantic_queries,
        "graph": {
            "seed_ids": ["wheel-alpha"],
            "edge_types": [],
            "direction": "out",
            "max_depth": 0,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


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

        graph_dir = root / "knowledge/graph"
        graph_dir.mkdir(parents=True)
        (graph_dir / "manifest.json").write_text(
            json.dumps(
                {"schema": "qlkg-v2", "graph_sha256": "1" * 64},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        for filename in ("nodes.jsonl", "edges.jsonl", "references.jsonl"):
            (graph_dir / filename).write_text("", encoding="utf-8")

        plan_path = root / "retrieval-plan.json"
        plan_path.write_text(
            json.dumps(
                _retrieval_plan(
                    semantic_queries=["First installed-wheel concept."],
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        no_semantic_plan_path = root / "retrieval-plan-no-semantic.json"
        no_semantic_plan_path.write_text(
            json.dumps(
                _retrieval_plan(semantic_queries=[]),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        state_before_retrieval = _tree_snapshot(database.parent)
        generation_before_retrieval = _generation_markers(database)

        planned_search = _run_agent(
            root,
            "search",
            ["--plan", str(plan_path)],
            environment,
            sentinels,
        )
        repeated_search = _run_agent(
            root,
            "search",
            ["--plan", str(plan_path)],
            environment,
            sentinels,
        )
        no_semantic_search = _run_agent(
            root,
            "search",
            ["--plan", str(no_semantic_plan_path)],
            environment,
            sentinels,
        )
        planned_context = _run_agent(
            root,
            "context",
            ["--plan", str(plan_path)],
            environment,
            sentinels,
        )
        legacy_search = _run_agent(
            root,
            "search",
            ["wheel-alpha"],
            environment,
            sentinels,
        )
        legacy_context = _run_agent(
            root,
            "context",
            ["wheel-alpha"],
            environment,
            sentinels,
        )

        validated_execution = validate_contract(planned_search)
        _require(
            validated_execution.get("schema") == "qlkg-search-execution-v1",
            "installed wheel did not load the planned execution contract",
        )
        _require(
            validate_contract(validated_execution["result"]).get("schema")
            == "qlkg-search-result-v2",
            "installed wheel did not load the nested search-result contract",
        )
        _require(
            planned_search.get("plan_mode") == "planned",
            "installed planned search did not report planned mode",
        )
        semantic_lane = (
            planned_search.get("result", {}).get("lanes", {}).get("semantic", {})
        )
        _require(
            semantic_lane.get("status") == "enabled"
            and semantic_lane.get("queries") == 1
            and int(semantic_lane.get("results", 0)) > 0,
            "installed planned search did not query the ready semantic space",
        )
        _require(
            repeated_search == planned_search,
            "repeated installed planned search was not deterministic",
        )
        _require(
            no_semantic_search.get("result", {})
            .get("lanes", {})
            .get("semantic", {})
            .get("status")
            == "disabled",
            "installed plan without semantic queries invoked the semantic lane",
        )
        _require(
            planned_context.get("schema") == "qlkg-context-bundle-v1",
            "installed planned context did not return an Agent context",
        )
        _require(
            planned_context.get("query")
            == "How can an installed wheel retrieve wheel alpha?",
            "installed planned context did not preserve the plan question",
        )
        _require(
            legacy_search.get("plan_mode") == "legacy",
            "installed legacy search did not report legacy mode",
        )
        _require(
            legacy_context.get("schema") == "qlkg-context-bundle-v1"
            and legacy_context.get("query") == "wheel-alpha",
            "installed legacy context did not preserve the query",
        )
        _require(
            _tree_snapshot(database.parent) == state_before_retrieval,
            "installed search/context mutated the materialized index",
        )
        _require(
            _generation_markers(database) == generation_before_retrieval,
            "installed search/context published a new generation",
        )

        missing_database = root / "missing/knowledge.sqlite"
        missing_before = _tree_snapshot(root / "missing")
        missing = _run_agent(
            root,
            "search",
            ["wheel-alpha"],
            environment,
            sentinels,
            expected_status=1,
            database=missing_database,
        )
        _require(
            missing.get("code") == "index-unavailable",
            "installed missing-index retrieval did not return index-unavailable",
        )
        _require(
            not missing_database.exists()
            and _tree_snapshot(root / "missing") == missing_before,
            "installed missing-index retrieval created materialized state",
        )

    print("installed profile, embedding, and retrieval smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
