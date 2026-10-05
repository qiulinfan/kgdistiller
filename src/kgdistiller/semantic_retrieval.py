"""Optional provider-neutral embedding retrieval; similarities never resolve identity.

Adapters own model loading and inference. This module uses only the standard
library and stores vectors outside the authority graph in an explicitly chosen
cache directory. Generation caches bind current graph identities; exact-input caches reuse only
model/projection/text-identical values and rebind them to each current generation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from collections import OrderedDict
from typing import Any, Callable, Protocol, cast

from .alignment import node_fingerprint
from .contracts import canonical_json, sha256_json
from .derived_cache import DerivedCacheError, ExactInputCache, file_signature
from .query import GraphView, _node_search_fields, load_graph_view


DOCUMENT_PROJECTION = "kgdistiller-search-document-v1"
VECTOR_CACHE_SCHEMA = "kgdistiller-vector-cache-v1"
MAX_DIMENSIONS = 8192
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_GENERATION_MEMORY_BYTES = 64 * 1024 * 1024
MAX_GENERATION_MEMORY_RECORDS = 4
MAX_SOURCE_MEMORY_RECORDS = 8


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
            raise ValueError("descriptor inference")
        if len(canonical_json(descriptor["inference"]).encode("utf-8")) > 4096:
            raise ValueError("descriptor inference size")
        # Take an immutable canonical copy even if an adapter reuses its object.
        return json.loads(canonical_json(descriptor))
    except Exception as error:
        raise SemanticRetrievalError("invalid-model-descriptor", f"{kind} model descriptor is invalid") from error


def search_document(node: dict[str, Any]) -> str:
    """Project the same full source fields as lexical search, without provenance guesses."""
    name, aliases, body = _node_search_fields(node)
    return f"Name: {name}\nAliases: {aliases}\nDefinition and conditions:\n{body}"


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


def _exact_binding(operation: str, model: dict[str, Any], *texts: str) -> dict[str, Any]:
    return {"operation": operation, "projection": DOCUMENT_PROJECTION, "model": model,
            "inputs": [{"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "bytes": len(text.encode("utf-8"))} for text in texts]}


def _source_state(view: GraphView, *, verify_filesystem: bool = False,
                  filesystem_cache: OrderedDict | None = None) -> str:
    """Check canonical snapshot content and signature-bound filesystem freshness.

    Only unchanged regular files reuse a previously validated digest. In-memory
    source content is still hashed on every model boundary; file changes trigger
    reload/rehash rather than trusting an old generation token alone.
    """
    try:
        unsigned = {key: value for key, value in view.snapshot.items() if key != "snapshot_sha256"}
        if sha256_json(unsigned) != view.snapshot["snapshot_sha256"]:
            raise ValueError("snapshot content changed")
        if view.nodes != {node["id"]: node for node in view.snapshot["nodes"]}:
            raise ValueError("node content changed")
        key = lambda edge: (edge["source"], edge["relation"], edge["target"])
        if len(view.edges) != len(view.snapshot["edges"]) or {key(edge): edge for edge in view.edges} != {key(edge): edge for edge in view.snapshot["edges"]}:
            raise ValueError("edge content changed")
        if len(view.references) != len(view.snapshot["references"]) or {ref["id"]: ref for ref in view.references} != {ref["id"]: ref for ref in view.snapshot["references"]}:
            raise ValueError("reference content changed")
        files: dict[str, str] = {}
        if view.graph_dir != Path("."):
            cache_key = (str(view.graph_dir.resolve()), view.generation, view.snapshot["snapshot_sha256"])
            cached = filesystem_cache.get(cache_key) if filesystem_cache is not None else None
            manifest_path = view.graph_dir / "manifest.json"
            info = manifest_path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("manifest is not a regular file")
            manifest_signature = file_signature(info)
            if cached is not None and cached["manifest_signature"] == manifest_signature:
                current_manifest = cached["manifest"]
            else:
                current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if sha256_json(current_manifest) != view.generation:
                raise ValueError("filesystem generation changed")
            paths = ["manifest.json", "nodes.jsonl", "edges.jsonl", "references.jsonl", "diagnostics.json"]
            paths.extend(shard["path"] for shard in current_manifest.get("entry_store", {}).get("shards", []))
            signatures = []
            for name in sorted(set(paths)):
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe source artifact path")
                info = (view.graph_dir / relative).lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("source artifact is not regular")
                signatures.append((name, file_signature(info)))
            reusable = cached is not None and cached["signatures"] == signatures
            if reusable and (not verify_filesystem or cached["verified"]):
                files = cached["files"]
                if filesystem_cache is not None:
                    filesystem_cache.move_to_end(cache_key)
            else:
                if verify_filesystem and load_graph_view(view.graph_dir).snapshot["snapshot_sha256"] != view.snapshot["snapshot_sha256"]:
                    raise ValueError("loaded graph no longer matches filesystem source")
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
                for name, signature in signatures:
                    digest = hashlib.sha256()
                    with os.fdopen(os.open(view.graph_dir / name, flags), "rb") as handle:
                        if file_signature(os.fstat(handle.fileno())) != signature:
                            raise ValueError("source artifact changed while opening")
                        for chunk in iter(lambda: handle.read(65536), b""):
                            digest.update(chunk)
                        if file_signature(os.fstat(handle.fileno())) != signature:
                            raise ValueError("source artifact changed while hashing")
                    files[name] = digest.hexdigest()
                if filesystem_cache is not None:
                    filesystem_cache[cache_key] = {"manifest_signature": manifest_signature, "manifest": current_manifest,
                                                   "signatures": signatures, "files": files, "verified": verify_filesystem}
                    filesystem_cache.move_to_end(cache_key)
                    while len(filesystem_cache) > MAX_SOURCE_MEMORY_RECORDS:
                        filesystem_cache.popitem(last=False)
        return sha256_json({"snapshot": view.snapshot, "nodes": view.nodes, "edges": view.edges,
                            "references": view.references, "generation": view.generation, "files": files})
    except Exception as error:
        raise SemanticRetrievalError("stale-generation", "model input source changed or no longer matches its graph generation") from error


def _scores(value: Any, count: int) -> list[float]:
    try:
        if not isinstance(value, list) or len(value) != count or any(isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) for score in value):
            raise ValueError("invalid score count or values")
        return [float(score) for score in value]
    except (ValueError, OverflowError) as error:
        raise SemanticRetrievalError("invalid-reranker-scores", "reranker must return one finite non-boolean score per input candidate") from error


class SemanticRankingService:
    """Cosine candidates from a content-bound vector cache plus original question."""

    def __init__(self, adapter: EmbeddingAdapter, *, cache_dir: Path, rerank: bool = False, candidate_limit: int = 50) -> None:
        if not isinstance(rerank, bool) or isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or not 1 <= candidate_limit <= 500:
            raise SemanticRetrievalError("invalid-ranking-configuration", "rerank must be boolean and candidate_limit must be between 1 and 500")
        self.adapter = adapter
        self.cache_dir = Path(cache_dir)
        self.rerank_enabled = rerank
        self.candidate_limit = candidate_limit
        self._exact_cache = ExactInputCache(self.cache_dir)
        self._seeded_indexes: OrderedDict[str, None] = OrderedDict()
        self._source_files_cache: OrderedDict = OrderedDict()
        self._generation_vectors: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._generation_vector_bytes = 0
        self.last_cache_stats: dict[str, dict[str, Any]] = {"rank": {}, "rerank": {}}

    def _cache_location(self, view: GraphView) -> None:
        if self.cache_dir.is_symlink() or (self.cache_dir.exists() and not self.cache_dir.is_dir()):
            raise SemanticRetrievalError("invalid-vector-cache", "selected cache root must be a real directory")
        if view.graph_dir != Path("."):
            graph_root, cache_root = view.graph_dir.resolve(), self.cache_dir.resolve()
            if cache_root == graph_root or graph_root in cache_root.parents:
                raise SemanticRetrievalError("cache-graph-conflict", "derived model cache must be outside the authority graph directory")

    def _get_exact(self, binding: dict[str, Any]) -> Any | None:
        try:
            return self._exact_cache.get(binding)
        except DerivedCacheError as error:
            raise SemanticRetrievalError(error.code, error.message) from error

    def _put_exact(self, binding: dict[str, Any], value: Any, *, only_if_absent: bool = False) -> bool:
        try:
            return self._exact_cache.put(binding, value, only_if_absent=only_if_absent)
        except DerivedCacheError as error:
            code = "vector-cache-unwritable" if binding["operation"] == "document-vector" and error.code == "exact-input-cache-unwritable" else error.code
            raise SemanticRetrievalError(code, error.message) from error

    def _exact_vectors(self, texts: list[str], model: dict[str, Any], operation: str,
                       guard: Callable[[], None], *, dimensions: int | None = None) -> tuple[list[list[float]], int]:
        unique = list(dict.fromkeys(texts))
        values: dict[str, list[float]] = {}
        missing: list[str] = []
        width = dimensions
        prefix = "document" if operation == "document-vector" else "query"
        stats = self.last_cache_stats["rank"]
        stats[prefix + "_deduplicated"] = len(texts) - len(unique)
        for text in unique:
            cached = self._get_exact(_exact_binding(operation, model, text))
            if cached is None:
                missing.append(text)
            else:
                try:
                    vectors, width = _vectors([cached], 1, dimensions=width)
                except SemanticRetrievalError as error:
                    raise SemanticRetrievalError("invalid-exact-input-cache", "cached embedding dimensions do not match selected input") from error
                values[text] = vectors[0]
        stats[prefix + "_cache_hits"] = len(values)
        stats[prefix + "_cache_misses"] = len(missing)
        stats[prefix + "_inference_items"] = len(missing)
        if missing:
            guard()
            try:
                encoded = self.adapter.encode_documents(missing) if operation == "document-vector" else self.adapter.encode_queries(missing)
            except Exception as error:
                raise SemanticRetrievalError("embedding-model-failed", f"embedding {prefix} inference failed") from error
            vectors, width = _vectors(encoded, len(missing), dimensions=width)
            guard()
            for text, vector in zip(missing, vectors):
                self._put_exact(_exact_binding(operation, model, text), vector)
                values[text] = vector
        return [values[text] for text in texts], width or 0

    def _mark_seeded(self, index: str) -> None:
        self._seeded_indexes[index] = None
        self._seeded_indexes.move_to_end(index)
        while len(self._seeded_indexes) > 32:
            self._seeded_indexes.popitem(last=False)

    def _remember_generation(self, index: str, signature: tuple[int, ...], vectors: list[list[float]],
                             dimensions: int, digest: str, size: int) -> None:
        previous = self._generation_vectors.pop(index, None)
        if previous is not None:
            self._generation_vector_bytes -= previous["cost"]
        cost = max(size * 2, sum(len(vector) for vector in vectors) * 80)
        if cost > MAX_GENERATION_MEMORY_BYTES:
            return
        self._generation_vectors[index] = {"signature": signature, "vectors": vectors,
                                           "dimensions": dimensions, "digest": digest,
                                           "normalized": None, "cost": cost}
        self._generation_vector_bytes += cost
        while len(self._generation_vectors) > MAX_GENERATION_MEMORY_RECORDS or self._generation_vector_bytes > MAX_GENERATION_MEMORY_BYTES:
            _, removed = self._generation_vectors.popitem(last=False)
            self._generation_vector_bytes -= removed["cost"]

    def _seed_content_vectors(self, index: str, manifest: dict[str, Any], documents: list[str], vectors: list[list[float]]) -> None:
        if index in self._seeded_indexes:
            self._seeded_indexes.move_to_end(index)
            return
        unique: dict[str, list[float]] = {}
        for document, vector in zip(documents, vectors):
            if document in unique and unique[document] != vector:
                raise SemanticRetrievalError("invalid-vector-cache", "identical cached model inputs have inconsistent vectors")
            unique[document] = vector
        for document, vector in unique.items():
            self.last_cache_stats["rank"]["document_cache_seeded"] += int(self._put_exact(_exact_binding("document-vector", manifest["model"], document), vector, only_if_absent=True))
        self._mark_seeded(index)

    def _normalized_documents(self, index: str, vectors: list[list[float]]) -> list[list[float]]:
        remembered = self._generation_vectors.get(index)
        if remembered is None or remembered["vectors"] is not vectors:
            return [_normalize(vector) for vector in vectors]
        if remembered["normalized"] is None:
            remembered["normalized"] = [_normalize(vector) for vector in vectors]
        return remembered["normalized"]

    def _cache_vectors(self, manifest: dict[str, Any], documents: list[str],
                       guard: Callable[[], None]) -> tuple[list[list[float]], int, str, str]:
        index_sha256 = sha256_json(manifest)
        path = self.cache_dir / f"{index_sha256}.json"
        stats = self.last_cache_stats["rank"]
        if path.exists() or path.is_symlink():
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CACHE_BYTES:
                    raise ValueError("cache file size/type")
                signature = file_signature(info)
                remembered = self._generation_vectors.get(index_sha256)
                if remembered is not None and remembered["signature"] == signature:
                    self._generation_vectors.move_to_end(index_sha256)
                    vectors, dimensions, digest = remembered["vectors"], remembered["dimensions"], remembered["digest"]
                else:
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(path, flags), "rb") as handle:
                        opened = os.fstat(handle.fileno())
                        if not stat.S_ISREG(opened.st_mode) or file_signature(opened) != signature:
                            raise ValueError("cache file changed while opening")
                        raw = handle.read(MAX_CACHE_BYTES + 1)
                        if file_signature(os.fstat(handle.fileno())) != signature:
                            raise ValueError("cache file changed while reading")
                    if len(raw) > MAX_CACHE_BYTES:
                        raise ValueError("cache file size")
                    payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                    if not isinstance(payload, dict) or set(payload) != {"schema", "manifest", "dimensions", "vectors", "cache_sha256"}:
                        raise ValueError("cache fields")
                    if payload["schema"] != VECTOR_CACHE_SCHEMA or payload["manifest"] != manifest:
                        raise ValueError("cache binding")
                    unsigned = {key: value for key, value in payload.items() if key != "cache_sha256"}
                    if payload["cache_sha256"] != sha256_json(unsigned):
                        raise ValueError("cache digest")
                    dimensions = payload["dimensions"]
                    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= MAX_DIMENSIONS:
                        raise ValueError("cache dimensions")
                    vectors, _ = _vectors(payload["vectors"], len(documents), dimensions=dimensions)
                    digest = payload["cache_sha256"]
                    self._remember_generation(index_sha256, signature, vectors, dimensions, digest, len(raw))
            except Exception as error:
                raise SemanticRetrievalError("invalid-vector-cache", "embedding cache is malformed, changed, or belongs to another input") from error
            stats["generation_cache_status"] = "hit"
            self._seed_content_vectors(index_sha256, manifest, documents, vectors)
            return vectors, dimensions, "hit", digest
        stats["generation_cache_status"] = "miss"
        vectors, dimensions = self._exact_vectors(documents, manifest["model"], "document-vector", guard)
        payload = {"schema": VECTOR_CACHE_SCHEMA, "manifest": manifest, "dimensions": dimensions, "vectors": vectors}
        payload["cache_sha256"] = sha256_json(payload)
        raw = canonical_json(payload).encode("utf-8")
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
            signature = file_signature(path.lstat())
        except OSError as error:
            raise SemanticRetrievalError("vector-cache-unwritable", "embedding cache could not be written atomically") from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        self._mark_seeded(index_sha256)
        self._remember_generation(index_sha256, signature, vectors, dimensions, payload["cache_sha256"], len(raw))
        return vectors, dimensions, "miss", payload["cache_sha256"]

    def rank(
        self,
        view: GraphView,
        *,
        namespace: str,
        question: str,
        eligible_ids: list[str],
        limit: int = 500,
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        if namespace != view.snapshot["namespace"]:
            raise SemanticRetrievalError("namespace-conflict", "embedding namespace conflicts with graph")
        if not isinstance(question, str) or not question.strip() or len(question) > 8192:
            raise SemanticRetrievalError("invalid-semantic-query", "embedding question is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise SemanticRetrievalError("invalid-semantic-limit", "embedding limit must be between 1 and 500")
        if not isinstance(eligible_ids, list) or any(not isinstance(item, str) or item not in view.nodes for item in eligible_ids) or len(eligible_ids) != len(set(eligible_ids)):
            raise SemanticRetrievalError("invalid-semantic-candidates", "embedding candidate IDs must be unique known graph IDs")
        self.last_cache_stats = {"rank": {
            "generation_cache_status": "empty", "documents_requested": len(eligible_ids),
            "document_cache_hits": 0, "document_cache_misses": 0, "document_inference_items": 0,
            "document_cache_seeded": 0, "document_deduplicated": 0,
            "query_cache_hits": 0, "query_cache_misses": 0, "query_inference_items": 0,
            "query_deduplicated": 0,
        }, "rerank": {}}
        self._cache_location(view)
        source_state = _source_state(view, verify_filesystem=True, filesystem_cache=self._source_files_cache)
        model = model_descriptor(self.adapter)

        def guard() -> None:
            if model_descriptor(self.adapter) != model:
                raise SemanticRetrievalError("model-descriptor-changed", "embedding model descriptor changed during retrieval")
            if _source_state(view, filesystem_cache=self._source_files_cache) != source_state:
                raise SemanticRetrievalError("stale-generation", "embedding input source changed during retrieval")

        # Filter the document universe before projection or model inference.
        # Excluded source text must not consume a model's budget or make an
        # otherwise valid scoped query fail. The ordered records also bind
        # each filtered universe to its own cache key.
        ordered_ids = sorted(eligible_ids)
        documents = [search_document(view.nodes[node_id]) for node_id in ordered_ids]
        manifest = {
            "namespace": namespace,
            "snapshot_sha256": view.snapshot["snapshot_sha256"],
            "graph_sha256": view.snapshot["graph"]["sha256"],
            "projection": DOCUMENT_PROJECTION,
            "model": model,
            "documents": [
                {"node_id": node_id, "node_sha256": node_fingerprint(view.nodes[node_id]),
                 "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest()}
                for node_id, document in zip(ordered_ids, documents)
            ],
        }
        if documents:
            vectors, dimensions, cache_status, cache_sha256 = self._cache_vectors(manifest, documents, guard)
        else:
            vectors, dimensions, cache_status = [], 0, "empty"
            cache_sha256 = sha256_json({"schema": VECTOR_CACHE_SCHEMA, "manifest": manifest, "dimensions": 0, "vectors": []})
        rows: list[tuple[str, float]] = []
        if eligible_ids:
            query_vectors, _ = self._exact_vectors([question], model, "query-vector", guard, dimensions=dimensions)
            query_vector = _normalize(query_vectors[0])
            normalized_documents = self._normalized_documents(sha256_json(manifest), vectors)
            for node_id, vector in zip(ordered_ids, normalized_documents):
                score = math.fsum(a * b for a, b in zip(query_vector, vector))
                rows.append((node_id, max(-1.0, min(1.0, score))))
            rows.sort(key=lambda item: (-item[1], item[0]))
        guard()
        provenance = {
            "model": model,
            "projection": DOCUMENT_PROJECTION,
            "namespace": namespace,
            "snapshot_sha256": manifest["snapshot_sha256"],
            "graph_sha256": manifest["graph_sha256"],
            "index_sha256": sha256_json(manifest),
            "cache_sha256": cache_sha256,
            "document_count": len(documents),
            "dimensions": dimensions,
            "cache_status": cache_status,
            "query_source": "plan.question",
            "query_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        }
        return rows[:limit], provenance

    def rerank(
        self,
        view: GraphView,
        *,
        namespace: str,
        question: str,
        candidate_ids: list[str],
        expected_snapshot_sha256: str | None = None,
        expected_graph_sha256: str | None = None,
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Score only the supplied source-bound candidates; never create IDs or edges."""
        if not self.rerank_enabled:
            raise SemanticRetrievalError("reranker-disabled", "reranker was not explicitly enabled")
        if namespace != view.snapshot["namespace"]:
            raise SemanticRetrievalError("namespace-conflict", "reranker namespace conflicts with graph")
        snapshot_sha256 = view.snapshot["snapshot_sha256"]
        graph_sha256 = view.snapshot["graph"]["sha256"]
        if (expected_snapshot_sha256 is not None and expected_snapshot_sha256 != snapshot_sha256) or (expected_graph_sha256 is not None and expected_graph_sha256 != graph_sha256):
            raise SemanticRetrievalError("stale-generation", "reranker candidates belong to another graph generation")
        if not isinstance(question, str) or not question.strip() or len(question) > 8192:
            raise SemanticRetrievalError("invalid-semantic-query", "reranker question is invalid")
        if not isinstance(candidate_ids, list) or len(candidate_ids) > self.candidate_limit or any(not isinstance(item, str) or item not in view.nodes for item in candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
            raise SemanticRetrievalError("invalid-reranker-candidates", "reranker candidates must be bounded unique known graph IDs")
        self.last_cache_stats["rerank"] = {"pairs_requested": len(candidate_ids), "pair_cache_hits": 0,
                                           "pair_cache_misses": 0, "pair_inference_items": 0, "pair_deduplicated": 0}
        self._cache_location(view)
        source_state = _source_state(view, verify_filesystem=True, filesystem_cache=self._source_files_cache)
        model = model_descriptor(self.adapter, "reranker")
        documents = [search_document(view.nodes[node_id]) for node_id in candidate_ids]
        records = [
            {"node_id": node_id, "node_sha256": node_fingerprint(view.nodes[node_id]),
             "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest()}
            for node_id, document in zip(candidate_ids, documents)
        ]
        def guard() -> None:
            if model_descriptor(self.adapter, "reranker") != model:
                raise SemanticRetrievalError("model-descriptor-changed", "reranker model descriptor changed during retrieval")
            if _source_state(view, filesystem_cache=self._source_files_cache) != source_state:
                raise SemanticRetrievalError("stale-generation", "reranker candidate source changed during retrieval")

        unique = list(dict.fromkeys(documents))
        cached_scores: dict[str, float] = {}
        missing: list[str] = []
        for document in unique:
            value = self._get_exact(_exact_binding("pair-score", model, question, document))
            if value is None:
                missing.append(document)
            else:
                cached_scores[document] = _scores([value], 1)[0]
        stats = self.last_cache_stats["rerank"]
        stats.update(pair_cache_hits=len(cached_scores), pair_cache_misses=len(missing),
                     pair_inference_items=len(missing), pair_deduplicated=len(documents) - len(unique))
        if missing:
            guard()
            try:
                raw_scores = cast(RerankingAdapter, self.adapter).score_pairs(question, missing)
            except Exception as error:
                raise SemanticRetrievalError("reranker-model-failed", "reranker pair inference failed") from error
            inferred = _scores(raw_scores, len(missing))
            guard()
            for document, score in zip(missing, inferred):
                self._put_exact(_exact_binding("pair-score", model, question, document), score)
                cached_scores[document] = score
        guard()
        scores = [cached_scores[document] for document in documents]
        provenance = {
            "model": model,
            "projection": DOCUMENT_PROJECTION,
            "namespace": namespace,
            "snapshot_sha256": snapshot_sha256,
            "graph_sha256": graph_sha256,
            "query_source": "plan.question",
            "query_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "candidate_limit": self.candidate_limit,
            "candidates": [record | {"score": score} for record, score in zip(records, scores)],
        }
        rows = list(zip(candidate_ids, scores))
        rows.sort(key=lambda item: (-item[1], item[0]))
        return rows, provenance
