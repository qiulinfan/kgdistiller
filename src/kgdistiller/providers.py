"""Bounded optional embedding-provider adapters for machine-local profiles."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import sha256_json

if TYPE_CHECKING:
    from .agent import EmbeddingProvider


MAX_PROVIDER_ADAPTERS = 32
MAX_EMBEDDING_BATCH = 128
MAX_EMBEDDING_TEXT_BYTES = 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30.0
ADAPTER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProviderError(ValueError):
    """Stable provider failure whose text never contains credentials or response bodies."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def payload(self) -> dict[str, str]:
        return {
            "kind": "kgdistiller-provider-error",
            "code": self.code,
            "message": self.message,
        }


def _validated_provider_config(config: Mapping[str, Any]) -> dict[str, Any]:
    adapter = config.get("adapter")
    model = config.get("model")
    dimensions = config.get("dimensions")
    base_url = config.get("base_url")
    credential_env = config.get("credential_env")
    if not isinstance(adapter, str) or not ADAPTER_NAME_RE.fullmatch(adapter):
        raise ProviderError("invalid-provider-config", "adapter name is invalid")
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ProviderError("invalid-provider-config", "model is invalid")
    if (
        isinstance(dimensions, bool)
        or not isinstance(dimensions, int)
        or dimensions < 1
        or dimensions > 1_048_576
    ):
        raise ProviderError("invalid-provider-config", "dimensions are invalid")
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ProviderError("invalid-provider-config", "base URL is invalid")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderError("invalid-provider-config", "base URL is invalid")
    if (
        not isinstance(credential_env, str)
        or len(credential_env) > 128
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential_env)
    ):
        raise ProviderError("invalid-provider-config", "credential environment name is invalid")
    return {
        "adapter": adapter,
        "model": model,
        "dimensions": dimensions,
        "base_url": base_url.rstrip("/"),
        "credential_env": credential_env,
    }


