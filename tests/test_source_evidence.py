from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.contracts import canonical_json, sha256_json
from kgdistiller.source_evidence import (
    SourceEvidenceError,
    SourceEvidenceIndex,
    validate_source_evidence_result,
)


class SourceEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="kgdistiller-source-evidence-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "manifest.json"

    def make_index(self, documents: dict[str,bytes], hash_mode="raw-utf8") -> SourceEvidenceIndex:
        items=[]
        for doc_id,raw in documents.items():
            path=self.root/(doc_id+".txt");path.write_bytes(raw)
            data=raw if hash_mode=="raw-utf8" else raw.decode().replace("\r\n","\n").replace("\r","\n").encode()
            items.append({"doc_id":doc_id,"path":path.name,"source_url":"https://example.org/"+doc_id,"source_version":"paper-v2","source_type":"paper-text","expected_sha256":hashlib.sha256(data).hexdigest()})
        manifest={"schema":"kgdistiller-source-evidence-manifest-v1","root":str(self.root),"hash_mode":hash_mode,"documents":items}
        self.path.write_text(json.dumps(manifest,ensure_ascii=False))
        return SourceEvidenceIndex.from_manifest(self.path)

    def test_table_rows_math_and_algorithm_order_are_preserved(self) -> None:
        raw=(b"# Training protocol\n\n| Model | MSE |\n|---|---:|\n| RRR | 0.79 |\n| Semantic | 8.20 |\n\n"
             b"## Algorithm 1\n\n```python\ntarget = softmax((t - C) / tau)\nloss = sum((prediction - target)**2)\n```\n\n"
             b"The domain is $H\\in\\mathbb{R}^{p\\times CN}$.\n")
        index=self.make_index({"paper":raw})
        tables=[fragment for fragment in index.fragments if fragment["fragment_type"]=="table"]
        algorithms=[fragment for fragment in index.fragments if fragment["fragment_type"]=="algorithm"]
        self.assertEqual(1,len(tables));self.assertEqual(1,len(algorithms))
        self.assertIn("| RRR | 0.79 |\n| Semantic | 8.20 |",tables[0]["text"])
        self.assertIn("target = softmax((t - C) / tau)\nloss = sum",algorithms[0]["text"])
        self.assertTrue(any("\\mathbb{R}^{p\\times CN}"in fragment["text"]for fragment in index.fragments))
        result=index.search("RRR Semantic MSE",limit=10,byte_budget=20000)
        self.assertTrue(any(fragment["fragment_type"]=="table"for fragment in result["fragments"]))
        validate_source_evidence_result(result)

    def test_exact_unicode_raw_spans_and_explicit_newline_hash_convention(self) -> None:
        raw="# 数学 σ\r\n\r\nμ输入与输出：x→y，条件α>0。\r\n".encode()
        index=self.make_index({"unicode":raw},"normalized-utf8")
        result=index.search("μ输入与输出 数学",byte_budget=20000)
        self.assertTrue(result["fragments"])
        for fragment in result["fragments"]:
            span=fragment["byte_span"]
            self.assertEqual(raw[span["start"]:span["end"]],fragment["text"].encode())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),fragment["raw_source_sha256"])
            self.assertNotEqual(fragment["raw_source_sha256"],fragment["normalized_source_sha256"])
            self.assertEqual(fragment["normalized_source_sha256"],fragment["source_sha256"])
        # Equal normalized text is insufficient to reuse raw offsets.
        (self.root/"unicode.txt").write_bytes(raw.replace(b"\r\n",b"\n"))
        with self.assertRaisesRegex(SourceEvidenceError,"stale-source"):
            index.search("数学")

    def test_binary_descriptor_preserves_crlf_and_ctrl_z_on_windows(self) -> None:
        from kgdistiller.source_evidence import _read_regular
        path = self.root / "binary.txt"
        raw = "α\r\nβ\x1aγ\r\n".encode()
        path.write_bytes(raw)
        actual_open, actual_read = os.open, os.read
        binary_flag = getattr(os, "O_BINARY", 0x8000)
        binary_descriptors = set()

        def windows_open(path, flags):
            descriptor = actual_open(path, flags & ~binary_flag if os.name != "nt" else flags)
            if flags & binary_flag:
                binary_descriptors.add(descriptor)
            return descriptor

        def windows_read(descriptor, count):
            data = actual_read(descriptor, count)
            return data if descriptor in binary_descriptors else data.replace(b"\r\n", b"\n").split(b"\x1a")[0]

        with patch("kgdistiller.source_evidence.os.O_BINARY", binary_flag, create=True), \
                patch("kgdistiller.source_evidence.os.open", side_effect=windows_open), \
                patch("kgdistiller.source_evidence.os.read", side_effect=windows_read):
            self.assertEqual(raw, _read_regular(path, 1000))

    def test_directory_is_rejected_before_platform_specific_open(self) -> None:
        from kgdistiller.source_evidence import _read_regular
        with (
            patch("kgdistiller.source_evidence.os.open", side_effect=AssertionError("directory must not be opened")),
            self.assertRaisesRegex(SourceEvidenceError, "regular files"),
        ):
            _read_regular(self.root, 1000)

    def test_replacement_between_stat_and_open_is_rejected(self) -> None:
        from kgdistiller.source_evidence import _read_regular
        source, replacement = self.root / "source.txt", self.root / "replacement.txt"
        source.write_bytes(b"expected\n")
        replacement.write_bytes(b"replaced\n")
        actual_open = os.open

        def changed_open(path, flags):
            return actual_open(replacement, flags)

        with (
            patch("kgdistiller.source_evidence.os.open", side_effect=changed_open),
            self.assertRaisesRegex(SourceEvidenceError, "stale-source"),
        ):
            _read_regular(source, 1000)

    def test_content_addresses_do_not_promote_headings_to_graph_identity(self) -> None:
        index=self.make_index({"scope":b"# --[[Invented canonical identity]]--\n\ncanonical evidence only\n"})
        result=index.search("canonical",byte_budget=20000)
        self.assertFalse(result["identity_authority"])
        self.assertFalse(result["complete_definition"])
        for fragment in result["fragments"]:
            self.assertTrue(fragment["fragment_id"].startswith("evidence:sha256:"))
            self.assertNotIn("node_id",fragment)
            self.assertNotIn("aliases",fragment)
            self.assertFalse(fragment["identity_authority"])
            self.assertFalse(fragment["complete_definition"])
            self.assertTrue(all(not heading["identity_authority"]for heading in fragment["heading_context"]))
        self.assertEqual(result,index.search("canonical",byte_budget=20000))

    def test_exact_document_filters_do_not_cross_scopes(self) -> None:
        index=self.make_index({"paper-a":b"# first\n\nalpha calibration\n","paper-b":b"# second\n\nalpha classification\n"})
        result=index.search("alpha",doc_ids=["paper-b"],byte_budget=20000)
        self.assertTrue(result["fragments"])
        self.assertEqual({"paper-b"},{fragment["doc_id"]for fragment in result["fragments"]})
        self.assertEqual([],index.search("alpha",doc_ids=[])['fragments'])
        for invalid in (["missing"],["paper-a","paper-a"],[[]]):
            with self.assertRaises(SourceEvidenceError):
                index.search("alpha",doc_ids=invalid)

    def test_oversized_blocks_split_only_at_valid_utf8_boundaries_and_are_partial(self) -> None:
        raw=("# Scope\n\n"+"数学σ "+"数学α "*80+"\n").encode()
        with patch("kgdistiller.source_evidence.MAX_FRAGMENT_BYTES",64):
            index=self.make_index({"long":raw})
        pieces=[fragment for fragment in index.fragments if fragment["fragment_type"]=="paragraph"]
        self.assertGreater(len(pieces),1)
        self.assertTrue(all(fragment["partial"]and not fragment["complete_definition"]for fragment in pieces))
        self.assertEqual(raw[pieces[0]["block_byte_span"]["start"]:pieces[0]["block_byte_span"]["end"]],b"".join(fragment["text"].encode()for fragment in pieces))
        self.assertTrue(all(len(fragment["text"].encode())<=64 for fragment in pieces))
        validate_source_evidence_result(index.search("数学α",limit=100,byte_budget=200000))

    def test_labelled_paper_algorithm_keeps_exact_original_bytes(self) -> None:
        raw=b"L000001 # Method\nL000002 \nL000003 Algorithm 1 teacher\nL000004 \nL000005 t\nL000006 =\nL000007 softmax((t-C)/tau)\nL000008 \nL000009 # Next section\nL000010 observation\n"
        index=self.make_index({"algorithm":raw})
        fragment=next(fragment for fragment in index.fragments if fragment["fragment_type"]=="algorithm")
        self.assertIn("L000007 softmax((t-C)/tau)",fragment["text"])
        self.assertNotIn("Next section",fragment["text"])
        result=index.search("softmax teacher",byte_budget=20000)
        self.assertTrue(any(fragment["fragment_type"]=="algorithm"for fragment in result["fragments"]))

    def test_missing_corrupt_invalid_utf8_and_nonregular_files_fail_explicitly(self) -> None:
        self.make_index({"source":b"alpha source\n"})
        (self.root/"source.txt").write_bytes(b"different source\n")
        with self.assertRaisesRegex(SourceEvidenceError,"stale-source"):
            SourceEvidenceIndex.from_manifest(self.path)
        (self.root/"source.txt").write_bytes(b"\xff")
        with self.assertRaisesRegex(SourceEvidenceError,"invalid-source-encoding"):
            SourceEvidenceIndex.from_manifest(self.path)
        (self.root/"source.txt").unlink()
        with self.assertRaises(SourceEvidenceError):
            SourceEvidenceIndex.from_manifest(self.path)
        with self.assertRaises(SourceEvidenceError):
            SourceEvidenceIndex.from_manifest(self.root/"missing.json")
        (self.root/"source.txt").mkdir()
        with self.assertRaisesRegex(SourceEvidenceError,"regular files"):
            SourceEvidenceIndex.from_manifest(self.path)

    def test_traversal_symlinks_and_graph_artifacts_are_rejected(self) -> None:
        self.make_index({"source":b"alpha\n"})
        manifest=json.loads(self.path.read_text())
        for relative in ("../secret.txt","/secret.txt","C:/secret.txt",".knowledge/graph/nodes.jsonl","source.txt/../source.txt","./source.txt"):
            invalid=copy.deepcopy(manifest);invalid["documents"][0]["path"]=relative;self.path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(SourceEvidenceError,"unsafe-source-path"):
                SourceEvidenceIndex.from_manifest(self.path)
        self.path.write_text(json.dumps(manifest));(self.root/"source.txt").unlink()
        (self.root/"source.txt").symlink_to(self.path)
        with self.assertRaisesRegex(SourceEvidenceError,"symlinks"):
            SourceEvidenceIndex.from_manifest(self.path)

    def test_source_and_manifest_mutation_are_stale_not_silent_rebuilds(self) -> None:
        index=self.make_index({"source":b"alpha source\n"})
        original=self.path.read_bytes();self.path.write_bytes(original+b" ")
        with self.assertRaisesRegex(SourceEvidenceError,"manifest changed"):
            index.search("alpha")
        self.path.write_bytes(original);(self.root/"source.txt").write_bytes(b"alpha new source\n")
        with self.assertRaisesRegex(SourceEvidenceError,"stale-source"):
            index.search("alpha")

    def test_lexical_projection_is_reused_and_untrusted_memory_changes_fail(self) -> None:
        index=self.make_index({"source":b"alpha observed source\n"})
        from kgdistiller.query import _tokens
        with patch("kgdistiller.source_evidence._tokens",wraps=_tokens)as tokenizer:
            index.search("alpha")
            self.assertEqual(1,tokenizer.call_count)
        index.fragments[0]["text"]="invented source"
        with self.assertRaisesRegex(SourceEvidenceError,"changed in memory"):
            index.search("alpha")

    def test_result_budget_never_cuts_text_and_counts_omissions(self) -> None:
        index=self.make_index({"source":("# Experiment\n\n"+"alpha target "*100+"\n\nalpha concise result\n").encode()})
        for budget in range(800,6000,29):
            try:
                result=index.search("alpha",limit=10,byte_budget=budget)
            except SourceEvidenceError as error:
                self.assertEqual("source-budget-too-small",error.code);continue
            self.assertLessEqual(len(canonical_json(result).encode()),budget)
            validate_source_evidence_result(result)
            if result["omitted_fragments"]:
                self.assertTrue(result["omissions"]or result["diagnostics_truncated"])
            for fragment in result["fragments"]:
                self.assertEqual(fragment["content_sha256"],hashlib.sha256(fragment["text"].encode()).hexdigest())
        with self.assertRaises(SourceEvidenceError):
            index.search("alpha",byte_budget=1)

    def test_malformed_results_and_hash_conventions_fail_closed(self) -> None:
        result=self.make_index({"source":b"alpha source\n"}).search("alpha",byte_budget=12000)
        for kind in ("authority","projection","query","count","partial"):
            forged=copy.deepcopy(result)
            if kind=="authority":forged["fragments"][0]["identity_authority"]=True
            if kind=="projection":forged["fragments"][0]["text"]+=" changed"
            if kind=="query":forged["query_sha256"]="0"*64
            if kind=="count":forged["omitted_fragments"]=1
            if kind=="partial":forged["fragments"][0]["partial"]=True
            with self.subTest(kind=kind),self.assertRaises(SourceEvidenceError):
                validate_source_evidence_result(forged)

    def test_document_and_manifest_byte_limits_and_empty_sources(self) -> None:
        index=self.make_index({"empty":b""})
        self.assertEqual([],index.search("alpha")["fragments"])
        self.make_index({"large":b"alpha large source\n"})
        with patch("kgdistiller.source_evidence.MAX_DOCUMENT_BYTES",4),self.assertRaisesRegex(SourceEvidenceError,"source-too-large"):
            SourceEvidenceIndex.from_manifest(self.path)
        with patch("kgdistiller.source_evidence.MAX_MANIFEST_BYTES",4),self.assertRaisesRegex(SourceEvidenceError,"source-too-large"):
            SourceEvidenceIndex.from_manifest(self.path)

    def test_manifest_rejects_duplicate_fields_and_nonfinite_json(self) -> None:
        self.make_index({"source":b"alpha source\n"})
        original=self.path.read_text()
        for raw in ('{"root":"ignored",'+original[1:], original[:-1]+',"unexpected":NaN}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                with self.assertRaisesRegex(SourceEvidenceError,"invalid-source-manifest"):
                    SourceEvidenceIndex.from_manifest(self.path)

    def test_manifest_and_root_symlinks_are_rejected_before_resolution(self) -> None:
        self.make_index({"source":b"alpha source\n"})
        link=self.root/"linked-manifest.json"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(SourceEvidenceError,"manifest may not be a symlink"):
            SourceEvidenceIndex.from_manifest(link)
        root_link=self.root/"linked-root"
        root_link.symlink_to(self.root, target_is_directory=True)
        manifest=json.loads(self.path.read_text());manifest["root"]=str(root_link)
        self.path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SourceEvidenceError,"root may not be a symlink"):
            SourceEvidenceIndex.from_manifest(self.path)

    def test_rehashed_result_cannot_forge_authority_or_navigation_scope(self) -> None:
        from kgdistiller.source_evidence import _finalize_budget, _projection
        result=self.make_index({"source":b"# Method\n\nalpha source\n"}).search("alpha",limit=1,byte_budget=12000)
        for kind in ("zero-authority", "zero-definition", "zero-scoring-authority", "zero-fragment-authority", "zero-heading-authority", "zero-top-definition", "rank", "heading-span", "duplicate-heading"):
            forged=copy.deepcopy(result);fragment=forged["fragments"][0]
            if kind=="zero-authority": forged["identity_authority"]=0
            if kind=="zero-definition": fragment["complete_definition"]=0
            if kind=="zero-scoring-authority": forged["scoring"]["identity_authority"]=0
            if kind=="zero-fragment-authority": fragment["identity_authority"]=0
            if kind=="zero-heading-authority": fragment["heading_context"][0]["identity_authority"]=0
            if kind=="zero-top-definition": forged["complete_definition"]=0
            if kind=="rank": fragment["rank"]=100
            if kind=="heading-span": fragment["heading_context"][0]["byte_start"]=100
            if kind=="duplicate-heading": fragment["heading_context"].append(copy.deepcopy(fragment["heading_context"][0]))
            fragment["projection_sha256"]=sha256_json(_projection(fragment))
            _finalize_budget(forged)
            with self.subTest(kind=kind),self.assertRaisesRegex(SourceEvidenceError,"invalid-source-evidence-contract"):
                validate_source_evidence_result(forged)


if __name__=="__main__":
    unittest.main()
