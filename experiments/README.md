# Experiments

A file in this directory is a checked-in command line. `driver.py`'s real invocation is long
— dataset, harness, side, sampling, rounds, jobs, trials, run id, method entrypoint — and a
command copied from an older run still starts while measuring something else (a renamed
method directory, a stale sampling policy). So the experiment is a small JSON object of
driver flags, reviewed and diffed with the code. `driver.py --config <file>` expands it into
flags before parsing; the spellings are `harnessgrad/experiments.py:_FLAGS` and a test
re-reads `driver.py`'s argparse block, so a key with no flag is refused by name.

## Start offline, then choose a research workload

* **`quickstart.json`** uses the eight-task repository `demo`, the reference
  `echo_base` scripted edit, `loop`, two maximum improvement rounds, one job and
  one trial. No dataset download or model is needed with `HG_AGENT_BACKEND=mock`.
  It is a plumbing control and is never a benchmark result.
* **`eval-from-round.json`** measures round 2 of `quickstart` on demo's held-out
  side. Run `quickstart.json` first, or override `from_run` with the actual study
  run ID. It edits nothing on the exam side.
* **`rsi-bbeh.json`** / **`rsi-bbeh-eval.json`** are the first real method recipe:
  BBEH × DGM, five study tasks per round and five tasks in fixed-checkpoint eval.
  They need pinned BBEH data, a frozen split, a live solver and an editor endpoint.
* **`rsi-terminal-bench2*`**, **`rsi-deepsearchqa*`** and
  **`rsi-swe-bench-pro-v2*`** cover terminal, research and code-repair agents.
  They have additional service/image/runtime prerequisites. Read the exact
  [preparation recipes](../docs/rsi-experiments.md) before launching.

Recipe files set `fail_on_incomplete: true`: method errors or incomplete task
measurements exit nonzero. A legitimate method stop remains successful and is
reported as `early_stop`. The summary distinguishes completion from the last
valid H0 score. Ordinary runs retain their legacy exit behavior when the flag
is absent. Use fresh run IDs when repeating a recipe.

## Change one leaf, do not copy the file

```bash
python3 driver.py --config experiments/method-ahe.json --set rounds=3 --set run_id=ahe-3r
python3 driver.py --config experiments/method-ahe.json --set dataset=alfworld
```

`--set a.b=c` deep-merges into the file and the patch wins; typed values (`3`, `true`,
`null`) are recognized and anything else stays a string. An explicit flag after `--config`
also wins, so `--config experiments/method-ahe.json --trials 2` is the same experiment with
two trials. A string value may spell `$VAR`: it is expanded from the environment at launch
time, so a credential stays out of the file and a missing variable fails by name instead of
starting on an empty value.

## Check before spending

```bash
python3 driver.py --config experiments/method-ahe.json --check-only    # creates nothing, calls no model
python3 driver.py --config experiments/method-ahe.json --prepare-only  # writes run_meta.json, launches nothing
```

`--check-only` prints one row per prerequisite and exits 0 only when every required one is
ok (2 otherwise); `--show-config` prints the effective platform configuration. None of them
starts a task or a model call. Local checks validate known method/editor prerequisites before H0. Add
`--check-net` to probe the required live agent and editor channels with GET requests;
an explicitly requested necessary endpoint failure is fatal. These probes never
generate tokens or prove a model can execute an entire experiment.

## The method presets

`experiments/method-<name>.json` exists for every **published** method (`"kind":
"published"` in `methods/<name>/method.json`). Each one is `gsm8k`, train side, `random_5`,
`rounds: 1`, `jobs: 1`, seed 0, run id `<name>-quickstart`, `loop` as the harness and one
trial -- except where the method's own `method.json:requires` says otherwise (the ten
upstream ports below: six need a prompt-carrying harness, one needs two trials). These are
legacy small QA smoke configurations of the method's propose/measure loop, not a benchmark protocol: five
tasks and one trial cannot resolve a difference between two methods.

**Every one of them needs an improver, and none can complete a round without one.**
`HG_AGENT_BACKEND=mock` replaces the harness's *agent* model; the method's *editing* model
is a separate budget (`HG_METHOD_BASE_URL / HG_METHOD_API_KEY / HG_METHOD_MODEL`, see
`.env.example`). With no improver, round 0 (H0) completes, round 1 records

    entrypoint failed ... a method needs HG_METHOD_BASE_URL / HG_METHOD_API_KEY / HG_METHOD_MODEL

the curve stops at H0, and the legacy driver exit remains 0 without `--fail-on-incomplete`. Known method
prerequisites are now checked before H0, and strict runs distinguish failures from
a valid early stop. This is not a substitute for configuring a reachable editor. The cheapest honest configurations
are a CLI improver (`--improver codex` or `dsh`, which needs that tool logged in) or an
endpoint (`--improver local` against a local server, or `HG_METHOD_*` set directly).
`tests/quality/test_method_presets_run.py` skips exactly the endpoint-only files with that
reason and fails on every other cause.

