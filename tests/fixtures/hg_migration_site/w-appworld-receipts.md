# AppWorld mounted-service diagnostic - w-appworld

**The diagnostic ran; the separate verifier mounted the service recording and returned a raw measured loss (`score=0.0`, `success=false`). Neither blocker closes.** The staged receipt remains `score=null`, measured 0 / unmeasured 1 / invalid 0 because `policy_enforcement_not_observed`. This is one AppWorld mounted-service control for shared assessment S2/S5, not a successful actor-task receipt or complete offline closure.

## Scope and exact command

Read `AGENTS.md`, all S1-S8 in `reports/w-assess-shared-closures.md`, `reports/w-shared-topology-survey.md`, `reports/w-img-abi-custody.md`, the AppWorld adapter/check, service verifier route, `tools/diagnose_appworld.py` and `docs/egress.md` before execution. The latter diagnostic tool was read but **not executed**: its no-model/no-scorer probe would not exercise the requested verifier. No code, gate, refusal, scorer, image, actor budget or task prompt was changed.

Registered worktree `/tmp/hg-appworld`, branch `wg-appworld`, baseline `cc4dc28ea66c585a9c4afd8204f93d21df814368`. The driver records `platform.dirty=true` because the observer/evidence files were untracked; tracked runtime code matched the baseline. Driver `platform.content_sha=545ed8fc6c98b61d67f1a45035f842c83732c3b0b20795171f2a54cf89c5e389` is retained without relabeling it as a clean checkout.

Run ID: `w-appworld-20261014T225023Z-f02d7cd5`. `launch-input.json` records `fresh_run_absent=true`, `fresh_work_absent=true`; no run ID was resumed. Work root: `/tmp/w-appworld-20261014T225023Z-f02d7cd5-work` (outside the platform tree). Original run: `/tmp/hg-appworld/runs/w-appworld-20261014T225023Z-f02d7cd5`. All subsequent paths below are relative to `reports/w-appworld-evidence/` unless explicitly absolute.

Exact driver invocation (`command.txt`, structured argv/environment in `launch-input.json`):

```sh
env HG_EGRESS_PROXY=http://172.17.0.1:17890 HG_AGENT_BACKEND=openai HG_AGENT_MODEL=qwen3.5-9b-local HG_AGENT_BASE_URL=http://127.0.0.1:8002/v1 HG_AGENT_API_KEY=local python3 driver.py --harness loop --dataset appworld --sampling diagnostic --policy-evidence /tmp/hg-appworld/reports/w-appworld-evidence/policy-input.json --authorize-unobserved-diagnostic 'Operator explicitly authorizes ONE AppWorld mounted-service diagnostic to collect separate-verifier recording mounts, network, resource and cleanup observations for shared scorer-offline-closure S2/S5. Gates and enforcement remain unknown; raw scorer output is not comparable score publication, successful-task evidence unless authentic measured success exists, pilot expansion or production authorization.' --rounds 0 --trials 1 --jobs 1 --side train --improver none --run-id w-appworld-20261014T225023Z-f02d7cd5 --runs-root /tmp/hg-appworld/runs --work-root /tmp/w-appworld-20261014T225023Z-f02d7cd5-work
```

The collection command was `python3 reports/w-appworld-evidence/launch.py`. The saved launcher starts this driver exactly once and observes Docker externally with `docker events`, `docker inspect`, `docker network inspect` and periodic container listings. It does not monkeypatch the runtime, supply enforcement attestations or execute scorer/actor control arms. Full local model binding is explicit; `/models` was probed without a completion. `processes-before.json` and `containers-before.json` retain preflight concurrency observations: no other driver execution was listed; unrelated long-lived services were left untouched.

`driver-result.json`: `started_at=2026-10-14T22:50:24.183437+00:00`, `ended_at=2026-10-14T22:51:15.294647+00:00`, `returncode=2`. `driver.stdout.txt` reports the task done and then `refusing staged curve: lifecycle publication gate or measurement incomplete; invalid/unmeasured outcomes are preserved in the receipt`. Exit 2 is the preserved publication refusal after execution; the actor and verifier each exited 0. All timestamps above are UTC; the journal uses Asia/Shanghai's 2026-10-15 date. There was no second diagnostic.

## Selected task, coverage and image custody

