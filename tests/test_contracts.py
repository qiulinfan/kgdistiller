from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from importlib import resources
from pathlib import Path
from typing import Optional
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


def load_fixture(group: str, schema: str) -> dict:
    return parse_contract_json(
        (FIXTURES / group / f"{schema}.json").read_text(encoding="utf-8")
    )


class ContractResourceTests(unittest.TestCase):
    def test_all_new_schemas_load_from_package_resources(self) -> None:
        self.assertEqual(7, len(CONTRACT_SCHEMAS))
        for discriminator, filename in CONTRACT_SCHEMAS.items():
            with self.subTest(schema=discriminator):
                schema = load_contract_schema(discriminator)
                self.assertEqual(
                    discriminator, schema["properties"]["schema"]["const"]
                )
                packaged = resources.files("kgdistiller").joinpath("schemas", filename)
                self.assertTrue(packaged.is_file())

    def test_valid_fixtures_validate_through_packaged_loader(self) -> None:
        for discriminator in CONTRACT_SCHEMAS:
            with self.subTest(schema=discriminator):
                payload = load_fixture("valid", discriminator)
                self.assertEqual(payload, validate_contract(payload))

    def test_invalid_fixture_for_every_contract_fails_closed(self) -> None:
        for discriminator in CONTRACT_SCHEMAS:
            with self.subTest(schema=discriminator):
                payload = load_fixture("invalid", discriminator)
                with self.assertRaises(ContractError):
                    validate_contract(payload)

    def test_missing_packaged_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch(
                "kgdistiller.contracts.resources.files",
                return_value=Path(temporary),
            ):
                with self.assertRaisesRegex(
                    ContractError, "packaged contract schema is unavailable"
                ):
                    load_contract_schema("qlkg-local-profile-v1")

    def test_malformed_packaged_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            schema_dir = Path(temporary) / "schemas"
            schema_dir.mkdir()
            (schema_dir / "qlkg-local-profile-v1.schema.json").write_text(
                "{", encoding="utf-8"
            )
            with mock.patch(
                "kgdistiller.contracts.resources.files",
                return_value=Path(temporary),
            ):
                with self.assertRaisesRegex(ContractError, "malformed contract JSON"):
                    load_contract_schema("qlkg-local-profile-v1")

    def test_malformed_fixture_json_and_nonfinite_constants_fail_closed(self) -> None:
        with self.assertRaisesRegex(ContractError, "malformed contract JSON"):
            parse_contract_json("{")
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(ContractError, "non-finite"):
                    parse_contract_json(f'{{"value":{constant}}}')

    def test_unresolved_and_nonlocal_references_fail_closed(self) -> None:
        for reference, message in (
            ("#/$defs/missing", "unresolved JSON Schema reference"),
            ("https://example.invalid/remote", "unsupported non-local"),
        ):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, message):
                    validate_json_schema({}, {"$ref": reference})


class CanonicalDigestTests(unittest.TestCase):
    def test_canonical_json_is_unicode_compact_and_order_stable(self) -> None:
        first = {"b": [2, 1], "a": "é"}
        second = {"a": "é", "b": [2, 1]}
        expected = '{"a":"é","b":[2,1]}'
        self.assertEqual(expected, canonical_json(first))
        self.assertEqual(expected, canonical_json(second))
        self.assertEqual(sha256_json(first), sha256_json(second))
        self.assertEqual([sha256_json(first)] * 10, [sha256_json(first) for _ in range(10)])

    def test_canonical_json_rejects_nonfinite_numbers(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "not finite canonical JSON"):
                    canonical_json({"value": value})

    def test_self_digest_omits_only_its_own_field(self) -> None:
        payload = {"schema": "fixture", "value": "stable", "digest": "0" * 64}
        finalized = finalize_self_digest(payload, "digest")
        self.assertEqual(self_digest(finalized, "digest"), finalized["digest"])
        reordered = {"value": "stable", "digest": finalized["digest"], "schema": "fixture"}
        self.assertEqual(finalized["digest"], self_digest(reordered, "digest"))
        tampered = copy.deepcopy(finalized)
        tampered["value"] = "changed"
        self.assertNotEqual(finalized["digest"], self_digest(tampered, "digest"))

    def test_request_and_receipt_tampering_is_rejected(self) -> None:
        for discriminator, path in (
            ("qlkg-document-upsert-request-v1", ("source", "source_sha256")),
            ("qlkg-document-ingest-receipt-v1", ("warnings",)),
        ):
            payload = load_fixture("valid", discriminator)
            if len(path) == 2:
                payload[path[0]][path[1]] = "f" * 64
            else:
                payload[path[0]].append("tampered")
            with self.subTest(schema=discriminator):
                with self.assertRaisesRegex(ContractError, "does not match canonical"):
                    validate_contract(payload)


