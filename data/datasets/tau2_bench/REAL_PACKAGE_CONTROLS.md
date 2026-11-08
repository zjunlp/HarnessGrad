# Offline controls using the actual Tau packages

`tests/quality/test_tau_real_package_controls.py` exercises author-created episodes;
it never loads released cases or trajectories. All HTTP binds to loopback and all
LLM responses are scripted synthetic substitutes. Passing these controls is not a
benchmark score or validation of a real model/provider.

Tau2 and Tau3 use their original adapter declarations, relay, CLI, bootstrap,
UserSimulator, retail `calculate` tool and `EvaluationType.ALL` scorer. Their
existing managed execution artifacts supply dependencies. The test's only fixture
substitutions are an authored private/public case and the selected physical
source-only bundle location. No upstream module or tool/scoring function is
replaced. `run_one(sandbox=True)` starts the real service in the managed Python
sandbox, runs the actor, seals its service-owned episode after shutdown, and runs
the original verifier with the separate local judge broker. Correct and missing
action cases yield official rewards 1 and 0 respectively; malformed judge output
and HTTP 503 remain unmeasured. Each completed episode makes two scripted user
requests and one scripted NL assertion judge request.

The standalone legacy Tau-Bench control runs its actual vendored
`modify_user_address`, original HTTP `Episode` handler and atomic recording, then
launches `verify/check.py` as an isolated subprocess. It checks DB-changing replay,
output substrings and termination: correct=1; wrong DB/output and unfinished=0;
invalid recording=unmeasured. Its observed model usage is zero. This is a real
component lifecycle control, **not** replacement validation of the legacy declared
container deployment; no Docker or system installation is used.

Choose existing source-only bundles and compatible managed stores explicitly:

```bash
export HG_TAU2_CONTROL_BUNDLE=/absolute/source-only-tau2-bundle
export HG_TAU2_CONTROL_STORE=/absolute/managed-execution-store
export HG_TAU3_CONTROL_BUNDLE=/absolute/source-only-tau3-bundle
export HG_TAU3_CONTROL_STORE=/absolute/managed-execution-store
pytest -q tests/quality/test_tau_real_package_controls.py --basetemp=/tmp/tau-author-controls
```

The suite skips Tau2/3 when their explicit fixture locations are absent. It never
installs dependencies or fetches source. The original bootstraps verify source
bundle pins; the execution manager revalidates the exact interpreter/dependency
artifact. Stores must match the checked-out execution descriptors and locks.
Existing prepared stores remain read-only. The parent process does not import
Tau2/3 dependencies.

For a platform interpreter containing only stdlib, run the same 13 controls
without installing pytest (from the checkout root):

```bash
/absolute/stdlib-platform/bin/python -I -B -c \
  "import sys,runpy;sys.path.insert(0,'.');runpy.run_path('tests/quality/test_tau_real_package_controls.py',run_name='__main__')"
```

The source bootstrap above adds only the trusted checkout, while services and
scorers use their selected external dependency artifacts. These controls cover
retail arithmetic and action/NL assertion evaluation in both versions. They do
not establish all-domain tool parity, real LLM quality, released-task correctness,
or legacy container enforcement.
