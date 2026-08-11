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
from kgdistiller.agent import write_agent_index  # noqa: E402
from tests.test_agent import fixture_snapshot  # noqa: E402


def retrieval_plan() -> dict:
    return {
        "schema": "qlkg-retrieval-plan-v1",
        "question": "How does beta depend on alpha?",
        "namespace": "personal",
        "identity_queries": ["alpha"],
        "lexical_queries": ["countable closure"],
        "semantic_queries": [],
        "graph": {
            "seed_ids": ["alpha"],
            "edge_types": ["prerequisite-for"],
            "direction": "out",
            "max_depth": 1,
            "strategy": "hybrid",
        },
        "filters": {
            "node_types": ["knowledge"],
            "include_stale": False,
            "include_orphaned": False,
        },
        "limit": 20,
    }


def write_authority_graph(root: Path) -> Path:
    graph = root / "knowledge" / "graph"
    graph.mkdir(parents=True)
    (graph / "manifest.json").write_text(
        json.dumps({"schema": "qlkg-v2", "graph_sha256": "a" * 64}),
        encoding="utf-8",
    )
    for name in ("nodes.jsonl", "edges.jsonl", "references.jsonl"):
        (graph / name).write_text("", encoding="utf-8")
    return graph


class RetrievalCliParserTest(unittest.TestCase):
    def parse(self, *arguments: str):
        with patch.object(sys, "argv", ["kgdistiller", *arguments]):
            return parse_args()

    def assert_parse_error(self, *arguments: str) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.parse(*arguments)
        self.assertEqual(2, raised.exception.code)

    def test_agent_search_accepts_exactly_one_query_or_plan(self) -> None:
        legacy = self.parse("agent", "search", "countable closure")
        planned = self.parse("agent", "search", "--plan", "retrieval.json")

        self.assertEqual("countable closure", legacy.query)
        self.assertIsNone(legacy.plan)
        self.assertIsNone(planned.query)
        self.assertEqual(Path("retrieval.json"), planned.plan)
        self.assert_parse_error("agent", "search")
        self.assert_parse_error(
            "agent",
            "search",
            "countable closure",
            "--plan",
            "retrieval.json",
        )
        self.assert_parse_error(
            "agent",
            "search",
            "--plan",
            "retrieval.json",
            "--namespace",
            "paper:fixture",
        )

    def test_agent_context_accepts_exactly_one_query_or_plan(self) -> None:
        legacy = self.parse(
            "agent",
            "context",
            "why alpha",
            "--namespace",
            "paper:fixture",
        )
        planned = self.parse(
            "agent",
            "context",
            "--plan",
            "retrieval.json",
            "--budget",
            "2048",
        )

        self.assertEqual("paper:fixture", legacy.namespace)
        self.assertEqual(2048, planned.budget)
        self.assert_parse_error("agent", "context")
        self.assert_parse_error(
            "agent", "context", "why alpha", "--plan", "retrieval.json"
        )

    def test_missing_index_query_never_bootstraps_a_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_authority_graph(root)
            database = root / "runtime" / "missing.sqlite"
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            with patch.object(
                sys,
                "argv",
                [
                    "kgdistiller",
                    "--repo-root",
                    str(root),
                    "--database",
                    str(database),
                    "agent",
                    "search",
                    "alpha",
                ],
            ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                status = main()

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(1, status)
            self.assertFalse(database.exists())
            self.assertEqual(before, after)

    def test_legacy_and_planned_search_execute_without_rewriting_the_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_authority_graph(root)
            database = root / "runtime" / "knowledge.sqlite"
            write_agent_index(database, fixture_snapshot())
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(retrieval_plan()), encoding="utf-8")
            before = {
                path.name: path.read_bytes()
                for path in database.parent.glob(f"{database.name}*")
                if path.is_file()
            }

            outputs = []
            long_legacy_query = "alpha " + ("q" * 2044)
            for arguments in (
                ["agent", "search", "countable closure"],
                ["agent", "search", long_legacy_query],
                ["agent", "search", "--plan", str(plan_path)],
            ):
                stdout = io.StringIO()
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kgdistiller",
                        "--repo-root",
                        str(root),
                        "--database",
                        str(database),
                        *arguments,
                    ],
                ), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    status = main()
                self.assertEqual(0, status)
                outputs.append(json.loads(stdout.getvalue()))

            after = {
                path.name: path.read_bytes()
                for path in database.parent.glob(f"{database.name}*")
                if path.is_file()
            }
            self.assertEqual("legacy", outputs[0]["plan_mode"])
            self.assertEqual("legacy", outputs[1]["plan_mode"])
            self.assertEqual("planned", outputs[2]["plan_mode"])
            self.assertEqual("qlkg-search-result-v2", outputs[2]["result"]["schema"])
            self.assertEqual(before, after)

    def test_legacy_and_planned_context_preserve_the_original_question(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-retrieval-cli-") as raw:
            root = Path(raw)
            write_authority_graph(root)
            database = root / "runtime" / "knowledge.sqlite"
            write_agent_index(database, fixture_snapshot())
            plan = retrieval_plan()
            plan_path = root / "retrieval.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            legacy_query = "alpha " + ("q" * 2044)

            outputs = []
            for arguments in (
                ["agent", "context", legacy_query],
                ["agent", "context", "--plan", str(plan_path)],
            ):
                stdout = io.StringIO()
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kgdistiller",
                        "--repo-root",
                        str(root),
                        "--database",
                        str(database),
                        *arguments,
                    ],
                ), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    status = main()
                self.assertEqual(0, status)
                outputs.append(json.loads(stdout.getvalue()))

            self.assertEqual(legacy_query, outputs[0]["query"])
            self.assertEqual(plan["question"], outputs[1]["query"])


if __name__ == "__main__":
    unittest.main()