class ContractNegativeTests(unittest.TestCase):
    def assert_invalid(self, payload: dict, message: Optional[str] = None) -> None:
        context = self.assertRaisesRegex(ContractError, message) if message else self.assertRaises(ContractError)
        with context:
            validate_contract(payload)

    def test_missing_unknown_and_wrong_discriminator_are_rejected(self) -> None:
        profile = load_fixture("valid", "qlkg-local-profile-v1")
        del profile["database"]
        self.assert_invalid(profile, "missing required property")

        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["unknown"] = True
        self.assert_invalid(profile, "unknown property")

        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["schema"] = "qlkg-local-profile-v2"
        self.assert_invalid(profile, "unsupported contract schema")

    def test_invalid_digest_syntax_enums_formats_dimensions_and_coverage(self) -> None:
        cases = []
        search = load_fixture("valid", "qlkg-search-result-v2")
        search["plan_sha256"] = "ABC"
        cases.append(search)

        search = load_fixture("valid", "qlkg-search-result-v2")
        search["lanes"]["semantic"]["status"] = "silently-off"
        cases.append(search)

        document = load_fixture("valid", "qlkg-document-record-v2")
        document["format"] = "pdf"
        cases.append(document)

        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["provider_profiles"]["primary"]["dimensions"] = 0
        cases.append(profile)

        policy = load_fixture("valid", "qlkg-embedding-policy-v1")
        policy["profiles"][0]["minimum_coverage"] = -0.01
        cases.append(policy)

        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                self.assert_invalid(payload)

    def test_search_path_evidence_rejects_unknown_edge_type(self) -> None:
        search = load_fixture("valid", "qlkg-search-result-v2")
        search["results"][0]["path_evidence"][0]["edge_types"][0] = "not-a-relation"
        self.assert_invalid(search, "must be one of")

    def test_matching_evidence_values_are_kind_specific_and_bounded(self) -> None:
        cases = (
            ("explicit-document-id", "not-a-document-id"),
            ("authority", "notes/../escaped.typ"),
            ("content-sha256", "not-a-digest"),
            ("external-id", "x" * 1025),
        )
        for kind, value in cases:
            request = load_fixture("valid", "qlkg-document-upsert-request-v1")
            request["document"]["matching_evidence"] = [
                {"kind": kind, "value": value}
            ]
            request = finalize_self_digest(request, "request_sha256")
            with self.subTest(kind=kind):
                self.assert_invalid(request, "must match exactly one oneOf branch")

        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["document"]["matching_evidence"] = [
            {"kind": "external-id", "value": "doi:10.1234/synthetic"}
        ]
        request = finalize_self_digest(request, "request_sha256")
        self.assertEqual(request, validate_contract(request))

    def test_secret_bearing_fields_and_urls_are_rejected(self) -> None:
        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["provider_profiles"]["primary"]["api_key"] = "secret"
        self.assert_invalid(profile, "unknown property")

        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["provider_profiles"]["primary"]["credential_env"] = "sk-secret"
        self.assert_invalid(profile, "must match pattern")

        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["provider_profiles"]["primary"]["base_url"] = (
            "https://user:secret@example.invalid/v1"
        )
        self.assert_invalid(profile, "must match pattern")

        policy = load_fixture("valid", "qlkg-embedding-policy-v1")
        policy["profiles"][0]["credential_env"] = "SECRET_ENV"
        self.assert_invalid(policy, "unknown property")

    def test_absolute_portable_paths_and_escape_representations_are_rejected(self) -> None:
        document = load_fixture("valid", "qlkg-document-record-v2")
        document["authority"] = "/private/synthetic.typ"
        self.assert_invalid(document, "must match pattern")

        for escape in (
            "notes/../private.typ",
            "notes/link/../../private.typ",
            "notes\\..\\private.typ",
            "file:///private/synthetic.typ",
        ):
            request = load_fixture("valid", "qlkg-document-upsert-request-v1")
            request["source"]["authority"] = escape
            request = finalize_self_digest(request, "request_sha256")
            with self.subTest(escape=escape):
                self.assert_invalid(request, "must match pattern")

        policy = load_fixture("valid", "qlkg-embedding-policy-v1")
        policy["portable_store"] = "/private/store"
        self.assert_invalid(policy, "unknown property")

    def test_forbidden_embedded_payloads_are_rejected(self) -> None:
        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["vector_bytes"] = [0, 1, 2]
        self.assert_invalid(receipt, "unknown property")

        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["raw_prose"] = "Unreviewed discovery input is forbidden."
        request = finalize_self_digest(request, "request_sha256")
        self.assert_invalid(request, "unknown property")

        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["reviewed"]["review"]["status"] = "proposed"
        request = finalize_self_digest(request, "request_sha256")
        self.assert_invalid(request, "must equal 'reviewed'")

    def test_nonfinite_scores_fail_schema_validation(self) -> None:
        result = load_fixture("valid", "qlkg-search-result-v2")
        result["results"][0]["fusion"]["score"] = math.nan
        self.assert_invalid(result, "must have type number")

    def test_over_limit_arrays_and_strings_are_rejected(self) -> None:
        plan = load_fixture("valid", "qlkg-retrieval-plan-v1")
        plan["question"] = "q" * 8193
        self.assert_invalid(plan, "at most 8192")

        plan = load_fixture("valid", "qlkg-retrieval-plan-v1")
        plan["identity_queries"] = [f"query-{index}" for index in range(33)]
        self.assert_invalid(plan, "at most 32")

        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["warnings"] = [f"warning-{index}" for index in range(65)]
        receipt = finalize_self_digest(receipt, "receipt_sha256")
        self.assert_invalid(receipt, "at most 64")

    def test_duplicate_policy_profile_names_are_rejected(self) -> None:
        policy = load_fixture("valid", "qlkg-embedding-policy-v1")
        duplicate = copy.deepcopy(policy["profiles"][0])
        duplicate["model"] = "different-space"
        policy["profiles"].append(duplicate)
        self.assert_invalid(policy, "profile names must be unique")

    def test_local_profile_selection_and_document_normalization_are_checked(self) -> None:
        profile = load_fixture("valid", "qlkg-local-profile-v1")
        profile["embedding_profile"] = "missing"
        self.assert_invalid(profile, "must name a provider_profiles entry")

        document = load_fixture("valid", "qlkg-document-record-v2")
        document["authority_history"].append(document["authority"])
        self.assert_invalid(document, "must not repeat")

        document = load_fixture("valid", "qlkg-document-record-v2")
        document["external_ids"]["doi"] = "10.1234/SYNTHETIC"
        self.assert_invalid(document, "lowercase normalized")

    def test_upsert_authority_must_match_bounded_registered_glob(self) -> None:
        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["source"]["authority"] = "research/synthetic.typ"
        request = finalize_self_digest(request, "request_sha256")
        self.assert_invalid(request, "must match its registered bounded glob")

        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["source"]["registered_glob"] = "**/*.typ"
        request = finalize_self_digest(request, "request_sha256")
        self.assert_invalid(request, "bounded relative prefix")

        request = load_fixture("valid", "qlkg-document-upsert-request-v1")
        request["preconditions"]["query_sha256"] = "f" * 64
        request = finalize_self_digest(request, "request_sha256")
        self.assert_invalid(request, "query artifact digest")

    def test_malformed_registered_glob_fails_with_contract_error(self) -> None:
        for malformed_glob in (
            "notes/[z-a].typ",
            "notes/[.typ",
            "notes/[].typ",
            "notes/[!].typ",
            "notes/[^].typ",
        ):
            with self.subTest(registered_glob=malformed_glob):
                request = load_fixture("valid", "qlkg-document-upsert-request-v1")
                request["source"]["registered_glob"] = malformed_glob
                request = finalize_self_digest(request, "request_sha256")
                self.assert_invalid(request, "registered_glob is malformed")

    def test_disabled_and_degraded_lanes_require_reason_codes(self) -> None:
        result = load_fixture("valid", "qlkg-search-result-v2")
        del result["lanes"]["semantic"]["reason"]
        self.assert_invalid(result, "missing required property 'reason'")


