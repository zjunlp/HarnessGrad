# B6: 2WikiMultihopQA and LiveCodeBench blocker review

Two historical-comparability **reviews are closed with comparability rejected**.
Three external blockers remain active with narrower evidence requests. No dataset
is ready for production. No historical score, record or admission rule was changed.

Base: `9066072`. Repository writes and retained artifacts are in `/tmp/hg-b6`;
pytest uses its normal external temporary directory as required by AGENTS.md.
Public metadata/code acquisition
and read-only review of journal-cited preserved metadata receipts only. No task,
answer, alias-table, shard, test-case, solution, trace or snapshot payload was
opened or acquired. No adapter was imported, no scorer/benchmark was run, and no
model, endpoint or GPU was called. Parent review and integration remain separate.

## Existing work read before acquisition

- Task 197's unchanged fixture authenticates original evaluator v1.0/v1.1, code
  license and dependency manifest against upstream
  `209e761834d1ce055856fc11072c0a5aab47601d`. Evaluator v1.1 SHA-256:
  `7c09ee2ec98c7ecb57df79eb68dba22eb381ab58d50e730dfcefafb4dda31458`.
  It establishes source semantics and synthetic checks with the old installed
  ujson, without data custody or clean deployment closure.
- Task 226's unchanged fixture binds the exact cp312/Linux/x86_64 wheel to
  official PyPI metadata, validates RECORD and installed original files, and
  proves a fresh offline package installation. Wheel SHA-256:
  `f8ccb77b3e40b151e20519c6ae6d89bfe3f4c14e8e210d910287f778368bb3d1`.
  Its ELF member SHA-256 is
  `deb036e60d4b61104bad389783094ebb09a35dbdecd21aeea572968762c3be5c`.
  Five DT_NEEDED libraries, no native import and no supported image are recorded.
  This review does not reinstall or redownload that wheel.
- Task 207's unchanged operator packet gives a target template, separate
  custodian/reviewer channels and offline verification procedure. It supplies
  **no issued record, trusted signing keys, signature or independent approval**.
  Task 198 already acquired source-tree/commit metadata; its failed Hub/code
  requests are historical failures, preserved rather than repeated as findings.

The five original `observed` and `next_action` strings were read before new
acquisition. Their exact text is also retained in
[original-five-blockers.json](b6-blocker-evidence/original-five-blockers.json).

## 2Wiki: hub-data-provenance — partial closure, external remains

Canonical HTTPS Hub responses now bind
`kamelliao/2wikimultihopqa@f4f0d7e4ae275d9281e90c46c93e66d6cdda3674`
to the adapter's declared raw-artifact hashes:

| Evidence | SHA-256 |
| --- | --- |
| Exact-revision canonical Hub metadata response | `6340521cf170fac8e82785cd21a5e9941306ea6ee54963438b6555561b09d21a` |
| Canonical revision file-tree response | `0726213cab74f08258be4080918684821a5b3c313f51043a089d79ddcc6947eb` |
| Exact-revision Hub builder code | `fab06a1a2d4731e1aefb415252f1613d93754bd94ca0b2ba1f6c268af93711c4` |
| Upstream pinned README, decoded original Git blob | `d1831fa4b1a8fdcc1729565e30e38b3d8b955534486eb690fd5ea54c1232955f` |
| Canonical witness-mirror revision metadata response | `e10a7ec4d3ca09616468f0ca2dc82b7f7278727343088045ce76983f2b6f1feb` |

The metadata identifies `data/dev.json` as 57,614,142 bytes, LFS SHA-256
`79f77ae104088ea8e25b1a65dbece768d45771194663bc5660ec9a98070dadf5`,
and `data/id_aliases.json` as 17,501,406 bytes, LFS SHA-256
`f08ffcb6c2cefca9bdbe86b4248d6ad7a7743762d3f7264c14ff0bae85726fb6`.
These are server-advertised artifact identities; their payloads were not fetched.
The builder blob SHA-1 `1db0e56ed271a87a85b115342bbab3125adef4c9`
matches the acquired code. Its split mapping is train → train.json, validation →
dev.json, test → test.json. Its generated schema/yield omits `answer_id` and
`evidences_id` and does not load the alias table. **That generated dataset is not
the adapter's raw-JSON-plus-alias route.** No conversion is substituted.

