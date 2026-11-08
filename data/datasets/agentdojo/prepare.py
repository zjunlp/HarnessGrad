"""Prepare clean pinned sources/cases or inspect readiness without model calls."""
from __future__ import annotations

import json
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from harnessgrad.paths import dataset_root

DATA = dataset_root('agentdojo')
COMMIT = '089ed468cf3ed0322acc66b0211f26d9d90dbf60'


def prepare_cases():
    # Run with the trusted runtime, never platform Python's optional SDK dependencies.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bridge
    records = bridge.metadata()
    catalog = DATA / '_src/cases.json'
    if catalog.exists():
        if json.loads(catalog.read_text()) != records:
            raise ValueError('Existing case roster differs; audit before replacing it')
    else:
        catalog.write_text(json.dumps(records))
    # Never overwrite legacy snapshots or an incomplete preparation.
    prepared = DATA / f'prepared-{bridge.ATTACK_NAME}'
    prepared.mkdir()
    for i, case in enumerate(records):
        target = prepared / str(i)
        target.mkdir()
        (target / 'case.json').write_text(json.dumps(case))
        (target / 'initial.json').write_text(json.dumps(bridge.snapshot(case)))
        (target / 'oracle.json').write_text(json.dumps(bridge.oracle(case)))
    return len(records)


def prepare_source(archive, target):
    """Extract only pinned package files to a fresh external source root."""
    import hashlib
    import tarfile
    assets = Path(__file__).resolve().parent
    import importlib.util
    spec = importlib.util.spec_from_file_location('agentdojo_prepare_bootstrap', assets / 'bootstrap.py')
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    manifest = bootstrap.source_manifest()
    archive, target = Path(archive), Path(target)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('AgentDojo archive digest mismatch')
    if not target.is_absolute() or target.resolve() != target or target.exists():
        raise ValueError('AgentDojo source target must be a fresh absolute directory')
    if target.resolve().is_relative_to(assets.parents[2]):
        raise ValueError('AgentDojo source target must be outside the checkout')
    # Extract file bytes by exact expected name, never tar links or arbitrary paths.
    target.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        for relative, expected in manifest['files'].items():
            member = tar.getmember('agentdojo-' + COMMIT + '/src/' + relative)
            if not member.isfile():
                raise ValueError('AgentDojo archive member must be a regular file')
            raw = tar.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('AgentDojo archive source digest mismatch')
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    bootstrap.validate_source(target)
    return target


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--archive')
    parser.add_argument('--source-target')
    parser.add_argument('--prepare-cases', action='store_true')
    args = parser.parse_args()
    if args.check_only:
        from data.datasets.agentdojo import provision_status
        status = provision_status()
        print(json.dumps(status))
        return 0 if status['present'] else 2
    if args.archive and args.source_target:
        print(prepare_source(args.archive, args.source_target))
        return 0
    if args.prepare_cases:
        from data.datasets.agentdojo import _execution_runtime, _source_root
        runtime = _execution_runtime()
        if Path(sys.prefix).resolve() != Path(runtime['path']).resolve():
            raise ValueError('Run case preparation with the selected managed execution Python')
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from bootstrap import bootstrap
        bootstrap(_source_root())
        print('cases:', prepare_cases())
        from bootstrap import validate_prepared
        validate_prepared(DATA)
        return 0
    parser.error('choose --check-only, --archive with --source-target, or --prepare-cases')


if __name__ == '__main__':
    raise SystemExit(main())
