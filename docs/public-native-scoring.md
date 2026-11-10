# Public native scoring on a new machine

ALFWorld, BIRD Mini-Dev, ChessBench puzzles, Jericho and ScienceWorld now use the
`public-native-local-v1` scoring profile by default. New evaluations do not require
an archive of the original machine's interpreter, standard library, shared
libraries, packages or Bubblewrap binary.

The scoring code and native algorithm stay explicit:

| Dataset | Scoring logic / engine |
| --- | --- |
| `alfworld` | Replay the submitted actions in a fresh ALFWorld/TextWorld environment; use native `won`. |
| `bird_minidev` | Execute prediction and gold SQL with local Python's SQLite engine; retain the documented EX set-comparison rule. |
| `chessbench_puzzles` | Public ChessBench scoring source and the declared python-chess version; retain the existing strict replay policy and evidence. |
| `jericho` | Replay commands with Jericho's native interpreter and use its score/max-score. |
| `scienceworld` | Replay actions through the public ScienceWorld engine/JVM and use the final native score. |

The dependency versions are in each dataset's `execution-runtime.json` and named
requirements file. The current recipes support CPython 3.12; they do not require a
particular 3.12 patch release, historical executable hash or historical host libc.
ScienceWorld still needs its public, versioned JRE/JAR assets. Game data, ROMs,
databases, splits and benchmark assets remain separate preparation requirements.

## Prepare a local scoring environment

With an already installed, compatible Python interpreter, prepare only the dataset
you will use. This explicit command may download its declared public dependencies:

```bash
python tools/prepare_dataset.py --dataset alfworld --runtime execution \
  --python /absolute/path/to/python3.12 --runtime-root /external/dataset-runtimes
python tools/prepare_dataset.py --dataset alfworld --runtime execution \
  --runtime-root /external/dataset-runtimes --check
```

Replace `alfworld` with `bird_minidev`, `chessbench_puzzles`, `jericho` or
`scienceworld`. Set `HG_DATASET_RUNTIME_ROOT=/external/dataset-runtimes` for the run.
For offline preparation, use an existing wheel cache and `UV_OFFLINE=1`. BIRD's
execution requirements are empty because its engine is standard-library SQLite.
Provider loading dependencies, where declared, are prepared separately with the
same tool's default `--runtime provider` profile. Check does not launch a task or
prove benchmark quality; it verifies the prepared local environment.

The actor's interactive service and offline scorer use the selected dataset's
execution recipe. Scoring imports trusted code from a private read-only snapshot,
with isolated Python startup and a read-only task view. The actor cannot replace
the scoring code or inject task-local Python modules into scorer imports.

## What is checked and recorded

* The public scoring source and input contract identify what algorithm was run.
* The actual local interpreter, SQLite version, dependency versions and prepared
  runtime identity are recorded with the verdict.
* A freshly prepared runtime on another machine is admitted without comparing its
  interpreter or dependency bytes to a historical machine. A read-only local
  inventory still detects changes to the selected artifact during a run.
* Missing dependencies, startup failures or engine failures remain **unmeasured**;
  they are not silently converted into an agent's zero.
* Changed scoring source, input data or local runtime identity leads to a fresh
  measurement. A common public metric does not authorize silently reusing an old
  numerical score from a different environment.

## Historical archives

The old `data/runtimes/*-native.json` pins and their provenance remain available
for explicitly requested forensic reproduction. `tools/prepare_native_runtime.py`,
`tools/build_bird_native_runtime.py` and `tools/prepare_chessbench_native.py` are
historical archive tools, not prerequisites for current evaluation. They retain
their exact-byte semantics so old evidence is not relabelled as a new measurement.

Synthetic tests cover all five default declarations and public recipes. An authored
SQLite control runs the actual BIRD scorer in locally prepared environments,
checks positive/negative rewards and actor import isolation, and proves two distinct
local installations can run the same public scorer. This is not a released-benchmark
or real-model result; native engine controls require their public dependencies and
assets on the target machine.

The optional authored Chess controls use an already prepared public store:

```bash
HG_PUBLIC_NATIVE_TEST_ROOT=/external/dataset-runtimes python -m pytest -q \
  tests/quality/test_public_native_scorers.py -k public_chess
```

They run only a fabricated starting-position trajectory and make no model calls.
