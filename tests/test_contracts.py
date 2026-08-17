from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

from kgdistiller.contracts import (
    CONTRACT_SCHEMAS,
    ContractError,
    canonical_json,
    finalize_self_digest,
    load_contract_schema,
    parse_contract_json,
    self_digest,
    sha256_json,
    validate_contract,
)
from kgdistiller.json_schema import validate_json_schema


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
FIXTURE_CONTRACTS = (
    "kgdistiller-retrieval-plan-v1",
    "kgdistiller-search-result-v1",
    "kgdistiller-search-execution-v1",
    "kgdistiller-document-record-v1",
    "kgdistiller-static-export-v1",
    "kgdistiller-site-graph-v1",
)


def fixture(schema: str, group: str = "valid") -> dict:
    return parse_contract_json(
        (FIXTURES / group / f"{schema}.json").read_text(encoding="utf-8")
    )


def minimal_store() -> dict:
    digest = "a" * 64
    return finalize_self_digest(
        {
            "schema": "kgdistiller-store-v1",
            "generator": "kgdistiller",
            "layout": "in-place",
            "paths": {
                "vault": "knowledge/vault.json",
                "registry": "knowledge/sources.json",
                "identities": None,
                "alignments": "knowledge/alignments.json",
                "graph": "knowledge/graph",
                "documents": "knowledge/documents.jsonl",
            },
            "documents": {
                "count": 0,
                "sha256": digest,
                "source_snapshot_sha256": digest,
            },
            "graph_artifacts": [
                {
                    "path": "knowledge/graph/manifest.json",
                    "bytes": 1,
                    "sha256": digest,
                },
                {"path": "knowledge/graph/nodes.jsonl", "bytes": 0, "sha256": digest},
                {"path": "knowledge/graph/edges.jsonl", "bytes": 0, "sha256": digest},
                {"path": "knowledge/graph/references.jsonl", "bytes": 0, "sha256": digest},
                {"path": "knowledge/graph/diagnostics.json", "bytes": 1, "sha256": digest},
            ],
            "vault_id": "00000000-0000-4000-8000-000000000000",
            "vault_sha256": digest,
            "registry_sha256": digest,
            "identity_sha256": None,
            "alignment_sha256": digest,
            "graph_sha256": digest,
            "store_generation_sha256": digest,
            "managed_paths": ["knowledge/documents.jsonl", "knowledge/store.json"],
        },
        "store_sha256",
    )


def minimal_query_status() -> dict:
    digest = "c" * 64
    return {
        "schema": "kgdistiller-query-status-v1",
        "snapshot_schema": "kgdistiller-agent-snapshot-v1",
        "namespace": "personal",
        "snapshot_sha256": digest,
        "graph_schema": "kgdistiller-graph-v1",
        "graph_sha256": digest,
        "generation": digest,
        "counts": {"nodes": 0, "edges": 0, "references": 0},
        "backend": "json-memory",
        "retrieval_lanes": ["identity", "lexical", "graph", "ppr"],
        "capabilities": ["json-memory", "read-only-query-v3"],
        "alignment_schema": "kgdistiller-alignments-v1",
        "alignment_sha256": digest,
        "alignment_counts": {"mappings": 0},
    }


def minimal_obsidian() -> dict:
    digest = "b" * 64
    return finalize_self_digest(
        {
            "schema": "kgdistiller-obsidian-projection-v1",
            "status": "ready",
            "source": {
                "graph_schema": "kgdistiller-graph-v1",
                "graph_sha256": digest,
                "snapshot_sha256": digest,
                "source_hashes_sha256": digest,
            },
            "policy": {
                "nodes": "active-knowledge",
                "edges": "current-semantic",
                "edge_semantics_in_obsidian_graph": "lossy",
                "authority_links": "vault-relative",
            },
            "counts": {"concepts": 0, "sources": 0, "links": 0},
            "artifacts": [],
        },
        "projection_sha256",
    )


def minimal_store_report() -> dict:
    digest = "d" * 64
    return {
        "schema": "kgdistiller-store-report-v1",
        "status": "verified",
        "artifact_schema": "kgdistiller-store-v1",
        "root": "/tmp/store",
        "store_sha256": digest,
        "store_generation_sha256": digest,
        "graph_sha256": digest,
        "documents": 0,
        "counts": {"nodes": 0, "edges": 0, "references": 0},
        "query_backend": "json-memory",
        "layout": "snapshot-copy",
    }


