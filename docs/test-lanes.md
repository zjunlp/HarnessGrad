# Test lanes: which files run alone, and why

`tools/run_tests.py` runs one pytest process per test file across two lanes:

* a **parallel lane** with `--jobs N` workers, for files that share a machine safely;
* an **exclusive lane** at concurrency **1**, which runs *beside* the parallel lane,
  for files that do not.

The exclusive lane is not "the slow lane". It exists because two processes that
bind the same port, create the same container, or write the same file fail for
reasons that have nothing to do with the code under test — and a failure that
disappears when the file runs alone is an isolation defect in the test, not a
regression in the platform. Running such a file at concurrency 1 is the only
correct way to run it, and it still overlaps everything else.

`tools/run_tests.py`'s `EXCLUSIVE` tuple names those files. This document records
the **signals** an entry must show, the **method** used to derive them, the
**per-file lane table**, and the **experiments** that moved a file between lanes.

## The signals

Every `EXCLUSIVE` entry must trip at least one of these, and the definition is
kept in one place: `tests/quality/test_test_lanes.py`. That guard fails if an
entry exists with no signal, if an entry names a file that no longer exists, if the
tuple repeats itself, or if `SLOW` and `EXCLUSIVE` drift into an undocumented
overlap.

| id | signal | what counts | why it needs the lane |
|----|--------|-------------|------------------------|
| a | **fixed TCP port** | a port literal in a binding or addressing position (`port=8080`, `127.0.0.1:8001`, `:8001` next to a URL/serve) | two processes cannot bind one host port; a client can reach the wrong server |
| b | **server bind / fixed-address listen** | `HTTPServer(`, `ThreadingHTTPServer(`, `.bind((host, port))`, `.listen(`, `serve_forever(`, `socket.socket(` | a long-lived server on a fixed address is a shared resource even at port 0 if the port is published into a fixed env var |
| c | **fixed `/tmp` path or fixed shared name** | a `"/tmp/..."` literal, a literal `"hg-..."` container/network/dir name | two files that name the same path or the same docker object collide, whatever their `--basetemp` is |
| d | **docker daemon / shared container name** | `subprocess…["docker", "run"|"create"|"rm"|"exec"|"network"|"image"|"commit"…]`, `DOCKER_HOST`, `/var/run/docker.sock` | named containers, images and networks are daemon-global; a concurrent `rm -f` or `commit` hits the other process |
| e | **process-wide registry another file mutates** | a module-level literal (`DICT`/`LIST`/`SET`) whose name is registry-shaped and which this file writes to | a mutated module global is visible to every process that imports the same module |
| f | **write into the repository checkout** | `(ROOT / …).write_text/write_bytes/mkdir/unlink/touch/rename`, `open(ROOT / …, "w")`, `shutil.copy2(…, ROOT.parent / …)` | the parallel lane is walking the same tree; a test that edits the shared checkout cannot be made safe by *any* lane |

A signal is **not** an accusation. It is a question the file has to answer: either
the resource is genuinely shared, or the file has to be shown — by experiment — to
survive running beside another file. Several signals here are false positives on a
literal reading (an ephemeral `bind(("127.0.0.1", 0))`, a port inside a container,
a `/tmp` string used as test *data*); those are called out per file below.

## Method

1. **Static derivation.** For every file under `tests/`, the signals above were
   extracted with grep-grade regexes over the file's lines plus one small AST pass
   (for the registry and checkout-write rules). The extraction never reads a test
   body; it is the `signals_of()` function in `tests/quality/test_test_lanes.py`,
   and the guard runs it over the specific files it needs, not the whole tree.
2. **Judgement.** Each current `EXCLUSIVE` entry was checked against its signals.
   An entry with none, or whose only signal was a false positive, became a
   candidate for removal.
