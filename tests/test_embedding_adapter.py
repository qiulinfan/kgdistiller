"""The int8 encoder, run on a tiny random XLM-RoBERTa and a sentencepiece model trained here; nothing is downloaded.

Skipped unless PyTorch, transformers and sentencepiece are installed (the
``retrieval`` extra).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy  # noqa: F401 - imported before any test hides modules, since NumPy cannot be imported twice

from kgdistiller.adapters import embedding
from kgdistiller.adapters.embedding import (
    EmbeddingError,
    Encoder,
    RetrievalExtraMissing,
)

EXTRA = all(importlib.util.find_spec(name) for name in ("torch", "transformers", "sentencepiece"))
CORPUS = [
    "A measure space is a triple of a set, a sigma-algebra and a measure.",
    "A measure is a countably additive set function on a sigma-algebra.",
    "测度论研究可测空间与测度。",
    "An autoencoder learns to reconstruct its input through a latent code.",
] * 20


@unittest.skipUnless(EXTRA, "install the retrieval extra to test the int8 encoder")
class EncoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sentencepiece
        import torch
        from transformers import XLMRobertaConfig, XLMRobertaModel

        cls.directory = Path(tempfile.mkdtemp(prefix="kgd-encoder-"))
        (cls.directory / "corpus.txt").write_text("\n".join(CORPUS), encoding="utf-8")
        sentencepiece.SentencePieceTrainer.train(
            input=str(cls.directory / "corpus.txt"), model_prefix=str(cls.directory / "sentencepiece.bpe"),
            vocab_size=60, character_coverage=1.0, minloglevel=2,
        )
        pieces = sentencepiece.SentencePieceProcessor(model_file=str(cls.directory / "sentencepiece.bpe.model"))
        torch.manual_seed(0)
        config = XLMRobertaConfig(
            vocab_size=pieces.get_piece_size() + 2, hidden_size=32, num_hidden_layers=2, num_attention_heads=2,
            intermediate_size=64, max_position_embeddings=130, pad_token_id=1, bos_token_id=0, eos_token_id=2,
        )
        cls.reference = XLMRobertaModel(config, add_pooling_layer=False).eval()
        cls.reference.save_pretrained(cls.directory)
        (cls.directory / "1_Pooling").mkdir()
        cls.pooling("cls")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, ignore_errors=True)

    @classmethod
    def pooling(cls, mode: str) -> None:
        (cls.directory / "1_Pooling/config.json").write_text(json.dumps({
            "pooling_mode_cls_token": mode == "cls", "pooling_mode_mean_tokens": mode == "mean",
        }), encoding="utf-8")

    def setUp(self) -> None:
        def download(model: str, name: str, required: bool = True) -> Path | None:
            path = self.directory / name
            if path.is_file():
                return path
            if required:
                raise FileNotFoundError(name)
            return None

        patcher = patch.object(embedding, "_download", download)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.pooling, "cls")

    def reference_vector(self, text: str, cls: bool = True) -> list[float]:
        import torch

        encoder = Encoder("tiny/model")
        ids = encoder._ids("", text)
        with torch.inference_mode():
            hidden = self.reference(input_ids=torch.tensor([ids])).last_hidden_state[0]
        pooled = hidden[0] if cls else hidden.mean(0)
        return torch.nn.functional.normalize(pooled, dim=-1).tolist()

    def vector(self, data: bytes) -> list[float]:
        import struct

        return list(struct.unpack(f"<{len(data) // 4}f", data))

    def assert_close(self, expected: list[float], actual: list[float]) -> None:
        cosine = sum(a * b for a, b in zip(expected, actual, strict=True))
        self.assertGreater(cosine, 0.99)
        self.assertAlmostEqual(1.0, sum(value * value for value in actual), delta=1e-3)

    def test_vectors_match_the_float_model_with_int8_weights(self) -> None:
        import torch

        encoder = Encoder("tiny/model")
        linear = encoder._network.encoder.layer[0].attention.self.query
        table = encoder._network.embeddings.word_embeddings
        self.assertEqual((torch.int8, torch.int8), (linear.values.dtype, table.values.dtype))
        self.assertEqual("cpu", table.values.device.type)
        for text in CORPUS[:4]:
            with self.subTest(text=text):
                self.assert_close(self.reference_vector(text), self.vector(encoder.encode_query(text)))
        documents = encoder.encode_documents(CORPUS[:4])
        self.assertEqual(4, len(documents))
        self.assertEqual(documents[1], encoder.encode_documents([CORPUS[1]])[0])

    def test_mean_pooling(self) -> None:
        self.pooling("mean")
        encoder = Encoder("tiny/model")
        self.assert_close(self.reference_vector(CORPUS[0], cls=False), self.vector(encoder.encode_query(CORPUS[0])))

    def test_texts_over_the_limit_are_reported_and_still_encoded(self) -> None:
        encoder = Encoder("tiny/model")
        self.assertEqual(128, encoder.max_tokens)
        long = " ".join(CORPUS[:4])
        self.assertEqual([1], encoder.over_limit(["short", long]))
        self.assertEqual(1, len(encoder.encode_documents([long])))

    def test_tokens_follow_xlm_roberta_ids(self) -> None:
        encoder = Encoder("tiny/model")
        ids = encoder._ids("", "measure")
        self.assertEqual((0, 2), (ids[0], ids[-1]))
        self.assertTrue(all(token >= 3 for token in ids[1:-1]))

    def test_other_architectures_are_refused(self) -> None:
        config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
        original = dict(config)
        config["model_type"] = "bert"
        (self.directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
        self.addCleanup((self.directory / "config.json").write_text, json.dumps(original), "utf-8")
        with self.assertRaisesRegex(EmbeddingError, "XLM-RoBERTa"):
            Encoder("tiny/model")

    def test_describe_checks_a_model_without_loading_it(self) -> None:
        self.assertEqual({"model": "tiny/model", "pooling": "cls", "weights": "model.safetensors", "precision": "int8"},
                         embedding.describe("tiny/model").report())

    def test_another_architecture_is_refused_before_its_weights_download(self) -> None:
        config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
        original = dict(config)
        config["model_type"] = "bert"
        (self.directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
        self.addCleanup((self.directory / "config.json").write_text, json.dumps(original), "utf-8")
        fetched: list[str] = []
        download = embedding._download

        def counting(model: str, name: str, required: bool = True) -> Path | None:
            fetched.append(name)
            return download(model, name, required)

        with patch.object(embedding, "_download", counting), self.assertRaisesRegex(EmbeddingError, "XLM-RoBERTa"):
            embedding.describe("tiny/model")
        self.assertEqual(["config.json"], fetched)

    def test_unsupported_pooling_is_refused(self) -> None:
        (self.directory / "1_Pooling/config.json").write_text(json.dumps({"pooling_mode_max_tokens": True}),
                                                               encoding="utf-8")
        with self.assertRaisesRegex(EmbeddingError, "CLS or mean"):
            Encoder("tiny/model")


class WithoutExtraTest(unittest.TestCase):
    def test_missing_extra(self) -> None:
        with patch.dict(sys.modules, {"sentencepiece": None}), self.assertRaises(RetrievalExtraMissing):
            Encoder("some/model")

    def test_identity_records_the_precision(self) -> None:
        self.assertEqual("BAAI/bge-m3 int8", embedding.identity("BAAI/bge-m3"))
        self.assertEqual("BAAI/bge-m3", embedding.model_of("BAAI/bge-m3 int8"))
        self.assertEqual("BAAI/bge-m3", embedding.model_of("BAAI/bge-m3"))

    def test_encoder_returns_a_client_and_loads_nothing(self) -> None:
        with patch.dict(sys.modules, {"torch": None}):
            client = embedding.encoder("some/model")
        self.assertEqual("some/model", client.model)


if __name__ == "__main__":
    unittest.main()
