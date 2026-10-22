# Task 206: 2WikiMultihopQA source-gate review

The pinned evaluator source gate is closed. Fresh `ujson`/native dependency
closure, Hub/data provenance, historical metric comparability and shared runtime
gates remain blocked. This review creates no benchmark measurement or production
authorization.

Reviewed only `/tmp/hg-codex-206` at base
`65b0f2a5bf95fdafd218ca54198843c27b9fffbe`, including merged fixture commit
`349d74f1d930bcb4c6664b1f6521571f118a619a`. The inherited Codex 197 report was
checked against its committed fixture/tests and a fresh test run. No benchmark
task, answer, alias-table, split or heldout payload, external benchmark receipt,
adapter import, production scorer rewrite, network request, model or H0 was used.

The [original fixture](../tests/fixtures/twowikimultihopqa_upstream_codex197/README.md)
preserves both evaluators, Apache-2.0 license, requirements and GitHub commit/tree
metadata at upstream commit `209e761834d1ce055856fc11072c0a5aab47601d`, root tree
`545a956af257f0c79c68c39a358086de7cbf243b`. The six pinned files pass size/SHA-256
checks; tests also reconstruct the tree and code/license/requirements blob
identities. Original evaluator SHA-256 values are:

- v1.0: `854b03c487608286d6381379eb32e46d9b845493527d1fe1951d0bf4ec923a40`.
- v1.1: `7c09ee2ec98c7ecb57df79eb68dba22eb381ab58d50e730dfcefafb4dda31458`.

The commit is unsigned. This is a review of the preserved GitHub HTTPS
acquisition evidence, not a fresh origin attestation or archive revalidation.
The original failed raw-host acquisition remains a historical failed attempt.
The earlier journal's missing-source status is superseded only for this fixture.

Both entire unchanged evaluators execute with real `ujson==5.10.0` on fabricated
inputs. Source/license/metadata drift and missing license fail closed. Tests deny
sockets, restrict evaluator opens to generated synthetic files, and compare the
adapter transcription as text without importing it. `f1` is Answer F1;
`joint_f1` is the harmonic mean of answer/support/evidence product precision and
recall. Their separate identities and upstream alias/evidence quirks are retained.
These checks establish neither complete wrapper equivalence nor leaderboard
comparability.

Fresh dependency closure remains blocked: neither pinned PyPI wheel nor sdist
payload was acquired, and the installed extension is not linked to a published
artifact. Direct native dependencies (`libstdc++.so.6`, `libgcc_s.so.1`,
`libc.so.6`) in the inherited receipt do not attest loader/transitive closure.
The custodian must provide the compatible pinned wheel/licenses, installation
origin and supported Python/native-runtime image receipts, followed by fresh
offline imports/synthetic execution. An sdist build also needs build closure.

Hub/data provenance remains blocked: source/license linkage authenticates no
Hub/release revision, split/selected manifest, alias identity/completeness,
dataset redistribution rights or release-to-evaluator mapping. These require
independent metadata-only custody evidence.

The registry retires `independent-evaluator-source` from its active-only schema
and records `ujson-native-offline-closure` and `hub-data-provenance` as `external`.
`recovery-comparability` and all eight shared blockers remain active. All
historical records, including 2Wiki's original mixed Answer F1/Joint F1 pilot
with 48 scored tasks and two invalids, remain unchanged with `effect=null`.
Historical original/recovery scores remain unverified journal claims; no receipt
was reopened or rescored.

Fresh verification used CPython 3.12.4, Linux x86_64, pytest 7.4.4 and
`ujson==5.10.0` (no declared Python runtime dependencies). The installed native
module SHA-256 matches the inherited receipt:
`41ef15691cb490d5c38cde5f7e588d9d348cb92008888a0dcb2770ae84ea87db`.
The unchanged provenance receipt SHA-256 is
`548134a016566217b1f87df1c95db7eba9e7c87ac9e5cb02f08cfff564bff13c`;
the unchanged provenance test SHA-256 is
`ed170d738e6f106cbbea3ba72a18561ec8a99451a130c47a0748431da8b6465b`.
These are local identity checks, not fresh installation/deployment evidence.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-twowikimultihopqa-codex206-pytest tests/quality/test_twowikimultihopqa_provenance_codex197.py
```

Exit 0:

```text
......................................                                   [100%]
38 passed in 0.24s
```

Registry checks:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-blocker-registry-codex206-pytest tests/quality/test_dataset_blockers.py
```

Both exited 0. Checker output:

```json
{"valid": true, "datasets": 28, "shared_blockers": 8, "historical_records": 11, "production_authorized": false}
```

Registry tests: **49 passed in 2.92s**. Total: **87 passed**, no failures or skips.
The source-only registry suite denies adapter imports, external file reads,
sockets and subprocess execution. A separate in-memory comparison against `HEAD`
confirmed that only 2Wiki's active blocker list changed: taxonomy, review/shared
objects, every other dataset, recovery comparability and every historical record
are identical. The complete earlier journal is an unchanged prefix. Fixture,
test and production source files have no diff. `git diff --check` passed.

Cleanup receipt: both task-specific scratch roots were absent before use,
created only for this review, removed after their results were preserved above,
and verified absent: `.scratch/twowikimultihopqa-codex206` and
`.scratch/blocker-registry-codex206`. No runtime/snapshot cleanup claim follows
from local test-file cleanup. Only this review, blocker docs/JSON and the appended
journal entry are committed; no fixture/test/scorer changes were needed.
