from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from kgdistiller.cli import main
from kgdistiller.profile import ProfileError, resolve_runtime_config
from kgdistiller.providers import (
    MAX_PROVIDER_ADAPTERS,
    DeterministicFixtureEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    ProviderAdapterRegistry,
    ProviderError,
    default_provider_registry,
    provider_config_sha256,
    provider_status,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def provider_profile(
    base_url: str,
    *,
    adapter: str = "openai-compatible",
    credential_env: str = "KGDISTILLER_TEST_API_KEY",
    dimensions: int = 3,
) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "model": "fixture-embedding-v1",
        "dimensions": dimensions,
        "base_url": base_url,
        "credential_env": credential_env,
    }


def local_profile(base_url: str) -> dict[str, Any]:
    return {
        "schema": "qlkg-local-profile-v1",
        "database": "state/knowledge.sqlite",
        "portable_store": "portable",
        "embedding_profile": "primary",
        "provider_profiles": {
            "primary": provider_profile(base_url),
            "secondary": provider_profile(
                base_url,
                credential_env="KGDISTILLER_SECONDARY_API_KEY",
                dimensions=4,
            ),
        },
    }


class _EmbeddingHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        if self.path == "/redirect/embeddings":
            self.send_response(307)
            self.send_header("Location", "/v1/embeddings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        dimensions = int(payload["dimensions"])
        records = []
        for index, text in enumerate(payload["input"]):
            vector = [float(index + 1), float(len(text) + 1)]
            vector.extend(float(offset + 1) for offset in range(dimensions - 2))
            records.append({"index": index, "embedding": vector[:dimensions]})
        response = json.dumps({"data": records}, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        pass


class LocalProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-profile-")
        self.root = Path(self.temporary.name)
        self.profile_path = self.root / "config/local-profile.json"
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text(
            json.dumps(local_profile("https://provider.example/v1")),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profile_paths_resolve_from_profile_directory(self) -> None:
        resolved = resolve_runtime_config(
            self.root,
            local_profile=self.profile_path,
        )

        self.assertTrue(resolved.profile_loaded)
        self.assertEqual(
            (self.profile_path.parent / "state/knowledge.sqlite").resolve(),
            resolved.database,
        )
        self.assertEqual(
            (self.profile_path.parent / "portable").resolve(),
            resolved.portable_store,
        )
        self.assertEqual("primary", resolved.embedding_profile)
        self.assertEqual(
            {
                "database": "profile",
                "portable_store": "profile",
                "embedding_profile": "profile",
            },
            resolved.sources,
        )

    def test_cli_overrides_profile_and_missing_profile_uses_defaults(self) -> None:
        resolved = resolve_runtime_config(
            self.root,
            local_profile=self.profile_path,
            database=Path("override/index.sqlite"),
            portable_store=Path("override/store"),
            embedding_profile="secondary",
        )

        self.assertEqual((self.root / "override/index.sqlite").resolve(), resolved.database)
        self.assertEqual((self.root / "override/store").resolve(), resolved.portable_store)
        self.assertEqual("secondary", resolved.embedding_profile)
        self.assertEqual(
            {
                "database": "cli",
                "portable_store": "cli",
                "embedding_profile": "cli",
            },
            resolved.sources,
        )

        defaults = resolve_runtime_config(self.root)
        self.assertFalse(defaults.profile_loaded)
        self.assertEqual(
            (self.root / "knowledge/build/knowledge.sqlite").resolve(),
            defaults.database,
        )
        self.assertEqual(self.root.resolve(), defaults.portable_store)

    def test_explicit_missing_invalid_and_unknown_profiles_have_stable_errors(self) -> None:
        with self.assertRaises(ProfileError) as missing:
            resolve_runtime_config(
                self.root,
                local_profile=self.root / "missing.json",
            )
        self.assertEqual("profile-not-found", missing.exception.code)

        invalid_path = self.root / "invalid.json"
        secret = "must-not-appear-in-errors"
        invalid = local_profile("https://provider.example/v1")
        invalid["provider_profiles"]["primary"]["api_key"] = secret
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(ProfileError) as invalid_error:
            resolve_runtime_config(self.root, local_profile=invalid_path)
        self.assertEqual("invalid-profile", invalid_error.exception.code)
        self.assertNotIn(secret, str(invalid_error.exception))

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        invalid_cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.root),
                "--local-profile",
                str(invalid_path),
                "profile",
                "status",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(1, invalid_cli.returncode)
        self.assertEqual("invalid-profile", json.loads(invalid_cli.stderr)["code"])
        self.assertNotIn(secret, invalid_cli.stdout + invalid_cli.stderr)

        with self.assertRaises(ProfileError) as unknown:
            resolve_runtime_config(
                self.root,
                local_profile=self.profile_path,
                embedding_profile="unconfigured",
            )
        self.assertEqual("unknown-embedding-profile", unknown.exception.code)

    def test_profile_status_reuses_configuration_and_redacts_secret(self) -> None:
        default_profile = self.root / "knowledge/build/local-profile.json"
        default_profile.parent.mkdir(parents=True)
        default_profile.write_text(
            json.dumps(local_profile("https://provider.example/v1")),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        secret = "profile-status-secret-value"
        environment["KGDISTILLER_TEST_API_KEY"] = secret
        command = [
            sys.executable,
            "-m",
            "kgdistiller",
            "--repo-root",
            str(self.root),
            "profile",
            "status",
        ]

        first = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        second = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        status = json.loads(first.stdout)
        self.assertEqual(
            (default_profile.parent / "state/knowledge.sqlite").resolve(),
            Path(status["database"]),
        )
        self.assertEqual(
            (default_profile.parent / "portable").resolve(),
            Path(status["portable_store"]),
        )
        self.assertEqual("primary", status["embedding_profile"])
        self.assertEqual("ready", status["provider"]["status"])
        self.assertTrue(status["provider"]["adapter_registered"])
        self.assertTrue(status["provider"]["credential_available"])
        self.assertNotIn("base_url", status["provider"])
        self.assertNotIn(secret, first.stdout + first.stderr + second.stdout + second.stderr)

        environment["KGDISTILLER_SECONDARY_API_KEY"] = "secondary-secret"
        overridden = subprocess.run(
            command[:-2]
            + [
                "--database",
                "override/index.sqlite",
                "--store",
                "override/store",
                "--embedding-profile",
                "secondary",
                "profile",
                "status",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, overridden.returncode, overridden.stderr)
        override_status = json.loads(overridden.stdout)
        self.assertEqual(
            (self.root / "override/index.sqlite").resolve(),
            Path(override_status["database"]),
        )
        self.assertEqual(
            (self.root / "override/store").resolve(),
            Path(override_status["portable_store"]),
        )
        self.assertEqual("secondary", override_status["embedding_profile"])
        self.assertEqual(
            {
                "database": "cli",
                "embedding_profile": "cli",
                "portable_store": "cli",
            },
            override_status["sources"],
        )
        self.assertNotIn("secondary-secret", overridden.stdout + overridden.stderr)

    def test_store_verify_uses_resolved_profile_store(self) -> None:
        expected_store = (self.profile_path.parent / "portable").resolve()
        arguments = [
            "kgdistiller",
            "--repo-root",
            str(self.root),
            "--local-profile",
            str(self.profile_path),
            "store",
            "verify",
        ]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", arguments), mock.patch(
            "kgdistiller.store.verify_store",
            return_value={"verified": True},
        ) as verify, contextlib.redirect_stdout(output):
            self.assertEqual(0, main())

        verify.assert_called_once_with(expected_store)
        self.assertEqual({"verified": True}, json.loads(output.getvalue()))


class ProviderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        _EmbeddingHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}/v1"
        self.secret = "fixture-http-secret"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_real_http_adapter_batches_documents_and_query(self) -> None:
        config = provider_profile(self.base_url)
        registry = default_provider_registry()
        provider = registry.create(
            "primary",
            config,
            environ={"KGDISTILLER_TEST_API_KEY": self.secret},
        )

        documents = provider.embed_documents(["first document", "second document"])
        query = provider.embed_query("search query")

        self.assertEqual(2, len(documents))
        self.assertEqual(3, len(documents[0]))
        self.assertEqual(3, len(query))
        self.assertEqual(2, len(_EmbeddingHandler.requests))
        self.assertEqual("/v1/embeddings", _EmbeddingHandler.requests[0]["path"])
        self.assertEqual(
            ["first document", "second document"],
            _EmbeddingHandler.requests[0]["payload"]["input"],
        )
        self.assertEqual(
            "fixture-embedding-v1",
            _EmbeddingHandler.requests[0]["payload"]["model"],
        )
        self.assertEqual(3, _EmbeddingHandler.requests[0]["payload"]["dimensions"])
        self.assertEqual(["search query"], _EmbeddingHandler.requests[1]["payload"]["input"])
        self.assertEqual(
            "Bearer fixture-http-secret",
            _EmbeddingHandler.requests[0]["authorization"],
        )
        self.assertEqual(provider_config_sha256(config), provider.provider_config_sha256)

        status = provider_status(
            "primary",
            config,
            registry,
            environ={"KGDISTILLER_TEST_API_KEY": self.secret},
        )
        self.assertNotIn(self.secret, json.dumps(status))
        renamed_env = dict(config, credential_env="DIFFERENT_ENV_NAME")
        self.assertEqual(
            provider_config_sha256(config),
            provider_config_sha256(renamed_env),
        )

    def test_deterministic_fixture_adapter_is_repeatable_without_a_credential(self) -> None:
        config = provider_profile(
            "https://fixture.invalid/v1",
            adapter="deterministic-fixture",
            dimensions=40,
        )
        registry = default_provider_registry()
        provider = registry.create("fixture", config, environ={})

        self.assertIsInstance(provider, DeterministicFixtureEmbeddingProvider)
        first = provider.embed_documents(["document", "second"])
        second = provider.embed_documents(["document", "second"])
        self.assertEqual(first, second)
        self.assertEqual(40, len(first[0]))
        self.assertNotEqual(first[0], provider.embed_query("query"))
        self.assertEqual(
            "ready",
            provider_status("fixture", config, registry, environ={})["status"],
        )

    def test_missing_adapter_and_credential_have_stable_codes(self) -> None:
        registry = default_provider_registry()
        missing_adapter_config = provider_profile(
            self.base_url, adapter="not-installed"
        )
        with self.assertRaises(ProviderError) as missing_adapter:
            registry.create(
                "primary",
                missing_adapter_config,
                environ={"KGDISTILLER_TEST_API_KEY": self.secret},
            )
        self.assertEqual("missing-adapter", missing_adapter.exception.code)
        self.assertEqual(
            "missing-adapter",
            provider_status(
                "primary",
                missing_adapter_config,
                registry,
                environ={"KGDISTILLER_TEST_API_KEY": self.secret},
            )["status"],
        )

        with self.assertRaises(ProviderError) as missing_credential:
            registry.create("primary", provider_profile(self.base_url), environ={})
        self.assertEqual("missing-credential", missing_credential.exception.code)
        self.assertEqual(
            "missing-credential",
            provider_status(
                "primary",
                provider_profile(self.base_url),
                registry,
                environ={},
            )["status"],
        )

    def test_http_redirect_is_not_followed_with_the_credential(self) -> None:
        redirect_base_url = (
            f"http://{self.server.server_address[0]}:"
            f"{self.server.server_address[1]}/redirect"
        )
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(redirect_base_url),
            self.secret,
        )

        with self.assertRaises(ProviderError) as redirected:
            provider.embed_query("query")

        self.assertEqual("provider-unavailable", redirected.exception.code)
        self.assertEqual(1, len(_EmbeddingHandler.requests))
        self.assertEqual("/redirect/embeddings", _EmbeddingHandler.requests[0]["path"])

    def test_dimension_timeout_and_invalid_response_have_stable_codes(self) -> None:
        config = provider_profile(self.base_url)
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            config,
            self.secret,
        )

        with mock.patch.object(
            provider,
            "_request",
            return_value={"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
        ), self.assertRaises(ProviderError) as dimension:
            provider.embed_query("query")
        self.assertEqual("dimension-mismatch", dimension.exception.code)

        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            side_effect=TimeoutError,
        ), self.assertRaises(ProviderError) as timeout:
            provider.embed_query("query")
        self.assertEqual("provider-timeout", timeout.exception.code)
        self.assertNotIn(self.secret, str(timeout.exception))

        with mock.patch.object(
            provider,
            "_request",
            return_value={"data": "not-a-list"},
        ), self.assertRaises(ProviderError) as invalid:
            provider.embed_query("query")
        self.assertEqual("invalid-response", invalid.exception.code)

    def test_adapter_registry_is_bounded(self) -> None:
        registry = ProviderAdapterRegistry()
        for index in range(MAX_PROVIDER_ADAPTERS):
            registry.register(
                f"fixture-{index}",
                lambda profile_name, config, credential: object(),
                requires_credential=False,
            )
        with self.assertRaises(ProviderError) as full:
            registry.register(
                "one-too-many",
                lambda profile_name, config, credential: object(),
                requires_credential=False,
            )
        self.assertEqual("adapter-limit", full.exception.code)

    def test_adapter_initialization_error_redacts_credential_and_cause(self) -> None:
        registry = ProviderAdapterRegistry()
        secret = "factory-must-not-leak-this"

        def broken_factory(
            profile_name: str, config: dict[str, Any], credential: str
        ) -> Any:
            raise RuntimeError(credential)

        registry.register("broken", broken_factory)
        with self.assertRaises(ProviderError) as broken:
            registry.create(
                "primary",
                provider_profile(self.base_url, adapter="broken"),
                environ={"KGDISTILLER_TEST_API_KEY": secret},
            )

        self.assertEqual("adapter-initialization", broken.exception.code)
        self.assertNotIn(secret, str(broken.exception))
        self.assertIsNone(broken.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