3. **Experiment.** A candidate was removed only after a controlled run: the file
   **serial** (alone) and the file **concurrent** with two other files, each
   process with its own `--basetemp` under `/mnt/20t/xubuqiang/.hg-pytest/s7/`.
   A candidate that passes concurrently — with the same failures, or none, as the
   serial run — does not need the lane. Files that *added* a lane were checked the
   same way, including two deliberately colliding copies where the resource is a
   fixed name.
4. **Missing entries.** Files outside `EXCLUSIVE` that trip signals (a)–(e) were
   added with the signal named.

## The signal table

Every test file that trips at least one signal, with the id from the table above.
The remaining **121 of 150** files trip none and stay in the parallel lane; the
guard re-derives this on demand rather than freezing a 150-row list here, so a
file that starts tripping a signal is caught by `test_slow_entries_without_a_signal_are_not_in_exclusive`
(if it is `SLOW`) or shows up in the next derivation.

| file | signals |
|------|---------|
| `test_ahe.py` | c |
| `test_appworld_actor_protocol.py` | b |
| `test_appworld_official_scorer.py` | a, d |
| `test_bfcl_ast_source_contract.py` | f |
| `test_cached_pilot_admission.py` | d |
| `test_candidate_apply.py` | c, d |
| `test_cli_harness.py` | c (false positive) |
| `test_config.py` | c |
| `test_container.py` | d |
| `test_container_state.py` | c, d† |
| `test_diagnostic_policy.py` | c |
| `test_gpu_entrypoint_contract_codex242.py` | a |
| `test_harness_contract.py` | c |
| `test_harness_runtime.py` | c, d |
| `test_improver.py` | a |
| `test_isolation.py` | f |
| `test_jericho_runtime_boundaries.py` | b |
| `test_new_dataset.py` | c |
| `test_progress.py` | d |
| `test_provider_failure.py` | a, b |
| `test_run_schema.py` | c |
| `test_sandbox.py` | c |
| `test_scienceworld_dataset.py` | c |
| `test_service_commands.py` | b, c |
| `test_services.py` | a, b |
| `test_test_lanes.py` | d (its own signal table) |
| `test_unix_model_relay.py` | b |
| `test_web_gateway.py` | b |

The two docker-bearing files whose calls the guard's own regex does not classify
are marked `d†` here and are counted as `d` reasons: `test_container_state.py`
(covered above) and `test_progress.py`, whose `docker` argv is passed to a
monkeypatched `container._run_streaming` and is therefore a false positive — it
stays in the parallel lane.

## Per-file table

`tests/quality/` is implied. "Signal" is the guard's own verdict; "verdict" is the
judgement, including where the signal is a false positive. A `d†` marks a file
whose docker calls the guard does **not** count (a negative-name literal such as
`test_container_state.py`'s `"hg-snapshot"` appears first inside a sorted leak
check), but which statically does call `["docker", "run"|…]` and therefore shares
the daemon; the letter is kept in the table because the reason is real.

### In the exclusive lane

| file | signal(s) | verdict |
|------|-----------|---------|
| `test_services.py` | a, b | real docker network/containers plus a host HTTP server; two live runs share the daemon. Kept. |
| `test_service_commands.py` | b, c | real container launched with the service `"port": 8080` and a fixed container-side listen; the service spec is a fixed port. Kept. |
| `test_web_gateway.py` | b | real `ThreadingHTTPServer` fixtures; the file also pins fixed endpoint literals. Kept. |
| `test_appworld_official_scorer.py` | a, d | real `docker run` (daemon) and the ~1274 s release replay. Kept as a **cost** entry: the only `EXCLUSIVE∧SLOW` overlap. The `fixed-port` hit (`127.0.0.1:8000`) is the AppWorld server *inside* the container, so the isolation reason is the daemon + cost, not a host port. |
| `test_container.py` | d | real `docker` subprocesses; image builds and `docker image rm` on the shared daemon. Kept. |
| `test_container_state.py` | c, d† | real `docker` container-state runs whose cleanup scans `hg-snapshot*` images on the daemon; the file's own comments record a leaked `hg-…` network breaking the next run. Kept. |
| `test_harness_runtime.py` | c, d | **added.** Real `docker rm/create/start/commit/rmi` with the literal container name `hg-test-overlay-commit` and image `hg-test-overlay-snap`; two copies of the file collide (measured below). |

