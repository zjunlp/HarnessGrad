# AppWorld unchanged-budget actor diagnostic - w-appworld2

**AppWorld successful-task-evidence remains OPEN.** One fresh actor diagnostic produced authentic raw measured TGC **0.0**, `success=false`, with **6/8 requirements passing**. It did not obtain successful actor-task evidence. The formal staged receipt remains **score=null, measured=0, unmeasured=1, invalid=0** because `policy_enforcement_not_observed`. No shared blocker closes; no pilot expansion is authorized.

All packet-relative paths below are under `reports/w-appworld2-evidence/`. Raw command outputs, the actor trace, service recording, official verifier bytes and copied driver receipts are preserved. Existing `runs/`, frozen receipts, historical journal paragraphs and prior evidence packets were not modified or resumed.

## Driver requirements and exact attempt

Read `AGENTS.md`, the exact AppWorld blocker, `reports/w-appworld-receipts.md` and `reports/w-awp-released-predicates.md` first, then the generic actor, public CLI/runtime prompt, task-bound service, separate replay checker, deterministic selector, staged policy and historical protocol review. `starting-appworld.json` preserves the starting blocker, including its unchanged next action. `review-source/` preserves reviewed runtime bytes with `review-source-hashes.json`; it is an evidence copy, not code used to alter the actor.

The driver requires a provisioned pool with its admitted digest, locally present pinned actor/service/verifier images, Docker/container-boundary preflight, the existing dependency overlay, explicit live model binding, and a work root outside the platform. Diagnostic policy also requires a dataset/side/endpoint-bound context with source/split/manifest/model/harness hash slots, resource/budget limits and comparability notes. Unknown dataset gates require explicit `--authorize-unobserved-diagnostic`; that permission never attests policy enforcement or permits staged score publication. The driver refuses rather than bypassing these boundaries. Actual preflight and execution output is in `driver.stdout.txt` and `driver.stderr.txt`.

`endpoint-models.json` records the `/models` response from **http://127.0.0.1:8002/v1**. An additional generic, task-free `Reply with OK.` completion returned **OK**, model `qwen3.5-9b-local`; exact request/status and unmodified response bytes are `endpoint-completion-request.json` and `endpoint-completion.raw.json`. The `/models` record's `model_completion_probe_sent=false` describes that models request only; the separate completion is explicitly retained. The probe used 16 maximum output tokens and returned 2, separate from actor usage. No server was started and no paid endpoint was used.

Preflight `processes-before.json` found an operator-owned Musique run (its argv includes `jobs=40`) and existing model servers, including a process on 8001. They were left untouched; this diagnostic used only 8002 and **one worker**. Endpoint exclusivity/global concurrency cannot be claimed. `containers-before.json` and `networks-before.json` preserve contemporaneous Docker listings. The process filter also captured its own long shell command; no global-idleness inference is made from that noisy listing. `inherited-overrides.json` and `dotenv-overrides.json` are empty: no hidden AppWorld/model/budget override was applied.

Registered worktree `/tmp/hg-appworld2`, branch `wg-appworld2`, baseline `9ef5e903c97a7bdc548495d5cad9ef0957fb89ea`. The driver records `platform.dirty=true`, content SHA `c8a742b2ec3a944fa5ac4f6c54ed0d602eef52cda5f030491ddd445111171a53`, because new evidence files existed. `runtime-unchanged.json` records an empty successful tracked-runtime diff. This was not relabeled a clean checkout.

Fresh run ID: **`w-appworld2-20261015T050036Z-a6d55a63`**. `launch-input.json` records both fresh paths absent before launch:

- Run directory: `/tmp/hg-appworld2-run-artifacts/w-appworld2-20261015T050036Z-a6d55a63`.
- Work root: `/tmp/w-appworld2-20261015T050036Z-a6d55a63-work`.

Both are outside the platform; the repository's `runs/` was not used. `launch.py` was invoked once, via `python3 reports/w-appworld2-evidence/launch.py > reports/w-appworld2-evidence/launcher.stdout.txt 2> reports/w-appworld2-evidence/launcher.stderr.txt`. It launches the driver once and passively observes run-owned Docker objects. No retry or second diagnostic occurred.

