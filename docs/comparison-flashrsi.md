# FlashRSI and HarnessGrad: a sourced comparison

[FlashRSI](https://github.com/RUC-NLPIR/FlashRSI) (RUC-NLPIR, Apache-2.0) and this repository both
run harness self-improvement experiments, and a reader deciding which to use needs the difference in
one place. This page answers three questions — where our measurements are stronger, what we should
borrow, and how to reproduce each claim here — and answers them with a source per row rather than an
adjective.

**Checked 2026-11-02 (UTC).** FlashRSI facts were read from its repository, its documentation site and
the GitHub API at commit `main` on that date; measurements attributed to this repository were produced
by the commands in [§4](#4-how-to-reproduce-each-claim). Everything about FlashRSI can change without
notice, so a claim is cited to the file or page that states it. A claim we could not check is marked
**[unverified]**, with the one check that would settle it.

---

## 1. What is being compared

| | FlashRSI | HarnessGrad |
| --- | --- | --- |
| **What is measured** | The *target harness's declared resources* — the files a `components.yaml` tags as prompt / skill / tool / runtime / config — executed natively against a benchmark, whose grader scores the trajectory. The algorithm is a search policy over that tree. Sources: [README "Design Logic"](https://github.com/RUC-NLPIR/FlashRSI#design-logic), [Framework & Snapshot](https://ruc-nlpir.github.io/FlashRSI/development/framework.html). | A **harness repository**, with the model and the environment fixed by the platform for the run and the score produced by the dataset's own verifier. The method is an external program that returns a new tree; it never scores. `INTERFACE.md` [§1–§2](../INTERFACE.md#1-the-exchange-unit-a-whole-harness-repository), `docs/adapter-spec.md` [§1–§2](adapter-spec.md#1-the-model-contract--one-contract-every-adapter-adapts-to). |
| **Version-control model** | **No git in the candidate model.** A version is one JSON file (`format_version: 2`) whose `id` is the SHA-256 of a canonical serialization of `files`, `component_rules`, `agent_type`, `root_id`, `parent_id` and `format_version`; lineage is `root_id`/`parent_id`; `save()` writes through a temp file, `fsync`, then `os.link`, and never replaces an existing path. `.git` is a *forbidden* path inside a Snapshot (`_RUNTIME_PARTS`). Sources: [Snapshot storage](https://ruc-nlpir.github.io/FlashRSI/guide/snapshot-storage.html), [`flashrsi/core/snapshot.py`](https://github.com/RUC-NLPIR/FlashRSI/blob/main/flashrsi/core/snapshot.py). | **Git per round.** `commit_state(work, "round N: …")` commits the round's tree and returns `git rev-parse HEAD^{tree}` — a content address, not the commit SHA — which is the curve point's `identity.harness_sha` (`eval/ckpt/git_state.py:87`). The inter-commit diff is written to `diffs/round-N.patch` (`harnessgrad/records.py:822`). A run directory stores **no** `.git`: the history lives in the run's workspace, whose path is recorded in `run_meta.json`. `harnessgrad/snapshot.py` (`harnessgrad.snapshot.v1`) additionally writes a self-verifying single-file artifact with a sha256 `id` and `parent_id` — FlashRSI's model — as an *extra* record, not the only one. |
| **A round's provenance** | `snapshots/<id>.json` per candidate (with `files[path].component` inside the artifact), redacted `config.yaml`, `trajectories/`, `result.json`, `end-summary.{json,md}`. Changed paths are computed by the framework, not self-reported (`Snapshot.changed_paths()`; [AHE guide](https://ruc-nlpir.github.io/FlashRSI/ahe.html) says so explicitly). | A curve point carrying `identity` / `sampling` / `env` / `score` + `per_task` / `cost` / `cumulative` (`INTERFACE.md` [§3](../INTERFACE.md#3-the-record-what-a-curve-point-is)), plus per round `state_after_roundN/`, `diffs/round-N.{patch,json}` and `snapshots/round-N.json`. The diff is the platform's; the method's own claim is kept beside it as `files_claimed`. A round with none of the three sources answers `round-not-stored` (`console/server.py:2820-2860`) rather than reconstructing an approximation. |
| **How many methods** | 13 registered ("implements 13 RSI methods", README); 12 of them carry results in the published table, Arbor "has no real-model results yet". Ours overlap on **AHE, Meta-Harness, RRSI**. | 16 entries under `methods/`: 9 published ports (`ahe`, `dgm`, `dream_rsi`, `harnessx`, `hyperagents`, `meta_harness`, `rrsi`, `sica_ci`, `tthe`), each with an `experiments/method-*.json`; 2 reference trajectory shapes (`a`, `b`), 2 controls (`noop`, `raise_step_cap`), an echo baseline (`echo_base`), a wrapper (`lightrsi`), and the improver driver. `methods/*/method.json` states which is which. |
| **How many benchmarks** | 30 registered suites; 34 pinned Hugging Face configurations across 29 of them (README). Registration is not reproduction — the FAQ says so: [Does a registered adapter imply a reproduced result?](https://ruc-nlpir.github.io/FlashRSI/faq.html). | [`DATASETS.md`](../DATASETS.md) is generated from the adapters and is the count to read (`python3 tools/datasets_doc.py --check` fails when it drifts). On 2026-11-02 its at-a-glance line read: 39 published datasets, 4 refused by design, 7 with a recorded metric deviation (two of them settled that day: `tau_bench` scores 139 of the official 165 tasks, `triviaqa` averages 17,944 rows over 9,960 questions), 1 cleared by a reading, 0 whose adapter mentions one nobody has recorded. The line is regenerated, so read it rather than this cell. |
| **How many harnesses** | README: **7** integrations — MiniAgent, Claude Code, Codex, Pi, Hermes, OpenClaw, OpenCode. [How a run works](https://ruc-nlpir.github.io/FlashRSI/running-experiments.html) says "three harnesses: MiniAgent, Claude Code, and Codex", and the [project page](https://ruc-nlpir.github.io/FlashRSI/project.html) lists Pi / Hermes / OpenCode as *in progress*. The two documents disagree; treat "available" as 3, "named" as 7. | **7 launchable** (`base_harness/launchable.json`: `loop`, `deepseek-harness`, `miniswe`, `miniagent`, `openclaw`, `codex`, `opencode`) plus `oracle`, which is never launchable because its score is the scorer's own check. Overlap with FlashRSI: MiniAgent, Codex, OpenClaw, OpenCode. |
| **What a candidate is** | One tagged file tree serialized into one JSON file; the harness always executes the *complete* tree even when the algorithm's scope is one skill ([Framework & Snapshot](https://ruc-nlpir.github.io/FlashRSI/development/framework.html)). | A complete runnable repository: any language, any internal layout, one `harness.json` (`INTERFACE.md` §1). A method may overwrite everything except `name`/`version`; the platform re-validates the tree before measuring it and never executes it during that check (`eval/run/candidate.py`). |
| **Comparing two methods on one harness** | One method per run (`--algorithm`), so a comparison is two runs; the docs tell the reader to "compare both versions on the same held-out tasks and metric" because *the runner evaluates the selected Snapshot on test and does not automatically evaluate the initial Snapshot on test* ([How a run works](https://ruc-nlpir.github.io/FlashRSI/running-experiments.html)). | One method per run too, but every run can declare the **same frozen reference** (`--baseline`), the platform scores every point itself, `improvement` on the point is the paired test, and `tools/results_matrix.py` renders method × dataset from the archive. A comparison whose provenance disagrees is refused rather than printed: `baseline.problems`, and the per-point scorer-isolation rule in `INTERFACE.md` §3. |
| **What is refused, and why** | An edit to a file whose component is outside the algorithm's declared scope (`edit outside allowed components`, `flashrsi/core/snapshot.py`); `components.yaml`, credentials, `.git`, `.venv`, `node_modules`, `__pycache__`, `logs/` inside a Snapshot; replacing an existing snapshot path; a candidate whose `agent_type` / root / component rules differ (`_validate_compatible`); a `format_version: 1` snapshot ("recapture the source"). A benchmark is *not* refused — but registration is explicitly not a reproduced result. | A dataset the adapter cannot measure, with the missing piece named and `load()` raising (`docs/refusal-policy.md`, `registry/dataset-blockers.json`; 4 such datasets in `DATASETS.md`); a run whose dataset needs an `env_kinds` the harness does not declare (`INTERFACE.md` §1.1); a staged diagnostic that cannot publish its score (`eval/policy/diagnostic_policy.py`); a comparison across mismatched or unknown scorer-isolation classes (`INTERFACE.md` §3). |

---

## 2. Where our measurements are stronger, and what that costs

Four properties of the measurement contract are the ones a reader can check. What they cost is at the
end of this section.

**1. A harness never sees its score, in either half.** At test time the verifier runs *after* the
harness exits, in its own sandbox invocation, with the harness's artifacts mounted read-only and its
inputs re-materialized; the verifier's definition lives in the dataset, never in the task dict or the
working directory. At train time the *method* gets traces and scores; the harness still does not,
because the harness is the artifact being edited (`docs/adapter-spec.md` §2, `INTERFACE.md` §2.2).
FlashRSI states the same boundary for search ("Test feedback does not feed back into search") and
keeps judge and user-simulator credentials with the benchmark, not with the evolving Worker
([Framework & Snapshot](https://ruc-nlpir.github.io/FlashRSI/development/framework.html),
[Harness evolution and the benchmark environment](https://ruc-nlpir.github.io/FlashRSI/benchmark-harness-boundary.html)),
so this is **parity, not an advantage** — but it is parity only where both systems actually enforce
it, and here it is enforced in code that runs on every task.

**2. The verifier is the dataset's own, and deviations are written down.** The score is one number per
task produced by the dataset's verifier; where an adapter's reward is *not* the benchmark's official
metric, `DATASETS.md` records the deviation in a column that is rendered from the code and checked in
CI. This is the difference between "we score it ourselves" and "we score it ourselves and say where we
differ from the paper".

**3. Round 0 is measured by the same platform on the same task set.** A curve point's baseline is
either the run's own round 0 or a frozen reference file, and `point["baseline"]["problems"]` is where
a mismatch surfaces instead of being absorbed into a delta. That is the structural answer to "did the
method improve, or did the exam move": the same tasks, the same verifier, the same model. FlashRSI's
own documentation says its runner does not evaluate the initial Snapshot on test, so its published
delta is assembled from a separate evaluation — legitimate, but it is a second measurement rather
than a property of the run.

**4. The gain is tested on the same tasks, and against a control arm.** `improvement` on the point is
a sign test over discordant pairs (McNemar's exact test when the per-task scores are binary), so it
works at `trials=1` and does not need a spread; `score_ci95` cannot substitute because it is computed
over *tasks* and does not shrink when a run repeats them (`docs/harness-evolution-protocol.md` §1, §4;
`harnessgrad/policies.py:140`). The control arm is the same configuration with a no-op method
(`methods/noop/`), and the frozen reference gets a drift probe — the unedited harness re-run against
it — because a frozen reference cannot detect its own staleness.

**What the archive says, including where it corrects the brief.** These are this repository's own
records, read on 2026-11-02 (commands in §4):

* The archive root holds **784 run directories, 698 of them with a `curve.jsonl`**, and those curves
  carry **1008 points**. (The attribution tool prints "864 discovered, 698 included" because a
  `runs_discarded/` directory sits under the same root; 54 of its runs have curves.)
* The attribution tool attributes **0 of 1008** points — and, read correctly, **0 of the 310** that
  carry the field at all: `field_absent` 698, `no_component_named` 292, `no_path_moved` 18. This is
  a limit of the old archive — those runs predate the `components` maps (the newest is 2026-10-23;
  `base_harness/loop` gained its map on 2026-11-02) — **not** a measurement that no component
  changed. The mechanism is in place for new runs: every launchable base harness carries a map since
  2026-11-02, a round records `editable_surface_touched` + `editable_surface_components`
  (`docs/adapter-spec.md` §3: declared and recorded, never enforced), and the first attributed point
  has been produced and recorded (`reports/component-attribution-2026-11-02.md`, which also shows
  that re-reading the 292 recorded paths against today's map makes every one of them `control`).
* `appworld-study-hyperagents-20261020T0414Z`: 90 tasks, round 0 = 1.11 %, best = 66.67 % at round 20,
  **Δ +65.56 pp**; its last point records `improvement: {gained 59, lost 0, significant: true}`. The
  same record also says `baseline.problems: ["jobs changed: reference 16, this run 8"]` and
  `side: train` — so this is a training curve whose baseline was measured at a different `--jobs`
  value, and it is **not** yet a generalisation result. The record says that; a table would not.
* `appworld-study-sica_ci-20261019T1816Z`: 90 tasks, round 0 = 1.11 % → round 20 = **81.11 %**, 72
  gained / 0 lost / 18 unchanged, `significant: true`, same `baseline.problems` and `side: train`.
* The harness those studies evolved (`loop`, one `agent.py`) grew **39,409 B (38.5 KiB) at round 0 →
  61,160 B (59.7 KiB) at round 1 → 383,023 B (374.0 KiB) at round 20**. **Correction to the brief:**
  59.7 KiB is round *1*, not round 0; round 0 is 38.5 KiB. The final size and the 20-round span are as
  stated.

**What that costs.** A round re-measures the *whole* fixed task set, so a 90-task, 21-point appworld
study is 1,890 scored task-runs, and its run directory is 0.87–3.5 GB of traces and method channel
(`du -sh` on the two studies above). Each round also does git work: stage, `commit_state`, render the
patch, copy `state_after_roundN/`, write `snapshots/round-N.json`. On the cheap end this is nothing —
the zero-cost pair in §4 measures one task three times in seconds on the mock backend, and the
recorded quickstart measured 12 s on `mock` and 24 s against one local endpoint for five tasks — but
`--sampling all` costs what its name says, and the protocol already measured that a container dataset
costs an order of magnitude more per task than a files one — one `aime` task ran end to end in 44.3 s
(`docs/harness-evolution-protocol.md` §3). FlashRSI's own quickstart uses 12 questions (6 train / 3
dev / 3 test), which is cheaper per iteration; the price of our path is paid in order to have a paired
test with a control arm at all.

---

## 3. What we should borrow

Five concrete items, each with what it would take here and whether it is worth doing before the
2026-11-05 release.

**1. A headless "check out this round as one file" verb.** FlashRSI's `flashrsi evaluate --snapshot
<run>/snapshots/<id>.json` is the whole interface to a saved candidate — one file, checkable out on
another machine. We already *write* `snapshots/round-N.json` every round (`harnessgrad/snapshot.py`,
verified on a fresh run in §4), and the reader exists inside `console/server.py`
(`harness_candidate_from_round`, three sources in order). What is missing is a documented command that
does it without the console. *Take:* a `tools/round_snapshot.py` (or a `harnessgrad` verb) wrapping
`snapshot.load` / `snapshot.check_out` and printing the `id`, `parent_id` and `harness_sha`.
*Worth it before release:* yes — it is the one artifact that survives the run workspace, and that
workspace does not survive. Pointed at the archived run,
`python3 tools/export_harnesses.py <archive>/appworld-study-hyperagents-20261020T0414Z` answers
*"cannot find the run's workspace: run_meta.json names `/tmp/hg-appworld-work/...`"* and exports
nothing, because the checkpoints are git commits that lived there.

**2. A self-describing candidate: component labels inside the snapshot.** FlashRSI's snapshot stores
`files[path].component`, so the artifact itself says which role each file plays; ours stores only
`{content_b64, executable}`, and a reader needs the harness's `components` map from a *different* file
to say anything about a path. The 0-of-1008 result in §2 is a limit of the archive rather than of this
idea (698 points predate the field; 292 name no component because the edit was outside the map), but a
candidate that travels with its labels is what makes an exported file attributable without its
repository. *Take:* add the component name per file to `harnessgrad.snapshot.v1` — a schema bump, since
the id covers every field, plus the test that pins the digest. *Worth it before release:* after. It
changes an artifact format and a digest, and a release window is the wrong time for that.

**3. A matrix that crosses method × dataset × harness.** `tools/results_matrix.py` groups by method or
by harness and filters by dataset, but there is no single table that puts all three axes in one view.
FlashRSI publishes exactly that as a browsable matrix (project page, "Full source and transfer
matrix"). *Take:* one more `--group-by` value (or a second table) rendering `method × dataset`, with
the harness as a column, from the same `run_meta.json` the tool already reads. *Worth it before
release:* yes — it is a rendering change over data we already emit, and a reader comparing methods is
the release's audience.

**4. The harnesses we do not have: Claude Code, Pi, Hermes.** Our launchable roster overlaps theirs on
four and misses these three. *Take:* an adapter each, which by `docs/adapter-spec.md` means a working
trace under `tools/check_trace_contract.py` plus live evidence, not a wrapper that reports one exit
code. Claude Code is the plausible one of the three: §1 of the adapter spec already shows three CLIs
driven through a generated config file or an environment variable, and a closed CLI with a stable
configuration surface is exactly that shape. *Worth it before release:* after — an adapter whose trace
does not pass `tools/check_trace_contract.py` is a roster entry, not a measured harness
(`docs/adapter-spec.md` §4).

**5. Their per-integration "validation status" habit.** Every FlashRSI method page ends with what has
and has not actually run (the AHE page: "no real AHE × Claude Code evolution experiment has been run;
the simulated score only validates the mechanism"). That habit is worth copying into the harness
pages here, because it is the difference between a roster and a claim. *Take:* a "validation status"
section in `docs/writing_a_harness.md`'s checklist and in each `base_harness/*/` note, naming the last
real run that exercised it. *Worth it before release:* yes, and it is prose only.

**What we should not borrow.** Their docs are internally inconsistent about how many harnesses are
available ("7" in the README, "three" in the run guide, three "in progress" on the project page), and
the AHE page's validation status contradicts the README's AHE/Claude Code row. One number, one source,
checked by a generator, is cheaper than three prose pages — which is what `DATASETS.md` already does
for datasets here.

---

## 4. How to reproduce each claim

All commands run from the repository root. `runs` is the *runs root* you are analysing: it is
gitignored, so a fresh checkout has no archive in it until you make one, and the archive the numbers
on this page came from is not part of the repository. Point `--runs` at the archive you have. Nothing
below writes into a run archive unless the command says so.

**The archive counts, and the attribution breakdown (§2).**

```bash
python3 tools/component_attribution.py --runs runs --json /tmp/attr.json
python3 -c "import json; print(json.load(open('/tmp/attr.json'))['points'])"
```

That prints, abbreviated (key order as the tool writes it):

```
{'total': 1008, 'attributed': 0, 'unattributed': 1008, 'with_field': 310, 'without_field': 698,
 'in_discarded_runs': 54,
 'breakdown': {'field_absent': 698, 'declaration_unreadable': 0,
               'no_component_named': 292, 'no_path_moved': 18}}
```

**The two appworld studies, their deltas and their recorded problems (§2).**

```bash
python3 tools/results_matrix.py --runs runs --dataset appworld --json /tmp/appworld.json
python3 tools/run_summary.py runs/appworld-study-sica_ci-20261019T1816Z --json
python3 -c "import json; p=[json.loads(l) for l in open('runs/appworld-study-sica_ci-20261019T1816Z/curve.jsonl')][-1]; print(p['improvement'], p['baseline'], p['side'])"
```

`--json` prints to stdout and writes nothing. Without it (or `--dry-run` / `--out DIR`) `run_summary`
writes `end-summary.{md,json}` **into the run directory**, so use `--json` on an archive you cannot
recreate. `r0`, `last`, `best`, `delta_pp`, `gained`/`lost` come from that run's own curve.

**The per-round version control, on a run you make yourself (§1, §2).** Step 2 of this repository's
README passes `--dataset demo`, but `demo` is a test fixture rather than a published dataset
(`data/scaffolds/registry.py:38`, `_FIXTURE_NAMES`), and the driver refuses it — so this pair uses the
authored bundle instead. It needs no download, and the mock backend needs no model and no network.

```bash
export HG_AGENT_BACKEND=mock TMPDIR=/tmp
python3 driver.py --dataset authored_example_fix_bug --sampling all --harness loop --rounds 2 \
    --run-id control --method-entrypoint "python $PWD/methods/noop/run.py" \
    --runs-root /tmp/hg-t18-runs --work-root /tmp/hg-t18-work
python3 driver.py --dataset authored_example_fix_bug --sampling all --harness loop --rounds 2 \
    --run-id treat-A --method-entrypoint "python $PWD/methods/echo_base/run.py" \
    --runs-root /tmp/hg-t18-runs --work-root /tmp/hg-t18-work
python3 tools/plot_curve.py /tmp/hg-t18-runs/control /tmp/hg-t18-runs/treat-A
```

Then check that the three records agree — the commit in the workspace, the curve point, and the
snapshot chain:

```bash
git -C /tmp/hg-t18-work/treat-A/workspace log --oneline        # H0, round 1, round 2
git -C /tmp/hg-t18-work/treat-A/workspace rev-parse 'HEAD^{tree}'
tail -1 /tmp/hg-t18-runs/treat-A/curve.jsonl | python3 -c "import json,sys; print(json.load(sys.stdin)['identity']['harness_sha'])"
python3 -c "import json; a=json.load(open('/tmp/hg-t18-runs/treat-A/snapshots/round-1.json')); b=json.load(open('/tmp/hg-t18-runs/treat-A/snapshots/round-2.json')); print(a['id'][:12], b['parent_id'][:12], a['id']==b['parent_id'])"
ls /tmp/hg-t18-runs/treat-A/snapshots /tmp/hg-t18-runs/treat-A/diffs    # round-N.json + round-N.patch
```

**The dataset table, the refusals, and what this host has (§1).**

```bash
python3 tools/datasets_doc.py --check      # fails when DATASETS.md drifts from the adapters
python3 tools/datasets_doc.py --host       # ... plus what is provisioned on this machine
python3 tools/datasets.py --list | grep -c '^[a-z_0-9]*: '   # the published set the driver accepts
```

**The trace contract on a real trace (§1, `docs/adapter-spec.md` §4).** Each round keeps its traces,
one JSONL per task:

```bash
python3 tools/check_trace_contract.py /tmp/hg-t18-runs/treat-A/round1_traces/t01.jsonl
```

**Borrowing item 1, verified today:** `snapshots/round-N.json` is written by every round, so
`harnessgrad.snapshot` can already read and check out a round without the git workspace — only a
command that exposes this is missing:

```bash
python3 -c "from harnessgrad import snapshot; s = snapshot.load('/tmp/hg-t18-runs/treat-A/snapshots/round-2.json'); print(len(s['files']), s['id'][:12], s['parent_id'][:12], s['harness_sha'][:12]); print(snapshot.last_id('/tmp/hg-t18-runs/treat-A')[:12])"
python3 -c "from harnessgrad import snapshot; snapshot.check_out('/tmp/hg-t18-runs/treat-A/snapshots/round-2.json', '/tmp/t18-checkout')"
ls /tmp/t18-checkout
```

---

## 5. Open questions

Neither system answers these, and a comparison that hid them would be the marketing page this is not.

1. **Is any of it generalisation?** Both systems can report a training number: our `side: train` points
   say the method read the traces of every task it was scored on, and FlashRSI's search runs on train
   and evaluates test only at the end of evolution. Neither system refuses to *publish* a train number,
   and neither publishes a control arm beside it. What would settle it here: `--side eval --from-run`
   on a fresh eval split, with a control at the same configuration.
2. **What does an edit change, on average, per component?** Both systems can name the files an edit
   touched; neither can yet attribute a *gain* to a component. Our tooling exists
   (`tools/component_attribution.py`) and currently reports 0 of 1008 because of the archive's age.
   The first new study with `components` maps is the experiment that answers it. FlashRSI's tagged
   roles give the same decomposition on its side, and we found no per-role gain reported in its docs.
3. **Is the substrate comparable?** FlashRSI's Codex is "a Python semantic port based on `rust-v0.147.0`,
   with documented coverage limits", and its OpenCode/Pi/Hermes paths are "pure-Python Snapshot
   runtimes" rather than the upstream CLIs. A score from a port is about the port. Our adapters wrap
   the real program (`base_harness/codex/`, `opencode/`, `openclaw/`) — but we have never measured how
   much of a harness's score is the wrapper rather than the harness, and neither have they.
4. **What does a curve cost, in the unit a reader can compare?** We record `cumulative` compute per
   point and FlashRSI records usage per run, but not in a shared unit; no cross-system cost comparison
   is possible without one. Our `INTERFACE.md` §3 already calls this "the field's largest reporting
   gap".
5. **How many of the published numbers are real-model runs at all?** FlashRSI's own docs disagree about
   this for AHE (the guide says no real AHE × Claude Code evolution was run; the README table has
   cells for it) **[unverified — settle it by asking the authors, or by their paper's per-cell
   provenance]**. On our side the answer is in `run_meta.json` per run and is never inferred.
6. **Does FlashRSI have a paper?** The README links its paper badge to an arXiv *search* and its FAQ
   cites a `@misc` entry (Jin et al., 2026); we found no arXiv identifier on 2026-11-02 **[unverified —
   one arXiv id or DOI settles it]**. This matters for citation, not for the engineering.
7. **Was the "skills/prompts alone can work better than broad edits" finding published?** We could not
   find it stated in FlashRSI's README, FAQ, algorithms page or the AHE page, and its table does not
   order by scope: the bolded best cell per benchmark × harness goes to ACE (a playbook method) on
   Claude-Code HotpotQA, to Meta-Harness (broad scope) on Claude-Code SearchQA, to SkillOpt (skills) on
   Mini-Agent AppWorld, and to RRSI (broad scope) on Claude-Code Terminal-Bench 2.0 **[unverified as a
   stated finding — their paper or a per-method analysis page would settle it]**. The claim is
   plausible and is exactly what `tools/component_attribution.py`'s narrow-vs-broad split is built to
   answer *here*.
8. **Is a repository-level comparison even the right one?** They ship a framework for *running* RSI
   methods across many harnesses and benchmarks; we ship an instrument for *deciding whether one
   improvement is real*. A team that wants to compare 12 methods on 30 benchmarks should use theirs; a
   team that wants to know whether a method's curve is a result should use ours. No one has run both
   on the same method and compared the verdicts, and that experiment is the only one that would settle
   which measurement is more trustworthy.
