"""Adapter boundaries are tested without importing or downloading model runtimes."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kgdistiller.adapters.sentence_transformers import SentenceTransformersAdapter
from kgdistiller.semantic_retrieval import SemanticRetrievalError


class Array:
    def __init__(self, value): self.value = value
    def tolist(self): return self.value


class Tokenizer:
    model_max_length = 8192
    def __call__(self, first, second=None, **kwargs):
        assert kwargs.get("truncation") is False
        assert kwargs.get("add_special_tokens") is True
        return {"input_ids": [0] * (len(first.split()) + (len(second.split()) if second else 0) + 2)}


class Embedder:
    def __init__(self, *args, **kwargs):
        self.max_seq_length = 8192
        self.prompts = {}
        self.tokenizer = Tokenizer()
        self.calls = []
        self.settings = kwargs
    def encode_document(self, texts, **kwargs):
        self.calls.append(("documents", texts, kwargs))
        return Array([[1., 0.] for _ in texts])
    def encode_query(self, texts, **kwargs):
        self.calls.append(("queries", texts, kwargs))
        return Array([[0., 1.] for _ in texts])


class Reranker:
    def __init__(self, *args, **kwargs):
        self.settings = kwargs
        self.tokenizer = Tokenizer()
        self.model = SimpleNamespace(config=SimpleNamespace(max_position_embeddings=8192))
        self.calls = []
    def predict(self, pairs, **kwargs):
        self.calls.append((pairs, kwargs))
        return Array([-3. if i == 0 else 4. for i in range(len(pairs))])


class AdapterTest(unittest.TestCase):
    def setUp(self):
        torch = SimpleNamespace(float32="float32", __version__="test",
                                backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
                                cuda=SimpleNamespace(is_available=lambda: False),
                                nn=SimpleNamespace(Identity=lambda: "identity"))
        self.modules = patch.dict("sys.modules", {"torch": torch,
                                    "sentence_transformers": SimpleNamespace(SentenceTransformer=Embedder,
                                                                             CrossEncoder=Reranker)})
        self.versions = patch("kgdistiller.adapters.sentence_transformers.version", return_value="test")
        self.modules.start(); self.versions.start()
        self.addCleanup(self.modules.stop); self.addCleanup(self.versions.stop)

    def test_load_is_explicit_lazy_and_revisions_are_bound(self):
        adapter = SentenceTransformersAdapter(batch_size=1, local_files_only=True)
        self.assertIsNone(adapter._embedder)
        self.assertIsNone(adapter._reranker)
        before = adapter.metadata("embedding")
        adapter.encode_documents(["one document"])
        adapter.encode_queries(["a question"])
        self.assertEqual(before, adapter.metadata("embedding"))
        settings = adapter._embedder.settings
        self.assertFalse(settings["trust_remote_code"])
        self.assertFalse(settings["token"])
        self.assertTrue(settings["local_files_only"])
        self.assertEqual(before["revision"], settings["revision"])
        self.assertEqual(["documents", "queries"], [c[0] for c in adapter._embedder.calls])

    def test_late_document_content_is_not_silently_truncated(self):
        adapter = SentenceTransformersAdapter(max_length=5)
        with self.assertRaisesRegex(SemanticRetrievalError, "model-input-too-long"):
            adapter.encode_documents(["one two three four late-condition"])
        self.assertEqual([], adapter._embedder.calls)

    def test_reranker_bounds_joint_query_document_input_and_keeps_logits(self):
        adapter = SentenceTransformersAdapter(max_length=7)
        scores = adapter.score_pairs("query", ["first doc", "second doc"])
        self.assertEqual([-3., 4.], scores)
        self.assertEqual("raw-logit", adapter.metadata("reranker")["inference"]["activation"])
        self.assertEqual("identity", adapter._reranker.calls[0][1]["activation_fn"])
        with self.assertRaisesRegex(SemanticRetrievalError, "model-input-too-long"):
            adapter.score_pairs("one two three", ["four five six seven"])
        self.assertEqual(1, len(adapter._reranker.calls))

    def test_device_error_never_silently_switches_to_cpu(self):
        with self.assertRaisesRegex(SemanticRetrievalError, "model-device-unavailable"):
            SentenceTransformersAdapter(device="mps")

    def test_revision_configured_passage_prompt_counts_toward_input_limit(self):
        adapter = SentenceTransformersAdapter(max_length=6)
        model = adapter._load_embedding()
        model.prompts = {"passage": "one two three "}
        with self.assertRaisesRegex(SemanticRetrievalError, "model-input-too-long"):
            adapter.encode_documents(["four five"])
        self.assertEqual([], model.calls)

    def test_implicit_reranker_template_cannot_bypass_pair_length_check(self):
        adapter = SentenceTransformersAdapter()
        model = adapter._load_reranker()
        model.default_prompt_name = "unaccounted-template"
        with self.assertRaisesRegex(SemanticRetrievalError, "model-input-configuration"):
            adapter.score_pairs("question", ["document"])
        self.assertEqual([], model.calls)

    def test_bad_settings_and_blank_inputs_fail_explicitly(self):
        for kwargs in [{"batch_size": True}, {"max_length": 0}, {"revision": ""}, {"revision": "main"},
                       {"reranker_revision": "latest"},
                       {"reranker_revision": ""}, {"device": "auto"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(SemanticRetrievalError):
                SentenceTransformersAdapter(**kwargs)
        adapter = SentenceTransformersAdapter()
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-model-input"):
            adapter.encode_queries([""])
        with self.assertRaisesRegex(SemanticRetrievalError, "invalid-model-input"):
            adapter.score_pairs("", ["doc"])


if __name__ == "__main__": unittest.main()
