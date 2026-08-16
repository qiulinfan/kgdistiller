from __future__ import annotations

import copy
import contextlib
import http.client
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import kgdistiller.api as api_module
import kgdistiller.federation as federation_module
import kgdistiller.source_archive as source_archive_module
from kgdistiller.api import ApiService, FederationSnapshotCache
from kgdistiller.contracts import canonical_json, validate_contract
from kgdistiller.federation import FederationSnapshot
from kgdistiller.native_compiler import sync_knowledge
from kgdistiller.recall import make_recall_request
from kgdistiller.source_archive import capture_source
from tests import test_federation as federation_fixture


def _payload(response: api_module.ApiHttpResponse) -> dict:
    return json.loads(response.body.decode("utf-8")) if response.body else {}


class ApiEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        federation_fixture.FederationFixture.setUp(self)
        self.source = self.analysis / "Sources" / "notes.md"
        self.source.parent.mkdir(exist_ok=True)
        self.source.write_text("first line\nmeasure evidence\nthird line\n", encoding="utf-8")
        captured = capture_source(self.source, home=self.home)
        self.document_id = captured["result"]["document_id"]
        sync_knowledge("analysis", home=self.home)
        with federation_module._INDEX_CACHE_LOCK:
            federation_module._INDEX_CACHE.clear()
        self.service = ApiService(home=self.home)

    def tearDown(self) -> None:
        federation_fixture.FederationFixture.tearDown(self)

    def _get(self, path: str, generation: str | None = None):
        headers = [] if generation is None else [(api_module.GENERATION_HEADER, generation)]
        return self.service.dispatch("GET", path, headers=headers)

    def test_all_twelve_routes_share_closed_generation_bound_dtos(self) -> None:
        status = self._get("/api/v1/status")
        self.assertEqual(200, status.status)
        status_payload = _payload(status)
        generation = status_payload["generation"]
        self.assertEqual(RESPONSE_SCHEMA := "qlkg-api-response-v1", status_payload["schema"])
        self.assertEqual(generation, status.headers[api_module.GENERATION_HEADER])
        self.assertIn("ETag", status.headers)
        self.assertEqual(
            {
                "kind": "status",
                "api_version": 1,
                "read_only": True,
                "registered_vaults": 2,
                "healthy_vaults": 2,
                "incomplete_vaults": 0,
            },
            status_payload["result"],
        )

        routes = [
            self._get("/api/v1/vaults"),
            self._get("/api/v1/vaults/analysis/roots", generation),
            self._get("/api/v1/vaults/analysis/nodes/measure?include_stale=true", generation),
            self._get("/api/v1/vaults/analysis/nodes/measure/neighbors?include_stale=true", generation),
            self._get("/api/v1/vaults/analysis/stale", generation),
            self._get(f"/api/v1/vaults/analysis/sources/{self.document_id}", generation),
            self._get(f"/api/v1/vaults/analysis/sources/{self.document_id}/versions", generation),
            self._get(f"/api/v1/vaults/analysis/sources/{self.document_id}/diff", generation),
            self._get(
                f"/api/v1/vaults/analysis/sources/{self.document_id}/excerpt?line=2&radius=1",
                generation,
            ),
        ]
        for response in routes:
            self.assertEqual(200, response.status, _payload(response))
            payload = _payload(response)
            self.assertEqual(RESPONSE_SCHEMA, payload["schema"])
            self.assertEqual(generation, payload["generation"])
            validate_contract(payload)

        for operation, request in (
            ("search", make_recall_request("search", query="measure", include_stale=True)),
            (
                "context",
                make_recall_request(
                    "context",
                    handles=["analysis:measure"],
                    include_stale=True,
                    token_budget=20_000,
                ),
            ),
        ):
            response = self.service.dispatch(
                "POST",
                f"/api/v1/{operation}",
                headers=[
                    (api_module.GENERATION_HEADER, generation),
                    ("Content-Type", "application/json; charset=utf-8"),
                ],
                body=canonical_json(request).encode("utf-8"),
            )
            self.assertEqual(200, response.status, _payload(response))
            validate_contract(_payload(response))

    def test_generation_preconditions_and_etag_are_closed_before_capture(self) -> None:
        with mock.patch.object(self.service.cache, "acquire", wraps=self.service.cache.acquire) as acquire:
            missing = self._get("/api/v1/vaults/analysis/roots")
            malformed = self.service.dispatch(
                "GET",
                "/api/v1/vaults/analysis/roots",
                headers=[(api_module.GENERATION_HEADER, "bad")],
            )
        self.assertEqual(428, missing.status)
        self.assertEqual(400, malformed.status)
        self.assertEqual("roots", _payload(missing)["route"])
        self.assertEqual("analysis", _payload(missing)["vault_id"])
        acquire.assert_not_called()

        status = self._get("/api/v1/status")
        current = _payload(status)["generation"]
        stale = self._get("/api/v1/vaults/analysis/roots", "0" * 64)
        self.assertEqual(409, stale.status)
        self.assertEqual(current, _payload(stale)["current_generation"])
        bootstrap = self.service.dispatch(
            "GET",
            "/api/v1/status",
            headers=[(api_module.GENERATION_HEADER, "0" * 64)],
        )
        self.assertEqual(200, bootstrap.status)

        match = self.service.dispatch(
            "GET",
            "/api/v1/status",
            headers=[("If-None-Match", status.headers["ETag"])],
        )
        self.assertEqual(304, match.status)
        self.assertEqual(b"", match.body)
        invalid = self.service.dispatch(
            "GET", "/api/v1/status", headers=[("If-None-Match", "weak")]
        )
        self.assertEqual(400, invalid.status)

        contextual = self._get(
            "/api/v1/vaults/analysis/roots?limit=0", current
        )
        contextual_payload = _payload(contextual)
        self.assertEqual(400, contextual.status)
        self.assertEqual("roots", contextual_payload["route"])
        self.assertEqual("analysis", contextual_payload["vault_id"])
        self.assertEqual(current, contextual_payload["current_generation"])

        unknown = self._get(
            "/api/v1/vaults/analysis/roots?unknown=1", current
        )
        self.assertEqual("roots", _payload(unknown)["route"])
        self.assertEqual("analysis", _payload(unknown)["vault_id"])
        malformed_body = self.service.dispatch(
            "POST",
            "/api/v1/search",
            headers=[
                (api_module.GENERATION_HEADER, current),
                ("Content-Type", "application/json"),
            ],
            body=b"{",
        )
        self.assertEqual(400, malformed_body.status)
        self.assertEqual("search", _payload(malformed_body)["route"])

        wrong_method = self.service.dispatch(
            "POST",
            "/api/v1/vaults/analysis/roots",
            headers=[(api_module.GENERATION_HEADER, current)],
        )
        unexpected_body = self.service.dispatch(
            "GET",
            "/api/v1/vaults/analysis/roots",
            headers=[(api_module.GENERATION_HEADER, current)],
            body=b"x",
        )
        for response in (wrong_method, unexpected_body):
            payload = _payload(response)
            self.assertEqual("roots", payload["route"])
            self.assertEqual("analysis", payload["vault_id"])

    def test_api_contract_rejects_forged_nested_and_source_semantics(self) -> None:
        status = _payload(self._get("/api/v1/status"))
        generation = status["generation"]
        node = _payload(
            self._get(
                "/api/v1/vaults/analysis/nodes/measure?include_stale=true",
                generation,
            )
        )
        forged_path = copy.deepcopy(node)
        forged_path["result"]["node"]["provenance"]["authority"] = "C:/Users/private.md"
        with self.assertRaisesRegex(ValueError, "canonical relative path"):
            validate_contract(forged_path)

        forged_generation = copy.deepcopy(status)
        forged_generation["vault_generations"][0]["graph_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Vault generation"):
            validate_contract(forged_generation)

        search_request = make_recall_request("search", query="measure", include_stale=True)
        search = _payload(
            self.service.dispatch(
                "POST",
                "/api/v1/search",
                headers=[
                    (api_module.GENERATION_HEADER, generation),
                    ("Content-Type", "application/json"),
                ],
                body=canonical_json(search_request).encode("utf-8"),
            )
        )
        forged_resolution = copy.deepcopy(search)
        forged_resolution["result"]["resolutions"] = [
            {
                "query": "missing",
                "status": "missing",
                "match_kind": None,
                "matches": ["analysis:measure"],
                "overflow": False,
            }
        ]
        with self.assertRaisesRegex(ValueError, "resolution fields"):
            validate_contract(forged_resolution)
        hidden_omission = copy.deepcopy(search)
        hidden_omission["result"]["omissions"] = [
            {"kind": "node", "id": "bounded", "reason": "limit"}
        ]
        hidden_omission["result"]["truncated"] = False
        with self.assertRaisesRegex(ValueError, "truncated"):
            validate_contract(hidden_omission)

        versions = _payload(
            self._get(
                f"/api/v1/vaults/analysis/sources/{self.document_id}/versions",
                generation,
            )
        )
        forged_version = copy.deepcopy(versions)
        forged_version["result"]["versions"][0]["sequence"] = 2
        with self.assertRaisesRegex(ValueError, "another document|predecessor"):
            validate_contract(forged_version)
        forged_time = copy.deepcopy(versions)
        forged_time["result"]["versions"][0]["captured_at"] = "not-a-time"
        with self.assertRaisesRegex(ValueError, "RFC3339"):
            validate_contract(forged_time)
        forged_cursor = copy.deepcopy(versions)
        forged_cursor["result"]["next_before_sequence"] = 1
        with self.assertRaisesRegex(ValueError, "untruncated"):
            validate_contract(forged_cursor)

        diff = _payload(
            self._get(
                f"/api/v1/vaults/analysis/sources/{self.document_id}/diff",
                generation,
            )
        )
        forged_diff = copy.deepcopy(diff)
        forged_diff["result"]["emitted_lines"] += 1
        with self.assertRaisesRegex(ValueError, "line count"):
            validate_contract(forged_diff)

        excerpt = _payload(
            self._get(
                f"/api/v1/vaults/analysis/sources/{self.document_id}/excerpt?line=2&radius=1",
                generation,
            )
        )
        forged_excerpt = copy.deepcopy(excerpt)
        forged_excerpt["result"]["line"] = forged_excerpt["result"]["end"] + 1
        with self.assertRaisesRegex(ValueError, "focus"):
            validate_contract(forged_excerpt)

    def test_source_status_uses_document_state_and_stale_index_drops_with_ready(self) -> None:
        first = federation_module.capture_federation(home=self.home)
        document = first.by_id["analysis"].ledger.documents[0]
        document["status"] = "stale"
        shells: list[FederationSnapshot] = []

        def fresh_shell(**_kwargs) -> FederationSnapshot:
            vaults = list(first.vaults)
            analysis_index = next(
                index for index, item in enumerate(vaults) if item.vault.id == "analysis"
            )
            card = copy.deepcopy(vaults[analysis_index].card)
            card["live_source_generation_sha256"] = (
                "1" * 64 if not shells else "2" * 64
            )
            vaults[analysis_index] = replace(vaults[analysis_index], card=card)
            shell = FederationSnapshot(
                registry_generation=first.registry_generation,
                generation=first.generation,
                vaults=tuple(vaults),
                incomplete_vaults=first.incomplete_vaults,
            )
            shells.append(shell)
            return shell

        cache = FederationSnapshotCache(capture=fresh_shell)
        service = ApiService(cache=cache)
        generation = _payload(service.dispatch("GET", "/api/v1/status"))["generation"]
        vaults = _payload(service.dispatch("GET", "/api/v1/vaults"))
        analysis_card = next(
            row for row in vaults["result"]["vaults"] if row["vault_id"] == "analysis"
        )
        self.assertEqual("2" * 64, analysis_card["live_source_generation_sha256"])
        self.assertIs(shells[-1], cache.ready)
        source = _payload(
            service.dispatch(
                "GET",
                f"/api/v1/vaults/analysis/sources/{self.document_id}",
                headers=[(api_module.GENERATION_HEADER, generation)],
            )
        )
        self.assertEqual("stale", source["result"]["source"]["status"])
        kinds: list[str] = []
        cursor = None
        with mock.patch.object(
            api_module, "_build_stale_index", wraps=api_module._build_stale_index
        ) as build_stale:
            for _ in range(10):
                suffix = "" if cursor is None else f"&cursor={cursor}"
                stale = _payload(
                    service.dispatch(
                        "GET",
                        f"/api/v1/vaults/analysis/stale?limit=1{suffix}",
                        headers=[(api_module.GENERATION_HEADER, generation)],
                    )
                )
                kinds.extend(row["kind"] for row in stale["result"]["items"])
                cursor = stale["result"]["next_cursor"]
                if cursor is None:
                    break
        self.assertEqual(1, build_stale.call_count)
        self.assertIn("source", kinds)
        self.assertIsNotNone(service._stale_ready)
        cache._capture = mock.Mock(
            side_effect=federation_module.FederationError("capture-failed", "failed")
        )
        failed = service.dispatch("GET", "/api/v1/status")
        self.assertEqual(503, failed.status)
        self.assertIsNone(service._stale_ready)

    def test_excerpt_reserves_focus_and_roots_stop_at_limit_plus_one(self) -> None:
        self.source.write_text(
            "\n".join(["x" * 4096] * 16 + ["focus line"]) + "\n",
            encoding="utf-8",
        )
        capture_source(self.source, home=self.home)
        sync_knowledge("analysis", home=self.home)
        service = ApiService(home=self.home)
        generation = _payload(service.dispatch("GET", "/api/v1/status"))["generation"]
        excerpt_response = service.dispatch(
            "GET",
            f"/api/v1/vaults/analysis/sources/{self.document_id}/excerpt?line=17&radius=16",
            headers=[(api_module.GENERATION_HEADER, generation)],
        )
        excerpt = _payload(excerpt_response)
        self.assertEqual(200, excerpt_response.status, excerpt)
        self.assertIn(17, [row["number"] for row in excerpt["result"]["lines"]])
        self.assertTrue(excerpt["result"]["truncated"])

        snapshot = federation_module.capture_federation(home=self.home)
        index = snapshot.by_id["analysis"].index
        object.__setattr__(index, "roots", tuple(["analysis-field"] * 1000))
        local = ApiService(cache=FederationSnapshotCache(capture=lambda **_kwargs: snapshot))
        with mock.patch.object(
            api_module, "_node_summary", wraps=api_module._node_summary
        ) as summarize:
            roots = local.dispatch(
                "GET",
                "/api/v1/vaults/analysis/roots?limit=1",
                headers=[(api_module.GENERATION_HEADER, snapshot.generation)],
            )
        self.assertEqual(200, roots.status, _payload(roots))
        self.assertEqual(2, summarize.call_count)

        with mock.patch.object(api_module, "verified_version_text", return_value=""):
            empty_focus = service.dispatch(
                "GET",
                f"/api/v1/vaults/analysis/sources/{self.document_id}/excerpt?line=2",
                headers=[(api_module.GENERATION_HEADER, generation)],
            )
        self.assertEqual(400, empty_focus.status)
        self.assertEqual("invalid-source-line", _payload(empty_focus)["error"]["code"])

    def test_neighbors_report_parent_omissions_and_hide_stale_endpoints(self) -> None:
        snapshot = federation_module.capture_federation(home=self.home)
        federated = snapshot.by_id["analysis"]

        class CountedParents:
            def __init__(self) -> None:
                self.consumed = 0

            def __iter__(self):
                for index in range(100_000):
                    self.consumed += 1
                    yield f"parent-{index}"

        counted = CountedParents()
        parents = dict(federated.index.parents)
        parents["measure-topic"] = counted
        object.__setattr__(federated.index, "parents", MappingProxyType(parents))
        service = ApiService(
            cache=FederationSnapshotCache(capture=lambda **_kwargs: snapshot)
        )
        response = service.dispatch(
            "GET",
            "/api/v1/vaults/analysis/nodes/measure/neighbors?include_stale=true",
            headers=[(api_module.GENERATION_HEADER, snapshot.generation)],
        )
        payload = _payload(response)
        self.assertEqual(200, response.status, payload)
        self.assertIn(
            {"kind": "node", "id": "analysis:measure-topic", "reason": "limit"},
            payload["result"]["omissions"],
        )
        self.assertFalse(any(row["kind"] == "edge" for row in payload["result"]["omissions"]))
        self.assertEqual(513, counted.consumed)

        federated.view.nodes["measure-topic"]["properties"][
            "curation_status"
        ] = "needs-review"
        neighbor_response = service.dispatch(
            "GET",
            "/api/v1/vaults/analysis/nodes/analysis-field/neighbors",
            headers=[(api_module.GENERATION_HEADER, snapshot.generation)],
        )
        neighbor_payload = _payload(neighbor_response)
        self.assertEqual(200, neighbor_response.status, neighbor_payload)
        neighbors = neighbor_payload["result"]
        self.assertNotIn(
            "analysis:measure-topic", [row["handle"] for row in neighbors["nodes"]]
        )
        self.assertNotIn(
            "analysis:measure-topic", [row["target"] for row in neighbors["edges"]]
        )
        detail = _payload(
            service.dispatch(
                "GET",
                "/api/v1/vaults/analysis/nodes/analysis-field",
                headers=[(api_module.GENERATION_HEADER, snapshot.generation)],
            )
        )["result"]
        self.assertNotIn(
            "analysis:measure-topic", [row["target"] for row in detail["edges"]]
        )

    def test_stale_peak_weight_and_diff_input_work_are_bounded(self) -> None:
        snapshot = federation_module.capture_federation(home=self.home)
        federated = snapshot.by_id["analysis"]
        for node in federated.view.nodes.values():
            node["properties"]["curation_status"] = "not-applicable"
            node["properties"]["source_status"] = "current"
        for edge in federated.view.edges:
            edge["curation_status"] = "not-applicable"
        federated.ledger.documents[0]["status"] = "stale"
        service = ApiService(
            cache=FederationSnapshotCache(capture=lambda **_kwargs: snapshot)
        )
        with mock.patch.object(api_module, "MAX_STALE_INDEX_BYTES", 512):
            response = service.dispatch(
                "GET",
                "/api/v1/vaults/analysis/stale",
                headers=[(api_module.GENERATION_HEADER, snapshot.generation)],
            )
        self.assertEqual(507, response.status)
        self.assertEqual("stale-index-too-large", _payload(response)["error"]["code"])

        ledger = federated.ledger
        current_id = str(ledger.documents[0]["current_version_id"])
        with (
            mock.patch.object(source_archive_module, "MAX_DIFF_INPUT_LINES", 1),
            mock.patch.object(
                source_archive_module,
                "verified_version_text",
                return_value="first\nsecond\n",
            ) as read_text,
            mock.patch.object(source_archive_module, "_bounded_diff") as diff,
        ):
            with self.assertRaisesRegex(
                source_archive_module.SourceArchiveError, "diff input bound"
            ):
                source_archive_module.verified_version_diff(
                    ledger,
                    document_id=self.document_id,
                    from_version_id=current_id,
                    to_version_id=current_id,
                )
        read_text.assert_called_once()
        diff.assert_not_called()

        object.__setattr__(
            ledger,
            "derivations",
            ({"version_id": current_id, "status": "superseded"},),
        )
        self.assertEqual(
            {current_id: "superseded"},
            api_module._derivation_statuses(federated, {current_id}),
        )


