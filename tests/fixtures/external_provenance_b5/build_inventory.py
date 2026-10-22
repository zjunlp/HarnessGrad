"""Rebuild this acquisition's integrity inventory, never an approval receipt."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def identity(path):
    body = path.read_bytes()
    return {'path': str(path.relative_to(ROOT)), 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest()}


def build():
    response_dir = HERE / 'responses'
    responses = []
    for path in sorted(response_dir.glob('*.receipt.json')):
        receipt = json.loads(path.read_bytes())
        responses.append({'label': path.name.removesuffix('.receipt.json'),
                          'receipt': identity(path), **receipt})
    tree = json.loads((response_dir / 'bfcl-tree.body').read_bytes())
    by_path = {e['path']: e for e in tree['tree']}
    original_files = {}
    for directory in (ROOT / 'tests/fixtures/bfcl_ast_source/candidate-codex195/files',
                      ROOT / 'tests/fixtures/bfcl_ast_source/retry-codex227/files',
                      HERE / 'bfcl/files'):
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                name = str(path.relative_to(directory))
                if name in original_files:
                    assert path.read_bytes() == (ROOT / original_files[name]['path']).read_bytes()
                    continue
                item = identity(path)
                blob = hashlib.sha1(b'blob ' + str(item['bytes']).encode() + b'\0'
                                    + path.read_bytes()).hexdigest()
                assert blob == by_path[name]['sha'] and item['bytes'] == by_path[name]['size']
                item.update(original_path=name, git_blob_sha1=blob,
                            expected_archive_member='gorilla-1.3/' + name,
                            archive_member_verified=False)
                if path.suffix == '.py':
                    item['imports'] = sorted({n.module for n in ast.walk(ast.parse(path.read_bytes()))
                                              if isinstance(n, ast.ImportFrom) and n.module})
                original_files[name] = item
    prefix = 'berkeley-function-call-leaderboard/'
    sources = {name: item for name, item in original_files.items() if name.endswith('.py')}
    modules = {name.removeprefix(prefix).removesuffix('.py').replace('/', '.'): name
               for name in sources}
    unresolved = set()
    third_party = set()
    for name, item in sources.items():
        parsed = ast.parse((ROOT / item['path']).read_bytes())
        imported = set(item['imports'])
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        local = {module for module in imported if module.startswith('bfcl_eval.')}
        item['requires'] = sorted(modules[module] for module in local if module in modules)
        item['missing_imports'] = sorted(module for module in local if module not in modules)
        unresolved.update(item['missing_imports'])
        third_party.update(module.split('.')[0] for module in imported if not module.startswith('bfcl_eval.'))
    loader = identity(HERE / 'triviaqa/trivia_qa.py')
    loader['git_blob_sha1'] = hashlib.sha1(b'blob ' + str(loader['bytes']).encode() + b'\0'
                                        + (ROOT / loader['path']).read_bytes()).hexdigest()
    manifest = {
        'schema': 'harnessgrad.external-provenance-acquisition-b5.v1',
        'scope': 'public code and metadata; no payloads, upstream execution, approval or model runs',
        'custodian_approved': False, 'independent_review': None,
        'production_authorized': False, 'measured': False,
        'responses': responses,
        'bfcl_v3': {
            'commit': 'ea13468e4423454d0c213704fb87cf7cb3990433',
            'root_tree': 'b34aa53a73491f42dc69b8b18c92448d7c311f50',
            'tag': 'v1.3',
            'archive': {'method': 'HEAD only', 'body_downloaded': False,
                        'declared_sha256': '57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe',
                        'digest_verified': False, 'member_binding_verified': False,
                        'etag_is_not_archive_sha256': True},
            'files': list(original_files.values()),
            'unacquired_imports': sorted(unresolved),
            'non_bfcl_import_roots_including_stdlib': sorted(third_party),
            'complete_import_closure': False,
            'package_initializers': 'tree identities only; original initializer responses not acquired',
            'dynamic_imports': 'multi_turn_utils.py loads func_source_code modules dynamically; not traversed',
            'runtime_and_native_dependencies_locked': False,
            'source_gate_approved_pin': None,
        },
        'triviaqa': {
            'hub_revision': '0f7faf33a3908546c6fd5b73a660e0f8ff173c2f',
            'transport': 'hf-mirror.com; direct huggingface.co unsuccessful',
            'predecessor_revision': 'd63a76f01c8226449f76b437dd512cf8b17adf5d',
            'loader': loader,
            'loader_license': 'Apache-2.0 header in unchanged predecessor loader; original license text already retained by task 225',
            'original_license_text': identity(ROOT / 'tests/fixtures/triviaqa_hub_codex225/historical_loader/LICENSE'),
            'conversion_execution_receipt_acquired': False,
            'actual_converter_execution_attested': False,
            'mirror_independently_authenticated': False,
            'scorer_equivalence_certified': False,
        },
    }
    (HERE / 'inventory.json').write_text(json.dumps(manifest, indent=2) + '\n')
    paths = [p for p in HERE.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'
             and '__pycache__' not in p.parts]
    (HERE / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(HERE)) + '\n'
        for p in sorted(paths)))


if __name__ == '__main__':
    build()
