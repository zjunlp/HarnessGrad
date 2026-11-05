# Current-main model source provenance preparation — 2026-11-04

Base: `44200fe8a0d7d976b3d65ed816db7a8532ccd706` (actual main at worktree registration).
Branch: `codex/model-provenance-current-main-20261104`.
Registered tree: `/tmp/hg-model-provenance-current-main-20261104`.
Only the provenance producer and focused tests were extracted from the reviewed
`1ad3d59` → `eee2953` → `a446983` line; none of those old commits or reports was
cherry-picked, and the historical 19 GB hashing manifest is not added here.

## Review changes

The producer binds shard selection to exact index bytes and refuses a mismatch
when independently hashing the index. A final small-index reread now also rejects
drift occurring later during collection. Per-file stat checks are retained; this
is not an atomic snapshot, and a change-and-restore race remains outside that claim.

Only recognized vLLM invocation syntax supplies model declarations. Long-form
model/tokenizer arguments accept separate and equals spellings; repeated flags
and positional-plus-explicit model declarations refuse. Short `-m`/`-t` spellings
inside deployment arguments are explicitly unsupported and refuse, including
mixed-form override attempts, instead of being silently ignored. Module-launch
`python -m` remains supported. This is declaration parsing, not executable or
module authenticity verification. Relative/symlink model path aliases do not
establish an equivalent binding: model and explicit tokenizer declarations must
match the resolved manifest root exactly.

Only allowlisted route/resource/revision flags are serialized. Raw argv,
environment, API keys and unknown credential flags are omitted. Existing output
paths fail before source reads; exclusive creation also refuses a concurrent
output creator. Tests cover both credential spellings, duplicate arguments,
index drift at selection and after hashing, and immutable existing output.

## Bounded read-only observation

No producer full hashing run was performed. Only the 79,657-byte current
`model.safetensors.index.json`, the historical small JSON receipt and current
`/proc` declarations were read. Current index SHA256:
`26d3539b516be613f39563617cb9d33b3f83d401298125be392c80cefb8f7fe5`.
It matches the historical index entry and its shard set matches the historical
manifest inventory. Current PIDs 3799919 / 3802962 and start ticks 831477189 /
831489254 pass the current strict parser and equal the historical safe bindings:
model `/mnt/20t/qzs/PLMs/Qwen3.5-9B`, served alias `qwen3.5-9b-local`,
max length 262144, GPU memory utilization `0.80`, ports 8001 / 8002.

Historical hash source is the unchanged, read-only
`/tmp/hg-sol-model-provenance-review-fix-20261104/reports/qwen35-source-provenance-20261104.json`,
originally produced in `1ad3d59` by the earlier producer, not this revision.
Canonical digests recomputed from that historical JSON are:

- weights: `93b4d26b9b80752358557114ddb4845f7f062865ef361b9ebbeb0e6156e91cdc`
- tokenizer: `58a188315ecbd022fa3059d8a2489c74ef53f1af06d7a54aa8ee7a6b996b01d0`
- deployment: `a8d9ade421be407c41183a923de002a3be397e5bf7dbb820d8264d66ec316d73`

These are historical manifest digests, not fresh current weight/tokenizer/config
hash measurements. No 19 GB reread, inference, endpoint request, network access,
service change, data download or experimental resumption occurred. Source bytes
and process declarations do not attest live GPU contents, GPU mapping, binary
identity, transitive dependencies or upstream revision. This preparation supplies
no execution authorization and does not repair historical measured identities.

## Validation

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/quality/test_local_model_provenance.py --basetemp=/tmp/hg-model-provenance-current-main-tests-final`

**29 passed in 0.07s**; tests use synthetic small sources and mocked process
arguments, plus the unrelated current Python process rejection. `git diff --check`
passes. Main, old worktrees and historical receipts were not modified.
