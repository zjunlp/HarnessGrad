"""Public actor command/backend payloads, distinct from private verification inputs.

Factories only project already selected values. They do not allocate directories,
prepare runtimes, start services, create networks or execute any command.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActorLaunchSpec:
    backend: str
    argv: list[str]
    options: dict


def argv_for(repo, entry, *, python, task_argument, workdir_argument, managed_argv):
    base = managed_argv if managed_argv is not None else [python, str(repo / entry)]
    return base + ['--task', task_argument, '--workdir', workdir_argument]


def process(argv, *, timeout_s, cwd, environment):
    return ActorLaunchSpec('process', argv, {'timeout_s': timeout_s, 'cwd': cwd, 'env': environment})


def container(argv, *, image, task_dir, task_mount, harness_root, state_dir, managed,
              environment, timeout_s, network, limits, readonly_paths, immutable_runtimes, actor_mounts):
    return ActorLaunchSpec('container', argv, {
        'image': image, 'task_dir': task_dir, 'task_mount': task_mount, 'harness_root': harness_root,
        'state_dirs': () if managed else (state_dir,), 'cwd': task_mount, 'env': environment,
        'timeout_s': timeout_s, 'network': network, 'limits': limits, 'readonly_paths': readonly_paths,
        'immutable_runtimes': immutable_runtimes, 'extra_mounts': actor_mounts})


def sandbox(argv, *, platform, work_root, task_dir, environment, timeout_s, repo, state_dir,
            runtime_paths, limits, stepwise_dir, surfaced_runtime, network, model_socket,
            model_path, tool_socket, package_overlays, immutable_runtimes, managed, private_paths):
    options = {'platform': platform, 'work_root': work_root, 'task_dir': task_dir,
        'env': environment, 'timeout_s': timeout_s, 'cwd': task_dir, 'repo': repo,
        'state_dirs': (state_dir,), 'runtime_paths': runtime_paths, 'limits': limits,
        'visible_paths': ((stepwise_dir,) if stepwise_dir else ()) + surfaced_runtime,
        'network': network, 'model_socket': model_socket, 'model_path': model_path,
        'tool_socket': tool_socket, 'package_overlays': package_overlays,
        'immutable_runtimes': immutable_runtimes}
    if managed:
        options.update(platform_interpreter=False,
                       opaque_source_dirs=('.git', '_harnessgrad', '.opencode-state', '.state'))
    if private_paths:
        options['private_paths'] = private_paths
    return ActorLaunchSpec('sandbox', argv, options)
