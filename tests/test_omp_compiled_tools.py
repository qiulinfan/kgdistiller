from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.compiled_retrieval import CompiledLibrary
from kgdistiller.omp_compiled_tools import (
    BridgeError,
    bounded_result,
    encoded,
    error_response,
    execute,
    load_config,
    public_payload,
)
from tests.test_compiled_retrieval import library_payload

REPO_ROOT = Path(__file__).resolve().parents[1]


class OMPCompiledToolsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="kgdistiller-omp-tools-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library_path = self.root / "library.json"
        self.payload = library_payload()
        self.payload["nodes"]["map-continuous"]["conditions"].append("末尾条件：λ > 0。")
        self.library_path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        self.library = CompiledLibrary.from_payload(self.payload)
        self.questions = [{"qid": "question-a", "question": "Which declared meaning applies?"},
                          {"qid": "question-b", "question": "哪些条件必须保留？"}]
        (self.root / "questions.json").write_text(json.dumps(self.questions, ensure_ascii=False), encoding="utf-8")
        self.config = {
            "python_interpreter": sys.executable, "library_path": str(self.library_path),
            "byte_budget": 24000, "reference_limit": 4, "search_limit": 3,
            "max_response_bytes": 48000,
        }
        self.config_path = self.root / "compiled-tools-config.json"
        self.save_config()

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def files(self):
        return {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def call(self, request):
        return execute(request, load_config(self.config_path, self.root), self.root)

    def selection(self):
        return {"operation": "submit_selection", "selections": [
            {"qid": "question-a", "ranked": ["map-continuous"], "abstain": False},
            {"qid": "question-b", "ranked": [], "abstain": True},
        ]}

    def module_call(self, raw):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller.omp_compiled_tools", "--config", str(self.config_path), "--output", str(self.root)],
            input=raw, check=False, capture_output=True, cwd=self.root, env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(b"", result.stderr)
        return json.loads(result.stdout), result.stdout

    def test_read_operations_return_complete_core_content_without_writes(self):
        before = self.files()
        self.assertEqual(self.library.search("continuous", 3), self.call({"operation": "search", "qid": "question-a", "query": "continuous"})["result"])
        for reference, kind in ((None, None), ("source-analysis", "source"), ("definitions", "layer"), ("bounded map", "term"), ("norm", "node")):
            request = {"operation": "browse", "qid": "question-a"}
            if reference is not None:
                request.update(reference=reference, kind=kind)
            self.assertEqual(self.library.browse(reference, kind=kind), self.call(request)["result"])
        self.assertEqual([self.library.get("map-continuous")], self.call({"operation": "get", "references": ["map-continuous"]})["result"])
        self.assertEqual(self.library.inventory("Bounded map"), self.call({"operation": "inventory", "term": "Bounded map"})["result"])
        packed = self.call({"operation": "pack", "references": ["map-continuous", "norm"]})["result"]
        self.assertEqual(self.library.pack(["map-continuous", "norm"], self.config["byte_budget"]), packed)
        self.assertIn("末尾条件：λ > 0。", packed["entries"][0]["conditions"])
        self.assertEqual(before, self.files())

    def test_query_addresses_are_required_for_multiple_questions_and_never_enter_retrieval(self):
        for operation, fields in (("search", {"query": "continuous"}), ("browse", {})):
            for qid in (None, "unknown", 1, ""):
                request = {"operation": operation, **fields}
                if qid is not None:
                    request["qid"] = qid
                with self.subTest(request=request), self.assertRaises(BridgeError) as raised:
                    self.call(request)
                self.assertEqual("invalid_question", raised.exception.code)
            first = self.call({"operation": operation, "qid": "question-a", **fields})
            self.assertEqual(first, self.call({"operation": operation, "qid": "question-b", **fields}))
        with patch.object(CompiledLibrary, "search", return_value=[]) as search:
            self.call({"operation": "search", "qid": "question-a", "query": "continuous"})
            search.assert_called_once_with("continuous", limit=3)
        (self.root / "questions.json").write_text(json.dumps(self.questions[:1]), encoding="utf-8")
        for operation, fields in (("search", {"query": "continuous"}), ("browse", {})):
            self.assertEqual(self.call({"operation": operation, "qid": "question-a", **fields}), self.call({"operation": operation, **fields}))
            with self.assertRaises(BridgeError):
                self.call({"operation": operation, "qid": None, **fields})

    def test_browse_rejects_unknown_ambiguous_or_invalid_handles_and_whole_oversized_response(self):
        self.payload["papers"]["norm"] = {"title": "An ambiguous handle"}
        self.library_path.write_text(json.dumps(self.payload), encoding="utf-8")
        invalid = [{"reference": "unknown"}, {"reference": "norm"}, {"kind": "source"},
                   {"reference": None}, {"reference": ""}, {"reference": "norm", "kind": []},
                   {"reference": "norm", "kind": "invalid"}, {"reference": "norm", "limit": 1}]
        before = self.files()
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(BridgeError):
                self.call({"operation": "browse", "qid": "question-a", **fields})
        self.assertEqual("norm", self.call({"operation": "browse", "qid": "question-a", "reference": "norm", "kind": "node"})["result"]["reference"])
        self.config["max_response_bytes"] = 160
        self.save_config()
        with self.assertRaises(BridgeError) as raised:
            self.call({"operation": "browse", "qid": "question-a"})
        self.assertEqual("response_too_large", raised.exception.code)
        self.assertEqual(before.keys(), self.files().keys())

    def test_source_gap_projection_preserves_scientific_fields_and_gap_reasons(self):
        self.payload["nodes"]["map-continuous"]["paper"] = "opaque-unregistered-source"
        self.payload["nodes"]["map-continuous"]["surfaces"]["head_terms"] = ["Bounded map"]
        self.library_path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        for request in (
            {"operation": "get", "references": ["map-continuous"]},
            {"operation": "browse", "qid": "question-a", "reference": "map-continuous", "kind": "node"},
            {"operation": "inventory", "term": "Bounded map"},
            {"operation": "pack", "references": ["map-continuous"]},
        ):
            response = self.call(request)
            self.assertTrue(response["ok"])
            self.assertIn("metadata_projection", response)
            self.assertNotIn("opaque-unregistered-source", encoded(response).decode())
            self.assertIn("unresolved-source", encoded(response).decode())
            self.assertIn("末尾条件：λ > 0。", encoded(response).decode())
        original = [{"gaps": [{"reason": "unresolved-source", "reference": "hidden"}],
                     "scientific": {"gaps": [{"reason": "unresolved-source", "reference": "retain"}]}}]
        copied = copy.deepcopy(original)
        projected, changed = public_payload(original, "get")
        self.assertTrue(changed)
        self.assertEqual(copied, original)
        self.assertEqual(copied[0]["scientific"], projected[0]["scientific"])

    def test_fixed_budget_counts_refs_and_exact_registry_are_not_overridden(self):
        invalid = [
            {"operation": "pack", "references": ["map-continuous"], "byte_budget": 99999},
            {"operation": "get", "references": ["Bounded map"]},
            {"operation": "get", "references": ["norm", "norm"]},
            {"operation": "get", "references": ["norm"] * 5},
            {"operation": "search", "qid": "question-a", "query": "norm", "limit": 4},
            {"operation": "search", "qid": "question-a", "query": "norm", "limit": True},
            {"operation": "inventory", "term": "Bounded map", "limit": 1},
            {"operation": "inventory", "term": "  "},
        ]
        before = self.files()
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(BridgeError):
                self.call(request)
        self.assertEqual(before, self.files())

    def test_whole_response_and_submission_limits_never_produce_partial_success(self):
        minimum = len(encoded(error_response("response_too_large", "Whole response exceeds the configured limit.")))
        self.config["max_response_bytes"] = minimum
        self.save_config()
        with self.assertRaisesRegex(BridgeError, "Whole response"):
            bounded_result({"late": "科学条件" * 100}, self.config)
        response, raw = self.module_call(encoded({"operation": "get", "references": ["map-continuous"]}))
        self.assertFalse(response["ok"])
        self.assertEqual("response_too_large", response["error"]["code"])
        self.assertLessEqual(len(raw), minimum + 1)
        selection = self.selection()
        selection["selections"][1]["ranked"] = ["map-finite-image"]
        selection["selections"][1]["abstain"] = False
        self.config["max_response_bytes"] = len(encoded(selection["selections"]))
        self.save_config()
        with self.assertRaisesRegex(BridgeError, "Whole response"):
            self.call(selection)
        self.assertFalse((self.root / "submitted-selection.json").exists())

    def test_submission_requires_current_question_order_and_registered_distinct_refs(self):
        invalid = []
        request = self.selection(); request["selections"].reverse(); invalid.append(request)
        request = self.selection(); request["selections"][0]["qid"] = "unknown"; invalid.append(request)
        request = self.selection(); request["selections"].pop(); invalid.append(request)
        request = self.selection(); request["selections"][0]["ranked"] = ["norm", "norm"]; invalid.append(request)
        request = self.selection(); request["selections"][0]["ranked"] = ["Bounded map"]; invalid.append(request)
        request = self.selection(); request["selections"][0]["abstain"] = 0; invalid.append(request)
        request = self.selection(); request["selections"][0]["abstain"] = True; invalid.append(request)
        request = self.selection(); request["selections"][0]["extra"] = "not accepted"; invalid.append(request)
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(BridgeError):
                self.call(request)
        self.assertFalse((self.root / "submitted-selection.json").exists())

    def test_exclusive_submission_preserves_original_and_never_overwrites(self):
        request = self.selection()
        response = self.call(request)
        self.assertEqual({"submitted": True, "questions": 2}, response["result"])
        submitted = self.root / "submitted-selection.json"
        original = submitted.read_bytes()
        self.assertEqual(request["selections"], json.loads(original))
        request["selections"][0]["ranked"] = ["norm"]
        with self.assertRaisesRegex(BridgeError, "no overwrite"):
            self.call(request)
        self.assertEqual(original, submitted.read_bytes())

    def test_config_rejects_wrong_location_or_limits_and_keeps_lexical_interpreter(self):
        self.assertEqual(sys.executable, load_config(self.config_path, self.root)["python_interpreter"])
        lexical = str(self.root / "lexical-venv" / "python")
        with patch.object(Path, "is_file", return_value=True), patch("kgdistiller.omp_compiled_tools.os.access", return_value=True):
            self.config["python_interpreter"] = lexical
            self.save_config()
            self.assertEqual(lexical, load_config(self.config_path, self.root)["python_interpreter"])
        self.config["python_interpreter"] = sys.executable
        for key, value in (("byte_budget", True), ("reference_limit", 0), ("search_limit", -1),
                           ("max_response_bytes", 1), ("library_path", "relative.json")):
            valid = self.config[key]; self.config[key] = value; self.save_config()
            with self.subTest(key=key), self.assertRaises(BridgeError):
                load_config(self.config_path, self.root)
            self.config[key] = valid
        self.save_config()
        outside = self.root / "outside"; outside.mkdir()
        with self.assertRaises(BridgeError):
            load_config(self.config_path, outside)

    def test_module_utf8_and_bounded_bad_requests_do_not_leak_paths(self):
        response, _ = self.module_call(encoded({"operation": "search", "qid": "question-a", "query": "有界线性算子如何判定连续"}))
        self.assertTrue(response["ok"])
        self.assertEqual("map-continuous", response["result"][0]["reference"])
        self.config["max_response_bytes"] = 240
        self.save_config()
        response, raw = self.module_call(b" " * 241)
        self.assertEqual("request_too_large", response["error"]["code"])
        self.assertLessEqual(len(raw), 241)
        response, raw = self.module_call(encoded({"operation": "pack", "references": ["unregistered"]}))
        self.assertFalse(response["ok"])
        self.assertNotIn(str(self.root), raw.decode())

    def test_large_caller_bound_does_not_preallocate_or_overflow_for_a_small_request(self):
        self.config["max_response_bytes"] = 10 ** 25
        self.save_config()
        response, _ = self.module_call(encoded({"operation": "get", "references": ["norm"]}))
        self.assertTrue(response["ok"])
        self.assertEqual("norm", response["result"][0]["reference"])


if __name__ == "__main__":
    unittest.main()