The official README blob `c83110f7f4dbaa4a8900e1ad68183b64729bf951`
is bound by the existing task-197 tree. It explicitly associates the December 8,
2020 ID-enriched `data_ids.zip`, `id_aliases.json` and evaluator v1.1. Thus the
intended release-to-scorer relationship is established at documentation level.
It publishes no archive digest and supplies no statement independently licensing
the dataset/alias contents. Apache-2.0 on repository/builder code does not settle
the rights of Wikipedia/Wikidata-derived data or aliases.

At witness revision `fe713bfbd1afbca1a65246741a75890405d56a3a`, canonical
metadata advertises 12,576 validation rows and the validation parquet LFS hash
`408e2dbb28edc6c8b9ca3ba0c94d4fc7bf17ffb923766593a3a7f546ab4cba59`,
matching the adapter declaration. It does not establish row equivalence or alias
completeness. No parquet was fetched. The current upstream README was also
preserved as a mutable observation (SHA-256
`bb0cf42315c4e65ccf8fda8e5a1a76fd76bf81552763b9bbd6230bc3077ddba0`);
it mentions later sentence-segmentation fixes and different download links.
It must not replace the pinned December release or retroactively identify it.

**Still open:** authenticated original-release-to-Hub byte linkage, data/alias
rights, alias completeness/ID coverage, and independently custodied selected
manifest. Canonical Hub hashes close the missing-Hub-identity subproblem; they
do not close these distinct claims. The preserved local imposed split and
selections below establish historical metadata consistency, without independent
custodian authentication or fresh pool verification.

## 2Wiki: ujson-native-offline-closure — notices acquired, external remains

The 5.10.0 source reference resolves to actual commit
`59875e089937c8b05c2f4b474530e3a9ec706a56`, with root tree
`f68f465c6b5d55dc9520b51c5bc666ddeb0dd2b5`. The GitHub tree API's `sha`
field for the commit lookup is **not** used as the root-tree identity; the root
was independently reconstructed from entries and checked against commit metadata.
The vendored `deps/double-conversion` tree is
`629e1ff9b56216eb1423941ad615573354a857e4`. This is the precise acquired
vendored source identity; no unobserved upstream double-conversion commit or
release number is invented.

| Evidence | SHA-256 |
| --- | --- |
| Original recursive source-tree response | `bb1a1648d7572d18beceafcc56f074b000e9dee5a814bc042c9619ecd720003d` |
| Immutable Git commit response | `6d56bf2aeebe121f69fb290be85a307bfc2d01723ee8cefd09af9b3af3013fe3` |
| Decoded original `setup.py` | `1202739ff87e1d5abb516549c15b42693c012439dac96c8980e5b0384bf0db0d` |
| Decoded double-conversion LICENSE/COPYING blob | `4af93c12062c58058378de2397dc1c92bbff9ddfb1d583a01c84127557ce97ca` |
| Existing wheel license, also identical to acquired source license | `36866344f0a486541bd9800f1a407a056c0c214a82c658f059694daf3dc394d2` |

`setup.py` compiles bundled double-conversion `.cc` files by default unless
`UJSON_BUILD_DC_LIBS` supplies external libraries. The acquired BSD-style
double-conversion license retains the V8 authors' copyright, conditions and
disclaimer and requires their reproduction with binary distributions. Its
LICENSE and COPYING have the same blob SHA-1
`933718a9ef9dae4cb4afa56e490211760d8a949f`. The wheel's combined license
does not include these V8 terms. Both original notices are now carried verbatim
in [ujson-supplemental-notices/](b6-blocker-evidence/ujson-supplemental-notices/)
for the eventual deployment bundle; no wheel was repacked or historical license
receipt changed.

