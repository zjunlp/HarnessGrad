# Review / validation receipt

Worktree: `/tmp/hg-flashrsi-methods`
Branch: `codex/flashrsi-methods`
Starting platform commit: `a100139`
Source: FlashRSI `e9c7667945f959d62da27204649a82f94d1b6705`

## What changed

Ten missing algorithm IDs now have external method entrypoints, manifests,
source-linked fidelity declarations and distinct policy mechanisms. Shared
artifact/JSON-role machinery lives in `methods/flashrsi_ports/runtime.py`; pure
archive, frontier, GA/DE and preservation rules live in `policies.py`.
Original strategies/prompts and the Apache-2.0 license are retained for review.
The existing platform/evaluator/driver and existing methods are not changed.

## Why

FlashRSI's native evaluator callbacks cannot be imported unchanged into a
HarnessGrad method: the platform owns benchmark execution and scoring. These
ports use measured study history between calls, preserve real candidate trees,
and explicitly identify themselves as policy ports rather than reproductions.

## How verified

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_{ace,adas,arbor,darwinx,evo_harness,evoprompt,gepa,modular_rsi,opro,skillopt}_rule.py \
  tests/quality/test_flashrsi_method_{integration,fidelity}.py \
  tests/quality/test_method_lint.py \
  --basetemp=/tmp/hg-flash-method-delivery
PYTHONDONTWRITEBYTECODE=1 python3 tools/method_lint.py
git diff --check
```

Observed: **50 tests passed in 6.70 seconds**; method lint **26 OK, zero failures**;
whitespace check passed.

The focused checks cover different behaviors, not just registration:

- OPRO ranks measured instructions, rejects repeated candidates and caps its
  batch while reserving a measured-state deployment slot.
- GEPA's actual frontier/pruning code agrees with the pinned native source over
  100 randomized populations; pairing and strict admission are tested.
- EvoPrompt GA standard vs top-k differ, DE preserves target positions, and final
  deployment restores a global best that standard GA could have displaced.
- DarwinX rejects lost solved tasks, keeps rejected complementary merge donors,
  forbids those donors as mutation parents, and inherits invariants per lineage.
- ACE invokes the unmodified ADD parser and preserves the original prompt.
- ADAS performs two reflections against the already edited design tree.
- Arbor grows two tree levels before execution; a measured regression gets
  reflection, bottom-up synthesis and an actual trunk restore on stop.
- ModularRSI refuses missing real repeated-trial evidence and rejects edits
  outside the declared module.
- SkillOpt splits success/failure reflection, merges proposals, protects slow
  guidance from fast patches and preserves optimizer meta-memory in a slow phase.
- EvoHarness writes native discoverable skills and refuses provenance from tasks
  outside the current reflected memory batch.
- Unknown native knobs fail rather than being silently accepted.

The integration check launches **the real driver and method sandbox** with a
local scripted OpenAI-compatible endpoint and a human-authored demo harness. The
platform's own scores are `0 -> 1 -> 0 -> 1`. The final `harness_sha` equals the
previously measured winning candidate, proving actual restoration/deployment.
There are exactly two scripted optimizer requests, no real model calls, no GPU
work, and no paid endpoints. The test secret is absent from the curve.

## Remaining limits

These checks do not establish published-paper result reproduction or benchmark
quality. Native separate-dev fitness, fresh screening/confirmation, dynamic
on-demand evaluator callbacks and reference-answer feedback remain unavailable
and are listed per method in `method.json` and the stage table in `README.md`.
ModularRSI still needs explicit module paths and platform repeated trials. JSON
roles need a JSON-capable method endpoint; CLI edit conversion is only used for
source-edit roles. No branch was merged into the operator's main checkout.
