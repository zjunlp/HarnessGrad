# B9 — AppWorld protocol review and shared pilot guidance

Date: 2026-10-15. Reviewed base: `9066072`. Work confined to the registered
`/tmp/hg-b9` worktree. Task A owns only AppWorld; Task B changes shared planning
documentation. No adapter, prompt, CLI, harness, scorer, budget, model, endpoint,
run ID or historical receipt was changed. No production runtime or benchmark ran.

## AppWorld: source review DONE; successful-task gate remains measurement

The task-facing protocol is present and generic. This establishes an available
interface, not successful execution of a real task:

- `data/datasets/appworld.py:202` takes the released instruction as the goal.
  The public prompt at `:206` shell-quotes only the opaque task ID and explains
  one initialization, persistent service-side Python/`apis`, whole snippets
  with multiple API calls, documentation discovery, requested state changes and
  final submission. `_setup` at `:242` exposes the CLI and this public prompt.
  It contains no selected-task solution or special method branch.
- `eval/run/task_contract.py:5` appends only `runtime_prompt` to the actor goal;
  source paths and verifier metadata are not projected by that function.
- `data/datasets/appworld/cli/aw:25` documents the service namespace. Its dispatch
  at `:45` initializes once per task marker and forwards the supplied snippet
  unchanged to `/execute`. `data/datasets/appworld/service.py:37` binds
  initialization to the platform task, disables ground-truth loading and returns
  public supervisor/time metadata; `:59` executes through the persistent world.
  Source declarations do not attest an effective deployed service boundary.
- `tests/quality/test_appworld_actor_protocol.py:29` extracts only public helper
  ASTs rather than importing the AppWorld task pool. Tests at `:48` and `:62`
  check public-field delivery, task independence and shell quoting. Tests at
  `:197` and `:254` use a scripted actor and fabricated local service to check
  discovery, state persistence and multi-call snippet forwarding. Their success
  cannot establish real-model behavior or official scorer execution. The exec
  cases at `:80` assert current admission refusal, not live container delivery.

Retain the following journal evidence exactly as recorded (line references
include the new current-policy notice). The cited external receipt bodies were
not opened: repository citations and summaries are the verified scope.

| Preserved record | Journal evidence | Disposition |
| --- | --- | --- |
| Initial actor launch, Docker exit 125; repaired eight-call attempt made no AppWorld API calls and scored 0.0 across five checks | `docs/adapter-integration-journal.md:478`, especially `:482`–`:483` | Initial incomplete attempt stays incomplete with null effect; the repaired measured zero does not replace it. |
| Protocol repair: four authoritative calls; no completed answer and later parse failures | journal `:489`, especially `:493`–`:495` | Historical protocol progress, still zero; not successful-task evidence. |
| Generic robustness smoke: eight commands, seven API calls, documentation discovery without a state change or final submission | journal `:500`, especially `:503`–`:507` | Source/loop progress does not close the task gate. |
| Sixteen-step smoke: 15 API calls, 41s, measured 0.0; one trial, jobs=1, output cap 2048 | journal `:510`, especially `:513`–`:517` | Retain `appworld-smoke-final-v6-20261117`, reported complete=true, n_trials=1, trial_scores=[0.0], API-log size 1507 bytes. |
| Thirty-two-step smoke: 26 API calls, 57s, measured 0.0; one trial, jobs=1, output cap 2048 | journal `:520`, especially `:523`–`:526` | Retain `appworld-smoke-final-v7-20261117`, reported complete=true, n_trials=1, trial_scores=[0.0], API-log size 2638 bytes. Budget escalation did not close the task gate. |

The historical task ID is cited only as part of preserved evidence, never used
to select, tune or rerun a task. The journal's external paths and inherited
20261117 date strings are preserved without reconciliation or freshness claims.
No new score, successful-task receipt or comparability claim is created.

What is missing: a checked, authentic successful measured actor-task receipt
binding actual task-facing actions and completion to official scorer evidence.
API-call counts, a nonempty log, healthy execution, `complete=true` or scalar
progress scores cannot supply success. `data/datasets/appworld/verify/check.py:29`
declares `appworld.task_goal_completion`; `:56` evaluates the replay and `:61`
records measured/Boolean success/test facts, while `:12`, `:32` and `:59` keep
missing-log/environment failures unmeasured. This is source inspection only.
The checked-in source audit `docs/appworld-local-adapter-audit-codex229.md:98`
also identifies missing original trial and effective production identities; its
production receipt requirements at `:134` remain unresolved. Public evaluator
source acquisition and saved-writer preservation are not successful-task evidence.

Registry outcome: retain `successful-task-evidence`, family `actor-protocol`,
status `measurement`, and its original next action. Add this review as evidence;
retain the 16/32-step measured zeros and unresolved successful-task gate. The
original incomplete historical record and every shared reference remain intact.
There is no task tuning, budget increase, new diagnostic or expansion approval.
A first future diagnostic still requires all admission gates; successful-task
evidence remains the separate expansion gate described in the journal.

## Shared guidance: DONE for documentation only

The controlling rule is `AGENTS.md:61`–`:66`: first one task/one trial after
all gates pass; separate review of a valid measured diagnostic receipt before
expansion; temporary reruns preselect `min(30, ceil(10% * eligible))` tasks with
`trials=1`, `jobs=1`; preserve old/incomplete receipts and use fresh run IDs.
`pilot-guidance-conflict` is retained as `done` in the registry, with repository
evidence. All existing dataset references remain, and other shared gates stay
open. This is no runtime or measurement pass and grants no H0 authorization.

