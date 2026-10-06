#!/usr/bin/env python3
"""Run-local OMP bridge to compiled knowledge and immutable selection submission.

The generic bridge originates in qiulinfan/kgdistiller-experiment at commit
cf2960559f520cade5ce00cf1734705bf226dd01. Knowledge and questions are supplied by
the caller; no prompts, benchmark answers or model settings are shipped here.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any


CONFIG_KEYS = {'python_interpreter', 'library_path', 'byte_budget', 'reference_limit', 'search_limit', 'max_response_bytes'}


class BridgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def error_response(code: str, message: str) -> dict[str, Any]:
    return {'ok': False, 'error': {'code': code, 'message': message}}


def load_config(path: Path, output: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.name != 'compiled-tools-config.json' or path.is_symlink() or not path.is_file():
        raise BridgeError('invalid_config', 'A regular run-local compiled-tools config is required.')
    if not output.is_absolute() or output.is_symlink() or not output.is_dir() or path.parent.resolve() != output.resolve():
        raise BridgeError('invalid_output', 'Output must be the existing config directory.')
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise BridgeError('invalid_config', 'Config is not readable JSON.') from error
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise BridgeError('invalid_config', 'Config requires its fixed interpreter, library and four limits.')
    for key in ('python_interpreter', 'library_path'):
        value = config[key]
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise BridgeError('invalid_config', 'Configured interpreter and library must be absolute regular files.')
    if not os.access(config['python_interpreter'], os.X_OK):
        raise BridgeError('invalid_config', 'Configured Python interpreter is not executable.')
    for key in ('byte_budget', 'reference_limit', 'search_limit', 'max_response_bytes'):
        if not isinstance(config[key], int) or isinstance(config[key], bool) or config[key] < 1:
            raise BridgeError('invalid_config', 'Configured limits must be positive integers.')
    minimum = len(encoded(error_response('response_too_large', 'Whole response exceeds the configured limit.')))
    if config['max_response_bytes'] < minimum:
        raise BridgeError('invalid_config', 'Response limit cannot contain the explicit error envelope.')
    return config


def exact_keys(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - optional:
        raise BridgeError('invalid_request', 'Request fields do not match the declared operation.')


def references(values: Any, registry: set[str], limit: int) -> list[str]:
    if not isinstance(values, list) or len(values) > limit or any(not isinstance(value, str) for value in values):
        raise BridgeError('invalid_references', 'References must be a bounded list of exact addresses.')
    if len(values) != len(set(values)) or any(value not in registry for value in values):
        raise BridgeError('invalid_references', 'References are duplicated or absent from the exact node registry.')
    return values


def public_payload(value: Any, operation: str) -> tuple[Any, bool]:
    """Hide only paper-key addresses at known unavailable-source gap locations."""
    result, changed = copy.deepcopy(value), False
    scopes = result if operation == 'get' else []
    if operation == 'inventory':
        scopes = [result, *result['uses'], *(member for group in result['groups'] for member in group['senses'])]
    elif operation == 'pack':
        scopes = [result]
    for scope in scopes:
        for gap in scope.get('gaps', []):
            if gap.get('reason') == 'unresolved-source' and 'reference' in gap:
                del gap['reference']
                changed = True
    return result, changed


def bounded_result(result: Any, config: dict[str, Any], operation: str = '') -> dict[str, Any]:
    payload, changed = public_payload(result, operation)
    response = {'ok': True, 'result': payload}
    if changed:
        response['metadata_projection'] = 'Unresolved source addresses hidden; any core byte count describes the original preprojection core packet.'
    if len(encoded(response)) > config['max_response_bytes']:
        raise BridgeError('response_too_large', 'Whole response exceeds the configured limit.')
    return response


def current_questions(output: Path) -> list[str]:
    path = output / 'questions.json'
    if path.is_symlink() or not path.is_file():
        raise BridgeError('invalid_questions', 'Current run questions are missing or symlinked.')
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or set(row) != {'qid', 'question'} for row in rows):
        raise BridgeError('invalid_questions', 'Current questions require only qid and literal question.')
    qids = [row['qid'] for row in rows]
    if any(not isinstance(qid, str) or not qid for qid in qids) or len(qids) != len(set(qids)):
        raise BridgeError('invalid_questions', 'Current question addresses must be unique and nonempty.')
    if any(not isinstance(row['question'], str) or not row['question'].strip() for row in rows):
        raise BridgeError('invalid_questions', 'Current questions require literal nonempty text.')
    return qids


def execute(request: Any, config: dict[str, Any], output: Path) -> dict[str, Any]:
    if not isinstance(request, dict) or not isinstance(request.get('operation'), str):
        raise BridgeError('invalid_request', 'A declared operation is required.')
    try:
        from kgdistiller.compiled_retrieval import CompiledLibrary
        payload = json.loads(Path(config['library_path']).read_text(encoding='utf-8'))
        library = CompiledLibrary.from_payload(payload)
        registry = set(payload['nodes'])
    except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
        raise BridgeError('invalid_library', 'Configured compiled library could not be loaded.') from error
    operation = request['operation']
    if operation == 'search':
        exact_keys(request, {'operation', 'query'}, {'limit'})
        query, limit = request['query'], request.get('limit', config['search_limit'])
        if not isinstance(query, str) or not query.strip() or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= config['search_limit']:
            raise BridgeError('invalid_search', 'Search requires text and a limit within this run bound.')
        return bounded_result(library.search(query, limit=limit), config, operation)
    if operation in {'get', 'pack'}:
        exact_keys(request, {'operation', 'references'})
        selected = references(request['references'], registry, config['reference_limit'])
        result = [library.get(reference) for reference in selected] if operation == 'get' else library.pack(selected, config['byte_budget'])
        return bounded_result(result, config, operation)
    if operation == 'inventory':
        exact_keys(request, {'operation', 'term'})
        term = request['term']
        if not isinstance(term, str) or not term.strip():
            raise BridgeError('invalid_term', 'Inventory requires an authored declaration name.')
        return bounded_result(library.inventory(term), config, operation)
    if operation == 'submit_selection':
        exact_keys(request, {'operation', 'selections'})
        rows = request['selections']
        qids = current_questions(output)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or [row.get('qid') for row in rows] != qids:
            raise BridgeError('invalid_selection', 'Selection must cover current questions exactly in order.')
        for row in rows:
            exact_keys(row, {'qid', 'ranked', 'abstain'})
            references(row['ranked'], registry, config['reference_limit'])
            if not isinstance(row['abstain'], bool) or (row['abstain'] and row['ranked']):
                raise BridgeError('invalid_selection', 'Abstention must be Boolean and contain no selected addresses.')
        response = bounded_result({'submitted': True, 'questions': len(rows)}, config)
        selection_bytes = encoded(rows)
        if len(selection_bytes) + 1 > config['max_response_bytes']:
            raise BridgeError('response_too_large', 'Whole response exceeds the configured limit.')
        try:
            with (output / 'submitted-selection.json').open('xb') as stream:
                stream.write(selection_bytes + b'\n')
        except FileExistsError as error:
            raise BridgeError('already_submitted', 'The original selection submission is retained; no overwrite.') from error
        return response
    raise BridgeError('unknown_operation', 'Operation is not exposed by this run-local bridge.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = None
    try:
        config = load_config(args.config, args.output)
        raw = bytearray()
        while len(raw) <= config['max_response_bytes']:
            chunk = sys.stdin.buffer.read(min(65536, config['max_response_bytes'] + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > config['max_response_bytes']:
            raise BridgeError('request_too_large', 'Whole request exceeds the configured limit.')
        request = json.loads(raw.decode('utf-8'))
        response = execute(request, config, args.output.resolve())
    except BridgeError as error:
        response = error_response(error.code, str(error))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        response = error_response('bridge_error', 'The read-only operation or exact submission could not complete.')
    if config is not None and len(encoded(response)) > config['max_response_bytes']:
        response = error_response('response_too_large', 'Whole response exceeds the configured limit.')
    sys.stdout.buffer.write(encoded(response) + b'\n')


if __name__ == '__main__':
    main()
