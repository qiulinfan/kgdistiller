"""Optional provider-neutral embedding retrieval; similarities never resolve identity.

Adapters own model loading and inference. This module uses only the standard
library. Document vectors live in one rebuildable cache file per embedding
model, keyed by node ID and storing the exact projected text that was embedded;
a node is re-embedded whenever its current projected text differs.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Protocol, cast

from .contracts import canonical_json
from .knowledge_store import entries_root
from .query import GraphView, _node_search_fields

DOCUMENT_PROJECTION = "kgdistiller-search-document-v1"
VECTOR_CACHE_SCHEMA = "kgdistiller-vector-cache-v1"
MAX_DIMENSIONS = 8192
MAX_CACHE_BYTES = 256 * 1024 * 1024


class SemanticRetrievalError(ValueError):
    """Explicit adapter/cache failure; callers must not silently change lanes."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class EmbeddingAdapter(Protocol):
    def metadata(self, kind: str) -> dict[str, Any]: ...

    def encode_documents(self, documents: list[str]) -> list[list[float]]: ...

    def encode_queries(self, queries: list[str]) -> list[list[float]]: ...


class RerankingAdapter(EmbeddingAdapter, Protocol):
    def score_pairs(self, question: str, documents: list[str]) -> list[float]: ...


def model_descriptor(adapter: EmbeddingAdapter, kind: str = "embedding") -> dict[str, Any]:
    """Require immutable model revision and all effective inference settings."""
    try:
        descriptor = adapter.metadata(kind)
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "provider", "model", "revision", "inference"
        }:
            raise ValueError("descriptor fields")
        for key in ("provider", "model", "revision"):
            if not isinstance(descriptor[key], str) or not descriptor[key].strip() or len(descriptor[key]) > 256:
                raise ValueError("descriptor strings")
        if not isinstance(descriptor["inference"], dict):
            raise ValueError("descriptor inference")  # noqa: TRY004
        if len(canonical_json(descriptor["inference"]).encode("utf-8")) > 4096:
            raise ValueError("descriptor inference size")
        # Take an immutable canonical copy even if an adapter reuses its object.
        return json.loads(canonical_json(descriptor))
    except Exception as error:
        raise SemanticRetrievalError("invalid-model-descriptor", f"{kind} model descriptor is invalid") from error


def search_document(node: dict[str, Any]) -> str:
    """Project the same entry fields as lexical search, including the Evidence quote."""
    name, aliases, body = _node_search_fields(node)
    return f"Name: {name}\nAliases: {aliases}\nEntry:\n{body}"


def model_cache_name(model: dict[str, Any]) -> str:
    """Return the readable cache filename for one embedding model."""
    text = unicodedata.normalize("NFKD", str(model["model"])).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return f"vectors-{slug or 'model'}.json"


def _vectors(value: Any, count: int, *, dimensions: int | None = None) -> tuple[list[list[float]], int]:
    if not isinstance(value, list) or len(value) != count:
        raise SemanticRetrievalError("invalid-embedding-vectors", "embedding vector count does not match input count")
    result: list[list[float]] = []
    width = dimensions
    for vector in value:
        if not isinstance(vector, list) or not 1 <= len(vector) <= MAX_DIMENSIONS:
            raise SemanticRetrievalError("invalid-embedding-vectors", "embedding vector dimensions are invalid")
        if width is None:
            width = len(vector)
        if len(vector) != width:
            raise SemanticRetrievalError("invalid-embedding-vectors", "embedding vector dimensions do not match")
        try:
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in vector):
                raise ValueError("invalid numeric value")
            numeric = [float(item) for item in vector]
        except (ValueError, OverflowError) as error:
            raise SemanticRetrievalError("invalid-embedding-vectors", "embedding vector values must be finite non-boolean numbers") from error
        norm = math.hypot(*numeric)
        if norm == 0 or not math.isfinite(norm):
            raise SemanticRetrievalError("invalid-embedding-vectors", "embedding vectors must have finite nonzero norm")
        result.append(numeric)
    return result, width or 0


def _normalize(vector: list[float]) -> list[float]:
    norm = math.hypot(*vector)
    return [item / norm for item in vector]


def _scores(value: Any, count: int) -> list[float]:
    try:
        if not isinstance(value, list) or len(value) != count or any(isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) for score in value):
            raise ValueError("invalid score count or values")
        return [float(score) for score in value]
    except (ValueError, OverflowError) as error:
        raise SemanticRetrievalError("invalid-reranker-scores", "reranker must return one finite non-boolean score per input candidate") from error


