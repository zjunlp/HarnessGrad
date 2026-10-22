# HarnessGrad Agent Operating Rules

## Execution priorities

1. Close automatically solvable blockers and internalize reusable platform capabilities before launching large pilots.
2. Use isolated Codex worktrees only. Parent reviews every diff, runs focused tests, rejects stale reports and unrelated changes, then merges safe commits and updates the journal.
3. Keep up to 10 independent Codex tasks active when work can be parallelized. Every task must have a unique output filename; never trust inherited `reports/report.md` without checking the diff and commit.
4. Avoid idle rounds: combine waits with concrete receipt inspection, diff review, test execution, journal work or launch preparation. Do not spend a round only polling unchanged status.

## Working copies

There are exactly **two writers**, and the rule is about who yields to whom:

- **The operator writes directly to the main checkout** `/mnt/20t/xubuqiang/Study/HarnessGrad`
  whenever they want. That is supported, not a conflict. An agent merges **on top of** whatever
  it finds there and never rewrites, rebases away, reverts or moves an operator commit. When a
  merge conflicts because the operator changed the same file, the operator's change is the
  starting point and the agent's is the one that has to fit around it.
- **Agents never commit to `main`.** They work in registered `git worktree` checkouts under
  `/tmp/hg-*`, commit there, and the parent reviews the diff and merges.
- Every *other* copy of this repository on this machine is read-only or disposable.
- **Never create a full clone of this repository inside `Study/`.** Two were made on 2026-11-17
  (`HarnessGrad-appworld-controls-20261117`, `HarnessGrad-gaia-controls-20261117`). Both are now
  stranded on pre-rewrite commits that do not exist in `main`'s object store, and both still
  carry the old layout (`web/dist`, root-level report files). Their `origin` remotes -- which
  pointed at the main checkout, fetch **and push** -- have been removed, because a single
  `git push --force` from either would have restored the old history over `main`.
- They are kept, not deleted, and not reset: they hold run receipts and evidence
  (`gaia-evidence/`, `gaia-evidence-all17/`, `GAIA_FINAL_LAUNCH_RECEIPT.json`, the appworld
  `deliverables/` and `runtime/`) that exist nowhere else, and rule 5 of the staged pilot policy
  says old and incomplete receipts are preserved. Treat them as frozen evidence: read them, never
  fetch, merge, push or "update" them.
- 10 of 20 local branches also still point at pre-rewrite commits. They are refs, not working
  copies, so they are not a writing hazard, but they keep the old history reachable; decide
  explicitly before deleting any, since they are the only record of that codex work.
- If a copy is ever needed for an isolated run, make it a `git worktree` (registered, so
  `git worktree list` shows it) rather than a clone, and remove it when the run is done.

## Temporary files: match the device to the access pattern

**The two disks are different devices, and picking the wrong one costs 50x.** Measured
2026-10-15:

| device | mount | type | capacity | good at |
| --- | --- | --- | --- | --- |
| `nvme0n1` (Samsung 980 PRO 2TB) | `/` | **SSD** (`rotational=0`) | 1.8 TB, shared, ~92% full | **random I/O** — many small files |
| `sdb` (Seagate ST20000NM004E 18.2 TB) | `/mnt/20t` | **mechanical HDD** (`rotational=1`) | 19 TB, ~73% full | **sequential I/O** — big files, archives |

`/` is shared by **48 people** and about **1.45 TB of it is 47 other users' home directories** —
space we can neither see nor reclaim. It is not ours to fill. But it is also the only fast device
for many-small-file work, so the rule is a trade, not a prohibition.

**What a run writes.** `eval/run/runner.py:257` does `tempfile.mkdtemp(prefix="hg-task-")` per
task and materialises the dataset into it. For AgentDojo that is a **217 MB isolated runtime
venv** — tens of thousands of small files — so 40 concurrent tasks copy roughly **28 GB** and the
work is almost pure **random** I/O.

**Measured, same 40 AgentDojo tasks, same two servers, only the temp filesystem changed:**

| temp root | 40 dirs after ~8 min | GPU utilisation |
| --- | --- | --- |
| `/mnt/20t` (HDD) | **514 MB** | **0 %** — the model was never reached |
| `/tmp` (SSD) | **28 GB** | **100 % / 100 %** |

So for a materialisation-heavy dataset the HDD is unusable, and "the disk was 98 % full" was
**not** the cause of the earlier stall — the device type was.

**Choose by access pattern:**

```bash
# Many small files (venv copies, per-task materialisation) -> SSD.
# Bound jobs so the peak fits; 40 tasks peaked at 28 GB against 146 GB free.
export TMPDIR=/tmp
python3 driver.py ... --work-root /tmp/hg-<dataset>-work     # per-dataset, so it is easy to reap

# Bulk sequential data (traces, archives, run receipts, compression) -> HDD.
```