def minimal_obsidian_report() -> dict:
    digest = "e" * 64
    return {
        "schema": "kgdistiller-obsidian-export-report-v1",
        "status": "verified",
        "artifact_schema": "kgdistiller-obsidian-projection-v1",
        "projection_sha256": digest,
        "source": {
            "graph_schema": "kgdistiller-graph-v1",
            "graph_sha256": digest,
            "snapshot_sha256": digest,
            "source_hashes_sha256": digest,
        },
        "policy": {
            "nodes": "active-knowledge",
            "edges": "current-semantic",
            "edge_semantics_in_obsidian_graph": "lossy",
            "authority_links": "vault-relative",
        },
        "counts": {"concepts": 0, "sources": 0, "links": 0},
        "output": "/tmp/obsidian",
        "changed": False,
    }


def minimal_static_report() -> dict:
    digest = "f" * 64
    counts = {"nodes": 0, "edges": 0, "references": 0}
    return {
        "schema": "kgdistiller-static-export-report-v1",
        "status": "exported",
        "artifact_schema": "kgdistiller-static-export-v1",
        "committed": True,
        "cleanup_status": "complete",
        "warnings": [],
        "recovery_paths": [],
        "output": "/tmp/site",
        "export_sha256": digest,
        "producer": {
            "name": "kgdistiller",
            "repository": "https://github.com/example/kgdistiller",
            "version": "0.4.0",
            "commit": "a" * 40,
        },
        "source": {
            "repository": "https://github.com/example/notes",
            "revision": "b" * 40,
            "digest": digest,
            "published_digest": digest,
        },
        "graph": {
            "private_schema": "kgdistiller-graph-v1",
            "private_sha256": digest,
            "private_counts": counts,
            "public_schema": "kgdistiller-site-graph-v1",
            "public_sha256": digest,
            "public_counts": counts,
        },
        "visibility": {
            "policy": "explicit-publish",
            "published_sources": [],
            "excluded_sources": 0,
        },
        "replaced": False,
        "replaces_export_sha256": None,
    }


