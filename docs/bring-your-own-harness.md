# Bringing your own harness: from a directory to a measured curve

You have a directory with an `agent.py` in it (or a `run.sh`, or a wrapper around somebody
else's agent CLI). You want it on a curve this platform measured. This page is the sequence
you run.

The contract itself is written down twice already, and neither document is duplicated here:

* [`docs/writing_a_harness.md`](writing_a_harness.md) — what a harness **is**: the manifest,
  the task contract, the answer file, the sandbox;
* [`docs/adapter-spec.md`](adapter-spec.md) — the five things an adapter must do, and the one
  trace record it must write.

Those are the reference. This page is the on-ramp: every command below was executed on
`bedc17b` (2026-11-02), on Linux, Python 3.12.4, with `HG_AGENT_BACKEND=mock` and no network.
Output is pasted as printed and trimmed only where a block says so; exit codes are the real
ones. Two things the reader should hold on to, because both cost an afternoon:

* **The driver's exit code is a verdict on the run, not on your harness.** A run in which
  every task scored zero — including a run in which the harness never started — exits `0`.
  What happened is in `curve.jsonl`, and §4 is how to read it.
* **This platform invokes your entrypoint as `<python> <entrypoint>`**, whatever language it
  is written in. §1.4 is the measured consequence.

---

## 1. What your directory has to be

### 1.1 Two files, and a manifest

A harness is a directory with a `harness.json` and a command. Nothing else is required; the
platform never looks inside beyond the manifest. Here is a `files` harness's manifest with every
field a reader will meet — `runtime` and `install` are the two a container harness adds, and
§1.2 lists both:

```json
{
  "name": "bash-example",
  "version": "0.1.0",
  "path": ".",
  "entrypoint": "run.py",
  "backend": "cli",
  "env_kinds": ["files"],
  "runtime_paths": ["/usr/bin"],
  "role": "harness",
  "notes": "the answer is a constant unless HG_BASH_EXAMPLE_ANSWER says otherwise",
  "components": {
    "control": ["harness.json", "run.py", "run.sh"]
  }
}
```

```
$ python3 tools/validate_harness.py --smoke /tmp/byo-full
validating /tmp/byo-full
  OK    harness.json valid, smoke run produced an answer
[exit 0]
```

(`/tmp/byo-full` is `examples/bash-harness` with that manifest, so the directory is one you
can read. An entry under `runtime_paths` that does not exist on the host is **skipped in
silence**, not refused — `eval/containers/sandbox.py` binds each one only `if target.exists()`,
and measured: a manifest naming `/nope/not-a-tree` passed the door (`OK  harness.json valid`)
and ran to `[exit 0]` with `0.000`, because nothing in that harness needed the tree. §6.4 is
what that costs when something does.)

### 1.2 Which fields the loader actually requires

Checked against the code, not assumed. `tools/validate_harness.py:29` is the door
(`REQUIRED`), and `harnessgrad/identity.py:_identity` indexes `man["name"]` and
`man["version"]` directly — so those two are not merely "recommended":

| field | required? | default | who reads it |
| --- | --- | --- | --- |
| `name` | **yes**, non-empty string | — | the door; `identity.py` (on every curve point); `driver.py` names the run's harness by it |
| `version` | **yes**, non-empty string | — | the door; `identity.py` → `harness_version` on every point |
| `entrypoint` | **yes**, non-empty string | — | the door (must exist as a regular file); `eval/run/runner.py` builds `argv` from it |
| `path` | no | `"."` | where `entrypoint` is resolved from; must be a relative directory |
| `backend` | no | `"cli"` | the door refuses anything else (`backend-unsupported`): `cli` is the only one implemented |
| `env_kinds` | no | `["files"]` | `harnessgrad/environments.py:_check_env_kinds` — a run whose dataset needs a kind you did not declare is **refused** |
| `runtime_paths` | no | `[]` | `eval/run/runner.py`: host trees bound read-only into the sandbox |
| `role` | no | `"harness"` | the door; must be `harness` or `control` |
| `notes` | no | — | nothing. Documentation for the next reader (`DOCUMENTATION_ONLY` in the door) |
| `components` | no | — | `harnessgrad/components.py:declared_components` → `editable_surface_components` on a point after round 0 (§5) |
| `runtime` | no | — | `eval/ckpt/source_tree.py:runtime_spec` — the Bun workspace runtime, not needed by a Python or shell harness |
| `install` | no | `null` | `tools/import_env.py`; **only** for a harness that runs in a container (`env_kinds: ["exec"]`) |

So the smallest manifest that loads is three fields — and dropping `version` is a refusal, not
a warning. Measured, `/tmp/byo-min` being `examples/bash-harness` with its manifest replaced by
the lines below:

```bash
printf '{"name":"minimal","version":"0.0.1","entrypoint":"run.py"}\n' > /tmp/byo-min/harness.json
python3 tools/validate_harness.py --smoke /tmp/byo-min
printf '{"name":"minimal","entrypoint":"run.py"}\n' > /tmp/byo-min/harness.json
python3 tools/validate_harness.py /tmp/byo-min
```

```
validating /tmp/byo-min
  OK    harness.json valid, smoke run produced an answer
validating /tmp/byo-min
  FAIL  harness.json field 'version' must be a non-empty string
[exit 1]
```

A key the platform does not read is named once and otherwise ignored, which is how `notes`
stays harmless:

```bash
printf '{"name":"minimal","version":"0.0.1","entrypoint":"run.py","notes":"documentation only","foo":1}\n' > /tmp/byo-min/harness.json
python3 tools/validate_harness.py /tmp/byo-min
```

```
validating /tmp/byo-min
  note: extra manifest keys ignored by the platform: ['foo']
  OK    harness.json valid
[exit 0]
```

### 1.3 What the platform runs, and what you leave behind

```
<entrypoint> --task <task.json> --workdir <dir>
```

`task.json` carries `task_id`, `goal` and (sometimes) `inputs`; `goal` is the contract,
`task_id` is opaque and must not be parsed. You write `<workdir>/answer.txt`, and you may write
`<workdir>/trace.jsonl`. Both, by hand, on the shipped example:

```bash
printf '{"task_id":"demo","goal":"Answer with the word alpha."}\n' > /tmp/byo-task.json
python3 examples/bash-harness/run.py --task /tmp/byo-task.json --workdir /tmp/byo-out
cat /tmp/byo-out/answer.txt
cat /tmp/byo-out/trace.jsonl
```

```
BASH_HARNESS_OK
{"harness_identity":{"harness":"bash-example","agent_model":null,"agent_backend":"scripted"}}
{"usage":{"calls":0,"input_tokens":null,"output_tokens":null}}
{"submitted":true,"answer_source":"artifact"}
```

Exit code `0` on completion is part of the contract, and a non-zero exit is recorded as a
harness failure (that task scores zero; the run continues). That trace is the v1 dialect of
[`docs/adapter-spec.md`](adapter-spec.md) §4 in its entirety: identity, usage, terminal record.
A harness with no model is legal — this one has `calls: 0` and says so.

### 1.4 If your entrypoint is not Python

`docs/writing_a_harness.md` §4 says a shell script is acceptable, and it is — but not
directly. `eval/run/runner.py` builds `argv = [python, str(repo / entry), …]` for a `files`
task, so a `#!/usr/bin/env bash` entrypoint is handed to the Python interpreter. Measured on
this repository, with the manifest naming `run.sh` (that is what `examples/bash-harness`
shipped until this page was written):

```
$ python3 tools/validate_harness.py --smoke /tmp/byo-shell
validating /tmp/byo-shell
  FAIL  smoke run exited 1; stdout tail:

stderr tail:
  File "/tmp/byo-shell/run.sh", line 8
    *) echo "unknown argument: $1" >&2; exit 2 ;;
     ^
SyntaxError: unmatched ')'
  FAIL  smoke run did not write answer.txt (required: F3); ...
[exit 1]
```

(the second `FAIL` prints the same `stderr` tail again; the `...` is that repetition, and
`/tmp/byo-shell` is the example with the manifest put back to `"entrypoint": "run.sh"`.)

and the same thing through the driver, where it is **not** an exit code but four failed tasks.
Trimmed to one line, and the line is this long in the terminal:

```
  ! t01: the harness failed (exit code 1) -- the harness left no trace at all. The score stands, but it is the platform's verdict on the artifact, not evidence about the harness. stderr: File "/tmp/harnessgrad_work/byo-shell2/workspace/run.sh", line 8 *) echo "unknown argument: $1" >&2; exit 2 ;; ^ SyntaxError: unmatched ')'
[exit 0]
```

The fix is a collar, not a rewrite: `examples/bash-harness/run.py` is a shim whose whole body is
`subprocess.call([bash, run.sh, *sys.argv[1:]])`, and `run.sh` is still the whole harness
(`bash run.sh --task … --workdir …` works by hand, as `console/uat/harness-upload-5min.md`
does it). A non-Python entrypoint needs that collar today.

---

## 2. Validate before you run

The tool has two flags; this is its whole interface:

```
$ python3 tools/validate_harness.py --help
usage: validate_harness.py [-h] [--smoke] harness

positional arguments:
  harness

options:
  -h, --help  show this help message and exit
  --smoke     also run the harness on a trivial task
[exit 0]
```

Static checks only (manifest fields, path/entrypoint/install existence, `role`,
`env_kinds` shape, that every `.py` file compiles, that no in-tree link escapes):

```
$ python3 tools/validate_harness.py examples/bash-harness
validating examples/bash-harness
  OK    harness.json valid
[exit 0]
```

`--smoke` runs it, on a two-field task (`"Write the word ok into answer.txt."`) in a throwaway
directory, and checks the answer file exists:

```
$ HG_AGENT_BACKEND=mock python3 tools/validate_harness.py --smoke examples/bash-harness
validating examples/bash-harness
  OK    harness.json valid, smoke run produced an answer
[exit 0]
```

Three things to know about what that just did, because they decide what a pass means:

* it runs `<your current python> <entrypoint>` (`sys.executable`), with the smoke directory as
  cwd and **your** environment inherited — so export `HG_AGENT_BACKEND=mock` before it, or a
  harness that reads the backend variable will try to reach a model;
* it is the *contract*, not the quality: one trivial task, one answer file, 120 s;
* the platform's submission gate uses the sandboxed variant of the same smoke
  (`validate_harness.smoke`), which sets `HG_AGENT_BACKEND=mock`, `PATH=/usr/bin:/bin` and
  `HOME=/tmp` and runs in a namespace. Passing the plain `--smoke` and failing there usually
  means you depend on something outside `/usr` — §6.4.

`examples/python-react-harness` is the other reference, and the same command passes it:

```
$ python3 tools/validate_harness.py --smoke examples/python-react-harness
validating examples/python-react-harness
  OK    harness.json valid, smoke run produced an answer
[exit 0]
```

---

## 3. Run it without touching the platform's base harnesses

`--harness-path` takes an **absolute** directory and is the whole external-harness path: the
tree is hashed (`submission_sha256`), staged into a workspace outside the platform tree, and
measured exactly like one of the platform's own. `$PWD/examples/bash-harness` is the shipped
example; substitute your directory.

```
$ export TMPDIR=/tmp HG_AGENT_BACKEND=mock
$ python3 driver.py --dataset demo --harness-path $PWD/examples/bash-harness \
    --sampling all --side train --rounds 0 --trials 1 --jobs 1 --run-id byo-smoke
  ✗ check · round 0 · t01 — answer 'BASH_HARNESS_OK' vs 'alpha'
  ✗ check · round 0 · t02 — answer 'BASH_HARNESS_OK' vs 'beta'
  ✗ check · round 0 · t03 — answer 'BASH_HARNESS_OK' vs 'gamma'
  ✗ check · round 0 · t04 — answer 'BASH_HARNESS_OK' vs 'delta'
mode A needs --method-entrypoint pointing at an external method
  ! no improver resolved (the 'deepseek' improver needs DEEPSEEK_API_KEY); a method that carries its own improver still runs, but this run carries no improver identity
byo-smoke

  harness   bash-example 0.1.0
  mode      unified · method drives its own loop
  data      demo · 4 tasks on the train side · train 4 / eval 4
  model     (none) · mock   ← not a live model
  sandbox   bwrap active — the harness cannot see this directory · capped at 4096M, 2 cpus, 1024 tasks

  ✓ round 0 · 第 1/4 题 t01 — done (0s)
  ✓ round 0 · 第 2/4 题 t02 — done (0s)
  ✓ round 0 · 第 3/4 题 t03 — done (0s)
  ✓ round 0 · 第 4/4 题 t04 — done (0s)
  ! this run scores 4 tasks — the score moves in steps of 0.250
  round 0  H0 (base harness)           train 0.000
  results
    round    train  95% CI           by         harness
        0    0.000  [0.00, 0.00]     platform   06820acf43f3

  curve     runs/byo-smoke/curve.jsonl
  summary   runs/byo-smoke/end-summary.md
  workspace /tmp/harnessgrad_work/byo-smoke/workspace
  events    runs/byo-smoke/events.jsonl
[exit 0]
```

**`0.000` is the correct number here.** The example answers `BASH_HARNESS_OK` to everything and
`demo/t01` expects `alpha`; the four `✗ check` lines above the summary are the platform saying
so, task by task. What was just verified is the plumbing — the harness started, answered, was
scored — and that is what this step is for. To see the same harness score instead of fail, set
the example's own knob and measure one task (trimmed: the banner, the long warning lines and
the `curve`/`summary`/`workspace` trailer, which are identical to the block above):

