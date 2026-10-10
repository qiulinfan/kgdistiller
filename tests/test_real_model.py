"""Opt-in smoke test of the dense lane with a real sentence-transformers model.

Skipped unless KGD_TEST_EMBEDDING_MODEL names a model that is already in the
Hugging Face cache. Run it with an interpreter that has the retrieval extra
and with network access forbidden, for example the uv tool interpreter:

    HF_HUB_OFFLINE=1 KGD_TEST_EMBEDDING_MODEL=BAAI/bge-m3 \\
        ~/.local/share/uv/tools/kgdistiller/bin/python -m unittest discover -s tests -p test_real_model.py

It never runs in CI or in the default suite.
"""

from __future__ import annotations

import os
import sqlite3
import unittest

from kgdistiller.index import database_path, index
from kgdistiller.retrieve import search
from tests.knowledge_fixture import make_record_home

MODEL = os.environ.get("KGD_TEST_EMBEDDING_MODEL")
SOURCE = "Chapter one\nA measure space is a triple (X, F, mu).\nA measure is countably additive.\n测度论研究可测空间。\n"


def node(label: str, lines: str, extra: str = "") -> str:
    return f"label: {label}\nkind: definition\nsource: notes/a.txt\nlines: {lines}\n{extra}".rstrip("\n")


@unittest.skipUnless(MODEL, "set KGD_TEST_EMBEDDING_MODEL to run the real-model smoke test")
class RealModelTest(unittest.TestCase):
    def test_index_and_search_with_the_dense_lane(self) -> None:
        import numpy

        kb = make_record_home(self, embedding=MODEL)
        kb.write_source("notes/a.txt", SOURCE)
        kb.write_record("measure-space", node("Measure space", "2", "aliases: [测度空间]"),
                        "A set with a sigma-algebra and a countably additive measure.", ["A measure space"])
        kb.write_record("measure", node("Measure", "3", "aliases: [测度]"),
                        "A countably additive set function.", ["A measure is"])
        kb.write_record("chapter", node("Chapter one", "1"), "The opening heading.", ["Chapter one"])

        report = index()
        self.assertEqual((3, 0, []), (report["embedded"], report["unembedded"], report["truncated"]))

        connection = sqlite3.connect(database_path())
        self.addCleanup(connection.close)
        vectors = [row[0] for row in connection.execute("SELECT vec FROM record ORDER BY uid")]
        self.assertEqual(3, len(vectors))
        self.assertTrue(all(isinstance(vector, bytes) for vector in vectors))
        self.assertEqual(1, len({len(vector) for vector in vectors}))
        for vector in vectors:
            norm = float(numpy.linalg.norm(numpy.frombuffer(vector, dtype="<f4")))
            self.assertAlmostEqual(1.0, norm, delta=1e-3)

        found = search("measure space")
        self.assertIn("dense", found["lanes"])
        ranks = {item["uid"]: item["ranks"] for item in found["results"]}
        self.assertIsNotNone(ranks["kb:measure-space"]["dense"])


if __name__ == "__main__":
    unittest.main()
