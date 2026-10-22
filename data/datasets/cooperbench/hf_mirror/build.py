"""Stage a dedicated immutable mirror layer on an already prepared runtime image."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil


def stage(assets, output, base_image, python):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', base_image):
        raise ValueError('base runtime must be an immutable local image ID')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', python) or '..' in Path(python).parts:
        raise ValueError('real interpreter must be an absolute image path')
    source = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('hf_mirror_server', source / 'server.py')
    server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
    manifest, sha = server.load(assets)
    output.mkdir(parents=True, exist_ok=False)
    blobs = output / 'assets/blobs'; blobs.mkdir(parents=True)
    shutil.copyfile(assets / 'manifest.json', output / 'assets/manifest.json')
    required = set()
    for row in manifest['repos'].values():
        required.update((row['info_blob'], row['tree_blob']))
        required.update(item['sha256'] for item in row['files'].values())
    for name in sorted(required): shutil.copyfile(assets / 'blobs' / name, blobs / name)
    for name in ('server.py', 'launcher.py', 'python'): shutil.copyfile(source / name, output / name)
    (output / 'python').write_text((source / 'python').read_text().replace('@REAL_PYTHON@', python))
    config = {'python': python, 'manifest_sha256': sha}
    (output / 'config.json').write_text(json.dumps(config, sort_keys=True) + '\n')
    (output / 'Dockerfile').write_text(
        f'FROM {base_image}\nUSER 0:0\n'
        'COPY assets /opt/hg-hf/assets\n'
        'COPY server.py launcher.py python config.json /opt/hg-hf/\n'
        f'RUN {python} -I -S -c \"import sys; assert sys.version_info >= (3, 10)\" && '
        'chmod -R a+rX /opt/hg-hf && chmod 0555 /opt/hg-hf/python\n')
    identity = {'base_image': base_image, 'manifest_sha256': sha, 'real_python': python,
                'runtime_python': '/opt/hg-hf/python',
                'source_sha256': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                  for name in ('server.py', 'launcher.py', 'python', 'config.json', 'Dockerfile')}}
    (output / 'mirror-build.json').write_text(json.dumps(identity, sort_keys=True, indent=2) + '\n')
    return identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--python', required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.assets, args.output, args.base_image, args.python), sort_keys=True))
