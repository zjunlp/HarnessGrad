"""Validate the separately pinned AgentDojo source before importing it."""
import hashlib
import json
import os
from pathlib import Path
import sys

MANIFEST = Path(__file__).with_name('source-manifest.json')


def source_manifest():
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'd9fc402c7cd510ef3c766aa7c28fb70e18352b24b6d941a443bcbe60b1654826':
        raise ValueError('AgentDojo source manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest['revision'] != '089ed468cf3ed0322acc66b0211f26d9d90dbf60':
        raise ValueError('AgentDojo source revision mismatch')
    return manifest


def validate_source(source):
    source = Path(source)
    if not source.is_absolute() or source.resolve(strict=True) != source:
        raise ValueError('AgentDojo source requires a physical absolute path')
    manifest = source_manifest()
    package = source / 'agentdojo'
    if (package.is_symlink() or not package.is_dir()
            or {path.name for path in source.iterdir()} != {'agentdojo'}):
        raise ValueError('AgentDojo source inventory mismatch')
    actual = set()
    for path in package.rglob('*'):
        if path.is_symlink():
            raise ValueError('AgentDojo source must not contain links')
        if path.is_file():
            relative = str(path.relative_to(source))
            actual.add(relative)
            if (relative not in manifest['files'] or
                    hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'][relative]):
                raise ValueError('AgentDojo source inventory mismatch')
    if actual != set(manifest['files']):
        raise ValueError('AgentDojo source inventory mismatch')
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def bootstrap(source):
    if sys.version_info[:2] != (3, 12):
        raise ValueError('AgentDojo managed execution requires Python 3.12')
    identity = validate_source(source)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    return identity


def validate_prepared(root):
    root = Path(root)
    raw = Path(__file__).with_name('prepared-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'd4b4528c6bc781a3ebe1ec3bba6c0d5057345d17243168c5ee1c6ebc58018caa':
        raise ValueError('AgentDojo prepared manifest digest mismatch')
    manifest = json.loads(raw)
    catalog = root / '_src/cases.json'
    if catalog.is_symlink() or hashlib.sha256(catalog.read_bytes()).hexdigest() != manifest['catalog_sha256']:
        raise ValueError('AgentDojo prepared catalog digest mismatch')
    entries = []
    prepared = root / 'prepared-ignore_previous'
    if (prepared.is_symlink() or any(p.is_symlink() for p in prepared.iterdir())
            or {p.name for p in prepared.iterdir() if p.is_dir()} != {str(i) for i in range(manifest['tasks'])}):
        raise ValueError('AgentDojo prepared inventory mismatch')
    for index in range(manifest['tasks']):
        for half in ('case.json', 'oracle.json'):
            relative = 'prepared-ignore_previous/' + str(index) + '/' + half
            path = root / relative
            if path.is_symlink():
                raise ValueError('AgentDojo prepared inputs must not be links')
            entries.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    digest = hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()
    if digest != manifest['prepared_sha256']:
        raise ValueError('AgentDojo prepared input digest mismatch')
    return manifest['tasks']