Exact driver command (`command.txt`; argv/environment also in `launch-input.json`):

```sh
env HG_AGENT_BACKEND=openai HG_AGENT_MODEL=qwen3.5-9b-local HG_AGENT_BASE_URL=http://127.0.0.1:8002/v1 HG_AGENT_API_KEY=local python3 driver.py --harness loop --dataset appworld --sampling diagnostic --policy-evidence /tmp/hg-appworld2/reports/w-appworld2-evidence/policy-input.json --authorize-unobserved-diagnostic 'User explicitly requests ONE fresh AppWorld diagnostic to attempt authentic successful measured actor-task evidence with the unchanged generic loop and existing budget. Dataset/shared gates remain unresolved and enforcement unobserved; preserve raw scorer outcome and formal publication refusal separately. No tuning, budget increase, expansion or production authorization.' --rounds 0 --trials 1 --jobs 1 --side train --improver none --run-id w-appworld2-20261015T050036Z-a6d55a63 --runs-root /tmp/hg-appworld2-run-artifacts --work-root /tmp/w-appworld2-20261015T050036Z-a6d55a63-work
```

The truthful authorization reason is: “User explicitly requests ONE fresh AppWorld diagnostic to attempt authentic successful measured actor-task evidence with the unchanged generic loop and existing budget. Dataset/shared gates remain unresolved and enforcement unobserved; preserve raw scorer outcome and formal publication refusal separately. No tuning, budget increase, expansion or production authorization.”

`driver-result.json`: start `2026-10-15T05:00:39.093463+00:00`, end `2026-10-15T05:02:13.514997+00:00`, return code **2**. Actor and verifier each exited 0; driver exit 2 is the retained staged-publication refusal after execution, not an actor/scorer infrastructure error. The driver printed task `done (88s)`; the actor's own trace reports `71.8` seconds. These measure different scopes.

## Selection, budget and provenance

`split-selection.json` and `execution/diagnostic_receipt.json` bind the deterministic `diagnostic_task_set` choice **`cf6abd2_2`**, source split **train**, scenario `cf6abd2`, variant 2, from **147 eligible train+dev study tasks**. Selected tasks=1, expected trials=1, trials=1, jobs=1, rounds=0. No held-out task executes. This repeats the unchanged selector's earlier choice, not a task chosen for ease or adjusted after seeing a result. No oracle, gold answer or released solution was supplied to the actor.

The actual trace identity (`execution/diagnostic_execution.json.identity_declared`) retains **max_steps=32**, **max_output_tokens=2048**, submit_retries=2, parse_retries=2, finish_nudge_steps=4, repeat_nudge_limit=2, output_limit=4000, context_chars=60000. No actor/prompt/CLI/scorer/model/selection/budget code was changed, and no budget override was added. `policy-input.json` retains actor/verifier 900-second deadlines and requested 2 CPUs / 4096 MiB / 1024 PIDs. Full effective identity and resource observations are preserved separately from requested policy.

Freshly computed hash inventories are `source-hashes.json` (adapter, CLI, checker, provisioned release bundle and pool), `harness-hashes.json` (generic loop files), `selected-source-hashes.json` (selected spec and predicate bytes, hashed only after completion), and `review-source-hashes.json`. Image preflight is in `image-actor.json`, `image-service.json`, `image-verifier.json`. No historical digest was substituted for a fresh computation. Policy canonical-JSON hash domains are:

```json
{
  "source": "e65f7069663f98550c067d2a9eeb8b7a770ae13c5384df5d1ac0f862a2ec22bf",
  "split": "c4ce84665842ad34fadf7d9a4172e1fdfc07092cc78bac42b6c2cebdf0077e8d",
  "manifest": "b1c3272256acdc3b79b0aec3030d26911460e051d1df77f7dced1ac3b70a37b4",
  "model": null,
  "harness": "3b3079a730139ac4bcd603f85606cacfa16560adddc53b0cc6f02ba3c252889c"
}
```

The model weight hash is **null/unknown**; advertised model identity and existing process path do not authenticate weights. Driver split source is `declared`, split SHA `4bc196ca42576811acbc43f030298826c0a5f1b63db88dc204c0f3a3514f0f5b`. It hashes a different representation from the policy split digest; neither is relabeled as the other. Source/image pins do not establish upstream authentication, full transitive closure or production custody.

