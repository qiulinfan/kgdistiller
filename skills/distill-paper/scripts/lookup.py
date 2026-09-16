#!/usr/bin/env python3
"""Search candidate previews, or read agent-selected IDs, through the public CLI."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import time


def lookup(target, terms, run=None, *, read=False):
    deadline = time.monotonic() + 15
    prefix = ['kgdistiller', *target]

    def call(*args):
        if run is not None:
            value = run([*prefix, 'agent', *args])
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('lookup time budget exhausted; retry only unfinished queries')
            result = subprocess.run([*prefix, 'agent', *args], capture_output=True, text=True, timeout=min(4, remaining))
            if result.returncode:
                raise ValueError(result.stderr.strip() or result.stdout[:500])
            value = json.loads(result.stdout)
        if isinstance(value, dict) and ('error' in value or str(value.get('kind', '')).endswith('error')):
            raise ValueError(str(value)[:500])
        return value

    def resolve(values):
        rows = call('resolve', *values)
        if not isinstance(rows, list) or len(rows) != len(values):
            raise ValueError('invalid resolve response')
        for value, row in zip(values, rows):
            if row.get('query') != value or row.get('status') not in {'exact', 'alias', 'missing', 'ambiguous'}:
                raise ValueError('invalid resolve identity or response order')
        return rows

    errors = (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired)
    output = {'target': target, 'mode': 'read' if read else 'search',
              'results': [], 'candidates': [], 'entries': [], 'errors': []}
    try:
        before = call('status')
        output['snapshot_sha256'] = before['snapshot_sha256']
    except errors as error:
        output['errors'].append(str(error))
        return output

    if read:
        # Only IDs explicitly selected by the caller. No rank-based content cap.
        for node_id in dict.fromkeys(terms):
            try:
                node = call('get', node_id)['node']
                text = node.get('text') or node.get('entry', {}).get('summary', '')
                details = {k: v for k, v in node.get('entry', {}).items() if k != 'summary'}
                encoded = json.dumps(details, ensure_ascii=False)
                output['entries'].append({
                    'id': node_id, 'label': node['label'], 'text': text[:5000],
                    'text_truncated': len(text) > 5000, 'provenance': node.get('provenance', {}),
                    'details': details if len(encoded) <= 5000 else encoded[:5000],
                    'details_truncated': len(encoded) > 5000, 'content_available': bool(text.strip())})
            except errors as error:
                output['errors'].append(f'{node_id}: {error}')
    else:
        try:
            resolved = resolve(terms)
        except errors as error:
            output['errors'].append(str(error))
            resolved = []
        nodes, labels = {}, {}
        for term, match in zip(terms, resolved):
            row = {'term': term, 'identity_status': match['status'], 'candidate_ids': []}
            try:
                matches = [m for m in match['matches'] if m.get('type') == 'knowledge']
                nodes.update({m['id']: m for m in matches})
                ids = [m['id'] for m in matches]
                if match.get('overflow'):
                    row['identity_overflow'] = True
                if not ids:
                    hits = call('search', term, '--type', 'knowledge', '--limit', '5', '--depth', '0')['result']['results']
                    if re.fullmatch(r'[A-Za-z]{2,5}', term) and (term.isupper() or re.search(r'[a-z][A-Z]', term)):
                        pattern = re.compile(r'(?<![A-Za-z])' + re.escape(term) + r'(?![A-Za-z])', re.I)
                        hits = [h for h in hits if pattern.search(h.get('label', ''))]
                    ids = [h['node_id'] for h in hits]
                    labels.update({h['node_id']: h.get('label', '') for h in hits})
                    row['retrieval'] = 'lexical candidates only; applicability not established'
                row['candidate_ids'] = list(dict.fromkeys(ids))
            except errors as error:
                row['error'] = str(error)
            output['results'].append(row)
        ordered = list(dict.fromkeys(node_id for row in output['results'] for node_id in row['candidate_ids']))
        missing = [node_id for node_id in ordered if node_id not in nodes]
        if missing:
            try:
                # Public batch resolution supplies previews without per-candidate get
                # calls. Discard full node payloads before returning to the agent.
                for node_id, match in zip(missing, resolve(missing)):
                    for node in match['matches']:
                        if node['id'] == node_id:
                            nodes[node_id] = node
            except errors as error:
                output['errors'].append(f'candidate previews: {error}')
        for node_id in ordered:
            node = nodes.get(node_id, {})
            summary = node.get('entry', {}).get('summary') or node.get('text', '')
            output['candidates'].append({
                'id': node_id, 'label': node.get('label', labels.get(node_id, '')),
                'summary': summary[:600], 'summary_truncated': len(summary) > 600,
                'preview_available': bool(summary.strip())})
    try:
        output['generation_unchanged'] = call('status')['snapshot_sha256'] == before['snapshot_sha256']
        if not output['generation_unchanged']:
            output['errors'].append('knowledge generation changed during lookup; recheck affected candidates')
    except errors as error:
        output['errors'].append(str(error))
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vault', action='append', default=[])
    p.add_argument('--repo-root')
    p.add_argument('--read', action='store_true', help='read selected node IDs from one target, instead of searching terms')
    p.add_argument('--output', type=Path, help='save complete, pretty JSON in a fresh local working file')
    p.add_argument('terms', nargs='+')
    args = p.parse_args()
    if bool(args.vault) == bool(args.repo_root):
        p.error('provide --vault NAME (repeatable) or --repo-root PATH')
    terms = list(dict.fromkeys(args.terms))
    if not 1 <= len(terms) <= 30 or len(args.vault) > 4:
        p.error('use at most 30 terms or selected IDs per call and 4 established vaults')
    targets = [['--vault', v] for v in dict.fromkeys(args.vault)] if args.vault else [['--repo-root', args.repo_root]]
    if args.read and len(targets) != 1:
        p.error('--read requires one target; candidate IDs are vault-local')
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        result = list(pool.map(lambda target: lookup(target, terms, read=args.read), targets))
    report = {'lookups': result, 'note': 'Agent selects relevant previews, then reads selected IDs to verify conditions. Missing is not proof of unfamiliarity.'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({'lookup_file': str(args.output), 'candidates': sum(len(v['candidates']) for v in result),
                          'entries': sum(len(v['entries']) for v in result)}, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
