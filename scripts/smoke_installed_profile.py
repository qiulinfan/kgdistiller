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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


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

    print("installed profile smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