`split-selection.json` and `execution/diagnostic_receipt.json`: deterministic `diagnostic_task_set` selected task `cf6abd2_2` before execution from `eligible_tasks=147` (released train+dev study side), `selected_tasks=1`, `expected_trials=1`, `trials=1`, `jobs=1`, `rounds=0`. `selected_metadata` has `source_split="train"`, `scenario_id="cf6abd2"`, `variant=2`. The release contains 732 evaluators; this run covers **one**, no eval-side task. No released solution/oracle was supplied to the actor and no tuning occurred.

The actual task-role container observations, not just configured defaults:

| Role | Container ID | Actual `Image` digest |
| --- | --- | --- |
| Actor | `1be0358d6873aa39d2235e2da9df6261aa820484af2cf0f2f94e708a40a91eea` | `sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb` |
| Service | `cb1e975618f9ab90105019eb19cbcff8004a5dd047d0ed741a20b4a9a81233fd` | `sha256:db09e34ec90347e7d5271d95445f360222ba1aef3ca0eaa5614ce768bf23aca4` |
| Verifier | `99c409ac81685df81f439e6f25d4a11803b512054bd1f404a3ccb917c92c8550` | `sha256:f3cb916b9bdc297d4c73dd9646e9550befb7c6e23827d937b90a0640373e97ca` |

Raw paths: `container-observations.jsonl` (time-stamped `docker inspect` objects, Config.Env restricted to local model/service/task bindings; unretained environment fields excluded), `docker-events.jsonl` (create/start/die/destroy and image references), and `image-actor.json`, `image-service.json`, `image-verifier.json` (fresh preflight `docker image inspect` outputs). `role-observations.json` is explicitly a derived role index with both running and final observations. Configured references in `launch-manifest.json` are `harnessgrad/appworld-agent@` plus the actor digest and `harnessgrad/appworld-service@` plus the distinct service/verifier digests. Both the actual `.Image` and `.Config.Image`/RepoDigests are preserved; no movable-tag substitution or image pull occurred.

## Mounted-service topology actually observed

`role-observations.json` / `container-observations.jsonl` show actor `/jolly_ramanujan` and verifier `/loving_kowalevski` with **different container IDs and different pinned images**. Actor `FinishedAt="2026-10-14T22:51:03.714643613Z"`; verifier `StartedAt="2026-10-14T22:51:04.21760232Z"`. The service remained running during the verifier's entire execution, then was removed at teardown. The actual verifier argv is:

```json
[
  "python3",
  "verify/check.py",
  "--task-id",
  "cf6abd2_2",
  "--source-split",
  "train",
  "--log",
  "/recordings/appworld/experiments/outputs/harness/tasks/cf6abd2_2/logs/api_calls.jsonl",
  "--reward-file",
  "/verifier/reward.txt",
  "--evidence-file",
  "/verifier/evidence.json"
]
```

Raw network identity from `network-observations.jsonl` and the three running Docker inspections:

- `Name="hg-w-appworld-20261014T225023Z-f02d7cd5-cf6abd2_2"`; `Id="a42eced608b3cd6170ce4a1727ce512b11949f21b7c8265bdffb0c132ba7f3da"`; `Driver="bridge"`; `Internal=true`; IPAM subnet `172.19.0.0/16`, gateway `172.19.0.1`.
- All three roles have `HostConfig.NetworkMode="hg-w-appworld-20261014T225023Z-f02d7cd5-cf6abd2_2"` and exactly that network attachment. There is no second default-bridge attachment in these observations. Driver `bridge` describes the **owned internal** network's implementation, not a claim of external access.
- Service alias `Aliases=["appworld"]`, IP `172.19.0.2`; actor environment `HG_SVC_APPWORLD_URL=http://appworld:8000`. Network observation at `2026-10-14T22:51:04.495988+00:00` lists both service and verifier attached, after the actor's removal. No separate verifier HTTP reachability probe was sent; attachment and replay are the observed scope.