**Still open:** no supported immutable CPython 3.12/Linux/x86_64/glibc image was
supplied or established. No image-bound native-loader providers, recursive
dependencies/symbol providers, network-denied fresh native import or evaluator
synthetic-execution receipt exists for the task-226 wheel. Vendored source and
default build behavior do not cryptographically attest a wheel build's source.
The deployment owner must carry the supplemental notices and bind this exact
wheel to the supported runtime and native replay. A host venv or newly passing
package install would not supply that image identity.

## 2Wiki: recovery-comparability — review closed; combined comparison rejected

The journal-cited original and recovery **curve and run_meta files** and imposed
split were read without executing their writers. Exact bytes were copied into
[preserved-receipts/](b6-blocker-evidence/preserved-receipts/), with source paths,
sizes, digests and before/after-copy identity checks in `custody.json`.

| Receipt | SHA-256 |
| --- | --- |
| Original curve | `14790c0c322981ad2e8497d368bee29de971526e4499a22a2e77be05b60eff0f` |
| Original run_meta | `c381baf1514ba903bd515bbe44092ae4cc0a96be34c5bb672e096557491bbda0` |
| Recovery curve | `0f27ad9146371b2e9e318d0cce8b4f3769eeb33e209948e780c5a3a3ec034f20` |
| Recovery run_meta | `f0c91f731006e4b760916c481262873a0808e36a1ec3b8e714b73026ed9a27a4` |
| Imposed split raw file | `3236a75876d8afea722255cb8d12c32aba64b327c19f15234af1c2325923c18f` |

| Field | Original | Recovery |
| --- | --- | --- |
| Actual run ID | `twowiki-pilot-h0-20261013` | `twowiki-invalid-recovery-h0-20261013` |
| Selected tasks | 50 | 2: `/1589`, `/2501` |
| Scored tasks | 48 | 2 |
| Stored trial values | 144 (48 × 3) | 6 (2 × 3) |
| Invalid tasks | 2; both absent from scored map | 0 |
| Historical recorded score | 0.5081597291666667 | 0.4000000000000001 |
| Harness SHA | `52853770ce67444cc6ec0f54e13455ba43791f52` | `8f5cfaf5687e50b518b32b468c43d05ba884e612` |
| `cost.evaluation_trials` as written | 50 | 2 |

Both run_meta files record `jobs=1`, `trials=3`, model `qwen3.5-9b-local`,
agent endpoint `http://127.0.0.1:8002/v1`, a 32-step harness configuration and
the same imposed 8,335-train/4,241-eval split. Canonical sorted split JSON
reproduces recorded split SHA-256
`161b79332301a152d9ff53ffff073b2f9b307054bc510764068fb48a89bc17b4`.
The recovery set equals the original invalid-ID set and is disjoint from the
original scored map. Recovery `/1589` has trial values `[0.8,0.8,0.8]` and
`/2501` has `[0,0,0]`. Both platform records say `dirty=true`, with different
content hashes. The original invalids remain invalid; they are never zero-filled.

The journal reports mixed legacy Answer F1/structured Joint F1 in the original.
These curves do not bind per-trial metric modes, scorer file hashes or data/alias
identities. Their `cost.evaluation_trials` values are task counts as recorded,
**not** 150/6-trial coverage receipts. Unknown modes, provider weights, trusted
cleanup, immutable runtime and comparability evidence are not inferred. No trace,
verdict payload or preserved harness snapshot was opened to fill those gaps.

The journal's combined `0.50383334` is retained as its historical claim; this
review creates no recomputed combined score, corrected curve or official effect.
The registry's original historical object, including its null run ID and null
effect, is byte-for-byte semantically unchanged. The newly located run ID is a
separate review finding, not a rewrite of that frozen record.

**Disposition:** the requested separate metadata review is complete. Retire the
active review blocker, recording that these generations cannot support a combined
comparable official metric. Any future official comparison needs separately
identified, homogeneous measurements after all data/runtime gates pass. Recovering
or reusing this old pilot is not a prerequisite to a separately authorized fresh
diagnostic.

## LiveCodeBench: row-attestation — release metadata closed, external remains