class SnapshotCacheTests(unittest.TestCase):
    def test_single_flight_retains_one_ready_and_failure_never_falls_back(self) -> None:
        snapshot = mock.sentinel.snapshot
        entered = threading.Event()
        release = threading.Event()

        def capture(**_kwargs):
            entered.set()
            release.wait(2)
            return snapshot

        cache = FederationSnapshotCache(capture=capture)
        results: list[object] = []
        threads = [threading.Thread(target=lambda: results.append(cache.acquire())) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(1))
        release.set()
        for thread in threads:
            thread.join(2)
        self.assertEqual([snapshot, snapshot], results)
        self.assertIs(snapshot, cache.ready)

        cache._capture = mock.Mock(side_effect=federation_module.FederationError("boom", "boom"))
        with self.assertRaises(federation_module.FederationError):
            cache.acquire()
        self.assertIsNone(cache.ready)

    def test_waiter_has_a_fixed_timeout(self) -> None:
        flight = api_module._CaptureFlight()
        cache = FederationSnapshotCache(capture=mock.Mock())
        cache._flight = flight
        with mock.patch.object(api_module, "MAX_CAPTURE_WAIT_SECONDS", 0.001):
            with self.assertRaisesRegex(federation_module.FederationError, "wait bound"):
                cache.acquire()


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        registry_generation = "1" * 64
        generation = federation_fixture.sha256_json(
            {
                "registry_generation": registry_generation,
                "vaults": [],
                "incomplete_vaults": [],
            }
        )
        self.snapshot = FederationSnapshot(
            registry_generation=registry_generation,
            generation=generation,
            vaults=(),
            incomplete_vaults=(),
        )
        self.service = ApiService(
            cache=FederationSnapshotCache(capture=lambda **_kwargs: self.snapshot)
        )
        self.server = api_module.create_api_server(
            host="127.0.0.1", port=0, service=self.service
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def _request(self, method: str, target: str, *, headers=None, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        try:
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, response.getheaders(), data
        finally:
            connection.close()

    def test_loopback_host_origin_methods_and_paths_are_closed(self) -> None:
        with mock.patch.object(api_module, "ThreadingHTTPServer") as constructor:
            with self.assertRaisesRegex(ValueError, "loopback"):
                api_module.create_api_server(host="0.0.0.0")
        constructor.assert_not_called()

        status, _, raw = self._request("DELETE", "/api/v1/status")
        self.assertEqual(405, status)
        self.assertEqual("qlkg-api-error-v1", json.loads(raw)["schema"])
        status, _, raw = self._request(
            "GET",
            "/api/v1/status",
            headers={"Origin": "http://example.invalid"},
        )
        self.assertEqual(403, status)
        self.assertEqual("forbidden-origin", json.loads(raw)["error"]["code"])
        status, _, raw = self._request("GET", "/api/v1/%252e%252e/status")
        self.assertEqual(400, status)
        self.assertEqual("invalid-request-target", json.loads(raw)["error"]["code"])

    def test_huge_content_length_is_rejected_without_integer_amplification(self) -> None:
        connection = socket.create_connection(
            ("127.0.0.1", self.server.server_port), timeout=2
        )
        try:
            request = (
                "POST /api/v1/search HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.server.server_port}\r\n"
                f"Content-Length: {'9' * 1000}\r\n"
                "Content-Type: application/json\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request)
            raw = connection.recv(8192)
        finally:
            connection.close()
        self.assertIn(b" 413 ", raw)
        self.assertIn(b"application/json", raw)

    def _raw_exchange(self, request: bytes) -> bytes:
        connection = socket.create_connection(
            ("127.0.0.1", self.server.server_port), timeout=2
        )
        chunks: list[bytes] = []
        try:
            connection.sendall(request)
            while True:
                try:
                    chunk = connection.recv(8192)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            connection.close()
        return b"".join(chunks)

    def test_rejected_framing_and_unknown_methods_close_without_desync(self) -> None:
        host = f"127.0.0.1:{self.server.server_port}"
        appended = f"GET /api/v1/status HTTP/1.1\r\nHost: {host}\r\n\r\n"
        requests = (
            (
                f"POST /api/v1/search HTTP/1.1\r\nHost: {host}\r\n"
                "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
                + appended
            ),
            (
                f"FOO /api/v1/status HTTP/1.1\r\nHost: {host}\r\n"
                "Content-Length: 4\r\n\r\nbody"
                + appended
            ),
        )
        for request in requests:
            raw = self._raw_exchange(request.encode("ascii"))
            self.assertEqual(1, raw.count(b"HTTP/1."), raw)
            self.assertIn(b"Connection: close", raw)

    def test_capacity_is_acquired_before_header_parsing(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        with mock.patch.object(api_module, "MAX_ACTIVE_HTTP_REQUESTS", 1):
            self.server = api_module.create_api_server(
                host="127.0.0.1", port=0, service=self.service
            )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        slow = socket.create_connection(
            ("127.0.0.1", self.server.server_port), timeout=2
        )
        try:
            slow.sendall(b"GET /api/v1/status HTTP/1.1\r\n")
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if not self.server._active_requests.acquire(blocking=False):
                    break
                self.server._active_requests.release()
                time.sleep(0.01)
            else:
                self.fail("slow header did not acquire the sole request slot")
            raw = self._raw_exchange(
                (
                    "GET /api/v1/status HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.server.server_port}\r\n\r\n"
                ).encode("ascii")
            )
            if raw:
                self.assertIn(b" 503 ", raw)
                self.assertIn(b"api-capacity-exhausted", raw)
            self.assertIsNone(self.service.cache.ready)
        finally:
            slow.close()

    def test_identifier_lengths_are_closed_at_the_http_boundary(self) -> None:
        generation = self.snapshot.generation
        headers = {api_module.GENERATION_HEADER: generation}
        vault_64 = "a" * 64
        for length, expected in ((64, "unknown-vault"), (65, "invalid-vault-id")):
            status, _, raw = self._request(
                "GET",
                f"/api/v1/vaults/{'a' * length}/roots",
                headers=headers,
            )
            payload = json.loads(raw)
            self.assertIn(status, {400, 404})
            self.assertEqual(expected, payload["error"]["code"])
            validate_contract(payload)
        for length, expected in ((256, "unknown-vault"), (257, "invalid-node-id")):
            status, _, raw = self._request(
                "GET",
                f"/api/v1/vaults/analysis/nodes/{'a' * length}",
                headers=headers,
            )
            payload = json.loads(raw)
            self.assertIn(status, {400, 404})
            self.assertEqual(expected, payload["error"]["code"])
            validate_contract(payload)


class CliIsolationTests(unittest.TestCase):
    def test_packaged_serve_is_default_and_legacy_serve_remains_explicit(self) -> None:
        import kgdistiller.cli as cli_module

        provider = mock.sentinel.provider
        with (
            mock.patch.object(
                sys, "argv", ["kgdistiller", "serve", "--federated", "--no-open"]
            ),
            mock.patch(
                "kgdistiller.frontend_assets.PackagedStaticAssetProvider",
                return_value=provider,
            ),
            mock.patch("kgdistiller.api.serve_api") as serve_api,
        ):
            self.assertEqual(0, cli_module.main())
        serve_api.assert_called_once_with(
            host="127.0.0.1",
            port=8765,
            static_assets=provider,
            open_browser=False,
        )

        with (
            mock.patch.object(
                sys, "argv", ["kgdistiller", "serve", "--legacy", "--no-open"]
            ),
            mock.patch("kgdistiller.web.serve_graph") as serve_graph,
            mock.patch("kgdistiller.api.serve_api") as serve_api,
            mock.patch(
                "kgdistiller.frontend_assets.PackagedStaticAssetProvider"
            ) as provider_constructor,
        ):
            self.assertEqual(0, cli_module.main())
        serve_graph.assert_called_once()
        serve_api.assert_not_called()
        provider_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
