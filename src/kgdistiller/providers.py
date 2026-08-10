"""Bounded optional embedding-provider adapters for machine-local profiles."""

from __future__ import annotations

import errno
import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
import re
import socket
import struct
import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .contracts import sha256_json

if TYPE_CHECKING:
    from .agent import EmbeddingProvider


MAX_PROVIDER_ADAPTERS = 32
MAX_EMBEDDING_BATCH = 128
MAX_EMBEDDING_TEXT_BYTES = 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_CREDENTIAL_BYTES = 8 * 1024
PROVIDER_RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_FLOAT32 = 3.4028234663852886e38
MAX_PROVIDER_JSON_DEPTH = 32
MAX_PROVIDER_JSON_NUMBER_BYTES = 128
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


def _normalized_base_url(value: str) -> str:
    utf8_failed = False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        utf8_failed = True
    if utf8_failed or not value or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ProviderError("invalid-provider-config", "base URL is invalid")
    parse_failed = False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        parse_failed = True
        parsed = None
        hostname = None
        port = None
    if parse_failed or parsed is None:
        raise ProviderError("invalid-provider-config", "base URL is invalid")

    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ProviderError("invalid-provider-config", "base URL is invalid")

    address = None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if address is not None:
        normalized_host = address.compressed.lower()
        rendered_host = f"[{normalized_host}]" if address.version == 6 else normalized_host
    else:
        idna_failed = False
        try:
            normalized_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            idna_failed = True
            normalized_host = ""
        labels = normalized_host.split(".") if normalized_host else []
        if (
            idna_failed
            or not labels
            or any(
                not label
                or len(label) > 63
                or re.fullmatch(r"[A-Za-z0-9_-]+", label) is None
                for label in labels
            )
        ):
            raise ProviderError("invalid-provider-config", "base URL is invalid")
        rendered_host = normalized_host

    if scheme == "http" and (address is None or not address.is_loopback):
        raise ProviderError(
            "invalid-provider-config",
            "plaintext provider URLs are restricted to numeric loopback hosts",
        )

    default_port = 80 if scheme == "http" else 443
    if port == default_port:
        port = None
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _validated_credential(credential: Any) -> str:
    if credential is None or credential == "":
        raise ProviderError("missing-credential", "provider credential is unavailable")
    if not isinstance(credential, str):
        raise ProviderError("invalid-provider-config", "provider credential is invalid")
    encoding_failed = False
    try:
        encoded = credential.encode("ascii")
    except UnicodeEncodeError:
        encoding_failed = True
        encoded = b""
    if (
        encoding_failed
        or len(encoded) > MAX_PROVIDER_CREDENTIAL_BYTES
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in credential
        )
    ):
        raise ProviderError("invalid-provider-config", "provider credential is invalid")
    return credential


def _validated_provider_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ProviderError("invalid-provider-config", "provider configuration is invalid")
    adapter = config.get("adapter")
    model = config.get("model")
    dimensions = config.get("dimensions")
    base_url = config.get("base_url")
    credential_env = config.get("credential_env")
    if not isinstance(adapter, str) or not ADAPTER_NAME_RE.fullmatch(adapter):
        raise ProviderError("invalid-provider-config", "adapter name is invalid")
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ProviderError("invalid-provider-config", "model is invalid")
    model_encoding_failed = False
    try:
        model.encode("utf-8")
    except UnicodeEncodeError:
        model_encoding_failed = True
    if model_encoding_failed:
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
    normalized_base_url = _normalized_base_url(base_url)
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
        "base_url": normalized_base_url,
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
        encoding_failed = False
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            encoding_failed = True
            encoded = b""
        if encoding_failed:
            raise ProviderError(
                "invalid-provider-request", "embedding text is not valid UTF-8"
            )
        total_bytes += len(encoded)
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


class _DeadlineSocketRaw(io.RawIOBase):
    """Raw response stream that tightens every recv to one absolute deadline."""

    def __init__(self, stream: Any, provider_socket: Any, deadline: float) -> None:
        super().__init__()
        self._stream = stream
        self._provider_socket = provider_socket
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return int(self._stream.fileno())

    def readinto(self, buffer: Any) -> int | None:
        remaining = _remaining_provider_timeout(self._deadline)
        self._provider_socket.settimeout(remaining)
        return self._stream.readinto(buffer)

    def close(self) -> None:
        if not self.closed:
            self._stream.close()
        super().close()


