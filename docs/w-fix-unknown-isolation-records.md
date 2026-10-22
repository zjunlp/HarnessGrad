# Preserve unknown isolation evidence without permitting comparison

Commit `72f5c37` made `common_class` reject nonempty all-unknown inputs. That
helper also runs while writing progress, aggregating trials/tasks/cache entries,
and constructing or saving curves. Consequently an authorized diagnostic lost
its progress JSON and trial traces: `_write_progress` caught the exception and
printed a warning, then the runner raised again. Rejecting a comparison must
not discard the evidence needed to understand why comparison is invalid.

The corrected contract separates recording from comparison:

- `common_class` normalizes absent, unsupported and malformed classes to
  `unknown`. Empty or uniform unknown inputs return `unknown`; uniform observed
  inputs return that observed class. Mixed classes still raise
  `IsolationMismatch`, including a known class mixed with `unknown`.
- Runner aggregates, progress JSON and curve points carry both
  `scorer_isolation_class` and the boolean `scorer_isolation_comparable`.
  Unknown topology always means `scorer_isolation_comparable: false`, even when
  raw scores exist or the result is empty. A known uniform class yields `true`
  **only for this topology prerequisite**; it does not establish matching
  metrics, splits, models, environments or policy admission.
- `require_comparable` explicitly raises for unknown observations. It also
  retains mixed-class refusal through `common_class`; a claimed comparability
  flag cannot supply an observation or bypass refusal.
- `_write_curve` checks the entire history for mixed classes before opening
  the destination, then derives each point's normalized class and comparability
  flag from its recorded observation. This also labels external/legacy points
  and overrides an unsupported `true` claim without mutating input dictionaries.
  Reading a legacy file does not rewrite it.

For example, a completed diagnostic may retain a raw `score: 0.25` alongside
`scorer_isolation_class: "unknown"`, `scorer_isolation_comparable: false`,
`trial_scores: [0.25]` and `complete: true`. Completion describes persistence of
the task result, not comparable measurement. Existing trial counts, per-trial
classes, verdict summaries, diagnostic enforcement and trace files still survive.
The authorized unobserved diagnostic still cannot publish a comparable curve,
populate its measurement cache, authorize expansion or claim measured coverage;
its existing policy and receipt gates are unchanged.

The invalidated assertions in `test_runtime_observations_w_obs.py` expected
runner exceptions, discarded progress, and refusal to construct/rewrite an
unknown-only curve. They asserted the regression. Their replacements require
complete, explicitly noncomparable evidence and continued comparison refusal.
Existing legacy caches remain unchanged when read; fresh raw facts may be cached
without acquiring an observed class. The analogous curve-construction assertion
in `test_scorer_isolation_option_b.py` and legacy-cache refusal assertion in
`test_shared_admission_wiring_codex163.py` receive the same correction. The latter
retains cache immutability and all selected-task/admission assertions. No mixed-class
or comparison-refusal assertion is removed. Added controls check mixed progress
and history refusal, trace preservation and a falsely claimed comparability flag.
The c2 tests gain assertions for the restored diagnostic progress and its label.

The stepwise ChessBench executed-class implementation and its tests remain
unchanged. No validator or admission gate changes. The registry changes only its
now-inaccurate mechanism description; blocker sets and authorization stay intact.
This document supersedes the write-path refusal prescription in
[the w-obs review](w-obs-runtime-review.md), preserving its comparison-safety intent.
Exact baseline, corrected-suite, checker and pristine-control outputs are in
[reports/w-fix-validation.txt](../reports/w-fix-validation.txt). Synthetic tests
do not authorize a production run or establish live runtime isolation.
