# Three dataset decisions — w-decisionpack

This is a choice sheet, not a recommendation or authorization. Each question accepts one word: **HOLD** (change nothing), **RECORD** (recognize completed work and retain the gate), **LIMIT** (retire the gate with its missing evidence named), or **SEEK** (request separately bounded work toward the original gate). All proposed operator statements below are conditional on that choice; none records a decision already made.

Evidence basis: the current worktree snapshot is recorded in `registry/w-decisionpack-validation.txt`. **FOUND** means a cited retained file was read; it does not mean its historical execution was repeated. **ABSENT** is restricted to an enumerated search; **UNKNOWN** includes unsearched/private upstream records and external artifact survival. Measurements attributed to w-evidence, w-bfclattest, w-trivia3/4 or the parent are theirs, not this worker's measurements. The requested `docs/paper-fidelity-audit-handoff.md` is **ABSENT at that exact worktree path** (existence and tracked-path commands in validation); its retained excerpts are **FOUND** in `registry/w-evidence-external-sources.md:1279` and `:1312`. The original outside this worktree is **UNKNOWN to this worker**.

**Integration boundary for every option:** the JSON below uses the single-record shape accepted by `tools/apply_blocker_proposal.py:10-17` and `:122-129`. The tool appends `observed`, replaces `next_action`, and cannot change status or remove an open blocker (`:7-8`, `:218-221`). Consequently LIMIT needs a separate parent closure disposition/removal; its prose alone would leave the blocker counted open. HOLD means no proposal and no write: an identical replacement is refused (`:157-158`). Replacements that retire obligations require the parent's explicit clause acknowledgements (`:159-166`, `:188-191`); no force or application is requested here. Evidence/history remain preserved. These are embedded reviewable texts, not applied proposal files.

## appworld

### 1. What is actually owed

The decisive obligation is the following current sentence, **AW7** (`registry/dataset-blockers.json:96`):

> Source review, API activity and synthetic protocol controls cannot satisfy the gate: only a genuine successful measured actor task under the staged pilot policy can do so, and this record authorizes no run and no expansion.

Every other current sentence, including its preservation, budget, review and disclosure obligations, follows verbatim. IDs identify sentences in the current field, not new requirements (`registry/dataset-blockers.json:96`).

<details><summary>All current next_action sentences (AW1–AW11)</summary>

**AW1**

> Retain all measured-zero and staged-unmeasured receipts, the incomplete Docker exit-125 attempt with effect=null, and keep successful-task-evidence OPEN (actor-protocol / measurement); preserve the distinction between a raw official TGC loss and the staged score=null with measured/unmeasured/invalid=0/1/0 for policy_enforcement_not_observed.

**AW2**

> The full task-independent fabricated-control set named here is now implemented and passing in tests/quality/test_appworld_completion_control.py: the completion-aware world that separates the actor/guidance boundary from a recording/verifier wiring fault from a task-state failure, the marker/working-directory persistence hazard driven through the shipped CLI with a negative control, and the live-versus-replayed recording differential including the refused-API case where HTTP succeeds and the world does not change.

**AW3**

> Review that set rather than re-deriving it; new controls are no longer the outstanding item.

**AW4**

> Any further protocol work stays subject to the original constraints: no selected-task tuning, no rerunning the selected task, and no increase to its step or token budget.

**AW5**

> Review the generic findings in registry/w-appworld-protocol-review.md: the public prompt directs the final answer through the harness while the official checker only replays recorded API calls and evaluates the replayed world, so a task whose state changes are correct can still return a measured zero when its in-world completion is omitted (data/datasets/appworld.py:234-236; base_harness/loop/agent.py:679-682,747; data/datasets/appworld/verify/check.py:23-27,47-56); supervisor completion requests are ABSENT over the enumerated retained-log scope while discovery does advertise complete_task, and the relative `MARKER = pathlib.Path(".appworld_task")` with implicit re-initialization is a source-level reset hazard whose occurrence in these runs is UNKNOWN (data/datasets/appworld/cli/aw:11,50-52).

**AW6**

> A fresh authorized diagnostic has been executed and it did NOT confirm the guidance hypothesis: with the in-world completion boundary named in the actor guidance, the run recorded no supervisor completion request and still returned a raw measured loss, while the staged layer published score=null for policy_enforcement_not_observed.

**AW7**

> Source review, API activity and synthetic protocol controls cannot satisfy the gate: only a genuine successful measured actor task under the staged pilot policy can do so, and this record authorizes no run and no expansion.

**AW8**

> The withdrawn shared requirements remain recorded limits and are not re-imposed as a prerequisite for this review.

**AW9**

> Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (appworld) and registry/w-evidence-disclosure.md as source-only metric disclosures.

**AW10**

> Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument.

**AW11**

> This append grants no measurement, closure or run authorization and waives no existing obligation.

</details>

### 2. What is already satisfied

| Sentence / obligation | FOUND evidence and exact limit | Sentence to change if accepted |
| --- | --- | --- |
| AW2–AW3: fabricated controls and their review | The parent records completion of the marker/cwd, refused-API and live/replayed controls and reports 108 focused passes, with merge `535d3464` (`docs/adapter-integration-journal.md:4638-4642`). The source names all twelve controls (`tests/quality/test_appworld_completion_control.py:209`, `:223`, `:236`, `:248`, `:257`, `:269`, `:295`, `:328`, `:342`, `:361`, `:370`, `:382`). This worker read names, not executed tests. | AW2–AW3 already say done; retain them. The older “proposed and unexecuted” controls sentence in `registry/w-appworld-protocol-review.md:105` is historical and superseded by the parent's completion report, not an outstanding control request. |
| AW5 review / AW6 guidance hypothesis | The parent correction reports that `appworld-diag-20261015T094842Z-3550c7c4` has fifteen calls ending in `POST /supervisor/message`, the completion endpoint; the parent replay reports six passes/two task failures, not a lost completion. It explicitly attributes the fourteen-call, no-completion log to W1 (`docs/adapter-integration-journal.md:4792-4794`; incorporated correction at `registry/dataset-blockers.json:63`). Current generic guidance names in-world completion (`data/datasets/appworld.py:239-243`). | AW6's entire guidance-hypothesis sentence, quoted verbatim above, is superseded. AW5's completion-ABSENT clause must be confined to the earlier enumerated W1/W2 scope, not the later diagnostic. RECORD supplies both replacements. |
| AW1: historical receipt retention and raw/staged distinction | Retained V6/V7/W1/W2 review identifies measured losses, staged nulls, their distinct scopes and the exit-125 incomplete attempt (`registry/w-appworld-protocol-review.md:53-60`). The current record retains the later failed diagnostic and its staged 0/1/0 (`registry/dataset-blockers.json:63`). These are retained reports, not a new completeness audit of every external receipt. | Retain AW1. No sentence needs to drop receipt retention; it is an ongoing preservation duty. |
| AW9: metric disclosure | w-evidence's source-only qualification distinguishes single-task TGC from implemented SGC, split-specific reporting and changed guidance; it asserts no successful measured actor task (`registry/w-evidence-qualification.md:104-123`; `registry/w-evidence-disclosure.md:12`). | Retain AW9–AW10. The dated forward assessment's claim that guidance was not yet changed is superseded for the current source, not erased from history (`registry/w-evidence-qualification.md:114-123`). |

**Not satisfied:** AW7's genuine successful measured actor task is not established by the current record; the parent correction reports a task failure, and the staged outcome remains null (`registry/dataset-blockers.json:63`). AW4/AW8/AW10/AW11 are continuing constraints, not one-time achievements. No newly verified external receipt, successful run or proof that success is impossible is claimed. The w-evidence proposal still copies the superseded AW6 into its replacement (`registry/proposals/w-evidence-appworld.json:5`); applying it unchanged would retain the wrong premise.

The inherited completion-absence scope is exactly the two W1/W2 recorded API logs identified by commit, prefix and log suffix at `registry/w-appworld-protocol-review.md:13-25`; its retrieval command is `git show <commit>:<prefix>/<suffix>` with those enumerated values. The reported outcomes are fourteen calls and no completion in each earlier log (`:55-56`), not in every AppWorld run. Those objects were not retrieved again here; the parent correction for the later diagnostic remains controlling.

