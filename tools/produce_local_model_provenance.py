#!/usr/bin/env python3
"""Read-only, no-network source provenance; never attests GPU-resident bytes.

Hashes are SHA256 of canonical JSON (sorted keys, compact separators) manifests.
Only explicitly allowlisted command flags are recorded; environment and raw argv
are never serialized. Run once per new measurement identity, never over old receipts.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import time

TOKENIZER = ('tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt', 'chat_template.jinja')
CONFIG = ('config.json', 'configuration.json', 'preprocessor_config.json', 'video_preprocessor_config.json')
FLAGS = ('--served-model-name', '--max-model-len', '--gpu-memory-utilization', '--port', '--revision', '--tokenizer-revision')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def hash_file(path):
    before = path.stat()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('source changed while hashing: ' + path.name)
    return {'bytes': after.st_size, 'sha256': h.hexdigest()}


def manifest(root, names):
    result = {}
    for name in sorted(names):
        path = root / name
        if Path(name).name != name or not path.is_file() or path.is_symlink():
            raise ValueError('missing, unsafe or symlink source: ' + name)
        started = time.monotonic()
        result[name] = hash_file(path)
        print(json.dumps({'completed': name, 'bytes': result[name]['bytes'], 'seconds': round(time.monotonic()-started, 3)}), file=sys.stderr, flush=True)
    return result


def declared_arguments(argv):
    """Parse supported single-value vLLM flags; ambiguous declarations refuse."""
    # Recognize the invocation before considering any model/resource flag. This
    # checks declaration syntax only, not executable/module authenticity.
    serve_index = None
    api_arguments = None
    python = bool(argv and re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', Path(argv[0]).name))
    if argv and Path(argv[0]).name == 'vllm':
        serve_index = 1
    elif python and len(argv) > 1 and Path(argv[1]).name == 'vllm':
        serve_index = 2
    elif python and len(argv) > 2 and argv[1] == '-m':
        if argv[2] == 'vllm.entrypoints.openai.api_server':
            api_arguments = argv[3:]
        elif argv[2] in {'vllm', 'vllm.entrypoints.cli.main'}:
            serve_index = 3
    if serve_index is not None and len(argv) > serve_index and argv[serve_index] == 'serve':
        arguments = argv[serve_index + 1:]
    elif api_arguments is not None:
        arguments = api_arguments
    else:
        raise ValueError('process does not declare a recognized vLLM invocation')
    parsed = {}
    recognized = set(FLAGS) | {'--model', '--tokenizer'}
    for position, argument in enumerate(arguments):
        flag, separator, value = argument.partition('=')
        # Reject short model/tokenizer spellings rather than silently overlooking
        # a possible override; only documented long-form declarations are supported.
        if flag in {'-m', '-t'}:
            raise ValueError('unsupported short model/tokenizer argument')
        if flag not in recognized:
            continue
        if flag in parsed:
            raise ValueError('duplicate deployment argument: ' + flag)
        if not separator:
            if position + 1 >= len(arguments):
                raise ValueError('missing deployment argument: ' + flag)
            value = arguments[position + 1]
        if not value or value.startswith('--'):
            raise ValueError('missing deployment argument: ' + flag)
        parsed[flag] = value
    # Only the first argument of a recognized serve command is positional MODEL.
    positional = ([argv[serve_index + 1]]
                  if serve_index is not None and len(argv) > serve_index + 1
                  and argv[serve_index] == 'serve'
                  and not argv[serve_index + 1].startswith('-') else [])
    if positional:
        if len(positional) != 1 or '--model' in parsed:
            raise ValueError('ambiguous model declaration')
        parsed['--model'] = positional[0]
    return parsed


def process_binding(pid, root):
    proc = Path('/proc') / str(pid)
    start_before = (proc/'stat').read_text().rsplit(')', 1)[1].split()[19]
    argv_bytes = (proc/'cmdline').read_bytes()
    argv = argv_bytes.decode().rstrip('\0').split('\0')
    parsed = declared_arguments(argv)
    if parsed.get('--model') != str(root):
        raise ValueError('process does not declare requested model path')
    if '--tokenizer' in parsed and parsed['--tokenizer'] != str(root):
        raise ValueError('separate tokenizer path is not covered by source manifest')
    safe = {flag: parsed[flag] for flag in FLAGS if flag in parsed}
    if safe.get('--gpu-memory-utilization') != '0.80' or safe.get('--max-model-len') != '262144':
        raise ValueError('unexpected deployment resource configuration')
    if safe.get('--served-model-name') != 'qwen3.5-9b-local' or safe.get('--port') not in ('8001', '8002'):
        raise ValueError('unexpected deployment route')
    if ((proc/'stat').read_text().rsplit(')', 1)[1].split()[19] != start_before
            or (proc/'cmdline').read_bytes() != argv_bytes):
        raise ValueError('process replaced during observation')
    return {'pid': pid, 'start_ticks': start_before, 'model_path': str(root), 'flags': safe}


def produce(root, pids):
    root = root.resolve()
    index_bytes = (root/'model.safetensors.index.json').read_bytes()
    index = json.loads(index_bytes)
    shards = set(index['weight_map'].values())
    if not shards:
        raise ValueError('empty weight map')
    weights = manifest(root, shards | {'model.safetensors.index.json'})
    expected_index = {'bytes': len(index_bytes), 'sha256': hashlib.sha256(index_bytes).hexdigest()}
    if weights['model.safetensors.index.json'] != expected_index:
        raise ValueError('weight index changed after selecting shards')
    tokenizer = manifest(root, TOKENIZER)
    config = manifest(root, CONFIG)
    processes = [process_binding(pid, root) for pid in pids]
    if (root/'model.safetensors.index.json').read_bytes() != index_bytes:
        raise ValueError('weight index changed during collection')
    if len(processes) != 2 or {p['flags']['--port'] for p in processes} != {'8001', '8002'}:
        raise ValueError('both distinct endpoint processes required')
    deployment = {'config_files': config, 'process_observations': processes}
    result = {'schema': 'harnessgrad-local-model-provenance-v1', 'observed_at_unix': time.time(),
              'model_path': str(root), 'weights': weights, 'tokenizer': tokenizer, 'deployment': deployment,
              'hash_contract': 'sha256(canonical JSON: sorted keys, compact separators, ensure_ascii=True)',
              'limitations': ['Source files and process declarations only; no live GPU weight/tokenizer attestation.',
                             'No inference or endpoint request performed; aliases alone do not establish identity.',
                             'No upstream revision inferred; directory name is not a verified upstream commit.',
                             'Source stats checked per file, not an atomic filesystem snapshot; rehash if source changes.']}
    result['bindings_by_port'] = {p['flags']['--port']: {
        'backend': 'openai', 'name': 'qwen3.5-9b-local',
        'endpoint': 'http://127.0.0.1:' + p['flags']['--port'] + '/v1',
        'weights_sha256': digest(weights), 'tokenizer_sha256': digest(tokenizer),
        'deployment_sha256': digest(deployment)} for p in processes}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--pid', type=int, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; old provenance is immutable')
    result = produce(args.model_dir, args.pid)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')


if __name__ == '__main__':
    main()
