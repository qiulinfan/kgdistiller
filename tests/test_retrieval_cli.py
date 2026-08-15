from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import main, parse_args  # noqa: E402
from kgdistiller.retrieval import (  # noqa: E402
    RETRIEVAL_PLAN_SCHEMA,
    SEARCH_EXECUTION_SCHEMA,
    SEARCH_RESULT_SCHEMA,
)
from tests.test_query import write_fixture_graph  # noqa: E402


def retrieval_plan() -> dict:
    return {
        "schema": "qlkg-retrieval-plan-v2",
        "question": "How does a measure depend on a sigma algebra?",
        "namespace": "personal",
        "identity_queries": ["西格玛代数"],
        "lexical_queries": ["countably additive"],
        "graph": {
            "seed_ids": [],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 2,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


def repository_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class RetrievalCliParserTest(unittest.TestCase):
    def parse(self, *arguments: str):
        with patch.object(sys, "argv", ["kgdistiller", *arguments]):
            return parse_args()

    def assert_parse_error(self, *arguments: str) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.parse(*arguments)
        self.assertEqual(2, raised.exception.code)

    def run_cli(self, root: Path, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            sys,
            "argv",
            ["kgdistiller", "--repo-root", str(root), *arguments],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_agent_search_and_context_require_query_xor_plan(self) -> None:
        legacy = self.parse("agent", "search", "countable closure")
        planned = self.parse("agent", "search", "--plan", "retrieval.json")
        context = self.parse(
            "agent", "context", "why alpha", "--namespace", "paper:fixture"
        )

        self.assertEqual("countable closure", legacy.query)
        self.assertIsNone(legacy.plan)
        self.assertEqual(Path("retrieval.json"), planned.plan)
        self.assertEqual("paper:fixture", context.namespace)
        self.assert_parse_error("agent", "search")
        self.assert_parse_error(
            "agent", "search", "countable closure", "--plan", "retrieval.json"
        )
        self.assert_parse_error(
            "agent", "context", "why alpha", "--plan", "retrieval.json"
        )
        self.assert_parse_error(
            "agent", "search", "--plan", "retrieval.json", "--limit", "5"
        )

    def test_missing_graph_fails_without_creating_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            before = repository_bytes(root)

            status, _, error = self.run_cli(root, "agent", "search", "alpha")

            self.assertEqual(1, status)
            self.assertIn("authority graph", error)
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_legacy_and_planned_search_read_the_same_json_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(retrieval_plan()), encoding="utf-8")
            before = repository_bytes(root)

            outputs = []
            for arguments in (
                ("agent", "search", "countably additive"),
                ("agent", "search", "--plan", str(plan_path)),
            ):
                status, output, error = self.run_cli(root, *arguments)
                self.assertEqual(0, status, error)
                outputs.append(json.loads(output))

            self.assertEqual("legacy", outputs[0]["plan_mode"])
            self.assertEqual("planned", outputs[1]["plan_mode"])
            self.assertEqual(SEARCH_EXECUTION_SCHEMA, outputs[1]["schema"])
            self.assertEqual(SEARCH_RESULT_SCHEMA, outputs[1]["result"]["schema"])
            self.assertRegex(outputs[1]["result"]["plan_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(before, repository_bytes(root))
            self.assertFalse(any(root.rglob("*.sqlite")))

    def test_context_preserves_original_question_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)
            plan = retrieval_plan()
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            before = repository_bytes(root)
            legacy_question = "why does measure need sigma algebra?"

            legacy_status, legacy_output, legacy_error = self.run_cli(
                root, "agent", "context", legacy_question, "--budget", "2048"
            )
            plan_status, plan_output, plan_error = self.run_cli(
                root,
                "agent",
                "context",
                "--plan",
                str(plan_path),
                "--budget",
                "2048",
            )

            self.assertEqual(0, legacy_status, legacy_error)
            self.assertEqual(0, plan_status, plan_error)
            self.assertEqual(legacy_question, json.loads(legacy_output)["question"])
            self.assertEqual(plan["question"], json.loads(plan_output)["question"])
            self.assertEqual(before, repository_bytes(root))

    def test_status_reports_json_memory_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_fixture_graph(root)

            status, output, error = self.run_cli(root, "agent", "status")

            self.assertEqual(0, status, error)
            payload = json.loads(output)
            self.assertEqual("json-memory", payload["backend"])
            self.assertIn("read-only-query-v3", payload["capabilities"])


if __name__ == "__main__":
    unittest.main()