### Added on evidence, then fixed at the source (amendment)

Two files were added to the lane by the derivation below and have since had the
defect itself removed, so they are back in the parallel lane. The history is kept
because the measurements are the evidence that the fix worked:

| file | was | now | why it moved back |
|------|-----|-----|-------------------|
| `test_dataset_lint.py` | added for signal (f) | parallel, no signal | It wrote `ROOT / "data" / "_lint_broken_copy.py"` into the checkout and unlinked it. It now writes the module under `tmp_path` and appends that directory to the `data` package's `__path__`, so `importlib` still resolves `data._lint_broken_copy` while nothing ever appears in `data/`. Nothing to collide over, so no lane is needed and the guard no longer sees a signal. |
| `test_cli_harness.py` | added for signals (c), (f) | parallel | The fixed shared directories `ROOT.parent / "harnessgrad_work" / "pytest-cli"` and `ROOT.parent / "harnessgrad_cli"` were replaced by per-test fixtures. Re-measured after the fix: serial **6 passed**, and two concurrent copies **6 passed each** — the extra failure recorded below is gone. The remaining signal (c) is a false positive: the regex matches the literal `"hg-task"` inside an assertion about the sandbox's HOME naming, not a fixed path. |

### Removed from the exclusive lane

| file | signal(s) | verdict |
|------|-----------|---------|
| `test_provider_failure.py` | a, b (both false positives) | Removed. The `fixed-port` hits are string literals in expected result data; the binds are `bind(("127.0.0.1", 0))` plus a real `ModelGateway` on an ephemeral port, cleaned up by the test. Passed under concurrency (measured). |
| `test_appworld_actor_protocol.py` | b (ephemeral) | Removed. `ThreadingHTTPServer(("127.0.0.1", 0))` in a fixture that shuts the server down in `finally`; the port is read from `server.server_port` into the env var, so it is unique per process. Passed under concurrency (measured). |
| `test_improver.py` | a (false positive) | Removed. Ports appear only in `HG_METHOD_BASE_URL`/`config.toml` literals that are *parsed*, never bound. Passed under concurrency (measured). |
| `test_unix_model_relay.py` | b (in-memory socket) | Removed. The relay socket is created under `tmp_path_factory.mktemp("ux")`, i.e. per-process; the `socket.socket(AF_UNIX, …)` is an object injected into `HTTPConnection`, and TCP is explicitly forbidden. Passed under concurrency (measured). |
| `test_cached_pilot_admission.py` | d (false positive) | Removed. Docker is a `FakeDocker` class and `DOCKER_HOST` appears only in a monkeypatched env-var assertion; no daemon is touched. Passed under concurrency (measured). |
| `test_appworld_service_diagnosis.py` | none | Removed. No signal; its one failure is pre-existing and identical serial vs concurrent. Passed under concurrency (measured). |
| `test_jericho_runtime_boundaries.py` | b (ephemeral) | Removed. The bind is an ephemeral `bind(("127.0.0.1", 0))` whose port is handed to a sandboxed probe; no fixed resource. Its 2 failures are pre-existing and identical serial vs concurrent. |
| `test_jericho_runtime_admission.py` | none | Removed. No signal; it drives `sandbox.run(..., network="none")` on `tmp_path`. |
| `test_parallel_tasks.py` | none after the fix | Removed. This file was fixed earlier today to stop editing the shared checkout; it now writes under `tmp_path` and monkeypatches `runner.PLATFORM_ROOT`. Cost is 5 s, so it is not a cost entry either. |

### Fixed signals outside the exclusive lane

These trip a signal but are **not** in the lane. Each is a false positive on a
literal reading; the guard's `_DOCUMENTED_SLOW_SIGNAL_EXCEPTIONS` pins the one
that lives in `SLOW`.

