"""Encoder-only adapter for the optional ``retrieval`` extra.

It takes a sentence-transformers model id and nothing else: the library picks
the device, and offline use is Hugging Face's own ``HF_HUB_OFFLINE``. Vectors
leave this module as little-endian float32 bytes, L2-normalized, so callers
never need NumPy to store them. NumPy and sentence-transformers are imported
lazily, so importing this module is cheap.
"""

from __future__ import annotations

from typing import Any

from ..home import KnowledgeError

DOCUMENT_PROMPTS = ("document", "passage", "corpus")


class EmbeddingError(KnowledgeError):
    """The embedding model could not be loaded or run."""


class RetrievalExtraMissing(EmbeddingError):
    """NumPy or sentence-transformers is not installed."""


class Encoder:
    """One loaded model, addressed by its id."""

    def __init__(self, model: str) -> None:
        try:
            import numpy
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RetrievalExtraMissing("install kgdistiller[retrieval]") from error
        try:
            loaded = SentenceTransformer(model, trust_remote_code=False, token=False)
        except Exception as error:
            raise EmbeddingError(f"cannot load embedding model {model}: {error}") from error
        self.model = model
        self.max_tokens: int = loaded.max_seq_length
        self._numpy = numpy
        self._loaded: Any = loaded

    def _document_prompt(self) -> str:
        prompts = self._loaded.prompts or {}
        name = next((name for name in DOCUMENT_PROMPTS if name in prompts),
                    getattr(self._loaded, "default_prompt_name", None))
        return prompts.get(name, "") if name else ""

    def over_limit(self, texts: list[str]) -> list[int]:
        """Positions of the texts whose document-prompted token count exceeds the model's limit."""
        prompt = self._document_prompt()
        over = []
        for position, text in enumerate(texts):
            encoded = self._loaded.tokenizer(prompt + text, truncation=False, add_special_tokens=True)
            if len(encoded["input_ids"]) > self.max_tokens:
                over.append(position)
        return over

    def _rows(self, operation: Any, texts: list[str]) -> list[bytes]:
        try:
            output = operation(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        except Exception as error:
            raise EmbeddingError(f"embedding with {self.model} failed: {error}") from error
        matrix = self._numpy.asarray(output, dtype="<f4")
        return [row.tobytes() for row in matrix]

    def encode_documents(self, texts: list[str]) -> list[bytes]:
        return self._rows(self._loaded.encode_document, texts)

    def encode_query(self, text: str) -> bytes:
        return self._rows(self._loaded.encode_query, [text])[0]


_resident: Encoder | None = None


def encoder(model: str) -> Encoder:
    """The resident encoder for ``model``; a different id loads and replaces it."""
    global _resident
    if _resident is None or _resident.model != model:
        _resident = Encoder(model)
    return _resident
