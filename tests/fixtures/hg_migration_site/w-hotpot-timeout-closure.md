# HotpotQA timeout outcome/persistence/aggregation closure - w-hotpot

Closed: `hotpotqa / timeout-aggregation-admission`, by focused synthetic
validation only. No HotpotQA run, selected control, model or GPU was used.

At baseline `bd25f5a`, native verifier expiration returned timeout detail without
`unmeasured` (`eval/verification/verifier.py:609`); the score fallback in
`eval/run/runner.py:1197` converted it to zero. The final regression tests fail
**12/12** against those baseline production sources: a single timeout becomes
zero, and a three-trial sequence containing a timeout becomes a false 2/3.
Exact commands and outputs, including early fixture corrections, are preserved
in `reports/w-hotpot-validation.txt`.

The repair marks expiration `outcome="timeout"`, `unmeasured=<reason>`, without
a score, in the native/sandbox/exec catch (`eval/verification/verifier.py:610`)
and snapshot catch (`eval/verification/verifier.py:313`). The runner retains
the outcome in `invalid` (`eval/run/runner.py:1102`), writes invalid progress
without a score or cache entry (`:1121`), and excludes it before trial/task
aggregation (`:1179`). The existing invalid writer saves the structured reason
and diagnostic trace (`eval/observe/tracing.py:374`).

`tests/quality/test_hotpot_timeout_admission_w_hotpot.py:61` defines
`test_timeout_is_unmeasured_through_persistence_and_aggregation`; four mocked
transports cover one trial and expiration after a successful trial. Assertions
check reloaded progress JSON, invalid trace, absent scores/cache, stopped trials,
and retained measured-zero controls. `:110` defines
`test_timeout_only_round_has_no_measurement_or_cache` for all four transports.
All **12 pass** after the repair. The existing hg-b3 characterization is updated
to assert the repaired behavior without rewriting its historical review.

```sh
python3 -m pytest -q tests/quality/test_hotpot_timeout_admission_w_hotpot.py
python3 tools/check_dataset_blockers.py
```

Still open: current selected scorer/control admission and the three existing
shared requirements. Synthetic transports establish classification and storage,
not runtime enforcement. The validator admits only active blocker statuses
(`tools/check_dataset_blockers.py:16`), so the closed ID is retired and its
closure evidence is retained in `historical-comparison-receipts.observed`, with
the remaining receipt requirement marked `measurement`. The checker is unchanged.

Historical comparison still requires original selected-ID, trial/error/timeout
and source/split/manifest/model/harness provenance receipts, plus lifecycle and
cleanup reconciliation. The old 492×3 protocol remains incomplete, unknown run
ID (`run_id=null`), `effect=null`; reported 94/1476 completion is uncertified,
with 1382 planned task-trials unaccounted for. No receipt was changed or run
resumed/repeated. Any separately authorized future diagnostic needs a fresh ID,
current staged policy and all applicable admission gates.
