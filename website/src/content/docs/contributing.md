---
title: Contributing results
description: How to submit a measured harness-improvement result, what we verify before publishing it, how trajectories are mirrored, and how credit works.
---

This site publishes **measured** results: a score that can be traced to per-task verdicts and to
the exact harness that earned it. If you have a method that improves a harness on one of the
benchmarks here, we want it on that benchmark's page with your name on it.

## Two things you can contribute, and where each one lives

| what you have | where it goes | how to send it |
| --- | --- | --- |
| **artifacts** for a run — the harness, the edit that produced it, the per-task trajectories | the dataset on the Hub, under `Experiments/<benchmark>/` | [open a pull request on the Hub](https://huggingface.co/datasets/Xubqpanda/HarnessGrad/tree/main/Experiments?new_pr=true) |
| **a one-file record** of your numbers, to appear as a row on a benchmark page | this repository, `contrib/results/<benchmark>/<method>/<variant>.json` | the JSON below, as a pull request or an issue |

One is not a substitute for the other: a dataset pull request is data somebody can re-run, and the
JSON is a row that appears in a table. Send both and the row links to your data.

### What a dataset pull request contains

One directory per **experiment family** — the task agent being improved, then the meta agent doing
the improving, then your method:

```
Experiments/<benchmark>/
  <task-agent>/                     e.g. qwen3.5-9b_loop   (agent model + base harness)
    <family>/                       e.g. dsh-your-model    (improver harness + improver model)
      family.json                   what the meta agent was, and the baseline round 0 hit
      <method>/
        curve.jsonl                 one record per round: score, per-task verdicts, identity
        harness/round-N/            the harness deployed in round N — the tree, not a diff
        patches/round-N.patch       the edit that produced round N
        tasks/round-N.tar.gz        one JSONL per task per trial
```

Copy the shape of an existing family rather than inventing one: the manifest fields and the
round-0 baseline are what make a result comparable to the ones already published.

What we check before merging, mechanically rather than by reading:

- every `curve.jsonl` record names the same task agent and meta agent as the manifests above it;
- round 0 is the **same baseline** as every other family (`harness_sha` and config), because that
  is what makes a comparison across families a comparison at all;
- every `harness/round-N/` recomputes to the `harness_sha` that round reports;
- every `patches/round-N.patch` joins the two states it claims to join;
- the coverage file matches the files actually present.

A pull request that fails this is not merged as data. If your numbers still have per-task records
behind them, they can be published as a row marked **unverified** (see below) — we will not
publish a score whose artifacts we could not check.

## What a submission contains

A submission is one JSON file plus, optionally, a link to the trajectories. It is **not** a
patch to our code, and it carries no large files.

```json
{
  "schema": "harnessgrad.submission.v1",
  "dataset": "alfworld",
  "task_agent": {
    "base_harness": "loop@0.2.0",
    "model": "qwen3.5-9b-local",
    "decoding": "greedy (temperature=0)"
  },
  "improver": {
    "method": "your-method@0.1.0",
    "model": "deepseek-flash",
    "harness": "dsh (headless profile)",
    "rounds": 10
  },
  "method": { "name": "your-method", "paper": "https://…", "code": "https://…" },
  "variant": "greedy-local-9b",
  "platform": { "commit": "<sha of this repository>", "content_sha": "<platform content sha>" },
  "runs": [
    {
      "run_id": "<your run id>",
      "harness_sha": "<content hash of the measured harness>",
      "round": 3,
      "score": 0.9321,
      "n_tasks": 134,
      "n_invalid": 0,
      "per_task": "results/<dataset>/<method>/<variant>/per_task.json"
    }
  ],
  "config": {
    "study_ids": "train100.csv",
    "rounds": 10,
    "jobs": 12,
    "slots": 48,
    "sampling": "all",
    "trials": 1,
    "endpoint": "http://127.0.0.1:8002/v1",
    "model": "qwen3.5-9b-local",
    "temperature": 0,
    "seed": null
  },
  "trajectories": {
    "location": "https://huggingface.co/datasets/<you>/…",
    "format": "one JSONL per task, one record per step",
    "bytes": 12345678,
    "sha256": "<sha256 of the archive you want mirrored>"
  },
  "credit": {
    "name": "Your Name",
    "affiliation": "Your Institution",
    "link": "https://your.site",
    "submitted": [{ "dataset": "alfworld", "method": "your-method" }],
    "acknowledge_in_report": true,
    "contact": "optional: email, or your WeChat id"
  }
}
```

The authoritative schema is [`contrib/submission.schema.json`](https://github.com/zjunlp/HarnessGrad/blob/main/contrib/submission.schema.json)
and a filled-in example is [`contrib/example-submission.json`](https://github.com/zjunlp/HarnessGrad/blob/main/contrib/example-submission.json).

## How to submit

0. **Run the validator first**: `python3 contrib/validate.py <your-file>.json`. It checks the
   schema, not the numbers — a file that fails it cannot be reviewed at all.
1. **Open a pull request to this repository** that adds
   `contrib/results/<dataset>/<method>/<variant>.json` (the file above) and the small
   `per_task.json` it points at. Keep the trajectories **out of this repository's git**: either
   send them as a dataset pull request ([above](#two-things-you-can-contribute-and-where-each-one-lives)),
   or put them somewhere you control (your own HuggingFace dataset, Zenodo, an institutional
   archive) and link them here.
2. If a pull request is not convenient, **open an issue** using the submission template and
   attach the same JSON. We will open the pull request for you, with you as the author of the
   data.
3. A maintainer reviews the numbers with the platform's own validity gate (below), asks at most
   one round of questions, and merges. The page is regenerated from the merged JSON, so the site
   and the record cannot disagree.

## Your stack does not have to be ours

The three things that define a measurement are the benchmark, the **task-agent** (the base
harness being improved, plus the model that drives it), and the **improver** (the method, the
model the improver itself used, and the harness the improver ran under). A result on a different
base harness, a different model, or a different improver is welcome — that is the point of an
open table.

What it must not do is pretend to be comparable across those lines. So:

- every row declares its stack, and the page groups rows by stack;
- the frozen reference (H0) is required **per stack**, because H0 on `loop` with a 9B local model
  says nothing about H0 with another agent;
- rows from different stacks are shown side by side and **never merged into one ranking**;
- if you cannot measure H0 on your stack, the row is published as an absolute score marked
  *no reference*, not as an improvement over ours.

## What we verify

Each of these is checked against the files, not asserted:

- **Task set**: the ids you scored are exactly the benchmark's declared set for that pool
  (`valid_unseen` is 134 ids; `valid_seen` 140; `valid_train` 200). A point that measured 139 of
  140 is excluded, not averaged.
- **Validity**: `n_invalid == 0` and `n_harness_failed == 0` for every published point.
- **Score**: recomputed from your `per_task.json`, and it must equal the reported `score`.
- **State**: `harness_sha` resolves to the same harness content the run was measured with, and
  the run's `run_meta.json` records the platform content sha you declared.
- **Config**: what you declare matches what the run recorded (study ids, rounds, trials,
  sampling, model, decoding).

A submission that does not pass is **published as `unverified` if you want it published at
all** — clearly marked, with the reason — never silently dropped and never silently accepted.
Verified and unverified rows are never mixed in one table.

## Trajectories, and why they are mirrored later

Trajectories are large, so they never enter git. The flow is:

1. you either add them to our dataset with a pull request, or upload them wherever you prefer
   and link that location in `trajectories`;
2. we download, check the archive hash and the file format, and verify that replaying the
   per-task records reproduces the same verdicts;
3. **after verification we mirror the archive into our own HuggingFace dataset**, and the
   merged JSON is updated to point at the mirrored copy with `verified: true` and
   `mirrored: true`. Both the original link and ours stay in the file, so the provenance is
   readable.

Mirroring is what makes a published row durable: a personal drive link that later disappears
would leave a number nobody can check. Until the mirror exists, the row says
`mirrored: false` and the page links to your copy.

## Credit

Each published row lists:

- **name (affiliation) + link** — as you write them in `credit`;
- **what you submitted**, as `dataset × method`, because a contributor who measured three
  benchmarks deserves three mentions;
- whether the row is **verified by the platform** or **unverified**.

Verified contributions **may be acknowledged in the technical report** of this project, with
the same name and affiliation. Set `credit.acknowledge_in_report` to `true` if you want that;
setting it to `false` keeps your row on the site and leaves you out of the report.

`credit.contact` is optional and never published. The maintainer's WeChat is
**Blue18067792208** — add it if you would rather discuss the submission directly than in the
pull request; say which dataset the message is about.

## What we will not publish

- a total score with no per-task record behind it;
- a number measured on a different task set, presented as if it were this pool's;
- a re-run of somebody else's method reported under your name without their agreement;
- a row whose trajectories cannot be produced on request.

These are not formalities. A benchmark page is only worth reading if every row on it can be
re-run by someone who does not trust us.
