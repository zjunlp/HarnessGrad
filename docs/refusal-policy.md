# Refusal lift — `tau2_bench` is refused by contract, not banned by policy

Operator decision, 2026-10-22: *"tau2 的这个也解禁吧"*. Base: `main` at `b3ec681c`, after
`#20` (τ³-bench) had been blocked by the same sentence this document amends.

This document records four things: what the ban said and why it existed, why a prohibition was the
wrong instrument for it, what changed in the repository since the refusal was written, and exactly
how far the lift goes. It is the amendment `registry/dataset-blockers.json`,
`data/datasets/tau2_bench.py`, `registry/blocker-closure-assessment.md` and `AGENTS.md` now point at.

## What the ban said, verbatim

`AGENTS.md`, *Prohibited shortcuts*, one line with twelve items:

> No method tuning, paid endpoints, heldout leakage, SOTUQA, API-Bank, NESTFUL, Tau2-bench, Matrix,
> score fabrication or fake benchmark utilization.

The operational half was the registry's one clause (`registry/dataset-blockers.json`,
`tau2_bench / retain-contract-refusal`, status `external`):

> Retain the explicit refusal and current exclusion; do not provision or run this dataset or
> substitute its metric.

and `docs/evidence-status-decision.md` listed the dataset among **"the four permanent refusals …
untouched, by operator instruction"**.

The measured reason, from `data/datasets/tau2_bench.py` — three findings, in the order of how hard
they are to engineer around:

1. **The second party is missing.** τ²-bench is dual-control: an LLM user simulator plays the
   customer, and `calculate_reward` replays *both* parties' actions to build the gold state. 55 of
   the 278 tasks carry gold actions with `requestor: "user"` (telecom's `toggle_data`,
   `refuel_data`) that no agent can perform, so the gold state is unreachable **by construction**.
2. **The missing party's script cannot be smuggled into the goal.** Each task's `user_scenario`
   says what the customer reveals *when*; handing it to the agent hands over the discovery the
   dialogue exists to create, and the number would go **up**. (The older τ-bench adapter's trick —
   putting the user id in the goal — works there only because that metric compares database states.)
3. **One factor of the metric needs a judge.** `reward = db_reward * env_assertion_reward`, each
   gated on `evaluation_criteria.reward_basis`; `nl_assertions` appear in all 50 airline and 8
   retail tasks, and those are model-judged.

## Why a prohibition was the wrong instrument

The list it sat in is **misconduct**: method tuning, paid endpoints, heldout leakage, score
fabrication, fake utilisation. A benchmark this platform cannot *measure* is not misconduct — it is
a contract gap, and the gap was already named precisely in the three findings above. Joining the two
classifications had two costs, both real:

* it said "never" where the record needed "not until these two facilities exist", which hides the
  exit and makes the same argument restart from zero every time; and
* it made the neighbouring entries read as accusations they are not — `api_bank`, `nestful` and
  `sotuqa` are also *contract* refusals, not suspected fabrication.

`data/datasets/tau2_bench.py` had already written the constructive path down and then declined to
take it. A prohibition is not the same statement as "unmeasured because unimplemented", and only the
second one is true.

## What changed in the repository

The two missing facilities now ship as platform code on `feat/tau3-bench` (`#20`, open at the time
of writing):

| facility | where | what it removes |
| --- | --- | --- |
| independent user simulator | `data/datasets/tau3_bench/lib/runtime.py` — the official `UserSimulator` and the domain tools run unchanged in a private trusted process; the actor is a half-duplex client with no reset, seed or scoring endpoint | finding 1: a second party exists that the actor cannot see or steer |
| judge channel | `eval/service/judge.py`, `eval/service/judge_request.py`, wired by `lib/model.py` into a declared `HG_JUDGE_SOCKET` | finding 3: `nl_assertions` can be judged by the verifier, under `VERIFY.judge`, with no implicit default and no caller-selected model |

And the reason this is not hypothetical for τ² specifically: `lib/runtime.py` already constructs
**τ² domain environments** (`tau2.domains.airline.environment.get_environment`, retail, telecom) —
the same objects τ²-bench v0.1.0 needs. The simulator is a reusable half-duplex loop, not a τ³-only
artefact.

