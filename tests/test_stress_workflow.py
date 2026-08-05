from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class StressWorkflowHarnessTest(unittest.TestCase):
    def test_small_disposable_stress_run(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/stress_workflow.py"),
                "--nodes",
                "200",
                "--skip-fault-injection",
                "--query-samples",
                "2",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual("passed", report["status"])
        self.assertEqual(200, report["knowledge_nodes"])
        self.assertEqual(["markdown", "typst"], report["formats"])
        self.assertTrue(report["query"]["database_byte_stable"])
        self.assertTrue(report["incremental"]["graph_digest_unchanged"])
        self.assertEqual("planned", report["transaction"]["plan_status"])
        self.assertEqual("committed", report["transaction"]["receipt_status"])
        self.assertEqual(0, report["transaction"]["reader_errors"])
        self.assertEqual(2, report["query"]["latency_seconds"]["hybrid_context"]["samples"])

    def test_small_query_only_stress_run(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/stress_workflow.py"),
                "--nodes",
                "20",
                "--skip-transaction",
                "--query-samples",
                "2",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(20, report["knowledge_nodes"])
        self.assertFalse(report["transaction"]["enabled"])
        self.assertEqual("skipped", report["transaction"]["plan_status"])
        self.assertEqual("skipped", report["transaction"]["receipt_status"])


if __name__ == "__main__":
    unittest.main()
