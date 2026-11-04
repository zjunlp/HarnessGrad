# Generating the pages that carry numbers

The benchmark and method pages are **generated**, not written: each one is a projection of run
records, and the comment at the top of the generated file names the script and the time it ran.
Hand edits are overwritten by the next export.

```
export-site.py            ALFWorld  -> src/data/results/alfworld.json,
                                      public/results/alfworld/*.svg,
                                      src/content/docs/benchmarks/alfworld.mdx
export-appworld-site.py   AppWorld  -> src/data/results/appworld.json,
                                      public/results/appworld/*.svg,
                                      src/content/docs/benchmarks/appworld.mdx
export-methods.py         Methods   -> src/content/docs/methods/*.mdx
                                      (reads the results JSON above, and each method's own
                                       source in the platform checkout)
```

## What they need

| Variable | Meaning | Default |
| --- | --- | --- |
| `HG_RUNS` | directory of run records, one subdirectory per run id (`curve.jsonl`, `run_meta.json`, traces) | `scripts/export/runs`, which does not exist — the exporters refuse rather than write an empty page |
| `HG_PLATFORM` | the platform checkout, for `methods/<name>/run.py` and `method.json` | unset; `export-methods.py` refuses without it |
| `HG_SITE` | output root | the repository root (this file is in `scripts/export/`) |
| `HG_GENERATED_AT` | pin the provenance line, for a byte comparison | now |

Two kinds of input are **committed**, because they are small and a page cannot be rebuilt
without them:

- `split.stamp` — which split of the study the pages are for;
- `reference-alfworld-<pool>.json` — the H0 reference score per ALFWorld pool, each one a
  measured round (`run_id`, `n_scored`, `n_passed`, `score`).

The run receipts themselves are **not** in any repository: they are tens of GB of per-round
traces, and the published copies live in the
[HuggingFace dataset](https://huggingface.co/datasets/Xubqpanda/HarnessGrad) the pages link to. A clone
can rebuild the site from the committed `src/data/results/*.json`; regenerating those needs the
receipts.

## The check that matters

An export is reproducible only if it reproduces the committed files byte for byte:

```bash
# Run from a clean, registered review worktree based on website, not platform main.
git status --short   # confirm clean before exporting
HG_RUNS=<receipts> HG_PLATFORM=<platform> HG_GENERATED_AT=<the stamp in the page> \
  python3 scripts/export/export-site.py
HG_RUNS=<receipts> HG_GENERATED_AT=<the stamp in the page> \
  python3 scripts/export/export-appworld-site.py
HG_PLATFORM=<platform> HG_GENERATED_AT=<the stamp in the page> \
  python3 scripts/export/export-methods.py
git status --short   # empty == the pages are what the receipts say
```

Measured 2026-10-27 against the receipts that produced the 2026-10-23 export: all three
reproduce byte for byte with `HG_GENERATED_AT` pinned. The only diff is the provenance comment,
which now names `scripts/export/` instead of the working directory the export used to live in.

Each page carries its provenance in the data: `generated_at`, `platform_content_sha` (the
platform tree the run was measured under) and the run ids per round, so a reader can tell a
re-export from an edit even when the numbers are identical.
