#!/usr/bin/env python3
"""Fetch paper HTML and extract source text deterministically, without an LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
BLOCK = {'article', 'main', 'section', 'div', 'p', 'blockquote', 'figure', 'figcaption', 'table', 'tr', 'ul', 'ol', 'pre'}


class Element:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self):
        return ''.join(c if isinstance(c, str) else c.text() for c in self.children)


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def extract(html, source_url):
    tree = Tree()
    tree.feed(html)
    nodes = list(tree.root.walk())
    root = next((n for n in nodes if n.tag == 'article'), None)
    root = root or next((n for n in nodes if n.tag == 'main'), None)
    root = root or next((n for n in nodes if n.tag == 'body'), tree.root)
    metadata = {n.attrs.get('name'): n.attrs.get('content') for n in nodes if n.tag == 'meta' and n.attrs.get('name')}
    title = next((n.text().strip() for n in nodes if n.tag == 'title'), '')
    warnings, anchors, visuals = set(), [], []

    def render(n):
        if isinstance(n, str):
            return re.sub(r'\s+', ' ', n)
        a = n.attrs
        if n.tag == 'script' and a.get('type', '').startswith('math/tex'):
            return ' $' + n.text().strip() + '$ '
        if n.tag in {'nav', 'header', 'footer', 'script', 'style', 'noscript', 'annotation', 'annotation-xml'} or a.get('aria-hidden') == 'true':
            return ''
        if n.tag == 'math':
            tex = a.get('alttext') or next((c.text() for c in n.walk() if c.tag == 'annotation' and 'tex' in c.attrs.get('encoding', '').lower()), None)
            if tex:
                fence = '$$' if a.get('display') == 'block' else '$'
                return ' ' + fence + tex.strip() + fence + ' '
            warnings.add('Some MathML has no TeX alternative; inspect raw HTML for those formulas.')
            return ' [MathML: ' + n.text().strip() + '] '
        if n.tag in {'img', 'svg', 'object', 'canvas', 'iframe'}:
            warnings.add('Images are not visually inspected; consult original figures when needed.')
            location = a.get('src') or a.get('data')
            url = urljoin(source_url, location) if location else None
            if url and urlsplit(url).scheme in {'http', 'https'}:
                visuals.append({'id': a.get('id'), 'url': url, 'alt': a.get('alt', '')})
            return ' [image: ' + (a.get('alt') or 'see original') + '] '
        content = ''.join(render(c) for c in n.children)
        marker = ''
        if a.get('id') and (n.tag in {'section', 'figure', 'table', 'li'} or 'equation' in a.get('class', '')):
            anchors.append(a['id'])
            marker = '\n\n[' + a['id'] + ']\n'
        if n.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            return '\n\n' + '#' * int(n.tag[1]) + ' ' + content.strip() + '\n\n'
        if n.tag == 'a' and content.strip() and a.get('href'):
            href = a['href']
            if href.startswith('#') or urlsplit(href).scheme in {'http', 'https'}:
                return '[' + content.strip() + '](' + href + ')'
        if n.tag == 'li':
            return marker + '\n- ' + content.strip() + '\n'
        if n.tag in {'td', 'th'}:
            return content.strip() + ' | '
        if n.tag == 'br':
            return '\n'
        if n.tag in {'sub', 'sup'}:
            return ('_{' if n.tag == 'sub' else '^{') + content.strip() + '}'
        if n.tag in BLOCK:
            return marker + '\n' + content.strip() + '\n'
        return content

    body = render(root)
    body = re.sub(r'[ \t]+\n', '\n', body)
    body = re.sub(r'\n[ \t]+', '\n', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    if len(body) < 1000 or re.search(r'access denied|just a moment|verify you are human', title, re.I):
        raise ValueError('No readable full paper detected; use the next source format.')
    info = {'requested_url': source_url, 'title': title, 'metadata': metadata,
            'anchors': list(dict.fromkeys(anchors)), 'visual_sources': visuals,
            'warnings': sorted(warnings)}
    return '# Source: ' + source_url + '\n\n' + body + '\n', info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('url')
    p.add_argument('--output-dir', required=True, type=Path)
    p.add_argument('--html-file', type=Path, help='reuse an already downloaded HTML file')
    args = p.parse_args()
    if urlsplit(args.url).scheme not in {'http', 'https'}:
        p.error('an HTTP(S) source URL is required')
    targets = [args.output_dir / name for name in ('original.html', 'source.txt', 'html-source.json')]
    if any(x.exists() for x in targets):
        p.error('source files already exist; reuse them or choose a fresh directory')
    try:
        if args.html_file:
            raw, final_url, charset = args.html_file.read_bytes(), args.url, 'utf-8'
        else:
            request = urllib.request.Request(args.url, headers={'User-Agent': 'kgdistiller-source-reader/1.0', 'Accept': 'text/html'})
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.headers.get_content_type() not in {'text/html', 'application/xhtml+xml'}:
                    raise ValueError('response is not HTML')
                raw = response.read(20_000_001)
                final_url, charset = response.url, response.headers.get_content_charset() or 'utf-8'
        if len(raw) > 20_000_000:
            raise ValueError('HTML exceeds 20 MB')
        text, info = extract(raw.decode(charset), final_url)
        info.update(requested_url=args.url, resolved_url=final_url,
                    html_sha256=hashlib.sha256(raw).hexdigest(),
                    source_text_sha256=hashlib.sha256(text.encode()).hexdigest())
        args.output_dir.mkdir(parents=True, exist_ok=True)
        targets[0].write_bytes(raw)
        targets[1].write_text(text, encoding='utf-8')
        targets[2].write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'source_text': str(targets[1]), 'metadata': str(targets[2]),
                          'chars': len(text), 'warnings': info['warnings']}, ensure_ascii=False))
    except (OSError, ValueError, LookupError) as error:
        p.exit(1, f'HTML source unavailable: {error}\n')


if __name__ == '__main__':
    main()