## Authentic replay result and formal refusal

`execution/diagnostic_execution.json.verdicts.cf6abd2_2` records unchanged checker metric **`appworld.task_goal_completion` / TGC**, `measured=true`, `success=false`, `score=0.0`, `num_tests=8`, six passes and two failures, with no invalid task or scorer-unmeasured marker. `result-summary.json.raw_counts` derives **measured=1, invalid=0, unmeasured=0** only at this raw scorer layer.

The raw verifier wrote `0.0\n` and complete evidence; exact captured bytes are:

- `verifier-reward.txt-51ff0d2f0d3a5d61edec31785532ea0d570f8c348d58b15b94ff9c2ca6e926a4`.
- `verifier-evidence.json-e6f59180c1dd5424b36dba3ffff54bae4facf5b7fc5ad5eee987802f8ac4804a`.

`verifier-file-observations.jsonl` binds those copies to verifier `bc0bd9baada9ed710fe3412f572828bcead44be7393ee9d9e091226768190fcc` and `/tmp/hg-verdict-4mf75dyd/`, with capture times, sizes and SHA-256. The actor has no recording mount; the service owns the recording, and a fresh separately pinned verifier mounts it read-only and replays API calls before `world.evaluate()`. This is real actor execution followed by the production replay checker, not a synthetic/gold-injected success control.

The two failed official requirements are `assert answers match.` (`'<<not_given>>' == 'null'`) and the updated note body comparison. The actor left the requested item marked done and also changed an unrelated item from not done to done. Complete expected/actual lists remain in the raw evidence; no corrected answer, replay, replacement state or alternative score was inserted.

Formal `execution/diagnostic_receipt.json` retains `score=null`, coverage measured_trials=0 / unmeasured_trials=1 / invalid_trials=0, reason `policy_enforcement_not_observed`, `comparable=false`, `production_authorized=false`, `policy_enforcement_observed=false`, `lifecycle=null`, gates/cleanup/snapshot status `unknown`. Passive Docker observations do not override these fields. There is no valid published success or comparable production diagnostic.

## What this establishes about 9B success at the existing budget

The trace `execution/progress/round-0/traces/cf6abd2_2.trial1.jsonl` and service API log establish that this 9B actor can initialize, discover documentation, obtain in-world credentials, authenticate, find the note, perform a state-changing PATCH and read it back with the existing budget. There are **14 recorded API calls**, **15 shell commands**, and **16 model calls**, with **56180 input / 1246 output tokens**, zero retries and zero context trims. A failed guessed documentation lookup returned an application message at step 6 and discovery recovered at step 7; shell commands still exited 0. `model_gateway` records target `127.0.0.1:8002`, 16 connections and 0 upstream connect failures.

The actor voluntarily submitted `{"answer": {"task_id": "cf6abd2_2", "status": "completed"}}` at **step 15**, after a readback displaying the incorrect state. It used 16 of 32 reply steps and ended normally, with 16 reply steps unused. The actor trace clock is model=61.6 s, commands=10.2 s, total=71.8 s, below the existing 900-second deadline. Thus **this loss is not evidence of exhausted step/output/deadline budget**; neither truncation/retry exhaustion nor provider/scorer failure explains it.

There is also a task-facing completion gap: the supervisor documentation returned at step 7 advertises `complete_task`, but none of the 14 logged API routes submits supervisor completion. The final harness answer does not become a supervisor API call in this recording. The generic public runtime prompt tells the actor to give its final answer through the harness; the scorer measures the replayed world's answer. The observed missing answer and semantic state error are distinct failures. This source/trace diagnosis is recorded without modifying the prompt, translating the final answer, replaying corrected actions or feeding task-specific hints back to the actor.

**Success reachability at this budget remains unestablished, not disproved.** One deterministic selected task/trial failed; this cannot establish that all 9B actors/tasks fail, that success is impossible, or a success-rate estimate. This run demonstrates substantial action reachability within spare budget, but no authentic successful actor-task receipt. The earlier `w-appworld` raw loss and historical 16/32-step zeros are retained; the released-predicate gold-answer controls in `w-awp` establish scorer positives only. None establishes full actor success. No budget increase or tuning is justified or performed here, and no further sampling was launched to search for a passing task.

