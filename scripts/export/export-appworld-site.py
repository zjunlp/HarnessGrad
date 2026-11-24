#!/usr/bin/env python3
"""Write AppWorld into the site in the **same shape** as the ALFWorld page.

Same data schema (`stacks` / `reference` / `coverage`), same `BenchmarkFilter` + `BenchmarkChart`
components, same one-score-table-per-split layout, same generated comment, same short prose.
The benchmark differs in what it has, not in how it is presented: two splits instead of four
(`train` 90 is the study side, `dev` 57 is the per-round validation side), and no Hub record
column, because no AppWorld record has been published there.

    python3 export-appworld-site.py            # write json + charts + page
    python3 export-appworld-site.py --check    # print the numbers, write nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import pathlib

from benchmark_charts import svg_bar_chart, svg_chart, svg_train_chart
from train_accounting import apply_task_cost, original_cost

D = pathlib.Path(__file__).resolve().parent
#: Same two knobs as `export-site.py`: the repository root is the site, and the run receipts
#: come from `HG_RUNS` (never vendored -- see `scripts/export/README.md`).
SITE = pathlib.Path(os.environ.get("HG_SITE") or D.parents[1])
RUNS = pathlib.Path(os.environ.get("HG_RUNS") or D / "runs")
if not RUNS.is_dir():
    raise SystemExit(
        f"export-appworld-site: no run receipts at {RUNS}. Set HG_RUNS to the directory "
        f"holding the run records -- see scripts/export/README.md.")
DONOR = "appworld-study-sica_ci-20261019T052140Z"
ALL_METHODS = ("sica_ci", "ahe", "dgm", "harnessx", "hyperagents", "tthe", "dream_rsi",
               "meta_harness", "rrsi")
POOLS = {"train": 90, "dev": 57, "test_normal": 168, "test_challenge": 417}
#: Methods whose single call produces a **batch** of children, so consecutive rounds are siblings
#: of one parent instead of successive hill-climbing steps. Their chart series is best-so-far
#: over the archive; the round's own value stays in `train_raw` so the table can show both.
#: (`methods/dgm/run.py` submits `children` per call; `methods/hyperagents/run.py` likewise.)
#: Empty on purpose (2026-10-21). `dgm` and `hyperagents` used to be plotted as a running
#: best-so-far, because their rule keeps an archive and the harness they "publish" is the best
#: node. The page never said so, so two rows of one column were a different quantity from the
#: other six: `dgm` repeated .400 for six rounds while its measured rounds were .311, .322,
#: .122, .200, .333. One column is one quantity -- the measured state of that round -- and the
#: archive best is a derived statistic that belongs in the text, not in the series.
BATCH_METHODS: tuple[str, ...] = ()


#: **Two methods are not shown for AppWorld, by operator decision (2026-10-20).** Neither is a
#: measurement problem -- both curves are complete and valid -- so the reason is about what the
#: number would say to a reader:
#:
#: * `meta_harness`: its rule needs two things this dataset does not give it. A per-task frontier
#:   is only informative when candidates already solve *different* subsets, and AppWorld's H0
#:   solves 1 of 90 tasks, so every candidate lands in 0-3 tasks and the frontier's "best" is the
#:   largest of a few noise draws. Upstream also smoke-tests a candidate on one task before paying
#:   for the full suite; this platform cannot (a method cannot run a harness), so each of its
#:   30 large rewrites was measured at full cost. On ALFWorld, where H0 solves 53 of 100, the same
#:   port goes .53 -> .70 -- which is where its evidence lives. See findings §115.
#: * `rrsi`: stopped at r6 at the operator's request after its own generality gate rejected four
#:   of six candidates; the two it admitted scored +1 task and 0/90. Its curve is real, it is just
#:   not comparable in length with the rest (findings §107).
#:
#: They stay in `runs/`, in `CKPT-appworld.md` and in the findings; they are omitted from this
#: page only.
HIDDEN = {# The three re-runs of 2026-10-20 are back on the page: `dgm` with the concrete target
          # rule, `dream_rsi` and `meta_harness` on the fixed platform. Their earlier curves stayed
          # hidden for the reasons kept below, which are now history rather than a reason to hide
          # the new run -- the run id in each row says which curve is plotted.
          "rrsi": "stopped at r6 by decision after its own gate rejected 4/6 candidates (§107)",
          # Withdrawn 2026-10-21 by operator decision, not for a measurement defect: the run is
          # complete and clean, it simply never improved (eleven points within .000-.022 of the
          # shared H0 .011). The record stays on disk; the number is not published.
          "meta_harness": "withdrawn by operator decision: ten rounds, no improvement over H0",
}


def measured_methods() -> tuple[str, ...]:
    """Methods with a study run on disk, in the order the goals were run, minus `HIDDEN`."""
    return tuple(m for m in ALL_METHODS
                 if m not in HIDDEN
                 and any("20261019T052140Z" not in p.name
                        for p in RUNS.glob(f"appworld-study-{m}-*")))
def _round_range() -> range:
    """Rounds the page draws: as many as the longest study curve has, at least 16.

    A fixed `range(0, 16)` silently dropped every round past r15: the six extension runs went to
    r20 on 2026-10-21 and the chart still ended at r15, which reads as "the run stopped" rather
    than "the exporter stopped". Deriving it from the curves keeps the page honest when a run is
    longer than the constant, and `15 + 1` is the floor the fixed version had.
    """
    best = 15
    for curve in RUNS.glob("appworld-study-*/curve.jsonl"):
        try:
            last = max(json.loads(line)["round"] for line in curve.read_text().splitlines()
                       if line.strip())
        except (OSError, ValueError, KeyError):
            continue
        best = max(best, int(last))
    return range(0, best + 1)


ROUNDS = _round_range()


def _load_exporter():
    """`export-site.py` has a hyphen in its name; load it as a module for the cost tables."""
    spec = importlib.util.spec_from_file_location("export_site", D / "export-site.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def points(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def study_run(method: str) -> pathlib.Path | None:
    runs = [p for p in RUNS.glob(f"appworld-study-{method}-*") if DONOR not in p.name]
    return sorted(runs)[-1] if runs else None


def study_scores(method: str) -> dict[int, dict]:
    """One round per measured state, with repeats of an already-measured state marked.

    **A state is named by its content address**, not by the round it happened to land in
    (`ckpt/git_state.py:84-112`: `harness_sha` is the tree sha, because a harness is code and
    equal content is the same harness). A method that reports `changed=False` -- or whose gate
    rejects its own proposal -- is measured again on the *same* tree, and the platform records
    that as a new round with a new noisy score: `dgm` r3/r4 (0.0444 / 0.0778 on `99d65efbb0`),
    `tthe` r6-r9 (`51dbfa42` five times), `rrsi` r0-r2 (`7a40f6dd`, the H0 itself).

    Two measurements of one state are two numbers, and picking either is a choice the record
    cannot defend, so the published series keeps the **first** one and marks the rest
    `repeat_of=<round of that state>`. They stay in the data (a reader can see how many rounds
    the gate burned) but they are not new states and must not be counted as rounds learned.
    """
    run = study_run(method)
    if run is None:
        return {}
    out: dict[int, dict] = {}
    first_of_sha: dict[str, int] = {}
    for row in points(run / "curve.jsonl"):
        if row.get("per_task") is None:
            continue
        round_no = int(row["round"])
        sha = (row.get("identity") or {}).get("harness_sha") or ""
        repeat_of = first_of_sha.get(sha) if sha else None
        if sha and sha not in first_of_sha:
            first_of_sha[sha] = round_no
        out[round_no] = {"run": run.name, "score": row.get("score"), "tasks": len(row["per_task"]),
                         "invalid": row.get("n_invalid") or 0,
                         "harness_failed": row.get("n_harness_failed") or 0,
                         "sha": sha, "repeat_of": repeat_of,
                         # `batch` is how many children one improver call produced and `step` is
                         # which of them this round is; the cost tables need both to count the
                         # call once instead of once per child (see `improver_cost_table`).
                         "batch": row.get("steps_in_submission") or 1,
                         "step": row.get("step_index"),
                         "cost": row.get("cost")}
    return out


def dev_scores(method: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for run in sorted(RUNS.glob(f"appworld-dev-{method}-r*")):
        rows = points(run / "curve.jsonl")
        if not rows or not rows[0].get("per_task"):
            continue
        row = rows[0]
        round_no = int(run.name.split("-r")[-1].split("-")[0])
        rank = (len(row["per_task"]) == POOLS["dev"] and not row.get("n_invalid"),
                len(row["per_task"]))
        if round_no in out and rank <= out[round_no]["_rank"]:
            continue
        out[round_no] = {"run": run.name, "score": row.get("score"),
                         "tasks": len(row["per_task"]),
                         "invalid": row.get("n_invalid") or 0, "_rank": rank,
                         "tokens": (row.get("cost") or {}).get("harness_tokens"),
                         "split": trace_split(run / "round0_traces")}
    for value in out.values():
        value.pop("_rank", None)
    return out


def trace_split(folder: pathlib.Path) -> dict:
    """Sum `input`/`output` tokens over one folder of per-task traces.

    The point's own `cost` block reports the harness total and the call count, but not the
    input/output split -- the traces carry it, and the ALFWorld page shows it, so read the same
    field instead of printing three dashes beside a number we do have.
    """
    if not folder.is_dir():
        return {"tasks": 0, "tasks_reporting": 0, "input": 0, "output": 0}
    total_in = total_out = 0
    reporting = 0
    files = sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
    for trace in files:
        usage = None
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("usage"):
                usage = event["usage"]
        if not usage:
            continue
        reporting += 1
        total_in += int(usage.get("input_tokens") or 0)
        total_out += int(usage.get("output_tokens") or 0)
    return {"tasks": len(files), "tasks_reporting": reporting,
            "input": total_in, "output": total_out}


def collect() -> dict:
    donor = points(RUNS / DONOR / "curve.jsonl")
    h0_train = donor[0]["score"] if donor else None
    dev0 = dev_scores("sica_ci").get(0, {})
    h0_dev = dev0.get("score") if dev0.get("tasks") == POOLS["dev"] else None
    # **The shared H0 dev point, kept so round 0 can be printed for every method.** Round 0 is one
    # state, measured once (`--h0-from` on the train side says the same thing); three runs
    # (`dream_rsi`, `hyperagents`, `tthe`) never got their own r0 cell, so their column read "—" and
    # looked like missing data rather than a shared measurement.
    h0_dev_cell = dev0.get("run") if dev0 else None
    h0_dev_tokens = dev0.get("tokens") if dev0 else None

    methods = []
    for method in measured_methods():
        study, dev = study_scores(method), dev_scores(method)
        if not study:
            continue
        cells = []
        run = study_run(method)
        best_so_far = None
        # A state's number is its **first** measurement. A repeat round re-measured the same
        # tree, so its replicate number must not become the state's score anywhere the page
        # shows one -- otherwise `rrsi` reads as "0.022 best" when the only state it ever
        # measured is H0 at 0.011. The replicate stays in `train_raw`.
        first_score = {r: v["score"] for r, v in study.items() if v.get("repeat_of") is None}
        for round_no in ROUNDS:
            s, d = study.get(round_no), dev.get(round_no)
            raw = s["score"] if s else None
            repeat_of = (s or {}).get("repeat_of")
            shown = raw if repeat_of is None else first_score.get(repeat_of, raw)
            # Best-so-far counts **states**, not measurements: a repeat of a state already on
            # the curve cannot raise the archive's best, so it must not raise this line either.
            if shown is not None and repeat_of is None:
                best_so_far = shown if best_so_far is None else max(best_so_far, shown)
            cell = {
                "round": round_no,
                # A round the run has not measured yet is "—", never a carried value: a
                # best-so-far line that extends into unmeasured rounds reads as progress the
                # run has not made (measured 2026-10-20 while the last rounds were still running).
                "train": (best_so_far if method in BATCH_METHODS else shown)
                         if s is not None else None,
                "train_n": s["tasks"] if s else None,
                "eval": {"dev": d["score"]} if d else {},
                "valid": bool(s and not s["invalid"] and not s["harness_failed"]),
                "n_invalid": (s or {}).get("invalid", 0),
            }
            if s:
                cell["train_raw"] = raw
                cell["step"] = s.get("step")
                cell["state"] = (s.get("sha") or "")[:12]
                if repeat_of is not None:
                    cell["repeat_of"] = repeat_of
                if (s.get("batch") or 1) > 1:
                    cell["batch"] = s["batch"]
            if d and d.get("tokens"):
                cell["eval_tokens"] = {"dev": d["tokens"]}
            if d and d.get("split"):
                cell["eval_split"] = {"dev": dict(d["split"], tasks=POOLS["dev"])}
            # **Round 0 reads the same for every method, on purpose.** It is one state (H0) and
            # one measurement, so a per-run number here would suggest seven independent
            # measurements of seven different checkpoints. `dream_rsi`, `hyperagents` and `tthe`
            # never got their own r0 dev point at all (their columns read "—", as if data were
            # missing), and the four runs that did measure their own r0 agreed with this one
            # anyway: score .0000 for all four, in 9.35-9.47 M, out 0.14-0.16 M -- within 1%.
            # Those receipts stay on disk; the page prints the one state once.
            if round_no == 0 and h0_dev is not None:
                cell["eval"] = {"dev": h0_dev}
                cell["dev_from_h0"] = True
                if dev0.get("tokens"):
                    cell["eval_tokens"] = {"dev": dev0["tokens"]}
                if dev0.get("split"):
                    cell["eval_split"] = {"dev": dict(dev0["split"], tasks=POOLS["dev"])}
            if s and s.get("cost"):
                split = trace_split(run / f"round{round_no}_traces")
                cost = s["cost"]
                cell["_cost"] = {
                    "task": {"tokens": cost.get("harness_tokens"),
                             "calls": cost.get("harness_model_calls")},
                    "task_split": {"input": split["input"], "output": split["output"],
                                   "tasks_reporting": split["tasks_reporting"],
                                   "tasks": POOLS["train"]},
                    "improver": {"input": cost.get("method_generation_input"),
                                 "output": cost.get("method_generation_output"),
                                 "cache_read": cost.get("method_cache_read_tokens"),
                                 "cache_write": 0,
                                 "calls": cost.get("method_model_calls")},
                }
            reviewed = original_cost(run.name, round_no)
            if reviewed is not None:
                apply_task_cost(cell.setdefault("_cost", {}), reviewed)
            cells.append(cell)
        # **Ties are exact, and the earlier round wins.** `dgm` r9 = r16 = .4386 and `tthe`
        # r6 = r17 = .4035 to the last digit, so which one "the best round" is was decided by
        # the iteration order of `dev` (which sorts round names as text: 0, 1, 10, 11, ... 2,
        # 20) -- and it landed on r16 / r17 while the exam measured r9 / r6. The tie is broken
        # by round number now: the exam's choice and this column must agree, and of two equal
        # checkpoints the earlier one is the cheaper.
        scored_dev = {r: v["score"] for r, v in dev.items() if v["score"] is not None}
        if scored_dev:
            top = max(scored_dev.values())
            best = (min(r for r, value in scored_dev.items() if value == top), top)
        else:
            best = (None, None)
        cost = {str(c["round"]): c.pop("_cost") for c in cells if c.get("_cost")}
        methods.append({"name": method, "run": study_run(method).name, "rounds": cells,
                        "cost": cost, "best_dev_round": best[0]})

    return {
        "slug": "appworld",
        # See `HG_GENERATED_AT` in export-site.py: pins the provenance line for a comparison.
        "generated_at": os.environ.get("HG_GENERATED_AT")
        or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stamp": "20261020",
        "study": {"pool": "train", "tasks": POOLS["train"], "rounds": ROUNDS[-1]},
        "pools": dict(POOLS),
        "reference": {"train": h0_train, "dev": h0_dev,
                      "dev_from": h0_dev_cell, "dev_total_tokens": h0_dev_tokens},
        "platform_content_sha": None,
        "stacks": [{
            "id": "loop-qwen9b__dsh-deepseek",
            "task_agent": {"base_harness": "loop@0.2.0", "model": "qwen3.5-9b-local",
                           "decoding": "greedy (temperature=0)"},
            "improver": {"method": "the methods listed below",
                         "improver_model": "deepseek-flash",
                         "improver_harness": "dsh (headless profile)",
                         "rounds": ROUNDS[-1]},
            "methods": methods,
        }],
        "excluded": [],
        "coverage": {
            "train": {"measured": len(methods), "valid": len(methods), "planned": None},
            "dev": {"measured": sum(1 for m in methods for c in m["rounds"]
                                    if c["eval"] and not c.get("dev_from_h0")),
                    "borrowed_h0": sum(1 for m in methods for c in m["rounds"]
                                       if c.get("dev_from_h0")),
                    "valid": sum(1 for m in methods for c in m["rounds"]
                                 if c["eval"] and not c.get("dev_from_h0")),
                    "planned": len(methods) * len(ROUNDS)},
        },
        "h0": {"run_id": DONOR, "train_score": h0_train, "dev_score": h0_dev},
    }


def score_table(methods: list[dict], pool: str) -> str:
    """One split's table, in the same shape the ALFWorld page uses."""
    head = ('<div class="hg-table"><table><thead><tr><th>method</th>'
            + "".join(f"<th>{'r0 (H0)' if r == 0 else f'r{r}'}</th>" for r in ROUNDS)
            + "</tr></thead><tbody>")
    rows = []
    for method in methods:
        cells = [f'<td><a href="../../methods/{method["name"]}/"><code>{method["name"]}</code></a></td>']
        for round_no in ROUNDS:
            cell = next(c for c in method["rounds"] if c["round"] == round_no)
            value = cell["train"] if pool == "train" else cell["eval"].get(pool)
            mark = "*" if cell.get("dev_from_h0") else ""
            cells.append(f"<td>{'—' if value is None else f'{value:.3f}{mark}'}</td>")
        rows.append(f'<tr data-method-row="{method["name"]}">' + "".join(cells) + "</tr>")
    return head + "".join(rows) + "</tbody></table></div>"


