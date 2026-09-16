#!/usr/bin/env python3
"""Prepare a lightweight LaTeX-only package from a local arXiv source archive."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

SCHEMA = 'qlpaper-latex-source-v1'
TEXT_SUFFIXES = {'.tex', '.bib', '.bbl', '.sty', '.cls', '.bst', '.def', '.clo', '.cfg', '.txt', '.ltx'}
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 2048

class PrepareError(ValueError):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def tree_digest(files):
    return digest(json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())

def arxiv_identity(url):
    match = re.fullmatch(r'https://arxiv\.org/abs/((?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7}))(v[1-9]\d*)?', url.strip())
    if not match:
        raise PrepareError('expected an arXiv abs URL, optionally with an exact version')
    ident, version = match.groups()
    if not version:
        raise PrepareError('pin the archive version in --arxiv-url (for example .../1512.03385v1)')
    return ident, version

def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or not path.parts:
        raise PrepareError('unsafe archive path: ' + name)
    return path.as_posix()

def source_members(data):
    """Never execute TeX, extract links, or retain archive media/binaries."""
    if len(data) > MAX_BYTES:
        raise PrepareError('archive exceeds size limit')
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PrepareError('decompressed archive exceeds size limit')
    if data.startswith(b'%PDF-'):
        raise PrepareError('PDF is not a LaTeX source archive')
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode='r:')
    except tarfile.ReadError:
        if b'\\documentclass' not in data and b'\\documentstyle' not in data:
            raise PrepareError('input is neither a tar archive nor a standalone LaTeX document')
        return {'main.tex': data}, []
    kept, omitted, seen = {}, [], set()
    total = 0
    with archive:
        for number, member in enumerate(archive):
            if number >= MAX_FILES:
                raise PrepareError('archive has too many members')
            if member.isdir() and member.name in {".", "./"}:
                continue
            name = safe_name(member.name)
            if name in seen:
                raise PrepareError('duplicate archive path: ' + name)
            seen.add(name)
            if member.isdir():
                continue
            if not member.isfile():
                raise PrepareError('archive links and special files are forbidden: ' + name)
            total += member.size
            if total > MAX_BYTES:
                raise PrepareError('expanded archive exceeds size limit')
            if PurePosixPath(name).suffix.lower() not in TEXT_SUFFIXES:
                omitted.append(name)
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise PrepareError('unreadable archive member: ' + name)
            payload = stream.read(MAX_BYTES + 1)
            if b'\0' in payload or payload.startswith(b'%PDF-'):
                raise PrepareError('binary content disguised as source: ' + name)
            kept[name] = payload
    if not any(Path(n).suffix.lower() in {'.tex', '.ltx'} for n in kept):
        raise PrepareError('archive contains no LaTeX source')
    return kept, omitted

def prepare(source, output, url):
    ident, version = arxiv_identity(url)
    if output.exists() and any(output.iterdir()):
        raise PrepareError('output directory must be empty')
    data = source.read_bytes()
    kept, omitted = source_members(data)
    files = [{'path': 'source/' + name, 'sha256': digest(value), 'bytes': len(value)} for name, value in sorted(kept.items())]
    mains = ['source/' + n for n, v in sorted(kept.items()) if Path(n).suffix.lower() in {'.tex', '.ltx'} and re.search(rb'^\s*\\document(?:class|style)\b', v, re.M)]
    if not mains:
        raise PrepareError('no LaTeX document entrypoint found')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.paper-source-', dir=output.parent))
    try:
        for name, value in kept.items():
            dest = stage / 'source' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(value)
        manifest = dict(schema=SCHEMA, identifier=ident, version=version,
                        arxiv_url='https://arxiv.org/abs/' + ident,
                        archive_url='https://arxiv.org/src/' + ident + version,
                        archive_sha256=digest(data), source_sha256=tree_digest(files),
                        entrypoints=mains, files=files,
                        omitted_assets=omitted)
        (stage/'link.txt').write_text(manifest['arxiv_url']+'\n', encoding='utf-8')
        (stage/'source.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if output.exists():
            output.rmdir()
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--arxiv-url', required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.archive, args.output_dir, args.arxiv_url)
    except (OSError, ValueError, tarfile.TarError, EOFError) as error:
        parser.exit(1, f'prepare_paper: {error}\n')
    print(json.dumps({'schema':SCHEMA,'files':len(result['files']),'source_sha256':result['source_sha256'],'output':str(args.output_dir)}))

if __name__ == '__main__':
    main()