class SemanticRankingService:
    """Cosine candidates from a node-keyed vector cache plus the original question."""

    def __init__(self, adapter: EmbeddingAdapter, *, cache_dir: Path, rerank: bool = False, candidate_limit: int = 50) -> None:
        if not isinstance(rerank, bool) or isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or not 1 <= candidate_limit <= 500:
            raise SemanticRetrievalError("invalid-ranking-configuration", "rerank must be boolean and candidate_limit must be between 1 and 500")
        self.adapter = adapter
        self.cache_dir = Path(cache_dir)
        self.rerank_enabled = rerank
        self.candidate_limit = candidate_limit
        self.last_cache_stats: dict[str, Any] = {}

    def _cache_location(self, view: GraphView) -> None:
        if self.cache_dir.is_symlink() or (self.cache_dir.exists() and not self.cache_dir.is_dir()):
            raise SemanticRetrievalError("invalid-vector-cache", "selected cache root must be a real directory")
        entries, cache_root = entries_root(view.repo_root).resolve(), self.cache_dir.resolve()
        if cache_root == entries or entries in cache_root.parents:
            raise SemanticRetrievalError("cache-store-conflict", "derived model cache must be outside the entry directory")

    def _read_cache(self, path: Path, model: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return stored records for this model; a different model starts empty."""
        if not path.exists() and not path.is_symlink():
            return {}
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CACHE_BYTES:
                raise ValueError("cache file size/type")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("cache file type")
                raw = handle.read(MAX_CACHE_BYTES + 1)
            if len(raw) > MAX_CACHE_BYTES:
                raise ValueError("cache file size")
            payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            if not isinstance(payload, dict) or set(payload) != {"schema", "model", "records"} or payload["schema"] != VECTOR_CACHE_SCHEMA:
                raise ValueError("cache fields")
            records = payload["records"]
            if not isinstance(records, dict):
                raise ValueError("cache records")  # noqa: TRY004
            if payload["model"] != model:
                return {}
            width: int | None = None
            for node_id, record in records.items():
                if not isinstance(node_id, str) or not isinstance(record, dict) or set(record) != {"text", "vector"} or not isinstance(record["text"], str):
                    raise ValueError("cache record")
                _, width = _vectors([record["vector"]], 1, dimensions=width)
            return records
        except (OSError, UnicodeError, ValueError, SemanticRetrievalError) as error:
            raise SemanticRetrievalError("invalid-vector-cache", "embedding cache is malformed") from error

    def _write_cache(self, path: Path, model: dict[str, Any], records: dict[str, dict[str, Any]]) -> None:
        raw = canonical_json({"schema": VECTOR_CACHE_SCHEMA, "model": model, "records": records}).encode("utf-8")
        if len(raw) > MAX_CACHE_BYTES:
            raise SemanticRetrievalError("vector-cache-too-large", "embedding cache exceeds the byte limit")
        temporary: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.cache_dir, prefix=".vectors-", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as error:
            raise SemanticRetrievalError("vector-cache-unwritable", "embedding cache could not be written atomically") from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _document_vectors(self, view: GraphView, node_ids: list[str], documents: list[str], model: dict[str, Any]) -> tuple[list[list[float]], int, str]:
        path = self.cache_dir / model_cache_name(model)
        stored = self._read_cache(path, model)
        records = {node_id: record for node_id, record in stored.items() if node_id in view.nodes}
        changed = len(records) != len(stored)
        missing = [(node_id, document) for node_id, document in zip(node_ids, documents) if records.get(node_id, {}).get("text") != document]
        width = len(next(iter(records.values()))["vector"]) if records else None
        self.last_cache_stats = {"documents_requested": len(node_ids), "document_cache_hits": len(node_ids) - len(missing), "document_inference_items": len(missing), "dropped_records": len(stored) - len(records)}
        if missing:
            try:
                encoded = self.adapter.encode_documents([document for _, document in missing])
            except Exception as error:
                raise SemanticRetrievalError("embedding-model-failed", "embedding document inference failed") from error
            if model_descriptor(self.adapter) != model:
                raise SemanticRetrievalError("model-descriptor-changed", "embedding model descriptor changed during retrieval")
            vectors, width = _vectors(encoded, len(missing), dimensions=width)
            for (node_id, document), vector in zip(missing, vectors):
                records[node_id] = {"text": document, "vector": vector}
            changed = True
        if changed:
            self._write_cache(path, model, dict(sorted(records.items())))
        status = "empty" if not node_ids else "miss" if missing else "hit"
        return [records[node_id]["vector"] for node_id in node_ids], width or 0, status

    def rank(
        self,
        view: GraphView,
        *,
        question: str,
        eligible_ids: list[str],
        limit: int = 500,
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        if not isinstance(question, str) or not question.strip() or len(question) > 8192:
            raise SemanticRetrievalError("invalid-semantic-query", "embedding question is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise SemanticRetrievalError("invalid-semantic-limit", "embedding limit must be between 1 and 500")
        if not isinstance(eligible_ids, list) or any(not isinstance(item, str) or item not in view.nodes for item in eligible_ids) or len(eligible_ids) != len(set(eligible_ids)):
            raise SemanticRetrievalError("invalid-semantic-candidates", "embedding candidate IDs must be unique known graph IDs")
        self._cache_location(view)
        model = model_descriptor(self.adapter)
        ordered_ids = sorted(eligible_ids)
        documents = [search_document(view.nodes[node_id]) for node_id in ordered_ids]
        if documents:
            vectors, dimensions, cache_status = self._document_vectors(view, ordered_ids, documents, model)
        else:
            vectors, dimensions, cache_status = [], 0, "empty"
            self.last_cache_stats = {"documents_requested": 0, "document_cache_hits": 0, "document_inference_items": 0, "dropped_records": 0}
        rows: list[tuple[str, float]] = []
        if ordered_ids:
            try:
                encoded = self.adapter.encode_queries([question])
            except Exception as error:
                raise SemanticRetrievalError("embedding-model-failed", "embedding query inference failed") from error
            if model_descriptor(self.adapter) != model:
                raise SemanticRetrievalError("model-descriptor-changed", "embedding model descriptor changed during retrieval")
            query_vectors, _ = _vectors(encoded, 1, dimensions=dimensions)
            query_vector = _normalize(query_vectors[0])
            for node_id, vector in zip(ordered_ids, vectors):
                score = math.fsum(a * b for a, b in zip(query_vector, _normalize(vector)))
                rows.append((node_id, max(-1.0, min(1.0, score))))
            rows.sort(key=lambda item: (-item[1], item[0]))
        provenance = {
            "model": model,
            "projection": DOCUMENT_PROJECTION,
            "document_count": len(documents),
            "dimensions": dimensions,
            "cache_status": cache_status,
            "query_source": "plan.question",
        }
        return rows[:limit], provenance

    def rerank(
        self,
        view: GraphView,
        *,
        question: str,
        candidate_ids: list[str],
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Score only the supplied candidates; never create IDs or edges."""
        if not self.rerank_enabled:
            raise SemanticRetrievalError("reranker-disabled", "reranker was not explicitly enabled")
        if not isinstance(question, str) or not question.strip() or len(question) > 8192:
            raise SemanticRetrievalError("invalid-semantic-query", "reranker question is invalid")
        if not isinstance(candidate_ids, list) or len(candidate_ids) > self.candidate_limit or any(not isinstance(item, str) or item not in view.nodes for item in candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
            raise SemanticRetrievalError("invalid-reranker-candidates", "reranker candidates must be bounded unique known graph IDs")
        model = model_descriptor(self.adapter, "reranker")
        documents = [search_document(view.nodes[node_id]) for node_id in candidate_ids]
        scores: list[float] = []
        if documents:
            try:
                raw_scores = cast(RerankingAdapter, self.adapter).score_pairs(question, documents)
            except Exception as error:
                raise SemanticRetrievalError("reranker-model-failed", "reranker pair inference failed") from error
            scores = _scores(raw_scores, len(documents))
            if model_descriptor(self.adapter, "reranker") != model:
                raise SemanticRetrievalError("model-descriptor-changed", "reranker model descriptor changed during retrieval")
        provenance = {
            "model": model,
            "projection": DOCUMENT_PROJECTION,
            "query_source": "plan.question",
            "candidate_limit": self.candidate_limit,
            "candidates": [{"node_id": node_id, "score": score} for node_id, score in zip(candidate_ids, scores)],
        }
        rows = list(zip(candidate_ids, scores))
        rows.sort(key=lambda item: (-item[1], item[0]))
        return rows, provenance