def dev_cost_table(methods: list[dict], exporter) -> str:
    """Per-point cost **with the input/output split**, which the eval points do have.

    The ALFWorld page prints one number per point because its eval points only recorded a total.
    These record the same `usage` block as the study runs, so the same two-column shape the study
    table uses is the honest one here: a single number would be a coarser table than the data.
    """
    rounds = list(ROUNDS)
    head = ('<div class="hg-table"><table><caption>task-agent tokens per measured point — '
            "millions of tokens (M); in = uncached prompt tokens, out = generated tokens; "
            'one point is one measurement</caption><thead><tr><th rowspan="2">method</th>'
            + "".join(f'<th colspan="2">r{r}</th>' for r in rounds) + "</tr><tr>"
            + "".join("<th>in</th><th>out</th>" for _ in rounds) + "</tr></thead><tbody>")
    rows = []
    for method in methods:
        cells = [f'<tr data-method-row="{method["name"]}">' + exporter.method_cell(method["name"])]
        for round_no in rounds:
            cell = next(c for c in method["rounds"] if c["round"] == round_no)
            split = (cell.get("eval_split") or {}).get("dev") or {}
            mark = "*" if cell.get("dev_from_h0") else ""
            for key in ("input", "output"):
                value = split.get(key)
                cells.append("<td>—</td>" if not value
                             else f"<td>{value / 1e6:.2f}{mark}</td>")
        rows.append("".join(cells) + "</tr>")
    return head + "".join(rows) + "</tbody></table></div>"


