"""Explicit raw-source evidence retrieval; fragments never confer graph identity.

BM25 shares the graph text lane's tokenizer and constants, but indexes byte
spans directly rather than constructing synthetic knowledge nodes. Sources and
the manifest are checked again before every search; the in-memory index is
derived and does not write authority files or graph artifacts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import canonical_json, sha256_json
from .file_io import file_signature, same_file_metadata
from .json_schema import validate_json_schema
from .query import _BM25_B, _BM25_K1, MAX_QUERY_LENGTH, MAX_QUERY_TERMS, _tokens

MANIFEST_SCHEMA = "kgdistiller-source-evidence-manifest-v1"
RESULT_SCHEMA = "kgdistiller-source-evidence-result-v1"
PROJECTION = "kgdistiller-source-evidence-bm25-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_CORPUS_BYTES = 64 * 1024 * 1024
MAX_DOCUMENTS = 128
MAX_FRAGMENTS = 50_000
MAX_FRAGMENT_BYTES = 6144
MAX_RESULT_BYTES = 200_000
MAX_INDEX_TOKENS = 2_000_000
_LINE_LABEL = re.compile(r"^L\d{6}(?:[ \t]|$)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_ALGORITHM = re.compile(r"^Algorithm\s+[A-Za-z0-9]+\b", re.I)
_CAPTION = re.compile(r"^(?:Figure|Table)\s+[A-Za-z0-9]+\b", re.I)


class SourceEvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")

    def to_payload(self) -> dict[str, str]:
        return {"kind": "kgdistiller-source-evidence-error", "code": self.code, "message": self.message}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite manifest value: {value}")


def _schema_check(payload: Any, schema: str) -> None:
    resource = resources.files("kgdistiller").joinpath("schemas", schema + ".schema.json")
    errors = validate_json_schema(payload, json.loads(resource.read_text(encoding="utf-8")))
    if errors:
        error = errors[0]
        raise SourceEvidenceError("invalid-source-evidence-contract", f"{schema} at {'.'.join(map(str,error.path))}: {error.message}")


def validate_source_evidence_manifest(payload: dict[str, Any]) -> None:
    _schema_check(payload, MANIFEST_SCHEMA)
    ids = [document["doc_id"] for document in payload["documents"]]
    paths = [document["path"] for document in payload["documents"]]
    if len(set(ids)) != len(ids) or len(set(paths)) != len(paths):
        raise SourceEvidenceError("invalid-source-manifest", "source document IDs and paths must be unique")
    for path in paths:
        parts = PurePosixPath(path).parts
        if not parts or PurePosixPath(path).as_posix() != path or PurePosixPath(path).is_absolute() or any(part in {"..", "."} for part in parts) or "\\" in path or "\x00" in path or re.match(r"^[A-Za-z]:", path):
            raise SourceEvidenceError("unsafe-source-path", "source paths must be relative POSIX paths without traversal")
        if any(parts[index] in {"knowledge", ".knowledge"} and parts[index+1] == "graph" for index in range(len(parts)-1)):
            raise SourceEvidenceError("unsafe-source-path", "derived graph artifacts are not original source evidence")


def _read_regular(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os,"O_BINARY",0) | getattr(os,"O_CLOEXEC",0) | getattr(os,"O_NONBLOCK",0) | getattr(os,"O_NOFOLLOW",0)
    descriptor = None
    try:
        path_info = path.lstat()
        if not stat.S_ISREG(path_info.st_mode):
            raise SourceEvidenceError("invalid-source-file", "source evidence requires regular files")
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceEvidenceError("invalid-source-file", "source evidence requires regular files")
        if not same_file_metadata(path_info, before):
            raise SourceEvidenceError("stale-source", "source evidence changed while opening")
        if before.st_size > limit:
            raise SourceEvidenceError("source-too-large", "source evidence file exceeds the byte bound")
        data = bytearray()
        while len(data) <= limit:
            piece = os.read(descriptor,min(65536,limit+1-len(data)))
            if not piece:
                break
            data.extend(piece)
        if len(data) > limit:
            raise SourceEvidenceError("source-too-large", "source evidence file exceeds the byte bound")
        after = os.fstat(descriptor)
        if file_signature(before) != file_signature(after) or not same_file_metadata(path_info, path.lstat()):
            raise SourceEvidenceError("stale-source", "source evidence changed while reading")
        return bytes(data)
    except SourceEvidenceError:
        raise
    except OSError as error:
        raise SourceEvidenceError("source-unavailable", "source evidence file is missing, unreadable or a symlink") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _document_path(root: Path, relative: str) -> Path:
    path = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SourceEvidenceError("unsafe-source-path", "source evidence paths may not contain symlinks")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError,ValueError,RuntimeError) as error:
        raise SourceEvidenceError("unsafe-source-path", "source evidence path is unavailable or escapes manifest root") from error
    return resolved


def _source_hashes(raw: bytes) -> tuple[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceEvidenceError("invalid-source-encoding", "source evidence must be valid UTF-8") from error
    normalized = text.replace("\r\n","\n").replace("\r","\n").encode("utf-8")
    return _sha(raw), _sha(normalized)


def _content_line(raw: bytes) -> str:
    # Corpus line labels are navigation labels, not scientific text or identity.
    return _LINE_LABEL.sub("",raw.decode("utf-8")).strip()


def _split_span(raw: bytes, start: int, end: int) -> list[tuple[int,int]]:
    result = []
    cursor = start
    while cursor < end:
        stop = min(end,cursor+MAX_FRAGMENT_BYTES)
        if stop < end:
            line_break = raw.rfind(b"\n",cursor,stop)
            if line_break > cursor:
                stop = line_break+1
            else:
                while stop > cursor and raw[stop] & 0xC0 == 0x80:
                    stop -= 1
        result.append((cursor,stop))
        cursor = stop
    return result


def _blocks(raw: bytes) -> list[tuple[int,int,str,list[dict[str,Any]]]]:
    """Detect only structural runs; headings provide navigation, never nodes."""
    lines = []
    cursor = 0
    for line in raw.splitlines(keepends=True):
        lines.append((cursor,cursor+len(line),_content_line(line)))
        cursor += len(line)
    result = []; headings: list[dict[str,Any]] = []; index = 0
    while index < len(lines):
        start,end,text = lines[index]
        if not text:
            index += 1; continue
        match = _HEADING.match(text)
        if match:
            level = len(match.group(1))
            if len(match.group(2).encode("utf-8")) > 8192:
                raise SourceEvidenceError("source-too-large", "source heading exceeds the navigation metadata bound")
            headings = [heading for heading in headings if heading["level"] < level]
            headings.append({"level":level,"text":match.group(2),"byte_start":start,"byte_end":end,"identity_authority":False})
            result.append((start,end,"heading",copy.deepcopy(headings)))
            index += 1; continue
        stop = index+1
        kind = "paragraph"
        fence = re.match(r"^(`{3,}|~{3,})",text)
        if fence:
            kind = "algorithm" if any("algorithm" in h["text"].casefold() for h in headings) else "code"
            while stop < len(lines):
                closing = lines[stop][2].startswith(fence.group(1))
                stop += 1
                if closing:
                    break
        elif _ALGORITHM.match(text):
            kind = "algorithm"
            while stop < len(lines) and not _HEADING.match(lines[stop][2]) and not _CAPTION.match(lines[stop][2]) and not _ALGORITHM.match(lines[stop][2]):
                stop += 1
        elif text.startswith("|") and "|" in text[1:]:
            kind = "table"
            while stop < len(lines) and lines[stop][2].startswith("|"):
                stop += 1
        else:
            while stop < len(lines) and lines[stop][2] and not _HEADING.match(lines[stop][2]) and not lines[stop][2].startswith(("|","```","~~~")) and not _ALGORITHM.match(lines[stop][2]):
                stop += 1
        result.append((start,lines[stop-1][1],kind,copy.deepcopy(headings)))
        index = stop
    return result


def _projection(fragment: dict[str,Any]) -> dict[str,Any]:
    # All callers serialize this hash projection immediately; nested values stay read-only.
    return {key:value for key,value in fragment.items() if key not in {"projection_sha256","rank","score"}}


def _lexical_words(fragment: dict[str,Any]) -> Counter[str]:
    body = "\n".join(_LINE_LABEL.sub("",line) for line in fragment["text"].splitlines())
    headings = "\n".join(heading["text"] for heading in fragment["heading_context"])
    return Counter(_tokens(body) + _tokens(headings)*2)


def _finalize_budget(payload: dict[str,Any]) -> int:
    while True:
        size = len(canonical_json(payload).encode("utf-8"))
        if payload["budget"]["used_bytes"] == size:
            return size
        payload["budget"]["used_bytes"] = size


def validate_source_evidence_result(payload: dict[str,Any]) -> None:
    _schema_check(payload, RESULT_SCHEMA)
    if payload["query_sha256"] != _sha(payload["query"].encode("utf-8")):
        raise SourceEvidenceError("invalid-source-evidence-contract", "source evidence query hash does not match")
    if len(set(payload["doc_ids"])) != len(payload["doc_ids"]):
        raise SourceEvidenceError("invalid-source-evidence-contract", "source document filters are not unique")
    ranks = [fragment["rank"] for fragment in payload["fragments"]]
    selected_count = min(payload["requested_limit"], payload["matched_fragments"])
    if ranks != sorted(set(ranks)) or any(rank > selected_count for rank in ranks) or payload["matched_fragments"] > payload["fragment_count"] or payload["omitted_fragments"] != selected_count-len(ranks) or payload["omitted_fragments"] < len(payload["omissions"]):
        raise SourceEvidenceError("invalid-source-evidence-contract", "source evidence result counts or ranks are inconsistent")
    seen = set()
    for fragment in payload["fragments"]:
        start,end = fragment["byte_span"]["start"],fragment["byte_span"]["end"]
        block = fragment["block_byte_span"]
        text = fragment["text"].encode("utf-8")
        if fragment["fragment_id"] in seen or end <= start or len(text) != end-start or not block["start"] <= start < end <= block["end"]:
            raise SourceEvidenceError("invalid-source-evidence-contract", "fragment byte-span closure is invalid")
        seen.add(fragment["fragment_id"])
        expected_source_sha = fragment["raw_source_sha256"] if fragment["hash_mode"] == "raw-utf8" else fragment["normalized_source_sha256"]
        if fragment["doc_id"] not in payload["doc_ids"] or fragment["source_sha256"] != expected_source_sha:
            raise SourceEvidenceError("invalid-source-evidence-contract", "fragment source scope or hash convention is inconsistent")
        if fragment["content_sha256"] != _sha(text) or fragment["projection_sha256"] != sha256_json(_projection(fragment)):
            raise SourceEvidenceError("invalid-source-evidence-contract", "fragment content or projection hash does not match")
        identity = {"doc_id":fragment["doc_id"],"raw_source_sha256":fragment["raw_source_sha256"],"byte_span":fragment["byte_span"],"content_sha256":fragment["content_sha256"]}
        if fragment["fragment_id"] != "evidence:sha256:"+sha256_json(identity):
            raise SourceEvidenceError("invalid-source-evidence-contract", "fragment content address does not match source span")
        if fragment["partial"] != (start != block["start"] or end != block["end"]):
            raise SourceEvidenceError("invalid-source-evidence-contract", "fragment partial-state declaration is inconsistent")
        previous_level = 0
        previous_end = 0
        for heading in fragment["heading_context"]:
            if not previous_level < heading["level"] or not previous_end <= heading["byte_start"] < heading["byte_end"] <= block["end"]:
                raise SourceEvidenceError("invalid-source-evidence-contract", "heading navigation order or byte-span closure is invalid")
            previous_level, previous_end = heading["level"], heading["byte_end"]
    actual = len(canonical_json(payload).encode("utf-8"))
    if actual != payload["budget"]["used_bytes"] or actual > payload["budget"]["byte_budget"]:
        raise SourceEvidenceError("invalid-source-evidence-contract", "source evidence result exceeds its declared byte budget")


@dataclass
class SourceEvidenceIndex:
    manifest_path: Path
    root: Path
    manifest: dict[str,Any]
    manifest_file_sha256: str
    source_hashes: dict[str,tuple[str,str]]
    fragments: tuple[dict[str,Any],...]
    _term_counts: tuple[Counter[str],...]
    _manifest_sha256: str
    _fragment_binding_sha256: str

    @classmethod
    def from_manifest(cls, path: Path) -> "SourceEvidenceIndex":
        path = Path(path)
        if path.is_symlink():
            raise SourceEvidenceError("unsafe-source-path", "source evidence manifest may not be a symlink")
        try:
            path = path.resolve(strict=True)
        except (OSError,ValueError,RuntimeError) as error:
            raise SourceEvidenceError("source-unavailable", "source evidence manifest is unavailable") from error
        raw_manifest = _read_regular(path,MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError) as error:
            raise SourceEvidenceError("invalid-source-manifest", "manifest is not valid UTF-8 JSON") from error
        validate_source_evidence_manifest(manifest)
        root_value = Path(manifest["root"])
        root_candidate = root_value if root_value.is_absolute() else path.parent/root_value
        if root_candidate.is_symlink():
            raise SourceEvidenceError("unsafe-source-path", "source evidence root may not be a symlink")
        try:
            root = root_candidate.resolve(strict=True)
        except (OSError,ValueError,RuntimeError) as error:
            raise SourceEvidenceError("source-unavailable", "source evidence root is unavailable") from error
        if any(root.parts[index] in {"knowledge", ".knowledge"} and root.parts[index+1] == "graph" for index in range(len(root.parts)-1)):
            raise SourceEvidenceError("unsafe-source-path", "derived graph directories are not source evidence corpora")
        if not root.is_dir():
            raise SourceEvidenceError("unsafe-source-path", "manifest root must be a directory")
        hashes = {}; fragments = []; total_bytes = 0
        for document in manifest["documents"]:
            raw = _read_regular(_document_path(root,document["path"]),MAX_DOCUMENT_BYTES)
            total_bytes += len(raw)
            if total_bytes > MAX_CORPUS_BYTES:
                raise SourceEvidenceError("source-too-large", "source evidence corpus exceeds its byte bound")
            pair = _source_hashes(raw)
            selected = pair[0] if manifest["hash_mode"] == "raw-utf8" else pair[1]
            if document["expected_sha256"] != selected:
                raise SourceEvidenceError("stale-source", "source evidence differs from its declared expected hash")
            hashes[document["doc_id"]] = pair
            for start,end,kind,headings in _blocks(raw):
                for left,right in _split_span(raw,start,end):
                    fragment = {"doc_id":document["doc_id"],"source_url":document["source_url"],"source_version":document["source_version"],"source_type":document["source_type"],"source_kind":"raw-source-evidence","identity_authority":False,"complete_definition":False,"hash_mode":manifest["hash_mode"],"source_sha256":selected,"raw_source_sha256":pair[0],"normalized_source_sha256":pair[1],"byte_span":{"start":left,"end":right},"block_byte_span":{"start":start,"end":end},"fragment_type":kind,"partial":left!=start or right!=end,"heading_context":copy.deepcopy(headings),"text":raw[left:right].decode("utf-8"),"content_sha256":_sha(raw[left:right])}
                    fragment["fragment_id"] = "evidence:sha256:"+sha256_json({"doc_id":document["doc_id"],"raw_source_sha256":pair[0],"byte_span":fragment["byte_span"],"content_sha256":fragment["content_sha256"]})
                    fragment["projection_sha256"] = sha256_json(_projection(fragment))
                    fragments.append(fragment)
                    if len(fragments) > MAX_FRAGMENTS:
                        raise SourceEvidenceError("source-too-large", "source evidence exceeds its fragment bound")
        terms = tuple(_lexical_words(fragment) for fragment in fragments)
        if sum(sum(words.values()) for words in terms) > MAX_INDEX_TOKENS:
            raise SourceEvidenceError("source-too-large", "source evidence exceeds its token-index bound")
        binding = sha256_json([{ "fragment_id":fragment["fragment_id"],"projection_sha256":fragment["projection_sha256"]} for fragment in fragments])
        return cls(path,root,manifest,_sha(raw_manifest),hashes,tuple(fragments),terms,sha256_json(manifest),binding)

    def _check_sources(self) -> None:
        binding = sha256_json([{ "fragment_id":fragment["fragment_id"],"projection_sha256":fragment["projection_sha256"]} for fragment in self.fragments])
        if sha256_json(self.manifest) != self._manifest_sha256 or binding != self._fragment_binding_sha256 or any(fragment["projection_sha256"] != sha256_json(_projection(fragment)) for fragment in self.fragments):
            raise SourceEvidenceError("stale-source", "source evidence index or metadata changed in memory")
        if _sha(_read_regular(self.manifest_path,MAX_MANIFEST_BYTES)) != self.manifest_file_sha256:
            raise SourceEvidenceError("stale-source", "source evidence manifest changed since index construction")
        for document in self.manifest["documents"]:
            raw = _read_regular(_document_path(self.root,document["path"]),MAX_DOCUMENT_BYTES)
            if _source_hashes(raw) != self.source_hashes[document["doc_id"]]:
                raise SourceEvidenceError("stale-source", "source bytes or newline representation changed since indexing")

    def search(self, query: str, *, doc_ids: list[str] | None = None, limit: int = 10, byte_budget: int = 12000) -> dict[str,Any]:
        if not isinstance(query,str) or not query.strip() or len(query)>MAX_QUERY_LENGTH:
            raise SourceEvidenceError("invalid-source-query", f"source query must contain1..{MAX_QUERY_LENGTH}characters")
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100 or isinstance(byte_budget,bool) or not isinstance(byte_budget,int) or not 1<=byte_budget<=MAX_RESULT_BYTES:
            raise SourceEvidenceError("invalid-source-query", "source result count or byte budget is invalid")
        known = set(self.source_hashes)
        if doc_ids is not None and (not isinstance(doc_ids,list) or len(doc_ids)>MAX_DOCUMENTS or any(not isinstance(doc_id,str) or doc_id not in known for doc_id in doc_ids) or len(set(doc_ids))!=len(doc_ids)):
            raise SourceEvidenceError("invalid-source-filter", "document filters must contain exact known document IDs")
        self._check_sources()
        selected_records = [(fragment,words) for fragment,words in zip(self.fragments,self._term_counts) if doc_ids is None or fragment["doc_id"] in doc_ids]
        eligible = [fragment for fragment,_ in selected_records]
        terms = set(_tokens(query)[:MAX_QUERY_TERMS])
        words = [count for _,count in selected_records]
        average = sum(sum(count.values())for count in words)/(len(words) or 1) or 1.0
        frequencies = Counter(term for count in words for term in count)
        ranked=[]
        for fragment,count in zip(eligible,words):
            norm=_BM25_K1*(1-_BM25_B+_BM25_B*sum(count.values())/average)
            score=sum(math.log1p((len(words)-frequencies[term]+0.5)/(frequencies[term]+0.5))*count[term]*(_BM25_K1+1)/(count[term]+norm)for term in sorted(terms)if count[term])
            if score>0:
                ranked.append((score,fragment))
        ranked.sort(key=lambda item:(-item[0],item[1]["doc_id"],item[1]["byte_span"]["start"],item[1]["fragment_id"]))
        selected=ranked[:limit]
        result={"schema":RESULT_SCHEMA,"projection":PROJECTION,"query":query,"query_sha256":_sha(query.encode()),"manifest_sha256":sha256_json(self.manifest),"manifest_file_sha256":self.manifest_file_sha256,"source_kind":"raw-source-evidence","identity_authority":False,"complete_definition":False,"doc_ids":list(doc_ids)if doc_ids is not None else sorted(known),"fragment_count":len(self.fragments),"matched_fragments":len(ranked),"requested_limit":limit,"fragments":[],"omitted_fragments":len(selected),"diagnostics_truncated":False,"omissions":[],"budget":{"byte_budget":byte_budget,"used_bytes":0},"scoring":{"method":"bm25","k1":_BM25_K1,"b":_BM25_B,"heading_weight":2,"identity_authority":False}}
        if _finalize_budget(result)>byte_budget:
            raise SourceEvidenceError("source-budget-too-small", "source evidence budget cannot hold required metadata")
        omissions=[]
        for rank,(score,fragment)in enumerate(selected,start=1):
            candidate=copy.deepcopy(result);candidate["fragments"].append({**copy.deepcopy(fragment),"rank":rank,"score":score})
            if _finalize_budget(candidate)<=byte_budget:
                candidate["omitted_fragments"]-=1;result=candidate
            else:
                omissions.append({"fragment_id":fragment["fragment_id"],"reason":"fragment-exceeds-byte-budget"})
        for omission in omissions:
            candidate=copy.deepcopy(result);candidate["omissions"].append(omission)
            if _finalize_budget(candidate)<=byte_budget:
                result=candidate
            else:
                result["diagnostics_truncated"]=True
        _finalize_budget(result)
        self._check_sources()
        validate_source_evidence_result(result)
        return result