Canonical HTTPS metadata binds
`livecodebench/code_generation_lite@0fe84c3912ea0c4d4a78037083943e8f0c4dd505`
to all six adapter shard digests. The original response SHA-256 is
`501d60839cc3f592b476d5d79b1093f29ade0144fb3318f1b261d3ece274e4d4`;
the tree response is
`91b24116907e1c5e5ed962fa90afe8583a36e97148e0ef6ed8369df21b27dee8`.
The exact builder code has SHA-256
`973e37989fed0f069c8246776e918d0d96600d64a3730cd6242dd76bccf2f7bb`
and Git blob `c71eaf4560b476b6f5c21a5732b10457ce543321`.

The builder explicitly maps `release_v6` to `test.jsonl` through `test6.jsonl`,
and yields upstream TEST records in shard order and then physical line order.
The source loader passes `version_tag=release_version`, `split="test"` and
`trust_remote_code=True`, with optional date filtering; it does not pin the data
revision itself. This upstream ordering differs from local
`data/datasets/livecodebench/prepare.py`'s unique-ID union and
`sorted(question_id)` ordering. The local `train` side is a HarnessGrad hash
partition, not an upstream training split. Builder/metadata license labels also
differ (`MIT License` versus card `cc`); this review makes no licensing clearance
claim from those labels.

**Still open:** no complete ID-only catalog/rank proof, externally custodied local
pool/split/statement linkage, issued task-207 record or independent reviewer
approval was obtained. A revision hash or upstream index cannot authenticate
local row 740. The existing inert selection is preserved as unattested; neither
an approval digest nor a positive row record was manufactured. No custodian was
contacted and no mixed pool or shard was read.

## LiveCodeBench: smoke-scorer-comparability — review closed; official claim rejected

Public code was acquired as Git blobs from the task-198 pinned tree at source
commit `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`. The original MIT license
is retained with the exact acquired files. They were read/parsed, never imported
or executed.

| Decoded source | SHA-256 |
| --- | --- |
| Benchmark loader | `0bc93f5a650b55d6a1b54d3db852d80ae64891ece4ff085b2e6b469eb89f8846` |
| `testing_util.py` | `b7cb6a8a69807bb868150a61742e25d7bb5328bbe01d514471b6ec43c9fa9ed2` |
| `compute_code_generation_metrics.py` | `073f388cc78bf0eb0ad35eaeb8bb509c0a3664963e5305d2be13d6b6eb6f3d21` |
| `pass_k_utils.py` | `6af3ee55d40f96fa06fa511bd5e43d94b19604750e82454b4b3c1f45c5199010` |
| MIT license | `104099948b78ebc36f2e951b3a7973b956adfa7d39aaec0d1512f97a2686d15d` |
| Pinned project metadata | `907d51622ad2e6b97f277f7f0c67a8e7a8bf27bedeaf0f2a5a1b9ba432a06bd2` |
| Journal-cited preserved launch record | `b2402fc26b4ea0c675c7880ffd72e5272793df084ee9471aebcb06022ed8e168` |
| Preserved candidate-path report | `163a3342fcce3e0cd2ace6b624de953c28e6c804072e9c9a4df7015ff6115897` |

The actual preserved launch record and accompanying report describe planning and
synthetic validation: `launch_executed=false`, `H0_authorized=false`, production
requests during validation zero. They do not contain an executed model-smoke
verdict. That later smoke remains a separate journal claim; no contradictory
receipt was rewritten and no score or missing run ID was invented.

| Requirement | Pinned official source | Preserved external-process route |
| --- | --- | --- |
| stdin execution | Clean `__main__`, AST-wrap non-import statements into `wrapped_function`; import prelude; compile once and invoke per case | Fresh isolated Python process for each input; no prelude |
| Functional tasks | `grade_call_based` using `fn_name` | stdin only |
| Per-case time | `run_test(..., timeout=6)` default | 3s CPU / 6s wall |
| Process-level time | `(timeout+1) * n_tests + 5` upstream supervisor | Each external process bounded separately |
| Memory | `reliability_guard()` receives default `None`; that call sets no RLIMIT memory bound | 512 MiB address space, 1 MiB output |
| Correct generation | `np.all(gen > 0)` then pass@k estimator | All scoped external-process comparisons pass |