### 3. Enumerable options

| Answer | Honest claim | Explicitly does not claim | New obligation / cost | Supporting evidence already on disk |
| --- | --- | --- | --- | --- |
| **HOLD** | Make no change and keep the original successful-task gate open. | No success, no authorization, no correction of the stale action text. | No new obligation; AW1–AW11 continue, including their internal corrected-observation/stale-action conflict. No execution cost now. | Current field and parent correction (`registry/dataset-blockers.json:63`, `:96`). |
| **RECORD** | Accept the controls and guidance/replay finding as completed; correct the stale action text; keep AW7 open. | Neither synthetic success nor faithful replay of a failure is a successful measured actor task. | Documentation integration only; no new execution or budget. Existing success, retention and qualification duties remain. | Parent correction/control report and current guidance cited in §2. |
| **LIMIT** | Retire the demand for a successful actor task, preserving “successful measured actor task unestablished” as a standing limit. | It does not satisfy AW7, prove model impossibility, convert null to zero, authorize admission or establish benchmark comparability. | No experiment. Parent must explicitly withdraw AW1's open disposition and AW7's success requirement, archive the limit and close separately; any future renewed success claim requires fresh evidence. | Current failure/staged-null record and w-evidence disclosure (`registry/dataset-blockers.json:63`; `registry/w-evidence-qualification.md:108`). |
| **SEEK** | Request preparation of one fresh diagnostic under the corrected interpretation and original constraints. | No promised success, selected-task retry, tuning, budget increase, policy waiver, production authorization or expansion. | Requires a separately bound operator authorization, fresh run ID, a different preselected eligible task, unchanged 32-step/2048-output-token ceiling, one trial/jobs=1, local-model GPU time, service/verifier CPU/container time and receipt review. External network/authentication needs and elapsed cost are UNKNOWN until runtime availability is checked; no paid endpoint. | Existing budgets/endpoint and failure identities (`registry/dataset-blockers.json:63`, `:96`); staged policy and receipt requirements (`AGENTS.md:57`, `:61-65`, `:77`); verifier path/timeout (`registry/w-appworld-protocol-review.md:45`). |

Exact registry text for RECORD, LIMIT and SEEK follows. HOLD writes nothing. RECORD/SEEK preserve AW1–AW4 and AW7–AW11; only AW5/AW6's superseded interpretation changes. Parent review must acknowledge those two superseded sentences if the prose guard flags them; their full verbatim text is above. LIMIT deliberately withdraws the success gate and open disposition, not receipt preservation or future scientific claim limits. Its separately required closure has no supported `status` field in this proposal shape (`tools/apply_blocker_proposal.py:7-8`).

<details><summary>RECORD — exact proposal text</summary>

