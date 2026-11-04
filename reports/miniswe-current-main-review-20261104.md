# MiniSWE standalone increment on current main — 2026-11-04

Base: `44200fe8a0d7d976b3d65ed816db7a8532ccd706` (rechecked before work and
at completion). Registered worktree `/tmp/hg-miniswe-current-main-review-20261104`,
branch `codex/miniswe-current-main-review-20261104`. Main and old review worktrees
remain unchanged. This is an independently reviewable increment, not an aggregate
merge or experiment restart.

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