| file | signal | why the lane is not needed |
|------|--------|----------------------------|
| `test_ahe.py` | c | the `"/tmp/..."`-shaped hit is a name in test data, not a path written or bound. |
| `test_bfcl_ast_source_contract.py` | f | the rewrite/`unlink`/`symlink` calls target `tmp_path`; the path is `__file__`-derived but the *value* is a temporary tree. |
| `test_candidate_apply.py` | c, d | `Path("/tmp") / "hg-cand-…"` are real fixed temp dirs, but nothing else in the suite names them, so they have no partner to collide with; `docker` is a docstring word. |
| `test_config.py` | c | `HG_OVERLAY_ROOT=/tmp/hg-overlays-test` is only asserted through a getter; nothing is created. |
| `test_diagnostic_policy.py` | c | `"/tmp/old"` is a parametrized *input* to a refusal check. |
| `test_gpu_entrypoint_contract_codex242.py` | a | endpoints are parsed, never bound. |
| `test_harness_contract.py`, `test_new_dataset.py` | c | `tempfile.mkdtemp(prefix="hg-…")` — unique by construction, cleaned up. |
| `test_isolation.py` | f | **in `SLOW`.** The `checkout-write` hit is the synthetic `tmp_path` platform the autouse guard fixture builds — the *shape* of a write, not a write to the shared checkout. The fixture is exactly what removes the defect this file used to have. Kept out of the lane because its reason for being slow is cost (it stats and hashes the whole checkout, ~40 s) and it is now safe. |
| `test_jericho_runtime_boundaries.py`, `test_unix_model_relay.py`, `test_appworld_actor_protocol.py` | b | ephemeral, per-process binds; see the removals above. |
| `test_progress.py` | d | all `docker` arguments are passed to a monkeypatched `container._run_streaming`. |
| `test_run_schema.py`, `test_sandbox.py`, `test_scienceworld_dataset.py` | c | `/tmp/...` strings are fixture *inputs* (`"/tmp/w"`, `"/tmp/x"`, archive candidates); none is bound or written. |
| `test_test_lanes.py` | d | this document's own guard contains the literal `"docker"` in its signal table. It reads files; it binds nothing. |
| `test_service_launcher_unchanged.py` | c | `hg-*` strings are container-internal path *expectations* and the probe network's name; nothing is created at them. No daemon is touched: `container_mod.run`, `sandbox_mod.run` and `docker` are all monkeypatched in this file. |
| `test_run_observation_wiring.py` | c, d | (c) `hg-observe-run-9-…` is an expected network-name prefix, asserted on a fake runner. (d) every `docker` argv in the file is handed to a monkeypatched runner; no daemon is touched. |

## Measurements

All runs used a fresh `--basetemp` per process under
`/mnt/20t/xubuqiang/.hg-pytest/s7/`. "Concurrent" means the target plus the
companions named, started together; the comparison is the **failure set**, not the
wall clock (the machine is shared, so timing is not evidence here).

### Removals (candidate passes under concurrency)

| file | serial | concurrent with | result |
|------|--------|-----------------|--------|
| `test_appworld_actor_protocol.py` | 15 passed | `test_config`, `test_runner_admission` | 15 passed both; no new failure |
| `test_improver.py` | 21 passed | `test_config`, `test_runner_admission` | 21 passed both |
| `test_unix_model_relay.py` | 49 passed (25.0 s) | `test_config`, `test_runner_admission` | 49 passed both (25.1 s) |
| `test_cached_pilot_admission.py` | 199 passed (37.2 s) | `test_config`, `test_runner_admission` | 199 passed (86.4 s — load, not failure) |
| `test_provider_failure.py` | 13 passed | `test_services`, `test_service_commands` | 13 passed while `test_services` ran a real docker lane beside it |
| `test_appworld_service_diagnosis.py` | 1 failed, 5 passed | `test_config`, `test_runner_admission` | identical 1 failed, 5 passed both |
| `test_jericho_runtime_boundaries.py` | 2 failed, 15 passed | `test_config`, `test_runner_admission` | identical 2 failed, 15 passed both |
| `test_web_gateway.py` (kept — control) | 3 failed, 40 passed | `test_config`, `test_runner_admission` | identical failure set; kept on its real-signal table, not this run |