Search covered tracked Markdown, reStructuredText, text and HTML documents,
including ignored-but-tracked docs, with `git ls-files -z` piped into `rg`.
Patterns covered `min(50, ...)`, 50-task phrasing, 50×3/9×3 variants, three-trial
pilot phrasing and current/default capped-pilot advice. Matches were read in
context; numbers used for unrelated split design or test counts are not pilot
guidance. The complete relevant document inventory follows.

| File and evidence | Current vs historical finding; action |
| --- | --- |
| `docs/platform-improvement-log.md:30`, `:45` | The current reuse table still called 50 the new-pilot default despite its policy preamble. Replace the table rule with staged 1×1, separate receipt review and the 30/one-trial/one-job cap. Keep C-001's original contract, checks and commit verbatim; insert an explicit supersession notice before them. |
| `docs/dataset-onboarding-handoff.md:189`, `:261` | “Current pilots score 50” blurred the retained observation with current planning. Identify it as a historical sample-size observation, preserve 50, point to §9; add the separate valid-receipt review requirement to §9. The 50-task coverage observation at `:207` (now `:208`) already says “have been run” and remains unchanged. |
| `docs/adapter-integration-journal.md:7` | Add a current-policy notice superseding old sizing advice without editing any original paragraph. This covers the former capped-pilot advice at original `:321`/`:343`, AppWorld's 9x3 “Remaining gates” at original `:464`, `:475`, `:486`, `:496`, `:506`, `:515`, and Chess's prospective 50×3 at original `:589`. Actual Bird, DS1000, ScienceWorld, HotpotQA, MuSiQue, 2Wiki, GAIA and AppWorld scopes/results remain historical, with their scores/IDs/receipts untouched. Append this scoped review to the journal. |
| `docs/dataset-blockers.md` | Update the live registry guide to describe retained `done` scope and this documentation closure, while explicitly retaining AppWorld's measurement gate. Its old conflict description is live index guidance, not a pinned receipt. |
| `AGENTS.md:61`–`:66` | Leave unchanged: controlling current rule, explicitly prohibits reverting to historical sizing. |
| `reports/DIAGNOSTIC_POLICY.md:12`–`:16`, `:28`–`:44` | Leave unchanged: “Observed gaps at the reviewed base” describes the historical 50 cap; implemented policy already uses diagnostic-first and 30/one-trial with separate review. |
| `docs/task-211-terminal-operator-backend-packet.md:303`–`:308` | Leave unchanged: current diagnostic/review/cap rules are explicit and historical sizing is expressly rejected as a fallback. |
| `docs/task-223-dry-diagnostic-launch.md:253`–`:259` | Leave unchanged: expressly disallows historical 50/three-trial fallback; grants no authorization. |
| `docs/task-234-diagnostic-readiness.md:9`–`:14`, `:66`, `:99`, `:121`–`:123` | Leave unchanged: pinned reviewed HEAD, retained history and express rejection of old 50×3/9×3 planning. Its then-active blocker finding records that HEAD, not today's registry state. |
| `docs/task-236-diagnostic-readiness.md:5`–`:9`, `:43`, `:62` | Leave unchanged: pinned reviewed HEAD/companion snapshot; the old active-conflict finding and historical completed 9×3 record must remain evidence of that review. It already denies old sizing authorization. |

Companion JSON snapshots (`docs/task-234-diagnostic-readiness.json`,
`docs/task-236-diagnostic-readiness.json`)
and `tests/fixtures/dataset_blockers/history-codex178.json` are preserved evidence,
not current planning instructions; no historical hashes or baselines change.
`reports/stage-d-surface-inventory.md` is a retained source/payload excerpt
inventory, not a launch guide. `docs/eval-split-design.md:3`, `:170`–`:180` and
`:205`–`:207` concern partition sizes, experiment cost and repeated trials for the
`score(N)` study, not the staged pilot cap; those statements are unchanged and
cannot override the AGENTS.md staged one-trial rule. Historical run-cost examples
in `INTERFACE.md:2429` and `:2474` likewise describe measured five-task/three-trial
runs, not new-run sizing. Current 30-cap guidance in `docs/user-setup.md:43`,
`docs/gaia-preflight-codex175-report.md:187`,
`docs/gaia-operator-packet-codex210.md:125`,
`docs/task-211-terminal-operator-backend-overview.md:57` and
`docs/task-243-diagnostic-readiness.md:127`–`:142` already agrees with staged
planning and needs no sizing edit.

Supporting changes: `tools/check_dataset_blockers.py` accepts the retained `done`
work status; `tests/quality/test_dataset_blockers.py` checks that such an entry
still needs repository evidence, remains counted, and leaves AppWorld at
`measurement`. The checker still rejects production authorization and preserves
all 11 pinned historical records. No blocker or shared reference is deleted.

## Validation

Executed only these focused tests:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  -p no:cacheprovider tests/quality/test_dataset_blockers.py \
  tests/quality/test_appworld_actor_protocol.py
python3 tools/check_dataset_blockers.py
git diff --check
```

Result: **68 passed in 6.77s**, no failures or skips. Pytest used its default
temporary directory outside the platform tree. Protocol tests used fabricated
inputs/scripted replies and a local synthetic HTTP service, with no model,
production service, Docker or official evaluator. Registry validation printed
`{"valid": true, "datasets": 28, "shared_blockers": 8, "historical_records": 11, "production_authorized": false}`.
The eight shared records include the retained documentation-only `done` entry;
this is not eight active blockers. `git diff --check` passed. No whole suite ran.

Diff review confirms only the shared pilot-guidance object and AppWorld
observation/evidence change in the registry. All other dataset objects, shared
objects, shared references, AppWorld's original next action, historical records
and the independent historical baseline are unchanged. Existing journal paragraphs
are preserved verbatim beneath the added supersession notice. No receipt was
resumed, overwritten, deleted or relabeled.
