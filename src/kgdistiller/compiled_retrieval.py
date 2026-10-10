"""Retrieval and navigation over owner-authored, compiled knowledge.

The input is a library of definitions, conditions, retrieval surfaces, explicit
dependencies, and claims. Search returns candidates; it never establishes
identity or infers a relation. ``semantic_text`` is also suitable for an external
embedding adapter, without lookup references or storage metadata.

The compiled-library shape and BM25 posting-list approach originate in
qiulinfan/kgdistiller-experiment at commit 2876f19. No experiment knowledge,
query rules, cue weights, or benchmark answers are included here.
"""

from __future__ import annotations

import copy
import json
import math
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .query import _BM25_B, _BM25_K1
from .tokens import tokenize


class CompiledRetrievalError(ValueError):
    """Invalid compiled input or an unavailable exact lookup reference."""


_NODE_TEXT = ("name", "kind", "layer", "statement", "formal", "inputs_outputs")
_SURFACE_TEXT = ("gloss", "sense_key")
_SURFACE_LISTS = ("head_terms", "query_forms", "zh", "symbols", "not_this")


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise CompiledRetrievalError(f"{field} must be an object with string keys")
    return dict(value)


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CompiledRetrievalError(f"{field} must be a list")
    return value


def _text(record: Mapping[str, Any], key: str, field: str) -> str:
    value = record.get(key, "")
    if not isinstance(value, str):
        raise CompiledRetrievalError(f"{field}.{key} must be text")
    return value


def _texts(record: Mapping[str, Any], key: str, field: str) -> list[str]:
    values = _list(record.get(key), f"{field}.{key}")
    if any(not isinstance(v, str) for v in values):
        raise CompiledRetrievalError(f"{field}.{key} must contain text")
    return list(values)


def _fields(record: Mapping[str, Any], keys: Iterable[str], field: str) -> dict[str, str]:
    return {key: _text(record, key, field) for key in keys if key in record and record[key] is not None}


def _scientific_json(value: Any, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_scientific_json(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        return {key: _scientific_json(item, f"{field}.{key}") for key, item in _mapping(value, field).items()}
    raise CompiledRetrievalError(f"{field} must contain finite JSON scientific values")


def _scientific_record(record: Mapping[str, Any], known_text: Iterable[str], field: str) -> dict[str, Any]:
    _fields(record, known_text, field)
    return _scientific_json(record, field)


def _scientific_text(value: Any) -> str:
    return _json_bytes(value).decode("utf-8")


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _year(record: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = record.get("year")
    if value is None:
        return {}
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise CompiledRetrievalError(f"{field}.year must be a year")
    return {"year": value}


def _notation(value: Any, field: str) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_notation(item, field) for item in value]
    return _scientific_record(_mapping(value, field), ("symbol", "meaning"), field)


def _semantic_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)
    elif isinstance(value, list):
        for item in value:
            yield from _semantic_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in {"reference", "available", "gaps"}:
                yield from _semantic_values(item)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _term_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class _BM25:
    def __init__(self, documents: Mapping[str, list[str]]) -> None:
        self.counts = {ref: Counter(tokens) for ref, tokens in documents.items()}
        self.lengths = {ref: len(tokens) for ref, tokens in documents.items()}
        self.average = sum(self.lengths.values()) / len(documents) if documents else 1.0
        self.average = self.average or 1.0
        frequency: Counter[str] = Counter()
        self.postings: dict[str, list[str]] = defaultdict(list)
        for ref, counts in self.counts.items():
            frequency.update(counts.keys())
            for token in counts:
                self.postings[token].append(ref)
        self.idf = {
            token: math.log(1 + (len(documents) - count + 0.5) / (count + 0.5))
            for token, count in frequency.items()
        }

    def rank(self, query: str, limit: int) -> list[tuple[str, float]]:
        scores: dict[str, float] = defaultdict(float)
        for token in sorted(set(tokenize(query))):
            for ref in self.postings.get(token, []):
                frequency = self.counts[ref][token]
                normalization = _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * self.lengths[ref] / self.average
                )
                scores[ref] += self.idf[token] * frequency * (_BM25_K1 + 1) / (
                    frequency + normalization
                )
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