Exact bind mounts (same raw inspect paths; `RW` is Docker's value):

| Role | Host source | Container destination | `RW` |
| --- | --- | --- | --- |
| Service | `/tmp/hg-appworld/runs/w-appworld-20261014T225023Z-f02d7cd5/recordings/cf6abd2_2/appworld` | `/recordings/appworld` | `true` |
| Actor | `/tmp/hg-task-_hfsrkcw` | `/app` | `true` |
| Verifier | `/tmp/hg-task-_hfsrkcw` | `/app` | `false` |
| Verifier | `/tmp/hg-appworld/runs/w-appworld-20261014T225023Z-f02d7cd5/recordings/cf6abd2_2/appworld` | `/recordings/appworld` | `false` |
| Verifier | `/tmp/hg-verdict-vsh36_jz` | `/verifier` | `true` |

The actor also has its harness root mounted read-only, its `.state` writable, and its dependency overlay read-only; exact four actor mounts are retained in `role-observations.json`. **The actor has no recording mount.** The service has only the recording bind. This is mounted state with a trusted recording surface and a fresh replay container; it is not an actor-image snapshot. The selected `launch-manifest.json` has `environment.state="mount"`. Saved Docker events include no commit event; the verifier's image is the independently pinned verifier image above.

The original recording file is `/tmp/hg-appworld/runs/w-appworld-20261014T225023Z-f02d7cd5/recordings/cf6abd2_2/appworld/experiments/outputs/harness/tasks/cf6abd2_2/logs/api_calls.jsonl`; its byte-identical review copy is `execution/recordings/cf6abd2_2/appworld/experiments/outputs/harness/tasks/cf6abd2_2/logs/api_calls.jsonl`. `result-summary.json.api_call_count=14` is a derived count of its nonempty JSON lines. Service stdout/stderr survive in `execution/recordings/cf6abd2_2/appworld/service.log`. The verifier replays this recording through unchanged `data/datasets/appworld/verify/check.py:45-56` and invokes `world.evaluate()`.

## Raw measurement versus staged publication

`execution/diagnostic_execution.json.verdicts.cf6abd2_2` and `result-summary.json.raw_verdict`:

```json
{
  "kind": "command",
  "passed": false,
  "score": 0.0,
  "detail": "reward 0 from /verifier/reward.txt (exit 0) | stdout 0.0",
  "scorer_isolation_class": "snapshot-service",
  "scorer_isolation_declared": "snapshot-service",
  "evidence": {
    "task_id": "cf6abd2_2", "source_split": "train",
    "metric": "appworld.task_goal_completion", "metric_name": "TGC",
    "measured": true, "success": false, "num_tests": 8
  }
}
```

The exact preserved reward bytes are `b'0.0\n'` in `verifier-reward.txt-51ff0d2f0d3a5d61edec31785532ea0d570f8c348d58b15b94ff9c2ca6e926a4`. Full scorer evidence is `verifier-evidence.json-0666239fd29ec703b538231a2b11a5cb644733b10f433555d4dfbdcc00fcacdb`. `verifier-file-observations.jsonl` binds these copies to the container ID, `/tmp/hg-verdict-vsh36_jz/reward.txt` and `/tmp/hg-verdict-vsh36_jz/evidence.json`, timestamps, byte lengths and SHA-256. The evidence lists seven passes and one failure; the failed requirement is `assert answers match.` with `AssertionError:  '<<not_given>>' == 'null'`. Those are the scorer's raw values, not a repaired or alternative score.

`result-summary.json.raw_counts` derives **measured=1, invalid=0, unmeasured=0** at the raw scorer layer from `evidence.measured=true`, no `unmeasured` key and `execution.invalid={}`. Actor output is `exit_code=0`, `stderr=""`. Executed isolation class is `snapshot-service`; its `scorer_isolation_comparable=true` only identifies matching topology.

Separately, unchanged `execution/diagnostic_receipt.json` has **measured_trials=0, unmeasured_trials=1, invalid_trials=0**, the same task counts, `score=null`, `outcomes[0].reason="policy_enforcement_not_observed"`, `enforcement.comparable=false`, `production_authorized=false`, `policy_enforcement_observed=false`, `lifecycle=null`, `cleanup.status="unknown"`, `snapshot.status="unknown"`, `context.gates.status="unknown"`. The raw loss cannot replace these formal fields. Passive observations below do not retroactively attest admission or publish a comparable score.

## Resource observations in force

`container-observations.jsonl` and its derived `role-observations.json` provide running-container configuration, rather than only requested policy values:

| Role | `HostConfig.NanoCpus` | `HostConfig.Memory` (bytes) | `HostConfig.PidsLimit` | `ReadonlyRootfs` |
| --- | --- | --- | --- | --- |
| Actor | `2000000000` | `4294967296` | `1024` | `true` |
| Verifier | `2000000000` | `4294967296` | `1024` | `true` |
| Service | `0` | `0` | `null` | `false` |

Actor/verifier each have `/tmp` tmpfs `rw,exec,nosuid,size=512m,mode=1777`. Service has no tmpfs entry. Thus actor/verifier have Docker-configured 2 CPU / 4 GiB / 1024 PID caps; the service has **no explicit per-container CPU/memory/PID cap**. This gap is retained; `eval/service/services.py:380-406` builds the service command without those resource flags. No limits were added or gates weakened for this run.

`cgroup-live-observations.json` records host reads of `/proc/<Pid>/cgroup` and the corresponding `/sys/fs/cgroup/...` files while verifier and service were live:

| Role / sampled host PID | `cpu.max` raw | `memory.max` raw | `pids.max` raw | `memory.current` / `pids.current` raw |
| --- | --- | --- | --- | --- |
| Verifier / `483087` | `200000 100000\n` | `4294967296\n` | `1024\n` | `677068800\n` / `3\n` |
| Service / `471632` | `max 100000\n` | `max\n` | `308691\n` | `796307456\n` / `7\n` |

Exact cgroup source paths, raw `cpu.stat`, and observation times are in that file. The service's finite `pids.max=308691` is an observed daemon-provided cgroup value, **not** an explicit 1024 cap; its origin was not separately traced. The actor had already exited when these host cgroup reads occurred; its resource evidence is running Docker configuration, not a sampled actor cgroup file. Memory/PID current values are point samples, not peaks. No filesystem quota enforcement, disk maximum, dynamic dependency completeness or infallible resource enforcement is established.

`policy-input.json.context.limits` retains the requested resource/time/budget declaration: `cpus=2`, `memory_mb=4096`, `pids=1024`, actor/verifier deadlines 900 s, `max_steps=32`, `max_output_tokens_per_call=2048`; the service mismatch is explicit above. `execution/diagnostic_execution.json.identity_declared.config` confirms the unchanged actor defaults. The actor made 16 calls within its existing budget; no budget was raised.

## Cleanup: exact objects and observed absence

`docker-events.jsonl` records actor/verifier normal `die exitCode="0"` and `destroy`; service `kill signal="9"`, `die exitCode="137"`, `destroy`, followed by owned network `destroy`. `container-observations.jsonl` records service `OOMKilled=false`. The service exit 137 is correlated with forced normal teardown **after** verifier exit, not silently classified as a scorer OOM or a task loss.

`cleanup-observations.json`, observed from `2026-10-14T22:51:15.294984+00:00`, contains exact commands/results:

- `docker inspect <actor full ID above>`: `returncode=1`, `stdout="[]\n"`, `stderr="error: no such object: <same full ID>\n"`.
- `docker inspect <service full ID above>`: same raw absence result for that ID.
- `docker inspect <verifier full ID above>`: same raw absence result for that ID.
- Both driver preflight containers (boundary self-check `de5d479559df...` and interpreter locator `208c058ff3c6...`) also have exact absent-after inspect results. These are not extra actor trials.
- `docker network inspect hg-w-appworld-20261014T225023Z-f02d7cd5-cf6abd2_2`: `returncode=1`, `stdout="[]\n"`, `stderr="Error response from daemon: network hg-w-appworld-20261014T225023Z-f02d7cd5-cf6abd2_2 not found\n"`.
- `temporary_paths`: `/tmp/hg-task-_hfsrkcw` and `/tmp/hg-verdict-vsh36_jz` each `exists=false`.
- Run-label filtered container/network listings: `returncode=0`, headers only, no objects. Exact-ID probes cover the unlabeled actor/verifier as well.

`cleanup-processes-and-relay.json` adds later exact `/proc/473746` (actor), `/proc/471632` (service) and `/proc/483087` (verifier) observations: all `proc_exists_after=false`. The saved command `ss -ltn 'sport = :41625'` returns 0 with only the header, no model gateway listener. Absence observations are bounded to these IDs/paths/times; no exhaustive namespace-proof claim follows.

The service recording and run/work receipts are deliberately retained. The recording is expected evidence, not a leaked running service. No historical run/receipt/archive was removed or overwritten. Formal staged `cleanup` and `snapshot` remain unknown as above.

## Endpoint, provenance and comparison limits

`endpoint-models.json`: `/models` at `http://127.0.0.1:8002/v1` lists `qwen3.5-9b-local`, `model_completion_probe_sent=false`. `launch-input.json` binds `HG_AGENT_BACKEND=openai`, `HG_AGENT_MODEL=qwen3.5-9b-local`, `HG_AGENT_BASE_URL=http://127.0.0.1:8002/v1`. Running actor inspect records `HG_AGENT_BASE_URL=http://172.19.0.1:41625/v1`; `execution/diagnostic_execution.json.model_gateway` records `published=true`, `target="127.0.0.1:8002"`, `connections=16`, `upstream_connect_failed=0`, `bytes_to_upstream=215585`, `bytes_to_client=17087`. This is one binding through the platform relay to the single authorized local endpoint. No paid endpoint or second GPU server was used.

`execution/diagnostic_execution.json.tokens` records `calls=16`, `input=56264`, `output=1286`, `tasks_reported=1`; trace `execution/progress/round-0/traces/cf6abd2_2.trial1.jsonl` preserves identity/usage and the actor interactions. Simulator `env_usage.calls=0` is separate from actor model usage.

Raw hash locations: `source-hashes.json`, `harness-hashes.json`, `launch-manifest.json`, `policy-input.json`, `execution/run_meta.json`. The selected scorer SHA-256 is `0271f08e42199bcc417222b896979408161392743271d5d9479983d4602a0ca5`; bundle SHA-256 `fd9f9608c2ec71ed0ac25c3633a738b9129a318a129e31230425b9188e508250`; pool SHA-256 `970c39331bc350ce9d3ed91a75fd32b554c257fc48fc31633dfccdde74164e65`. Policy context hashes (SHA-256 of canonical sorted JSON inventories/manifest as computed by the saved launcher):

```json
{
  "harness": "3b3079a730139ac4bcd603f85606cacfa16560adddc53b0cc6f02ba3c252889c",
  "manifest": "9ab0b2b8342d8073ae009e0f9c6f9b4624abec1fea84e343117d78c162a3dde9",
  "model": null,
  "source": "24603418aca4321802353989029418684db53a894904844b6b535e8b3e957ae9",
  "split": "c4ce84665842ad34fadf7d9a4172e1fdfc07092cc78bac42b6c2cebdf0077e8d"
}
```

The model weights hash remains `null`. Driver `split_source="declared"`, `split_sha="4bc196ca42576811acbc43f030298826c0a5f1b63db88dc204c0f3a3514f0f5b"` hashes its own split representation; the distinct policy split hash binds the expanded selection metadata in `split-selection.json`. These hash domains are not conflated. Image pins and source hashes do not establish complete transitive scorer installation/ABI custody, provenance authentication or closure across all 732 tasks.

All 25 original run files have byte-identical committed review copies under `execution/`; `artifact-copy-provenance.json` gives absolute originals, copy paths, byte counts and recomputed hashes. The temporary verdict files were copied by the observer before platform cleanup. `packet-sha256.json` inventories the final evidence packet, excluding itself and Python cache files. Historical evidence bodies were not repaired or resumed.

## Blocker decisions and validation

**AppWorld mounted-service control exists at one-task scope.** This removes the specific absence of an AppWorld observation in shared assessment S2/S5. It does not satisfy S2/S5 across other runtime classes/datasets or the complete action in `scorer-offline-closure`; S1-S6's remaining dataset/closure/valid-live-control requirements remain. Shared `scorer-offline-closure` stays `code-solvable`. `artifact-only-state-export` is unchanged.

**AppWorld successful-task-evidence remains open.** Its status remains `measurement`; raw `success=false` and staged `score=null` establish no authentic successful measured actor-task receipt. The registry appends this bounded failure to the retained historical evidence, with no successful-task closure or pilot authorization.

`reports/w-appworld-validation.txt` retains checker and focused pytest command/output. Initial focused validation: **82 passed, 1 failed**. The failure is the unchanged test stub at `tests/quality/test_appworld_service_diagnosis.py:79`, `lambda *a: None`, which rejects `run_id` keyword arguments passed by unchanged `eval/run/runner.py:160-162`. It is a stale test double, not the diagnostic result and not a reason to weaken admission. Both files match baseline tracked content. The original failing output is retained in `focused-tests-initial.txt`; no production or test patch was made around it. Final source-only registry tests after registry edits: **54 passed**. No additional benchmark diagnostic/control was launched for testing.

Final packet audit: all 64 inventoried evidence files are SHA-256 matched and staged, with the packet manifest also staged. `git diff --cached --check` reports whitespace only in byte-preserved pytest output and the service recording `environment_io.md`; these raw outputs were deliberately not normalized.
