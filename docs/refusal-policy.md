# Refusals are statements about the contract, not prohibitions

Operator decisions, 2026-10-22: *"tau2 的这个也解禁吧"* and then *"挪出来吧"*. Base: `main` after
`f4078220`, which lifted the `tau2_bench` exclusion and left the other three where they were; this
document now covers all four, and the fifth name on the old list (`Matrix`).

The rule this replaces was one line among twelve in `AGENTS.md`, *Prohibited shortcuts*:

> No method tuning, paid endpoints, heldout leakage, SOTUQA, API-Bank, NESTFUL, Tau2-bench, Matrix,
> score fabrication or fake benchmark utilization.

and, per dataset, the registry clause:

> Retain the explicit refusal and current exclusion; do not provision or run this dataset or
> substitute its metric.

## Why the list was wrong

The first five items are **misconduct**: tuning a method on the test set, buying scores from a paid
endpoint, leaking a heldout split, fabricating a score, running a benchmark for show. The last six
were **contract refusals** — datasets whose official metric this platform cannot carry. Putting them
in one sentence made two claims that were never true:

* that a refusal is a judgement about the benchmark, when it is a statement about this platform; and
* that someone might be tempted to fake one, when the honest failure was that the number is *not
  reachable here* and the exit was already written down in each adapter.

A prohibition is also the wrong shape for a conditional fact: it says "never" where the record needs
"not until this exists", and it hides the exit exactly where a reader needs it.

## The five names

| name | what is actually missing | exit recorded in the adapter | state after 2026-10-22 |
| --- | --- | --- | --- |
| `tau2_bench` | a second party — an LLM user simulator — and a judge for `nl_assertions` | both facilities **now ship in this repository** (`data/datasets/tau3_bench/lib/runtime.py`, `eval/service/judge.py`); what remains is a v0.1.0 pin, a split spec and an adapter | **exclusion lifted, conditionally** (`code-solvable`) |
| `api_bank` | the dialogue state the recorded API replies depend on | an oracle that reaches 1, or a separately named repaired subset; the gold call reproduces its own recorded reply on **262 / 508** samples (51.6 %) | refused (`external`) — not prohibited |
| `nestful` | reproducibility of the official execution Win Rate | a repaired upstream release, or a deterministically pinned run record; the pinned scorer returned **1857 true / 4 false**, then **1854 / 7** on the same 1861 gold sequences | refused (`external`) — not prohibited |
| `sotuqa` | the pinned judge behind the headline `acc` | a judge model that can be named and re-run; `llm_accuracy_score` resolved to `text-davinci-003`, which is retired, so the headline cannot be reproduced at all | refused (`external`) — not prohibited |
| `Matrix` | nothing: no adapter, no registry row, no blocker, no file | — | the name is dropped, because it was bookkeeping rather than a decision |

The three that stay refused keep their registry id (`retain-contract-refusal`) so that receipts
citing `api_bank / retain-contract-refusal` and friends remain verifiable, and keep `status:
external`: what they need is an artefact this repository cannot produce (dialogue state, a repaired
release, a retired judge model), not code.

`tau2_bench` is the one that moved, because it is the one whose missing facility turned out to be
platform code rather than upstream data.

## The `tau2_bench` case, in full

The measured reason, from `data/datasets/tau2_bench.py` — three findings, in order of how hard they
are to engineer around:

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

What changed in the repository: on `feat/tau3-bench` (#20) the official `UserSimulator` and the
domain tools run unchanged in a private trusted process (`data/datasets/tau3_bench/lib/runtime.py`,
which already constructs **τ²** domain environments —
`tau2.domains.airline.environment.get_environment` and the retail/telecom siblings), `relay.py` gives
the actor a half-duplex client with no reset, seed or scoring endpoint, and `eval/service/judge.py`
supplies the judge under `VERIFY.judge` with no implicit default and no caller-selected model.

What is still missing, and is the whole of the remaining work:

* **A pin of its own.** τ³ reads `split_tasks.json` at `fc0055dc…`; τ²-bench v0.1.0's base split is
  `tasks.json` (airline 50 + retail 114 + telecom 114 = 278) over a different domains revision.
  Upstream states results below 1.0.1 are not comparable with results at or above it, so mixing the
  two would produce a number under τ²'s name that neither paper supports.
* **A split spec** (`specs/task-splits.json` has no τ² counterpart) and an adapter that materialises
  `initial_state` for all 114 telecom tasks and the domain `db.json` for airline/retail, plus τ³'s
  `db.toml`-and-policy-directory trap.

Two shapes are recorded in the adapter, and neither is chosen:

1. **A staged second party** — declare the user's side as data the platform re-materialises (a
   scripted action list, exactly as ALFWorld's walkthrough and Jericho's gold commands are staged),
   over the **223 tasks whose gold actions are all the agent's**. Fully reproducible, and a
   *decision about what is being benchmarked*.
2. **A real simulator** — all 278 tasks through the platform's `UserSimulator` plus the judge
   channel. Closer to the published benchmark, and the number then carries simulator identity.

## What is never permitted, under either policy

These were never in the prohibition list and are now stated positively, per dataset:

* **No proxy under the benchmark's own name.** The database half or a `communicate_info` substring
  for τ²; EM/F1 for SOTU-QA's judge metric (against sentence-long golds they measure string
  overlap, and ReWOO's own author added the judge because `em` is a column of zeros); function/slot
  F1 or full-sequence match for NESTFUL's Win Rate; an execution-success rate for API-Bank's
  correctness metric. Each of those is a different benchmark, and publishing it under the original
  name is the fabrication the old list was actually trying to prevent.
* **No unrecorded second party or scorer.** A simulator, a judge and a resolved judge model are
  metric identity: they go in the receipt's `identity`, and two different ones never pool.
* **No paid-endpoint judge.** A number whose scorer cannot be re-run locally is not publishable.
* **A refusal the code still makes stays `REFUSED`.** Lifting an exclusion changes what may be
  *attempted*; it does not produce a number, and `load()` still raising is the record of that.

## Registry effect

| dataset | `next_action` | `status` |
| --- | --- | --- |
| `tau2_bench` | the two facilities plus the identity requirement (unchanged from `f4078220`) | `code-solvable` |
| `api_bank` | "refused by contract, not prohibited": the exit and the 262/508 measurement; no proxy | `external` (unchanged) |
| `nestful` | same shape: the repaired-release / pinned-record exit and the 1857-vs-1854 measurement | `external` (unchanged) |
| `sotuqa` | same shape: the pinned-judge exit, noting that the platform's judge channel now exists | `external` (unchanged) |

All four keep `id: retain-contract-refusal`. `registry/blocker-closure-assessment.md` keeps all four
rows and their `REFUSED` class — the adapter still refuses — and its dispositions now say "refused by
contract" rather than "permanent".

## Provenance

* `AGENTS.md` — the misconduct list is now the five items that are misconduct; the refusal paragraph
  names each dataset's exit and this document.
* `registry/dataset-blockers.json` — `next_action` rewritten for `api_bank`, `nestful`, `sotuqa` and
  `tau2_bench`; statuses as above; checker still `{"valid": true, "datasets": 35, …}`.
* `data/datasets/tau2_bench.py` — docstring and `REFUSED` point here.
* `docs/evidence-status-decision.md` — the "four permanent refusals" section is superseded.
* Tests: `tests/quality/test_dataset_refusals.py`, `test_dataset_blockers.py`,
  `test_closure_assessment.py`, `test_apply_blocker_proposal.py`.
