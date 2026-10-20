---
title: Getting started
description: Install HarnessGrad and run the local demo end to end, with no network and no cost.
---

## Requirements

`bubblewrap`, `git` and `python3 >= 3.10`. The platform itself has **no third-party
Python packages** — everything it uses is in the standard library.

```bash
./install.sh --check      # verify only; change nothing (also useful in CI)
./install.sh              # optionally install missing dependencies, then verify
```

The script does not stop at "bwrap is installed", because that is not the same as the
sandbox working: a kernel with unprivileged user namespaces disabled, or an AppArmor
policy restricting them (the default on newer Ubuntu), leaves `bwrap` present and failing
at runtime. So it finishes by asking the platform to build a real namespace and look for
itself inside it, and refuses to call the machine ready if that fails — rather than
letting a run fall back to `--no-sandbox` and produce numbers that mean something other
than what they say.

Ordinary local use needs no `sudo`, no dedicated storage volume and no root Docker
access. Quota and evidence backends are optional infrastructure for stronger formal
claims; ordinary runs cannot claim those guarantees.

## Run the local demo

Zero network, zero cost — the loop, the curve points, the checkpoints and the cost
accounting exercised end to end.

```bash
export HG_AGENT_BACKEND=mock
M="python $PWD/methods/echo_base/run.py"

python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id control \
    --method-entrypoint "python $PWD/methods/noop/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id treat-A \
    --method-entrypoint "$M"
python3 driver.py --dataset demo --sampling all --harness loop --mode B --rounds 2 --run-id treat-B \
    --method-entrypoint "$M"

python3 tools/plot_curve.py runs/control runs/treat-A runs/treat-B
```

```
run                            policy     conditions                         curve (one glyph per round)
----------------------------------------------------------------------------------------------------
control                        all        sandboxed v4 / files / training / default protocol ▁▁  0.00..0.00
                                                                             rounds=2 tasks=4 trials=8
treat-A                        all        sandboxed v4 / files / training / default protocol ▁██  0.00..1.00
                                                                             rounds=3 tasks=4 trials=12
treat-B                        all        sandboxed v4 / files / training / default protocol ▁██  0.00..1.00
                                                                             rounds=3 tasks=4 trials=12
```

The point is not the scores. `--sampling all` explicitly selects the ordinary demo path;
the CLI default is staged `diagnostic`, which requires `--policy-evidence`, one task, one
trial, one job and zero improvement rounds. **The demo grants no production/H0
authorization** and is not evidence that runtime, quota, snapshot or cleanup gates have
passed.

## Step 0: measure one shared H0

A round 0 baseline is measured **inside** every run, so a batch that starts N runs gets N
baselines. Those are not interchangeable: the cross-method comparison is a *paired* one, and
"method A beat method B" computed against two different baselines is a statement about two
different questions. Measured 2026-10-19: three AppWorld study runs were launched without a
shared baseline and each re-measured H0.

`tools/prepare_h0.py` is the missing step 0. It measures the baseline once, verifies it (whole
study set, no invalid task, a recorded `h0_sha`) and prints the `--h0-from` to pass to every run
that follows:

```bash
python3 tools/prepare_h0.py --dataset appworld --side train --study-ids ids.txt --jobs 32
# -> h0 run id, score, task count, and the exact --h0-from <id>
python3 driver.py ... --h0-from <that id>
```

It **reuses** an existing baseline whose configuration matches instead of measuring a second one.
The configuration is `(dataset, side, study-id set, agent model, endpoint)`: a different model or
a different study set is a different H0, and the tool measures one rather than pretending they
are the same. A baseline with holes is not published, and a typo'd `--study-ids` path is an error
instead of "no study set".

## Test the part you changed

The full suite is large, so run the tests for the dataset you are editing:

```bash
python3 tools/run_tests.py --dataset gsm8k
```

It runs that dataset's tests plus the core platform tests, and skips every other
dataset's. If you pass `--basetemp`, it must be **outside the platform tree** — the
runner refuses a harness work root inside it, and a suite writes thousands of small files.

That covers a change that belongs to one dataset. For a change to the platform itself
(`eval/`, `harnessgrad/`, `tools/`) use the lane, which selects the tests that mention what you
touched and prints one reason per selection:

```bash
python3 tools/test_lane.py                 # what this branch changed
python3 tools/test_lane.py --list          # the selection, nothing run
python3 tools/test_lane.py --full          # the whole suite: the gate for a merge
```

A changed file that no test mentions is printed as **unmatched** rather than dropped — that is
usually the most useful line the lane produces.


```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
    -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-quality tests/quality
```

## Next

- [Status and limits](/HarnessGrad/status/) — read this before quoting any number.
- [`docs/writing_a_harness.md`](https://github.com/zjunlp/HarnessGrad/blob/main/docs/writing_a_harness.md)
  — the harness contribution contract.
- [`docs/architecture.md`](https://github.com/zjunlp/HarnessGrad/blob/main/docs/architecture.md)
  — the repository map.
