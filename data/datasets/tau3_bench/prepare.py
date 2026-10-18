#!/usr/bin/env python3
"""Prepare the pinned τ³ text base split; never execute a model request."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request

REVISION = 'fc0055dc4e0a316c3f83133267fbd6faaa770992'
ARCHIVE_SHA256 = '7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e'
DOMAINS = {'airline': 50, 'retail': 114, 'telecom': 114, 'banking_knowledge': 97}
ASSETS = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n')


def prepare(source, target):
    """source is extracted from the digest-checked official archive."""
    target.mkdir(parents=True, exist_ok=True)
    bundle = target / 'bundle'
    if bundle.exists():
        raise FileExistsError('use a fresh preparation root')
    shutil.copytree(source / 'src', bundle / 'src')
    shutil.copy2(source / 'LICENSE', bundle / 'LICENSE')
    # Runtime contains business databases/policy and user guidelines, never task
    # sets, reference trajectories, release results or other benchmark solutions.
    shutil.copytree(source / 'data/tau2/user_simulator', bundle / 'data/tau2/user_simulator')
    for domain in DOMAINS:
        src = source / 'data/tau2/domains' / domain
        dst = bundle / 'data/tau2/domains' / domain
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns('*tasks*', '*trajectory*', '*trajectories*', '__pycache__'))
    files = {str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
    write(bundle / 'manifest.json', {'revision': REVISION, 'files': files})
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TAU2_DATA_DIR'] = str(bundle / 'data')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    sys.path.insert(0, str(bundle / 'src'))
    sys.path.insert(0, str(ASSETS / 'lib'))
    from loguru import logger
    logger.remove()
    from tau2.data_model.tasks import Task
    from runtime import _get_tau2_domain_constructor
    rows = []
    for domain, count in DOMAINS.items():
        src = source / 'data/tau2/domains' / domain
        if domain == 'banking_knowledge':
            tasks = [json.loads(p.read_text()) for p in sorted((src / 'tasks').glob('task_*.json'))]
        else:
            all_tasks = json.loads((src / 'tasks.json').read_text())
            by_id = {str(t['id']): t for t in all_tasks}
            selected = json.loads((src / 'split_tasks.json').read_text())['base']
            if len(set(selected)) != len(selected):
                raise ValueError('duplicate official split task')
            tasks = [by_id[str(tid)] for tid in selected]  # missing IDs fail closed
        if len(tasks) != count:
            raise ValueError(f'{domain}: expected {count} official base tasks, got {len(tasks)}')
        for raw in tasks:
            task = Task.model_validate(raw)
            tid = f'tau3_bench/{domain}/{task.id}'
            config = {'revision': REVISION, 'task_id': tid, 'domain': domain,
                      'task': raw, 'retrieval_variant': 'bm25', 'max_steps': 200, 'max_errors': 10}
            constructor, kwargs = _get_tau2_domain_constructor(domain, config, task)
            env = constructor(solo_mode=False, **kwargs)
            public = {'task_id': tid, 'domain': domain, 'policy': env.get_policy(),
                      'tools': [t.openai_schema for t in env.get_tools()]}
            users = (env.get_user_tools(include=task.user_tools) or []) if env.user_tools is not None else []
            relative = f'cases/{domain}/{task.id}'
            write(target / relative / 'private.json', config)
            write(target / relative / 'public.json', public)
            rows.append({'task_id': tid, 'domain': domain, 'path': relative,
                         'private_sha256': digest(target / relative / 'private.json'),
                         'public_sha256': digest(target / relative / 'public.json'),
                         'user_tools': [t.openai_schema for t in users],
                         'needs_judge': 'NL_ASSERTION' in [str(x).upper() for x in raw['evaluation_criteria']['reward_basis']]})
    write(target / 'manifest.json', {'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256,
                                    'counts': DOMAINS, 'tasks': rows})
    print(json.dumps({'tasks': len(rows), 'manifest_sha256': digest(target / 'manifest.json')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    if args.archive is None and not args.fetch:
        parser.error('supply --archive or --fetch')
    with tempfile.TemporaryDirectory(prefix='tau3-prepare-') as tmp:
        archive = args.archive or Path(tmp) / 'source.tar.gz'
        if args.fetch:
            urllib.request.urlretrieve(f'https://codeload.github.com/sierra-research/tau2-bench/tar.gz/{REVISION}', archive)
        if digest(archive) != ARCHIVE_SHA256:
            raise ValueError('official source archive digest mismatch')
        with tarfile.open(archive) as tar:
            # Exclude historical model outputs and voice assets before extraction.
            members = [m for m in tar if '/data/tau2/results/' not in m.name and '/data/voice/' not in m.name]
            tar.extractall(tmp, members=members, filter='data')
        prepare(Path(tmp) / ('tau2-bench-' + REVISION), args.root)

if __name__ == '__main__':
    main()
