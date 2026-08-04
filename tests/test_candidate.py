from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kgdistiller.candidate import CandidateError, build_candidate_snapshot


REPO_ROOT = Path(__file__).resolve().parents[1]


def candidate_graph() -> dict:
    return {
        "schema": "qlkg-candidate-graph-v1",
        "namespace": "paper:fixture",
        "nodes": [
            {
                "id": "second-concept",
                "type": "knowledge",
                "label": "Second concept",
                "text": "A concept derived in Section 2.",
                "properties": {"aliases": ["SC"]},
                "provenance": {
                    "authority": "paper.tex",
                    "section": "2",
                    "line": 40,
                    "source_format": "latex",
                },
            },
            {
                "id": "first-concept",
                "type": "knowledge",
                "label": "First concept",
                "text": "A prerequisite defined in Section 1.",
                "properties": {"aliases": []},
                "provenance": {
                    "authority": "paper.tex",
                    "section": "1",
                    "line": 12,
                    "source_format": "latex",
                },
            },
        ],
        "edges": [
            {
                "source": "first-concept",
                "relation": "prerequisite-for",
                "target": "second-concept",
                "origin": "paper-extraction",
                "confidence": "high",
                "evidence": "Section 2 explicitly assumes the first concept.",
            }
        ],
        "references": [
            {
                "id": "paper-ref-1",
                "target": "first-concept",
                "authority": "paper.tex",
                "line": 42,
                "context": "Uses the first concept.",
                "source_format": "latex",
            }
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }


class CandidateBuilderTest(unittest.TestCase):
    def test_builder_is_deterministic_and_sorts_records(self) -> None:
        first = build_candidate_snapshot(candidate_graph())
        second = build_candidate_snapshot(candidate_graph())

        self.assertEqual(first, second)
        self.assertEqual("qlkg-agent-snapshot-v1", first["schema"])
        self.assertEqual(
            ["first-concept", "second-concept"],
            [node["id"] for node in first["nodes"]],
        )
        self.assertRegex(first["snapshot_sha256"], r"^[0-9a-f]{64}$")

    def test_builder_rejects_personal_namespace_dangling_edges_and_missing_locations(self) -> None:
        personal = candidate_graph()
        personal["namespace"] = "personal"
        with self.assertRaisesRegex(CandidateError, "forbidden"):
            build_candidate_snapshot(personal)

        dangling = candidate_graph()
        dangling["edges"][0]["target"] = "missing"
        with self.assertRaisesRegex(CandidateError, "dangling"):
            build_candidate_snapshot(dangling)

        ungrounded = candidate_graph()
        ungrounded["nodes"][0]["provenance"] = {"authority": "paper.tex"}
        with self.assertRaisesRegex(CandidateError, "bounded source location"):
            build_candidate_snapshot(ungrounded)

    def test_candidate_cli_builds_and_validates_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-candidate-test-") as temporary:
            root = Path(temporary)
            source = root / "candidate.json"
            output = root / "candidate.snapshot.json"
            source.write_text(json.dumps(candidate_graph()), encoding="utf-8")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(REPO_ROOT / "src")

            built = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kgdistiller",
                    "--repo-root",
                    str(root),
                    "candidate",
                    "build",
                    str(source),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, built.returncode, built.stderr)
            self.assertTrue(output.is_file())

            validated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kgdistiller",
                    "--repo-root",
                    str(root),
                    "candidate",
                    "validate",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, validated.returncode, validated.stderr)
            self.assertEqual(
                "qlkg-agent-snapshot-v1", json.loads(validated.stdout)["schema"]
            )


if __name__ == "__main__":
    unittest.main()