What is still missing, and is the whole of the remaining work:

* **A pin of its own.** τ³ reads `split_tasks.json` at `fc0055dc…`; τ²-bench v0.1.0's base split is
  `tasks.json` (airline 50 + retail 114 + telecom 114 = 278) and its domains code is a different
  revision. Upstream states results below 1.0.1 are not comparable with results at or above it, so
  mixing the two would produce a number under τ²'s name that neither paper supports.
* **A split spec** (`data/datasets/tau3_bench/specs/task-splits.json` has no τ² counterpart) and an
  adapter that materialises `initial_state` for all 114 telecom tasks and the domain `db.json` for
  airline/retail, plus τ³'s `db.toml`-and-policy-directory trap.

## The lift, and its limits

**Lifted:** the permanent exclusion, in both places it was stated. `tau2_bench` may now be
provisioned and run *conditionally*, and the condition is written down in `AGENTS.md` and in the
registry's `next_action` rather than in a reviewer's memory.

**Not lifted, and not implied:** the adapter still returns `REFUSED`. Nothing above can be turned on
with a config flag — the pin, the split spec and the adapter are port work, and until they exist
`load()` cannot return tasks. A refusal that the code still makes is not a policy statement.

**Never permitted, lifted or not** — these were never in the ban and are now stated positively:

* reporting the **database half alone**, a `communicate_info` substring check, or any other nearby
  number under the τ² name (it is a different benchmark, per `docs/dataset-first-day.md`);
* letting the **actor** play the user's side, or reading `user_scenario` into the goal (finding 2 —
  it raises the score by removing the dialogue);
* a **pooled** number where per-domain `reward_basis` differ; each domain's basis has to be reported;
* a **paid-endpoint** judge — a number whose scorer cannot be re-run locally is not publishable.

## Recorded shapes, if the port is taken

Both are in `data/datasets/tau2_bench.py`; neither is chosen here.

1. **Staged second party** — declare the user's side as data the platform re-materialises (a scripted
   action list, exactly as ALFWorld's walkthrough and Jericho's gold commands are staged), and
   restrict to the **223 tasks whose gold actions are all the agent's**. Fully reproducible, and a
   *decision about what is being benchmarked*.
2. **Real simulator** — all 278 tasks through the platform's `UserSimulator` plus the judge channel.
   Closer to the published benchmark, and the number then carries simulator identity.

Either way the receipt's `identity` must name the simulator, the judge, the model family behind each,
the pin, and the per-domain `nl_assertions`/`env_assertions`/`communicate_info` counts, so a reader
can tell a staged side from a simulated one.

## Registry effect

| field | before | after |
| --- | --- | --- |
| `next_action` | "Retain the explicit refusal … do not provision or run this dataset or substitute its metric." | conditional lift: the two facilities, the identity requirement, and the three shapes that may never be reported |
| `status` | `external` | `code-solvable` — the facility is in this repository now, so the remaining work is code |
| `id` | `retain-contract-refusal` | unchanged, deliberately: receipts in `registry/w-evidence-validation.txt` and `registry/blocker-closure-assessment.md` reference `tau2_bench / retain-contract-refusal`, and an id that moves makes an old receipt unverifiable |

`api_bank`, `nestful` and `sotuqa` are **unchanged** — still refused, still listed. This document is
about `tau2_bench` only, per the decision it records; the same conditional form would apply to them
if the operator lifts them, and their own missing facilities are different (an oracle for the
released API results; a reproducible Win Rate; a judge for the headline metric).

## Provenance

* `AGENTS.md` — *Prohibited shortcuts* now carries the condition and this document's path.
* `registry/dataset-blockers.json` — `tau2_bench` blocker fields as above;
  `python3 tools/check_dataset_blockers.py` stays `{"valid": true, "datasets": 28, …}`.
* `data/datasets/tau2_bench.py` — docstring and `REFUSED` say "conditional", and name what lifts it.
* `docs/evidence-status-decision.md` — the "four permanent refusals" section is annotated as superseded
  for `tau2_bench` by this document; the other three are still permanent.
* Tests: `tests/quality/test_dataset_refusals.py`, `test_dataset_blockers.py`,
  `test_closure_assessment.py`, `test_apply_blocker_proposal.py`.