class ReceiptInvariantTests(unittest.TestCase):
    def test_ready_requires_every_stage_ready(self) -> None:
        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["stages"]["embeddings"] = {
            "status": "provider-failed",
            "ready": 0,
            "missing": 2,
            "reason": "provider-unavailable",
        }
        receipt = finalize_self_digest(receipt, "receipt_sha256")
        with self.assertRaisesRegex(ContractError, "ready receipt"):
            validate_contract(receipt)

    def test_graph_committed_failure_is_degraded_not_failed(self) -> None:
        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["overall_status"] = "failed"
        receipt["git_ready"] = False
        receipt = finalize_self_digest(receipt, "receipt_sha256")
        with self.assertRaisesRegex(ContractError, "post-commit failure"):
            validate_contract(receipt)

    def test_degraded_post_commit_receipt_is_representable(self) -> None:
        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["overall_status"] = "degraded"
        receipt["stages"]["embeddings"] = {
            "status": "provider-failed",
            "ready": 0,
            "missing": 2,
            "reason": "provider-unavailable",
        }
        receipt["stages"]["portable"] = {"status": "pending"}
        receipt["stages"]["materialization"] = {"status": "pending"}
        receipt["git_ready"] = False
        receipt["warnings"] = ["Embedding enrichment is pending."]
        receipt = finalize_self_digest(receipt, "receipt_sha256")
        self.assertEqual("degraded", validate_contract(receipt)["overall_status"])

    def test_pending_receipt_is_representable_and_not_git_ready(self) -> None:
        receipt = load_fixture("valid", "qlkg-document-ingest-receipt-v1")
        receipt["overall_status"] = "pending"
        receipt["document"] = {"document_id": "doc:synthetic-note", "operation": "update"}
        receipt["stages"] = {
            "authority_graph": {"status": "pending"},
            "embeddings": {"status": "pending", "ready": 0, "missing": 0, "reason": "authority-pending"},
            "portable": {"status": "pending"},
            "materialization": {"status": "pending"},
        }
        receipt["git_ready"] = False
        receipt = finalize_self_digest(receipt, "receipt_sha256")
        self.assertEqual("pending", validate_contract(receipt)["overall_status"])


