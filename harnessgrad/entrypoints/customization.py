"""Create an unevaluated customization proposal from explicit JSON input."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from harnessgrad.customization.inputs import collect_inputs, read_component_manifest
from harnessgrad.improvement.contracts import Feedback, reject_json_constant, unique_json_object
from harnessgrad.customization.review import input_digest, review_proposal

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROPOSAL_BYTES = 8 * 1024 * 1024


def _read_json(path: Path, *, limit: int = MAX_REQUEST_BYTES):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('JSON input exceeds the byte limit')
    try:
        value = json.loads(raw, object_pairs_hook=unique_json_object, parse_constant=reject_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('request must be a valid JSON object with unique keys') from None
    return value


def _feedback_rows(feedback):
    if not isinstance(feedback, list):
        raise ValueError('feedback must be a list')
    try:
        rows = [Feedback(**item) for item in feedback if isinstance(item, dict)]
    except TypeError:
        raise ValueError('each feedback item needs id, kind and text') from None
    if len(rows) != len(feedback):
        raise ValueError('each feedback item must be an object')
    return rows


def _read_request(path: Path) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict) or set(value) - {'feedback', 'skills', 'instruction'}:
        raise ValueError('request fields must be feedback, skills and/or instruction')
    rows = _feedback_rows(value.get('feedback'))
    instruction = value.get('instruction')
    if instruction is not None:
        if not isinstance(instruction, dict) or set(instruction) != {'path', 'text'}:
            raise ValueError('instruction must contain path and text')
        instruction = (instruction['path'], instruction['text'])
    skills = value.get('skills')
    if skills is not None and not isinstance(skills, dict):
        raise ValueError('skills must be an object mapping paths to text')
    return {'feedback': rows, 'skills': skills, 'instruction': instruction}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='harnessgrad customize', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect', help='show declared harness components without running it')
    inspect.add_argument('--root', type=Path, required=True, help='trusted harness root')
    collect = sub.add_parser('collect', help='read selected files into a request without a model call')
    collect.add_argument('--root', type=Path, required=True, help='trusted harness root')
    collect.add_argument('--skill', action='append', default=[], help='relative skill path; may be repeated')
    collect.add_argument('--instruction', help='relative instruction path')
    collect.add_argument('--feedback', type=Path, required=True, help='JSON array of selected feedback')
    review = sub.add_parser('review', help='check proposal consistency without a model call')
    review.add_argument('--request', type=Path, required=True, help='original selected input JSON')
    review.add_argument('--proposal', type=Path, required=True, help='saved proposal JSON')
    review.add_argument('--root', type=Path, help='also compare the selected current source files')
    review.add_argument('--components', action='store_true', help='attribute changed paths using the harness manifest')
    patch = sub.add_parser('patch', help='print a source-checked patch without applying it')
    patch.add_argument('--request', type=Path, required=True, help='original selected input JSON')
    patch.add_argument('--proposal', type=Path, required=True, help='saved proposal JSON')
    patch.add_argument('--root', type=Path, required=True, help='trusted harness root to check')
    propose = sub.add_parser('propose', help='generate an in-memory, unevaluated candidate')
    propose.add_argument('--request', type=Path, required=True, help='JSON input containing selected feedback and text')
    propose.add_argument('--with-memory', action='store_true', help='also propose memories and skills from failures')
    propose.add_argument('--refine-instruction', action='store_true', help='add a failure-focused instruction refinement pass')
    propose.add_argument('--max-calls', type=int, default=8, help='maximum logical model calls (HTTP retries are separate)')
    propose.add_argument('--timeout', type=float, default=180, help='per-request transport timeout in seconds')
    args = parser.parse_args(argv)
    model = None
    try:
        if args.command == 'inspect':
            from harnessgrad.core.harness import describe_harness_manifest
            description = describe_harness_manifest(args.root, read_component_manifest(args.root))
            output = asdict(description)
            output['root'] = str(description.root)
            output['components'] = {name: list(paths) for name, paths in description.components}
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'collect':
            feedback = _feedback_rows(_read_json(args.feedback))
            request = collect_inputs(args.root, args.skill, args.instruction, feedback)
            output = json.dumps(request, ensure_ascii=False, indent=2)
            if len(output.encode('utf-8')) + 1 > MAX_REQUEST_BYTES:
                raise ValueError('combined request exceeds 2 MiB')
            print(output)
            return 0
        request = _read_request(args.request)
        if args.command in ('review', 'patch'):
            document = _read_json(args.proposal, limit=MAX_PROPOSAL_BYTES)
            if args.command == 'review':
                print(json.dumps(review_proposal(request, document, root=args.root, include_components=args.components), ensure_ascii=False, indent=2))
            else:
                from harnessgrad.customization.patch import export_patch
                print(export_patch(request, document, root=args.root), end='')
            return 0
        digest = input_digest(request)
        if args.with_memory and request['skills'] is None:
            raise ValueError('memory proposals require selected skills')
        if args.with_memory and not any(item.kind == 'failure' for item in request['feedback']):
            raise ValueError('memory proposals require failure feedback')
        if args.refine_instruction and (request['instruction'] is None or not any(item.kind == 'failure' for item in request['feedback'])):
            raise ValueError('instruction refinement requires an instruction and failure feedback')
        from harnessgrad.customization.model import ProposalModel
        model = ProposalModel(base_url=os.environ.get('HG_METHOD_BASE_URL', ''),
                              api_key=os.environ.get('HG_METHOD_API_KEY', ''),
                              model=os.environ.get('HG_METHOD_MODEL', ''),
                              max_calls=args.max_calls, timeout=args.timeout)
        from harnessgrad.customization.proposals import propose_customization, propose_with_memory
        if args.with_memory:
            result, memories = propose_with_memory(**request, ask=model, refine_instruction=args.refine_instruction)
        else:
            result = propose_customization(**request, ask=model, refine_instruction=args.refine_instruction)
            memories = None
        document = {'status': 'unevaluated', 'input_sha256': digest, 'proposal': asdict(result),
                    'model_usage': model.usage()}
        if memories is not None:
            document['memories'] = memories
        output = json.dumps(document, ensure_ascii=False, indent=2)
        # The emitted document must fit the same limit used by review and patch.
        # Check before writing to avoid returning an unusable partial candidate.
        if len(output.encode('utf-8')) + 1 > MAX_PROPOSAL_BYTES:
            raise ValueError('proposal exceeds 8 MiB')
        print(output)
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        # EndpointError avoids response bodies and credentials. Invalid input errors
        # contain only our fixed messages, not user text or model output.
        print('customization failed: ' + str(exc), file=sys.stderr)
        if model is not None:
            print(json.dumps({'model_usage': model.usage()}, ensure_ascii=False), file=sys.stderr)
        return 1
