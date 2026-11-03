#!/usr/bin/env python3
"""Prepare fixed native source without running an actor or judge.

For JobBench, --render explicitly invokes the pinned author's renderer after
source preparation. The renderer requires its declared Python dependencies and
access to the pinned HF source. It never runs an actor or judge.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

NAME = 'swe_bench_pro'
SOURCE = json.loads((Path(__file__).parent / 'SOURCE.json').read_text())
RENDERER_SOURCE = 'https://github.com/Job-Bench/job-bench-eval'
RENDERER_REVISION = '660b33a86ed032559df0b2fdc1ad38cf7ceb83f3'


def extract(archive, root):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    with tarfile.open(archive, 'r:*') as native:
        for entry in native.getmembers():
            parts = Path(entry.name).parts
            if not parts or '..' in parts or Path(entry.name).is_absolute():
                raise ValueError('unsafe native archive path')
            if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                raise ValueError('native source archive contains non-plain entry')
            relative = Path(*parts[1:])
            if not relative.parts or entry.isdir():
                continue
            output = root / relative
            if output.is_symlink() or not output.resolve().is_relative_to(root):
                raise ValueError('native extraction path leaves source root')
            data = native.extractfile(entry).read()
            if output.exists() and output.read_bytes() != data:
                raise ValueError(f'pre-existing native file differs: {output}')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(0o644 | (entry.mode & 0o111))
            files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    if not files:
        raise ValueError('empty native source archive')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    repo = RENDERER_SOURCE if NAME == 'jobbench' else SOURCE['source']
    revision = RENDERER_REVISION if NAME == 'jobbench' else SOURCE['revision']
    root = args.root.resolve()
    archive = args.archive or root / '_src' / f'{NAME}-{revision}.tar.gz'
    if not archive.is_file():
        repo = repo.removeprefix('https://github.com/').removesuffix('.git')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f'https://codeload.github.com/{repo}/tar.gz/{revision}', timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as temp:
                pending = Path(temp.name)
                try:
                    while chunk := response.read(1 << 20):
                        temp.write(chunk)
                    temp.close()
                    pending.replace(archive)
                finally:
                    pending.unlink(missing_ok=True)
    files = extract(archive, root)
    manifest = {'dataset': NAME, 'source': repo, 'revision': revision,
                'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': files}
    (root / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if NAME == 'jobbench' and args.render:
        # This is the exact native renderer invocation, not a rewritten task
        # generator. Both source revisions remain recorded in its manifest.
        code = ("import json,sys; from pathlib import Path; "
                "sys.path.insert(0,str(Path(sys.argv[1])/'harbor')); "
                "from jobbench_harbor.hf_source import fetch_snapshot; "
                "from jobbench_harbor.prepare import generate; "
                "r=Path(sys.argv[1]); s=fetch_snapshot(r/'harbor/.cache/huggingface',repo_id='JobBench/job-bench',revision=sys.argv[2]); "
                "m=generate(s.path,r/'harbor',repo_id='JobBench/job-bench',revision=sys.argv[2],judge_path=r/'eval/judge.py',remote_files=s.remote_files); "
                "print(json.dumps(m))")
        subprocess.run([sys.executable, '-I', '-c', code, str(root), SOURCE['revision']], check=True)
    print(json.dumps({'prepared_source': True, 'root': str(root), 'files': len(files),
                      'execution_ready': False, 'detail': 'Native source preparation is not execution admission.'}))


if __name__ == '__main__':
    main()