class _DeadlineSocketView:
    def __init__(self, provider_socket: Any, deadline: float) -> None:
        self._provider_socket = provider_socket
        self._deadline = deadline

    def makefile(self, mode: str, buffering: int | None = None) -> Any:
        if mode != "rb":
            return self._provider_socket.makefile(mode, buffering)
        stream = self._provider_socket.makefile("rb", buffering=0)
        return io.BufferedReader(
            _DeadlineSocketRaw(stream, self._provider_socket, self._deadline)
        )


class _DeadlineHTTPResponse(http.client.HTTPResponse):
    def __init__(
        self,
        provider_socket: Any,
        debuglevel: int = 0,
        method: str | None = None,
        url: str | None = None,
        *,
        deadline: float,
    ) -> None:
        super().__init__(
            _DeadlineSocketView(provider_socket, deadline),
            debuglevel=debuglevel,
            method=method,
            url=url,
        )


def _deadline_response_factory(deadline: float) -> Callable[..., Any]:
    return partial(_DeadlineHTTPResponse, deadline=deadline)


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, deadline: float, **kwargs: Any) -> None:
        self._provider_deadline = deadline
        super().__init__(*args, **kwargs)
        self.response_class = _deadline_response_factory(deadline)

    def connect(self) -> None:
        self.timeout = _remaining_provider_timeout(self._provider_deadline)
        super().connect()
        remaining = _remaining_provider_timeout(self._provider_deadline)
        if self.sock is not None:
            self.sock.settimeout(remaining)


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, deadline: float, **kwargs: Any) -> None:
        self._provider_deadline = deadline
        super().__init__(*args, **kwargs)
        self.response_class = _deadline_response_factory(deadline)

    def connect(self) -> None:
        self.timeout = _remaining_provider_timeout(self._provider_deadline)
        super().connect()
        remaining = _remaining_provider_timeout(self._provider_deadline)
        if self.sock is not None:
            self.sock.settimeout(remaining)


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline: float) -> None:
        super().__init__()
        self._provider_deadline = deadline

    def http_open(self, request: Request) -> Any:
        connection = partial(
            _DeadlineHTTPConnection,
            deadline=self._provider_deadline,
        )
        return self.do_open(connection, request)


class _DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, deadline: float) -> None:
        super().__init__()
        self._provider_deadline = deadline

    def https_open(self, request: Request) -> Any:
        connection = partial(
            _DeadlineHTTPSConnection,
            deadline=self._provider_deadline,
        )
        return self.do_open(connection, request, context=self._context)