def provider_configuration(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return only non-secret fields that determine the requested vector space."""
    validated = _validated_provider_config(config)
    configuration = {
        "adapter": validated["adapter"],
        "model": validated["model"],
        "dimensions": validated["dimensions"],
    }
    if validated["adapter"] != "deterministic-fixture":
        configuration["base_url"] = validated["base_url"]
    return configuration


def provider_config_sha256(config: Mapping[str, Any]) -> str:
    return sha256_json(provider_configuration(config))


def _validated_embedding_texts(texts: list[str]) -> list[str]:
    if not isinstance(texts, list) or not texts or len(texts) > MAX_EMBEDDING_BATCH:
        raise ProviderError(
            "invalid-provider-request",
            f"embedding batch must contain 1 to {MAX_EMBEDDING_BATCH} texts",
        )
    normalized: list[str] = []
    total_bytes = 0
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ProviderError(
                "invalid-provider-request", "embedding text must be a non-empty string"
            )
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_EMBEDDING_TEXT_BYTES:
            raise ProviderError(
                "invalid-provider-request", "embedding batch exceeds the text budget"
            )
        normalized.append(text)
    return normalized


class _NoProviderRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _open_provider_request(request: Request, timeout: float) -> Any:
    return build_opener(_NoProviderRedirects()).open(request, timeout=timeout)


class DeterministicFixtureEmbeddingProvider:
    """Credential-free deterministic adapter for acceptance fixtures only."""

    def __init__(self, profile_name: str, config: Mapping[str, Any]) -> None:
        validated = _validated_provider_config(config)
        self.profile_name = profile_name
        self.name = str(validated["adapter"])
        self.model = str(validated["model"])
        self.dimensions = int(validated["dimensions"])
        self.provider_config_sha256 = provider_config_sha256(validated)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in _validated_embedding_texts(texts):
            vector: list[float] = []
            counter = 0
            while len(vector) < self.dimensions:
                digest = hashlib.sha256(
                    self.model.encode("utf-8")
                    + b"\0"
                    + text.encode("utf-8")
                    + counter.to_bytes(8, "big")
                ).digest()
                vector.extend((byte + 1) / 256.0 for byte in digest)
                counter += 1
            vectors.append(vector[: self.dimensions])
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OpenAICompatibleEmbeddingProvider:
    """Small stdlib-only adapter for an OpenAI-compatible embeddings endpoint."""

    def __init__(
        self,
        profile_name: str,
        config: Mapping[str, Any],
        credential: str,
        *,
        timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    ) -> None:
        validated = _validated_provider_config(config)
        if not credential:
            raise ProviderError("missing-credential", "provider credential is unavailable")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ProviderError("invalid-provider-config", "provider timeout is invalid")
        self.profile_name = profile_name
        self.name = str(validated["adapter"])
        self.model = str(validated["model"])
        self.dimensions = int(validated["dimensions"])
        self.provider_config_sha256 = provider_config_sha256(validated)
        self._base_url = str(validated["base_url"])
        self._credential = credential
        self._timeout_seconds = float(timeout_seconds)

    def _validated_texts(self, texts: list[str]) -> list[str]:
        return _validated_embedding_texts(texts)

    def _request(self, texts: list[str]) -> Any:
        body = json.dumps(
            {
                "input": texts,
                "model": self.model,
                "dimensions": self.dimensions,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self._base_url}/embeddings",
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._credential}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _open_provider_request(request, self._timeout_seconds) as response:
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                        if parsed_length < 0 or parsed_length > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ProviderError(
                                "invalid-response", "provider response exceeds the byte budget"
                            )
                    except ValueError as error:
                        raise ProviderError(
                            "invalid-response", "provider response has an invalid length"
                        ) from error
                response_body = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except ProviderError:
            raise
        except (TimeoutError, socket.timeout):
            raise ProviderError(
                "provider-timeout", "embedding provider timed out"
            ) from None
        except HTTPError as error:
            error.close()
            raise ProviderError(
                "provider-unavailable", "embedding provider returned an HTTP error"
            ) from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ProviderError(
                    "provider-timeout", "embedding provider timed out"
                ) from None
            raise ProviderError(
                "provider-unavailable", "embedding provider could not be reached"
            ) from None
        except OSError:
            raise ProviderError(
                "provider-unavailable", "embedding provider could not be reached"
            ) from None
        if len(response_body) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderError("invalid-response", "provider response exceeds the byte budget")
        try:
            return json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError("invalid-response", "provider returned malformed JSON") from error

    def embed(self, texts: list[str]) -> list[list[float]]:
        normalized = self._validated_texts(texts)
        payload = self._request(normalized)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(normalized):
            raise ProviderError("invalid-response", "provider returned the wrong vector count")
        indexed: list[tuple[int, Any]] = []
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise ProviderError("invalid-response", "provider vector record is invalid")
            index = item.get("index", position)
            if isinstance(index, bool) or not isinstance(index, int):
                raise ProviderError("invalid-response", "provider vector index is invalid")
            indexed.append((index, item.get("embedding")))
        if sorted(index for index, _ in indexed) != list(range(len(normalized))):
            raise ProviderError("invalid-response", "provider vector indexes are invalid")
        vectors: list[list[float]] = []
        for _, raw_vector in sorted(indexed):
            if not isinstance(raw_vector, list):
                raise ProviderError("invalid-response", "provider vector is invalid")
            if len(raw_vector) != self.dimensions:
                raise ProviderError(
                    "dimension-mismatch",
                    "provider vector dimensions do not match the selected profile",
                )
            vector: list[float] = []
            for value in raw_vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ProviderError("invalid-response", "provider vector is invalid")
                converted = float(value)
                if not math.isfinite(converted):
                    raise ProviderError("invalid-response", "provider vector is not finite")
                vector.append(converted)
            if not any(value != 0.0 for value in vector):
                raise ProviderError("invalid-response", "provider vector is all zero")
            vectors.append(vector)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


ProviderFactory = Callable[[str, Mapping[str, Any], str], "EmbeddingProvider"]


@dataclass(frozen=True)
class _Registration:
    factory: ProviderFactory
    requires_credential: bool


class ProviderAdapterRegistry:
    """Bounded adapter registry; registration never imports optional dependencies."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        name: str,
        factory: ProviderFactory,
        *,
        requires_credential: bool = True,
    ) -> None:
        if not ADAPTER_NAME_RE.fullmatch(name):
            raise ProviderError("invalid-adapter", "provider adapter name is invalid")
        if name in self._registrations:
            raise ProviderError("duplicate-adapter", "provider adapter is already registered")
        if len(self._registrations) >= MAX_PROVIDER_ADAPTERS:
            raise ProviderError("adapter-limit", "provider adapter registry is full")
        self._registrations[name] = _Registration(factory, requires_credential)

    def contains(self, name: str) -> bool:
        return name in self._registrations

    def requires_credential(self, name: str) -> bool | None:
        registration = self._registrations.get(name)
        return registration.requires_credential if registration is not None else None

    def create(
        self,
        profile_name: str,
        config: Mapping[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "EmbeddingProvider":
        validated = _validated_provider_config(config)
        registration = self._registrations.get(str(validated["adapter"]))
        if registration is None:
            raise ProviderError("missing-adapter", "selected provider adapter is unavailable")
        environment = os.environ if environ is None else environ
        credential = environment.get(str(validated["credential_env"]), "")
        if registration.requires_credential and not credential:
            raise ProviderError("missing-credential", "provider credential is unavailable")
        try:
            return registration.factory(profile_name, validated, credential)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(
                "adapter-initialization", "provider adapter could not be initialized"
            ) from None


def default_provider_registry() -> ProviderAdapterRegistry:
    registry = ProviderAdapterRegistry()
    registry.register(
        "deterministic-fixture",
        lambda profile_name, config, credential: DeterministicFixtureEmbeddingProvider(
            profile_name, config
        ),
        requires_credential=False,
    )
    registry.register(
        "openai-compatible",
        lambda profile_name, config, credential: OpenAICompatibleEmbeddingProvider(
            profile_name, config, credential
        ),
    )
    return registry


def provider_status(
    profile_name: str,
    config: Mapping[str, Any],
    registry: ProviderAdapterRegistry,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Describe availability without exposing base URLs, credentials, or response data."""
    validated = _validated_provider_config(config)
    environment = os.environ if environ is None else environ
    adapter = str(validated["adapter"])
    adapter_registered = registry.contains(adapter)
    credential_available = bool(environment.get(str(validated["credential_env"])))
    credential_required = registry.requires_credential(adapter)
    if not adapter_registered:
        status = "missing-adapter"
    elif credential_required and not credential_available:
        status = "missing-credential"
    else:
        status = "ready"
    return {
        "profile": profile_name,
        "status": status,
        "adapter": adapter,
        "model": validated["model"],
        "dimensions": validated["dimensions"],
        "provider_config_sha256": provider_config_sha256(validated),
        "adapter_registered": adapter_registered,
        "credential_env": validated["credential_env"],
        "credential_available": credential_available,
    }
