from __future__ import annotations

import unittest

from kgdistiller.tokens import tokenize


class TokenizeTest(unittest.TestCase):
    def test_ascii_words_are_normalized_unicode_words(self) -> None:
        self.assertEqual(["cross", "entropy", "σ", "algebra"], tokenize("Ｃｒｏｓｓ-Entropy σ-algebra"))
        self.assertEqual(["l2", "space"], tokenize("L2_space"))

    def test_cjk_runs_yield_unigrams_and_bigrams(self) -> None:
        self.assertEqual(["测", "度", "论", "测度", "度论"], tokenize("测度论"))
        self.assertEqual(["borel", "测", "度", "测度"], tokenize("Borel测度"))

    def test_sub_word_query_tokens_are_contained_in_longer_terms(self) -> None:
        document = set(tokenize("测度论研究可测空间"))
        for query in ("测度", "度论", "可测"):
            with self.subTest(query=query):
                self.assertTrue(set(tokenize(query)) <= document)


if __name__ == "__main__":
    unittest.main()
