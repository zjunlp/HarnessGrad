"""Offline consistency checks for customization proposals.

Hashes bind a proposal to supplied input bytes; they do not authenticate either
file, establish full harness identity, validate behavior or approve deployment.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any

from .inputs import collect_inputs, read_component_manifest
from harnessgrad.improvement.contracts import text_diff, index_feedback, is_relative_path, selected_surfaces


def _original(request: dict[str, Any]) -> dict[str, str]:
    feedback = request.get('feedback')
    if not isinstance(feedback, list):
        raise ValueError('feedback must be a list')
    index_feedback(feedback)
    return selected_surfaces(request.get('skills'), request.get('instruction'))


def input_digest(request: dict[str, Any]) -> str:
    """Hash normalized explicit input, preserving feedback order and exact text."""
    _original(request)
    value = {
        'feedback': [asdict(item) for item in request['feedback']],
        'skills': request.get('skills'),
        'instruction': list(request['instruction']) if request.get('instruction') is not None else None,
    }
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def review_proposal(request: dict[str, Any], document: Any, *, root=None, include_components=False) -> dict:
    """Check request binding, permitted surfaces and exact diff; never execute code."""
    if include_components and root is None:
        raise ValueError('component attribution requires a harness root')
    original = _original(request)
    digest = input_digest(request)
    if not isinstance(document, dict) or document.get('status') != 'unevaluated':
        raise ValueError('proposal must have unevaluated status')
    if not isinstance(document.get('input_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', document['input_sha256']):
        raise ValueError('proposal input digest is missing or invalid')
    if document['input_sha256'] != digest:
        raise ValueError('proposal input does not match the request')
    proposal = document.get('proposal')
    if not isinstance(proposal, dict) or set(proposal) != {'files', 'diff', 'feedback_ids', 'stages'}:
        raise ValueError('proposal structure is invalid')
    files = proposal['files']
    if not isinstance(files, dict) or not all(is_relative_path(k) and isinstance(v, str) for k, v in files.items()):
        raise ValueError('proposal files are invalid')
    if not set(original).issubset(files):
        raise ValueError('proposal omits selected files')
    extra = set(files) - set(original)
    if extra and (request.get('skills') is None or not all(p.startswith('skills/') and p.endswith('.md') for p in extra)):
        raise ValueError('proposal contains paths outside the selected surfaces')
    expected_stages = (['skillopt-fast'] if request.get('skills') is not None else []) + (
        ['ace-reflect-curate'] if request.get('instruction') is not None else [])
    proposed_stages = proposal['stages']
    if isinstance(proposed_stages, list) and 'gepa-reflect-propose' in proposed_stages:
        if request.get('instruction') is None or not any(item.kind == 'failure' for item in request['feedback']):
            raise ValueError('instruction refinement requires an instruction and failure feedback')
        expected_stages.append('gepa-reflect-propose')
    if 'memories' in document:
        memories = document['memories']
        failure_ids = {item.id for item in request['feedback'] if item.kind == 'failure'}
        if request.get('skills') is None or not failure_ids or not isinstance(memories, list):
            raise ValueError('memory proposal requires selected skills and failure feedback')
        seen = set()
        for memory in memories:
            if (not isinstance(memory, dict) or not isinstance(memory.get('task_id'), str)
                    or memory['task_id'] not in failure_ids):
                raise ValueError('memory references unknown failure feedback')
            if memory['task_id'] in seen or any(not isinstance(memory.get(key), str) or not memory[key].strip()
                                                for key in ('lesson', 'trigger', 'evidence', 'scope_hint')):
                raise ValueError('memory records are invalid')
            seen.add(memory['task_id'])
        expected_stages.append('evo-harness-skill-curation')
    if proposal['stages'] != expected_stages:
        raise ValueError('proposal stages do not match selected surfaces')
    if proposal['feedback_ids'] != [item.id for item in request['feedback']]:
        raise ValueError('proposal feedback does not match the request')
    if not isinstance(proposal['diff'], str) or proposal['diff'] != text_diff(original, files):
        raise ValueError('proposal diff does not match candidate files')
    source_check = 'not_checked'
    if root is not None:
        instruction = request.get('instruction')
        selected = collect_inputs(root, list(request.get('skills') or {}),
                                  instruction[0] if instruction else None, request['feedback'])
        current = dict(selected.get('skills') or {})
        if selected.get('instruction'):
            current[selected['instruction']['path']] = selected['instruction']['text']
        if current != original:
            raise ValueError('selected source files differ from the request')
        source_check = 'selected_files_match'
    changed = sorted(path for path in files if original.get(path) != files[path])
    result = {'status': 'unevaluated', 'input_sha256': digest, 'source_check': source_check,
              'changed_paths': changed, 'feedback_ids': proposal['feedback_ids'], 'stages': expected_stages}
    if include_components:
        from harnessgrad.core.harness import describe_harness_manifest
        description = describe_harness_manifest(root, read_component_manifest(root))
        result['component_attribution'] = {
            **description.attribute_changes(changed),
            'by_path': {path: description.attribute_changes([path]) for path in changed},
        }
    return result