```
$ HG_AGENT_BACKEND=mock HG_BASH_EXAMPLE_ANSWER=alpha python3 driver.py --dataset demo \
    --harness-path $PWD/examples/bash-harness --sampling all --side train --rounds 0 \
    --trials 1 --jobs 1 --run-id byo-alpha --tasks t01
...
  ✓ round 0 · 第 1/1 题 t01 — done (0s)
  ! this run scores 1 tasks — the score moves in steps of 1.000
  round 0  H0 (base harness)           train 1.000
  · check · round 0 · t01 — answer 'alpha' vs 'alpha'
  results
    round    train  95% CI           by         harness
        0    1.000  n/a              platform   06820acf43f3
[exit 0]
```

`--tasks t01` narrows the round; `--sampling all` had already selected the side without
looking at a score. Two points now exist for one directory, `0.000` and `1.000`, and both are
honest: the score follows the artifact.

**Your directory is read, not written.** The run stages a copy, and the identity it recorded is
the identity of the tree that is still on disk:

```bash
python3 -c "
import json, sys
sys.path.insert(0, '.')
from tools.validate_harness import source_identity
print('now    ', source_identity('examples/bash-harness')['submission_sha256'])
print('as run ', json.load(open('runs/byo-smoke/run_meta.json'))['harness_source']['submission_sha256'])
"
```