`TMPDIR` must be exported **in the shell that launches the driver**; a background job started from
a different shell does not inherit it. `--work-root` is a driver flag, so it goes on every
invocation.

**Do not leave temp on the SSD after a run**: reap `--work-root` and any `hg-task-*` trees, and
keep an eye on `df -h /`. 146 GB free minus a 28 GB peak is fine; three concurrent
materialisation-heavy datasets is not.

Two related facts:

* **A killed run leaks its temp tree.** The platform removes it in a `finally`
  (`eval/run/runner.py:584`), so a survivor means the process was SIGKILLed or SIGTERMed, not
  that cleanup is broken. Prefer letting a run finish; if you must stop one, delete the
  `hg-task-*` trees it leaves. Note `nohup` ignores SIGHUP but **not** SIGTERM, so launch long
  runs with `setsid` to detach them from the tool's process group.
* Clean `/tmp` with `python3 /mnt/20t/xubuqiang/hg-census/tmp_cleanup.py` (dry-run by default;
  `--apply` deletes). It only removes trees whose **newest file** is older than the window, and
  pins `hg-hp4-*` as active.

## Test execution

Pytest `--basetemp` must be **OUTSIDE the platform tree** — the runner refuses a harness work root
inside it. A test suite writes thousands of small files, so apply the rule above and put it on the
**SSD**, e.g. `--basetemp=/tmp/harnessgrad-pytest-quality`, then reap it. Driver-based tests use
`tmp_path` as a harness work root. Omitting the option preserves pytest's normal default.

## GPU execution

- **One local endpoint: `http://127.0.0.1:8002/v1`** (model `qwen3.5-9b-local`). GPU0 and the
  `8001` server were released on 2026-10-14 to give a card back; do not start a second one.
- `eval/unix_model_relay.py` hard-codes the agent model route to `127.0.0.1:8002`, so `8002` is
  the endpoint a containerised harness actually reaches. That, not preference, is why this is
  the one that was kept -- the other would have needed a production change.
- Keep the endpoint productively occupied with necessary no-model controls, diagnostics or valid
  pilots; never create duplicate, fake or scientifically unjustified work for utilization. If
  there is no approved diagnostic or pilot, an idle loaded server is the correct state: record
  that rather than inventing work.
- Bind each run to one endpoint and one fresh run ID. Keep total H0 concurrency `<=3`, `jobs=1`.

## Staged pilot policy

- First run a one-task/one-trial diagnostic after all dataset gates pass.
- For temporary reruns of blocked/incomplete datasets, select `min(30, ceil(10% * eligible))` tasks before execution, with `trials=1`, `jobs=1`.
- Expand only after a separate review of a valid measured diagnostic receipt. Do not silently revert to the historical `min(50, ceil(10% * eligible)) x 3` protocol.
- Invalid, unmeasured, timeout, scorer, provider, cleanup or snapshot failures are not ordinary zero scores.
- Preserve old and incomplete receipts; never resume or overwrite an old run ID.

## Dataset taxonomy

Task capability is a cross-table: `demand x surface`. Harness coverage is a separate axis: `exercises`. Do not collapse these into one flat dataset category.

## Prohibited shortcuts

No method tuning, paid endpoints, heldout leakage, SOTUQA, API-Bank, NESTFUL, Matrix, score fabrication or fake benchmark utilization.

`tau2_bench` is **not** on that list: its adapter refuses because the *platform*, not the harness, was missing a second party. The operator lifted the permanent exclusion on 2026-10-22, conditionally — it runs only with an **independent user simulator** (the platform's; the actor never plays both sides) and the **judge** its metric names, both recorded in the receipt's `identity`, and its tasks pinned to one upstream revision. Until those exist the adapter keeps returning `REFUSED`. The database half alone, a `communicate_info` substring check, or a pooled number under the τ² name is a different benchmark under the same name; a paid-endpoint judge makes a number non-publishable. Reasoning, the three measured findings and the two recorded shapes: `docs/refusal-lift-decision.md`.

## Evidence requirements

Every diagnostic or pilot records metric identity, eligible/selected/task/trial coverage, invalid and unmeasured counts, source/split/manifest/model/harness hashes, endpoint binding, resource/budget limits, cleanup receipt and comparability notes.

## Blocker closure priorities

Prioritize selected-only/privacy and provenance, scorer dependency/network and offline closure, trusted verifier/runtime, artifact-only state export, filesystem quota enforcement, official scorer execution, snapshot lifecycle and cleanup. A passing synthetic control is not a production H0 authorization.
