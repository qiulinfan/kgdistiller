"""The encoder adapter, tested against a fake ``sentence_transformers`` module; no model runtime is imported."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

import numpy

from kgdistiller.adapters import sentence_transformers as adapter
from kgdistiller.home import KnowledgeError

DIMENSION = 3


class Tokenizer:
    def __call__(self, text: str, **kwargs: Any) -> dict[str, list[int]]:
        assert kwargs == {"truncation": False, "add_special_tokens": True}, kwargs
        return {"input_ids": [0] * (len(text.split()) + 2)}


class FakeModel:
    constructed: ClassVar[list[tuple[tuple[Any, ...], dict[str, Any]]]] = []
    fail_load = False
    fail_encode = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if FakeModel.fail_load:
            raise OSError("no such model in the cache")
        FakeModel.constructed.append((args, kwargs))
        self.prompts: dict[str, str] = {}
        self.default_prompt_name = None
        self.max_seq_length = 8
        self.tokenizer = Tokenizer()
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def _rows(self, kind: str, texts: list[str], kwargs: dict[str, Any]) -> numpy.ndarray:
        if FakeModel.fail_encode:
            raise RuntimeError("device lost")
        self.calls.append((kind, texts, kwargs))
        rows = [[float(len(text)), 1.0, 0.5] for text in texts]
        return numpy.asarray(rows, dtype=numpy.float32)

    def encode_document(self, texts: list[str], **kwargs: Any) -> numpy.ndarray:
        return self._rows("document", texts, kwargs)

    def encode_query(self, texts: list[str], **kwargs: Any) -> numpy.ndarray:
        return self._rows("query", texts, kwargs)


class EncoderTest(unittest.TestCase):
    def setUp(self) -> None:
        adapter._resident = None
        FakeModel.constructed = []
        FakeModel.fail_load = False
        FakeModel.fail_encode = False
        modules = patch.dict(sys.modules, {"sentence_transformers": SimpleNamespace(SentenceTransformer=FakeModel)})
        modules.start()
        self.addCleanup(modules.stop)

    def tearDown(self) -> None:
        adapter._resident = None

    def test_load_takes_the_model_id_only(self) -> None:
        encoder = adapter.encoder("some/model")
        self.assertEqual([(("some/model",), {"trust_remote_code": False, "token": False})], FakeModel.constructed)
        self.assertEqual(("some/model", 8), (encoder.model, encoder.max_tokens))

    def test_hugging_face_progress_bars_are_off_unless_set(self) -> None:
        for preset, expected in ((None, "1"), ("0", "0")):
            environment = {} if preset is None else {"HF_HUB_DISABLE_PROGRESS_BARS": preset}
            with self.subTest(preset=preset), patch.dict(os.environ, environment):
                if preset is None:
                    os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)
                adapter.Encoder("some/model")
                self.assertEqual(expected, os.environ["HF_HUB_DISABLE_PROGRESS_BARS"])

    def test_documents_and_queries_become_little_endian_float32_bytes(self) -> None:
        encoder = adapter.encoder("some/model")
        vectors = encoder.encode_documents(["one", "three words here"])
        self.assertEqual(2, len(vectors))
        for vector in vectors:
            self.assertIsInstance(vector, bytes)
            self.assertEqual(4 * DIMENSION, len(vector))
        self.assertEqual([16.0, 1.0, 0.5], numpy.frombuffer(vectors[1], dtype="<f4").tolist())
        query = encoder.encode_query("a question")
        self.assertEqual([10.0, 1.0, 0.5], numpy.frombuffer(query, dtype="<f4").tolist())
        loaded = encoder._loaded
        self.assertEqual(["document", "query"], [call[0] for call in loaded.calls])
        for _, _, kwargs in loaded.calls:
            self.assertEqual({"normalize_embeddings": True, "convert_to_numpy": True, "show_progress_bar": False}, kwargs)

    def test_over_limit_reports_positions_without_raising(self) -> None:
        encoder = adapter.encoder("some/model")
        texts = ["short text", "one two three four five six seven", "a b c d e f"]
        self.assertEqual([1], encoder.over_limit(texts))
        encoder._loaded.prompts = {"query": "ignored ignored ignored ", "passage": "p1 p2 "}
        self.assertEqual([1, 2], encoder.over_limit(texts))

    def test_encoder_is_resident_per_model_id(self) -> None:
        first = adapter.encoder("some/model")
        self.assertIs(first, adapter.encoder("some/model"))
        self.assertEqual(1, len(FakeModel.constructed))
        second = adapter.encoder("other/model")
        self.assertIsNot(first, second)
        self.assertEqual(2, len(FakeModel.constructed))
        self.assertIs(second, adapter._resident)
        self.assertIs(second, adapter.encoder("other/model"))
        self.assertEqual(2, len(FakeModel.constructed))

    def test_missing_extra(self) -> None:
        with (patch.dict(sys.modules, {"sentence_transformers": None}),
              self.assertRaises(adapter.RetrievalExtraMissing) as caught):
            adapter.encoder("some/model")
        self.assertIsInstance(caught.exception, KnowledgeError)
        self.assertIn("install kgdistiller[retrieval]", str(caught.exception))
        self.assertIsNone(adapter._resident)

    def test_load_failure_is_an_embedding_error(self) -> None:
        FakeModel.fail_load = True
        with self.assertRaises(adapter.EmbeddingError) as caught:
            adapter.encoder("some/model")
        self.assertNotIsInstance(caught.exception, adapter.RetrievalExtraMissing)
        self.assertIn("cannot load embedding model some/model", str(caught.exception))
        self.assertIsNone(adapter._resident)

    def test_inference_failure_is_an_embedding_error(self) -> None:
        encoder = adapter.encoder("some/model")
        FakeModel.fail_encode = True
        for call in (lambda: encoder.encode_documents(["text"]), lambda: encoder.encode_query("text")):
            with self.subTest(call=call), self.assertRaises(adapter.EmbeddingError) as caught:
                call()
            self.assertIn("embedding with some/model failed: device lost", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