```
now     bac9991d619ef39814b73774201ee86e3bb8765d11e416c4afa66d44ad0f59d0
as run  bac9991d619ef39814b73774201ee86e3bb8765d11e416c4afa66d44ad0f59d0
[exit 0]
```

Paths are outside the platform tree on purpose: the workspace is
`/tmp/harnessgrad_work/<run-id>/workspace`, which is why the harness cannot read `driver.py`
or `.env` from it.

`--run-id` is not a lock. Running the same id twice **overwrites** `runs/<run-id>/` — measured:
a second identical invocation exited `0` and left a fresh point with a new `timestamp_utc`, and
nothing warned. Use a new id per run; an old run directory is evidence, and this page's own
runs are only scratch.

### 3.1 What `--harness-path` refuses, and what it does not

Each of these was run; the exit code is `2` and **no curve is written**. Four of the five leave
no run directory at all; the `env_kinds` refusal happens inside the run, so it leaves
`runs/<run-id>/` containing `events.jsonl` and nothing else.

| you did | it prints |
| --- | --- |
| a relative path | `--harness-path must be an absolute directory` |
| `entrypoint` names a file that is not there | `refusing --harness-path: entrypoint 'agent.py' could not be read: [Errno 2] No such file or directory: '/tmp/byo-noentry/agent.py'` |
| `env_kinds: ["exec"]` on a `files` dataset | `✗ refusing to run: this dataset needs the environment kind(s) ['files'], and harness 'bash-example' declares only ['exec']. Running it anyway would score zero on every task for a reason that has nothing to do with the harness.` |
| `--side eval --rounds 1` | `direct --harness-path eval requires --rounds 0` |
| `--side eval --sampling below_0.5` | `direct --harness-path eval requires --sampling all` |