class CompiledLibrary:
    """A read-only compiled library with data-driven search and navigation.

    ``nodes`` is a mapping of existing references to authored records. Optional
    ``papers``, ``terms``, ``claims``, and ``edges`` retain the experiment's
    authored organization. References are opaque lookup handles, not semantic
    text. Missing relation targets remain visible as gaps.
    """

    def __init__(self, payload: Mapping[str, Any]) -> None:
        data = copy.deepcopy(_mapping(payload, "library"))
        if "nodes" not in data:
            raise CompiledRetrievalError("library requires nodes")
        self._nodes = {
            ref: _mapping(node, f"nodes[{ref}]")
            for ref, node in _mapping(data["nodes"], "nodes").items()
        }
        for ref, node in self._nodes.items():
            if not ref or ("id" in node and node["id"] != ref):
                raise CompiledRetrievalError(f"nodes[{ref}] has an inconsistent lookup reference")
            if not _text(node, "name", f"nodes[{ref}]").strip():
                raise CompiledRetrievalError(f"nodes[{ref}] requires a name")
        self._sources = {
            ref: self._source(_mapping(source, f"papers[{ref}]"), f"papers[{ref}]")
            for ref, source in _mapping(data.get("papers", {}), "papers").items()
        }
        self._terms = {
            ref: _mapping(term, f"terms[{ref}]")
            for ref, term in _mapping(data.get("terms", {}), "terms").items()
        }
        self._edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for raw in _list(data.get("edges"), "edges"):
            edge = _mapping(raw, "edge")
            source = _text(edge, "source", "edge")
            target = _text(edge, "target", "edge")
            detail = _scientific_record(edge, ("type", "evidence", "basis", "confidence"), "edge")
            detail.pop("source", None)
            detail.pop("target", None)
            if "direction" in detail:
                raise CompiledRetrievalError("edge.direction is reserved for explicit endpoint navigation")
            self._edges[source].append({"direction": "out", "target": target, **detail})
            self._edges[target].append({"direction": "in", "target": source, **detail})
        self._claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for raw in _list(data.get("claims"), "claims"):
            claim = self._claim(_mapping(raw, "claim"))
            for position in claim["positions"]:
                self._claims[position["reference"]].append(claim)
        self._entries = {ref: self._entry(ref) for ref in self._nodes}
        self._texts = {
            ref: self._semantic_text({
                "entry": self._semantic_entry(ref),
                "source_surfaces": self._sources.get(self._nodes[ref].get("paper"), {}),
            })
            for ref, entry in self._entries.items()
        }
        self._index = _BM25({ref: tokenize(text) for ref, text in self._texts.items()})

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CompiledLibrary:
        return cls(payload)

    @classmethod
    def from_path(cls, path: str | Path) -> CompiledLibrary:
        try:
            with Path(path).open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError) as error:
            raise CompiledRetrievalError(f"could not read compiled library: {error}") from error
        return cls.from_payload(payload)

    @staticmethod
    def _source(source: Mapping[str, Any], field: str) -> dict[str, Any]:
        result: dict[str, Any] = _fields(source, ("title",), field)
        for key in ("short", "authors", "zh", "families"):
            if key in source:
                result[key] = _texts(source, key, field)
        result.update(_year(source, field))
        return result

    def _source_record(self, reference: str | None) -> dict[str, Any]:
        source = self._sources.get(reference, {})
        return {key: value for key, value in source.items() if key in {"title", "year"}}

    def _target(self, reference: str) -> dict[str, Any]:
        node = self._nodes.get(reference)
        result: dict[str, Any] = {"reference": reference, "available": node is not None}
        if node is not None:
            result["name"] = node["name"]
            source = self._source_record(node.get("paper"))
            if source:
                result["source"] = copy.deepcopy(source)
            result.update(_year(node, f"nodes[{reference}]"))
        return result

    def _claim(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = _scientific_record(claim, ("question", "relation", "note", "family"), "claim")
        result["positions"] = []
        for raw in _list(claim.get("positions"), "claim.positions"):
            position = _mapping(raw, "claim.position")
            item = self._target(_text(position, "id", "claim.position"))
            detail = _scientific_record(position, ("position",), "claim.position")
            detail.pop("id", None)
            if "reference" in detail or "available" in detail:
                raise CompiledRetrievalError("claim.position reference and availability are derived from id")
            item.update(detail)
            item.update(_year(position, "claim.position"))
            result["positions"].append(item)
        result["timeline"] = [self._target(ref) for ref in _texts(claim, "timeline", "claim")]
        return result

    def _entry(self, reference: str) -> dict[str, Any]:
        node = self._nodes[reference]
        field = f"nodes[{reference}]"
        result: dict[str, Any] = {"reference": reference, **_fields(node, _NODE_TEXT, field)}
        result["aliases"] = _texts(node, "aliases", field)
        result["conditions"] = _texts(node, "conditions", field)
        result.update(_year(node, field))
        source_ref = node.get("paper")
        if source_ref is not None and not isinstance(source_ref, str):
            raise CompiledRetrievalError(f"{field}.paper must be a lookup reference")
        result["source"] = self._source_record(source_ref)
        surfaces = _mapping({} if node.get("surfaces") is None else node["surfaces"], f"{field}.surfaces")
        result["surfaces"] = _fields(surfaces, _SURFACE_TEXT, f"{field}.surfaces")
        for key in _SURFACE_LISTS:
            if key in surfaces:
                result["surfaces"][key] = _texts(surfaces, key, f"{field}.surfaces")
        result["notation"] = [_notation(raw, f"{field}.notation") for raw in _list(node.get("notation"), f"{field}.notation")]
        for key, fields in (
            ("distinguish_from", ("term", "here", "not")),
            ("evidence", ("section", "quote")),
        ):
            result[key] = [
                _scientific_record(_mapping(raw, f"{field}.{key}"), fields, f"{field}.{key}")
                for raw in _list(node.get(key), f"{field}.{key}")
            ]
        status = _mapping({} if node.get("epistemic") is None else node["epistemic"], f"{field}.epistemic")
        result["epistemic"] = _scientific_record(status, ("status", "note"), f"{field}.epistemic")
        for key, detail in (
            ("depends_on", ("role", "note")),
            ("relations", ("type", "evidence")),
            ("instances", ("how_it_differs",)),
        ):
            result[key] = []
            for raw in _list(node.get(key), f"{field}.{key}"):
                if isinstance(raw, str) and key == "depends_on":
                    record: dict[str, Any] = {"target": raw}
                else:
                    record = _mapping(raw, f"{field}.{key}")
                target_key = "id" if key == "instances" else "target"
                item = self._target(_text(record, target_key, f"{field}.{key}"))
                attributes = _scientific_record(record, detail, f"{field}.{key}")
                attributes.pop(target_key, None)
                if "reference" in attributes or "available" in attributes:
                    raise CompiledRetrievalError(f"{field}.{key} reference and availability are derived from {target_key}")
                item.update(attributes)
                result[key].append(item)
        result["edges"] = []
        for edge in self._edges.get(reference, []):
            item = self._target(edge["target"])
            item.update({key: value for key, value in edge.items() if key != "target"})
            result["edges"].append(item)
        result["claims"] = copy.deepcopy(self._claims.get(reference, []))
        endpoints = [item for key in ("depends_on", "relations", "instances", "edges") for item in result[key]]
        endpoints.extend(item for claim in result["claims"] for key in ("positions", "timeline") for item in claim[key])
        result["gaps"] = self._unresolved(endpoints)
        if source_ref and source_ref not in self._sources:
            result["gaps"].append({"reference": source_ref, "reason": "unresolved-source"})
        return result

    @staticmethod
    def _unresolved(value: Any) -> list[dict[str, str]]:
        gaps: list[dict[str, str]] = []
        if isinstance(value, dict):
            if value.get("available") is False:
                gaps.append({"reference": value["reference"], "reason": "unresolved-reference"})
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("available") is False:
                    gaps.append({"reference": item["reference"], "reason": "unresolved-reference"})
        return list({(gap["reference"], gap["reason"]): gap for gap in gaps}.values())

    @staticmethod
    def _semantic_text(entry: Mapping[str, Any]) -> str:
        seen: set[str] = set()
        texts: list[str] = []
        for text in _semantic_values(dict(entry)):
            key = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
            if key and key not in seen:
                seen.add(key)
                texts.append(text)
        return "\n".join(texts)

    def _semantic_entry(self, reference: str) -> dict[str, Any]:
        """Index scientific JSON, excluding lookup addresses at their own locations."""
        entry = self._entries[reference]
        controls = {"reference", "gaps", "depends_on", "relations", "instances", "edges", "claims"}
        result = {key: value for key, value in entry.items() if key not in controls}
        for key in ("epistemic", "notation", "distinguish_from"):
            result[key] = _scientific_text(entry[key])
        # Only this declared acquisition container is metadata. A scientific
        # qualifier named provenance, id, path or hash is not recursively hidden.
        result["evidence"] = _scientific_text([
            {key: value for key, value in record.items() if key != "provenance" or not isinstance(value, Mapping)}
            for record in entry["evidence"]
        ])
        for key in ("depends_on", "relations", "instances"):
            target_key = "id" if key == "instances" else "target"
            records = []
            for raw, endpoint in zip(_list(self._nodes[reference].get(key), key), entry[key]):
                record = {} if isinstance(raw, str) else dict(raw)
                record.pop(target_key, None)
                records.append(_scientific_text(record))
                records.append(endpoint.get("name", ""))
            result[key] = records
        result["edges"] = [_scientific_text({key: value for key, value in edge.items() if key not in {"target", "direction", "origin"}})
                           for edge in self._edges.get(reference, [])]
        result["claims"] = []
        for claim in entry["claims"]:
            value = {key: item for key, item in claim.items() if key not in {"positions", "timeline"}}
            for key in ("positions", "timeline"):
                value[key] = [{field: item for field, item in endpoint.items() if field not in {"reference", "available"}}
                              for endpoint in claim[key]]
            result["claims"].append(_scientific_text(value))
        return result

    def semantic_text(self, reference: str) -> str:
        """Full compiled semantic text, without identifiers or storage metadata."""
        if reference not in self._texts:
            raise CompiledRetrievalError(f"unknown knowledge reference: {reference}")
        return self._texts[reference]

    def get(self, reference: str) -> dict[str, Any]:
        """Return a complete entry by an existing, exact reference."""
        if reference not in self._entries:
            raise CompiledRetrievalError(f"unknown knowledge reference: {reference}")
        return copy.deepcopy(self._entries[reference])

    def _preview(self, reference: str) -> dict[str, Any]:
        entry = self._entries[reference]
        return copy.deepcopy({
            "reference": reference,
            "name": entry["name"],
            "source": entry["source"],
            "kind": entry.get("kind", ""),
            "gloss": entry["surfaces"].get("gloss", entry.get("statement", "")),
            "sense": entry["surfaces"].get("sense_key", ""),
            "depends_on": entry["depends_on"],
        })

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise CompiledRetrievalError("query must be text")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise CompiledRetrievalError("limit must be a nonnegative integer")
        return [
            {**self._preview(reference), "score": score}
            for reference, score in self._index.rank(query, limit)
        ]

    def overview(self) -> dict[str, Any]:
        """Author-provided source and sense organization, with explicit layers."""
        return {
            "sources": [
                {"reference": ref, **copy.deepcopy(source), "entries": sum(n.get("paper") == ref for n in self._nodes.values())}
                for ref, source in self._sources.items()
            ],
            "terms": [
                {"reference": ref, "term": _text(term, "term", f"terms[{ref}]"), "senses": len(_list(term.get("senses"), f"terms[{ref}].senses"))}
                for ref, term in self._terms.items()
            ],
            "layers": [
                {"reference": layer, "name": layer, "entries": sum(n.get("layer") == layer for n in self._nodes.values())}
                for layer in dict.fromkeys(n["layer"] for n in self._nodes.values() if n.get("layer"))
            ],
        }

    def inventory(self, term: str) -> dict[str, Any]:
        """Enumerate explicit compiled declarations, without ranking or merging."""
        if not isinstance(term, str) or not _term_key(term):
            raise CompiledRetrievalError("term must be nonempty text")
        key = _term_key(term)

        def member(reference: str) -> dict[str, Any]:
            if reference in self._entries:
                return {"available": True, **self.get(reference)}
            return self._target(reference)

        groups = []
        rows = []
        for reference, declaration in self._terms.items():
            field = f"terms[{reference}]"
            name = _text(declaration, "term", field)
            if key not in {_term_key(reference), _term_key(name)}:
                continue
            senses = []
            for raw in _list(declaration.get("senses"), f"{field}.senses"):
                sense = _mapping(raw, f"{field}.sense")
                item = member(_text(sense, "id", f"{field}.sense"))
                item["declaration"] = _fields(sense, ("gloss", "context"), f"{field}.sense")
                senses.append(item)
            groups.append({
                "reference": reference, "term": name,
                "disambiguation": _texts(declaration, "disambiguation", field),
                "senses": senses,
            })
            rows.extend(senses)
        uses = [
            member(reference)
            for reference, entry in self._entries.items()
            if any(_term_key(head) == key for head in entry["surfaces"].get("head_terms", []))
        ]
        rows.extend(uses)
        gaps = self._unresolved(rows)
        for row in rows:
            gaps.extend(row.get("gaps", []))
        if not groups and not uses:
            gaps.append({"term": term, "reason": "unmatched-term"})
        return {
            "term": term, "scope": "compiled declarations", "matched": bool(groups or uses),
            "groups": groups, "uses": uses,
            "gaps": list({tuple(sorted(gap.items())): gap for gap in gaps}.values()),
            "source_corpus_completeness": "not-certified",
        }

    def browse(self, reference: str | None = None, *, kind: str | None = None) -> dict[str, Any]:
        """Open a source, sense group, layer, or node; ambiguous handles need kind."""
        if reference is None:
            return self.overview()
        matches = []
        if reference in self._sources:
            matches.append("source")
        if reference in self._terms:
            matches.append("term")
        if reference in self._nodes:
            matches.append("node")
        if any(node.get("layer") == reference for node in self._nodes.values()):
            matches.append("layer")
        if kind is None and len(matches) > 1:
            raise CompiledRetrievalError(f"ambiguous navigation reference: {reference}; specify kind")
        selected_kind = kind if kind is not None else (matches[0] if matches else None)
        if selected_kind not in matches:
            raise CompiledRetrievalError(f"unknown navigation reference: {reference}")
        if selected_kind == "node":
            return self.get(reference)
        if selected_kind == "term":
            term = self._terms[reference]
            senses = []
            for raw in _list(term.get("senses"), "term.senses"):
                sense = _mapping(raw, "term.sense")
                item = self._target(_text(sense, "id", "term.sense"))
                item.update(_fields(sense, ("gloss", "context"), "term.sense"))
                senses.append(item)
            return {
                "reference": reference, "term": _text(term, "term", "term"),
                "disambiguation": _texts(term, "disambiguation", "term"), "senses": senses,
                "gaps": self._unresolved(senses),
            }
        field = "paper" if selected_kind == "source" else "layer"
        references = [ref for ref, node in self._nodes.items() if node.get(field) == reference]
        claims: list[dict[str, Any]] = []
        for ref in references:
            for claim in self._claims.get(ref, []):
                if not any(_json_equal(claim, existing) for existing in claims):
                    claims.append(copy.deepcopy(claim))
        heading = copy.deepcopy(self._sources[reference]) if selected_kind == "source" else {"name": reference}
        return {"reference": reference, **heading, "entries": [self._preview(ref) for ref in references], "claims": claims}

    def tree(self, reference: str | None = None, *, kind: str | None = None) -> dict[str, Any]:
        return self.browse(reference, kind=kind)

    def _evidence_entry(self, reference: str) -> dict[str, Any]:
        """Scientific content, excluding retrieval expansions and shared tables."""
        entry = self._entries[reference]
        result = {
            key: copy.deepcopy(value)
            for key, value in entry.items()
            if key not in {"surfaces", "claims", "edges", "gaps"}
        }
        # Sense labels and exclusions may contain authored scope information;
        # retain them verbatim even when the other surfaces are retrieval-only.
        if entry["surfaces"].get("sense_key"):
            result["sense"] = entry["surfaces"]["sense_key"]
        if entry["surfaces"].get("not_this"):
            result["not_this"] = copy.deepcopy(entry["surfaces"]["not_this"])
        for field in ("depends_on", "relations", "instances"):
            raw = _list(self._nodes[reference].get(field), field)
            result[field] = [self._reference_content(item, authored=record if isinstance(record, Mapping) else {})
                             for item, record in zip(result[field], raw)]
        return result

    @staticmethod
    def _reference_content(item: Mapping[str, Any], *, authored: Mapping[str, Any] = {}) -> dict[str, Any]:
        """Compact derived bibliography while keeping explicitly authored fields."""
        return {key: copy.deepcopy(value) for key, value in item.items()
                if key not in {"source", "year"} or key in authored}

    def _evidence_edges(self, reference: str) -> list[dict[str, Any]]:
        """Represent each authored directed edge once, with explicit endpoints."""
        edges = []
        for edge in self._edges.get(reference, []):
            owner = self._reference_content(self._target(reference))
            neighbor = self._reference_content(self._target(edge["target"]))
            source, target = (owner, neighbor) if edge["direction"] == "out" else (neighbor, owner)
            detail = {
                key: copy.deepcopy(value)
                for key, value in edge.items()
                if key not in {"target", "direction", "origin"}
            }
            edges.append({"source": source, "target": target, **detail})
        return edges

    def pack(self, selected_refs: Iterable[str], byte_budget: int) -> dict[str, Any]:
        """Pack whole scientific entries and share repeated claims and edges.

        The budget counts the complete UTF-8 JSON payload (including gaps).
        Retrieval expansions stay in ``get`` and the search index. Claims and
        edges are shared only when their complete structures are equal; their
        positions and endpoints retain the original lookup references.
        Edges connect packed entries only. A complete claim group enters the
        packet when at least two distinct positions refer to packed entries;
        its other positions, timeline and qualifications are retained in full.
        The complete navigable neighborhood remains available through ``get``.
        Explicit dependencies absent from the packed entries are reported. A
        fitting pack makes no claim about scientific evidence completeness.
        """
        if not isinstance(byte_budget, int) or isinstance(byte_budget, bool) or byte_budget < 0:
            raise CompiledRetrievalError("byte_budget must be a nonnegative integer")
        if isinstance(selected_refs, str):
            raise CompiledRetrievalError("selected_refs must contain lookup references")
        refs = list(selected_refs)
        if any(not isinstance(ref, str) for ref in refs):
            raise CompiledRetrievalError("selected_refs must contain lookup references")
        refs = list(dict.fromkeys(refs))
        packed: list[str] = []
        entries = {ref: self._evidence_entry(ref) for ref in refs if ref in self._entries}
        edges_by_ref = {ref: self._evidence_edges(ref) for ref in entries}

        def bundle(included: list[str]) -> dict[str, Any]:
            included_refs = set(included)
            gaps: list[dict[str, Any]] = []
            for ref in refs:
                if ref not in included:
                    gaps.append({"reference": ref, "reason": "byte-budget" if ref in self._entries else "unknown-reference"})
            for ref in included:
                gaps.extend({"from": ref, **gap} for gap in self._entries[ref]["gaps"])
                for dependency in self._entries[ref]["depends_on"]:
                    if dependency["available"] and dependency["reference"] not in included:
                        gaps.append({"from": ref, "reference": dependency["reference"], "name": dependency["name"], "reason": "dependency-not-packed"})
            claims: list[dict[str, Any]] = []
            edges: list[dict[str, Any]] = []
            for ref in included:
                for claim in self._entries[ref]["claims"]:
                    positions = {position["reference"] for position in claim["positions"]}
                    if len(positions & included_refs) >= 2 and not any(_json_equal(claim, existing) for existing in claims):
                        claims.append(copy.deepcopy(claim))
                for edge in edges_by_ref[ref]:
                    if edge["source"]["reference"] in included_refs and edge["target"]["reference"] in included_refs and not any(_json_equal(edge, existing) for existing in edges):
                        edges.append(copy.deepcopy(edge))
            result = {"entries": [copy.deepcopy(entries[ref]) for ref in included], "claims": claims, "edges": edges, "gaps": gaps, "byte_budget": byte_budget, "bytes_used": 0}
            while True:
                size = len(_json_bytes(result))
                if result["bytes_used"] == size:
                    return result
                result["bytes_used"] = size

        result = bundle(packed)
        if result["bytes_used"] > byte_budget:
            raise CompiledRetrievalError("byte budget cannot describe the selection and its gaps")
        for ref in refs:
            if ref in self._entries:
                candidate = bundle(packed + [ref])
                if candidate["bytes_used"] <= byte_budget:
                    packed.append(ref)
                    result = candidate
        return result
