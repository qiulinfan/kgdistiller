from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SemanticBenchmarkTest(unittest.TestCase):
    def test_one_thousand_vector_query_only_smoke(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/benchmark_semantic.py"),
                "--sizes",
                "1000",
                "--samples",
                "1",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual("qlkg-semantic-benchmark-v1", report["schema"])
        self.assertEqual("passed", report["status"])
        self.assertEqual([1000], report["configuration"]["sizes"])
        self.assertEqual(128, report["configuration"]["dimensions"])
        self.assertIn("python", report["environment"])
        self.assertIn("sqlite", report["environment"])

        self.assertEqual(1, len(report["cases"]))
        case = report["cases"][0]
        self.assertEqual(1000, case["size"])
        self.assertEqual(1000, case["ready_records"])
        self.assertEqual(1000 * 128 * 4, case["ready_vector_bytes"])
        self.assertGreater(case["database_bytes"], 0)
        self.assertEqual(1000 * 128, case["scalar_operations_per_query"])
        self.assertEqual(1, case["query_batches"])
        self.assertEqual(0, case["document_embedding_calls"])
        self.assertEqual(10, case["result_count"])
        self.assertTrue(case["generation_unchanged"])
        self.assertTrue(case["database_bytes_unchanged"])
        latency = case["latency_seconds"]
        self.assertEqual(1, latency["samples"])
        self.assertEqual(latency["p50"], latency["p95"])
        self.assertEqual(latency["p95"], latency["max"])
        self.assertGreaterEqual(latency["p50"], 0.0)


if __name__ == "__main__":
    unittest.main()
