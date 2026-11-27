"""Serve a pinned GR00T N1.5 checkpoint on a private local UNIX socket."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from importlib.metadata import distributions
from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from eval.action_policies.ray_policy import local_ray, RayPolicyService
from eval.service.action_policy import PROTOCOL, digest
from eval.service.tool_transport import Relay


def checkpoint_hashes(checkpoint):
    index = checkpoint / 'model.safetensors.index.json'
    if index.is_file():
        shards = sorted(set(json.loads(index.read_text())['weight_map'].values()))
        if not shards or any(not re.fullmatch(r'model-[0-9]+-of-[0-9]+\.safetensors', p) for p in shards):
            raise ValueError('invalid safetensors index')
        files = [index.name, *shards]
    else:
        files = ['model.safetensors']
    hashes = {}
    for name in ['config.json', 'experiment_cfg/metadata.json', *files]:
        with (checkpoint / name).open('rb') as stream:
            fingerprint = sha256()
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                fingerprint.update(chunk)
            hashes[name] = fingerprint.hexdigest()
    if index.is_file() and (checkpoint / 'model.safetensors').exists():
        raise ValueError('checkpoint contains both single-file and sharded weights')
    return hashes


def source_revision(source):
    if not (source / 'gr00t/model/policy.py').is_file():
        raise ValueError('source must be a local Isaac-GR00T checkout')

    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()

    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Isaac-GR00T checkout must be clean')
    return git('rev-parse', 'HEAD')


def selected_gpu(device):
    match = re.fullmatch(r'cuda:([0-9]+)', device)
    if not match:
        raise ValueError('GR00T requires an explicit CUDA device, for example cuda:0')
    index = int(match[1])
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    if visible is None:
        return str(index)
    devices = [item.strip() for item in visible.split(',')]
    if index >= len(devices) or not devices[index] or '-1' in devices[:index + 1]:
        raise ValueError(f'{device} is outside CUDA_VISIBLE_DEVICES')
    return devices[index]


class Gr00tWorker:
    def __init__(self, args):
        checkpoint = args.checkpoint.resolve(strict=True)
        source = args.source.resolve(strict=True)
        revision = source_revision(source)
        hashes = checkpoint_hashes(checkpoint)
        sys.path.insert(0, str(source))
        import numpy as np
        from PIL import Image
        import torch
        from gr00t.experiment.data_config import DATA_CONFIG_MAP
        from gr00t.model.policy import Gr00tPolicy

        self.np = np
        self.image = Image
        self.torch = torch
        self.device = torch.device(args.device)
        torch.cuda.set_device(self.device)
        data = DATA_CONFIG_MAP[args.data_config]
        config = data.modality_config()
        for stream in ('video', 'state', 'language'):
            if list(config[stream].delta_indices) != [0]:
                raise ValueError('the current policy port supports current-frame checkpoints')
        self.cameras = list(config['video'].modality_keys)
        self.states = list(config['state'].modality_keys)
        self.language, = config['language'].modality_keys
        self.actions = list(config['action'].modality_keys)
        self.chunk_size = len(config['action'].delta_indices)
        self.policy = Gr00tPolicy(model_path=str(checkpoint), modality_config=config,
                                 modality_transform=data.transform(), embodiment_tag=args.embodiment,
                                 denoising_steps=args.denoising_steps, device=self.device)
        self.identity = {
            'protocol': PROTOCOL, 'backend': 'gr00t-n1.5', 'source_commit': revision,
            'checkpoint_sha256': hashes, 'adapter_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'data_config': args.data_config, 'embodiment': args.embodiment,
            'denoising_steps': args.denoising_steps, 'control': args.control,
            'cameras': self.cameras, 'states': self.states, 'actions': self.actions,
            'chunk_size': self.chunk_size, 'gpu': torch.cuda.get_device_name(self.device),
            'python': list(sys.version_info[:3]), 'cuda': torch.version.cuda,
            'packages_sha256': digest(sorted((dist.metadata['Name'], dist.version) for dist in distributions())),
        }

    def describe(self):
        return self.identity

    def predict(self, args):
        start = time.monotonic()
        observation = {self.language: [args['instruction']]}
        for name in self.cameras:
            raw = base64.b64decode(args['images'][name], validate=True)
            with self.image.open(BytesIO(raw)) as image:
                observation[name] = self.np.array(image.convert('RGB'))[None, ...]
        for name in self.states:
            observation[name] = self.np.asarray(args['state'][name], dtype=self.np.float32)[None, ...]
        torch = self.torch
        # Isolate noise sampling from other episodes sharing this server.
        with torch.cuda.device(self.device), torch.random.fork_rng(devices=[self.device.index]):
            torch.random.default_generator.manual_seed(args['seed'])
            torch.cuda.manual_seed(args['seed'])
            prediction = self.policy.get_action(observation)
        actions = [{key: prediction[key][i].tolist() for key in self.actions}
                   for i in range(self.chunk_size)]
        return {'actions': actions, 'inference_seconds': time.monotonic() - start}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--checkpoint', required=True, type=Path)
    parser.add_argument('--data-config', required=True)
    parser.add_argument('--embodiment', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--denoising-steps', type=int, default=4)
    args = parser.parse_args()
    args.socket = args.socket.absolute()
    if (args.socket.resolve() != args.socket or args.socket.exists()
            or args.socket.parent.stat().st_mode & 0o077):
        parser.error('--socket must be an unused path in a private directory')
    if args.denoising_steps < 1:
        parser.error('--denoising-steps must be positive')
    try:
        os.environ['CUDA_VISIBLE_DEVICES'] = selected_gpu(args.device)
    except ValueError as exc:
        parser.error(str(exc))
    # Ray allocates the one visible GPU; CUDA numbers it from zero in the actor.
    args.device = 'cuda:0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'
    with local_ray(num_gpus=1) as ray:
        service = RayPolicyService(ray, Gr00tWorker, args, num_gpus=1,
                                   inference_timeout_s=10.0, recovery_timeout_s=120.0)
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        with Relay(args.socket, service.handle, max_calls=None):
            print(f'GR00T ready, identity {service.signature}', flush=True)
            stop.wait()


if __name__ == '__main__':
    main()
