#!/usr/bin/env python3
"""Validate the LaTeX source package; check complete aligned bilingual Markdown readings."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from prepare_paper import SCHEMA, TEXT_SUFFIXES, arxiv_identity, digest, tree_digest

def package_path(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError('invalid package path')
    path = root / value
    if Path(value).is_absolute() or '..' in Path(value).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('package path escapes its root')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('source symlinks are forbidden')
        current = current.parent
    return path

def validate(manifest_path, markdown=None, source_only=False):
    root = manifest_path.parent
    m = json.loads(manifest_path.read_text(encoding='utf-8'))
    errors = []
    if m.get('schema') != SCHEMA:
        return ['expected ' + SCHEMA]
    try:
        ident, version = arxiv_identity(m['arxiv_url'] + m['version'])
        if ident != m['identifier'] or m['archive_url'] != 'https://arxiv.org/src/'+ident+version:
            errors.append('inconsistent arXiv identity')
        if (root/'link.txt').read_text(encoding='utf-8') != m['arxiv_url']+'\n':
            errors.append('link.txt must contain exactly the canonical arXiv abs URL and newline')
    except (KeyError, OSError, ValueError, TypeError) as error:
        errors.append(str(error))
    files = m.get('files', [])
    if not isinstance(files, list) or not files:
        return errors + ['source file inventory is empty or invalid']
    seen = set()
    for record in files:
        try:
            name = record['path']
            path = package_path(root, name)
            if not name.startswith('source/') or path.suffix.lower() not in TEXT_SUFFIXES:
                raise ValueError('inventory must contain only LaTeX/text source files')
            if name in seen:
                raise ValueError('duplicate source file')
            seen.add(name)
            payload = path.read_bytes()
            if b'\0' in payload or payload.startswith(b'%PDF-'):
                raise ValueError('binary source file')
            if len(payload) != record['bytes'] or digest(payload) != record['sha256']:
                raise ValueError('source file hash/size mismatch: '+name)
        except (KeyError, OSError, ValueError, TypeError) as error:
            errors.append(str(error))
    actual = {x.relative_to(root).as_posix() for x in (root/'source').rglob('*') if x.is_file() or x.is_symlink()}
    if actual != seen:
        errors.append('source inventory differs from on-disk files')
    if m.get('source_sha256') != tree_digest(files):
        errors.append('source tree digest mismatch')
    mains = m.get('entrypoints', [])
    if not isinstance(mains, list) or not mains:
        errors.append('LaTeX entrypoints are missing')
    else:
        for main in mains:
            try:
                if main not in seen or Path(main).suffix.lower() not in {'.tex', '.ltx'} or not re.search(rb'^\s*\\document(?:class|style)\b',package_path(root, main).read_bytes(), re.M):
                    errors.append('invalid LaTeX entrypoint: '+str(main))
            except (OSError, ValueError, TypeError) as error:
                errors.append(str(error))
    if not re.fullmatch('[0-9a-f]{64}', str(m.get('archive_sha256',''))):
        errors.append('missing archive digest')
    if (root/'evidence').exists():
        errors.append('evidence directory is not part of the lightweight package')
    if any(x.suffix.lower() == '.pdf' for x in root.rglob('*')):
        errors.append('PDF files are forbidden in the paper package')
    if markdown is not None:
        try:
            path = package_path(root, str(markdown.resolve().relative_to(root.resolve())))
            text = path.read_text(encoding='utf-8')
            if '\0' in text or re.search(r'QLPAPER_UNRESOLVED|\bTODO\b|\bTBD\b', text):
                errors.append('unresolved Markdown content')
            if re.search(r'!\[.*?\]\(|<(?:img|svg|iframe|object|embed)\b|data:image', text, re.I):
                errors.append('forbidden Markdown image embed or media')
            for name, start, end in re.findall(r'<!-- qlpaper-source: file=([^;]+); lines=(\d+)-(\d+) -->', text):
                if name not in seen:
                    errors.append('unknown source marker file: '+name)
                else:
                    count = len(package_path(root,name).read_bytes().splitlines())
                    if not 1 <= int(start) <= int(end) <= count:
                        errors.append('source marker line range out of bounds')
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(str(error))
    if not source_only:
        english = markdown if markdown is not None else root/'paper.md'
        chinese = root/'paper_ch.md'
        try:
            english = package_path(root, str(english.relative_to(root)))
            chinese = package_path(root, 'paper_ch.md')
            en = english.read_text(encoding='utf-8')
            ch = chinese.read_text(encoding='utf-8')
            pattern = r'<!-- qlpaper-block: ([A-Za-z0-9_-]+) -->'
            en_ids, ch_ids = re.findall(pattern, en), re.findall(pattern, ch)
            if not en_ids or len(en_ids) != len(set(en_ids)):
                errors.append('paper.md requires unique aligned block IDs')
            if en_ids != ch_ids:
                errors.append('paper_ch.md block order/coverage differs from paper.md')
            if not re.search(r'[\u4e00-\u9fff]', ch):
                errors.append('paper_ch.md contains no Chinese narrative')
            for text in [en, ch]:
                if re.search(r'QLPAPER_UNRESOLVED|\bTODO\b|\bTBD\b', text):
                    errors.append('unfinished bilingual reading')
                if re.search(r'!\[.*?\]\(|<(?:img|svg|iframe|object|embed)\b|data:image', text, re.I):
                    errors.append('forbidden media in bilingual reading')
                if re.search(r'<table\b|\\begin\{tabular\}|`\{=(?:html|latex)\}', text, re.I):
                    errors.append('raw table/layout residue in bilingual reading')
            # Translation preserves mathematical expressions literally.
            math = r'(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$'
            if re.findall(math, en) != re.findall(math, ch):
                errors.append('bilingual mathematical expressions differ')
        except (OSError, ValueError, UnicodeError) as error:
            errors.append('complete paper.md and paper_ch.md are required: '+str(error))
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--source-only', action='store_true', help='acquisition preflight only; not a completed reading')
    args = parser.parse_args()
    try:
        errors = validate(args.manifest,args.markdown,args.source_only)
        if errors:
            parser.exit(1, '\n'.join(errors)+'\n')
        m = json.loads(args.manifest.read_text())
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error)+'\n')
    print(json.dumps({'schema':'qlpaper-latex-validation-v1','status':'ok','scope':'source-only' if args.source_only else 'bilingual-reading','files':len(m['files']),'source_sha256':m['source_sha256']}))

if __name__ == '__main__':
    main()