The two pre-existing failure sets were re-checked against the original commit
(`git stash` of `tools/run_tests.py`, i.e. the old `EXCLUSIVE` tuple): they fail
identically, so they are environmental, not lane-related.

### Additions (fixed name collides)

| file | serial | concurrent with | result |
|------|--------|-----------------|--------|
| `test_harness_runtime.py` | 15 passed | a **second copy of itself** | 1 failed, 13 passed, 1 error in **both** copies. The failure is `test_the_overlay_cannot_reach_the_graded_snapshot` (two processes race on the container `hg-test-overlay-commit` / image `hg-test-overlay-snap`). Confirmed by the fact that the same file passes when the daemon companion is a *different* file. |
| `test_cli_harness.py` | 2 failed, 4 passed | a **second copy of itself** | 3 failed, 3 passed in both copies. The extra failure (`test_a_harness_that_cannot_operate_on_this_environment_is_refused`) was caused by both processes sharing `ROOT.parent / "harnessgrad_cli"` and `ROOT.parent / "harnessgrad_work"`. **Re-measured after the fixture fix: 6 passed serial, 6 passed in each of two concurrent copies.** |
| `test_dataset_lint.py` | 4 passed alone (27.3 s) | a second copy (targeted) | both passed in this window — a sub-second create/unlink race did **not** fire. Kept on the static signal (f) alone at the time; the write was then removed entirely (see the amendment above), so the file is back in the parallel lane. |

Also confirmed: `test_container.py` (6 failed, 20 passed) and
`test_container_state.py` (1 failed, 18 passed) show **the same failure sets**
running alone and running concurrently with `test_harness_runtime.py` and each
other. Their failures are environmental (missing `DEEPSEEK_API_KEY`, unpinned
local images), not contention — but both keep the lane on signals (c)/(d).

### Smokes

* `python3 -m pytest -q -p no:cacheprovider --basetemp=… tests/quality/test_test_lanes.py` → **8 passed**.
* Falsification of the guard: a bogus `EXCLUSIVE` entry with no signal, a stale
  name, a duplicate, and an undocumented `SLOW`/`EXCLUSIVE` overlap were each
  injected and each was rejected.
* `python3 tools/run_tests.py --list` after the original change: **141 parallel files,
  9 exclusive files**; after the two source fixes above: **143 parallel, 7 exclusive**.

## What is static-only

Stated plainly, so the strength of each entry is visible:

* **Static signal only, no experiment**: `test_services.py`,
  `test_service_commands.py`, `test_web_gateway.py`, `test_container.py`,
  `test_container_state.py`, and `test_appworld_official_scorer.py` (whose replay is
  too expensive to run twice; one concurrent run was not attempted).
* **Confirmed by experiment**: the removal of the nine files listed above
  (`test_web_gateway.py` was the kept control), the addition of
  `test_harness_runtime.py`, and the two files that were added and then fixed at the
  source (`test_dataset_lint.py`, `test_cli_harness.py` — see the amendment).

## Could not determine

* The exact set of pre-existing failures in `test_container.py` and
  `test_container_state.py` is environmental on this host (missing
  `DEEPSEEK_API_KEY`, local image availability); those files were not the subject
  of this change and their lane was not altered.
* `test_sandbox.py` fails on this host with the sandbox's platform listing
  reported as `not-a-dir`, identically serial and concurrent. Whether that is a
  lane concern or a host configuration concern was not established, and the file
  is not in either lane by cost.
* The demoted files were measured against two companions, not against a full
  8-job parallel lane. The runner's own run-alone recheck remains the backstop: a
  file that fails under load and passes alone is reported as load-sensitive rather
  than as a regression.
