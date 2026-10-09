from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import canonical_json, sha256_json
from kgdistiller.source_context import (
    CONTEXT_PROJECTION,
    CONTEXT_SCHEMA,
    build_source_context,
    validate_source_context,
)
from kgdistiller.source_evidence import (
    SourceEvidenceError,
    SourceEvidenceIndex,
    _finalize_budget,
    _projection,
    validate_source_evidence_result,
)


class SourceContextTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="kgdistiller-source-context-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def index(self, documents: dict[str, bytes], *, hash_mode: str = "raw-utf8") -> SourceEvidenceIndex:
        entries = []
        for doc_id, raw in documents.items():
            path = self.root / (doc_id + ".txt")
            path.write_bytes(raw)
            normalized = raw.decode().replace("\r\n", "\n").replace("\r", "\n").encode()
            entries.append({"doc_id": doc_id, "path": path.name,
                            "source_url": "https://example.org/" + doc_id,
                            "source_version": "paper-v2", "source_type": "paper-text",
                            "expected_sha256": hashlib.sha256(raw if hash_mode == "raw-utf8" else normalized).hexdigest()})
        manifest = {"schema": "kgdistiller-source-evidence-manifest-v1", "root": str(self.root),
                    "hash_mode": hash_mode, "documents": entries}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return SourceEvidenceIndex.from_manifest(path)

    def result(self, raw: bytes = b"# Method\n\nalpha observation\n\nalpha second observation\n") -> dict:
        return self.index({"paper": raw}).search("alpha", limit=100, byte_budget=200000)

    def finalize(self, payload: dict) -> dict:
        _finalize_budget(payload)
        return payload

    def test_shared_metadata_preserves_every_original_scientific_byte(self) -> None:
        raw = ("# " + "Method heading " * 90 + "\n\n" + "\n\n".join(
            "alpha observation " + str(index) for index in range(12)) + "\n").encode()
        result = self.result(raw)
        bundle = build_source_context([result], byte_budget=200000)
        self.assertEqual(CONTEXT_SCHEMA, bundle["schema"])
        self.assertEqual(CONTEXT_PROJECTION, bundle["projection"])
        self.assertEqual(result["projection"], bundle["source_projection"])
        self.assertEqual(1, len(bundle["sources"]))
        self.assertEqual(1, len(bundle["headings"]))
        self.assertLess(len(canonical_json(bundle).encode()), len(canonical_json(result).encode()))
        self.assertEqual([fragment["text"] for fragment in result["fragments"]],
                         [fragment["text"] for fragment in bundle["fragments"]])
        for fragment in bundle["fragments"]:
            span = fragment["byte_span"]
            self.assertEqual(raw[span["start"]:span["end"]], fragment["text"].encode())
            self.assertNotIn("rank", fragment)
            self.assertNotIn("score", fragment)
            self.assertNotIn("node_id", fragment)
            self.assertIs(fragment["identity_authority"], False)
            self.assertIs(fragment["complete_definition"], False)
        validate_source_context(bundle, [result])

    def test_queries_keep_separate_ranks_scores_and_stable_first_baseline_order(self) -> None:
        index = self.index({"paper": b"# Method\n\nalpha beta beta\n\nalpha alpha beta\n\nalpha gamma\n"})
        results = [index.search(query, limit=100, byte_budget=200000) for query in ("alpha", "beta")]
        before = copy.deepcopy(results)
        bundle = build_source_context(results, byte_budget=200000)
        baseline = list(dict.fromkeys(fragment["fragment_id"] for result in results for fragment in result["fragments"]))
        self.assertEqual(baseline, [fragment["fragment_id"] for fragment in bundle["fragments"]])
        for index, (result, provenance) in enumerate(zip(results, bundle["retrievals"])):
            self.assertEqual(result["query"], provenance["query"])
            self.assertEqual(sha256_json(result), provenance["result_sha256"])
            packed_origins = [(fragment["fragment_id"], origin["rank"], origin["score"])
                              for fragment in bundle["fragments"] for origin in fragment["origins"] if origin["result_index"] == index]
            self.assertEqual([(fragment["fragment_id"], fragment["rank"], fragment["score"]) for fragment in result["fragments"]],
                             sorted(packed_origins, key=lambda item: item[1]))
        self.assertEqual(before, results)
        self.assertEqual(bundle, build_source_context(results, byte_budget=200000))
        validate_source_context(bundle, results)

    def test_unicode_crlf_table_algorithm_math_and_partial_are_not_trimmed(self) -> None:
        raw = ("# alpha 数学σ\r\n\r\n| model | MSE |\r\n|---|---|\r\n| alpha | 0.79 |\r\n\r\n"
               "## Algorithm 1\r\n\r\n```python\r\nalpha = softmax((t-C)/tau)\r\nloss = sum(alpha**2)\r\n```\r\n\r\n"
               "alpha 域 $H\\in\\mathbb{R}^{p\\times CN}$。\r\n").encode()
        index = self.index({"unicode": raw}, hash_mode="normalized-utf8")
        results = [index.search("alpha 数学", limit=100, byte_budget=200000)]
        bundle = build_source_context(results, byte_budget=200000)
        source = bundle["sources"][0]
        self.assertNotEqual(source["raw_source_sha256"], source["normalized_source_sha256"])
        self.assertEqual(source["source_sha256"], source["normalized_source_sha256"])
        self.assertEqual({"table", "algorithm", "paragraph", "heading"}, {item["fragment_type"] for item in bundle["fragments"]})
        for item in bundle["fragments"]:
            self.assertEqual(raw[item["byte_span"]["start"]:item["byte_span"]["end"]], item["text"].encode())
        validate_source_context(bundle, results)
        with patch("kgdistiller.source_evidence.MAX_FRAGMENT_BYTES", 64):
            index = self.index({"long": ("alpha 数学σ " * 100 + "\n").encode()})
        result = index.search("alpha", limit=100, byte_budget=200000)
        bundle = build_source_context([result], byte_budget=200000)
        self.assertTrue(all(item["partial"] for item in bundle["fragments"]))
        self.assertEqual([item["text"] for item in result["fragments"]], [item["text"] for item in bundle["fragments"]])

    def test_caller_selection_controls_only_subset_and_packing_order(self) -> None:
        result = self.result()
        ids = [fragment["fragment_id"] for fragment in result["fragments"]]
        selected = ids[::-1][:1]
        bundle = build_source_context([result], selected_fragment_ids=selected)
        self.assertEqual(selected, [fragment["fragment_id"] for fragment in bundle["fragments"]])
        self.assertEqual([{"fragment_id": item, "reason": "not-selected"} for item in ids if item not in selected], bundle["omissions"])
        validate_source_context(bundle, [result])
        empty = build_source_context([result], selected_fragment_ids=[])
        self.assertEqual([], empty["fragments"])
        self.assertEqual([], empty["sources"])
        self.assertEqual([], empty["headings"])
        self.assertEqual(len(ids), empty["omitted_fragments"])
        for invalid in ([ids[0], ids[0]], ["evidence:sha256:" + "0" * 64], [False], ids[0]):
            with self.subTest(invalid=invalid), self.assertRaises(SourceEvidenceError):
                build_source_context([result], selected_fragment_ids=invalid)

    def test_budget_packs_fragments_atomically_and_declares_bounded_diagnostics(self) -> None:
        result = self.result(("# Method\n\nalpha " + "longcondition " * 170 + "\n\nalpha concise\n").encode())
        full = build_source_context([result], byte_budget=200000)
        all_omitted = copy.deepcopy(full)
        all_omitted["fragments"] = []; all_omitted["sources"] = []; all_omitted["headings"] = []
        all_omitted["omissions"] = []
        all_omitted["omitted_fragments"] = full["candidate_count"]
        all_omitted["budget_omitted_fragments"] = full["candidate_count"]
        all_omitted["diagnostics_truncated"] = True
        self.finalize(all_omitted)
        minimum = all_omitted["budget"]["used_bytes"]
        saw_subset = False
        for budget in range(minimum + 20, full["budget"]["used_bytes"] + 40, 151):
            bundle = build_source_context([result], byte_budget=budget)
            self.assertLessEqual(bundle["budget"]["used_bytes"], budget)
            self.assertEqual(bundle["budget"]["used_bytes"], len(canonical_json(bundle).encode()))
            self.assertEqual(bundle["diagnostics_truncated"], len(bundle["omissions"]) < bundle["omitted_fragments"])
            validate_source_context(bundle, [result])
            saw_subset |= bool(bundle["fragments"] and bundle["omissions"])
            for item in bundle["fragments"]:
                original = next(fragment for fragment in result["fragments"] if fragment["fragment_id"] == item["fragment_id"])
                self.assertEqual(original["text"], item["text"])
        self.assertTrue(saw_subset)
        with self.assertRaisesRegex(SourceEvidenceError, "budget cannot hold"):
            build_source_context([result], byte_budget=1)

    def test_empty_retrieval_and_upstream_limit_budget_gaps_are_explicit(self) -> None:
        index = self.index({"paper": b"alpha first\n\nalpha second\n\nalpha third\n"})
        result = index.search("alpha", limit=1, byte_budget=200000)
        bundle = build_source_context([result])
        self.assertEqual("retrieval-limit", bundle["gaps"][0]["reason"])
        self.assertEqual(result["matched_fragments"] - 1, bundle["gaps"][0]["fragment_count"])
        omitted = None
        for budget in range(1200, 4000, 20):
            try:
                candidate = index.search("alpha", limit=100, byte_budget=budget)
            except SourceEvidenceError:
                continue
            if candidate["omitted_fragments"]:
                omitted = candidate
                break
        self.assertIsNotNone(omitted)
        bundle = build_source_context([omitted])
        self.assertEqual("retrieval-byte-budget", bundle["gaps"][0]["reason"])
        self.assertEqual(omitted["omitted_fragments"], bundle["gaps"][0]["fragment_count"])
        validate_source_context(bundle, [omitted])
        empty = index.search("unmatchedtoken", byte_budget=200000)
        bundle = build_source_context([empty])
        self.assertEqual(0, bundle["candidate_count"])
        validate_source_context(bundle, [empty])

    def test_input_bounds_nonfinite_values_and_original_result_validation(self) -> None:
        result = self.result()
        for inputs in ([], [result] * 33, result, [None]):
            with self.subTest(inputs=type(inputs)), self.assertRaises(SourceEvidenceError):
                build_source_context(inputs)
        for budget in (True, 0, -1, 1.5, 4194305):
            with self.subTest(budget=budget), self.assertRaises(SourceEvidenceError):
                build_source_context([result], byte_budget=budget)
        for kind in ("nan", "inf", "boolean-score", "rank", "span", "text", "hash", "authority"):
            forged = copy.deepcopy(result)
            fragment = forged["fragments"][0]
            if kind == "nan": fragment["score"] = float("nan")
            if kind == "inf": fragment["score"] = float("inf")
            if kind == "boolean-score": fragment["score"] = True
            if kind == "rank": fragment["rank"] = 100
            if kind == "span": fragment["byte_span"]["end"] += 1
            if kind == "text": fragment["text"] += " x"
            if kind == "hash": fragment["content_sha256"] = "0" * 64
            if kind == "authority": forged["identity_authority"] = 0
            if kind not in ("nan", "inf"):
                self.finalize(forged)
            with self.subTest(kind=kind), self.assertRaises(SourceEvidenceError):
                build_source_context([forged])
        with patch("kgdistiller.source_context.MAX_INPUT_BYTES", 10), self.assertRaisesRegex(SourceEvidenceError, "4 MiB"):
            build_source_context([result])
        with patch("kgdistiller.source_context.validate_source_evidence_result", wraps=validate_source_evidence_result) as validator:
            build_source_context([result, result])
            self.assertGreaterEqual(validator.call_count, 2)

    def test_manifest_document_and_duplicate_fragment_conflicts_fail_closed(self) -> None:
        result = self.result()
        for kind in ("manifest", "manifest-file", "projection", "source-url", "version", "source-hash", "same-id-heading"):
            forged = copy.deepcopy(result)
            if kind == "manifest": forged["manifest_sha256"] = "0" * 64
            if kind == "manifest-file": forged["manifest_file_sha256"] = "0" * 64
            if kind == "projection": forged["projection"] = "other-projection"
            for fragment in forged["fragments"]:
                if kind == "source-url": fragment["source_url"] += "/changed"
                if kind == "version": fragment["source_version"] = "paper-v3"
                if kind == "source-hash": fragment["normalized_source_sha256"] = "0" * 64
                if kind == "same-id-heading": fragment["heading_context"][0]["text"] += " altered"
                fragment["projection_sha256"] = sha256_json(_projection(fragment))
            self.finalize(forged)
            with self.subTest(kind=kind), self.assertRaises(SourceEvidenceError):
                build_source_context([result, forged])

    def test_standalone_hash_span_reference_rank_and_budget_tampering_is_rejected(self) -> None:
        result = self.result()
        bundle = build_source_context([result])
        for kind in ("text", "span", "projection", "unknown-source", "unknown-heading", "duplicate-heading", "heading-text", "rank", "candidate-score", "budget", "used", "omission", "selected", "gaps"):
            forged = copy.deepcopy(bundle)
            fragment = forged["fragments"][0]
            if kind == "text": fragment["text"] += "scientific change"
            if kind == "span": fragment["block_byte_span"]["end"] = 1
            if kind == "projection": fragment["projection_sha256"] = "0" * 64
            if kind == "unknown-source": fragment["source_ref"] = "missing"
            if kind == "unknown-heading": fragment["heading_refs"] = [19199]
            if kind == "duplicate-heading": fragment["heading_refs"] *= 2
            if kind == "heading-text": forged["headings"][0]["text"] += "changed"
            if kind == "rank": fragment["origins"][0]["rank"] = 100
            if kind == "candidate-score": fragment["origins"][0]["score"] = float("inf")
            if kind == "budget": forged["budget"]["byte_budget"] = 1
            if kind == "omission": forged["omitted_fragments"] = 1
            if kind == "selected": forged["selected_fragment_ids"] = [fragment["fragment_id"], fragment["fragment_id"]]
            if kind == "gaps": forged["gaps"].append({"result_index": 0, "reason": "retrieval-limit", "fragment_count": 1, "reported_fragment_ids": [], "diagnostics_truncated": False})
            if kind not in ("candidate-score", "used"):
                self.finalize(forged)
            if kind == "used": forged["budget"]["used_bytes"] += 1
            with self.subTest(kind=kind), self.assertRaises(SourceEvidenceError):
                validate_source_context(forged)

    def test_all_authority_flags_require_actual_false_booleans(self) -> None:
        bundle = build_source_context([self.result()])
        for target in ("top-identity", "top-complete", "fragment-identity", "fragment-complete", "heading", "scoring"):
            forged = copy.deepcopy(bundle)
            if target == "top-identity": forged["identity_authority"] = 0
            if target == "top-complete": forged["complete_definition"] = 0
            if target == "fragment-identity": forged["fragments"][0]["identity_authority"] = 0
            if target == "fragment-complete": forged["fragments"][0]["complete_definition"] = 0
            if target == "heading": forged["headings"][0]["identity_authority"] = 0
            if target == "scoring": forged["retrievals"][0]["scoring"]["identity_authority"] = 0
            self.finalize(forged)
            with self.subTest(target=target), self.assertRaises(SourceEvidenceError):
                validate_source_context(forged)

    def test_original_input_replay_checks_omitted_fields_and_rehashed_provenance(self) -> None:
        result = self.result()
        bundle = build_source_context([result], selected_fragment_ids=[])
        for kind in ("input-hash", "result-hash", "scoring", "original-budget"):
            forged = copy.deepcopy(bundle)
            if kind == "input-hash": forged["input_sha256"] = "0" * 64
            if kind == "result-hash": forged["retrievals"][0]["result_sha256"] = "0" * 64
            if kind == "scoring": forged["retrievals"][0]["diagnostics_truncated"] = True
            if kind == "original-budget":
                forged["retrievals"][0]["budget"]["used_bytes"] += 1
                forged["input_bytes"] += 1
            self.finalize(forged)
            validate_source_context(forged)  # Closure is not an original-file attestation.
            with self.subTest(kind=kind), self.assertRaisesRegex(SourceEvidenceError, "supplied original"):
                validate_source_context(forged, [result])
        packed = build_source_context([result])
        packed["fragments"][0]["origins"][0]["score"] += 0.5
        self.finalize(packed)
        validate_source_context(packed)
        with self.assertRaisesRegex(SourceEvidenceError, "supplied original"):
            validate_source_context(packed, [result])
        changed = copy.deepcopy(result)
        changed["query"] = "other-query"
        changed["query_sha256"] = hashlib.sha256(changed["query"].encode()).hexdigest()
        self.finalize(changed)
        with self.assertRaises(SourceEvidenceError):
            validate_source_context(bundle, [changed])

    def test_compact_context_keeps_more_whole_fragments_than_full_result_in_same_budget(self) -> None:
        index = self.index({"paper": ("# alpha Method\n\n" + "\n\n".join(
            f"alpha scientific observation {row}: condition x > 0." for row in range(40)) + "\n").encode()})
        full = index.search("alpha", limit=30, byte_budget=15000)
        original = index.search("alpha", limit=30, byte_budget=200000)
        compact = build_source_context([original], byte_budget=15000)
        self.assertGreater(len(compact["fragments"]), len(full["fragments"]))
        self.assertIsNone(compact["selected_fragment_ids"])
        self.assertNotIn("candidates", compact["retrievals"][0])
        self.assertTrue(all(isinstance(reference, int) for fragment in compact["fragments"] for reference in fragment["heading_refs"]))
        validate_source_context(compact, [original])

    def test_partial_context_cannot_drop_a_required_query_origin(self) -> None:
        result = self.result()
        self.assertEqual(2, len(result["fragments"]))
        bundle = build_source_context([result, result], selected_fragment_ids=[result["fragments"][0]["fragment_id"]])
        self.assertEqual(1, bundle["omitted_fragments"])
        self.assertEqual(2, len(bundle["fragments"][0]["origins"]))
        validate_source_context(bundle)
        forged = copy.deepcopy(bundle)
        forged["fragments"][0]["origins"].pop()
        self.finalize(forged)
        with self.assertRaisesRegex(SourceEvidenceError, "origin counts"):
            validate_source_context(forged)

    def test_input_canonical_bytes_must_close_over_each_original_result_budget(self) -> None:
        result = self.result()
        for results in ([result], [result, result]):
            bundle = build_source_context(results)
            self.assertEqual(sum(item["budget"]["used_bytes"] for item in results) + len(results) + 1, bundle["input_bytes"])
            forged = copy.deepcopy(bundle)
            forged["input_bytes"] = 1
            self.finalize(forged)
            with self.subTest(result_count=len(results)), self.assertRaisesRegex(SourceEvidenceError, "input_bytes"):
                validate_source_context(forged)

    def test_builder_and_result_replay_do_not_attest_or_write_current_source_files(self) -> None:
        result = self.result()
        source = self.root / "paper.txt"
        source.write_bytes(b"changed after the original retrieval\n")
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        bundle = build_source_context([result])
        validate_source_context(bundle, [result])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})
        self.assertNotIn("changed after the original retrieval", "".join(item["text"] for item in bundle["fragments"]))
        self.assertEqual(hashlib.sha256(b"changed after the original retrieval\n").hexdigest(),
                         hashlib.sha256(source.read_bytes()).hexdigest())

    def test_inputs_mutating_during_validation_or_build_fail_closed(self) -> None:
        results = [self.result()]
        original_validator = validate_source_evidence_result
        def mutate_during_validation(snapshot):
            original_validator(snapshot)
            results[0]["query"] = "changed during validation"
        with patch("kgdistiller.source_context.validate_source_evidence_result", side_effect=mutate_during_validation), self.assertRaisesRegex(SourceEvidenceError, "changed during validation"):
            build_source_context(results)
        results = [self.result()]
        original_context_validator = validate_source_context
        def mutate_during_build(payload):
            original_context_validator(payload)
            results[0]["query"] = "changed during build"
        with patch("kgdistiller.source_context.validate_source_context", side_effect=mutate_during_build), self.assertRaisesRegex(SourceEvidenceError, "changed while building"):
            build_source_context(results)


if __name__ == "__main__":
    unittest.main()