class CompatibilityTests(unittest.TestCase):
    def test_published_v1_fixtures_remain_readable(self) -> None:
        for path in sorted((FIXTURES / "compatibility").glob("*.json")):
            with self.subTest(fixture=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                schema_path = resources.files("kgdistiller").joinpath(
                    "schemas", f"{payload['schema']}.schema.json"
                )
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertEqual([], validate_json_schema(payload, schema))

    def test_document_v1_and_v2_are_isolated(self) -> None:
        v1 = load_fixture("compatibility", "qlkg-document-record-v1")
        v2 = load_fixture("valid", "qlkg-document-record-v2")
        v1_schema = json.loads(
            resources.files("kgdistiller")
            .joinpath("schemas", "qlkg-document-record-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        v2_schema = load_contract_schema("qlkg-document-record-v2")
        self.assertTrue(validate_json_schema(v2, v1_schema))
        self.assertTrue(validate_json_schema(v1, v2_schema))
        self.assertNotIn("document_id", v1_schema["required"])
        self.assertIn("document_id", v2_schema["required"])

    def test_unknown_future_schema_fails_explicitly(self) -> None:
        future = load_fixture("valid", "qlkg-document-record-v2")
        future["schema"] = "qlkg-document-record-v3"
        with self.assertRaisesRegex(ContractError, "unsupported contract schema"):
            validate_contract(future)

    def test_markdown_typst_and_latex_share_document_contracts(self) -> None:
        variants = (("md", "md"), ("typ", "typ"), ("tex", "tex"))
        for format_name, extension in variants:
            with self.subTest(format=format_name):
                document = load_fixture("valid", "qlkg-document-record-v2")
                document["format"] = format_name
                document["authority"] = f"notes/synthetic.{extension}"
                validate_contract(document)

                request = load_fixture("valid", "qlkg-document-upsert-request-v1")
                request["source"]["format"] = format_name
                request["source"]["authority"] = f"notes/synthetic.{extension}"
                request["source"]["registered_glob"] = f"notes/**/*.{extension}"
                request = finalize_self_digest(request, "request_sha256")
                validate_contract(request)


if __name__ == "__main__":
    unittest.main()
