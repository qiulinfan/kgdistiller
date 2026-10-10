from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from kgdistiller import cli
from kgdistiller.document_types import load_document_types, validate_node_kind
from kgdistiller.knowledge_store import load_state, validate
from kgdistiller.sources import KnowledgeError, load_sources
from tests.knowledge_fixture import make_fixture

PROFILE = {
    "node_kinds": ["construction", "measurement protocol"],
    "extraction_guidance": "Extract explained constructions. Leave unexplained terms pending.\nObservations are relations.",
}


class DocumentTypesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = make_fixture(
            self,
            sources=[{"id": "reading", "root": "sources", "files": ["*"], "document_type": "实验日志"}],
            document_types={"实验日志": PROFILE},
        )
        self.registry = self.fixture.registry

    def scan(self, *relative: str) -> dict:
        output = io.StringIO()
        args = ["kgdistiller", "--repo-root", str(self.fixture.root), "scan"]
        for path in relative:
            args += ["--file", path]
        with patch("sys.argv", args), redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        return json.loads(output.getvalue())

    def test_profile_applies_identically_to_every_text_format(self) -> None:
        names = ("text.md", "native.typ", "formula.tex", "plain.txt", "data.unusual")
        for name in names:
            self.fixture.write_source(f"sources/{name}", "First line.\n\\kn{not a marker}\n")
        result = self.scan(*(f"sources/{name}" for name in names))
        self.assertEqual([item["path"] for item in result["files"]], [f"sources/{name}" for name in names])
        for item in result["files"]:
            with self.subTest(path=item["path"]):
                self.assertEqual(item["source_id"], "reading")
                self.assertEqual(item["document_type"], "实验日志")
                self.assertEqual(item["profile"], PROFILE)
                self.assertEqual(item["lines"], [
                    {"line": 1, "text": "First line."},
                    {"line": 2, "text": "\\kn{not a marker}"},
                ])
        self.assertEqual(load_document_types(self.registry), {"实验日志": PROFILE})

    def test_kind_is_validated_against_the_source_profile(self) -> None:
        self.fixture.write_source("sources/log.txt", "A construction is described here.\n")
        self.fixture.add_entry("allowed", "Allowed", "sources/log.txt", 1, kind="construction")
        self.fixture.add_entry("rejected", "Rejected", "sources/log.txt", 1, kind="theorem")
        report = validate(load_state(self.fixture.root), self.fixture.root, self.registry)
        self.assertEqual([(item["code"], item["entry"]) for item in report["errors"]],
                         [("kind-not-allowed", "rejected")])

    def test_omitted_profile_allows_any_single_line_kind(self) -> None:
        self.fixture.sources[0].pop("document_type")
        self.fixture.document_types = None
        self.fixture.write_registry()
        self.assertEqual(load_document_types(self.registry), {})
        self.assertEqual(load_sources(self.fixture.root, self.registry)[0].document_type, "")
        self.assertEqual(validate_node_kind("unlisted semantic object", "", {}), "unlisted semantic object")
        self.fixture.write_source("sources/log.txt", "Anything.\n")
        self.assertIsNone(self.scan("sources/log.txt")["files"][0]["profile"])

    def test_rejects_unknown_and_malformed_explicit_classifications(self) -> None:
        for value in ("missing", "", None, 7, ["实验日志"]):
            with self.subTest(value=value):
                self.fixture.sources[0]["document_type"] = value
                self.fixture.write_registry()
                with self.assertRaises(KnowledgeError):
                    load_sources(self.fixture.root, self.registry)

    def test_rejects_malformed_profiles(self) -> None:
        variants = [None, [], {"": PROFILE}, {"x": {}},
                    {"x": {**PROFILE, "node_kinds": []}},
                    {"x": {**PROFILE, "node_kinds": ["a", "a"]}},
                    {"x": {**PROFILE, "node_kinds": ["a\nb"]}},
                    {"x": {**PROFILE, "node_kinds": [3]}},
                    {"x": {**PROFILE, "extraction_guidance": " "}},
                    {"x": {**PROFILE, "typo": "ignored?"}}]
        for profiles in variants:
            with self.subTest(profiles=profiles):
                self.registry.write_text(json.dumps({
                    "schema": "kgdistiller-sources-v1", "sources": [], "document_types": profiles,
                }), encoding="utf-8")
                with self.assertRaises(KnowledgeError):
                    load_document_types(self.registry)


if __name__ == "__main__":
    unittest.main()
