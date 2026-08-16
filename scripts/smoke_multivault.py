#!/usr/bin/env python3
"""Run one disposable, real multi-Vault kgdistiller smoke workflow."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


SUMMARY_SCHEMA = "kgdistiller-multivault-smoke-v1"
CLI_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 5
SERVER_START_TIMEOUT_SECONDS = 15
SERVER_STOP_TIMEOUT_SECONDS = 5
MAX_HTTP_BODY_BYTES = 4 * 1024 * 1024
GENERATION_HEADER = "Kgdistiller-Generation"

STEP_NAMES = (
    "vault-setup",
    "source-archive",
    "transactional-ingest",
    "native-notes",
    "source-change",
    "federated-recall",
    "packaged-api",
    "portable-snapshot",
    "vault-move",
    "incomplete-vault",
)


class SmokeFailure(Exception):
    """A path-free failure suitable for the closed smoke summary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _finalize_self_digest(value: Mapping[str, Any], field: str) -> Dict[str, Any]:
    payload = json.loads(_canonical_json(dict(value)))
    payload.pop(field, None)
    digest = _sha256_json(payload)
    payload[field] = digest
    return payload


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canonical_json(value) + "\n").encode("utf-8"))


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _field_note(vault_id: str) -> str:
    title = "Alpha Foundations" if vault_id == "alpha" else "Beta Foundations"
    return "\n".join(
        (
            "---",
            "kgd_schema: qlkg-taxonomy-v1",
            "kgd_id: %s-field" % vault_id,
            "kgd_kind: field",
            "aliases: []",
            "kgd_parents: []",
            "---",
            "",
            "# %s" % title,
            "",
            "A portable field created by the multi-Vault smoke.",
            "",
        )
    )


def _topic_note(vault_id: str) -> str:
    title = "Alpha Shared Topic" if vault_id == "alpha" else "Beta Shared Topic"
    field_title = "Alpha Foundations" if vault_id == "alpha" else "Beta Foundations"
    return "\n".join(
        (
            "---",
            "kgd_schema: qlkg-taxonomy-v1",
            "kgd_id: %s-topic" % vault_id,
            "kgd_kind: topic",
            "aliases: []",
            'kgd_parents: ["[[Knowledge/Fields/%s]]"]' % field_title,
            "---",
            "",
            "# %s" % title,
            "",
            "A portable topic created by the multi-Vault smoke.",
            "",
        )
    )


def _concept_note(vault_id: str) -> str:
    field_title = "Alpha Foundations" if vault_id == "alpha" else "Beta Foundations"
    topic_title = "Alpha Shared Topic" if vault_id == "alpha" else "Beta Shared Topic"
    return "\n".join(
        (
            "---",
            "kgd_schema: qlkg-concept-v1",
            "kgd_id: %s-principle" % vault_id,
            "aliases: []",
            "tags: [kgdistiller/concept]",
            'kgd_fields: ["[[Knowledge/Fields/%s]]"]' % field_title,
            'kgd_topics: ["[[Knowledge/Topics/%s]]"]' % topic_title,
            "kgd_prerequisites: []",
            "kgd_implies: []",
            "kgd_generalizes: []",
            "kgd_contrasts_with: []",
            "kgd_derived_from: []",
            "---",
            "",
            "# Shared Principle",
            "",
            "A shared principle grounded in %s source evidence." % vault_id,
            "",
        )
    )


def _recall_request(
    operation: str,
    *,
    query: Optional[str] = None,
    handles: Sequence[str] = (),
) -> Dict[str, Any]:
    return {
        "schema": "qlkg-recall-request-v1",
        "operation": operation,
        "vault_ids": [],
        "queries": [],
        "query": query,
        "handle": None,
        "handles": list(handles),
        "scopes": [],
        "direction": "both",
        "edge_types": [],
        "max_depth": 1,
        "limit": 20,
        "token_budget": 12000 if operation == "context" else 6000,
        "include_stale": True,
    }