def _uses_numeric_loopback_http(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        return False
    return parsed.scheme.lower() == "http" and address.is_loopback


def _open_provider_request(
    request: Request,
    timeout: float,
    *,
    deadline: float | None = None,
) -> Any:
    provider_deadline = (
        time.monotonic() + timeout if deadline is None else deadline
    )
    handlers: list[Any] = []
    if _uses_numeric_loopback_http(request.full_url):
        handlers.append(ProxyHandler({}))
    handlers.extend(
        (
            _DeadlineHTTPHandler(provider_deadline),
            _DeadlineHTTPSHandler(provider_deadline),
            _NoProviderRedirects(),
        )
    )
    return build_opener(*handlers).open(request, timeout=timeout)


def _remaining_provider_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("provider-timeout", "embedding provider timed out")
    return remaining


def _set_response_socket_timeout(response: Any, timeout: float) -> None:
    stream = getattr(response, "fp", None)
    raw = getattr(stream, "raw", None)
    provider_socket = getattr(raw, "_sock", None)
    if provider_socket is None:
        return
    try:
        provider_socket.settimeout(timeout)
    except (OSError, ValueError):
        pass


def _read_provider_response(response: Any, deadline: float) -> bytes:
    headers = response.headers
    get_all = getattr(headers, "get_all", None)
    content_lengths = (
        list(get_all("Content-Length") or [])
        if callable(get_all)
        else ([headers.get("Content-Length")] if headers.get("Content-Length") is not None else [])
    )
    transfer_encodings = (
        list(get_all("Transfer-Encoding") or [])
        if callable(get_all)
        else ([headers.get("Transfer-Encoding")] if headers.get("Transfer-Encoding") is not None else [])
    )
    if len(content_lengths) > 1 or len(transfer_encodings) > 1:
        raise ProviderError("invalid-response", "provider response framing is ambiguous")
    declared_length = content_lengths[0] if content_lengths else None
    transfer_encoding = transfer_encodings[0] if transfer_encodings else None
    if not all(
        isinstance(value, str)
        for value in (*content_lengths, *transfer_encodings)
    ):
        raise ProviderError("invalid-response", "provider response framing is invalid")
    if transfer_encoding is not None:
        codings = [item.strip().lower() for item in transfer_encoding.split(",")]
        if declared_length is not None or codings != ["chunked"]:
            raise ProviderError("invalid-response", "provider response framing is invalid")
    parsed_length: int | None = None
    invalid_length = False
    if declared_length is not None:
        normalized_length = declared_length.strip()
        if re.fullmatch(r"[0-9]+", normalized_length) is None:
            invalid_length = True
        else:
            try:
                parsed_length = int(normalized_length)
            except (ValueError, OverflowError):
                invalid_length = True
        if (
            invalid_length
            or parsed_length is None
            or parsed_length < 0
            or parsed_length > MAX_PROVIDER_RESPONSE_BYTES
        ):
            raise ProviderError(
                "invalid-response", "provider response has an invalid length"
            )

    target_bytes = (
        parsed_length if parsed_length is not None else MAX_PROVIDER_RESPONSE_BYTES + 1
    )
    chunks: list[bytes] = []
    received = 0
    read_once = getattr(response, "read1", None)
    if not callable(read_once):
        read_once = response.read
    while received < target_bytes:
        remaining = _remaining_provider_timeout(deadline)
        _set_response_socket_timeout(response, remaining)
        chunk = read_once(
            min(PROVIDER_RESPONSE_CHUNK_BYTES, target_bytes - received)
        )
        _remaining_provider_timeout(deadline)
        if not isinstance(chunk, bytes):
            raise ProviderError("invalid-response", "provider response body is invalid")
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
    if parsed_length is not None and received != parsed_length:
        raise ProviderError("invalid-response", "provider response body is incomplete")
    if received > MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderError("invalid-response", "provider response exceeds the byte budget")
    return b"".join(chunks)


def _is_timeout_error(error: BaseException) -> bool:
    current: Any = error
    seen: set[int] = set()
    for _ in range(5):
        if id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if isinstance(current, OSError) and (
            getattr(current, "errno", None) == errno.ETIMEDOUT
            or getattr(current, "winerror", None) == 10060
        ):
            return True
        next_error = getattr(current, "reason", None)
        if not isinstance(next_error, BaseException):
            next_error = getattr(current, "__cause__", None)
        if not isinstance(next_error, BaseException):
            break
        current = next_error
    return False


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON constants are forbidden")


def _bounded_json_int(value: str) -> int:
    if len(value) > MAX_PROVIDER_JSON_NUMBER_BYTES:
        raise ValueError("provider JSON integer is too long")
    return int(value)


def _bounded_json_float(value: str) -> float:
    if len(value) > MAX_PROVIDER_JSON_NUMBER_BYTES:
        raise ValueError("provider JSON float is too long")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("provider JSON float is not finite")
    return parsed


def _provider_json_is_valid(value: Any) -> bool:
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_PROVIDER_JSON_DEPTH:
            return False
        if isinstance(current, float) and not math.isfinite(current):
            return False
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return True


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
        validated_credential = _validated_credential(credential)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
            or timeout_seconds > 120
            or not math.isfinite(float(timeout_seconds))
        ):
            raise ProviderError("invalid-provider-config", "provider timeout is invalid")
        self.profile_name = profile_name
        self.name = str(validated["adapter"])
        self.model = str(validated["model"])
        self.dimensions = int(validated["dimensions"])
        self.provider_config_sha256 = provider_config_sha256(validated)
        self._base_url = str(validated["base_url"])
        self._credential = validated_credential
        self._timeout_seconds = float(timeout_seconds)

    def _validated_texts(self, texts: list[str]) -> list[str]:
        return _validated_embedding_texts(texts)

    def _request(self, texts: list[str]) -> Any:
        body_failed = False
        try:
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
        except (TypeError, ValueError, UnicodeError, OverflowError):
            body_failed = True
            body = b""
        if body_failed:
            raise ProviderError(
                "invalid-provider-request", "embedding request could not be encoded"
            )

        request_failed = False
        try:
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
        except Exception:
            request_failed = True
            request = None
        if request_failed or request is None:
            raise ProviderError(
                "provider-unavailable", "embedding provider request is invalid"
            )

        deadline = time.monotonic() + self._timeout_seconds
        phase = "connect"
        failure: tuple[str, str] | None = None
        try:
            with _open_provider_request(
                request,
                _remaining_provider_timeout(deadline),
                deadline=deadline,
            ) as response:
                phase = "response"
                response_body = _read_provider_response(response, deadline)
        except ProviderError as error:
            message = error.message
            if self._credential in f"{error.code}: {message}":
                failure = (
                    "provider-unavailable",
                    "embedding provider request failed",
                )
            else:
                failure = (error.code, message)
        except HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            failure = (
                "provider-unavailable",
                "embedding provider returned an HTTP error",
            )
        except URLError as error:
            failure = (
                (
                    "provider-timeout",
                    "embedding provider timed out",
                )
                if _is_timeout_error(error) or time.monotonic() >= deadline
                else (
                    "provider-unavailable",
                    "embedding provider could not be reached",
                )
            )
        except Exception as error:
            if _is_timeout_error(error) or time.monotonic() >= deadline:
                failure = ("provider-timeout", "embedding provider timed out")
            elif phase == "response" or isinstance(error, http.client.HTTPException):
                failure = ("invalid-response", "provider returned an invalid HTTP response")
            else:
                failure = (
                    "provider-unavailable",
                    "embedding provider could not be reached",
                )
        if failure is not None:
            raise ProviderError(*failure)

        json_failed = False
        try:
            payload = json.loads(
                response_body.decode("utf-8"),
                parse_constant=_reject_nonfinite_json_constant,
                parse_int=_bounded_json_int,
                parse_float=_bounded_json_float,
            )
        except (UnicodeDecodeError, ValueError, RecursionError, OverflowError):
            json_failed = True
            payload = None
        if json_failed or not _provider_json_is_valid(payload):
            raise ProviderError("invalid-response", "provider returned malformed JSON")
        _remaining_provider_timeout(deadline)
        return payload

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
                conversion_failed = False
                try:
                    converted = float(value)
                except (ValueError, OverflowError):
                    conversion_failed = True
                    converted = 0.0
                if conversion_failed:
                    raise ProviderError("invalid-response", "provider vector is invalid")
                if not math.isfinite(converted):
                    raise ProviderError("invalid-response", "provider vector is not finite")
                if abs(converted) > MAX_FLOAT32:
                    raise ProviderError(
                        "invalid-response", "provider vector exceeds float32 range"
                    )
                quantization_failed = False
                try:
                    quantized = struct.unpack("<f", struct.pack("<f", converted))[0]
                except (OverflowError, struct.error):
                    quantization_failed = True
                    quantized = 0.0
                if quantization_failed or not math.isfinite(quantized):
                    raise ProviderError(
                        "invalid-response", "provider vector exceeds float32 range"
                    )
                vector.append(quantized)
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
        raw_credential = environment.get(str(validated["credential_env"]), "")
        credential = (
            _validated_credential(raw_credential)
            if registration.requires_credential
            else ""
        )
        failure: tuple[str, str] | None = None
        try:
            return registration.factory(profile_name, validated, credential)
        except ProviderError as error:
            if credential and credential in f"{error.code}: {error.message}":
                failure = (
                    "adapter-initialization",
                    "provider adapter could not be initialized",
                )
            else:
                failure = (error.code, error.message)
        except Exception:
            failure = (
                "adapter-initialization", "provider adapter could not be initialized"
            )
        if failure is not None:
            raise ProviderError(*failure)
        raise ProviderError(
            "adapter-initialization", "provider adapter could not be initialized"
        )


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
    raw_credential = environment.get(str(validated["credential_env"]), "")
    credential_available = isinstance(raw_credential, str) and bool(raw_credential)
    credential_required = registry.requires_credential(adapter)
    if not adapter_registered:
        status = "missing-adapter"
    elif credential_required:
        credential_failure = None
        try:
            _validated_credential(raw_credential)
        except ProviderError as error:
            credential_failure = error.code
        status = credential_failure or "ready"
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