def page(data: dict) -> str:
    exporter = _load_exporter()
    methods = data["stacks"][0]["methods"]
    stamp = data["generated_at"]
    h0_train = data["reference"]["train"]
    h0_dev = data["reference"]["dev"]
    splits = [
        f"{{value: 'train', label: 'train', count: {POOLS['train']}, h0: '{h0_train:.3f}'}}",
        f"{{value: 'dev', label: 'dev', count: {POOLS['dev']}, h0: '{h0_dev:.3f}'}}"
        if h0_dev is not None else
        f"{{value: 'dev', label: 'dev', count: {POOLS['dev']}}}",
        # **No combined `eval` tab.** The two pools are the comparison; a tab that averages them
        # is the one view the operator asked to remove, because the average is what hides the gap.
        f"{{value: 'test_normal', label: 'test_normal', count: {POOLS['test_normal']}}}",
        f"{{value: 'test_challenge', label: 'test_challenge', count: {POOLS['test_challenge']}}}",
    ]
    method_opts = ", ".join(f"{{ value: '{m['name']}', label: '{m['name']}' }}" for m in methods)
    # `dev_from_h0` cells are the shared H0, not a published point of their own, so they must
    # not inflate the count the page reports.
    published = sum(1 for m in methods for c in m["rounds"]
                    if c["eval"] and not c.get("dev_from_h0"))
    # The page stays short on purpose: what a method's line means (batch best-so-far, rounds
    # that re-measured a state already on the curve) is in `appworld.json` as `train_raw` /
    # `repeat_of` and argued in `hg-work/rsi10/findings.md` (§95, §105), not repeated under the
    # table where a reader has to scroll past it to see the numbers.
    lines = [
        "---",
        "title: AppWorld",
        "description: What AppWorld measures, the scores this platform measured, and how its "
        "study and validation splits relate.",
        "---",
        "import BenchmarkFilter from '../../../components/BenchmarkFilter.astro';",
        "import BenchmarkChart from '../../../components/BenchmarkChart.astro';",
        "",
        f"{{/* Generated by scripts/export/export-appworld-site.py from each run own curve record. "
        f"Do not edit by hand; last export {stamp}. */}}",
        "",
        "## What it measures",
        "",
        "A simulated world of apps — e-mail, calendar, contacts, a file system, shopping, a music "
        "player — behind REST-like APIs. A task is a natural-language goal that must be reached by "
        "calling those APIs, and it counts as solved only when the official evaluator finds the "
        "world state in the target state: a right-looking answer that never touched the world is a "
        "failure.",
        "",
        f"Study on **{POOLS['train']}** ids from the official `train` split, scored every round on "
        f"the training tasks the method was allowed to learn from; each round is also scored on the "
        f"official `dev` split (**{POOLS['dev']}** tasks), which never enters the search and is the "
        f"pool a round is chosen on. `test_normal` (168) and `test_challenge` (417) are the eval side: "
        f"measured once, on the round that was chosen.",
        "",
        "## Results",
        "",
        "",
        "<BenchmarkFilter",
        '  slug="appworld"',
        "  splits={[" + ", ".join(splits) + "]}",
        "  methods={[" + method_opts + "]}",
        "  taskModels={[{ value: 'qwen3.5-9b-local', label: 'qwen3.5-9b-local' }]}",
        "  metaModels={[{ value: 'deepseek-flash', label: 'deepseek-flash' }]}",
        "  metaHarnesses={[{ value: 'dsh (headless profile)', label: 'dsh (headless profile)' }]}",
        "  baseHarnesses={[{ value: 'loop@0.2.0', label: 'loop@0.2.0' }]}",
        "/>",
        "",
        f"Last export **{stamp}** · {len(methods)} methods · {published} published validation "
        f"points, each recomputable from its per-task verdicts.",
        "",
    ]
    # A curve that stops before its budget is a fact about the clock, not about the method: the
    # 2026-10-20 re-runs were stopped at the 01:00 UTC price boundary and resume from their last
    # measured state. Say it once, generated from the records, so an empty round is not read as a
    # zero or as a finished run.
    budget = 10
    cut = []
    for m in methods:
        last = max((c["round"] for c in m["rounds"] if c.get("train") is not None), default=None)
        if last is not None and last < budget:
            cut.append(f"`{m['name']}` at r{last}")
    if cut:
        lines += [
            f"**Still measuring:** {', '.join(cut)} — stopped at the 01:00 UTC price boundary and "
            "resumable from their last measured state. A round without a number was not measured; "
            "it is not a zero.",
            "",
        ]
    for pool, blurb, alt in (
        ("train", "study split", "Study score by round"),
        ("dev", "validation split, the pool a round is chosen on", "Score by checkpoint round on dev"),
    ):
        h0 = data["reference"].get(pool)
        heading = (f"### `{pool}` · {POOLS[pool]} tasks · "
                   + (f"H0 {h0:.3f} · " if h0 is not None else "")
                   + blurb)
        per = "round" if pool == "train" else "point"
        lines += [
            f'<section data-split="{pool}" data-base-harness="loop@0.2.0" '
            f'data-task-model="qwen3.5-9b-local" data-meta-model="deepseek-flash" '
            f'data-meta-harness="dsh (headless profile)">',
            "",
            heading,
            "",
            f'<BenchmarkChart slug="appworld" pool="{pool}" alt="{alt}" />',
            "",
            f"**Score — `{pool}`, {POOLS[pool]} tasks per {per}**",
            "",
            score_table(methods, pool),
            "",
        ]
        if pool == "train":
            lines += ["**Cost — task-agent: millions of tokens the harness spent acting, per round**",
                      "",
                      exporter.task_cost_table(methods, "train"),
                      "",
                      "**Cost — improver: millions of tokens the method spent editing, per round**",
                      "",
                      exporter.improver_cost_table(methods),
                      ""]
        else:
            lines += ["**Cost — task-agent: millions of tokens the harness spent acting on the "
                      "dev point** (in / out)",
                      "",
                      dev_cost_table(methods, exporter),
                      "",
                      # A dev point runs no improver: validating a checkpoint is agent-only, and
                      # every dev cell on disk has `method_*` null, so this half of the price
                      # cannot be read off the point itself. It still exists -- it is the train
                      # round's editing spend, and it is what produced the checkpoint the column
                      # scores. Printing only the agent half would price an evaluation without
                      # ever editing anything, which is not what a round costs.
                      "**Cost — improver: millions of tokens the method spent editing that "
                      "produced the checkpoint this point validated**",
                      "",
                      exporter.improver_cost_table(
                          methods,
                          note="these are the round's train-side numbers: a dev point runs no "
                               "improver, it only prices the checkpoint that round produced"),
                      "",
                      "\\* round 0 is the shared H0 — one state, measured once and reused, the "
                      "same way the train side reuses it via `--h0-from`. Every method prints "
                      "that one measurement at r0, so the column reads as what it is: a single "
                      "checkpoint, not seven.",
                      ""]
        lines += ["</section>", ""]
    exam = data.get("exam") or []
    # One tab per pool as well: the combined number is the headline, the two pools are the
    # comparison, and pooling them is exactly what hides the difficulty gap.
    for pool in ("test_normal", "test_challenge"):
        lines += [
            f'<section data-split="{pool}" data-base-harness="loop@0.2.0" '
            'data-task-model="qwen3.5-9b-local" data-meta-model="deepseek-flash" '
            'data-meta-harness="dsh (headless profile)">',
            "",
            f"### {pool} · {POOLS[pool]} tasks",
            "",
            f'<BenchmarkChart slug="appworld" pool="{pool}" alt="{pool} score per method" />',
            "",
        ]
        pool_rows = [e for e in exam
                     if (((e.get("by_split") or {}).get(pool)) or {}).get("score") is not None]
        if pool_rows:
            lines += [exam_table(pool_rows, methods, pool), ""]
        else:
            lines += ["Not measured yet.", ""]
        lines += ["</section>", ""]
    lines += [
        "## Contribute",
        "",
        "Bring your own setup — a different base harness, model or improver is welcome as long as it "
        "is declared and H0 is measured on it. Format and verification: "
        "**[Contributing](../../contributing/)**.",
        "",
    ]
    return "\n".join(lines)


