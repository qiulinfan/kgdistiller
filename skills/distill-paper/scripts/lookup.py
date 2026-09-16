#!/usr/bin/env python3
"""Batch read-only identity/search/content lookup through the public CLI."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
from pathlib import Path
import subprocess
import time


def lookup(target, terms, run=None):
    deadline = time.monotonic() + 15
    prefix = ['kgdistiller', *target]

    def call(*args):
        if run is not None:
            value = run([*prefix, 'agent', *args])
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('lookup budget exhausted')
            result = subprocess.run([*prefix, 'agent', *args], capture_output=True, text=True, timeout=min(4, remaining))
            if result.returncode:
                raise ValueError(result.stderr.strip() or result.stdout[:500])
            value = json.loads(result.stdout)
        if isinstance(value, dict) and ('error' in value or str(value.get('kind', '')).endswith('error')):
            raise ValueError(str(value)[:500])
        return value

    output = {'target': target, 'results': [], 'entries': [], 'errors': []}
    try:
        before = call('status')
        output['snapshot_sha256'] = before['snapshot_sha256']
        resolved = call('resolve', *terms)
        if not isinstance(resolved, list) or len(resolved) != len(terms):
            raise ValueError('invalid resolve response')
    except (OSError, ValueError, KeyError, TimeoutError, subprocess.TimeoutExpired) as error:
        output['errors'].append(str(error))
        return output
    # Collect handles first so early noisy terms cannot consume the entire
    # content budget before later exact matches or mathematical prerequisites.
    exact_ids, candidate_rows = [], []
    for term, match in zip(terms, resolved):
        row = {'term': term, 'identity_status': 'unavailable', 'candidate_ids': []}
        try:
            if not isinstance(match, dict) or match.get('status') not in {'exact', 'alias', 'missing', 'ambiguous'}:
                raise ValueError('invalid identity status')
            if match.get('query') != term:
                raise ValueError('resolve response order differs from the request')
            row['identity_status'] = match['status']
            ids = [m['id'] for m in match['matches'] if m.get('type') == 'knowledge']
            if ids:
                exact_ids.extend(ids)
            else:
                search = call('search', term, '--type', 'knowledge', '--limit', '5', '--depth', '0')
                hits = search['result']['results']
                # The engine's lexical lane can match RoPE inside "properties".
                # An acronym is a retrieval hint, not evidence from a substring.
                if re.fullmatch(r'[A-Za-z]{2,5}', term):
                    pattern = re.compile(r'(?<![A-Za-z])' + re.escape(term) + r'(?![A-Za-z])', re.I)
                    hits = [h for h in hits if pattern.search(h.get('label', ''))]
                ids = [h['node_id'] for h in hits]
                row['retrieval'] = 'lexical candidates only; applicability not established'
            row['candidate_ids'] = list(dict.fromkeys(ids))
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired) as error:
            row['error'] = str(error)
        candidate_rows.append(row)
    ordered = list(dict.fromkeys(exact_ids))
    for rank in range(5):
        for row in candidate_rows:
            if rank < len(row['candidate_ids']) and row['candidate_ids'][rank] not in ordered:
                ordered.append(row['candidate_ids'][rank])
    entries = {}
    for node_id in ordered[:20]:
        try:
            node = call('get', node_id)['node']
            text = node.get('text') or node.get('entry', {}).get('summary', '')
            details = {k: v for k, v in node.get('entry', {}).items() if k != 'summary'}
            encoded = json.dumps(details, ensure_ascii=False)
            entries[node_id] = {'id': node_id, 'label': node['label'], 'text': text[:5000],
                                'text_truncated': len(text) > 5000, 'provenance': node.get('provenance', {}),
                                'details': details if len(encoded) <= 5000 else encoded[:5000],
                                'details_truncated': len(encoded) > 5000, 'content_available': bool(text.strip())}
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired) as error:
            output['errors'].append(f'{node_id}: {error}')
    for row in candidate_rows:
        omitted = [node_id for node_id in row['candidate_ids'] if node_id not in entries]
        if omitted:
            row['unread_candidate_ids'] = omitted
            row['error'] = 'candidate content unavailable or budget limited; not a negative lookup'
    output['results'] = candidate_rows
    output['entries'] = list(entries.values())
    try:
        output['generation_unchanged'] = call('status')['snapshot_sha256'] == before['snapshot_sha256']
        if not output['generation_unchanged']:
            output['errors'].append('knowledge generation changed during lookup; do not accept matches without rechecking')
    except (OSError, ValueError, KeyError, TimeoutError, subprocess.TimeoutExpired) as error:
        output['errors'].append(str(error))
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vault', action='append', default=[])
    p.add_argument('--repo-root')
    p.add_argument('--output', type=Path, help='save complete, pretty JSON in a fresh local working file')
    p.add_argument('terms', nargs='+')
    args = p.parse_args()
    if bool(args.vault) == bool(args.repo_root):
        p.error('provide --vault NAME (repeatable) or --repo-root PATH')
    terms = list(dict.fromkeys(args.terms))
    if not 1 <= len(terms) <= 12 or len(args.vault) > 4:
        p.error('use at most 12 concrete terms and 4 established vaults')
    targets = [['--vault', v] for v in dict.fromkeys(args.vault)] if args.vault else [['--repo-root', args.repo_root]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        result = list(pool.map(lambda target: lookup(target, terms), targets))
    report = {'lookups': result, 'note': 'Candidates require definition/condition review; missing is not proof of unfamiliarity.'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({'lookup_file': str(args.output), 'entries': sum(len(v['entries']) for v in result)}, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
