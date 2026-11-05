# MiniSWE standalone increment on current main — 2026-11-04

Base: `44200fe8a0d7d976b3d65ed816db7a8532ccd706` (rechecked before work and
at completion). Registered worktree `/tmp/hg-miniswe-current-main-review-20261104`,
branch `codex/miniswe-current-main-review-20261104`. Main and old review worktrees
remain unchanged. This is an independently reviewable increment, not an aggregate
merge or experiment restart.

The original increment described below is commit `d015f17`. A separate dependency
verification fix follows it; its later source snapshot is recorded at the end.

## Current-state integration

Current main already contains the native mini-SWE-agent 2.4.6 adapter. Its manifest
still had install=null, a host Python 3.12 user-site runtime mount and files-only
admission. No adapter execution logic needs to change: the current platform already
supports executing-interpreter overlays. The increment adds requirements pins
mini-swe-agent==2.4.6 and litellm==1.75.9, removes the host-ABI mount and declares
files plus ordinary exec. The adapter changes only its dependency documentation.
Native prompts, context behavior, tools, defaults and strategy remain unchanged.

The standalone regression is MiniSWE-only; it does not change the shared
MiniAgent/OpenClaw integration test. No MiniAgent, DSH, recovery, campaign, general
configure verifier or platform runtime changes are included. The pre-existing
configure import-banner issue is documented, not silently worked around in its
verifier. Dependency overlays must be separately provisioned; no installation or
network request was performed in this increment.

## Fresh verification

```bash
HG_UAT_EVIDENCE_ROOT=/mnt/20t/xubuqiang/HarnessGrad_experiments_result/h0-review-followup-20261104/miniswe-current-main-44200fe/final2 \
PYTHONPATH=/home/xubuqiang/.cache/harnessgrad/overlays/d9f65f472a3ed40a/3.12 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
 tests/quality/test_miniswe_harness.py tests/quality/test_miniswe_exec_platform.py \
 tests/quality/test_base_harness_components.py \
 --basetemp=/tmp/hg-miniswe-current-main-final2
```

**50 passed in 11.62s**. `git diff --check` passes. A previous intermediate
candidate also passed 19 tests; it is preserved in the sibling final directory,
but only final2 includes the complete current candidate README identity.

The real ordinary run_one uses actor
`sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb`
and genuine container_observation.observe_for Docker/kernel receipt (network none,
2 CPU/4096 MiB default envelope), without diagnostic admission bypass. The runtime
records Python 3.11, mini_swe_agent 2.4.6, litellm 1.75.9 and host site packages
invisible; the actual native submission trajectory independently reports 2.4.6.
Native mock drives a shell artifact and completes. Candidate edit/probe verifies
read-only source, correct cwd, hidden platform and absence of private verifier
inputs. Fresh post-exit synthetic verifier gets its token and validates the
artifact/probe. Trace contract, usage, tool observations and unchanged staged and
submitted tree identity pass.

Evidence: `/mnt/20t/xubuqiang/HarnessGrad_experiments_result/h0-review-followup-20261104/miniswe-current-main-44200fe/final2/miniswe-exec.json`.
Its SHA256 is `5727a87edca9ebd5c50786f58fb51a6d732cba1943b8ce26b6ec9bb42cc18d9e`;
edited candidate tree is `ca153c0116e106d9e177308900ce715324611a00`.
All five base-harness file hashes were independently compared against the final
current files and match exactly. Evidence writes are exclusive; use a fresh path
for reruns.

## Scope

No real model, benchmark, official AppWorld scorer/services or campaign ran.
This is one ordinary backend/image/ABI interface check with a synthetic verifier,
not official AppWorld measurement, all-image/observed-worker compatibility,
complete lifecycle isolation proof or production authorization. The existing
terminating submission sentinel remains an unexecuted trajectory action. New
experiments must freeze a new identity after this manifest edit. This increment
requires no other pending review branch; only separately provisioned dependencies
and the existing pinned actor/ordinary platform are needed.

## Separate dependency-verifier follow-up after d015f17

Parent review required the import-banner tooling blocker to be closed as a separate
commit. The narrowly scoped `tools/configure_harness.py` fix from reviewed
`5167b28` is now carried separately after the MiniSWE increment: imports emit one
`HGIMPORTPROBE` JSON result, ordinary startup stdout is ignored, and missing,
malformed, repeated, actual-error or failed-process results refuse. Seven
regressions cover real fixture imports (banner, RuntimeError and SystemExit) plus
missing/non-list/repeated/nonzero probe outcomes. Container verification also
specifies `--pull=never` and network none. No package installation code, native
agent logic, prompt, context policy or scoring is changed.

The MiniSWE README was corrected to describe this resolved verifier limitation.
That documentation changes candidate identity after the earlier final2 mock;
final2 remains the exact d015f17 snapshot and is not relabeled as the later one.
A fresh final3 native mock was therefore run on the updated candidate.

Combined focused command adds `tests/quality/test_configure_import_output.py` to
the previously documented test set, uses
`HG_UAT_EVIDENCE_ROOT=/mnt/20t/xubuqiang/HarnessGrad_experiments_result/h0-review-followup-20261104/miniswe-current-main-44200fe/final3`
and `--basetemp=/tmp/hg-miniswe-current-main-dependency-fix-final`:
**57 passed in 11.54s**. All five base file hashes again match current candidate
files exactly. Final3 edited tree: `b997d7ae3f049fb86babdb2ea7386b82bd967a1a`;
`miniswe-exec.json` SHA256:
`0e3353062a4f685703b1aa1910f2c2ccb9a80ce6f8ba98cb7785a4e360524e09`.

Actual existing MiniSWE Python 3.11 overlay verification also passed against the
same pinned, locally inspected actor: **77 modules import with
/usr/local/bin/python3**. The call was direct `verify_one` with
`HG_OVERLAY_ROOT=/home/xubuqiang/.cache/harnessgrad/overlays`, version 3.11 and
install SHA256 `d9f65f472a3ed40ac268562c8a25b8e0a31a70e70c28e187af61dd57ce0097c2`.
No builder, install, model or endpoint request ran. Evidence:
`final3/overlay-import-verify.json` under the above root. It records verifier
source SHA256 `88eea651072495b8bb7c3809367b1cda501e7ade2cdc6032eaf3c0fddc71ab08`
and overlay provenance SHA256
`f4b49e319ed868d29e57171420d987b25a903c6d2fecd0f034d8fd3dee18045c`.

The prior scope limits remain: import/native mock compatibility is not official
AppWorld scoring, full lifecycle measurement or authorization to run experiments.
Main and old review worktrees remain unchanged; this fix is independently
reviewable from the original MiniSWE manifest/ABI increment.
