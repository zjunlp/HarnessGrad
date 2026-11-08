#!/usr/bin/env python3
"""Import, export or verify reviewed native scorer packages without running tasks.

The store follows HG_DATA_NATIVE_RUNTIMES, or HG_DATA_ROOT/native-runtimes.
Assembly can recover the exact pinned bytes from explicit legacy source ledgers
and archives. It never captures the current interpreter as a replacement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harnessgrad import native_runtime_store as store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('import', 'export', 'verify', 'assemble', 'inventory'))
    parser.add_argument('--dataset', required=True, choices=sorted(store.DATASETS))
    parser.add_argument('--root', type=Path, help='external package store override for this command')
    parser.add_argument('--manifest', type=Path, help='explicit reviewed manifest (requires --sha256)')
    parser.add_argument('--sha256', help='independently selected digest for --manifest')
    parser.add_argument('--archive', type=Path, action='append', default=[], help='input archive; repeat for assembly')
    parser.add_argument('--output', type=Path, help='new exported archive; existing files are refused')
    parser.add_argument('--source-manifest', type=Path, action='append', default=[], help='legacy schema 1 source ledger for assembly')
    parser.add_argument('--source-map', action='append', default=[], metavar='OLD=NEW', help='explicit legacy source prefix relocation')
    parser.add_argument('--source-root', type=Path, default=store.ROOT, help='base for relative paths in legacy source ledgers')
    parser.add_argument('--probe', action='store_true', help='also run the sealed host-capability probe after verify/import/assemble')
    args = parser.parse_args(argv)
    if bool(args.manifest) != bool(args.sha256):
        parser.error('--manifest and --sha256 must be supplied together')
    if args.action == 'import' and len(args.archive) != 1:
        parser.error('import requires exactly one --archive')
    if args.action == 'export' and not args.output:
        parser.error('export requires --output')
    if args.action == 'assemble' and not (args.archive or args.source_manifest):
        parser.error('assemble requires --archive or --source-manifest')
    if args.action != 'assemble' and (args.source_manifest or args.source_map):
        parser.error('--source-manifest and --source-map require assemble')
    if args.action not in ('import', 'assemble') and args.archive:
        parser.error('--archive requires import or assemble')
    if args.action != 'export' and args.output:
        parser.error('--output requires export')
    if args.action in ('export', 'inventory') and args.probe:
        parser.error('--probe requires verify, import or assemble')
    remaps = []
    for value in args.source_map:
        old, separator, new = value.partition('=')
        if not separator or not old or not new or not Path(old).is_absolute():
            parser.error('--source-map must be an absolute OLD prefix and a NEW directory')
        remaps.append((Path(old), Path(new).expanduser().resolve()))
    if args.manifest:
        binding = {'dataset': args.dataset, 'manifest': str(args.manifest), 'sha256': args.sha256}
    else:
        from data.runtimes import NATIVE_PINS
        binding = NATIVE_PINS[args.dataset].copy()
    try:
        if args.action == 'import':
            result = store.import_package(binding, args.archive[0], args.root)
        elif args.action == 'assemble':
            result = store.assemble_package(binding, source_manifests=args.source_manifest,
                archives=args.archive, remaps=remaps, source_root=args.source_root, root=args.root)
        elif args.action == 'export':
            output = store.export_package(binding, args.output, args.root)
            result = {'dataset': args.dataset, 'sha256': binding['sha256'], 'archive': str(output)}
        elif args.action == 'inventory':
            result = store.inventory_package(binding, args.root)
        else:
            result = store.verify_package(binding, args.root)
        if args.probe:
            from eval.verification.native_runtime import admit, NativeRuntimeUnavailable
            if args.root is not None:
                os.environ['HG_DATA_NATIVE_RUNTIMES'] = str(args.root.expanduser().resolve())
            try:
                with admit(binding) as runtime:
                    result['native_runtime'] = runtime.receipt
            except NativeRuntimeUnavailable as exc:
                raise store.StoreError(str(exc)) from exc
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        result = {'dataset': args.dataset, 'sha256': binding['sha256'], 'error': str(exc)}
        if isinstance(exc, store.MissingBlobs):
            result['missing'] = exc.missing
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