The fourth and fifth are the shape of a direct external evaluation: it is one frozen state on
the eval side, so there is nothing to improve over and no score to select on. Improvement
rounds are the train side's business (§5).

**A `components` map it does not like is not refused.** The door does not read `components` at
all, and the run that has one does not fail either: the point records the complaint beside the
touched paths (`declaration_error`, §5.3). The table above is the complete set — if your map is
wrong you will find out from the curve, not from an exit code.

### 3.2 `--no-sandbox`

The run above used the sandbox (`bwrap active`). On a host where `./install.sh --check` reports
the sandbox broken, the same command with `--no-sandbox` runs and measures the same thing. Only
two lines differ, so only those two are quoted:

```
  sandbox   OFF — the harness can read the driver and .env; only the hash check stands between it and the scorer
        0    0.000  [0.00, 0.00]     platform   06820acf43f3
[exit 0]
```

Same `harness_sha`, same score — and the two points are **not comparable**, which is why
`README.md` calls the flag a debugging decision and `tools/plot_curve.py` refuses to mix them.
Everything in this page after §3 was run with the sandbox on unless it says otherwise.

---

## 4. Read the result

`runs/<run-id>/curve.jsonl` is one JSON object per round, and the fields a newcomer needs are
six. `n_scored`/`per_task` are what the platform measured, `identity` is what it measured, and
`sampling` is on which tasks:

```bash
python3 - <<'PY'
import json
point = [json.loads(line) for line in open('runs/byo-smoke/curve.jsonl')][-1]
ident = point['identity']
print({k: point[k] for k in ('round', 'score', 'n_scored', 'n_passed',
                             'n_harness_failed', 'per_task')})
print({k: ident[k] for k in ('harness_name', 'harness_version',
                             'harness_sha', 'agent_model', 'agent_backend')})
print(point['sampling'])
PY
```

```
{'round': 0, 'score': 0.0, 'n_scored': 4, 'n_passed': 0, 'n_harness_failed': 0, 'per_task': {'t01': 0.0, 't02': 0.0, 't03': 0.0, 't04': 0.0}}
{'harness_name': 'bash-example', 'harness_version': '0.1.0', 'harness_sha': '06820acf43f3fc97034bbb087186ed7e2024a7ea', 'agent_model': None, 'agent_backend': 'scripted'}
{'dataset': 'demo', 'policy': 'all', 'task_ids': ['t01', 't02', 't03', 't04'], 'selection_basis_round': 0, 'selected_within': 'train'}
[exit 0]
```

`run_summary.py` writes (and prints) the one page per run, including what the run cannot say:

```
$ python3 tools/run_summary.py runs/byo-improve
unchanged runs/byo-improve/end-summary.md (byte-identical to the file on disk)
unchanged runs/byo-improve/end-summary.json (byte-identical to the file on disk)
  byo-improve                                  rounds=2   0.000 → 0.000            → runs/byo-improve/end-summary.md
[exit 0]
```

`unchanged` is not a failure: `driver.py` already wrote the page, and the tool reports that the
bytes on disk already match rather than overwriting them silently.

**A kept trace is checked against the adapter contract, not against your hopes.** Each task's
trace is kept at `runs/<run-id>/round0_traces/<task_id>.jsonl` (and per round after it):

```
$ python3 tools/check_trace_contract.py runs/byo-improve/round0_traces/t01.jsonl
OK    runs/byo-improve/round0_traces/t01.jsonl  [v1]
[exit 0]
```

The directory form is what its docstring advertises and what does **not** work on a run
directory today, because the checker looks for files literally named `trace.jsonl` and the
platform keeps them as `<task_id>.jsonl`:

```
$ python3 tools/check_trace_contract.py runs/byo-improve
no trace.jsonl under runs/byo-improve
[exit 2]
```

Pass the file. Two failures are worth recognising on sight, both measured on a copy of that
same trace with its `usage` line deleted:

```
$ python3 tools/check_trace_contract.py /tmp/byo-notrace.jsonl
FAIL  /tmp/byo-notrace.jsonl  [v1]
      - no `usage` record: the platform cannot tell a crash from a run that finished without reporting
[exit 1]
```

and what the *method* then reads, from the one extractor every harness goes through:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '.')
from eval.observe.tracing import loop_facts
for name in ('runs/byo-smoke/round0_traces/t01.jsonl', '/tmp/byo-notrace.jsonl'):
    f = loop_facts(open(name).read())
    print(name, '->', json.dumps({k: f[k] for k in ('ended','model_calls','commands','tokens')}))
"
```

```
runs/byo-smoke/round0_traces/t01.jsonl -> {"ended": "unknown", "model_calls": 0, "commands": 0, "tokens": 0}
/tmp/byo-notrace.jsonl -> {"ended": "crashed", "model_calls": 0, "commands": 0, "tokens": 0}
[exit 0]
```

`ended: crashed` is `eval/observe/tracing.py:_ended`: `usage` is the harness's **last** record,
so a missing one means the run never reached its own end. That is §6.2.

### 4.1 Three ways to read `0.000`

The number is the same in all three and only one of them is a measurement of your harness:

| what you see | what it means |
| --- | --- |
| `score: 0.0`, `n_passed: 0`, `n_harness_failed: 0`, and a `detail` on the verdict like `answer 'BASH_HARNESS_OK' vs 'alpha'` | the harness ran and answered wrongly. A measurement |
| `score: 0.0`, `n_harness_failed: n`, `harness_failed: {t01: {reason: …, exit_code: 1, stderr: …}}` | the harness did not start or did not finish. Not a measurement — read `stderr` |
| `unmeasured: {reason: …, problems: […]}` on the point | the candidate tree is not a harness the platform can measure at all |

The per-task file behind the first row is `runs/<run-id>/verdicts/round-0/<task_id>.json`:
`passed`, `detail`, and the harness's `exit_code`/`stderr` together. The second row's
`harness_failed` block is on the point itself, with the stderr tail that says *why* — that is
where the `SyntaxError` in §1.4 appeared.

---

## 5. Make it improvable

### 5.1 Everything is editable except the identity

`name` and `version` are identity: a method may not change them, or it can submit work under
another harness's name (`INTERFACE.md` §1.1.1). Everything else in the directory — the
entrypoint included — is the method's to edit, and that is deliberate: the ability to rewrite
its own improvement mechanism is the thing being measured, so there is no allowlist
(`docs/adapter-spec.md` §3).

### 5.2 The `components` map buys attribution, not permission

Optional, and worth adding. It is a map from the ten components
(`harnessgrad/components.py:COMPONENTS`) to the files that implement them, and after round 0
the point carries which paths the round moved and which component they belong to
(`editable_surface_touched` / `editable_surface_components`). Without it every path is
`unattributed` — a true statement, and a useless figure.

The reference method is `methods/echo_base`, and one round against **your** harness is one
command. Trimmed below to the score-bearing lines: the banner, the four `done` lines and the
trailer are the ones §3 already printed.

```
$ HG_AGENT_BACKEND=mock python3 driver.py --dataset demo --harness-path $PWD/examples/bash-harness \
    --sampling all --side train --rounds 1 --trials 1 --jobs 1 --run-id byo-improve \
    --method-entrypoint "python $PWD/methods/echo_base/run.py"
...
  round 0  H0 (base harness)           train 0.000
  round 1  one improvement step        train 0.000   · no change reported
  results
    round    train  95% CI           by         harness
        0    0.000  [0.00, 0.00]     platform   06820acf43f3
        1    0.000  [0.00, 0.00]     platform   06820acf43f3
[exit 0]
```

`no change reported`, and the same `harness_sha` twice, is the honest answer: `echo_base`'s one
edit is a string replacement written against `base_harness/loop`'s `agent.py`, and it does not
match the example's `agent.py` (there isn't one). The reference method is a template, not a
service — a real method replaces that file.

The attribution mechanism itself, on the harness its edit *does* match (trimmed the same way):

```
$ HG_AGENT_BACKEND=mock python3 driver.py --dataset demo --sampling all --harness loop \
    --rounds 1 --trials 1 --jobs 1 --run-id byo-loop \
    --method-entrypoint "python $PWD/methods/echo_base/run.py"
