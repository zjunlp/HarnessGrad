"""Provision an AgentDojo tool-only runtime and trusted case snapshots outside git.

Only upstream functions_runtime, strenum, tool implementations, and suite environment
classes are retained in the agent package. Task, ground_truth, scoring and attack code
are absent. Full trusted runtime remains separate for evaluator use.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path('/mnt/20t/xubuqiang/datasets/agentdojo')
COMMIT = '089ed468cf3ed0322acc66b0211f26d9d90dbf60'
SOURCE = DATA / '_src' / f'agentdojo-{COMMIT}' / 'src' / 'agentdojo'


def prepare_tools():
    runtime = DATA / 'agent_runtime'
    if runtime.exists():
        raise FileExistsError(f'{runtime} exists; audit before replacing it')
    shutil.copytree(DATA / 'runtime', runtime, symlinks=True)
    site = next(runtime.glob('lib/python*/site-packages'))
    package = site / 'agentdojo'
    shutil.rmtree(package)
    package.mkdir()
    (package / '__init__.py').write_text('"""Tool-only AgentDojo package; no tasks or gold."""\n')
    for name in ['functions_runtime.py', 'strenum.py']:
        shutil.copy2(SOURCE / name, package / name)
    for rel in ['default_suites', 'default_suites/v1']:
        path = package / rel
        path.mkdir(exist_ok=True)
        (path / '__init__.py').write_text('')
    shutil.copytree(SOURCE / 'default_suites/v1/tools', package / 'default_suites/v1/tools',
                    ignore=shutil.ignore_patterns('__pycache__'))
    for suite in ['workspace', 'banking', 'travel', 'slack']:
        src = SOURCE / f'default_suites/v1/{suite}/task_suite.py'
        tree = ast.parse(src.read_text())
        kept = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == 'agentdojo.task_suite.task_suite':
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)):
                kept.append(node)
            elif isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == 'TOOLS' for x in node.targets):
                kept.append(node)
        target = package / f'default_suites/v1/{suite}'
        target.mkdir()
        (target / '__init__.py').write_text('')
        (target / 'environment.py').write_text(ast.unparse(ast.Module(body=kept, type_ignores=[])) + '\n')
    # No stale bytecode from the full package can preserve its ground-truth modules.
    assert not list(package.rglob('*user_task*'))
    assert not list(package.rglob('*ground_truth*'))
    return runtime


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


if __name__ == '__main__':
    trusted_python = DATA / 'runtime/bin/python'
    if Path(sys.prefix) != DATA / 'runtime':
        raise SystemExit(f'Run this with {trusted_python}; provision that venv from the pinned source first.')
    print('cases:', prepare_cases())
    if not (DATA / 'agent_runtime').exists():
        print('tools:', prepare_tools())
    else:
        print('tool-only runtime already exists; inspect before replacing')