SPLIT_OF: dict[str, str] = {}


def split_of_task() -> dict[str, str]:
    """task id -> the release's own split name (`test_normal` / `test_challenge` / ...).

    Needed because the exam is two pools with very different difficulty, and a single 585-task
    average hides that: measured 2026-10-22, `ahe` scored .488 on `test_normal` and .259 on
    `test_challenge`. Reporting one number would make the easy pool pay for the hard one.
    """
    pool = pathlib.Path("/mnt/20t/xubuqiang/datasets/appworld/pool.jsonl")
    out: dict[str, str] = {}
    if pool.is_file():
        for line in pool.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["task_id"]] = row.get("split") or "unknown"
    return out


def collect_exam() -> list[dict]:
    """The exam: each method's chosen round measured on the official test pool (585 tasks).

    Why it is collected rather than assumed: the exam is the only number on this page that is
    scored on material no method ever studied, so it is the number a reader may quote. The run ids
    carry the round (`appworld-exam-<method>-r<N>-<stamp>`), and the point itself carries `n_scored`
    and `n_invalid`; both are read from the record, not recomputed here.
    """
    out = []
    for run in sorted(RUNS.glob("appworld-exam-*")):
        pts = [json.loads(l) for l in (run / "curve.jsonl").read_text().splitlines() if l.strip()] \
            if (run / "curve.jsonl").is_file() else []
        if not pts:
            continue
        point = pts[0]
        name = run.name[len("appworld-exam-"):].split("-r")[0]
        try:
            round_no = int(run.name.split("-r")[-1].split("-")[0])
        except (IndexError, ValueError):
            round_no = point.get("round")
        per = point.get("per_task") or {}
        src = point.get("evaluated_from") or {}
        by_split: dict[str, dict] = {}
        for tid, value in per.items():
            # **Not `name`.** That local already holds the method, and reusing it here silently
            # replaced every row's method with the last task's split name (caught by reading the
            # generated table, not by a test: the export is only exercised when there is exam data).
            pool_name = SPLIT_OF.get(tid, "unknown")
            cell = by_split.setdefault(pool_name, {"passed": 0, "tasks": 0, "score": 0.0})
            cell["passed"] += 1 if value >= 1.0 else 0
            cell["tasks"] += 1
        for cell in by_split.values():
            cell["score"] = round(cell["passed"] / cell["tasks"], 4) if cell["tasks"] else None
        out.append({
            "method": name,
            "round": round_no,
            "score": point.get("score"),
            "tasks": len(per),
            "passed": sum(1 for v in per.values() if v >= 1.0),
            "invalid": point.get("n_invalid") or 0,
            "by_split": by_split,
            "harness_sha": (point.get("identity") or {}).get("harness_sha"),
            "from_run": src.get("run_id"),
            "from_round": src.get("round"),
        })
    return out


