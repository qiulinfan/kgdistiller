from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

import kgdistiller.providers as providers_module
from kgdistiller.agent import AgentIndexError, _validate_vector
from kgdistiller.cli import main
from kgdistiller.profile import (
    MAX_LOCAL_PROFILE_BYTES,
    ProfileError,
    _load_profile,
    resolve_runtime_config,
)
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


def exception_debug_text(error: BaseException) -> str:
    """Expose retained exception state so secret-redaction tests inspect raw chains."""

    pending = [error]
    seen: set[int] = set()
    details: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        details.extend(
            (
                type(current).__name__,
                str(current),
                repr(vars(current)),
            )
        )
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(details)


class _ProviderResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        headers: Any | None = None,
        read_error: BaseException | None = None,
        drip_delay: float = 0.0,
    ) -> None:
        self.headers = headers or {}
        self.body = body
        self.read_error = read_error
        self.drip_delay = drip_delay
        self.offset = 0
        self.read_calls = 0

    def __enter__(self) -> "_ProviderResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        if self.offset >= len(self.body):
            return b""
        if self.drip_delay:
            time.sleep(self.drip_delay)
            size = 1
        elif size < 0:
            size = len(self.body) - self.offset
        end = min(len(self.body), self.offset + size)
        chunk = self.body[self.offset : end]
        self.offset = end
        return chunk

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)


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

    def test_invalid_profile_does_not_retain_raw_secret_in_exception_chain(self) -> None:
        secret = "raw-profile-secret-sentinel"
        malformed = self.root / "malformed-profile.json"
        malformed.write_text(
            '{"schema":"qlkg-local-profile-v1","credential":"'
            + secret,
            encoding="utf-8",
        )

        with self.assertRaises(ProfileError) as rejected:
            resolve_runtime_config(self.root, local_profile=malformed)

        self.assertEqual("invalid-profile", rejected.exception.code)
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)
        self.assertNotIn(secret, exception_debug_text(rejected.exception))

    def test_profile_reader_requests_exactly_the_bounded_maximum_plus_one(self) -> None:
        oversized = self.root / "oversized-profile.json"
        with oversized.open("wb") as handle:
            handle.truncate(MAX_LOCAL_PROFILE_BYTES + 1)
        real_fdopen = os.fdopen
        read_sizes: list[int] = []

        class RecordingReader:
            def __init__(self, descriptor: int, *args: object, **kwargs: object) -> None:
                self.handle = real_fdopen(descriptor, *args, **kwargs)

            def __enter__(self) -> "RecordingReader":
                return self

            def __exit__(self, *args: object) -> None:
                self.handle.close()

            def read(self, size: int = -1) -> bytes:
                read_sizes.append(size)
                return self.handle.read(size)

        with mock.patch("kgdistiller.profile.os.fdopen", RecordingReader):
            with self.assertRaises(ProfileError) as rejected:
                _load_profile(oversized, required=True)

        self.assertEqual("profile-too-large", rejected.exception.code)
        self.assertEqual([MAX_LOCAL_PROFILE_BYTES + 1], read_sizes)
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)

    def test_profile_permission_probe_does_not_retain_raw_os_errors(self) -> None:
        with mock.patch(
            "kgdistiller.profile.os.open",
            side_effect=PermissionError("permission-secret-sentinel"),
        ), mock.patch.object(
            Path,
            "is_dir",
            side_effect=OSError("stat-secret-sentinel"),
        ), self.assertRaises(ProfileError) as rejected:
            _load_profile(self.profile_path, required=True)

        self.assertEqual("profile-unreadable", rejected.exception.code)
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)
        self.assertNotIn("secret-sentinel", exception_debug_text(rejected.exception))

    def test_profile_parser_wraps_deep_and_huge_json_failures(self) -> None:
        payloads = {
            "deep": "[" * 2000 + "]" * 2000,
            "huge-integer": "[" + "9" * 5000 + "]",
        }
        for name, payload in payloads.items():
            with self.subTest(name=name):
                path = self.root / f"{name}.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ProfileError) as rejected:
                    _load_profile(path, required=True)
                self.assertEqual("invalid-profile", rejected.exception.code)
                self.assertIsNone(rejected.exception.__cause__)
                self.assertIsNone(rejected.exception.__context__)

        surrogate = local_profile("https://provider.example/v1")
        surrogate["provider_profiles"]["primary"]["model"] = "\ud800"
        surrogate_path = self.root / "surrogate.json"
        surrogate_path.write_text(json.dumps(surrogate), encoding="utf-8")
        with self.assertRaises(ProfileError) as rejected:
            _load_profile(surrogate_path, required=True)
        self.assertEqual("invalid-profile", rejected.exception.code)
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)

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

    def test_cli_serializes_provider_errors_as_structured_payloads(self) -> None:
        arguments = [
            "kgdistiller",
            "--repo-root",
            str(self.root),
            "--local-profile",
            str(self.profile_path),
            "profile",
            "status",
        ]
        output = io.StringIO()
        errors = io.StringIO()
        failure = ProviderError(
            "invalid-provider-config",
            "provider configuration is invalid",
        )

        with mock.patch.object(sys, "argv", arguments), mock.patch(
            "kgdistiller.providers.provider_status",
            side_effect=failure,
        ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(1, main())

        self.assertEqual("", output.getvalue())
        self.assertEqual(
            {
                "kind": "kgdistiller-provider-error",
                "code": "invalid-provider-config",
                "message": "provider configuration is invalid",
            },
            json.loads(errors.getvalue()),
        )

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

    def assert_stable_provider_error(
        self,
        expected_code: str,
        operation: Any,
        *,
        forbidden: tuple[str, ...] = (),
    ) -> ProviderError:
        with self.assertRaises(ProviderError) as rejected:
            operation()
        error = rejected.exception
        self.assertEqual(expected_code, error.code)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        diagnostic = exception_debug_text(error)
        for value in forbidden:
            self.assertNotIn(value, diagnostic)
        return error

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

    def test_provider_urls_require_https_except_for_numeric_loopback_http(self) -> None:
        canonical = provider_profile("https://example.com/v1")
        equivalent = provider_profile("HTTPS://EXAMPLE.COM:443/v1/")
        self.assertEqual(
            provider_config_sha256(canonical),
            provider_config_sha256(equivalent),
        )

        for base_url in (
            "http://127.0.0.1:43123/v1",
            "http://[::1]:43123/v1",
        ):
            with self.subTest(allowed=base_url):
                self.assertEqual(64, len(provider_config_sha256(provider_profile(base_url))))

        for base_url in (
            "http://192.0.2.1/v1",
            "http://localhost:43123/v1",
            "https://example.com:notaport/v1",
            "https://example.com:65536/v1",
            "https://[::1",
            "https://example.com/\ud800",
        ):
            with self.subTest(rejected=base_url):
                self.assert_stable_provider_error(
                    "invalid-provider-config",
                    lambda base_url=base_url: provider_config_sha256(
                        provider_profile(base_url)
                    ),
                )

    def test_credentials_are_bounded_header_safe_and_secret_safe(self) -> None:
        maximum = providers_module.MAX_PROVIDER_CREDENTIAL_BYTES
        secrets = (
            "crlf-secret-sentinel\r\nInjected: yes",
            "non-ascii-secret-sentinel-é",
            "overlong-secret-sentinel-" + "x" * maximum,
        )
        registry = default_provider_registry()
        for secret in secrets:
            with self.subTest(secret_kind=secret.split("-secret-sentinel", 1)[0]):
                self.assert_stable_provider_error(
                    "invalid-provider-config",
                    lambda secret=secret: registry.create(
                        "primary",
                        provider_profile(self.base_url),
                        environ={"KGDISTILLER_TEST_API_KEY": secret},
                    ),
                    forbidden=("secret-sentinel",),
                )

    def test_malformed_and_incomplete_http_responses_are_stable_and_redacted(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(self.base_url),
            self.secret,
        )
        response_secret = "response-body-secret-sentinel"
        malformed = _ProviderResponse(
            ('{"data":["' + response_secret).encode("utf-8")
        )
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=malformed,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
                forbidden=(response_secret,),
            )

        malformed_http = _ProviderResponse(
            read_error=http.client.BadStatusLine(
                "malformed-http-secret-sentinel"
            )
        )
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=malformed_http,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
                forbidden=("malformed-http-secret-sentinel",),
            )

        incomplete = _ProviderResponse(
            read_error=http.client.IncompleteRead(
                b"incomplete-response-secret-sentinel",
                100,
            )
        )
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=incomplete,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
                forbidden=("incomplete-response-secret-sentinel",),
            )

        duplicate_lengths = Message()
        duplicate_lengths.add_header("Content-Length", "2")
        duplicate_lengths.add_header("Content-Length", "2")
        framing_headers = (
            {"Content-Length": "+2"},
            {"Content-Length": "2", "Transfer-Encoding": "chunked"},
            duplicate_lengths,
        )
        for headers in framing_headers:
            with self.subTest(headers=str(headers)):
                ambiguous = _ProviderResponse(b"{}", headers=headers)
                with mock.patch(
                    "kgdistiller.providers._open_provider_request",
                    return_value=ambiguous,
                ):
                    self.assert_stable_provider_error(
                        "invalid-response",
                        lambda: provider.embed_query("query"),
                    )

    def test_deep_oversized_and_overflowing_json_are_stable_invalid_responses(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(self.base_url),
            self.secret,
        )
        deeply_nested = _ProviderResponse(b"[" * 2000 + b"]" * 2000)
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=deeply_nested,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )

        huge_integer = _ProviderResponse(
            b'{"data":[{"index":0,"embedding":['
            + b"9" * 5000
            + b",1,2]}]}"
        )
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=huge_integer,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )

        nonfinite_padding = _ProviderResponse(
            b'{"data":[{"index":0,"embedding":[1,2,3]}],"padding":NaN}'
        )
        overflowing_padding = _ProviderResponse(
            b'{"data":[{"index":0,"embedding":[1,2,3]}],"padding":1e999}'
        )
        deeply_nested_padding = _ProviderResponse(
            b'{"data":[{"index":0,"embedding":[1,2,3]}],"padding":'
            + b"[" * 64
            + b"0"
            + b"]" * 64
            + b"}"
        )
        for malformed_json in (
            nonfinite_padding,
            overflowing_padding,
            deeply_nested_padding,
        ):
            with mock.patch(
                "kgdistiller.providers._open_provider_request",
                return_value=malformed_json,
            ):
                self.assert_stable_provider_error(
                    "invalid-response",
                    lambda: provider.embed_query("query"),
                )

        oversized = _ProviderResponse(b'{' + b'"padding":"' + b"x" * 256 + b'"}')
        with mock.patch(
            "kgdistiller.providers.MAX_PROVIDER_RESPONSE_BYTES",
            128,
        ), mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=oversized,
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )

        with mock.patch.object(
            provider,
            "_request",
            return_value={
                "data": [{"index": 0, "embedding": [10**400, 1.0, 2.0]}]
            },
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )

        with mock.patch.object(
            provider,
            "_request",
            return_value={
                "data": [{"index": 0, "embedding": [1e308, 1.0, 2.0]}]
            },
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )

        with mock.patch.object(
            provider,
            "_request",
            return_value={
                "data": [{"index": 0, "embedding": [1e-50, 1e-50, 1e-50]}]
            },
        ):
            self.assert_stable_provider_error(
                "invalid-response",
                lambda: provider.embed_query("query"),
            )
        with self.assertRaises(AgentIndexError):
            _validate_vector([1e-50, 1e-50, 1e-50], 3)
        with self.assertRaises(AgentIndexError):
            _validate_vector([1e308, 1.0, 2.0], 3)

    def test_slow_drip_response_obeys_a_total_deadline(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(self.base_url),
            self.secret,
            timeout_seconds=0.03,
        )
        body = json.dumps(
            {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]},
            separators=(",", ":"),
        ).encode("utf-8")
        slow = _ProviderResponse(
            body,
            headers={"Content-Length": str(len(body))},
            drip_delay=0.01,
        )
        started = time.monotonic()
        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            return_value=slow,
        ):
            self.assert_stable_provider_error(
                "provider-timeout",
                lambda: provider.embed_query("query"),
            )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.2)

    def test_slow_status_header_obeys_a_total_deadline(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()

        def serve() -> None:
            try:
                connection, _ = listener.accept()
                with connection:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += connection.recv(4096)
                    response = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
                    for value in response:
                        try:
                            connection.sendall(bytes((value,)))
                        except OSError:
                            break
                        time.sleep(0.01)
            finally:
                listener.close()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(f"http://{host}:{port}/v1"),
            self.secret,
            timeout_seconds=0.03,
        )
        started = time.monotonic()
        self.assert_stable_provider_error(
            "provider-timeout",
            lambda: provider.embed_query("query"),
        )
        elapsed = time.monotonic() - started
        server.join(timeout=1)
        self.assertLess(elapsed, 0.2)

    def test_adapter_provider_error_cannot_retain_or_report_credential(self) -> None:
        registry = ProviderAdapterRegistry()
        secret = "adapter-provider-error-secret-sentinel"

        def leaking_factory(
            profile_name: str, config: dict[str, Any], credential: str
        ) -> Any:
            raise ProviderError("provider-unavailable", f"adapter leaked {credential}")

        registry.register("leaking", leaking_factory)
        self.assert_stable_provider_error(
            "adapter-initialization",
            lambda: registry.create(
                "primary",
                provider_profile(self.base_url, adapter="leaking"),
                environ={"KGDISTILLER_TEST_API_KEY": secret},
            ),
            forbidden=(secret,),
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

    def test_late_resolver_failure_is_classified_as_provider_timeout(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            "primary",
            provider_profile(self.base_url),
            self.secret,
            timeout_seconds=0.01,
        )

        def late_resolver_failure(*args: object, **kwargs: object) -> object:
            time.sleep(0.03)
            raise providers_module.URLError(socket.gaierror(socket.EAI_AGAIN))

        with mock.patch(
            "kgdistiller.providers._open_provider_request",
            side_effect=late_resolver_failure,
        ):
            self.assert_stable_provider_error(
                "provider-timeout",
                lambda: provider.embed_query("query"),
            )

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