| method | file | what it needs |
| --- | --- | --- |
| `ahe` | `method-ahe.json` | an improver |
| `dgm` | `method-dgm.json` | an improver |
| `dream_rsi` | `method-dream_rsi.json` | an improver |
| `harnessx` | `method-harnessx.json` | an improver |
| `hyperagents` | `method-hyperagents.json` | an improver |
| `meta_harness` | `method-meta_harness.json` | an improver; at `rounds > 1` also a method endpoint (its design stage is a schema call) |
| `rrsi` | `method-rrsi.json` | an endpoint (the leakage critic) **and** an improver (the edit) |
| `sica_ci` | `method-sica_ci.json` | an improver |
| `tthe` | `method-tthe.json` | an improver |

The ten upstream policy ports (`methods/policy_ports/`) declare what they need in
`method.json:requires`, and the file shape follows that declaration rather than one universal
value:

| method | file | harness | what it needs |
| --- | --- | --- | --- |
| `arbor`, `darwinx`, `evo_harness` | `method-<name>.json` | `loop` | an improver |
| `ace`, `adas`, `evoprompt`, `gepa`, `opro`, `skillopt` | `method-<name>.json` | `miniagent` | an improver **and** an instruction file: these six rewrite the harness's prompt/skill text, and `loop` ships no prompt, so the method refuses before any model call with "declare instruction_path in `HG_POLICY_CONFIG`". `miniagent` has exactly one of the four files the port looks for (`prompts/system.md`); any harness with one of `prompts/system.md`, `prompt.md`, `CLAUDE.md`, `AGENTS.md` works. |
| `modular_rsi` | `method-modular_rsi.json` | `loop` | an improver **and** a module map: `HG_POLICY_CONFIG='{"modules": {"core": ["agent.py"]}}'` (plus `--trials >= 2`). The map is a judgement about the harness being improved, so no preset can carry it; the method refuses with a named error instead of guessing one. |

The six MiniAgent recipes use its [managed CPython 3.12 environment](../base_harness/miniagent/README.md).
Prepare its complete lock with `python tools/configure_harness.py --harness base_harness/miniagent --fetch`
before the first run when wheels are not cached. Candidate preparation is offline;
MiniAgent requires sandboxed native `files` tasks and refuses `--no-sandbox`.

`HG_AGENT_BACKEND=mock` mocks the harness's agent model and nothing else, so **none of the
ten reaches round 1 model-free**: nine stop at H0 with the endpoint message above,
`modular_rsi` stops with the module-map error. `tests/quality/test_method_presets_run.py`
skips each of those by the message it records, and fails on any other cause.

*An improver* is the editing layer the run resolves (`--improver`; the registry default is
the `deepseek` endpoint, a CLI such as `dsh` is the other kind), or the method endpoint
supplied directly through `HG_METHOD_BASE_URL` / `HG_METHOD_MODEL` / `HG_METHOD_API_KEY`.
*An endpoint* is a chat endpoint required even when the improver is a CLI: `rrsi`'s critic
must return a JSON verdict, which a codex-style CLI cannot do.

### No preset

The other entrypoints under `methods/` are runnable but are not published ports, so they
get no `method-*.json`:

| method | kind | why no preset |
| --- | --- | --- |
| `a`, `b` | reference | the platform's two trajectory shapes, not ports of anything (need an improver) |
| `echo_base` | reference | contract self-check; needs nothing |
| `noop` | control | the negative control; needs nothing |
| `raise_step_cap` | control | the positive control; needs nothing |
| `lightrsi` | other | a wrapper layer that produces no candidate of its own; needs `--base` |
| `llm_improver` | improver | the reference diagnose-and-edit entrypoint, not a published port |

### Not runnable here

* **`lightrsi` cannot be a one-line preset.** `methods/lightrsi/run.py` requires
  `--base <method>` (`argparse` `required=True`), and an experiment file's `"method":
  `<name>"` renders `python .../methods/<name>/run.py` **with no extra arguments**. No
  portable preset shape can start it: run it by hand with
  `--method-entrypoint "python $PWD/methods/lightrsi/run.py --base llm_improver"`.
  `quickstart.json` therefore names the reference `echo_base`, which needs nothing; naming
  `lightrsi` there would ship an example that stops in round 1 with `the following
  arguments are required: --base`. `tests/quality/test_method_presets_run.py` pins both.
* **No published method is excluded for a missing artifact.** Each ships the helper modules
  its rule loads (`methods/dgm/archive.py`, `methods/dgm/staged_eval.py`,
  `methods/hyperagents/archive.py`, `methods/rrsi/fidelity.py`). The absent upstream pieces
  — DGM's SWE-bench gold patch, Dream-RSI's replay simulator, HarnessX's shipping validator —
  are declared deviations in the ports' `method.json`, not launch blockers.
* That leaves only the environment: the improver layer is not shipped as a model. The
  default `deepseek` endpoint comes from `.env`, and a CLI improver (`dsh`, `codex`) comes
  from the machine; `--improver none` is for a method that carries its own, such as `noop`.
  This is a deployment fact, not a missing artifact.

## Custom study/held-out partitions

The `split` field accepts `declared`, `official`, `default`, or a file created by
`harnessgrad split create`. For example, add `"split": "splits/math500.json"`
beside `"dataset": "math500"`, or pass `--set split=splits/math500.json`.
Both train and checkpoint-eval runs must reuse the same file. See
[the custom split guide](../docs/custom-dataset-splits.md) for a complete example.
