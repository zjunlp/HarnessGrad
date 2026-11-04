# README story review

## Scope and result

Worktree: `/tmp/hg-readme-story-v2`, registered branch `codex/readme-story-v2`.
Starting commit: `16170bb`; initial status was clean. Read root `AGENTS.md`;
there are no nested AGENTS files. Main and the separately handled homepage were
not edited, merged or run.

README is now 200 lines (was 404). Branding is **HarnessGrad: Harness as a
Continual Learner**. The introduction explains the harness as the code organizing
model actions, tools and context, with a fixed solver model and optimizer edits
between feedback rounds. The practical route is install → no-model smoke → local
solver/editor settings → preflight → the checked-in GSM8K/DGM experiment → records
and BYO component guides. Scientific limits are consolidated rather than repeated.
No improvement/retention result or universal compatibility is claimed; the mock
fixture is explicitly interface smoke, not measured learning.

## Preserved reference and integration

`docs/readme-reference.md` retains the full component inventory, dataset storage
resolver rules, refusal/removal history, execution/comparison requirements,
contribution history and generated block, console screenshots/regeneration command,
archived curve inclusion rules and record locations. The old illustrative score
increase is explicitly described as not an experiment receipt; it is not presented
as an expected measured result. No receipt, artifact, registry or screenshot was
deleted or changed.

Legacy README heading anchors remain, grouped under a collapsed reference section,
with links to the moved material. `tools/contributors.py` now regenerates the
reference block with docs-relative links; its records, figures, detail page and HTML
remain unchanged. Contributor tests target the new location and additionally guard
the scheduled workflow's diff/add target. `.github/workflows/contributors.yml`
now stages the reference page rather than restoring foreground README content.
The new docs file matches an existing ignore rule and is force-added deliberately;
no ignore policy change is needed.

## Source checks

Reviewed `pyproject.toml`, `install.sh`, `base_harness/loop/harness.json`,
`tools/configure_harness.py`, `methods/dgm/method.json`,
`harnessgrad/readiness.py`, `experiments/quickstart.json`,
`experiments/method-dgm.json`, and `docs/rsi-experiments.md`.
The documented real config is train/random_5, rounds=1, jobs=1, trials=1, seed=0.
The README adds explicit dependency-overlay preparation, `--improver none` for the
direct endpoint editor, fresh run IDs and `--fail-on-incomplete`. The local endpoint
must already exist; neither install nor preflight is described as starting a model.

## Tests and validation

Executed only static/local focused checks; **no paid calls, model generations,
GPU runs, dataset downloads, dependency installs or actual experiment runs**.

- `python3 tools/contributors.py --write`: regenerated the relocated block;
  existing SVGs/contributor detail/HTML produced no diff.
- `python3 tools/contributors.py --check`: passed, 3 people / 91 entries / 2 months.
- `python3 -m pytest tests/quality/test_documentation_links.py tests/quality/test_documentation_commands.py tests/quality/test_contributors.py tests/quality/test_ci_select.py -q --basetemp=/tmp/hg-readme-story-v2-pytest`: **98 passed in 0.79s**.
- `python3 -m harnessgrad.cli run --help`: passed without starting a run.
- `git diff --check`: passed.
- `wc -l README.md`: 200.

The command checks validate paths/CLI flags statically, not endpoint behavior or
runtime readiness. Real recipe execution was intentionally not attempted.

## Brief journal

1. Confirmed clean registered worktree/branch before reading instructions and source.
2. Found contributor generation/tests depended on README inventory; notified parent
   and scoped the necessary relocation rather than weakening those checks.
3. Rewrote onboarding, preserved technical/history reference, and repaired all
   old heading routes. Consolidated repetitive caveats after parent's draft review.
4. Updated the scheduled contributor refresh target identified by parent and added
   a regression guard; reran focused checks successfully.
5. Committed only this task's README/reference/integration/report files on the
   worktree branch for parent review. No main merge.
