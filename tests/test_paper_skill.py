from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/extract-paper-markdown/scripts'
spec = importlib.util.spec_from_file_location('paper_prepare_tests', SCRIPTS/'prepare_paper.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)

class PaperMarkdownSkillTests(unittest.TestCase):
    def archive(self, entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for name, content in entries:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
        return output.getvalue()

    def package(self, root):
        archive = root/'input.tar.gz'
        archive.write_bytes(self.archive([('论文/main.tex', b'\\documentclass{article}\n\\input{section}\n'), ('论文/section.tex', b'\\section{Method}\nA supported claim.\n'), ('fig.pdf', b'%PDF-discard'), ('fig.png',b'png')]))
        out = root/'paper'
        prepare.prepare(archive,out,'https://arxiv.org/abs/1512.03385v1')
        return out

    def validate(self, out, extra=()):
        return subprocess.run([sys.executable,str(SCRIPTS/'validate_paper_markdown.py'),'--manifest',str(out/'source.json'),'--source-only',*extra],capture_output=True,text=True)

    def test_archive_is_text_only_and_queryable_without_pdf_or_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.package(Path(tmp))
            self.assertEqual('https://arxiv.org/abs/1512.03385\n',(out/'link.txt').read_text())
            self.assertFalse(list(out.rglob('*.pdf')))
            self.assertFalse((out/'evidence').exists())
            self.assertFalse((out/'paper.md').exists())
            result=self.validate(out)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual(2,json.loads(result.stdout)['files'])

    def test_tampered_source_and_extra_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.package(Path(tmp))
            (out/'source/论文/section.tex').write_text('changed')
            (out/'source/extra.tex').write_text('extra')
            result=self.validate(out)
            self.assertNotEqual(0,result.returncode)
            self.assertIn('hash/size mismatch',result.stderr)
            self.assertIn('inventory differs',result.stderr)

    def test_unsafe_archive_entries_fail_without_writing_outside(self):
        for name in ['../escape.tex','/escape.tex','a/../../escape.tex','C:/evil.tex']:
            with self.subTest(name=name), self.assertRaises(prepare.PrepareError):
                prepare.source_members(self.archive([(name,b'\\documentclass{article}')]))
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as tar:
            member=tarfile.TarInfo('alias.tex');member.type=tarfile.SYMTYPE;member.linkname='../outside';tar.addfile(member)
        with self.assertRaisesRegex(prepare.PrepareError,'links'):
            prepare.source_members(output.getvalue())

    def test_duplicate_archive_paths_and_binary_tex_fail(self):
        for entries in [[('main.tex',b'x'),('main.tex',b'y')],[('main.tex',b'%PDF-fake')]]:
            with self.assertRaises(prepare.PrepareError):
                prepare.source_members(self.archive(entries))

    def test_rejects_pdf_input_missing_version_and_source_less_archive(self):
        for data in [b'%PDF-file',self.archive([('figure.pdf',b'%PDF-file')])]:
            with self.assertRaises(prepare.PrepareError):prepare.source_members(data)
        with self.assertRaises(prepare.PrepareError):prepare.arxiv_identity('https://arxiv.org/abs/1512.03385')
        self.assertEqual(('hep-th/9901001','v2'),prepare.arxiv_identity('https://arxiv.org/abs/hep-th/9901001v2'))

    def test_evidence_pdf_and_multiline_link_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.package(Path(tmp));(out/'evidence').mkdir();(out/'source.pdf').write_bytes(b'%PDF-')
            (out/'link.txt').write_text('https://arxiv.org/abs/1512.03385\nextra\n')
            result=self.validate(out)
            self.assertNotEqual(0,result.returncode)
            for text in ['evidence directory','PDF files','link.txt']:self.assertIn(text,result.stderr)

    def test_manifest_escape_and_bad_optional_markdown_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.package(Path(tmp));m=json.loads((out/'source.json').read_text());m['files'][0]['path']='../escape.tex';(out/'source.json').write_text(json.dumps(m))
            result=self.validate(out);self.assertIn('escapes its root',result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            out=self.package(Path(tmp));md=out/'paper.md';md.write_text('<!-- qlpaper-source: file=source/论文/main.tex; lines=1-999 -->\n![bad](x.png)\n')
            result=self.validate(out,['--markdown',str(md)])
            self.assertIn('line range out of bounds',result.stderr);self.assertIn('forbidden Markdown image',result.stderr)

    def test_bilingual_reading_requires_complete_aligned_blocks_and_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.package(Path(tmp))
            args=[sys.executable,str(SCRIPTS/'validate_paper_markdown.py'),'--manifest',str(out/'source.json')]
            self.assertNotEqual(0,subprocess.run(args,capture_output=True).returncode)
            (out/'paper.md').write_text('<!-- qlpaper-block: b001 -->\n# Method\n\n<!-- qlpaper-block: b002 -->\nResidual learning uses $x+y$.\n')
            (out/'paper_ch.md').write_text('<!-- qlpaper-block: b001 -->\n# Method\n\n<!-- qlpaper-block: b002 -->\nResidual learning 使用 $x+y$。\n')
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
            (out/'paper_ch.md').write_text('<!-- qlpaper-block: b001 -->\n摘要 $z$。\n')
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertIn('block order/coverage',result.stderr)
            self.assertIn('mathematical expressions differ',result.stderr)

    def test_plain_gzipped_tex_and_existing_output(self):
        import gzip
        content=b'\\documentclass{article}\nText\n'
        self.assertEqual(content,prepare.source_members(gzip.compress(content))[0]['main.tex'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=self.package(root)
            with self.assertRaisesRegex(prepare.PrepareError,'empty'):
                prepare.prepare(root/'input.tar.gz',out,'https://arxiv.org/abs/1512.03385v1')

if __name__ == '__main__':unittest.main()
