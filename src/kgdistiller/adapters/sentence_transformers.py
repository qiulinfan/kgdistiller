"""Explicit local embedding inference through the optional retrieval extra."""
from __future__ import annotations

import re
from importlib.metadata import version
from typing import Any

from ..semantic_retrieval import SemanticRetrievalError

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


class SentenceTransformersAdapter:
    """Models are loaded only when explicitly selected; input is never truncated."""

    def __init__(self, *, model: str = DEFAULT_EMBEDDING_MODEL,
                 revision: str = DEFAULT_EMBEDDING_REVISION, device: str = "cpu",
                 max_length: int = 8192, batch_size: int = 4,
                 local_files_only: bool = False,
                 reranker_model: str = DEFAULT_RERANKER_MODEL,
                 reranker_revision: str = DEFAULT_RERANKER_REVISION):
        if (not isinstance(model, str) or not model.strip() or len(model) > 256
                or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
            raise SemanticRetrievalError("invalid-model-settings", "model and immutable revision are required")
        if (not isinstance(reranker_model, str) or not reranker_model.strip() or len(reranker_model) > 256
                or not isinstance(reranker_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", reranker_revision)):
            raise SemanticRetrievalError("invalid-model-settings", "reranker model and immutable revision are required")
        if (not isinstance(max_length, int) or isinstance(max_length, bool) or not 1 <= max_length <= 8192
                or not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 64
                or device not in {"cpu", "mps", "cuda"}):
            raise SemanticRetrievalError("invalid-model-settings", "invalid device, input length or batch size")
        try:
            import torch
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except ImportError as error:
            raise SemanticRetrievalError(
                "model-dependency-missing", "install kgdistiller[retrieval]"
            ) from error
        if device == "mps" and not torch.backends.mps.is_available():
            raise SemanticRetrievalError("model-device-unavailable", "MPS was requested but is unavailable")
        if device == "cuda" and not torch.cuda.is_available():
            raise SemanticRetrievalError("model-device-unavailable", "CUDA was requested but is unavailable")
        self.model = model
        self.revision = revision
        self.reranker_model = reranker_model
        self.reranker_revision = reranker_revision
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self.local_files_only = bool(local_files_only)
        self._torch = torch
        self._model_type = SentenceTransformer
        self._reranker_type = CrossEncoder
        self._embedder: Any = None
        self._reranker: Any = None
        self._versions = {"sentence_transformers": version("sentence-transformers"),
                          "transformers": version("transformers"),
                          "torch": str(torch.__version__)}

    def metadata(self, kind: str) -> dict[str, Any]:
        if kind not in {"embedding", "reranker"}:
            raise SemanticRetrievalError("model-operation-unavailable", "adapter operation is unavailable")
        if kind == "reranker":
            return {"provider": "sentence-transformers", "model": self.reranker_model,
                    "revision": self.reranker_revision,
                    "inference": {"device": self.device, "dtype": "float32",
                                  "max_length": self.max_length, "batch_size": self.batch_size,
                                  "activation": "raw-logit", "backend": "torch",
                                  "truncation": "reject-over-limit", **self._versions}}
        return {"provider": "sentence-transformers", "model": self.model,
                "revision": self.revision,
                "inference": {"device": self.device, "dtype": "float32",
                              "max_length": self.max_length, "batch_size": self.batch_size,
                              "normalization": "l2", "pooling": "revision-model-config",
                              "prompt": "revision-model-config", "backend": "torch",
                              "truncation": "reject-over-limit", **self._versions}}

    def _load_embedding(self):
        if self._embedder is None:
            try:
                model = self._model_type(
                    self.model, revision=self.revision, device=self.device,
                    local_files_only=self.local_files_only, token=False,
                    trust_remote_code=False, model_kwargs={"dtype": self._torch.float32},
                )
            except Exception as error:
                raise SemanticRetrievalError("model-load-failed", "local embedding model could not be loaded") from error
            if self.max_length > model.max_seq_length:
                raise SemanticRetrievalError("model-input-limit", "requested length exceeds the model's supported length")
            model.max_seq_length = self.max_length
            self._embedder = model
        return self._embedder

    def _check_embedding_lengths(self, model, texts: list[str], kind: str) -> None:
        names = ["query"] if kind == "query" else ["document", "passage", "corpus"]
        prompt_name = next((name for name in names if name in model.prompts),
                           getattr(model, "default_prompt_name", None))
        prompt = model.prompts.get(prompt_name, "")
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise SemanticRetrievalError("invalid-model-input", "embedding input must be nonempty text")
            tokens = model.tokenizer(prompt + text, truncation=False, add_special_tokens=True)
            length = len(tokens["input_ids"])
            if length > self.max_length:
                raise SemanticRetrievalError(
                    "model-input-too-long", f"embedding input uses {length} tokens; limit is {self.max_length}"
                )

    def _encode(self, texts: list[str], kind: str) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_embedding()
        self._check_embedding_lengths(model, texts, kind)
        operation = model.encode_document if kind == "document" else model.encode_query
        try:
            vectors = operation(texts, normalize_embeddings=True, batch_size=self.batch_size,
                                show_progress_bar=False, convert_to_numpy=True)
        except Exception as error:
            raise SemanticRetrievalError("model-inference-failed", "embedding inference failed on the selected device") from error
        return vectors.tolist()

    def encode_documents(self, documents: list[str]) -> list[list[float]]:
        return self._encode(documents, "document")

    def encode_queries(self, queries: list[str]) -> list[list[float]]:
        return self._encode(queries, "query")

    def _load_reranker(self):
        if self._reranker is None:
            try:
                model = self._reranker_type(
                    self.reranker_model, revision=self.reranker_revision,
                    device=self.device, max_length=self.max_length,
                    local_files_only=self.local_files_only, token=False,
                    trust_remote_code=False, activation_fn=self._torch.nn.Identity(),
                    model_kwargs={"dtype": self._torch.float32},
                )
            except Exception as error:
                raise SemanticRetrievalError("model-load-failed", "local reranker model could not be loaded") from error
            supported = min(model.tokenizer.model_max_length,
                            getattr(model.model.config, "max_position_embeddings", self.max_length))
            if self.max_length > supported:
                raise SemanticRetrievalError("model-input-limit", "requested length exceeds the reranker's supported length")
            self._reranker = model
        return self._reranker

    def score_pairs(self, question: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if not isinstance(question, str) or not question.strip():
            raise SemanticRetrievalError("invalid-model-input", "reranker question must be nonempty text")
        model = self._load_reranker()
        if getattr(model, "default_prompt_name", None) is not None:
            raise SemanticRetrievalError(
                "model-input-configuration", "rerankers with implicit prompt templates require a dedicated adapter"
            )
        for document in documents:
            if not isinstance(document, str) or not document.strip():
                raise SemanticRetrievalError("invalid-model-input", "reranker documents must be nonempty text")
            pair = model.tokenizer(question, document, truncation=False, add_special_tokens=True)
            length = len(pair["input_ids"])
            if length > self.max_length:
                raise SemanticRetrievalError(
                    "model-input-too-long", f"reranker pair uses {length} tokens; limit is {self.max_length}"
                )
        try:
            scores = model.predict([(question, text) for text in documents],
                                   batch_size=self.batch_size, show_progress_bar=False,
                                   activation_fn=self._torch.nn.Identity(), convert_to_numpy=True)
        except Exception as error:
            raise SemanticRetrievalError("model-inference-failed", "reranker inference failed on the selected device") from error
        return scores.tolist()
