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
import zipfile

from . import CATALOG, Dataset


def read(path):
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
            yield split, read(path)
    else:
        from datasets import load_dataset
        for config in spec.get('configs',[spec.get('config')]):
            options={'revision':spec['revision'],'streaming':True}
            if config is not None: options['name']=config
            data=load_dataset(spec['source'],**options)
            for split in spec['splits']:
                if split not in data: raise ValueError(f'pinned native dataset omits expected split {split}')
                yield split, iter(data[split])


def prepare(name, root, local=()):
    root=Path(root).expanduser().resolve()
    if (root/'source-manifest.json').exists():
        raise ValueError('source-manifest.json already exists; use a fresh data root to preserve custody')
    root.mkdir(parents=True,exist_ok=True)
    if local:
        sources=[]
        for value in local:
            split, sep, path=value.partition('=')
            if not sep or split not in CATALOG[name]['splits']: raise ValueError('--local requires native-split=path')
            sources.append((split,read(path)))
        acquisition='local-native-import; metadata pin is expected source, not authenticated acquisition'
    else:
        sources=native_sources(name,root)
        acquisition='huggingface pinned revision'
    files=[]; split_counts={split:0 for split in CATALOG[name]['splits']}
    for index,(split,rows) in enumerate(sources):
        relative=f'rows/{index:02d}-{split}.jsonl'; target=root/relative
        target.parent.mkdir(exist_ok=True)
        digest=hashlib.sha256();count=0
        with target.open('wb') as stream:
            for row in rows:
                encoded=(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
                stream.write(encoded);digest.update(encoded);count+=1
        files.append({'path':relative,'split':split,'sha256':digest.hexdigest(),'rows':count})
        split_counts[split]+=count
    if any(count==0 for count in split_counts.values()): raise ValueError('one or more native splits are missing or empty')
    spec=CATALOG[name]
    manifest={'version':1,'dataset':name,'source':spec['source'],'revision':spec['revision'],
              'upstream_commit':'e9c7667945f959d62da27204649a82f94d1b6705',
              'acquisition':acquisition,'files':files,'split_rows':split_counts}
    (root/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True,choices=sorted(CATALOG))
    parser.add_argument('--root',type=Path)
    parser.add_argument('--local',action='append',default=[],metavar='SPLIT=PATH')
    args=parser.parse_args()
    root=args.root or Dataset(args.dataset).root
    print(json.dumps(prepare(args.dataset,root,args.local),indent=2))


if __name__=='__main__': main()