...
  round 0  H0 (base harness)           train 0.000
  round 1  one improvement step        train 1.000
  results
    round    train  95% CI           by         harness
        0    0.000  [0.00, 0.00]     platform   9c5ba821c310
        1    1.000  [1.00, 1.00]     platform   93f34aa96a6e
[exit 0]
```

`--harness loop` is the platform's own base harness, copied into the workspace — the original
is not written to. Two rounds, and the two points differ in exactly the field that matters:

```bash
python3 - <<'PY'
import json
for run in ('byo-improve', 'byo-loop'):
    for point in map(json.loads, open(f'runs/{run}/curve.jsonl')):
        print(run, point['round'], point['score'], point['identity']['harness_sha'][:12],
              point.get('editable_surface_touched'),
              point.get('editable_surface_components'))
PY
```

```
byo-improve 0 0.0 06820acf43f3 None None
byo-improve 1 0.0 06820acf43f3 [] {'components': [], 'unattributed': []}
byo-loop 0 0.0 9c5ba821c310 None None
byo-loop 1 1.0 93f34aa96a6e ['agent.py'] {'components': ['control'], 'unattributed': []}
```

Read the two trailing fields. A round that moved nothing records `[]` on both sides, and the
`unattributed` half is what keeps "the edit was in `control`" distinguishable from "the edit was
somewhere the map cannot name" — a path the map does not mention lands there instead of
disappearing, because a dropped path is a round that reads as "nothing changed"
(`harnessgrad/components.py:attribute`). A round that moved one file names it and the component
the harness declared for it. Both round-0 rows are `None`: the baseline has nothing to diff
against.

### 5.3 A malformed map is recorded, not refused

`components` is a map for reading, so a wrong one cannot fail a measurement. Measured with
`"components": {"prompts": ["run.sh"]}` on the example and one method round:

```
$ python3 -c "import json; p=[json.loads(l) for l in open('runs/byo-badcomp-m/curve.jsonl')][-1]; print(p['editable_surface_components'])"
{'components': [], 'unattributed': [], 'declaration_error': "`prompts` is not a harness component. The vocabulary is closed (context, tools, control, memory, verification, recovery, delegation, reliability, cost, safety); 'unattributed' is the only other legal key, for a shipped file no component owns. Add the component here first if the harness really needs a new one."}
[exit 0]
```

The run exits `0`, the score is measured, and the reason the attribution is empty is on the
point. That is the division of labour: the door refuses a tree it cannot identify (§3.1), and a
map that is merely wrong is recorded instead.

---

## 6. The five mistakes that cost the most time

Each is sourced — in this repository's own record, not in folklore — and each is one line to
fix.

### 6.1 `answer.txt` is the task's channel, not your output stream

**Symptom.** A task whose goal says *write the answer to `answer.txt`* scores zero although the
agent did the work, and the file holds the model's narration; or the reverse, and the file the
agent wrote is gone when the verifier looks. **Fix.** Keep the file the agent wrote, record
`answer_source: artifact`, and use the final message only when there is no file.
**Source.** `docs/adapter-integration-journal.md`, “Fixed the same day: the codex adapter was
overwriting the task's own answer”: `-o <workdir>/answer.txt`, preceded by
`answer.txt.unlink()`, so the CLI's final message **replaced** whatever the task asked the agent
to write; `deepseek-harness` had the same defect, and `loop` always overwrote the file. The
three-source rule is `docs/adapter-spec.md` §5, pinned by
`tests/quality/test_answer_delivery.py`.

### 6.2 A trace the extractor cannot read

**Symptom.** Your harness works, the score is real, and the point reads `ended: crashed`,
`model_calls: 0`, `commands: 0` — the method is told the harness did nothing. §4 printed both
sides of it. **Fix.** Write the five records of `docs/adapter-spec.md` §4 and run
`tools/check_trace_contract.py` on the file until it says `OK`. **Source.** §4's own measured
table: a `harness_identity` + `codex_event` + `usage` trace reports **0 steps, 0 commands** for
work that a `harness_identity` + `reply`/`command` + `usage` trace reports as 2 and 2; and
`eval/observe/tracing.py:_ended`, where an absent `usage` is `crashed` by construction.

### 6.3 `env_kinds` says `exec` while the dataset needs `files`

**Symptom.** `✗ refusing to run: this dataset needs the environment kind(s) ['files'], and
harness … declares only ['exec']`, exit `2`, no curve (§3.1). **Fix.**
Declare every kind your harness can actually operate on; the gate is a refusal rather than a
zero *because* the alternative is a harness that looks weak for a reason that has nothing to do
with it. **Source.** `harnessgrad/environments.py:_check_env_kinds` states that reason, and
`INTERFACE.md` §1.1 promises the refusal. Do not “fix” it by declaring both kinds unless the
container path really works — the declaration is a claim, and `eval/run/candidate.py` refuses a
candidate that widens it.

### 6.4 `runtime_paths` without the tree your harness needs

**Symptom.** `ModuleNotFoundError: No module named 'openai'` in the task's `stderr`, recorded as
an exit code — which reads like a weak harness rather than a missing mount. The declaration side
is silent too: an entry that does not exist on the host is skipped (`if target.exists()`), so a
typo costs nothing until a task needs the tree (§1.1 measured a wrong entry passing the door and
the run). **Fix.** Name the tree in `runtime_paths`, or declare `install` for a container
harness — and test under the sandbox, not only with `--smoke`. **Source.**
`docs/writing_a_harness.md` §2 records the measurement: the platform's own base harness scored
zero on every container task for `ModuleNotFoundError: No module named 'openai'`;
`docs/adapter-spec.md` §2 and `INTERFACE.md` §1.1.1 say the same thing about undeclared trees.
**Half verified on this page**: the silent-skip and the door's behaviour were measured, the
`exec`/container half was not — every run here was `files`.

### 6.5 A model call that does not go to `HG_AGENT_BASE_URL`

**Symptom.** The point's `agent_model` is not the model that ran, `calls` is `0` in a run that
plainly called a model, and the call was never counted or priced — the number is about a
different experiment. **Fix.** Route every call through the six `HG_AGENT_*` variables, isolate
`HOME`, and if the model did not come from `HG_AGENT_MODEL`, write a `harness_identity` record
saying what did run. **Source.** `docs/adapter-spec.md` §1 rules 1 and 3;
`docs/adapter-integration-journal.md`, where a `deepseek-harness` smoke with the real `HOME`
“answered through a **real DeepSeek endpoint** with the local key … and produced a trace with 1
call and 0 commands”, and the platform now refuses rather than recording a model it did not
use; and `harnessgrad/identity.py`, which records that its own `agent_backend` line once said
`mock` while a live model answered every task.

### Not a mistake, but the sixth thing that bites: the entrypoint language

See §1.4. A shell entrypoint is documented as acceptable and is invoked as Python; the collar
in `examples/bash-harness/run.py` is what makes it measurable today.

---

## What this page did not verify

* **A live model.** Every run here used `HG_AGENT_BACKEND=mock`. The endpoint variables,
  `HG_AGENT_DEADLINE_S` and the token accounting in `usage` were not exercised: no paid API and
  no GPU endpoint was used.
* **The container path.** `env_kinds: ["exec"]`, `install`, `runtime_paths` inside an image and
  `tools/import_env.py` are read from the code and the documents, not run here. §6.3/§6.4 are
  therefore sourced, not measured — with the exception §6.4 names.
* **The submission gate's own smoke.** `validate_harness.smoke` (the sandboxed variant used by
  `contrib/`) was read, not run; only the plain `--smoke` was.
* **The sandbox on another host.** `bwrap active` and `--no-sandbox` were both measured here
  (§3.2); that a reader's machine can build the namespace is `./install.sh --check`'s question,
  and this page does not answer it.
* **A real edit to an external harness.** §5.2's populated attribution came from
  `base_harness/loop`, whose `agent.py` the reference method's edit matches. The external
  harness's round recorded `[]`, which is the honest empty answer, not a demonstration that
  attribution works on a tree you wrote.
* **`tools/plot_curve.py` and `tools/results_matrix.py`.** Not run here; `README.md` §“Run it”
  shows them on runs that already exist.
