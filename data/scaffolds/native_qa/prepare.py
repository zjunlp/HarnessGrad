#!/usr/bin/env python3
"""Fetch pinned native source rows and write a digest-bound external data root.

Only this explicit preparation command may download data. `--local split=path`
imports native JSON/JSONL/CSV/Parquet bytes and records that custody as local,
without asserting authenticated Hub acquisition. Full native split membership is
required. No data or model is fetched by adapter import or load().
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import os
import subprocess
import zipfile

from . import CATALOG, Dataset, bbeh_selection, parse_row


def _read_native(path):
    path=Path(path)
    if path.suffix=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as handle: yield from csv.DictReader(handle)
    elif path.suffix=='.parquet':
        import pyarrow.parquet as pq
        for batch in pq.ParquetFile(path).iter_batches(): yield from batch.to_pylist()
    elif path.suffix=='.json':
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,list): raise ValueError('native JSON source must contain a list')
        yield from data
    else:
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip(): yield json.loads(line)


def read(path):
    """Standard-library imports stay available without preparing dependencies."""
    if Path(path).suffix == '.parquet':
        raise ValueError('Parquet requires the explicit native_qa preparation runtime')
    yield from _read_native(path)


def _convert(name, root, local):
    from harnessgrad import dataset_runtime
    runtime = dataset_runtime.resolve_execution('native_qa')
    worker = Path(__file__).with_name('prepare_worker.py')
    environment = dataset_runtime._child_environment()
    if not local:
        for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HF_ENDPOINT',
                    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'):
            if key in os.environ:
                environment[key] = os.environ[key]
    # Hub configuration and caches are owned by this explicit preparation attempt.
    environment['HF_HOME'] = str(root / 'hub-cache')
    identity = {'purpose': 'native source acquisition/conversion only',
        'runtime': runtime, 'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
        'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (root / 'worker-identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    request = {'dataset': name, 'root': str(root), 'local': list(local)}
    proc = subprocess.run([runtime['python'], '-I', str(worker)],
        input=json.dumps(request), text=True, capture_output=True, env=environment)
    if proc.returncode:
        # Worker diagnostics can contain private rows, URLs and credentials.
        raise ValueError('native source conversion worker failed; retained attempt for inspection')
    index = json.loads((root / 'conversion.json').read_text())
    sources = [(item['split'], read(root / item['path'])) for item in index]
    return sources, identity


def native_sources(name, root):
    from huggingface_hub import hf_hub_download
    spec=CATALOG[name]
    cache=root/'source'
    if name=='searchqa':
        for split in spec['splits']:
            member='val' if split=='validation' else split
            path=hf_hub_download(repo_id=spec['source'],repo_type='dataset',revision=spec['revision'],filename=f'data/train_test_val/{member}.zip',local_dir=cache)
            def zipped(path=path):
                with zipfile.ZipFile(path) as archive:
                    for file in sorted(archive.namelist()):
                        if file.endswith('.json'): yield json.loads(archive.read(file))
            yield split, zipped()
    elif 'files' in spec:
        for split,filename in spec['files'].items():
            path=hf_hub_download(repo_id=spec['source'],repo_type='dataset',revision=spec['revision'],filename=filename,local_dir=cache)
            yield split, _read_native(path)
    else:
        from datasets import load_dataset
        for config in spec.get('configs',[spec.get('config')]):
            options={'revision':spec['revision'],'streaming':True}
            if config is not None: options['name']=config
            data=load_dataset(spec['source'],**options)
            for split in spec['splits']:
                if split not in data: raise ValueError(f'pinned native dataset omits expected split {split}')
                yield split, iter(data[split])


def _publish_manifest(root, attempt, manifest):
    """Expose a complete attempt with one atomic, non-replacing filesystem link."""
    staged = attempt / 'source-manifest.json'
    with staged.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(staged, root / 'source-manifest.json')
    except FileExistsError:
        raise ValueError('source-manifest.json already exists; preserved competing publication') from None


def prepare(name, root, local=(), *, duplicate_questions='error'):
    if duplicate_questions not in ('error', 'exclude'):
        raise ValueError('duplicate_questions must be error or exclude')
    if name != 'bbeh' and duplicate_questions != 'error':
        raise ValueError('--duplicate-questions is supported only for BBEH')
    root=Path(root).expanduser().resolve()
    if os.path.lexists(root/'source-manifest.json'):
        raise ValueError('source-manifest.json already exists; use a fresh data root to preserve custody')
    root.mkdir(parents=True,exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='preparation-attempt-', dir=root))
    provenance = {'purpose': 'standard-library local native import',
        'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    custody = []
    for value in local:
        split, sep, path = value.partition('=')
        if not sep or split not in CATALOG[name]['splits']:
            raise ValueError('--local requires native-split=path')
        source = Path(path).expanduser().resolve()
        custody.append({'split': split, 'path': str(source),
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    (attempt / 'input-custody.json').write_text(json.dumps({
        'dataset': name, 'source': CATALOG[name]['source'],
        'revision': CATALOG[name]['revision'], 'local_sources': custody,
        'controller_sha256': provenance['controller_sha256']}, indent=2) + '\n')
    if not local or any(Path(v.partition('=')[2]).suffix == '.parquet' for v in local):
        sources, provenance = _convert(name, attempt, local)
        acquisition = ('local-native-import; metadata pin is expected source, not authenticated acquisition'
                       if local else 'huggingface pinned revision')
    elif local:
        sources=[]
        for value in local:
            split, sep, path=value.partition('=')
            if not sep or split not in CATALOG[name]['splits']: raise ValueError('--local requires native-split=path')
            sources.append((split,read(path)))
        acquisition='local-native-import; metadata pin is expected source, not authenticated acquisition'
    files=[]; split_counts={split:0 for split in CATALOG[name]['splits']}
    native_ids=[]
    for index,(split,rows) in enumerate(sources):
        relative=f'rows/{index:02d}-{split}.jsonl'; target=attempt/relative
        target.parent.mkdir(exist_ok=True)
        digest=hashlib.sha256();count=0
        with target.open('wb') as stream:
            for row in rows:
                if name == 'bbeh':
                    native_ids.append(parse_row(name, row, split)[0])
                encoded=(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
                stream.write(encoded);digest.update(encoded);count+=1
        files.append({'path':str(Path(attempt.name)/relative),'split':split,'sha256':digest.hexdigest(),'rows':count})
        split_counts[split]+=count
    if any(count==0 for count in split_counts.values()): raise ValueError('one or more native splits are missing or empty')
    spec=CATALOG[name]
    manifest={'version':1,'dataset':name,'source':spec['source'],'revision':spec['revision'],
              'upstream_commit':'e9c7667945f959d62da27204649a82f94d1b6705',
              'acquisition':acquisition,'files':files,'split_rows':split_counts}
    if name == 'bbeh':
        manifest['selection'], _ = bbeh_selection(native_ids, duplicate_questions)
    if not local:
        custody = [{'path': str(p.relative_to(root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in sorted((attempt/'source').rglob('*')) if p.is_file()]
    if local:
        for item in custody:
            if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('local source changed during preparation')
    manifest['source_files'] = custody
    provenance['scaffold_sha256'] = hashlib.sha256(Path(__file__).with_name('__init__.py').read_bytes()).hexdigest()
    manifest['preparation'] = provenance
    manifest['attempt'] = attempt.name
    _publish_manifest(root, attempt, manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',choices=sorted(CATALOG))
    runtime_mode = parser.add_mutually_exclusive_group()
    runtime_mode.add_argument('--prepare-runtime', action='store_true', help='explicitly install preparation dependencies only')
    runtime_mode.add_argument('--check-runtime', action='store_true', help='read-only dependency artifact check')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--python', help='base Python for explicit runtime preparation')
    parser.add_argument('--root',type=Path)
    parser.add_argument('--local',action='append',default=[],metavar='SPLIT=PATH')
    parser.add_argument('--duplicate-questions',choices=('error','exclude'),
                        help='BBEH only: refuse duplicates (default), or explicitly exclude every repeated question group')
    args=parser.parse_args()
    if args.prepare_runtime or args.check_runtime:
        from harnessgrad import dataset_runtime
        if args.prepare_runtime:
            dataset_runtime.prepare_execution('native_qa', root=args.runtime_root, python=args.python)
        result = dataset_runtime.check_execution('native_qa', root=args.runtime_root)
        print(json.dumps(result, indent=2))
        return 0 if result['present'] else 1
    if not args.dataset:
        parser.error('--dataset is required for data preparation')
    if args.runtime_root is not None:
        os.environ['HG_DATASET_RUNTIME_ROOT'] = str(args.runtime_root)
    if args.duplicate_questions is not None and args.dataset != 'bbeh':
        parser.error('--duplicate-questions is supported only for BBEH')
    root=args.root or Dataset(args.dataset).root
    print(json.dumps(prepare(args.dataset,root,args.local,
        duplicate_questions=args.duplicate_questions or 'error'),indent=2))


if __name__=='__main__':
    from harnessgrad.paths import DataRootNotConfigured
    try:
        raise SystemExit(main())
    except DataRootNotConfigured as exc:
        raise SystemExit(str(exc)) from None
