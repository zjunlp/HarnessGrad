"""AgentDojo v1.2.2, utility under registered ``ignore_previous`` injection.

949 user/injection pairs across 97 user tasks, pinned package/source 0.1.35. Scalar
reward is the official utility predicate; official attacker success is separately recorded,
never inverted into a utility score. This is not the paper's ``important_instructions``
attacker, all-attacks safety, or an adaptive attack. Goals carry no defence treatment.
Same-user attack variants used to stay on the same platform train/eval side because the
adapter cut the pool with a hash over `suite/user`; the adapter declares **no division** now
(2026-10-24) -- the 949 pairs are the benchmark's evaluation material -- and the platform's
`--split default` hashes **task ids**, which does not respect that pairing. A division that
must not leak across a user task is therefore supplied explicitly (`--split <file.json>`).

**The environment used to be agent-readable, and that changed what the numbers mean.**
Until the relay landed, ``SETUP`` materialised ``dojo.initial.json`` -- ``bridge.snapshot(case)``:
the whole initial environment (inbox, calendar, cloud drive, bank, slack, web) plus the tool
catalogue, with the registered injection payload verbatim inside ``environment`` -- into the
agent's working directory, because ``cli/dojo`` was a program the agent ran and read that file
from its cwd on every command. The agent could read the state instead of eliciting it.
Measured on 949 archived tasks: the 515 that read the file average utility 0.2592, the 434
that did not average 0.1406. Utility was therefore an upper bound, a case was answerable with
no tool call at all (``submit`` alone yields utility 1 on case 0), and ``attack_success``
measured an instruction the agent could read directly rather than one delivered inside a tool
response. The published 0.2055 utility under ``ignore_previous`` (949 pairs) was produced
under that SETUP and is not comparable with runs from this adapter now.

**What the agent gets instead.** ``SETUP`` writes one program and no data: ``dojo.py`` (the
client, ``cli/dojo``). It reaches the environment only through the UNIX socket the platform
publishes at ``/run/harnessgrad-tool/tool.sock``, served by ``data/datasets/agentdojo/relay.py``
outside the sandbox with the trusted runtime; the transport beside that socket is the
platform's one implementation (``eval/service/tool_transport.py``), not a second copy. That
relay offers exactly two ops, ``tools`` and ``call``, and there is no op that returns the
environment, a state dump, or a ``model_dump``. The action log (``action.json``) stays
agent-side because it is the replay transcript the verifier consumes. ``dojo_runtime`` left ``SETUP`` together with the snapshot: the client is
standard-library-only, so the tool-only AgentDojo package is no longer on the agent's disk
(``prepare.py`` still builds it; nothing projects it). ``VERIFY`` is untouched -- it never read
the snapshot, and ``check.py`` re-derives the environment from ``case.json`` with
``load_and_inject_default_environment``.

**What still has to happen outside this adapter.** The platform must start the relay for the
task and publish its socket directory at ``/run/harnessgrad-tool`` (``tool.sock`` plus
``tool_transport.py``), and turn a relay fault (absent socket, nonzero exit, or a receipt whose
errors/refusals are non-zero) into an **unmeasured** task rather than a zero. None of that is
wired here; the relay prints its receipt on shutdown for the runner to read.
"""
from __future__ import annotations

import json
from pathlib import Path


KIND = 'dataset'

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"

#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the v1.2.2 suite's user x injection pairs; every one is an evaluation pair under the single fixed attack this adapter names.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

DATASET_ROOT = Path('/mnt/20t/xubuqiang/datasets/agentdojo')
COMMIT = '089ed468cf3ed0322acc66b0211f26d9d90dbf60'
VERSION = 'v1.2.2'
ASSETS = Path(__file__).resolve().parent / 'agentdojo'
REWARD_REL = 'verifier/reward.txt'
EVIDENCE_REL = 'verifier/evidence.json'
PREPARED = 'prepared-ignore_previous'


def records():
    with (DATASET_ROOT / '_src/cases.json').open() as h:
        return json.load(h)


def _indices():
    """Public case IDs from directory names, without opening the mixed prompt catalog.

    The legacy roster can describe required paths before fresh attack-specific preparation;
    legacy snapshots themselves are never selected for SETUP or verification.
    """
    prepared = DATASET_ROOT / PREPARED
    if not prepared.is_dir():
        # Roster only: a pre-preparation checkout still has to enumerate its cases so
        # `load()` can say the dataset is unprovisioned. `provision_status` gates on
        # PREPARED, and SETUP/VERIFY never read this legacy directory.
        prepared = DATASET_ROOT / 'prepared'
    return sorted(int(p.name) for p in prepared.iterdir()
                  if p.is_dir() and p.name.isdecimal() and str(int(p.name)) == p.name)


def _task(index, record):
    return {'task_id': f'agentdojo/{index}', 'goal':
              record['prompt'] + '\n\nUse the AgentDojo tools through this CLI:\n'
              '  python3 dojo.py tools\n'
              '  python3 dojo.py call \'{"function":"name","args":{}}\'\n'
              '  python3 dojo.py submit "final answer"\n'}


