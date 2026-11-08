#!/usr/bin/env python3
"""Recalculate private copies, then call the pinned native v2 evaluator.

The actor output is read-only to this verifier. LibreOffice operates on private
copies with an ephemeral UNO listener and profile. Its iterative calculation
settings and the evaluator's comparisons/rounding remain upstream's own code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

# The executor stages this source and its helpers after actor exit. Never import
# a package or bytecode cache from the actor cwd, even for direct offline checks.
import types
ENTRY = Path(__file__).resolve()
HERE = ENTRY.parent
if tuple(ENTRY.parts[-5:]) == ('data', 'datasets', 'spreadsheetbench2', 'verify', 'check.py'):
    _helper = ENTRY.parents[3] / 'scaffolds/scorer_input.py'
    _native_source = ENTRY.parents[3] / 'scaffolds/spreadsheet_runtime/native.py'
else:
    _helper = HERE / 'data/scaffolds/scorer_input.py'
    _native_source = HERE / 'native.py'


def source_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


_inputs = source_module('spreadsheetbench2_scorer_input', _helper)
_native = source_module('spreadsheetbench2_native', _native_source)
add_arguments, run_bound = _inputs.add_arguments, _inputs.run_bound


def trusted_context(paths, args):
    context = json.loads(paths['runtime-context.json'].read_text())
    if (context.get('schema') != 'spreadsheetbench2.trusted-context.v1'
            or context.get('task_id') != args.task_id
            or context.get('metric') != 'spreadsheetbench2.official.accuracy'):
        raise ValueError('trusted scorer context identity mismatch')
    runtime = context['python_runtime']
    if (runtime.get('dataset') != 'spreadsheetbench2' or runtime.get('role') != 'verifier'
            or os.path.abspath(sys.executable) != runtime.get('python')):
        raise ValueError('trusted scorer interpreter identity mismatch')
    for name, expected in context['sources'].items():
        if hashlib.sha256(paths[name].read_bytes()).hexdigest() != expected:
            raise ValueError('trusted scorer source identity mismatch')
    # Authenticate the code already executing, as well as the consumed private copies.
    for path, name in ((ENTRY, 'check.py'), (_helper, 'data/scaffolds/scorer_input.py'),
                       (_native_source, 'native.py')):
        if hashlib.sha256(path.read_bytes()).hexdigest() != context['sources'][name]:
            raise ValueError('trusted executing source identity mismatch')
    if hashlib.sha256(paths['native-runtime.json'].read_bytes()).hexdigest() != context['native_manifest_sha256']:
        raise ValueError('trusted native deployment identity mismatch')
    native = _native.check(paths['native-runtime.json'])
    identity = context['native_identity']
    if (identity['python'] != native['python']
            or identity['python_sha256'] != native['files'][native['python']]
            or identity['soffice_binary'] != native['soffice_binary']
            or identity['soffice_sha256'] != native['files'][native['soffice_binary']]
            or not identity['uno_bindings']
            or any(native['files'].get(name) != sha for name, sha in identity['uno_bindings'].items())):
        raise ValueError('trusted native interpreter/binding identity mismatch')
    return context, native


def evaluate(paths, receipt, args):
    evidence = Path(args.evidence_file)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    reward = Path(args.reward_file)
    reward.unlink(missing_ok=True)
    try:
        context, runtime = trusted_context(paths, args)
        packages = {name: importlib.metadata.version(name) for name in ('openpyxl', 'tqdm')}
        if packages != {'openpyxl': '3.1.2', 'tqdm': '4.66.4'}:
            raise RuntimeError('SpreadsheetBench2 verifier requires its pinned requirements.txt runtime')
        record = json.loads(paths['record.json'].read_text(encoding='utf-8'))
        if args.task_id and record.get('task_id') != args.task_id:
            raise ValueError('selected task identity differs from scorer record')
        provenance = json.loads(paths['PROVENANCE.json'].read_text(encoding='utf-8'))
        for name, digest in provenance['files'].items():
            if hashlib.sha256(paths[name].read_bytes()).hexdigest() != digest:
                raise ValueError('official v2 verifier source digest mismatch')
        output = Path.cwd() / f'{record["row"]["id"]}_output.xlsx'
        if output.is_symlink() or (output.exists() and (not output.is_file() or output.stat().st_nlink != 1)):
            raise ValueError('output workbook is not an owned regular file')
        with tempfile.TemporaryDirectory(prefix='hg-spreadsheetbench2-check-') as tmp:
            scratch = Path(tmp)
            source_dir = scratch / 'data'
            output_dir = scratch / 'outputs'
            output_dir.mkdir()
            for name, file in paths.items():
                if name not in (record['row']['spreadsheet_path'], record['row']['golden_response_path']):
                    continue
                target = source_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
            originals = [source_dir / record['row'][key]
                         for key in ('spreadsheet_path', 'golden_response_path')]
            files = list(originals)
            if output.is_file():
                owned_output = output_dir / output.name
                descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, 'rb') as source, owned_output.open('wb') as target:
                    identity = os.fstat(source.fileno())
                    if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
                        raise ValueError('output workbook is not an owned regular file')
                    shutil.copyfileobj(source, target)
                files.append(owned_output)
            _native.isolated_uno_recalculate(runtime, paths['open_spreadsheet.py'], files)
            spec = importlib.util.spec_from_file_location('spreadsheetbench2_official_evaluator', paths['evaluation.py'])
            native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(native)
            result, _missing = native.process_single_item(record['row'], str(source_dir),
                                                          str(output_dir), {}, record['category'])
            score = result['accuracy']
            if type(score) not in (int, float) or score not in (0, 1):
                raise ValueError('native scorer returned an invalid accuracy')
            reward.parent.mkdir(parents=True, exist_ok=True)
            reward.write_text(str(float(score)), encoding='utf-8')
            # Upstream errors contain expected cell values. Keep those private;
            # methods receive counts/metrics, never the golden workbook contents.
            evidence.write_text(json.dumps({'measured': True, 'score': score,
                'metric': 'spreadsheetbench2.official.accuracy',
                'regression_accuracy': result['regression_accuracy'],
                'modification_accuracy': result['modification_accuracy'],
                'missing_output': not output.is_file(), 'scorer_input': receipt,
                'runtime_packages': packages,
                'runtime_context': {'native_manifest_sha256': context['native_manifest_sha256'],
                    'native_version': runtime['version'], 'native_deployment': runtime['deployment'],
                    'python_runtime_id': context['python_runtime']['runtime_id'],
                    'source_sha256': context['sources']},
                'evaluator_revision': provenance['revision']}) + '\n', encoding='utf-8')
            return 0
    except Exception as exc:
        evidence.write_text(json.dumps({'measured': False, 'error': type(exc).__name__,
                                        'scorer_input': receipt}) + '\n', encoding='utf-8')
        print(f'SpreadsheetBench2 native check unavailable ({type(exc).__name__})', file=sys.stderr)
        return 3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reward-file', default='verifier/reward.txt')
    parser.add_argument('--evidence-file', default='verifier/evidence.json')
    add_arguments(parser)
    args = parser.parse_args()
    # Trusted argv declares the exact inventory; do not parse actor-visible record
    # paths before run_bound has authenticated private copies.
    expected = json.loads(args.scorer_input_files or 'null')
    if not args.task_id or not isinstance(expected, dict):
        raise ValueError('SpreadsheetBench2 requires a task-bound trusted scorer context')
    return run_bound(args, {name: Path(name) for name in expected},
                     lambda paths, receipt: evaluate(paths, receipt, args))



if __name__ == '__main__':
    raise SystemExit(main())