class MultiVaultSmoke:
    def __init__(self, workspace: Path, *, workspace_mode: str) -> None:
        self.workspace = workspace
        self.workspace_mode = workspace_mode
        self.home = workspace / "home"
        self.alpha = workspace / "AlphaVault"
        self.beta = workspace / "BetaVault"
        self.beta_moved = workspace / "MovedBetaVault"
        self.alpha_offline = workspace / "AlphaOffline"
        self.server_cwd = workspace / "server-cwd"
        self.steps: List[Dict[str, Any]] = []
        self._step_checks = 0
        self._active_step: Optional[str] = None
        self.server: Optional[subprocess.Popen[Any]] = None
        self.alpha_is_offline = False
        self.captures: Dict[str, Dict[str, Any]] = {}
        self.registry_generation = ""
        self.beta_store_sha256: Optional[str] = None

        self.environment = os.environ.copy()
        self.environment["KGDISTILLER_HOME"] = str(self.home)

    def expect(self, condition: bool, code: str) -> None:
        self._step_checks += 1
        if not condition:
            raise SmokeFailure(code)

    @contextlib.contextmanager
    def step(self, name: str) -> Iterator[None]:
        if name not in STEP_NAMES or self._active_step is not None:
            raise SmokeFailure("invalid-step-state")
        self._active_step = name
        self._step_checks = 0
        started = time.monotonic()
        try:
            yield
        except Exception:
            self.steps.append(
                {
                    "name": name,
                    "status": "failed",
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "checks": self._step_checks,
                }
            )
            raise
        else:
            self.steps.append(
                {
                    "name": name,
                    "status": "passed",
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "checks": self._step_checks,
                }
            )
        finally:
            self._active_step = None

    def cli(self, *arguments: str) -> Dict[str, Any]:
        command = [sys.executable, "-m", "kgdistiller", *arguments]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.server_cwd),
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SmokeFailure("cli-timeout") from error
        except OSError as error:
            raise SmokeFailure("cli-start-failed") from error
        if completed.returncode != 0:
            raise SmokeFailure("cli-command-failed")
        try:
            payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SmokeFailure("cli-invalid-json") from error
        if not isinstance(payload, dict):
            raise SmokeFailure("cli-invalid-payload")
        return payload

    def _note_inventory_sha256(self, vault: Path) -> str:
        rows: List[List[str]] = []
        for root in ("Knowledge/Concepts", "Knowledge/Fields", "Knowledge/Topics"):
            selected = vault.joinpath(*root.split("/"))
            for path in sorted(selected.rglob("*.md"), key=lambda item: item.as_posix()):
                relative = path.relative_to(vault).as_posix()
                rows.append([relative, _sha256_bytes(path.read_bytes())])
        rows.sort(key=lambda item: item[0])
        return _sha256_json(rows)

    def _graph_generation(self, vault: Path) -> Optional[str]:
        manifest_path = vault / ".kgdistiller" / "graph" / "manifest.json"
        if not manifest_path.is_file():
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = payload.get("graph_sha256")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise SmokeFailure("invalid-graph-generation")
        return value

    def _ingest_request(
        self,
        *,
        vault_id: str,
        vault: Path,
        committed_capture: Mapping[str, Any],
        reviewed_empty_captures: Sequence[Mapping[str, Any]],
        evidence_line: str,
        query_report: Mapping[str, Any],
    ) -> Path:
        request_root = self.workspace / "requests" / vault_id
        request_root.mkdir(parents=True, exist_ok=True)
        query_path = request_root / "query.json"
        query_payload = json.loads(_canonical_json(dict(query_report)))
        _write_json(query_path, query_payload)
        query_sha256 = _sha256_bytes(query_path.read_bytes())

        manifest = json.loads(
            (vault / ".kgdistiller" / "vault.json").read_text(encoding="utf-8")
        )
        concept_id = "%s-principle" % vault_id
        field_id = "%s-field" % vault_id
        topic_id = "%s-topic" % vault_id
        version_id = str(committed_capture["result"]["current_version_id"])
        span = {
            "version_id": version_id,
            "start_line": 2,
            "end_line": 2,
            "excerpt_sha256": _sha256_bytes(evidence_line.encode("utf-8")),
        }

        notes = (
            ("Knowledge/Concepts/Shared Principle.md", _concept_note(vault_id)),
            (
                "Knowledge/Fields/%s Foundations.md"
                % ("Alpha" if vault_id == "alpha" else "Beta"),
                _field_note(vault_id),
            ),
            (
                "Knowledge/Topics/%s Shared Topic.md"
                % ("Alpha" if vault_id == "alpha" else "Beta"),
                _topic_note(vault_id),
            ),
        )
        patches = [
            {
                "path": path,
                "operation": "write",
                "expected_raw_sha256": None,
                "content": content,
                "content_sha256": _sha256_bytes(content.encode("utf-8")),
            }
            for path, content in notes
        ]
        committed_update = {
            "version_id": version_id,
            "status": "committed",
            "candidate_dispositions": [
                {"candidate_id": concept_id, "disposition": "add"}
            ],
            "concept_ids": [concept_id],
            "concept_evidence": [{"concept_id": concept_id, "spans": [span]}],
            "relation_evidence": [
                {
                    "source": field_id,
                    "relation": "contains",
                    "target": topic_id,
                    "spans": [span],
                },
                {
                    "source": field_id,
                    "relation": "contains",
                    "target": concept_id,
                    "spans": [span],
                },
                {
                    "source": topic_id,
                    "relation": "contains",
                    "target": concept_id,
                    "spans": [span],
                },
            ],
        }
        empty_updates = [
            {
                "version_id": str(item["result"]["current_version_id"]),
                "status": "reviewed-empty",
                "candidate_dispositions": [],
                "concept_ids": [],
                "concept_evidence": [],
                "relation_evidence": [],
            }
            for item in reviewed_empty_captures
        ]
        request = _finalize_self_digest(
            {
                "schema": "qlkg-vault-ingest-request-v1",
                "request_id": "multivault-smoke-%s" % vault_id,
                "request_sha256": "0" * 64,
                "capabilities": ["vault-transactional-ingest-v1"],
                "vault_id": vault_id,
                "registry_generation": self.registry_generation,
                "vault_manifest_sha256": _sha256_json(manifest),
                "base": {
                    "source_ledger_generation_sha256": str(
                        reviewed_empty_captures[-1]["ledger_generation"]
                        if reviewed_empty_captures
                        else committed_capture["ledger_generation"]
                    ),
                    "graph_generation_sha256": self._graph_generation(vault),
                    "note_inventory_sha256": self._note_inventory_sha256(vault),
                },
                "query_report": {"path": "query.json", "sha256": query_sha256},
                "note_patches": patches,
                "derivation_updates": [committed_update, *empty_updates],
                "alignment_mutations": [],
                "review": {
                    "status": "reviewed",
                    "reviewer": "kgdistiller-multivault-smoke",
                    "evidence": "disposable source spans and native notes were reviewed together",
                    "provenance": "scripts/smoke_multivault.py",
                },
            },
            "request_sha256",
        )
        request_path = request_root / "request.json"
        _write_json(request_path, request)
        return request_path

    def _http(
        self,
        port: int,
        method: str,
        path: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        body: Optional[bytes] = None,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> Tuple[int, Dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=dict(headers or {}))
            response = connection.getresponse()
            raw = response.read(MAX_HTTP_BODY_BYTES + 1)
            if len(raw) > MAX_HTTP_BODY_BYTES:
                raise SmokeFailure("http-body-too-large")
            return (
                response.status,
                {name.casefold(): value for name, value in response.getheaders()},
                raw,
            )
        except (OSError, http.client.HTTPException) as error:
            raise SmokeFailure("http-request-failed") from error
        finally:
            connection.close()

    def _http_json(
        self,
        port: int,
        method: str,
        path: str,
        *,
        expected_status: int,
        generation: Optional[str] = None,
        request: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        headers: Dict[str, str] = {}
        body = None
        if generation is not None:
            headers[GENERATION_HEADER] = generation
        if request is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            body = _canonical_json(request).encode("utf-8")
        status, response_headers, raw = self._http(
            port, method, path, headers=headers, body=body
        )
        self.expect(status == expected_status, "unexpected-http-status")
        try:
            payload = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SmokeFailure("http-invalid-json") from error
        self.expect(isinstance(payload, dict), "http-invalid-payload")
        return payload, response_headers

    def _start_server(self) -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        finally:
            probe.close()
        command = [
            sys.executable,
            "-m",
            "kgdistiller",
            "serve",
            "--federated",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-open",
        ]
        try:
            self.server = subprocess.Popen(
                command,
                cwd=str(self.server_cwd),
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise SmokeFailure("server-start-failed") from error
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise SmokeFailure("server-exited-early")
            try:
                status, _, _ = self._http(
                    port, "GET", "/api/v1/status", timeout=0.5
                )
                if status == 200:
                    return port
            except SmokeFailure:
                pass
            time.sleep(0.05)
        raise SmokeFailure("server-start-timeout")

    def stop_server(self) -> None:
        process = self.server
        if process is None or process.poll() is not None:
            self.server = None
            return
        process.terminate()
        try:
            process.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                raise SmokeFailure("server-stop-timeout") from error
        finally:
            if process.poll() is not None:
                self.server = None

    def run(self) -> None:
        self.server_cwd.mkdir()

        with self.step("vault-setup"):
            alpha = self.cli(
                "vault", "init", str(self.alpha), "--id", "alpha", "--label", "Alpha Vault"
            )
            beta = self.cli(
                "vault", "init", str(self.beta), "--id", "beta", "--label", "Beta Vault"
            )
            listed = self.cli("vault", "list")
            self.expect(alpha.get("action") == "init", "alpha-init-failed")
            self.expect(beta.get("action") == "init", "beta-init-failed")
            self.expect(listed.get("schema") == "qlkg-vault-report-v1", "invalid-vault-list")
            self.expect(
                [item.get("id") for item in listed["result"]["vaults"]]
                == ["alpha", "beta"],
                "unexpected-vault-list",
            )
            generation = listed.get("registry_generation")
            self.expect(
                isinstance(generation, str) and bool(re.fullmatch(r"[0-9a-f]{64}", generation)),
                "invalid-registry-generation",
            )
            self.registry_generation = str(generation)

        with self.step("source-archive"):
            alpha_markdown = self.alpha / "Sources" / "alpha.md"
            alpha_typst = self.alpha / "Sources" / "alpha.typ"
            beta_latex = self.beta / "Sources" / "beta.tex"
            _write_lf(
                alpha_markdown,
                "Alpha source header.\nShared Principle evidence for alpha.\nAlpha tail.\n",
            )
            _write_lf(
                alpha_typst,
                "Typst source header.\nA reviewed empty Typst source.\nTypst tail.\n",
            )
            _write_lf(
                beta_latex,
                "LaTeX source header.\nShared Principle evidence for beta.\nLaTeX tail.\n",
            )
            sources = (
                ("alpha-md", alpha_markdown, "alpha", "markdown"),
                ("alpha-typ", alpha_typst, "alpha", "typst"),
                ("beta-tex", beta_latex, "beta", "latex"),
            )
            for key, path, vault_id, source_format in sources:
                located = self.cli("vault", "locate", str(path))
                self.expect(located.get("action") == "locate", "source-locate-failed")
                self.expect(
                    located["result"]["vault"]["id"] == vault_id,
                    "source-located-in-wrong-vault",
                )
                captured = self.cli("source", "capture", str(path))
                self.expect(captured.get("action") == "capture", "source-capture-failed")
                self.expect(captured["result"]["format"] == source_format, "wrong-source-format")
                self.expect(captured["result"]["outcome"] == "capture", "wrong-capture-outcome")
                self.captures[key] = captured

        with self.step("transactional-ingest"):
            query_reports: Dict[str, Dict[str, Any]] = {}
            for vault_id in ("alpha", "beta"):
                report = self.cli("recall", "status", "--vault", vault_id)
                self.expect(
                    report.get("schema") == "qlkg-recall-report-v1",
                    "ingest-query-report-schema",
                )
                self.expect(report.get("operation") == "status", "ingest-query-report-operation")
                self.expect(report.get("status") == "partial", "ingest-query-report-status")
                self.expect(
                    report.get("registry_generation") == self.registry_generation,
                    "ingest-query-report-registry",
                )
                self.expect(
                    isinstance(report.get("generation"), str)
                    and bool(re.fullmatch(r"[0-9a-f]{64}", str(report.get("generation")))),
                    "ingest-query-report-generation",
                )
                self.expect(report.get("vaults") == [], "ingest-query-report-vault-projection")
                self.expect(
                    [item.get("vault_id") for item in report.get("incomplete_vaults", [])]
                    == [vault_id],
                    "ingest-query-report-incomplete-vault",
                )
                result = report.get("result", {})
                self.expect(
                    isinstance(result, dict)
                    and result.get("query") is None
                    and result.get("resolutions") == []
                    and result.get("nodes") == []
                    and result.get("edges") == []
                    and result.get("evidence") == []
                    and result.get("truncated") is False,
                    "ingest-query-report-not-bounded",
                )
                self.expect(
                    str(self.workspace).casefold()
                    not in _canonical_json(report).casefold(),
                    "ingest-query-report-contained-path",
                )
                query_reports[vault_id] = report
            alpha_request = self._ingest_request(
                vault_id="alpha",
                vault=self.alpha,
                committed_capture=self.captures["alpha-md"],
                reviewed_empty_captures=[self.captures["alpha-typ"]],
                evidence_line="Shared Principle evidence for alpha.",
                query_report=query_reports["alpha"],
            )
            beta_request = self._ingest_request(
                vault_id="beta",
                vault=self.beta,
                committed_capture=self.captures["beta-tex"],
                reviewed_empty_captures=[],
                evidence_line="Shared Principle evidence for beta.",
                query_report=query_reports["beta"],
            )
            for vault_id, request_path in (
                ("alpha", alpha_request),
                ("beta", beta_request),
            ):
                plan_path = request_path.parent / "plan.json"
                receipt_path = request_path.parent / "receipt.json"
                planned = self.cli(
                    "knowledge", "ingest", "plan", str(request_path), "--output", str(plan_path)
                )
                self.expect(planned.get("action") == "plan", "ingest-plan-failed")
                self.expect(planned.get("vault_id") == vault_id, "ingest-plan-wrong-vault")
                self.expect(plan_path.is_file(), "ingest-plan-missing")
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.expect(plan.get("status") == "ready", "ingest-plan-not-ready")
                applied = self.cli(
                    "knowledge",
                    "ingest",
                    "apply",
                    str(request_path),
                    "--receipt",
                    str(receipt_path),
                )
                self.expect(applied.get("action") == "apply", "ingest-apply-failed")
                self.expect(applied.get("outcome") == "committed", "ingest-not-committed")
                self.expect(receipt_path.is_file(), "ingest-receipt-missing")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.expect(receipt.get("status") == "committed", "invalid-ingest-receipt")

        with self.step("native-notes"):
            for vault_id, vault in (("alpha", self.alpha), ("beta", self.beta)):
                checked = self.cli("knowledge", "check", "--vault", vault_id)
                self.expect(checked.get("status") == "ok", "native-check-failed")
                field_name = "%s Foundations.md" % ("Alpha" if vault_id == "alpha" else "Beta")
                topic_name = "%s Shared Topic.md" % ("Alpha" if vault_id == "alpha" else "Beta")
                paths = (
                    vault / "Knowledge" / "Fields" / field_name,
                    vault / "Knowledge" / "Topics" / topic_name,
                    vault / "Knowledge" / "Concepts" / "Shared Principle.md",
                )
                bodies = [path.read_text(encoding="utf-8") for path in paths]
                self.expect(all(body.startswith("---\nkgd_schema:") for body in bodies), "invalid-native-note")
                self.expect(
                    "kgd_id: %s-principle" % vault_id in bodies[-1],
                    "native-concept-id-missing",
                )
                self.expect(
                    "[[Knowledge/Fields/" in bodies[-1]
                    and "[[Knowledge/Topics/" in bodies[-1],
                    "native-links-missing",
                )

        with self.step("source-change"):
            alpha_markdown = self.alpha / "Sources" / "alpha.md"
            old_version = self.captures["alpha-md"]["result"]["current_version_id"]
            _write_lf(
                alpha_markdown,
                "Alpha source header.\nShared Principle evidence for alpha, revised.\nAlpha tail.\n",
            )
            pending = self.cli("source", "status", str(alpha_markdown))
            self.expect(pending["result"]["outcome"] == "semantic-change", "source-change-not-detected")
            changed = self.cli("source", "capture", str(alpha_markdown))
            self.expect(changed["result"]["predecessor_version_id"] == old_version, "wrong-predecessor")
            self.expect(changed["result"]["semantic_changed"] is True, "semantic-change-not-captured")
            self.expect(
                "alpha-principle" in changed["result"]["affected_concept_ids"],
                "affected-concept-missing",
            )
            diffed = self.cli("source", "diff", str(alpha_markdown))
            self.expect(diffed["result"]["predecessor_version_id"] == old_version, "diff-wrong-predecessor")
            self.expect("revised" in diffed["result"]["diff"]["text"], "diff-content-missing")
            self.expect(
                "alpha-principle" in diffed["result"]["affected_concept_ids"],
                "diff-affected-concept-missing",
            )
            synchronized = self.cli("knowledge", "sync", "--vault", "alpha")
            self.expect(synchronized.get("status") == "ok", "changed-vault-sync-failed")
            self.captures["alpha-md-v2"] = changed

        with self.step("federated-recall"):
            reports = {
                "status": self.cli("recall", "status"),
                "roots": self.cli("recall", "roots", "--include-stale"),
                "resolve": self.cli(
                    "recall",
                    "resolve",
                    "Shared Principle",
                    "--vault",
                    "alpha",
                    "--vault",
                    "beta",
                    "--include-stale",
                ),
                "search": self.cli(
                    "recall", "search", "Shared Principle", "--include-stale"
                ),
                "get": self.cli(
                    "recall", "get", "alpha:alpha-principle", "--include-stale"
                ),
                "expand": self.cli(
                    "recall",
                    "expand",
                    "alpha:alpha-field",
                    "beta:beta-field",
                    "--direction",
                    "outgoing",
                    "--relation",
                    "contains",
                    "--include-stale",
                ),
                "context": self.cli(
                    "recall",
                    "context",
                    "--handle",
                    "alpha:alpha-principle",
                    "--handle",
                    "beta:beta-principle",
                    "--budget",
                    "12000",
                    "--include-stale",
                ),
            }
            for operation, report in reports.items():
                self.expect(report.get("schema") == "qlkg-recall-report-v1", "invalid-recall-report")
                self.expect(report.get("operation") == operation, "wrong-recall-operation")
            self.expect(reports["status"].get("status") == "complete", "federation-not-complete")
            resolved = reports["resolve"]["result"]["resolutions"][0]
            self.expect(
                set(resolved["matches"])
                == {"alpha:alpha-principle", "beta:beta-principle"},
                "cross-vault-resolution-missing",
            )
            searched = {item["handle"] for item in reports["search"]["result"]["nodes"]}
            self.expect(
                {"alpha:alpha-principle", "beta:beta-principle"}.issubset(searched),
                "cross-vault-search-missing",
            )
            self.expect(
                reports["get"]["result"]["nodes"][0]["handle"]
                == "alpha:alpha-principle",
                "qualified-get-failed",
            )
            expanded = {item["handle"] for item in reports["expand"]["result"]["nodes"]}
            self.expect(
                {"alpha:alpha-principle", "beta:beta-principle"}.issubset(expanded),
                "cross-vault-expand-missing",
            )
            contextual = {item["handle"] for item in reports["context"]["result"]["nodes"]}
            self.expect(
                {"alpha:alpha-principle", "beta:beta-principle"}.issubset(contextual),
                "cross-vault-context-missing",
            )

        with self.step("packaged-api"):
            port = self._start_server()
            try:
                status_payload, status_headers = self._http_json(
                    port, "GET", "/api/v1/status", expected_status=200
                )
                generation = status_payload.get("generation")
                self.expect(
                    isinstance(generation, str)
                    and bool(re.fullmatch(r"[0-9a-f]{64}", generation)),
                    "api-generation-missing",
                )
                self.expect(
                    status_headers.get(GENERATION_HEADER.casefold()) == generation,
                    "api-generation-header-mismatch",
                )
                document_id = self.captures["alpha-md-v2"]["result"]["document_id"]
                routes = (
                    ("GET", "/api/v1/vaults", "vaults", None, None),
                    ("GET", "/api/v1/vaults/alpha/roots?include_stale=true", "roots", generation, None),
                    ("GET", "/api/v1/vaults/alpha/nodes/alpha-principle?include_stale=true", "node", generation, None),
                    ("GET", "/api/v1/vaults/alpha/nodes/alpha-principle/neighbors?include_stale=true", "neighbors", generation, None),
                    ("GET", "/api/v1/vaults/alpha/stale", "stale", generation, None),
                    ("GET", "/api/v1/vaults/alpha/sources/%s" % document_id, "source", generation, None),
                    ("GET", "/api/v1/vaults/alpha/sources/%s/versions" % document_id, "versions", generation, None),
                    ("GET", "/api/v1/vaults/alpha/sources/%s/diff" % document_id, "diff", generation, None),
                    ("GET", "/api/v1/vaults/alpha/sources/%s/excerpt?line=2&radius=1" % document_id, "excerpt", generation, None),
                    ("POST", "/api/v1/search", "search", generation, _recall_request("search", query="Shared Principle")),
                    ("POST", "/api/v1/context", "context", generation, _recall_request("context", handles=("alpha:alpha-principle", "beta:beta-principle"))),
                )
                observed_kinds = {status_payload["result"]["kind"]}
                for method, path, kind, selected_generation, request in routes:
                    payload, headers = self._http_json(
                        port,
                        method,
                        path,
                        expected_status=200,
                        generation=selected_generation,
                        request=request,
                    )
                    self.expect(payload.get("generation") == generation, "api-mixed-generation")
                    self.expect(payload["result"]["kind"] == kind, "api-wrong-route-kind")
                    self.expect(
                        headers.get(GENERATION_HEADER.casefold()) == generation,
                        "api-route-generation-header-mismatch",
                    )
                    observed_kinds.add(kind)
                self.expect(len(observed_kinds) == 12, "api-route-coverage-incomplete")
                stale_payload, stale_headers = self._http_json(
                    port,
                    "GET",
                    "/api/v1/vaults/alpha/roots",
                    expected_status=409,
                    generation="0" * 64,
                )
                self.expect(stale_payload.get("current_generation") == generation, "api-409-generation-missing")
                self.expect(
                    stale_headers.get(GENERATION_HEADER.casefold()) == generation,
                    "api-409-header-missing",
                )
                missing_payload, _ = self._http_json(
                    port,
                    "GET",
                    "/api/v1/vaults/alpha/roots",
                    expected_status=428,
                )
                self.expect(missing_payload.get("route") == "roots", "api-428-route-missing")

                index_status, index_headers, index_raw = self._http(port, "GET", "/")
                self.expect(index_status == 200, "frontend-index-failed")
                self.expect(index_headers.get("cache-control") == "no-store", "frontend-index-cache-policy")
                index_text = index_raw.decode("utf-8", errors="strict")
                references = sorted(set(re.findall(r'(?:src|href)="(/[^"]+)"', index_text)))
                self.expect(bool(references), "frontend-assets-missing")
                self.expect(
                    all(re.search(r"-[A-Za-z0-9_-]{8,}\.(?:js|css)$", item) for item in references),
                    "frontend-assets-not-fingerprinted",
                )
                for reference in references:
                    asset_status, asset_headers, asset_raw = self._http(port, "GET", reference)
                    self.expect(asset_status == 200 and bool(asset_raw), "frontend-asset-failed")
                    self.expect(
                        asset_headers.get("cache-control")
                        == "public, max-age=31536000, immutable",
                        "frontend-asset-cache-policy",
                    )
                    self.expect(
                        asset_headers.get("etag") == '"%s"' % _sha256_bytes(asset_raw),
                        "frontend-asset-etag-mismatch",
                    )
            finally:
                self.stop_server()

        with self.step("portable-snapshot"):
            snapshot_parent = Path(
                tempfile.mkdtemp(prefix="kgd-ms-store-")
            ).resolve()
            snapshot_path = snapshot_parent / "Vault"
            try:
                snapshot = self.cli(
                    "vault", "snapshot", "beta", "--output", str(snapshot_path)
                )
                verified = self.cli("vault", "verify", str(snapshot_path))
                self.expect(snapshot.get("status") == "verified", "vault-snapshot-failed")
                self.expect(snapshot.get("layout") == "snapshot-copy", "vault-snapshot-not-external")
                self.expect(verified.get("status") == "verified", "vault-verify-failed")
                self.expect(
                    snapshot.get("store_sha256") == verified.get("store_sha256"),
                    "vault-store-digest-mismatch",
                )
            finally:
                shutil.rmtree(snapshot_parent)
            in_place = self.cli("vault", "snapshot", "beta")
            in_place_verified = self.cli("vault", "verify", str(self.beta))
            self.expect(in_place.get("status") == "verified", "in-place-snapshot-failed")
            self.expect(in_place.get("layout") == "in-place", "in-place-snapshot-layout")
            self.expect(in_place_verified.get("status") == "verified", "in-place-verify-failed")
            self.expect(
                in_place.get("store_sha256") == in_place_verified.get("store_sha256"),
                "in-place-store-digest-mismatch",
            )
            self.beta_store_sha256 = str(in_place["store_sha256"])

        with self.step("vault-move"):
            removed = self.cli("vault", "remove", "beta")
            self.expect(removed.get("action") == "remove", "vault-remove-failed")
            self.expect(not self.beta_moved.exists(), "move-destination-exists")
            shutil.move(str(self.beta), str(self.beta_moved))
            added = self.cli("vault", "add", str(self.beta_moved))
            self.expect(added.get("action") == "add", "moved-vault-add-failed")
            self.expect(added["result"]["vault"]["id"] == "beta", "moved-vault-id-changed")
            moved_verified = self.cli("vault", "verify", str(self.beta_moved))
            self.expect(moved_verified.get("status") == "verified", "moved-vault-verify-failed")
            self.expect(
                moved_verified.get("store_sha256") == self.beta_store_sha256,
                "moved-vault-store-digest-changed",
            )
            doctor = self.cli("vault", "doctor", "beta")
            self.expect(doctor.get("status") == "ok", "moved-vault-doctor-failed")
            self.expect(doctor["result"]["counts"]["healthy"] == 1, "moved-vault-not-healthy")
            queried = self.cli(
                "recall", "get", "beta:beta-principle", "--include-stale"
            )
            self.expect(
                queried["result"]["nodes"][0]["handle"] == "beta:beta-principle",
                "moved-vault-query-failed",
            )

        with self.step("incomplete-vault"):
            self.expect(not self.alpha_offline.exists(), "offline-destination-exists")
            self.alpha.rename(self.alpha_offline)
            self.alpha_is_offline = True
            try:
                partial = self.cli("recall", "status")
                self.expect(partial.get("status") == "partial", "missing-vault-not-partial")
                self.expect(
                    [item["vault_id"] for item in partial["incomplete_vaults"]]
                    == ["alpha"],
                    "missing-vault-not-reported",
                )
                self.expect(
                    [item["vault_id"] for item in partial["vaults"]] == ["beta"],
                    "healthy-vault-lost-with-incomplete-peer",
                )
            finally:
                if self.alpha_is_offline and self.alpha_offline.exists():
                    self.alpha_offline.rename(self.alpha)
                    self.alpha_is_offline = False
            doctor = self.cli("vault", "doctor", "alpha")
            self.expect(doctor.get("status") == "ok", "restored-vault-not-healthy")

    def emergency_cleanup(self) -> None:
        server_error: Optional[Exception] = None
        try:
            self.stop_server()
        except Exception as error:
            server_error = error
        finally:
            if self.alpha_is_offline and self.alpha_offline.exists() and not self.alpha.exists():
                self.alpha_offline.rename(self.alpha)
                self.alpha_is_offline = False
        if server_error is not None:
            raise server_error


def _workspace_from_args(value: Optional[Path]) -> Tuple[Path, str, bool]:
    if value is None:
        return Path(tempfile.mkdtemp(prefix="kgdistiller-multivault-smoke-")).resolve(), "temporary", True
    selected = value.expanduser()
    if selected.is_symlink():
        raise SmokeFailure("workspace-not-directory")
    workspace = selected.resolve(strict=True)
    if not workspace.is_dir() or workspace == Path(workspace.anchor):
        raise SmokeFailure("workspace-not-directory")
    try:
        empty = next(workspace.iterdir(), None) is None
    except OSError as error:
        raise SmokeFailure("workspace-unreadable") from error
    if not empty:
        raise SmokeFailure("workspace-not-empty")
    return workspace, "supplied", False


def _cleanup_workspace(workspace: Path, *, remove_root: bool) -> None:
    if remove_root:
        shutil.rmtree(workspace)
        return
    for child in tuple(workspace.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _failure_code(error: Exception) -> str:
    if isinstance(error, SmokeFailure):
        return error.code
    if isinstance(error, subprocess.TimeoutExpired):
        return "subprocess-timeout"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, (UnicodeError, json.JSONDecodeError)):
        return "invalid-text"
    if isinstance(error, OSError):
        return "filesystem-error"
    return "unexpected-failure"


def _summary(
    *,
    status: str,
    workspace_mode: str,
    steps: Sequence[Mapping[str, Any]],
    cleanup: str,
    error: Optional[Mapping[str, str]],
) -> Dict[str, Any]:
    return {
        "schema": SUMMARY_SCHEMA,
        "status": status,
        "python_module_invocation": True,
        "workspace_mode": workspace_mode,
        "formats": ["markdown", "typst", "latex"],
        "vault_count": 2,
        "api_route_count": 12,
        "steps": [dict(item) for item in steps],
        "cleanup": cleanup,
        "error": None if error is None else dict(error),
    }


def _write_summary_output(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canonical_json(payload) + "\n").encode("utf-8"))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="use an existing empty directory instead of a temporary workspace",
    )
    parser.add_argument(
        "--keep-on-failure",
        action="store_true",
        help="preserve the disposable workspace only when the smoke fails",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="also write the closed JSON summary to this file",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    workspace: Optional[Path] = None
    workspace_mode = "supplied" if args.workspace is not None else "temporary"
    remove_root = False
    smoke: Optional[MultiVaultSmoke] = None
    status = "failed"
    cleanup = "not-started"
    error_payload: Optional[Dict[str, str]] = None
    steps: Sequence[Mapping[str, Any]] = ()
    try:
        workspace, workspace_mode, remove_root = _workspace_from_args(args.workspace)
        smoke = MultiVaultSmoke(workspace, workspace_mode=workspace_mode)
        smoke.run()
        status = "passed"
    except Exception as error:
        failed_step = "workspace"
        if smoke is not None:
            if smoke._active_step:
                failed_step = smoke._active_step
            elif smoke.steps and smoke.steps[-1].get("status") == "failed":
                failed_step = str(smoke.steps[-1]["name"])
        error_payload = {
            "step": failed_step,
            "code": _failure_code(error),
        }
    finally:
        if smoke is not None:
            steps = tuple(smoke.steps)
            try:
                smoke.emergency_cleanup()
            except Exception:
                status = "failed"
                error_payload = {"step": "cleanup", "code": "server-or-restore-cleanup-failed"}
        preserve = status == "failed" and bool(args.keep_on_failure)
        if workspace is not None and not preserve:
            try:
                _cleanup_workspace(workspace, remove_root=remove_root)
                cleanup = "complete"
            except Exception:
                status = "failed"
                cleanup = "failed"
                error_payload = {"step": "cleanup", "code": "workspace-cleanup-failed"}
        elif workspace is not None:
            cleanup = "preserved"
        else:
            cleanup = "not-applicable"

    summary = _summary(
        status=status,
        workspace_mode=workspace_mode,
        steps=steps,
        cleanup=cleanup,
        error=error_payload,
    )
    serialized = _canonical_json(summary)
    if workspace is not None and str(workspace).casefold() in serialized.casefold():
        summary = _summary(
            status="failed",
            workspace_mode=workspace_mode,
            steps=steps,
            cleanup=cleanup,
            error={"step": "summary", "code": "summary-contained-workspace-path"},
        )
        status = "failed"
    if args.json_output is not None:
        try:
            _write_summary_output(args.json_output, summary)
        except OSError:
            summary = _summary(
                status="failed",
                workspace_mode=workspace_mode,
                steps=steps,
                cleanup=cleanup,
                error={"step": "summary", "code": "json-output-failed"},
            )
            status = "failed"
    print(_canonical_json(summary))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
