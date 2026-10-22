# Task 227 — BFCL v1.3 checker Git blob retry

**Result: checker acquired; complete checker closure remains blocked.** Work was
confined to `/tmp/hg-codex-227`. Four unauthenticated official GitHub Git blob API
requests returned HTTP 200. Original source/license/specification bytes and full
request/response evidence are retained separately under
[retry-codex227](../tests/fixtures/bfcl_ast_source/retry-codex227/provenance.json).
The Task 195 candidate and all earlier receipts remain unchanged.

## Acquired and verified

The fixed target is `ShishirPatil/gorilla`, repository tag `v1.3`, recorded commit
`ea13468e4423454d0c213704fb87cf7cb3990433` and root tree
`b34aa53a73491f42dc69b8b18c92448d7c311f50`. The v1.3/V3 relationship remains
the prior release observation; this task did not re-resolve a mutable tag.

Exactly these requests were made, all with prefix
`https://api.github.com/repos/ShishirPatil/gorilla/git/blobs/`:

| Original path (`B/` = `berkeley-function-call-leaderboard/`) | Blob SHA-1 | Bytes |
| --- | --- | ---: |
| `B/bfcl_eval/eval_checker/ast_eval/ast_checker.py` | `214f3ba55c012691761913b6aca68ab7bc4aa38b` | 24,916 |
| `B/bfcl_eval/constants/type_mappings.py` | `fdbdde48402f8f2812b146f9b7341b6aae9e55b8` | 1,813 |
| `LICENSE` | `261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64` | 11,357 |
| `B/pyproject.toml` | `a514e581f18902f4f87562ee3c4964931b61e81f` | 1,831 |

Total: **39,917 bytes**. Decoded original bytes match the API's SHA and size,
the retained pins, and recomputed Git SHA-1 over `blob <length>\0<bytes>`.
SHA-256, response-body hashes, exact URL/method/request headers, response headers,
UTC timestamps and results are in the per-request receipts. The three previously
acquired files are byte-identical to Task 195. Redirects were disabled; no token
or authenticated request was used.

The checker SHA agrees with Task 195's recorded pinned `ast_eval` directory
metadata. **The underlying directory/tree response body is absent.** Other
pins come from Task 195's recorded commit-qualified contents metadata. This task
verifies agreement with those declarations; it cannot independently reconstruct
the commit-tree/path proof. No tree/contents endpoint was requested under the
Git-blob-only restriction. The prior metadata file's SHA-256 is recorded in the
new provenance for precise evidence binding.

The original root Apache-2.0 license and `pyproject.toml:16` Apache 2.0 declaration
were retrieved and hash-verified. Both Python bodies were statically reviewed as
checker logic/type constants, without embedded benchmark rows or separate license
headers. Original bytes, including whitespace, are unchanged. Missing-helper,
nested LICENSE/NOTICE and third-party license coverage remain unresolved.

## Exact remaining blocker and request accounting

`ast_checker.py:30` defines
`ast_checker(func_description, model_output, possible_answer, language, test_category, model_name)`.
Static import discovery identifies four direct in-repository dependencies:

- `constants/type_mappings.py`, imports at lines 2–5: acquired and verified.
- `constants/model_config.py`, line 1: missing pinned blob SHA and original body.
  `MODEL_CONFIG_MAPPING` is used at line 78 for function-name conversion.
- `eval_checker/ast_eval/type_convertor/java_type_converter.py`, line 6: missing
  pinned blob SHA and original body; called in Java branches at lines 392/396.
- `eval_checker/ast_eval/type_convertor/js_type_converter.py`, line 7: missing
  pinned blob SHA and original body; called in JavaScript branches at lines 413/417.

Paths above are relative to `B/bfcl_eval/`. The only other direct import is
standard-library `re`. All four repository imports occur unconditionally at
module scope. Missing imports cannot be discarded to claim Python-only closure.
Their further imports, package initializers and licenses have not been discovered.

**Task 227 failed requests: none (0 of 4).** The three missing files were
**not requested**, because retained evidence contains no path-to-blob SHA for
them and the allowed blob endpoint requires that identity. They are recorded
separately as `not_attempted_missing_pinned_blob_sha`, rather than fabricated
HTTP failures. No guessed SHA, alternate version, contents/tree/raw/web fallback,
archive or dependency installation was used. Historical aggregate 403/timeouts
remain in the unchanged Task 195 record; no exact historical request is invented.

The new record has a deliberately inadmissible retry schema and explicit
`status=blocked`, `measured=false`, `custodian_approved=false`,
`production_authorized=false`. Full source/license/runtime closure and independent
custody/admission remain open. This acquisition grants no official scorer or H0
authorization.

## Validation

- Offline audit reverified all four response hashes, decoded blob identities,
  sizes, SHA-256 values, retained pins and unchanged original bytes. Both Python
  files parsed with `ast.parse`; neither was imported or executed.
- Existing source-contract suite: **38 passed** with plugin autoload, bytecode
  and pytest cache disabled; temporary files stayed inside this worktree.
- Explicit unpinned source gate: expected exit **2**, fixed blocked result,
  `measured=false`, `production_authorized=false`.
- New retry negative admission: refused even with its locally computed integrity
  digest, before source inventory or projected-source reads. The digest supplies
  no independent approval. Results are in
  [verification.json](../tests/fixtures/bfcl_ast_source/retry-codex227/verification.json).
- Authored report/metadata/request receipts pass whitespace checks. The original
  checker retains seven upstream trailing-whitespace lines (37, 42, 244, 257,
  276, 284, 529), which produce the expected full-diff whitespace warnings.
- Task-specific scratch was removed after validation; acquisition and validation
  receipts remain in `retry-codex227`.

No task data, answers, references, archive or external dataset root was retrieved
or opened. No dependency install, upstream evaluator execution, main worktree
change, H0, model/provider call or GPU activity occurred. The parent must review
this task's exact diff and commit before merging.