The old launch's phrase “official 2s/1024MiB” is retained verbatim in its raw
record. The acquired pinned source does not support it: both executor and metric
entry point default to six seconds. A comment says 4GB, but the code calls
`reliability_guard()` without `maximum_memory_bytes`, so that comment does not
establish an effective bound. Matching decimal/text comparison helpers is
insufficient for executor equivalence.

The current adapter wrapper at
`data/datasets/livecodebench/verify/check.py` has its own disclosed 600-second
global cap, six-second per-test default and one-generation scope. These differ
from both an arbitrary external process and an uncapped full upstream benchmark.
The project dependency list is broad/unpinned; the executor/pass@k modules use
NumPy, and the full metrics driver also uses tqdm. No source import inventory is
an offline dependency/runtime receipt.

**Disposition:** the requested path-difference and code-only requirements review
is complete; retire this active measurement review. The old smoke remains scoped
and incomparable. A future official claim must bind unchanged executor/helper
bytes, homogeneous task/test/generation selection, parameters, dependency/runtime
identity, and independently trusted verifier observations. Row admission and all
shared runtime/closure/cleanup requirements still apply. No benchmark execution
or equivalence certification follows from this review.

## Denominators, custody and acquisition method

`data/aggregation_policies.json` is the current explicit declaration:

| Dataset / scope | Aggregation | Denominator |
| --- | --- | --- |
| 2Wiki corpus metrics | ordered macro mean | gold rows |
| MuSiQue corpus metrics | ordered macro mean | provided results |
| 2Wiki / MuSiQue / LCB run reward | arithmetic mean | measured tasks |

2Wiki's corpus evaluator retains missing gold predictions as zero, ignores extra
prediction IDs, scales to percentage and rounds at the end. The platform run
curve excludes invalid/unmeasured tasks. A partial run curve is not a complete
gold-row corpus metric. MuSiQue's provided-result helper is a distinct protocol;
this review changes neither helper nor denominator policy.

Direct canonical Hub requests failed before HTTP responses. The saved request
failures have no fabricated response hash. Mirror metadata/code was then
acquired and clearly labeled. The existing local egress proxy recorded in run
metadata (`http://127.0.0.1:17891`) allowed canonical HTTPS Hub acquisition with
normal certificate validation. No host DNS/proxy configuration was changed.
Canonical bodies match mirror bodies byte-for-byte. Hash agreement supports byte
identity; canonical HTTPS supplies the observed origin, not a publisher signature.
Each redirect and HTTP error body is preserved too. DNS troubleshooting replies
are observations only and never an artifact provenance authority.

[review.json](b6-blocker-evidence/review.json) records every acquisition URL,
time, response status/error, body digest and header digest, original source
SHA-256/Git blob identities, receipt custody, separately derived coverage and
explicit false authorization flags.
[SHA256SUMS](b6-blocker-evidence/SHA256SUMS) pins every retained evidence file
other than itself. Raw response bodies and decoded original source bytes are
unchanged. The bounded acquisition helper refuses overwrite and payload URLs;
the review builder rechecks Git linkage, canonical Hub pins, exact receipt bytes,
selection and denominator declarations without external reads or execution.

## Validation and registry disposition

Only the two owned dataset entries change. Three external blockers remain;
the two completed historical-review blockers leave the active-only registry.
All shared references, taxonomy, historical-run objects, history baseline,
production authorization and unrelated dataset entries remain unchanged.
The journal receives an appended review entry. No production adapter or runtime
code changes.

Focused commands/results are in
[b6-dataset-blocker-validation.txt](b6-dataset-blocker-validation.txt).
Tests cover pinned response/source integrity, notices, Hub artifact and builder
identity, distinct denominator contracts, separate historical coverage and
preservation of all non-owned registry data. No whole-suite run occurs.
