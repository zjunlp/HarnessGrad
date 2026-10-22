# BFCL v1.3/V3 code-only candidate — Codex 195

**Blocked, incomplete source projection.** Seven original files (126,311 bytes)
were acquired from the public upstream at immutable commit
`ea13468e4423454d0c213704fb87cf7cb3990433`. This is neither a complete evaluator
nor an approved source-contract receipt. No acquired Python module was imported
or executed. No benchmark task, answer, heldout, release archive, external
dataset root, model endpoint or H0 was accessed.

[provenance.json](provenance.json) records every original path, acquisition URL,
commit-qualified raw URL, byte count, SHA-256, Git blob identity, source imports,
observed metadata, failed attempts and unresolved dependencies. Downloaded bytes
were verified against the Git blob SHA-1 supplied by the commit-qualified GitHub
contents API. This binds source bytes to the observed repository ref; it does not
bind them to the adapter's declared compressed archive bytes.

## Ownership, ref, release and license

- Berkeley's [BFCL site](https://gorilla.cs.berkeley.edu/leaderboard.html) links to
  `ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard`. The GitHub
  account owner is `ShishirPatil`; the Berkeley project link establishes project
  provenance rather than an assertion that Berkeley owns that personal account.
- The public [tag-ref API](https://api.github.com/repos/ShishirPatil/gorilla/git/ref/tags/v1.3)
  returned a **lightweight** `refs/tags/v1.3` pointing directly to the commit above.
  The [commit metadata](https://api.github.com/repos/ShishirPatil/gorilla/git/commits/ea13468e4423454d0c213704fb87cf7cb3990433)
  identifies tree `b34aa53a73491f42dc69b8b18c92448d7c311f50` and date
  `2025-07-17T20:52:32Z`. A tag remains mutable; retrieval is an observation, not
  historical custody certification.
- The [official release](https://github.com/ShishirPatil/gorilla/releases/tag/v1.3),
  published `2025-07-17T21:10:14Z`, says “Stable release of Berkeley Function
  Calling Leaderboard V3 with Multi-step and Multi-turn function call evaluation”.
  Thus repository tag `v1.3` is connected to benchmark V3 by release prose, not
  by treating the two version strings as equivalent. Its `target_commitish` is
  the branch name `main`, so use the tag-ref resolution for the immutable pin.
  The release lists no attached assets; its tarball/zipball URLs are generated
  repository archives. Neither was requested.
- The pinned root [LICENSE](files/LICENSE) contains Apache License 2.0. The exact
  [package metadata](files/berkeley-function-call-leaderboard/pyproject.toml)
  also declares Apache 2.0 and names Berkeley project authors. No different
  license or NOTICE appeared in the repository or BFCL package root inventories.
  Original license and source bytes are preserved unchanged. Third-party
  dependencies have a separate, unresolved license/runtime closure.
- The declared codeload URL is
  `https://codeload.github.com/ShishirPatil/gorilla/tar.gz/refs/tags/v1.3`, with
  locally declared SHA-256
  `57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe`.
  **This digest and archive member relationships are unverified.** A matching
  raw Git blob cannot establish a tar.gz digest. Expected `gorilla-1.3/...`
  member names in the record are explicitly predictions, not acquisition facts.

## Verified source semantics and their limits

All source references below are relative to
`files/berkeley-function-call-leaderboard/bfcl_eval/` at the pinned commit.
The corresponding upstream URL is
`https://github.com/ShishirPatil/gorilla/blob/ea13468e4423454d0c213704fb87cf7cb3990433/berkeley-function-call-leaderboard/bfcl_eval/` plus that path.

| Source / symbol / lines | Verified observation |
| --- | --- |
| `model_handler/utils.py`, `default_decode_ast_prompting`, 695–703 | Strips backticks, newlines and spaces from the ends, adds missing outer brackets, and calls `ast_parse(result, language)`. This is the default prompting helper; concrete handlers can provide other decoders. |
| `model_handler/utils.py`, `ast_parse`, 241–260 | Python strips `[]'` from the ends and uses `ast.parse(..., mode="eval")`; extracts a single call or iterates call elements. Java and JavaScript delegate to separate parser modules after slicing off outer characters. Those parsers are unacquired. |
| `model_handler/utils.py`, `resolve_ast_call`, 263–277 | Retains dotted function names and builds arguments from **keywords only**. It does not collect positional arguments; repeated keyword keys overwrite earlier entries in the resulting dictionary. These are parser behaviors, not checker acceptance rules. |
| `model_handler/utils.py`, `resolve_ast_by_type`, 280–323 | Handles constants, lists, dictionaries, tuples, names and nested calls with branch-specific conversions. This is not a general safe AST-only parser: some expression branches use Python evaluation. No prediction was evaluated here; runtime admission is still unresolved. |
| `model_handler/base_handler.py`, `BaseHandler.decode_ast`, 654–658 | Base method raises `NotImplementedError`. A verified default helper is insufficient to certify the selected model's raw-output contract. |
| `eval_checker/eval_runner.py`, `evaluate_task`, 410–468 | Uses category predicates to select Python/Java/JavaScript; relevance and multi-turn take other runners; the remaining single-turn categories go to `ast_file_runner`. Predicate definitions and category constants are unacquired. |
| `eval_checker/eval_runner.py`, `ast_file_runner`, 246–345 | Requires equal prediction/schema/reference array lengths; reads function schemas and complete `ground_truth` from supplied arrays. Calls `handler.decode_ast`, checks its output format, then invokes `ast_checker(schema, decoded, references, language, category, model_name)`. This documents a call site, not the missing checker's definition/signature. |
| `eval_checker/eval_runner.py`, 270–305, 315–345 | Decode exceptions and wrong formats produce failure records and remain in the denominator. `correct_count` increments only for checker `valid`; category accuracy is `correct_count / len(model_result)`. No argument-level partial credit appears in this runner. Length assertion failures and uncaught checker exceptions prevent ordinary score completion; there is no explicit unmeasured taxonomy. |
| `eval_checker/eval_runner_helper.py`, `calculate_unweighted_accuracy`, 38–62; `generate_leaderboard_csv`, 212–229 | Non-live **Simple AST** is the arithmetic mean of the Python `simple`, `java` and `javascript` category accuracies, not pooled accuracy over their combined tasks. A selected Python-Simple subset alone is a different metric scope. |
| `eval_checker/eval_runner_helper.py`, `get_category_score`, 148–159; `write_score_csv_file`, 162–190 | Missing category has accuracy zero and display `N/A`; the aggregate includes that zero and propagates `N/A` by default. Internal accuracy is a fraction; CSV formatting multiplies it by 100 and displays two decimal places. The missing-category branch would read task files, so it was not executed. |

The **official full-call acceptance rules remain unresolved**. The pinned
`ast_eval` directory metadata lists
`eval_checker/ast_eval/ast_checker.py` with Git blob
`214f3ba55c012691761913b6aca68ab7bc4aa38b`, and the acquired runner imports
`ast_checker` from it. Its body was not acquired: the public contents/blob API
returned HTTP 403 with rate limit remaining zero; the GitHub blob-page request
timed out. No call-count, namespace matching, optional/extra parameter,
acceptable-reference alternative, numeric/string normalization, collection
ordering or value/type comparison rule is asserted from that filename alone.

## Closure and admission

The parser directly imports missing Java/JavaScript parsers, default prompt
constants and `ModelStyle`; it also imports `tenacity`. The runner/helper/base
handler additionally import missing category/evaluation/model/header constants,
`bfcl_eval.utils`, multi-turn modules, and external packages. The checker has an
unreviewed `type_convertor` source directory; its exact helper graph is unknown.
The record inventories observed imports, including imports outside the Simple
call path, and marks acquired versus missing modules. It does not claim that
static import inventory is a complete runtime graph.

`pyproject.toml` preserves original package requirements (Python >=3.10 and
tree-sitter parser pins among others). It is not an offline lock: many requirements
are unpinned, provider SDKs are included, and neither wheels, native grammars,
image/ABI hashes nor licenses were acquired. Do not install or import this
projection as a substitute evaluator, trim imports, or stub helpers to run it.

The candidate deliberately uses schema
`harnessgrad.bfcl-ast-source-candidate.v1`, with blocked/false admission fields.
The existing gate expects an independently approved
`harnessgrad.bfcl-ast-source.v1` receipt in the parent directory; that receipt is
still absent. It rejects this candidate even if supplied its computed digest.
A digest here is integrity evidence, never independent approval. The registry
remains `external`; the platform's function-name diagnostic and all production
code are unchanged. See [CUSTODIAN_REQUEST.md](CUSTODIAN_REQUEST.md) for the exact
remaining handoff.
