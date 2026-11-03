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
import socket
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, str(Path.cwd()))
from data.scaffolds.scorer_input import add_arguments, run_bound

HERE = Path(__file__).resolve().parent


def native_recalculate(files, scratch):
    source = (HERE / 'open_spreadsheet.py').read_text(encoding='utf-8')
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    # The upstream helper hard-codes one listener port. Change only its endpoint,
    # not formula evaluation or workbook operations; concurrent checks share no
    # listener or LibreOffice user installation.
    source = source.replace('port=2002', f'port={port}')
    module = types.ModuleType('spreadsheetbench2_native_recalculate')
    module.__file__ = str(HERE / 'open_spreadsheet.py')
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    native_popen = subprocess.Popen
    profile = (scratch / 'lo-profile').as_uri()
    def start(argv, **kwargs):
        if Path(argv[0]).name in ('soffice', 'libreoffice'):
            argv = [argv[0], f'-env:UserInstallation={profile}', *argv[1:]]
        return native_popen(argv, **kwargs)
    module.subprocess = types.SimpleNamespace(Popen=start, run=subprocess.run,
                                              PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
                                              TimeoutExpired=subprocess.TimeoutExpired)
    for file in files:
        os.utime(file, ns=(1_000_000_000, 1_000_000_000))
    if not module.start_libreoffice_service():
        raise RuntimeError('native LibreOffice service did not start')
    try:
        module.batch_open_files([str(file) for file in files])
        if any(file.stat().st_mtime_ns == 1_000_000_000 for file in files):
            raise RuntimeError('native LibreOffice helper did not save every workbook')
    finally:
        module.stop_libreoffice_service()


def evaluate(paths, receipt, args):
    evidence = Path(args.evidence_file)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    reward = Path(args.reward_file)
    reward.unlink(missing_ok=True)
    try:
        packages = {name: importlib.metadata.version(name) for name in ('openpyxl', 'tqdm')}
        if packages != {'openpyxl': '3.1.2', 'tqdm': '4.66.4'}:
            raise RuntimeError('SpreadsheetBench2 verifier requires its pinned requirements.txt runtime')
        record = json.loads(paths['record.json'].read_text(encoding='utf-8'))
        if args.task_id and record.get('task_id') != args.task_id:
            raise ValueError('selected task identity differs from scorer record')
        provenance = json.loads((HERE / 'PROVENANCE.json').read_text(encoding='utf-8'))
        for name, digest in provenance['files'].items():
            if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
                raise ValueError('official v2 verifier source digest mismatch')
        output = Path.cwd() / f'{record["row"]["id"]}_output.xlsx'
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise ValueError('output workbook is not an owned regular file')
        with tempfile.TemporaryDirectory(prefix='hg-spreadsheetbench2-check-') as tmp:
            scratch = Path(tmp)
            source_dir = scratch / 'data'
            output_dir = scratch / 'outputs'
            output_dir.mkdir()
            for name, file in paths.items():
                if name == 'record.json':
                    continue
                target = source_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
            originals = [source_dir / record['row'][key]
                         for key in ('spreadsheet_path', 'golden_response_path')]
            files = list(originals)
            if output.is_file():
                owned_output = output_dir / output.name
                shutil.copyfile(output, owned_output)
                files.append(owned_output)
            native_recalculate(files, scratch)
            spec = importlib.util.spec_from_file_location('spreadsheetbench2_official_evaluator', HERE / 'evaluation.py')
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
    record = json.loads(Path('record.json').read_text(encoding='utf-8'))
    names = ('record.json', record['row']['spreadsheet_path'], record['row']['golden_response_path'])
    return run_bound(args, {name: Path(name) for name in names},
                     lambda paths, receipt: evaluate(paths, receipt, args))


if __name__ == '__main__':
    raise SystemExit(main())
