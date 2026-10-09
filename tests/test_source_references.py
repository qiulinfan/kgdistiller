from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import self_digest, sha256_json
from kgdistiller.source_evidence import SourceEvidenceError, SourceEvidenceIndex
from kgdistiller.source_references import (
    resolve_source_references,
    validate_source_reference_result,
)


class SourceReferencesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="kgdistiller-source-references-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "manifest.json"

    def make_index(self, versions: dict[str, str] | None = None) -> SourceEvidenceIndex:
        versions = versions or {"adam": "1412.6980v8", "rmsnorm": "1910.07467v1", "roformer": "2104.09864v5"}
        documents = []
        for ordinal, (doc_id, version) in enumerate(versions.items()):
            path = self.root / (str(ordinal) + ".txt")
            raw = b"# Unrelated title\n\nNothing here defines a document alias.\n"
            path.write_bytes(raw)
            documents.append({"doc_id": doc_id, "path": path.name,
                              "source_url": "https://example.org/" + doc_id,
                              "source_version": version, "source_type": "paper-text",
                              "expected_sha256": hashlib.sha256(raw).hexdigest()})
        manifest = {"schema": "kgdistiller-source-evidence-manifest-v1", "root": str(self.root),
                    "hash_mode": "raw-utf8", "documents": documents}
        self.path.write_text(json.dumps(manifest))
        return SourceEvidenceIndex.from_manifest(self.path)

    def redigest(self, result):
        result["result_sha256"] = self_digest(result, "result_sha256")
        return result

    def test_doc_ids_qualified_versions_and_full_arxiv_versions(self) -> None:
        index = self.make_index()
        hints = ["Adam", "RMSNorm v1", "RoFormer-v5", "Adam@v8", "1412.6980v8",
                 "rms_norm@1910.07467v1", "adam @ v8"]
        result = resolve_source_references(index, hints)
        self.assertEqual(hints, result["hints"])
        self.assertEqual(["matched"] * len(hints), [row["status"] for row in result["resolutions"]])
        self.assertEqual(["adam", "rmsnorm", "roformer"], result["matched_doc_ids"])
        self.assertEqual(["exact-normalized-doc-id", "qualified-doc-id-version", "qualified-doc-id-version",
                          "qualified-doc-id-version", "exact-declared-source-version",
                          "qualified-doc-id-version", "qualified-doc-id-version"],
                         [row["candidates"][0]["match_type"] for row in result["resolutions"]])
        self.assertEqual(sha256_json(index.manifest), result["manifest_sha256"])
        self.assertEqual(index.manifest_file_sha256, result["manifest_file_sha256"])
        for row in result["resolutions"]:
            candidate = row["candidates"][0]
            original = next(doc for doc in index.manifest["documents"] if doc["doc_id"] == candidate["doc_id"])
            for key in ("doc_id", "source_version", "source_url", "expected_sha256"):
                self.assertEqual(original[key], candidate[key])
            self.assertIs(candidate["identity_authority"], False)
        validate_source_reference_result(result)
        validate_source_reference_result(result, index=index)

    def test_wrong_versions_titles_methods_and_prefixes_remain_unmatched(self) -> None:
        index = self.make_index()
        hints = ["Adam v9", "1412.6980v9", "rmsnorm-v2", "RoFormer@v4", "v8",
                 "Adam paper v8", "Root Mean Square Layer Normalization", "pRMSNorm",
                 "arXiv:1412.6980v8", "Adam v08", "RoFormer@V5", "Adam v8 extra",
                 "RMSNorm / v1", " Adam "]
        result = resolve_source_references(index, hints)
        self.assertEqual([], result["matched_doc_ids"])
        self.assertEqual(len(hints), result["unmatched_count"])
        self.assertTrue(all(row["candidates"] == [] for row in result["resolutions"]))

    def test_unicode_doc_key_surface_rule_and_literal_version_rule(self) -> None:
        index = self.make_index()
        result = resolve_source_references(index, ["ＡＤＡＭ", "ＲＭＳ＿Ｎｏｒｍ", "ＲＭＳＮｏｒｍ v1",
                                                    "Adam ｖ８", "１４１２.６９８０v８"])
        self.assertEqual(["matched", "matched", "matched", "unmatched", "unmatched"],
                         [row["status"] for row in result["resolutions"]])
        self.assertEqual(["adam", "rmsnorm"], result["matched_doc_ids"])
        validate_source_reference_result(result, index)

    def test_normalization_ambiguity_does_not_choose_a_document(self) -> None:
        index = self.make_index({"paper-a": "1000.0001v2", "paper_a": "1000.0002v2"})
        result = resolve_source_references(index, ["Paper-A", "paper_a v2", "1000.0001v2"])
        self.assertEqual(["ambiguous", "ambiguous", "matched"],
                         [row["status"] for row in result["resolutions"]])
        self.assertEqual(["paper-a", "paper_a"], [c["doc_id"] for c in result["resolutions"][0]["candidates"]])
        self.assertEqual(["paper-a"], result["matched_doc_ids"])
        validate_source_reference_result(result, index)

    def test_qualifier_disambiguates_normalized_ids_by_actual_declared_version(self) -> None:
        index = self.make_index({"paper-a": "1000.0001v2", "paper_a": "1000.0002v3"})
        result = resolve_source_references(index, ["paper_a-v2", "paper-a@v3", "paper-a@1000.0002v3"])
        self.assertEqual(["paper-a", "paper_a", "paper_a"],
                         [row["candidates"][0]["doc_id"] for row in result["resolutions"]])

    def test_full_version_and_cross_selector_collisions_are_ambiguous(self) -> None:
        index = self.make_index({"adam": "1412.6980v8", "alias-free-copy": "1412.6980v8",
                                 "1412.6980v8": "1000.0001v1"})
        result = resolve_source_references(index, ["1412.6980v8"])
        self.assertEqual("ambiguous", result["resolutions"][0]["status"])
        self.assertEqual(3, result["resolutions"][0]["candidate_count"])
        self.assertEqual([], result["matched_doc_ids"])
        self.assertEqual({"exact-declared-source-version", "exact-normalized-doc-id"},
                         {c["match_type"] for c in result["resolutions"][0]["candidates"]})
        validate_source_reference_result(result, index)

    def test_hyphens_in_registered_doc_and_full_version_are_not_guessed(self) -> None:
        index = self.make_index({"paper-a": "preprint-1000.0001v2"})
        result = resolve_source_references(index, ["paper-a-v2", "paper-a@preprint-1000.0001v2",
                                                  "paper-a-preprint-1000.0001v2"])
        self.assertEqual(3, result["matched_count"])

    def test_duplicates_preserve_per_hint_results_and_first_seen_matched_doc_order(self) -> None:
        index = self.make_index()
        result = resolve_source_references(index, ["roformer", "adam", "roformer", "missing"])
        self.assertEqual([0, 1, 2, 3], [r["hint_index"] for r in result["resolutions"]])
        self.assertEqual(["roformer", "adam"], result["matched_doc_ids"])
        self.assertEqual(3, result["matched_count"])
        self.assertEqual(1, result["unmatched_count"])
        self.assertEqual(result, resolve_source_references(index, result["hints"]))

    def test_bounded_hints_reject_malformed_and_unencodable_values(self) -> None:
        index = self.make_index()
        for hints in ([], [""] , ["x" * 4097], ["x"] * 129, "adam", [False], [1], [float("nan")], ["\ud800"]):
            with self.subTest(hints=repr(hints)[:100]), self.assertRaises(SourceEvidenceError):
                resolve_source_references(index, hints)
        self.assertEqual(128, resolve_source_references(index, ["unknown"] * 128)["unmatched_count"])
        self.assertEqual(1, resolve_source_references(index, ["x" * 4096])["unmatched_count"])
        with self.assertRaises(SourceEvidenceError):
            resolve_source_references(False, ["adam"])
        with patch("kgdistiller.source_references.MAX_RESULT_BYTES", 100), self.assertRaisesRegex(SourceEvidenceError, "byte bound"):
            resolve_source_references(index, ["adam"])

    def test_strict_boolean_authority_counts_and_unknown_fields_fail(self) -> None:
        result = resolve_source_references(self.make_index(), ["adam"])
        mutations = [lambda r: r.update(identity_authority=0),
                     lambda r: r.update(identity_authority=True),
                     lambda r: r.update(hint_count=True),
                     lambda r: r.update(matched_count=True),
                     lambda r: r["resolutions"][0].update(identity_authority=0),
                     lambda r: r["resolutions"][0].update(candidate_count=True),
                     lambda r: r["resolutions"][0]["candidates"][0].update(identity_authority=0),
                     lambda r: r["resolutions"][0]["candidates"][0].update(aliases=["invented"]),
                     lambda r: r.update(unexpected=float("nan"))]
        for ordinal, mutate in enumerate(mutations):
            forged = copy.deepcopy(result)
            mutate(forged)
            if ordinal != len(mutations) - 1:
                self.redigest(forged)
            with self.subTest(ordinal=ordinal), self.assertRaises(SourceEvidenceError):
                validate_source_reference_result(forged)

    def test_selector_and_count_forgery_fails_even_after_redigest(self) -> None:
        result = resolve_source_references(self.make_index(), ["Adam v8", "rmsnorm", "missing"])
        mutations = [lambda r: r["resolutions"][0]["candidates"][0].update(source_version="1412.6980v9"),
                     lambda r: r["resolutions"][0]["candidates"][0].update(match_type="exact-normalized-doc-id"),
                     lambda r: r["resolutions"][0].update(status="ambiguous"),
                     lambda r: r["resolutions"][0].update(hint_index=2),
                     lambda r: r["resolutions"][0].update(candidate_count=0),
                     lambda r: r.update(matched_doc_ids=["rmsnorm", "adam"]),
                     lambda r: r.update(unmatched_count=0),
                     lambda r: r.update(hints_sha256="0" * 64),
                     lambda r: r["resolutions"][0].update(hint_sha256="0" * 64)]
        for ordinal, mutate in enumerate(mutations):
            forged = copy.deepcopy(result)
            mutate(forged)
            self.redigest(forged)
            with self.subTest(ordinal=ordinal), self.assertRaises(SourceEvidenceError):
                validate_source_reference_result(forged)
        changed = copy.deepcopy(result)
        changed["resolutions"][0]["candidates"][0]["source_url"] += "/forged"
        with self.assertRaisesRegex(SourceEvidenceError, "digests"):
            validate_source_reference_result(changed)

    def test_standalone_closure_is_not_current_manifest_membership(self) -> None:
        index = self.make_index()
        result = resolve_source_references(index, ["adam"])
        forged = copy.deepcopy(result)
        forged["resolutions"][0]["candidates"][0]["expected_sha256"] = "0" * 64
        self.redigest(forged)
        validate_source_reference_result(forged)
        with self.assertRaisesRegex(SourceEvidenceError, "current full manifest"):
            validate_source_reference_result(forged, index)
        missing = copy.deepcopy(result)
        missing["resolutions"][0].update(candidates=[], candidate_count=0, status="unmatched")
        missing.update(matched_count=0, unmatched_count=1, matched_doc_ids=[])
        self.redigest(missing)
        validate_source_reference_result(missing)
        with self.assertRaisesRegex(SourceEvidenceError, "current full manifest"):
            validate_source_reference_result(missing, index)

    def test_conflicting_declarations_and_candidate_order_fail(self) -> None:
        index = self.make_index({"paper-a": "1000.0001v2", "paper_a": "1000.0002v2"})
        result = resolve_source_references(index, ["paper-a", "paper_a"])
        duplicate = copy.deepcopy(result)
        duplicate["resolutions"][1]["candidates"][0]["expected_sha256"] = "0" * 64
        self.redigest(duplicate)
        with self.assertRaisesRegex(SourceEvidenceError, "conflict"):
            validate_source_reference_result(duplicate)
        reversed_result = copy.deepcopy(result)
        reversed_result["resolutions"][0]["candidates"].reverse()
        self.redigest(reversed_result)
        with self.assertRaisesRegex(SourceEvidenceError, "order"):
            validate_source_reference_result(reversed_result)

    def test_source_manifest_and_memory_mutations_fail_pre_and_post_guards(self) -> None:
        index = self.make_index()
        result = resolve_source_references(index, ["adam"])
        original = self.path.read_bytes()
        self.path.write_bytes(original + b" ")
        with self.assertRaisesRegex(SourceEvidenceError, "stale-source"):
            resolve_source_references(index, ["adam"])
        self.path.write_bytes(original)
        path = self.root / index.manifest["documents"][0]["path"]
        old_source = path.read_bytes()
        path.write_bytes(b"changed source")
        with self.assertRaisesRegex(SourceEvidenceError, "stale-source"):
            validate_source_reference_result(result, index)
        path.write_bytes(old_source)
        check = index._check_sources
        calls = []
        def mutate_after_first_guard():
            check()
            calls.append(True)
            if len(calls) == 1:
                path.write_bytes(b"changed between guards")
        with patch.object(index, "_check_sources", side_effect=mutate_after_first_guard), self.assertRaisesRegex(SourceEvidenceError, "stale-source"):
            resolve_source_references(index, ["adam"])
        self.assertEqual(1, len(calls))
        path.write_bytes(old_source)
        index.fragments[0]["text"] += " changed"
        with self.assertRaisesRegex(SourceEvidenceError, "changed in memory"):
            resolve_source_references(index, ["adam"])

    def test_caller_hint_and_result_mutation_are_detected(self) -> None:
        index = self.make_index()
        hints = ["adam"]
        check = index._check_sources
        def mutate_hints():
            check()
            hints[:] = ["rmsnorm"]
        with patch.object(index, "_check_sources", side_effect=mutate_hints), self.assertRaisesRegex(SourceEvidenceError, "hints changed"):
            resolve_source_references(index, hints)
        result = resolve_source_references(index, ["adam"])
        def mutate_result():
            check()
            result["matched_doc_ids"] = ["rmsnorm"]
        with patch.object(index, "_check_sources", side_effect=mutate_result), self.assertRaisesRegex(SourceEvidenceError, "changed during validation"):
            validate_source_reference_result(result, index)


if __name__ == "__main__":
    unittest.main()
