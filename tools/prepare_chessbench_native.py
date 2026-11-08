"""Prepare/check the independently pinned Chess scorer, never the chess provider."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from data.runtimes import NATIVE_PINS
from harnessgrad import native_runtime_store as store

BINDING = NATIVE_PINS['chessbench_puzzles']


def mappings(values):
    result = []
    for value in values:
        target, separator, source = value.partition('=')
        if (not separator or not Path(target).is_absolute() or not Path(source).is_absolute()
                or '..' in Path(target).parts or '..' in Path(source).parts):
            raise ValueError('source mappings require absolute SANDBOX=RETAINED paths without traversal')
        if any(old == Path(target) for old, _ in result):
            raise ValueError('duplicate sandbox source mapping')
        result.append((Path(target), Path(source)))
    return sorted(result, key=lambda row: len(row[0].parts), reverse=True)


def discover(source_maps=(), files=(), launcher=None):
    """Read only manifest-named candidates, not a directory/venv traversal."""
    _, manifest = store.load_manifest(BINDING)
    wanted = store.entries(manifest)
    matched, rejected = {}, []
    explicit = dict(files)
    rows = list(manifest['files'].items())
    if launcher is not None:
        rows.append(('/__launcher__', manifest['launcher']))
        explicit[Path('/__launcher__')] = Path(launcher)
    for target, entry in rows:
        target = Path(target)
        source = explicit.get(target)
        if source is None:
            for old, new in source_maps:
                if target == old or target.is_relative_to(old):
                    source = new / target.relative_to(old)
                    break
        if source is None or not source.is_file():
            continue
        if source.stat().st_size != entry['size']:
            rejected.append({'target': str(target), 'reason': 'size mismatch'})
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            rejected.append({'target': str(target), 'reason': 'digest mismatch'})
            continue
        matched[entry['source']] = str(source.resolve(strict=True))
    missing = store.missing_inventory(manifest, wanted.keys() - matched.keys())
    report = {'dataset': 'chessbench_puzzles', 'manifest_sha256': BINDING['sha256'],
              'required_blobs': len(wanted), 'matched_blobs': len(matched), 'missing': missing,
              'rejected_candidates': rejected, 'candidate_complete': not missing,
              'verified': False, 'scope': 'exact pinned byte recovery; no provider or task execution'}
    ledger = {'schema': 1, 'files': {name: {**wanted[name], 'source': path}
                                   for name, path in matched.items()},
              'launcher': {**manifest['launcher'], 'source': matched.get(manifest['launcher']['source'], '')}}
    return report, ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inventory', 'prepare', 'check'))
    parser.add_argument('--root', type=Path, required=True, help='external native package store, not provider store')
    parser.add_argument('--map', action='append', default=[], metavar='SANDBOX=RETAINED')
    parser.add_argument('--file', action='append', default=[], metavar='SANDBOX=RETAINED')
    parser.add_argument('--launcher', type=Path, help='explicit retained bubblewrap candidate')
    parser.add_argument('--package-archive', type=Path, help='complete independently pinned native package')
    parser.add_argument('--probe', action='store_true', help='sealed host-capability probe; no chess task')
    args = parser.parse_args(argv)
    if args.action == 'inventory' and args.probe:
        parser.error('--probe requires prepare/check')
    if args.action != 'prepare' and args.package_archive:
        parser.error('--package-archive requires prepare')
    try:
        if args.action == 'check':
            result = store.verify_package(BINDING, args.root)
        elif args.package_archive:
            result = store.import_package(BINDING, args.package_archive, args.root)
        else:
            result, ledger = discover(mappings(args.map), mappings(args.file), args.launcher)
            if args.action == 'inventory':
                result['installed'] = store.inventory_package(BINDING, args.root)
            else:
                if result['missing']:
                    print(json.dumps(result, indent=2))
                    return 2
                # Temporary ledger is a locator only. Existing assembly checks every
                # byte again and publishes with no replacement; no partial package.
                with tempfile.TemporaryDirectory(prefix='hg-chess-native-ledger-') as temporary:
                    declaration = Path(temporary) / 'sources.json'
                    declaration.write_text(json.dumps(ledger))
                    result = store.assemble_package(BINDING, source_manifests=[declaration], root=args.root)
        if args.probe:
            from eval.verification.native_runtime import admit
            os.environ['HG_DATA_NATIVE_RUNTIMES'] = str(args.root.resolve())
            with admit(BINDING) as runtime:
                result['native_runtime'] = runtime.receipt
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({'dataset': 'chessbench_puzzles', 'manifest_sha256': BINDING['sha256'],
                          'verified': False, 'error_type': type(exc).__name__, 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