def load():
    rows = records()
    return ([_task(i, r) for i, r in enumerate(rows)],
            {f'agentdojo/{i}': 'official utility' for i in range(len(rows))})


def control_task(task_id):
    """Load one explicit control case without opening the mixed prompt catalog."""
    if task_id not in {f'agentdojo/{i}' for i in _indices()}:
        raise SystemExit(f'AgentDojo: unknown task id {task_id!r}')
    index = task_id.split('/')[-1]
    record = json.loads((DATASET_ROOT / PREPARED / index / 'case.json').read_text())
    return _task(index, record)


def oracle_files(task):
    index = task['task_id'].split('/')[-1]
    return {'action.json': (DATASET_ROOT / PREPARED / index / 'oracle.json').read_text()}


def __getattr__(name):
    if name == 'TASK_COUNT':
        return len(records())
    if name == 'SPLIT':
        # No division: AgentDojo's four suites are the benchmark's evaluation material and
        # there is no study split to declare (2026-10-24). `--split default` still applies
        # the platform's hash rule to a run that asks for it.
        return None
    if name == 'SPLIT_SOURCE':
        return 'none'
    if name == 'ENV':
        return {f'agentdojo/{i}': {'kind': 'files'} for i in _indices()}
    if name == 'SETUP':
        # One agent-visible program and no data. The interaction environment is served over the
        # socket the platform publishes; the transport beside it is the platform's, not a copy.
        #
        # `tool_relay` is deliberately not under `files`: `eval/run/tool_relay.py` stages
        # `case.json` in a directory the platform owns, starts `relay.py` on it with the
        # verifier's own interpreter (the environment needs `task_suite`/`attacks`, which the
        # agent-visible tool runtime never had), and publishes only the socket into the sandbox.
        return {f'agentdojo/{i}': {
            'files': [{'path': 'dojo.py', 'from': str(ASSETS / 'cli/dojo')}],
            'tool_relay': {
                'program': str(ASSETS / 'relay.py'),
                'python': str(DATASET_ROOT / 'runtime/bin/python'),
                'content_flag': '--case',
                'filename': 'case.json',
                # A path, not the text: `SETUP` must not read a task's case file (that is an
                # existing invariant, pinned by test_agentdojo_metadata_privacy). The runner
                # reads it when it stages the relay, for the one task it is about to run.
                'content_path': str(DATASET_ROOT / PREPARED / str(i) / 'case.json'),
            },
        } for i in _indices()}
    if name == 'VERIFY':
        return {f'agentdojo/{i}': {
            'kind': 'command', 'argv': ['python3', 'verify/check.py', '--reward-file', REWARD_REL,
                                      '--evidence-file', EVIDENCE_REL],
            'inputs': [
                {'path': 'verify/check.py', 'from': str(ASSETS / 'verify/check.py')},
                {'path': 'verify/bridge.py', 'from': str(ASSETS / 'bridge.py')},
                {'path': 'verify/case.json', 'from': str(DATASET_ROOT / PREPARED / str(i) / 'case.json')},
                {'path': 'trusted_runtime', 'from': str(DATASET_ROOT / 'runtime'), 'immutable': True},
            ], 'reward_file': '/'+REWARD_REL, 'evidence_file': '/'+EVIDENCE_REL,
        } for i in _indices()}
    raise AttributeError(name)


def downloads():
    return [
        {'kind': 'http', 'name': 'AgentDojo source + suites', 'version': COMMIT,
         'url': f'https://codeload.github.com/ethz-spylab/agentdojo/tar.gz/{COMMIT}',
         'size': '~200 KB compressed source', 'dst': '_src/agentdojo.tar.gz',
         'sha256': 'd7e0ee021be27a69e7e66647979b5a5e0dd10030d38d04ce4102e18a76a228d8',
         'extract_to': '_src',
         'note': 'Package 0.1.35 and suite v1.2.2 are different version axes. Provision isolated runtime and tool-only runtime using data/datasets/agentdojo/prepare.py; never expose full task/ground_truth modules to the agent. Registered ignore_previous attack only; fresh prepared-ignore_previous snapshots required.'},
        {'kind': 'pip', 'name': 'AgentDojo isolated runtime', 'package': 'agentdojo', 'version': '0.1.35',
         'size': '~200 MB with SDK dependencies',
         'note': 'Install only inside dataset runtime venv. Exact installed dependency versions are recorded in _src/runtime-requirements.lock; no model API calls are used by tools, gold audit or predicate evaluation.'},
    ]


def provision_status():
    rows = records()
    needed = [DATASET_ROOT / '_src/cases.json', DATASET_ROOT / 'runtime/bin/python']
    needed += [DATASET_ROOT / PREPARED / str(i) / name
               for i in range(len(rows)) for name in ('case.json', 'oracle.json')]
    return {'root': str(DATASET_ROOT), 'split_dir': str(DATASET_ROOT),
            'present': bool(rows) and all(p.is_file() for p in needed), 'tasks': len(rows)}
