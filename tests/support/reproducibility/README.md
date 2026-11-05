# Historical experiment-specific controls

These scripts support focused regression tests and preserve narrow historical
protocols. They are **not generic user-facing launchers**, a benchmark result,
or authorization to run an experiment. Their old public `tools/` entrypoints
have been removed; invoking these controls requires independent review of data
scope, credentials, runtime and budget. The public AppWorld identity and
recovery primitives remain in `tools/appworld_campaign_state.py` and
`tools/launch_appworld_harness_baselines.py`.

- `lcb740_identity.py` and `launch_lcb740_candidate.py`: historical public-only
  LiveCodeBench row identity and bounded isolated control. A supplied index hash
  does not itself establish external custodian approval.
- `run_appworld_harness_campaign.py`: the reviewed historical four-pool pilot
  protocol with its fixed cohort sizes. Existing receipts are path/commit bound;
  relocation is not a license to rewrite them or resume an old experiment.

The original scripts and their SHA-256 digests were separately archived before
relocation. Runtime/scorer fixture custody is not repinned by this move.