```json
{
  "dataset": "appworld",
  "blocker": "successful-task-evidence",
  "observed_append": "Operator accepts the retained parent correction and completed fabricated-control review as documentary findings: the later diagnostic performed, recorded and replayed completion, while its two task failures and staged null remain. The no-completion observation belongs to earlier W1/W2 scopes, not that later diagnostic. No successful measured actor task is established (docs/adapter-integration-journal.md:4638-4642,4792-4794; registry/w-evidence-qualification.md:104-123).",
  "next_action_replacement": "Retain all measured-zero and staged-unmeasured receipts, the incomplete Docker exit-125 attempt with effect=null, and keep successful-task-evidence OPEN (actor-protocol / measurement); preserve the distinction between a raw official TGC loss and the staged score=null with measured/unmeasured/invalid=0/1/0 for policy_enforcement_not_observed. The full task-independent fabricated-control set named here is now implemented and passing in tests/quality/test_appworld_completion_control.py: the completion-aware world that separates the actor/guidance boundary from a recording/verifier wiring fault from a task-state failure, the marker/working-directory persistence hazard driven through the shipped CLI with a negative control, and the live-versus-replayed recording differential including the refused-API case where HTTP succeeds and the world does not change. Review that set rather than re-deriving it; new controls are no longer the outstanding item. Any further protocol work stays subject to the original constraints: no selected-task tuning, no rerunning the selected task, and no increase to its step or token budget. Review the generic findings in registry/w-appworld-protocol-review.md as historical W1/W2 findings: their omitted in-world completion can yield a raw TGC loss despite a harness answer, because the official checker replays recorded API calls and evaluates the replayed world. Supervisor completion requests were ABSENT within that earlier enumerated retained-log scope, not in the later appworld-diag-20261015T094842Z-3550c7c4 log. Preserve the source-level reset hazard from the relative `MARKER = pathlib.Path(\".appworld_task\")` and implicit re-initialization; its occurrence in the retained actor runs remains UNKNOWN. Current guidance explicitly requires discovered in-world completion before the harness report (data/datasets/appworld.py:239-243). The parent correction supersedes the earlier claim that appworld-diag-20261015T094842Z-3550c7c4 recorded no supervisor completion request: it records fifteen calls ending in POST /supervisor/message, and completion was performed, recorded and replayed. The parent reports reward 0.0 with six passes and two failures on answer convention and note content; the staged layer remains score=null for policy_enforcement_not_observed (docs/adapter-integration-journal.md:4792-4794; registry/dataset-blockers.json:63). This is actor-task failure evidence, not an established replay defect or a successful measured actor task. Source review, API activity and synthetic protocol controls cannot satisfy the gate: only a genuine successful measured actor task under the staged pilot policy can do so, and this record authorizes no run and no expansion. The withdrawn shared requirements remain recorded limits and are not re-imposed as a prerequisite for this review. Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (appworld) and registry/w-evidence-disclosure.md as source-only metric disclosures. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument. This append grants no measurement, closure or run authorization and waives no existing obligation."
}
```

</details>

<details><summary>LIMIT — exact proposal text</summary>

```json
{
  "dataset": "appworld",
  "blocker": "successful-task-evidence",
  "observed_append": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limit: successful measured actor task unestablished (registry/dataset-blockers.json:63,96).",
  "next_action_replacement": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limit: no authentic successful measured actor task is established; the retained actor attempts are losses or staged-unmeasured outcomes (registry/dataset-blockers.json:63; docs/adapter-integration-journal.md:4792-4794). Withdraw the demand to keep successful-task-evidence open until such a task succeeds; this is scope retirement, not satisfaction of that gate or a claim that success is impossible. Retain every measured-zero, staged-null and incomplete exit-125 receipt and their raw/staged identities. Preserve the completed controls, parent correction, historical W1/W2 scope and marker reset hazard with actual run occurrence UNKNOWN. No selected-task tuning, no selected-task rerun and no step/token budget increase is authorized. Preserve the withdrawn-shared-requirement limits and source-only metric disclosures in registry/w-evidence-qualification.md and registry/w-evidence-disclosure.md; bind any future number to actual scorer/channel/split/trial-mean/budget and never relabel an earlier number. Any future reopening requires fresh staged-policy evidence and separate authorization; no run or expansion is authorized."
}
```

</details>

<details><summary>SEEK — exact proposal text</summary>

```json
{
  "dataset": "appworld",
  "blocker": "successful-task-evidence",
  "observed_append": "Operator accepts the retained parent correction and completed fabricated-control review as documentary findings: the later diagnostic performed, recorded and replayed completion, while its two task failures and staged null remain. The no-completion observation belongs to earlier W1/W2 scopes, not that later diagnostic. No successful measured actor task is established (docs/adapter-integration-journal.md:4638-4642,4792-4794; registry/w-evidence-qualification.md:104-123). Operator requests only a separately bounded work packet; actual access/execution authorization remains outstanding.",
  "next_action_replacement": "Retain all measured-zero and staged-unmeasured receipts, the incomplete Docker exit-125 attempt with effect=null, and keep successful-task-evidence OPEN (actor-protocol / measurement); preserve the distinction between a raw official TGC loss and the staged score=null with measured/unmeasured/invalid=0/1/0 for policy_enforcement_not_observed. The full task-independent fabricated-control set named here is now implemented and passing in tests/quality/test_appworld_completion_control.py: the completion-aware world that separates the actor/guidance boundary from a recording/verifier wiring fault from a task-state failure, the marker/working-directory persistence hazard driven through the shipped CLI with a negative control, and the live-versus-replayed recording differential including the refused-API case where HTTP succeeds and the world does not change. Review that set rather than re-deriving it; new controls are no longer the outstanding item. Any further protocol work stays subject to the original constraints: no selected-task tuning, no rerunning the selected task, and no increase to its step or token budget. Review the generic findings in registry/w-appworld-protocol-review.md as historical W1/W2 findings: their omitted in-world completion can yield a raw TGC loss despite a harness answer, because the official checker replays recorded API calls and evaluates the replayed world. Supervisor completion requests were ABSENT within that earlier enumerated retained-log scope, not in the later appworld-diag-20261015T094842Z-3550c7c4 log. Preserve the source-level reset hazard from the relative `MARKER = pathlib.Path(\".appworld_task\")` and implicit re-initialization; its occurrence in the retained actor runs remains UNKNOWN. Current guidance explicitly requires discovered in-world completion before the harness report (data/datasets/appworld.py:239-243). The parent correction supersedes the earlier claim that appworld-diag-20261015T094842Z-3550c7c4 recorded no supervisor completion request: it records fifteen calls ending in POST /supervisor/message, and completion was performed, recorded and replayed. The parent reports reward 0.0 with six passes and two failures on answer convention and note content; the staged layer remains score=null for policy_enforcement_not_observed (docs/adapter-integration-journal.md:4792-4794; registry/dataset-blockers.json:63). This is actor-task failure evidence, not an established replay defect or a successful measured actor task. Source review, API activity and synthetic protocol controls cannot satisfy the gate: only a genuine successful measured actor task under the staged pilot policy can do so, and this record authorizes no run and no expansion. The withdrawn shared requirements remain recorded limits and are not re-imposed as a prerequisite for this review. Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (appworld) and registry/w-evidence-disclosure.md as source-only metric disclosures. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument. This append grants no measurement, closure or run authorization and waives no existing obligation. Operator requests preparation of a separately bounded one-task/one-trial/jobs=1 diagnostic packet after applicable dataset gates pass, using a fresh run ID, a different preselected eligible task, the existing local endpoint/model and unchanged 32-step/2048-output-token ceiling. Preserve the no-selected-task-tuning/no-selected-task-rerun rule, raw-versus-staged distinction, required identity/coverage/resource/cleanup evidence and separate valid-receipt review before expansion. Packet preparation is not run authorization; bind actual runtime/network needs, budget and acceptance criteria for a separate operator authorization before execution. Do not convert staged null or a failed/invalid/unmeasured trial into successful-task evidence."
}
```

</details>

### 4. Observation needed for outright closure

A fresh, genuine actor task must produce recorded actions whose independent official replay succeeds, with a valid measured staged diagnostic receipt rather than `score=null`/unmeasured. It must bind actual metric, selection/eligible/task/trial coverage, source/split/manifest/model/harness identities, endpoint, unchanged budgets, invalid/unmeasured counts, cleanup/snapshot outcomes and comparability notes; separate valid-receipt review precedes expansion (`registry/dataset-blockers.json:96`; `AGENTS.md:57`, `:61-65`, `:77`). This states the current success gate, without re-imposing the withdrawn shared admission gates (AW8).

**Obtainability: UNKNOWN prospectively; not ruled out by the failed task.** The parent reports faithful measurement of a real failure, not a success or an impossibility theorem (`docs/adapter-integration-journal.md:4793`; `registry/dataset-blockers.json:63`). Existing source-only/control evidence cannot supply the missing observation (AW7). HOLD/RECORD retain that demand, SEEK attempts it subject to separate authorization, and LIMIT explicitly gives it up.

### 5. Decision

Which of A / B / C / D? **A = HOLD / B = RECORD / C = LIMIT / D = SEEK**

## bfcl_v3

### 1. What is actually owed

The central execution obligation is **BF13**, with an additional exact-build requirement in **BF2** (`registry/dataset-blockers.json:195`):

> Preserve selected-only/privacy boundaries and full-call selected reference/schema information, contain expression evaluation, satisfy the technical source gate and later derive/validate the full-call contract and record actual official scorer execution with exact source/runtime/input/metric/receipt identities.

> Acquire producer-authenticated compiled-build manifests bound to the exact retained archive/member hashes, naming CPython build implementation/version/SOABI, source revisions, compiler/flags and compiled inputs.

The whole field is retained verbatim below because obtaining build manifests alone would not discharge runtime, containment, privacy, handler or official-scorer duties (`registry/dataset-blockers.json:195`).

<details><summary>All current next_action sentences (BF1–BF18)</summary>

**BF1**

> Reuse the existing exact source/runtime custody and updated canonical per-target frontier in registry/w-bfclrt-custody.md and tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl.

**BF2**

> Acquire producer-authenticated compiled-build manifests bound to the exact retained archive/member hashes, naming CPython build implementation/version/SOABI, source revisions, compiler/flags and compiled inputs.

**BF3**

> PyPI publication attestations, supplied-leaf signature verification, SBOM declarations and static SONAME availability are partial evidence only.

**BF4**

> Later authorized runtime work must observe exact per-parent loader/dlopen choices and bind complete component licence/NOTICE texts to the exact compiled inputs.

**BF5**

> Resolve the strict websockets provenance filename mismatch without substituting another archive.

**BF6**

> Preserve the handler/config/expression-containment, selected-only/privacy/full-schema/reference and official scorer/full-call work for separately authorized workers.

**BF7**

> Preserve frozen receipts and the function-name diagnostic; external/open, no production H0.

**BF8**

> Use the measured source graph, candidate artifact hashes, host ABI identities and native/license custody already recorded in tests/fixtures/hg_bfclrt; do not reacquire established archive/tag/commit/member/root-license custody.

**BF9**

> Resolve the 87 exact compiled-build UNKNOWNs enumerated in the updated per-target frontier tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl, including any remaining exact member filenames/SHA-256 and their actual transitive graph; the wandb==0.18.5 entry is required only if the optional logging branch is selected.

**BF10**

> Supply observed filenames/SHA-256, compatible CPython/Linux x86_64 ABI and grammar/API bindings, exact compiled build provenance, unresolved linked/dlopen libraries and version-matched license/NOTICE scope.

**BF11**

> Availability outside the enumerated local scope is unobserved.

**BF12**

> Document and bind the concrete local-model handler, response grammar and model-config behavior before determining its branch-specific runtime.

**BF13**

> Preserve selected-only/privacy boundaries and full-call selected reference/schema information, contain expression evaluation, satisfy the technical source gate and later derive/validate the full-call contract and record actual official scorer execution with exact source/runtime/input/metric/receipt identities.

**BF14**

> No scorer execution or full-call differential was performed by w-bfclrt.

**BF15**

> Leave this blocker external/open.

**BF16**

> Treat the retained prompting decoder's `eval(ast.unparse(value))` at tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/utils.py:302 as a named containment target that must be bounded or bypassed before any official-scorer execution; the platform's own text boundary holding does not bound the upstream decoder.

**BF17**

> The containment target above now has a scoped reachability result (ABSENT over 317 enumerated platform files, UNKNOWN outside them) and an unwired preflight; any future official-scorer execution must either use that preflight or record why it does not, and must not read this ABSENT result as proof of general unreachability.

**BF18**

> The preflight gate is deliberately unwired in the platform (see this record's observed field, parent decision 2026-10-16); a future official-scorer execution uses `guarded_decode` from tests/fixtures/hg_bfclguard/gate.py or records why it does not.

</details>

### 2. What is already satisfied

| Sentence / obligation | FOUND evidence and exact limit | Sentence to change if accepted |
| --- | --- | --- |
| BF1/BF8: reuse acquired custody rather than reacquire it | The current record reports archive/tag/commit/member/root-license custody, the retained runtime graph and the later wheel/member/publication findings; these are historical worker observations (`registry/dataset-blockers.json:137`). The canonical custody document explains the source graph and the updated frontier (`registry/w-bfclrt-custody.md:41-45`, `:879-883`). | BF1/BF8 already recognize acquisition. Retain them; do not restate “no source custody” or rerun acquisition. Their continuing reuse instruction cannot be certified complete forever. |
| BF4/BF10: static providers and some license custody, only partially | Later custody reports static system providers and package/component notices, but still distinguishes declarations from compiled-input proof and actual loader selection (`registry/w-bfclrt-custody.md:877-883`; `registry/dataset-blockers.json:137`). | Do not mark BF4/BF10 satisfied: per-parent loader/dlopen and exact compiled-input component texts remain required. The earlier broad sentence “Complete offline package/native/Python ABI/transitive/license artifacts and a concrete local-model handler/full-call integration remain absent.” is narrowed by later FOUND artifacts; it is not a claim that complete closure is now met. |
| BF16–BF18: named containment target, scoped reachability and a preflight artifact | The parent records the decoder's uncontained `eval`, the platform-scope reachability finding and its deliberate decision to leave the gate unwired; the guard is required at a future integration boundary, not on today's diagnostic path (`registry/dataset-blockers.json:137`, `:195`). | BF17–BF18 already capture the decision. Do not change “unwired” to “missing” or “contained.” The broad parent phrase “provably never reaches” is limited by the same record's enumerated 317-file scope and UNKNOWN computed/outside-scope mechanisms. |
| BF14 historical “No scorer execution or full-call differential was performed by w-bfclrt.” | A later nineteen-case fabricated differential is reported for w-bfclcontain; it is a direct-function control, not official entrypoint execution (`registry/dataset-blockers.json:137`). | BF14 is true for the named worker and remains. The earlier observed sentence “No fabricated full-call differential or official scorer execution occurred; production remains function-name multiset diagnostic-only.” is historical and superseded as to the fabricated differential by the later control; official scorer execution remains unresolved. |

**Not satisfied:** producer-authenticated exact compiled-build closure for the enumerated 87 members, strict websockets filename provenance, actual loader/dlopen traces, complete exact-input license coverage, bound local handler/grammar/config, bounded upstream decoder integration and actual official execution (`registry/dataset-blockers.json:137`, `:195`; `registry/w-bfclrt-custody.md:883`). Publication signatures and available SONAMEs are explicitly partial evidence (BF3), not substitutes. The withdrawn independent-human-approval/signature-admission gates in the observation are not resurrected; that does not remove the separate exact-build provenance requirement (same observation).

w-evidence measures the source contract as sorted bare-name **multiset** equality, ignoring arguments, and flags irrelevance parsing; its disclosure is not an official execution (`registry/w-evidence-qualification.md:127-141`; `registry/w-evidence-disclosure.md:13`). Its BFCL proposal lacks the current final BF18 `guarded_decode` sentence and would replace the action without that explicit parent addition (`registry/proposals/w-evidence-bfcl_v3.json:5` versus `registry/dataset-blockers.json:195`). RECORD below carries the current field forward intact.

The inherited reachability ABSENT scope is `driver.py` plus every Python/shebang entry under `eval/`, `data/`, `tools/`, `harnessgrad/`, `ckpt/`, `methods/`, enumerated in `tests/fixtures/hg_bfclguard/sources.jsonl`; the worker's historical command was `python3 -I -S -B tests/fixtures/hg_bfclguard/reachability.py` (`registry/w-bfclguard-validation.txt:5-8`). Its target-import/mechanism counts are zero, with the later parent refresh recorded in `registry/dataset-blockers.json:137`; no replay is performed here. Publication/build-record search commands and their exact 95-archive/190-URL/member-name scopes are retained at `registry/w-bfclrt-custody.md:473-481`; private/unlisted producer records remain UNKNOWN. These scopes are evidence limits, not a proof that the future integration is safe.

### 3. Enumerable options

| Answer | Honest claim | Explicitly does not claim | New obligation / cost | Supporting evidence already on disk |
| --- | --- | --- | --- | --- |
| **HOLD** | Make no change; retain every exact-build, runtime, integration and execution obligation. | No official AST score, complete provenance, containment or production clearance. | No new work now; blocker can remain open indefinitely if producer records never become available. | Current field (`registry/dataset-blockers.json:195`). |
| **RECORD** | Keep the current gate while explicitly limiting active scoring to the named diagnostic and recognizing existing custody/preflight work. | No relaxation of the 87 build UNKNOWNs; no substitution of publication for build proof; no claim that the unwired guard contains an upstream integration. | Documentation integration only, no new experiment. BF1–BF18 survive intact. | Current parent decision (`registry/dataset-blockers.json:137`); w-evidence source qualification (`registry/w-evidence-qualification.md:127-141`). |
| **LIMIT** | Retire the official full-call/build-proof work package; retain only the name-multiset diagnostic and name the missing guarantees permanently. | Not a fulfilled full-AST contract, official score, known-safe decoder, source impossibility proof or production H0 permission. | Parent explicitly retires BF2/BF4–BF6/BF9–BF10/BF12–BF13's acquisition/execution work and BF7/BF15's open disposition; BF16–BF18 become mandatory conditions only if official work is reopened. Record the limit and close separately. No execution cost. | Current unresolved frontier and guard decision (`registry/dataset-blockers.json:137`, `:195`); exact closing-artifact list (`registry/w-bfclrt-custody.md:883`). |
| **SEEK** | Request a bounded attempt to acquire missing producer artifacts and, if gates really pass, perform bound runtime/full-call/official-scorer work. | A loader trace, rebuild, import smoke or direct-function control cannot retroactively authenticate an old wheel build; no guaranteed artifact availability or official success. | Requires separately bounded network/producer contact or authenticated upstream access where available, artifact/license review, branch-specific CPU/container loader tracing, a handler/grammar contract and safe decoder boundary, then official-scorer CPU execution. A model-driven diagnostic additionally costs local GPU time and separate staged authorization; fabricated scorer inputs need no GPU. Exact elapsed cost and access requirements remain UNKNOWN. No paid endpoints. | Required artifacts (`registry/w-bfclrt-custody.md:883`); current handler/containment/execution clauses (`registry/dataset-blockers.json:195`); historical control versus official distinction (`:137`). |

Exact text follows. HOLD writes nothing. LIMIT is a deliberate scope withdrawal, not an evidence-based declaration that the old requirements were met. SEEK preserves all original gates; it does not silently lower the provenance standard to make execution possible.

<details><summary>RECORD — exact proposal text</summary>

```json
{
  "dataset": "bfcl_v3",
  "blocker": "simple-ast-contract",
  "observed_append": "Operator records the completed custody and scoped guard review without closing any remaining BFCL gate. The latest parent decision deliberately keeps the preflight unwired on the platform diagnostic path and requires guarded_decode or an explicit reason at a future official integration; scoped absence is not general unreachability (registry/dataset-blockers.json:137,195). The active metric is bare-name multiset equality with arguments ignored and an irrelevance-parser qualification, not official AST/full-call scoring (registry/w-evidence-qualification.md:127-141).",
  "next_action_replacement": "Reuse the existing exact source/runtime custody and updated canonical per-target frontier in registry/w-bfclrt-custody.md and tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl. Acquire producer-authenticated compiled-build manifests bound to the exact retained archive/member hashes, naming CPython build implementation/version/SOABI, source revisions, compiler/flags and compiled inputs. PyPI publication attestations, supplied-leaf signature verification, SBOM declarations and static SONAME availability are partial evidence only. Later authorized runtime work must observe exact per-parent loader/dlopen choices and bind complete component licence/NOTICE texts to the exact compiled inputs. Resolve the strict websockets provenance filename mismatch without substituting another archive. Preserve the handler/config/expression-containment, selected-only/privacy/full-schema/reference and official scorer/full-call work for separately authorized workers. Preserve frozen receipts and the function-name diagnostic; external/open, no production H0. Use the measured source graph, candidate artifact hashes, host ABI identities and native/license custody already recorded in tests/fixtures/hg_bfclrt; do not reacquire established archive/tag/commit/member/root-license custody. Resolve the 87 exact compiled-build UNKNOWNs enumerated in the updated per-target frontier tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl, including any remaining exact member filenames/SHA-256 and their actual transitive graph; the wandb==0.18.5 entry is required only if the optional logging branch is selected. Supply observed filenames/SHA-256, compatible CPython/Linux x86_64 ABI and grammar/API bindings, exact compiled build provenance, unresolved linked/dlopen libraries and version-matched license/NOTICE scope. Availability outside the enumerated local scope is unobserved. Document and bind the concrete local-model handler, response grammar and model-config behavior before determining its branch-specific runtime. Preserve selected-only/privacy boundaries and full-call selected reference/schema information, contain expression evaluation, satisfy the technical source gate and later derive/validate the full-call contract and record actual official scorer execution with exact source/runtime/input/metric/receipt identities. No scorer execution or full-call differential was performed by w-bfclrt. Leave this blocker external/open. Treat the retained prompting decoder's `eval(ast.unparse(value))` at tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/utils.py:302 as a named containment target that must be bounded or bypassed before any official-scorer execution; the platform's own text boundary holding does not bound the upstream decoder. The containment target above now has a scoped reachability result (ABSENT over 317 enumerated platform files, UNKNOWN outside them) and an unwired preflight; any future official-scorer execution must either use that preflight or record why it does not, and must not read this ABSENT result as proof of general unreachability. The preflight gate is deliberately unwired in the platform (see this record's observed field, parent decision 2026-10-16); a future official-scorer execution uses `guarded_decode` from tests/fixtures/hg_bfclguard/gate.py or records why it does not. Evidence qualification: retain registry/w-evidence-qualification.md (bfcl_v3) and registry/w-evidence-disclosure.md as source-only disclosures of bare-name multiset equality, ignored arguments and the irrelevance-parser difference. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not relabel an earlier diagnostic as an official full-call score. This qualification waives no BFCL obligation and authorizes no execution."
}
```

</details>

<details><summary>LIMIT — exact proposal text</summary>

```json
{
  "dataset": "bfcl_v3",
  "blocker": "simple-ast-contract",
  "observed_append": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limit: exact-build/runtime/containment/full-call official execution unestablished (registry/dataset-blockers.json:137,195; registry/w-bfclrt-custody.md:883).",
  "next_action_replacement": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limits: exact compiled-build provenance for the retained 87-member frontier, actual per-parent loader/dlopen choices, complete exact-input component license/NOTICE coverage, strict websockets filename provenance, a bound local handler/config/grammar, contained upstream decoding and official full-call entrypoint execution remain unestablished (registry/dataset-blockers.json:137,195; registry/w-bfclrt-custody.md:879-883). Withdraw acquisition of those missing guarantees and official full-call integration/execution as active completion obligations; this is retirement to the existing function-name multiset diagnostic, not their satisfaction. Retain frozen receipts, the canonical source/runtime/member/license inventories and named unresolved frontier; reuse established custody rather than reacquiring it. Publication and static-provider findings remain partial evidence, with availability outside named searches UNKNOWN. The historical w-bfclrt no-execution/no-differential statement remains worker-specific. Preserve selected-only/privacy/reference boundaries and all source-only metric qualifications; no official AST/full-call or production H0 claim is authorized. Keep the parent decision that the preflight stays unwired on the present platform path; any future official-scorer work reopens the original technical gate, bounds or bypasses the retained eval decoder, uses guarded_decode from tests/fixtures/hg_bfclguard/gate.py or records why it does not, and must not read scoped ABSENT as general unreachability. No scorer or model run is authorized."
}
```

</details>

<details><summary>SEEK — exact proposal text</summary>

```json
{
  "dataset": "bfcl_v3",
  "blocker": "simple-ast-contract",
  "observed_append": "Operator records the completed custody and scoped guard review without closing any remaining BFCL gate. The latest parent decision deliberately keeps the preflight unwired on the platform diagnostic path and requires guarded_decode or an explicit reason at a future official integration; scoped absence is not general unreachability (registry/dataset-blockers.json:137,195). The active metric is bare-name multiset equality with arguments ignored and an irrelevance-parser qualification, not official AST/full-call scoring (registry/w-evidence-qualification.md:127-141). Operator requests only a separately bounded work packet; actual access/execution authorization remains outstanding.",
  "next_action_replacement": "Reuse the existing exact source/runtime custody and updated canonical per-target frontier in registry/w-bfclrt-custody.md and tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl. Acquire producer-authenticated compiled-build manifests bound to the exact retained archive/member hashes, naming CPython build implementation/version/SOABI, source revisions, compiler/flags and compiled inputs. PyPI publication attestations, supplied-leaf signature verification, SBOM declarations and static SONAME availability are partial evidence only. Later authorized runtime work must observe exact per-parent loader/dlopen choices and bind complete component licence/NOTICE texts to the exact compiled inputs. Resolve the strict websockets provenance filename mismatch without substituting another archive. Preserve the handler/config/expression-containment, selected-only/privacy/full-schema/reference and official scorer/full-call work for separately authorized workers. Preserve frozen receipts and the function-name diagnostic; external/open, no production H0. Use the measured source graph, candidate artifact hashes, host ABI identities and native/license custody already recorded in tests/fixtures/hg_bfclrt; do not reacquire established archive/tag/commit/member/root-license custody. Resolve the 87 exact compiled-build UNKNOWNs enumerated in the updated per-target frontier tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl, including any remaining exact member filenames/SHA-256 and their actual transitive graph; the wandb==0.18.5 entry is required only if the optional logging branch is selected. Supply observed filenames/SHA-256, compatible CPython/Linux x86_64 ABI and grammar/API bindings, exact compiled build provenance, unresolved linked/dlopen libraries and version-matched license/NOTICE scope. Availability outside the enumerated local scope is unobserved. Document and bind the concrete local-model handler, response grammar and model-config behavior before determining its branch-specific runtime. Preserve selected-only/privacy boundaries and full-call selected reference/schema information, contain expression evaluation, satisfy the technical source gate and later derive/validate the full-call contract and record actual official scorer execution with exact source/runtime/input/metric/receipt identities. No scorer execution or full-call differential was performed by w-bfclrt. Leave this blocker external/open. Treat the retained prompting decoder's `eval(ast.unparse(value))` at tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/utils.py:302 as a named containment target that must be bounded or bypassed before any official-scorer execution; the platform's own text boundary holding does not bound the upstream decoder. The containment target above now has a scoped reachability result (ABSENT over 317 enumerated platform files, UNKNOWN outside them) and an unwired preflight; any future official-scorer execution must either use that preflight or record why it does not, and must not read this ABSENT result as proof of general unreachability. The preflight gate is deliberately unwired in the platform (see this record's observed field, parent decision 2026-10-16); a future official-scorer execution uses `guarded_decode` from tests/fixtures/hg_bfclguard/gate.py or records why it does not. Evidence qualification: retain registry/w-evidence-qualification.md (bfcl_v3) and registry/w-evidence-disclosure.md as source-only disclosures of bare-name multiset equality, ignored arguments and the irrelevance-parser difference. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not relabel an earlier diagnostic as an official full-call score. This qualification waives no BFCL obligation and authorizes no execution. Operator requests a separately bounded acquisition/runtime/official-scorer work packet under every original gate above. Price upstream producer/public/authenticated record access, exact-build provenance and license review, the concrete local handler/config/grammar, per-parent loader/dlopen observations and a bounded upstream decoder boundary before any official entrypoint execution. Preserve the deliberately unwired platform preflight and use guarded_decode at a future integration or record why it does not apply. A prospective rebuild, import smoke or fabricated direct-function control is not the required historical build proof or official entrypoint receipt. Bind scope, inputs, resource cap and expected evidence for separate authorization; this packet request itself authorizes no network, scorer, dataset, model, GPU or production run."
}
```

</details>

### 4. Observation needed for outright closure

The original standard needs producer-authenticated manifests bound to the exact retained archive/member digests, naming source commits, CPython build implementation/version/SOABI, compiler/flags and compiled inputs; exact parent/child loader/dlopen observations; complete input-bound license/NOTICE scope; a strict retained-filename websockets provenance record; a concrete handler/config/grammar and privacy-preserving full-call contract; a bounded/bypassed expression decoder with `guarded_decode` or an explicit justified alternative; and an authentic official-entrypoint execution receipt naming source/runtime/input/metric/results (`registry/dataset-blockers.json:195`; `registry/w-bfclrt-custody.md:883`). A publication receipt and a static provider list alone do not meet that standard (BF3).

**Obtainability: no complete closure from the retained evidence described by these records; upstream recoverability is UNKNOWN.** The canonical frontier still records compiled-build UNKNOWNs, and public producer records outside its enumerated URLs are explicitly unsearched (`registry/w-bfclrt-custody.md:881-883`). Runtime execution can add loader observations but cannot observe a historical compiler invocation. Producer-authenticated historical records might settle it; the cited search does not prove those records do not exist. Therefore an unconditional “unobtainable everywhere” statement would be invented. With existing evidence and no new access/execution, the choice is open indefinitely (HOLD/RECORD) or closed with the named limit (LIMIT); SEEK purchases an attempt with no promise of full closure.

### 5. Decision

Which of A / B / C / D? **A = HOLD / B = RECORD / C = LIMIT / D = SEEK**

## triviaqa

### 1. What is actually owed

The historical execution obligation is **TQ1**; **TQ4** also owes membership, answer-preservation and scorer-key compatibility (`registry/dataset-blockers.json:740`):

> Obtain immutable upstream build/export EXECUTION evidence binding Hub export 0f7faf33a3908546c6fd5b73a660e0f8ff173c2f to the loader/converter actually executed, source/license hashes, runtime/writer versions and consumed input revision/hashes.

> Use registry/w-trivia2-review.md metadata findings rather than repeating the completed schema/inventory review; resolve HumanAnswers input incidence/preservation/redundancy, reconcile twelve metadata declarations versus eight exported configs and prose 18669 versus structured 17944 validation counts, and establish actual Domain/VerifiedEval/source membership, row/scorer-key duplicates, official key mapping and numeric denominator/aggregation through immutable payload-free membership/key/answer-preservation audits or separately authorized evidence.

All current sentences follow verbatim, including the later correction that the audits are completed with residual UNKNOWNs (`registry/dataset-blockers.json:740`).

<details><summary>All current next_action sentences (TQ1–TQ13)</summary>

**TQ1**

> Obtain immutable upstream build/export EXECUTION evidence binding Hub export 0f7faf33a3908546c6fd5b73a660e0f8ff173c2f to the loader/converter actually executed, source/license hashes, runtime/writer versions and consumed input revision/hashes.

**TQ2**

> The inspected authoritative public record supplies ancestry and output declarations but no immutable execution receipt.

**TQ3**

> Expanded named search is bounded absence only: supported historical-build/path-history interfaces, authenticated job records, unavailable historical snapshots and original immutable maintainer artifacts remain unobserved; no standing-limit withdrawal is justified.

**TQ4**

> Use registry/w-trivia2-review.md metadata findings rather than repeating the completed schema/inventory review; resolve HumanAnswers input incidence/preservation/redundancy, reconcile twelve metadata declarations versus eight exported configs and prose 18669 versus structured 17944 validation counts, and establish actual Domain/VerifiedEval/source membership, row/scorer-key duplicates, official key mapping and numeric denominator/aggregation through immutable payload-free membership/key/answer-preservation audits or separately authorized evidence.

**TQ5**

> Do not infer execution, member equality, unique-key denominator or HumanAnswers redundancy from ancestry, predecessor blobs, counts, filenames, ETags or converter names.

**TQ6**

> Retain the bound operator first-five eval-side manifest/export and scoring-input custody in tests/fixtures/hg_wsel/triviaqa.json without replacement/re-derivation or treating it as converter-execution or metadata-only provenance evidence.

**TQ7**

> Preserve v2 and historical identities; withdrawn shared admission gates do not condition closure or admission.

**TQ8**

> No production authorization.

**TQ9**

> The payload-free audits named above are now completed and their residuals recorded in this record's observed field: the declaration/configuration mismatch is decided, while HumanAnswers input incidence/preservation/redundancy and the 18669-versus-17944 difference remain UNKNOWN over the enumerated scopes.

**TQ10**

> Deciding them requires an immutable input/export correspondence audit or a prose-side counted ID/key inventory bound to source/split identities; do not substitute a questions-versus-answers, split-selection or deduplication story.

**TQ11**

> Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (triviaqa) and registry/w-evidence-disclosure.md as source-only metric disclosures.

**TQ12**

> Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument.

**TQ13**

> This append grants no measurement, closure or run authorization and waives no existing obligation.

</details>

### 2. What is already satisfied

| Sentence / obligation | FOUND evidence and exact limit | Sentence to change if accepted |
| --- | --- | --- |
| TQ4's general metadata review and twelve-versus-eight reconciliation | w-trivia4's retained measurement lists all twelve declarations, all eight routes and the exact four declaration-only names; section 2 is DECIDED (`registry/w-trivia4-reconciliation.md:68-105`). The current observation also incorporates that finding (`registry/dataset-blockers.json:692`). | Replace TQ4's “reconcile twelve metadata declarations versus eight exported configs” subclause with the completed disposition in TQ9. Do not request another general inventory or claim twelve materialized exports. |
| TQ4/TQ9: audits were performed, not all questions answered | w-trivia4 says sections 1/3/4 remain UNKNOWN; schema omission is not HumanAnswers input incidence, the prose count's cause is unidentified and partial key overlap is not the official denominator (`registry/w-trivia4-reconciliation.md:3-23`, `:44-66`, `:134-170`, `:174-221`). | TQ9 supersedes any reading of TQ4 as “perform these audits for the first time.” RECORD rewrites TQ4 to its residual settling-artifact contract, preserving TQ9–TQ10. Completing a search does not satisfy its unresolved underlying evidence demand. |
| TQ4: 17944 for the exact no-context stored split | The retained card itself contains both prose 18669 and structured 17944; the measured footer corroborates 17944 for the sole `rc.nocontext` validation shard (`registry/w-trivia4-reconciliation.md:109-141`). This is w-trivia3/4's measurement, not this worker's recount. | Retain TQ9–TQ10's UNKNOWN cause. Do not replace the residual with a guessed explanation of 725 or call the full original denominator established. |
| TQ6: retain first-five selection/scoring-input custody | The current record binds the five ordered eval tasks and fixture, with runtime_run_id null, and reports w-trivia4's byte comparison (`registry/dataset-blockers.json:692`). | TQ6 already recognizes custody. Retain it. External `/tmp` export survival is UNKNOWN here; do not imply those bytes were opened or re-export them. |
| TQ11: metric qualification | w-evidence's source-only finding identifies alias-max normalized row EM, no F1 scalar, and unestablished official keys/denominator (`registry/w-evidence-qualification.md:32-47`; `registry/w-evidence-disclosure.md:35`). | Retain TQ11–TQ12 and v2/historical identities. The quoted adapter “no human answers at all” sentence is unsupported, not proof that the input obligation is satisfied (`registry/w-evidence-qualification.md:40-47`). |

**Other superseded historical wording:** the early observation's “both shared admission gates” is displaced by TQ7's explicit withdrawal; those are not conditions of this choice (`registry/dataset-blockers.json:692`, `:740`). The earlier “no inventory-level duplicate members/identities were exposed” statement describes metadata review, not a whole-export absence: later measurements found repeated QuestionIds/candidate document keys in two enumerated `rc` shards (`registry/w-trivia4-reconciliation.md:180-203`). The earlier “no filename columns read” wording is explicitly corrected: the unfiltered no-context inventory read filename paths and observed empty exported filename keys (`:205-213`). Neither correction supplies original official Web membership.

**Not satisfied:** TQ1's immutable executed-code/runtime/writer/input binding, HumanAnswers input/export correspondence, the prose counting rule and full source/verified membership/key/denominator linkage (`registry/w-trivia4-reconciliation.md:16-23`, `:59-66`, `:163-170`, `:215-221`; `registry/w-trivia2-review.md:283-297`). An ancestry proof, five-row selection or the existence of scorer source cannot discharge them (TQ5–TQ6). The w-evidence proposal preserves these gates, rather than offering closure (`registry/proposals/w-evidence-triviaqa.json:5`).

### 3. Enumerable options

| Answer | Honest claim | Explicitly does not claim | New obligation / cost | Supporting evidence already on disk |
| --- | --- | --- | --- | --- |
| **HOLD** | Make no change and keep the entire historical execution/compatibility demand open. | No official key-level comparability or explanation of HumanAnswers/counts. | No new work now; the old wording still requires reading TQ4 together with the completion/residual correction TQ9. | Current field (`registry/dataset-blockers.json:740`). |
| **RECORD** | Accept the declaration/config mismatch and completed audit work, narrowing the active request to explicitly unresolved evidence. | No assertion that the remaining three audit questions were resolved, or that absent schema fields mean no HumanAnswers input. | Documentation integration only; original execution and compatibility requirements remain. | w-trivia4's DECIDED versus UNKNOWN dispositions (`registry/w-trivia4-reconciliation.md:3-23`, `:68-105`). |
| **LIMIT** | Retire the original historical export/scorer-linkage demand, preserve v2 as a specifically named local row-EM instrument and name the unresolved provenance/compatibility limits. | Not a historical execution attestation, original official EM/F1/key denominator, preserved HumanAnswers proof, membership explanation, restored receipt or production authorization. | Parent explicitly withdraws TQ1, TQ3's prohibition on limit withdrawal, TQ4's unresolved work and TQ10's settling-artifact demand; preserve their UNKNOWN limits, TQ5–TQ8 and disclosures, then close separately. No execution cost. | Current residuals (`registry/dataset-blockers.json:692`, `:740`); explicit upstream-search limits (`registry/w-trivia2-review.md:283-297`). |
| **SEEK** | Request a bounded upstream receipt/membership correspondence attempt under the current standard. | No guarantee that a receipt was ever captured; rerunning a converter now cannot prove what produced the pinned export then. | Requires separately bounded network/access, potentially authenticated Hub/maintainer historical records, an immutable input/export and prose-count inventory, and permission for answer-derived preservation attestations if needed. This may require upstream CPU/data processing or separately authorized payload access; amounts are UNKNOWN. No GPU/model is needed to seek provenance or audit correspondence. A later model diagnostic would be separate. | Unobserved access scopes and limits (`registry/w-trivia2-review.md:264-279`, `:283-297`); concrete settling artifacts (`registry/w-trivia4-reconciliation.md:59-66`, `:163-170`). |

Exact text follows. HOLD writes nothing. RECORD retires only the completed part of TQ4; TQ1 and all residuals survive. LIMIT deliberately waives unresolved historical/compatibility evidence, rather than describing a bounded search as a universal absence proof.

<details><summary>RECORD — exact proposal text</summary>

```json
{
  "dataset": "triviaqa",
  "blocker": "hub-export-scorer-linkage",
  "observed_append": "Operator accepts the completed metadata and payload-free audit work: twelve declarations versus eight export routes is DECIDED, while HumanAnswers, prose-count reconciliation and complete source/official-key/denominator linkage remain UNKNOWN at the whole-input/export level. The immutable upstream execution demand remains unmet; completed audits are not completed compatibility proof (registry/w-trivia4-reconciliation.md:3-23,68-105,134-170,174-221; registry/w-trivia2-review.md:283-297).",
  "next_action_replacement": "Obtain immutable upstream build/export EXECUTION evidence binding Hub export 0f7faf33a3908546c6fd5b73a660e0f8ff173c2f to the loader/converter actually executed, source/license hashes, runtime/writer versions and consumed input revision/hashes. The inspected authoritative public record supplies ancestry and output declarations but no immutable execution receipt. Expanded named search is bounded absence only: supported historical-build/path-history interfaces, authenticated job records, unavailable historical snapshots and original immutable maintainer artifacts remain unobserved; no standing-limit withdrawal is justified. Reuse registry/w-trivia2-review.md and registry/w-trivia4-reconciliation.md without repeating completed schema/inventory or declaration/configuration reconciliation: twelve declarations versus eight export routes and exactly four declaration-only names are DECIDED. HumanAnswers input incidence/preservation/redundancy, the counting rule behind prose 18669 versus stored rc.nocontext validation 17944, actual Domain/VerifiedEval/source membership, row/scorer-key duplicates, complete official key mapping and numeric denominator/aggregation remain UNKNOWN at the whole-input/export level. Resolve only those residual evidence demands through immutable payload-free input/export correspondence and source/split-bound counted-key inventories or separately authorized evidence; completed partial audits do not establish complete compatibility. Do not infer execution, member equality, unique-key denominator or HumanAnswers redundancy from ancestry, predecessor blobs, counts, filenames, ETags or converter names. Retain the bound operator first-five eval-side manifest/export and scoring-input custody in tests/fixtures/hg_wsel/triviaqa.json without replacement/re-derivation or treating it as converter-execution or metadata-only provenance evidence. Preserve v2 and historical identities; withdrawn shared admission gates do not condition closure or admission. No production authorization. The payload-free audits named above are now completed and their residuals recorded in this record's observed field: the declaration/configuration mismatch is decided, while HumanAnswers input incidence/preservation/redundancy and the 18669-versus-17944 difference remain UNKNOWN over the enumerated scopes. Deciding them requires an immutable input/export correspondence audit or a prose-side counted ID/key inventory bound to source/split identities; do not substitute a questions-versus-answers, split-selection or deduplication story. Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (triviaqa) and registry/w-evidence-disclosure.md as source-only metric disclosures. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument. This append grants no measurement, closure or run authorization and waives no existing obligation."
}
```

</details>

<details><summary>LIMIT — exact proposal text</summary>

```json
{
  "dataset": "triviaqa",
  "blocker": "hub-export-scorer-linkage",
  "observed_append": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limit: historical export execution and complete original answer/membership/key/denominator linkage unestablished (registry/dataset-blockers.json:692,740; registry/w-trivia4-reconciliation.md:3-23).",
  "next_action_replacement": "Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows. Named limits: the historical execution receipt for Hub export 0f7faf33a3908546c6fd5b73a660e0f8ff173c2f, executed code/source/license/runtime/writer/consumed-input identities, HumanAnswers input/export preservation, the prose 18669 counting rule and complete original Domain/VerifiedEval/source/key/denominator compatibility remain unestablished (registry/w-trivia2-review.md:283-297; registry/w-trivia4-reconciliation.md:3-23,59-66,163-170,174-221). Withdraw obtaining those missing historical and compatibility observations as active closure obligations, including the prior no-standing-limit-withdrawal disposition; this is an operator scope decision, not a new universal absence finding. Preserve the DECIDED twelve-declaration/eight-route mismatch and the limited 17944 stored-row observation without inventing a 725-member explanation. Do not infer execution, member equality, unique-key denominator or HumanAnswers redundancy from ancestry, predecessor blobs, counts, filenames, ETags or converter names. Retain the bound first-five eval manifest/export and scoring-input custody in tests/fixtures/hg_wsel/triviaqa.json without replacement/re-derivation or treating it as historical execution or metadata-only provenance evidence; external export survival is not reverified here. Preserve v2 and all historical identities and receipts; withdrawn shared gates do not condition closure or admission. Report only the existing local alias-max normalized row-EM identity with the source-only disclosures in registry/w-evidence-qualification.md and registry/w-evidence-disclosure.md; no official key-level EM/F1, denominator, production authorization or new numerical effect is claimed. Bind any future changed instrument to new evidence without relabeling an old number. No acquisition, payload audit or execution is authorized."
}
```

</details>

<details><summary>SEEK — exact proposal text</summary>

```json
{
  "dataset": "triviaqa",
  "blocker": "hub-export-scorer-linkage",
  "observed_append": "Operator accepts the completed metadata and payload-free audit work: twelve declarations versus eight export routes is DECIDED, while HumanAnswers, prose-count reconciliation and complete source/official-key/denominator linkage remain UNKNOWN at the whole-input/export level. The immutable upstream execution demand remains unmet; completed audits are not completed compatibility proof (registry/w-trivia4-reconciliation.md:3-23,68-105,134-170,174-221; registry/w-trivia2-review.md:283-297). Operator requests only a separately bounded work packet; actual access/execution authorization remains outstanding.",
  "next_action_replacement": "Obtain immutable upstream build/export EXECUTION evidence binding Hub export 0f7faf33a3908546c6fd5b73a660e0f8ff173c2f to the loader/converter actually executed, source/license hashes, runtime/writer versions and consumed input revision/hashes. The inspected authoritative public record supplies ancestry and output declarations but no immutable execution receipt. Expanded named search is bounded absence only: supported historical-build/path-history interfaces, authenticated job records, unavailable historical snapshots and original immutable maintainer artifacts remain unobserved; no standing-limit withdrawal is justified. Reuse registry/w-trivia2-review.md and registry/w-trivia4-reconciliation.md without repeating completed schema/inventory or declaration/configuration reconciliation: twelve declarations versus eight export routes and exactly four declaration-only names are DECIDED. HumanAnswers input incidence/preservation/redundancy, the counting rule behind prose 18669 versus stored rc.nocontext validation 17944, actual Domain/VerifiedEval/source membership, row/scorer-key duplicates, complete official key mapping and numeric denominator/aggregation remain UNKNOWN at the whole-input/export level. Resolve only those residual evidence demands through immutable payload-free input/export correspondence and source/split-bound counted-key inventories or separately authorized evidence; completed partial audits do not establish complete compatibility. Do not infer execution, member equality, unique-key denominator or HumanAnswers redundancy from ancestry, predecessor blobs, counts, filenames, ETags or converter names. Retain the bound operator first-five eval-side manifest/export and scoring-input custody in tests/fixtures/hg_wsel/triviaqa.json without replacement/re-derivation or treating it as converter-execution or metadata-only provenance evidence. Preserve v2 and historical identities; withdrawn shared admission gates do not condition closure or admission. No production authorization. The payload-free audits named above are now completed and their residuals recorded in this record's observed field: the declaration/configuration mismatch is decided, while HumanAnswers input incidence/preservation/redundancy and the 18669-versus-17944 difference remain UNKNOWN over the enumerated scopes. Deciding them requires an immutable input/export correspondence audit or a prose-side counted ID/key inventory bound to source/split identities; do not substitute a questions-versus-answers, split-selection or deduplication story. Evidence qualification 2026-10-16: retain registry/w-evidence-qualification.md (triviaqa) and registry/w-evidence-disclosure.md as source-only metric disclosures. Bind any future number to its actual scorer, output channel, split, trial-mean protocol and budget; do not reuse an earlier number under a changed metric or instrument. This append grants no measurement, closure or run authorization and waives no existing obligation. Operator requests a separately bounded upstream historical-receipt and input/export correspondence work packet under all existing provenance, privacy and compatibility requirements. Identify supported public/authenticated/maintainer access, actual permissions, immutable consumed-input/export and prose-count inventory scope, answer-derived attestation needs and CPU/data budget before separate authorization. A newly executed converter cannot prove the historical pinned export execution. Do not repeat the completed declaration/configuration inventory or substitute first-five custody for original input/export proof. This packet request itself authorizes no network, payload acquisition, row/answer read, scorer, model, GPU, diagnostic or production run."
}
```

</details>

### 4. Observation needed for outright closure

An authentic immutable upstream execution receipt must name export `0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`, the actually executed loader/converter and source/license hashes, runtime/writer versions and consumed input revisions/hashes. A separate immutable input/export correspondence audit must bind HumanAnswers incidence/preservation, original Domain/VerifiedEval/source selection, row and official-key duplicate handling, requested official keys and denominator/aggregation. A source/split-bound prose counted-ID/key inventory or authentic correction must explain 18669 versus stored 17944; the twelve-versus-eight declaration question needs no repeat (`registry/dataset-blockers.json:740`; `registry/w-trivia4-reconciliation.md:59-66`, `:102-105`, `:163-170`; `registry/w-trivia2-review.md:242-251`).

**Obtainability: the retained payload-free scopes cannot produce the missing historical execution/answer-preservation observations; recoverability outside them is UNKNOWN.** The named public search found no receipt in its accessible responses, while authenticated jobs, unsupported historical interfaces, inaccessible snapshots/index and original maintainer artifacts remained unobserved. The review explicitly rejects universal unavailability and sets withdrawal_entry to null (`registry/w-trivia2-review.md:264-297`). Those are inherited bounded search results, not a search run here; exact scopes, request plans and commands are cited in that review and its validation. A converter run today is prospective evidence only (`:294-297`). Thus existing evidence plus no new access supports open indefinitely (HOLD/RECORD) or an operator-selected named limit (LIMIT), not “no receipt can exist.” SEEK is conditional on new access, with no guarantee of closure.

### 5. Decision

Which of A / B / C / D? **A = HOLD / B = RECORD / C = LIMIT / D = SEEK**

## Decisions that are not per-dataset

- **Closure standard: EVIDENCE or LIMIT?** EVIDENCE means keep an unmet evidence obligation open until satisfied. LIMIT permits the operator to retire it while explicitly preserving what remains unestablished. Neither the BFCL nor TriviaQA retained search proves global impossibility (`registry/w-bfclrt-custody.md:883`; `registry/w-trivia2-review.md:283-297`). Choosing LIMIT is a scope decision, not a newly observed absence. The common standard sentence proposed for every limit closure is: **“Closed by operator scope decision with a named evidence limit; the missing observation remains UNKNOWN, the original obligation is withdrawn rather than satisfied, historical identities and receipts are preserved, and no official comparability or production authorization follows.”** Dataset-specific missing observations are in each LIMIT payload. This principle can answer both external-blocker closure questions at once; AppWorld additionally gives up the empirical success gate.
- **Authorization boundary: DOCUMENT or BIND?** DOCUMENT keeps the choices documentary. BIND is the common SEEK instruction: ask the parent to prepare a separately priced execution/access packet, specifying actual scope, parameters, resource cap and acceptance observation before authorization. No cost estimate or access availability has been observed here. A historical-receipt search, an official-scorer integration and a GPU actor trial are different purchases; this principle does not authorize any of them (current action boundaries at `registry/dataset-blockers.json:96`, `:195`, `:740`; staged policy `AGENTS.md:61-65`).

Two constraints survive either principle. First, preserve actual instrument labels and frozen identities: AppWorld single-task TGC with separate SGC/split reporting, BFCL bare-name multiset diagnostic, TriviaQA v2 row alias-max EM. Changed scorer/grouping/budget needs separately bound evidence; earlier numbers cannot be relabeled (w-evidence's source-only findings at `registry/w-evidence-disclosure.md:5`, `:12-13`, `:35`). Second, document closure cannot restore missing receipts. w-evidence's archive measurement reports zero verdict files in exactly the four enumerated AgentDojo/ALFWorld/GSM8K/MuSiQue archive trees and incomplete surviving census counterparts, not a global loss proof (`registry/w-evidence-archive.md:3-16`, `:35-57`). Its future retention proposal cannot recover those bytes (`:59-63`). These are w-evidence's measurements, not new archive inspection by this worker; they provide no successful receipt for the choices above.

The four permanent refusals require no option in this pack: the read-only registry inventory in validation lists precisely the three blockers above and the four `retain-contract-refusal` entries; their unchanged refusal sentence is retained (`registry/dataset-blockers.json:45`, `:499`, `:579`, `:643`). No proposal, permission request or investigation targets those datasets.
