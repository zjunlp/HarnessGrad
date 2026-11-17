"""Public source declarations and implementation roles for spreadsheet adapters.

These are declarations and source fingerprints, not proof of workbook origin or
of the native LibreOffice/UNO runtime used for a measurement.
"""
import ast
import hashlib
import json
import re

from harnessgrad.provenance import measurement_components as identities


def _assignments(content, wanted):
    values = {}
    for node in ast.parse(content).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted):
            key = node.targets[0].id
            if key in values:
                raise ValueError('duplicate spreadsheet declaration')
            if isinstance(node.value, ast.JoinedStr):
                parts = []
                for part in node.value.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        parts.append(part.value)
                    elif (isinstance(part, ast.FormattedValue) and part.conversion == -1
                          and part.format_spec is None and isinstance(part.value, ast.Name)):
                        parts.append(values[part.value.id])
                    else:
                        raise ValueError('unsupported spreadsheet URL declaration')
                values[key] = ''.join(parts)
            else:
                values[key] = ast.literal_eval(node.value)
    if set(values) != wanted:
        raise ValueError('missing spreadsheet declaration')
    return values


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate spreadsheet provenance key')
        result[key] = value
    return result


def profile(root, name):
    if name not in ('spreadsheetbench', 'spreadsheetbench2'):
        raise ValueError('invalid spreadsheet dataset')
    adapter = f'data/datasets/{name}.py'
    adapter_bytes = (root / adapter).read_bytes()
    loader_files = {'adapter': adapter}
    scorer_files = {'check': f'data/datasets/{name}/verify/check.py',
                    'input_binding': 'data/scaffolds/scorer_input.py',
                    'native_runtime': 'data/scaffolds/spreadsheet_runtime/native.py'}
    try:
        if name == 'spreadsheetbench':
            values = _assignments(adapter_bytes, {'REVISION', 'TARBALL', 'TARBALL_URL', 'TARBALL_SHA256'})
            revision, archive, digest = values['REVISION'], values['TARBALL'], values['TARBALL_SHA256']
            origin = values['TARBALL_URL']
            if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)
                    or archive != 'spreadsheetbench_verified_400.tar.gz'
                    or origin != f'https://hf-mirror.com/datasets/KAKA22/SpreadsheetBench/resolve/{revision}/{archive}'
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('invalid spreadsheet v1 declaration')
            metric = 'spreadsheetbench.verified_400.named_cells.v1'
            declaration = None
        else:
            values = _assignments(adapter_bytes, {'REVISION', 'EVALUATOR_REVISION', 'METRIC', 'ARCHIVE'})
            revision, archive = values['REVISION'], values['ARCHIVE']
            manifest = f'data/datasets/{name}/PROVENANCE.json'
            prepare = f'data/datasets/{name}/prepare.py'
            manifest_bytes, prepare_bytes = (root / manifest).read_bytes(), (root / prepare).read_bytes()
            upstream = json.loads(manifest_bytes, object_pairs_hook=_unique)
            prep = _assignments(prepare_bytes, {'REPOSITORY', 'REVISION', 'FILENAME'})
            if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)
                    or not isinstance(values['EVALUATOR_REVISION'], str)
                    or not re.fullmatch(r'[0-9a-f]{40}', values['EVALUATOR_REVISION'])
                    or prep != {'REPOSITORY': 'KAKA22/SpreadsheetBench-v2', 'REVISION': revision, 'FILENAME': archive}
                    or upstream['source'] != prep['REPOSITORY'] or upstream['source_revision'] != revision
                    or upstream['archive'] != archive or archive != 'spreadsheetbench-v2-09-10.zip'
                    or upstream['repository'] != 'https://github.com/RUCKBReasoning/SpreadsheetBench-2'
                    or upstream['revision'] != values['EVALUATOR_REVISION']
                    or values['METRIC'] != 'spreadsheetbench2.official.accuracy'):
                raise ValueError('inconsistent spreadsheet v2 declaration')
            files = upstream['files']
            if (not isinstance(files, dict) or set(files) != {'evaluation.py', 'open_spreadsheet.py'}
                    or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                           for value in files.values())):
                raise ValueError('invalid upstream evaluator declaration')
            declared = {'repository': upstream['repository'], 'revision': upstream['revision'], 'files': files}
            declaration = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            origin = f'https://huggingface.co/datasets/{prep["REPOSITORY"]}/resolve/{revision}/{archive}'
            digest, metric = None, values['METRIC']
            loader_files.update({'prepare': prepare, 'safe_file': 'data/scaffolds/native_harbor.py'})
            scorer_files.update({'evaluation': f'data/datasets/{name}/verify/evaluation.py',
                                 'open_spreadsheet': f'data/datasets/{name}/verify/open_spreadsheet.py'})
    except (SyntaxError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'invalid {name} provenance declaration') from exc
    loader = identities.capture_implementation('loader', root, loader_files)
    if loader['details']['components']['adapter'] != hashlib.sha256(adapter_bytes).hexdigest():
        raise ValueError('spreadsheet declaration changed during capture')
    if name == 'spreadsheetbench2':
        stamp = identities.capture_implementation('loader', root, {'manifest': manifest})
        if (loader['details']['components']['prepare'] != hashlib.sha256(prepare_bytes).hexdigest()
                or stamp['details']['components']['manifest'] != hashlib.sha256(manifest_bytes).hexdigest()):
            raise ValueError('spreadsheet declaration changed during capture')
    scorer = identities.capture_implementation('scorer', root, scorer_files, protocol=metric)
    if declaration is not None:
        scorer = identities.implementation('scorer', {**scorer['details']['components'],
                                                       'declared_upstream': declaration}, protocol=metric)
    return {'data_source': identities.data_source(name, origin=origin, revision=revision,
                                                  declared_artifact_sha256=digest),
            'loader': loader, 'scorer': scorer}