def exam_table(exam: list[dict], methods: list[dict], focus: str | None = None) -> str:
    """One row per method: the dev score that chose the round, then this pool's score.

    Emitted as **HTML, not a markdown table**, for one reason: the page's method filter hides
    `[data-method-row]` elements, and a markdown row cannot carry an attribute. A filtered-out
    method whose bar disappeared but whose row stayed would be two answers to one question.
    """
    chosen = {m["name"]: m for m in methods}
    # POOLS order, so `test_normal` sits left of `test_challenge`: the easy pool first is how the
    # rest of the page reads, and alphabetical order puts the harder one first for no reason.
    splits = sorted({name for e in exam for name in (e.get("by_split") or {})},
                    key=lambda n: list(POOLS).index(n) if n in POOLS else len(POOLS))
    head = ("<div class=\"hg-table\"><table><thead><tr><th>method</th><th>chosen on dev</th>"
            "<th>dev (57)</th>"
            + "".join(f"<th>{name}</th>" for name in splits)
            + "<th>eval (585)</th></tr></thead><tbody>")
    body = []
    # **A row whose pool was not fully measured says so on the number itself.** `dream_rsi`'s
    # exam scored 584 of 585 tasks because one task's verifier timed out twice at 900 s; printing
    # three decimals beside a `585` column header claims a coverage the row does not have. The
    # marker follows the page's own convention for the shared `r0 (H0)` cell.
    short: list[str] = []
    for e in sorted(exam, key=lambda x: (x["score"] is None, -(x["score"] or 0))):
        m = chosen.get(e["method"]) or {}
        point = next((c for c in (m.get("rounds") or []) if c.get("round") == e["from_round"]), None)
        dev_score = ((point or {}).get("eval") or {}).get("dev")
        dev_s = "—" if dev_score is None else f"{dev_score:.3f}"
        cells = [f'<td><a href="../../methods/{e["method"]}/"><code>{e["method"]}</code></a></td>',
                 f'<td>r{e["from_round"] if e["from_round"] is not None else e["round"]}</td>',
                 f"<td>{dev_s}</td>"]
        star = ""
        for name in splits:
            cell = (e.get("by_split") or {}).get(name)
            if not cell:
                cells.append("<td>—</td>")
                continue
            mark = ""
            if POOLS.get(name) and cell["tasks"] != POOLS[name]:
                mark = "*"
                star = "*"
                if focus in (None, name):
                    short.append(f'{e["method"]} on {name}: {cell["tasks"]} of {POOLS[name]} tasks')
            cells.append(f"<td>{cell['score']:.3f}{mark}</td>")
        score = "—" if e["score"] is None else f"<strong>{e['score']:.3f}{star}</strong>"
        cells.append(f"<td>{score}</td>")
        body.append(f'<tr data-method-row="{e["method"]}">' + "".join(cells) + "</tr>")
    note = ""
    if short:
        note = ("<p><strong>*</strong> " + "; ".join(sorted(set(short)))
                + ". The missing tasks produced no verdict at all -- the check wrote no "
                  "reward file, or the platform killed the relay -- and stay excluded from the "
                  "denominator. A verifier <em>timeout</em> is not excluded: it is scored 0, "
                  "because a harness that leaves the environment unscorable inside the 900 s "
                  "budget has failed that task.</p>")
    return head + "".join(body) + "</tbody></table></div>" + note


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = collect()
    if args.check:
        print(json.dumps({"reference": data["reference"], "coverage": data["coverage"]}, indent=1))
        return 0

    folder = SITE / "src" / "data" / "results"
    folder.mkdir(parents=True, exist_ok=True)
    SPLIT_OF.update(split_of_task())
    data["exam"] = collect_exam()
    (folder / "appworld.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")

    charts_dir = SITE / "public" / "results" / "appworld"
    charts_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    methods_ = data["stacks"][0]["methods"]
    # The study curve is `cell["train"]`, not an eval pool, so it comes from `svg_train_chart`
    # -- the same split of responsibilities the ALFWorld page uses (its `train.svg` is made the
    # same way, which is why `POOLS` there does not contain `train`).
    charts = [("train", svg_train_chart(data, methods_)),
              ("dev", svg_chart(data, "dev", methods_))]
    exam_rows = data.get("exam") or []
    measured = len(exam_rows)
    note = (f"{measured} of {len(methods_)} methods measured so far"
            if exam_rows and measured < len(methods_) else "")
    charts += [("test_normal", svg_bar_chart(exam_rows, "test_normal", "test_normal score per method", note)),
               ("test_challenge", svg_bar_chart(exam_rows, "test_challenge", "test_challenge score per method", note))]
    for pool, svg in charts:
        target = charts_dir / f"{pool}.svg"
        if svg:
            target.write_text(svg, encoding="utf-8")
            written += 1
        elif target.is_file():
            target.unlink()

    page_path = SITE / "src" / "content" / "docs" / "benchmarks" / "appworld.mdx"
    # No tab points at the combined `eval` chart any more; a leftover SVG is a trap for the next
    # reader ("there must be a reason this file exists").
    stale = charts_dir / "eval.svg"
    if stale.is_file():
        stale.unlink()
    page_path.write_text(page(data), encoding="utf-8")
    print(f"wrote appworld.json, {written} chart(s) and {page_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