## Lifecycle, resources, retained artifacts and comparison limits

`container-observations.jsonl` contains passive run-owned inspections; `role-observations.json` is their derived index. Actual image IDs:

- Actor `197642c60a8eccf947517d8ef776c69ee93c5f1c6816ae8d61a0e6c52ff54769`: `sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb`.
- Service `320faecb491c76b28eb404cc432f0dbbfc3c5616a2bf205202ed7ffd49516377`: `sha256:db09e34ec90347e7d5271d95445f360222ba1aef3ca0eaa5614ce768bf23aca4`.
- Verifier `bc0bd9baada9ed710fe3412f572828bcead44be7393ee9d9e091226768190fcc`: `sha256:f3cb916b9bdc297d4c73dd9646e9550befb7c6e23827d937b90a0640373e97ca`.

All three observed Docker configurations have NanoCpus=2000000000, Memory=4294967296 bytes, PidsLimit=1024. Actor/verifier root filesystems are read-only, with 512 MiB `/tmp` tmpfs; service root is writable. `live-cgroups.json` independently samples actor/service `cpu.max=200000 100000`, `memory.max=4294967296`, `pids.max=1024`; no live verifier cgroup file was sampled. The prior report's service lacked these caps; this worktree's inherited baseline already applies them. This evidence-only task did not add them. No filesystem quota enforcement is claimed.

`network-observations.jsonl` preserves the owned internal service network. All roles attach to `hg-w-appworld2-20261015T050036Z-a6d55a63-cf6abd2_2`; service recording is RW for the service and RO for the verifier, with no actor recording bind. `docker-events.jsonl` records actor/verifier exit 0 and destruction; service is killed during teardown after the verifier exits, exit 137 with `OOMKilled=false`. It is not classified as a scorer OOM or substituted with a task loss.

`cleanup-observations.json` records exact absent-after Docker inspections for all three role IDs and the owned network, no run-labeled objects, and temporary task/verdict directories absent. `cleanup-processes-after.json` records sampled host PIDs absent and the external work/run roots retained. These observations cover this run's roles, not an exhaustive daemon/namespace audit or a trusted platform lifecycle attestation. Boundary/interpreter preflight containers are outside this collector's run-owned identity filter.

All **25** original run files remain intact at the external run directory and have byte-identical committed review copies under `execution/`; `artifact-copy-provenance.json` records original/copy paths, lengths and recomputed hashes. Worktree staging and service recording remain retained. `packet-sha256.json` inventories final packet bytes. Endpoint sharing, unknown model weights, platform dirty state, unobserved production enforcement and the changed inherited service envelope preclude an equivalence or model-improvement claim against older runs. This task adds one failed raw observation only.

## Exact registry observation - w-appworld2

Only `appworld / successful-task-evidence` receives this new bounded observed suffix and report/journal evidence. Status remains `measurement`; family, next_action, historical runs, dataset taxonomy/shared references, shared blockers and production authorization remain unchanged.

Fresh unchanged-budget diagnostic `w-appworld2-20261015T050036Z-a6d55a63` selected deterministic study task `cf6abd2_2` from 147 eligible, one trial/jobs=1/rounds=0 on `http://127.0.0.1:8002/v1` (`qwen3.5-9b-local`). Raw official TGC was measured=true, success=false, score=0.0, six passes/two failures across eight requirements; the actor made an incorrect note edit and omitted supervisor completion. The actor submitted at step 15 after 16 model calls within its unchanged 32-step/2048-output-token budget; spare budget demonstrates action reachability but neither success nor impossibility. Formal staged score remains null, measured=0/unmeasured=1/invalid=0 for policy_enforcement_not_observed. No successful actor-task receipt exists in this attempt; successful-task-evidence remains OPEN. No shared blocker closes and no expansion is authorized.

Validation commands/output are retained in `reports/w-appworld2-validation.txt`; packet audit checks original/copy hashes, full packet identities, raw-vs-formal outcomes, unchanged runtime/shared/other-dataset records, verbatim evidence locators and journal heading. Registry source tests run without another model/benchmark execution.