class ContractTest(unittest.TestCase):
    def test_current_contract_catalog_is_packaged(self) -> None:
        self.assertEqual(
            {
                "kgdistiller-retrieval-plan-v1",
                "kgdistiller-query-status-v1",
                "kgdistiller-search-result-v1",
                "kgdistiller-search-execution-v1",
                "kgdistiller-document-record-v1",
                "kgdistiller-store-v1",
                "kgdistiller-store-report-v1",
                "kgdistiller-obsidian-projection-v1",
                "kgdistiller-obsidian-export-report-v1",
                "kgdistiller-static-export-v1",
                "kgdistiller-static-export-report-v1",
                "kgdistiller-site-graph-v1",
            },
            set(CONTRACT_SCHEMAS),
        )
        for discriminator, filename in CONTRACT_SCHEMAS.items():
            with self.subTest(schema=discriminator):
                schema = load_contract_schema(discriminator)
                self.assertEqual(discriminator, schema["properties"]["schema"]["const"])
                self.assertTrue(resources.files("kgdistiller").joinpath("schemas", filename).is_file())

    def test_current_valid_contracts_pass(self) -> None:
        payloads = [fixture(name) for name in FIXTURE_CONTRACTS]
        payloads.extend(
            [
                minimal_query_status(),
                minimal_store(),
                minimal_store_report(),
                minimal_obsidian(),
                minimal_obsidian_report(),
                minimal_static_report(),
            ]
        )
        for payload in payloads:
            with self.subTest(schema=payload["schema"]):
                self.assertEqual(payload, validate_contract(payload))

    def test_current_invalid_fixtures_fail_closed(self) -> None:
        for discriminator in FIXTURE_CONTRACTS:
            with self.subTest(schema=discriminator):
                with self.assertRaises(ContractError):
                    validate_contract(fixture(discriminator, "invalid"))

    def test_removed_runtime_contracts_are_explicitly_unsupported(self) -> None:
        for discriminator in (
            "legacy-local-profile-v0",
            "legacy-embedding-policy-v0",
            "legacy-store-v0",
            "legacy-retrieval-plan-v0",
            "legacy-static-export-v0",
            "legacy-document-upsert-request-v0",
            "legacy-document-ingest-receipt-v0",
            "legacy-document-record-v0",
        ):
            with self.subTest(schema=discriminator):
                with self.assertRaisesRegex(ContractError, "unsupported contract schema"):
                    validate_contract({"schema": discriminator})

    def test_current_wrapper_contracts_reject_unknown_graph_schema(self) -> None:
        cases = [
            (minimal_query_status(), ("graph_schema",)),
            (minimal_obsidian(), ("source", "graph_schema")),
            (minimal_obsidian_report(), ("source", "graph_schema")),
            (minimal_static_report(), ("graph", "private_schema")),
            (fixture("kgdistiller-static-export-v1"), ("graph", "private_schema")),
        ]
        for payload, path in cases:
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = "legacy-graph-v0"
            with self.subTest(schema=payload["schema"]):
                with self.assertRaises(ContractError):
                    validate_contract(payload)

    def test_store_and_obsidian_self_digests_detect_tampering(self) -> None:
        for payload, field in (
            (minimal_store(), "store_sha256"),
            (minimal_obsidian(), "projection_sha256"),
        ):
            payload["status" if "status" in payload else "generator"] = "tampered"
            with self.subTest(schema=payload["schema"]):
                with self.assertRaises(ContractError):
                    validate_contract(payload)
            self.assertNotEqual(payload[field], self_digest(payload, field))

    def test_search_execution_identity_indices_are_contiguous(self) -> None:
        payload = fixture("kgdistiller-search-execution-v1")
        payload["identity_resolutions"][1]["query_index"] = 3
        with self.assertRaisesRegex(ContractError, "unique and contiguous"):
            validate_contract(payload)

    def test_nested_search_result_is_validated_by_its_own_contract(self) -> None:
        execution = validate_contract(fixture("kgdistiller-search-execution-v1"))
        self.assertEqual(execution["result"], validate_contract(execution["result"]))
        invalid = copy.deepcopy(execution["result"])
        invalid["lanes"]["semantic"] = {
            "status": "enabled",
            "queries": 1,
            "results": 1,
        }
        with self.assertRaisesRegex(ContractError, "unknown property"):
            validate_contract(invalid)
        execution["result"] = invalid
        with self.assertRaisesRegex(ContractError, "unknown property"):
            validate_contract(execution)

    def test_search_result_seed_counts_obey_the_runtime_limit(self) -> None:
        result = fixture("kgdistiller-search-result-v1")
        result["lanes"]["ppr"]["seeds"] = 129
        with self.assertRaisesRegex(ContractError, "must be <= 128"):
            validate_contract(result)

    def test_document_normalization_and_public_edge_privacy_are_enforced(self) -> None:
        document = fixture("kgdistiller-document-record-v1")
        document["format"] = "typst"
        with self.assertRaisesRegex(ContractError, "authority extension"):
            validate_contract(document)

        graph = fixture("kgdistiller-site-graph-v1")
        graph["nodes"] = [
            {"id": "a", "type": "knowledge", "label": "A"},
            {"id": "b", "type": "knowledge", "label": "B"},
        ]
        graph["edges"] = [
            {
                "source": "a",
                "relation": "derived-from",
                "target": "b",
                "evidence": "must remain private",
            }
        ]
        graph["counts"] = {"nodes": 2, "edges": 1, "references": 0}
        graph = finalize_self_digest(graph, "graph_sha256")
        with self.assertRaisesRegex(ContractError, "unknown property"):
            validate_contract(graph)

    def test_schema_loading_and_json_parsing_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("kgdistiller.contracts.resources.files", return_value=Path(temporary)):
                with self.assertRaisesRegex(ContractError, "unavailable"):
                    load_contract_schema("kgdistiller-store-v1")
        with self.assertRaisesRegex(ContractError, "malformed contract JSON"):
            parse_contract_json("{")
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaisesRegex(ContractError, "non-finite"):
                parse_contract_json(f'{{"value":{constant}}}')

    def test_canonical_json_is_unicode_stable_and_finite(self) -> None:
        first = {"b": [2, 1], "a": "é"}
        second = {"a": "é", "b": [2, 1]}
        self.assertEqual('{"a":"é","b":[2,1]}', canonical_json(first))
        self.assertEqual(sha256_json(first), sha256_json(second))
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ContractError, "not finite"):
                canonical_json({"value": value})

    def test_json_schema_references_remain_local(self) -> None:
        for reference, message in (
            ("#/$defs/missing", "unresolved JSON Schema reference"),
            ("https://example.invalid/remote", "unsupported non-local"),
        ):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, message):
                    validate_json_schema({}, {"$ref": reference})


if __name__ == "__main__":
    unittest.main()
