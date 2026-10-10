from __future__ import annotations

import copy
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
    load_contract_schema,
    parse_contract_json,
    validate_contract,
)
from kgdistiller.json_schema import validate_json_schema

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
FIXTURE_CONTRACTS = (
    "kgdistiller-retrieval-plan-v1",
    "kgdistiller-search-result-v1",
    "kgdistiller-search-execution-v1",
)


def fixture(schema: str, group: str = "valid") -> dict:
    return parse_contract_json(
        (FIXTURES / group / f"{schema}.json").read_text(encoding="utf-8")
    )


def minimal_query_status() -> dict:
    return {
        "schema": "kgdistiller-query-status-v1",
        "counts": {"entries": 2, "edges": 1},
        "relations": {"prerequisite-for": 1},
    }


def minimal_obsidian_graph() -> dict:
    return {
        "schema": "kgdistiller-obsidian-graph-v1",
        "counts": {"concepts": 1, "sources": 1, "semantic_edges": 0, "definitions": 1},
        "concepts": [
            {
                "id": "measure",
                "label": "Measure",
                "kind": "definition",
                "aliases": ["测度"],
                "authority": ".knowledge/entries/measure.md",
                "understanding": "unknown",
            }
        ],
        "sources": [{"authority": "notes/chapter.txt"}],
        "semantic_edges": [],
        "definitions": [
            {"source_authority": "notes/chapter.txt", "target": "measure", "line_start": 1, "line_end": 3}
        ],
    }


class ContractTest(unittest.TestCase):
    def test_current_contract_catalog_is_packaged(self) -> None:
        self.assertEqual(
            {
                "kgdistiller-retrieval-plan-v1",
                "kgdistiller-query-status-v1",
                "kgdistiller-search-result-v1",
                "kgdistiller-search-execution-v1",
                "kgdistiller-search-result-v2",
                "kgdistiller-search-execution-v2",
                "kgdistiller-search-result-v3",
                "kgdistiller-search-execution-v3",
                "kgdistiller-context-bundle-v2",
                "kgdistiller-obsidian-graph-v1",
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
                minimal_obsidian_graph(),
            ]
        )
        for payload in payloads:
            with self.subTest(schema=payload["schema"]):
                self.assertEqual(payload, validate_contract(payload))

    def test_current_invalid_fixtures_fail_closed(self) -> None:
        for discriminator in FIXTURE_CONTRACTS:
            with (
                self.subTest(schema=discriminator),
                self.assertRaises(ContractError),
            ):
                validate_contract(fixture(discriminator, "invalid"))

    def test_unknown_contracts_are_explicitly_unsupported(self) -> None:
        for discriminator in ("kgdistiller-unknown-v1", "kgdistiller-entry-v2", "unexpected-v0"):
            with (
                self.subTest(schema=discriminator),
                self.assertRaisesRegex(ContractError, "unsupported contract schema"),
            ):
                validate_contract({"schema": discriminator})

    def test_wrapper_contracts_reject_unknown_fields(self) -> None:
        for payload in (minimal_query_status(), minimal_obsidian_graph()):
            payload["unexpected"] = "value"
            with self.subTest(schema=payload["schema"]), self.assertRaises(ContractError):
                validate_contract(payload)
        status = minimal_query_status()
        status["relations"]["unexpected-relation"] = 1
        with self.assertRaises(ContractError):
            validate_contract(status)

    def test_obsidian_graph_requires_closed_endpoints_and_exact_counts(self) -> None:
        payload = minimal_obsidian_graph()
        payload["definitions"][0]["target"] = "unknown"
        with self.assertRaisesRegex(ContractError, "unknown endpoint"):
            validate_contract(payload)

        payload = minimal_obsidian_graph()
        payload["counts"]["definitions"] = 0
        with self.assertRaisesRegex(ContractError, "counts do not match"):
            validate_contract(payload)

        payload = minimal_obsidian_graph()
        payload["definitions"][0]["line_end"] = 0
        with self.assertRaises(ContractError):
            validate_contract(payload)

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

    def test_schema_loading_and_json_parsing_fail_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("kgdistiller.contracts.resources.files", return_value=Path(temporary)),
            self.assertRaisesRegex(ContractError, "unavailable"),
        ):
            load_contract_schema("kgdistiller-retrieval-plan-v1")
        with self.assertRaisesRegex(ContractError, "malformed contract JSON"):
            parse_contract_json("{")
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaisesRegex(ContractError, "non-finite"):
                parse_contract_json(f'{{"value":{constant}}}')

    def test_canonical_json_is_unicode_stable_and_finite(self) -> None:
        first = {"b": [2, 1], "a": "é"}
        second = {"a": "é", "b": [2, 1]}
        self.assertEqual('{"a":"é","b":[2,1]}', canonical_json(first))
        self.assertEqual(canonical_json(first), canonical_json(second))
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ContractError, "not finite"):
                canonical_json({"value": value})

    def test_json_schema_references_remain_local(self) -> None:
        for reference, message in (
            ("#/$defs/missing", "unresolved JSON Schema reference"),
            ("https://example.invalid/remote", "unsupported non-local"),
        ):
            with (
                self.subTest(reference=reference),
                self.assertRaisesRegex(ValueError, message),
            ):
                validate_json_schema({}, {"$ref": reference})


if __name__ == "__main__":
    unittest.main()
