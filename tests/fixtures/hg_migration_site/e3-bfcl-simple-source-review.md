# E3 — BFCL Simple source acquisition and invocation review

## Disposition

**Established:** the inventoried 23 missing handler bodies and ten required
package initializers are acquired, byte-verified and checked in. The combined
projection contains 83 original files (81 Python, root LICENSE and pyproject).
The static Simple invocation graph below is locally reviewed against those bytes.
**Still open:** independent review, archive/tag/member/license custody, third-party
offline/native runtime closure and an independently approved exact receipt pin.
Leave `bfcl_v3/simple-ast-contract` **external**. No official scorer execution,
full-call differential result, benchmark effect or production authorization is claimed.

## Starting next_action (verbatim)

> Complete the precisely inventoried missing original import/initializer bodies and reviewed Simple invocation graph. Obtain independent v1.3 archive/tag/commit/member/license custody and code-only parser/checker/dispatch/helper review plus a separate approved exact receipt pin. Only then pass the explicit source gate and derive the full-call contract using fabricated differential calls; preserve name diagnostic and selected/privacy boundaries. No production H0.

The starting blocker object is retained in
`tests/fixtures/e3_bfcl_source/starting-blocker.json`; this review changes no
historical receipt or earlier acquisition bundle.

## What tasks 195, 227 and B5 already establish

Task 195 establishes public `v1.3` → commit
`ea13468e4423454d0c213704fb87cf7cb3990433`, release prose identifying V3,
seven original code/license/specification files and partial parser/aggregation
semantics (`reports/bfcl-source-provenance-codex195-report.md:9`, `:17`, `:28`).
It explicitly leaves the archive digest/member bindings unverified (`:52`).
Task 227 adds the original six-argument AST checker; its four requests include
three byte-identical repeats, bringing the unique total to eight
(`reports/bfcl-blob-retry-codex227-report.md:22`, `:31`, `:50`). It records missing
checker imports rather than assuming Python-only import closure (`:63`).

B5 adds 42 original Python files, including those three checker imports, plus
retained commit/tree responses reconstructing path/blob linkage for all 50 files
(`tests/fixtures/external_provenance_b5/README.md:18`, `:41`). B5 records 23 HTTP
403 handler requests and initializer identities without original response bodies
(`:52`). These older failures and receipts remain unchanged. B5's archive HEAD
does not verify the declared archive SHA-256 or members (`:25`).

## New acquisition and integrity evidence

`tests/fixtures/e3_bfcl_source/acquire.py:33` obtains each target SHA from the
retained untruncated B5 tree. It requests only public Git blob endpoints; it
never requests archives, tasks, answers, model results or external dataset roots.
The 24 unauthenticated requests returned HTTP 200. Exact URL, headers, UTC times,
status, raw JSON response, byte count and SHA-256 are retained in `responses/`.
Decoded bytes reproduce both their Git `blob <length>\0<bytes>` SHA-1 and the
retained tree's size/SHA. `inventory.json` maps every original path to local bytes,
SHA-256, Git blob and reused provenance. Expected archive member strings remain
explicitly unverified. `SHA256SUMS` covers the new bundle, including raw bodies.

The 23 new handlers are exactly B5's `unacquired_imports`: `bitagent`,
`deepseek_coder`, `deepseek_reasoning`, `falcon_fc`, `gemma`, `glaive`, `glm`,
`granite`, `granite_3`, `hammer`, `hermes`, `llama`, `llama_3_1`, `minicpm`,
`minicpm_fc`, `mistral_fc`, `phi`, `phi_fc`, `quick_testing_oss`, `qwen_fc`,
`salesforce_llama`, `salesforce_qwen`, `think_agent`. Original files live under
`tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/`.

Ten initializers share upstream empty blob
`e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` (one response, ten original paths).
Their SHA-256 is
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
They cover `bfcl_eval`, `constants`, `eval_checker`, `ast_eval`, `type_convertor`,
`multi_turn_eval`, `model_handler`, `api_inference`, `local_inference`, `parser`.
They are fetched original zero-byte files, never synthesized stubs.

The separate `multi_turn_eval/func_source_code/__init__.py` is not included:
its package is reached through a multi-turn-only dynamic loader, discussed below.
The pinned wheel command's `bfcl_eval.__main__:cli` is also outside this graph;
this review starts at the retained `eval_runner.main`, not that package CLI.

## Source citation key

Each key below is an exact checked-in filename; `R:246` means that file, line 246.
`P` is `tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/`.
`B` is `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/`.
`C` is `tests/fixtures/bfcl_ast_source/retry-codex227/files/berkeley-function-call-leaderboard/`.

| Key | Original checked-in file beneath the stated prefix |
| --- | --- |
| R | `P/bfcl_eval/eval_checker/eval_runner.py` |
| H | `P/bfcl_eval/eval_checker/eval_runner_helper.py` |
| U | `P/bfcl_eval/model_handler/utils.py` |
| A | `C/bfcl_eval/eval_checker/ast_eval/ast_checker.py` |
| M | `B/bfcl_eval/constants/model_config.py` |
| K | `B/bfcl_eval/constants/category_mapping.py` |
| V | `B/bfcl_eval/utils.py` |
| Q | `B/bfcl_eval/model_handler/local_inference/qwen.py` |
| O | `B/bfcl_eval/model_handler/local_inference/base_oss_handler.py` |
| T | `B/bfcl_eval/eval_checker/multi_turn_eval/multi_turn_utils.py` |
| E | `B/bfcl_eval/constants/eval_config.py` |

## Locally reviewed Simple invocation graph

| Edge or boundary | Source evidence and implication |
| --- | --- |
| `eval_runner.main` → category expansion → `runner` | `R:474`, `R:488`, `R:502`; `V:187` expands the categories declared by `K:3`. `simple`, `java`, `javascript` are distinct categories (`K:11`). The package's top-level `bfcl` CLI is not reviewed here. |
| `runner` → config → concrete handler | `R:349`, `R:382`, `R:29`; the mapping stores the handler class and `is_fc_model`, not an inferred parser based on a model name. All catalogue imports in `M:4`–`:79` execute at import time. |
| Concrete prompting example | `M:1492` maps `Qwen/Qwen3-0.6B` to `QwenHandler`, `is_fc_model=False`, `underscore_to_dot=False`; `Q:5` inherits `OSSHandler`, whose `decode_ast` at `O:55` calls `default_decode_ast_prompting`. This example is source evidence, not an approved mapping for `qwen3.5-9b-local`, which is absent from this pinned catalogue. |
| `runner` → `evaluate_task` | `R:388` loads and sorts results; `R:390` dispatches. `R:420` defaults to Python, `R:421`/`:423` select Java/JavaScript. `V:59`/`:63` test category substrings. |
| `evaluate_task` → Simple runner | `R:432`/`:442` load full prompt/reference files; `R:444` tests multi-turn; otherwise `R:457` calls `ast_file_runner`. `V:35` tests `multi_turn`; `simple`, `java`, `javascript` take the single-turn AST path. This upstream whole-category loader cannot replace HarnessGrad's selected-only loading. |
| `ast_file_runner` → decoder → format check | `R:256` asserts equal list lengths, then indexes each list at the same position (`R:263`). It does not assert cross-list ID equality. `R:270` calls the selected handler decoder. `V:140` requires list → singleton dict → parameter dict; an empty list passes format checking but fails Simple cardinality later. |
| Prompting parser | `U:695` trims backticks/whitespace and adds brackets; `U:241` routes Python/Java/JavaScript. Python `resolve_ast_call` at `U:263` preserves dotted names and gathers keyword arguments, without traversing positional arguments. `resolve_ast_by_type` at `U:280` resolves values recursively. Java/JS use the original parsers, acquired in B5. |
| Decoded call → AST checker | `R:307` calls `A:30` with function description, output, full reference alternatives, language, category, model. Categories without `parallel` or `multiple` require exactly one call and invoke `simple_function_checker` (`A:44`, `A:53`, `A:323`). |
| Checker → helpers | `A:343` → `convert_func_name` (`A:75`) applies config-dependent dot/underscore mapping. `A:357` checks required params; `A:365` rejects unexpected params; `A:377`/`:398` call Java/JS converters; `A:442` calls `type_checker`. `A:458`, `:466`, `:474`, `:480` dispatch dict/list-of-dict/string/list checks. `A:489` tests alternatives, `A:498` tests omitted parameters against the empty-string marker. This is a dependency/call inventory, not an independently approved full-call contract. |
| Validity → category score → leaderboard | `R:316` increments correct count; `R:332` divides by all result entries. `H:66` stores category accuracy; `H:212`/`:218`/`:219` retrieve Python/Java/JS Simple, `H:222` calls equal-category averaging (`H:38`). `H:181` renders percentages. A Python-only selected-subset score has a different scope. |

Decode exceptions and wrong formats remain in the upstream denominator (`R:271`,
`R:287`, `R:332`). The checker call at `R:307` is outside the decode `try` block;
schema/key/converter/runtime exceptions there are not ordinary false verdicts.
Empty results divide by zero at `R:332`; equal lengths alone do not establish
aligned IDs. Runtime/source failures must not become measured zero scores in any
future platform integration. No upstream code was imported or executed here.

## Import graph and remaining runtime boundaries

`static-audit.json` records every explicit import with original filename, line,
function scope and resolved repository path, plus dynamic/evaluation call sites.
All statically named BFCL imports and their existing ancestor initializers are
present. This is explicit-name acquisition closure, not a runnable environment
attestation or proof of every runtime-generated edge.

Multi-turn support is imported unconditionally (`R:18`, BaseHandler in
`P/bfcl_eval/model_handler/base_handler.py:12`). Its eight function-module names
are strings (`T:7`); `importlib.import_module` is inside
`execute_multi_turn_func_call` at `T:48`, not module initialization. Simple's
`evaluate_task` branch (`R:444`) and the example decoder (`O:55`) do not call it.
Those module bodies are unnecessary for this statically reviewed Simple route;
they remain unacquired and unreviewed for any expanded multi-turn scope. No import
was deleted or stubbed to obtain this result.

Python prompting value resolution includes `eval` at `U:302` and `U:313`.
Other handlers have distinct decoding/evaluation branches recorded in the audit.
The concrete handler and permitted input grammar therefore require independent
review before fabricated differential execution. This work does not execute predictions.

Java and JavaScript parser modules create native parser objects at import time
(`B/bfcl_eval/model_handler/parser/java_parser.py:1`, `:4`, `:6`;
`B/bfcl_eval/model_handler/parser/js_parser.py:1`, `:4`, `:6`), even for Python
prompting. `E:34` creates result/score directories on import. The example handler
constructor creates an OpenAI SDK client (`O:38`); that is not evidence of a
request, and none was made here. Its generation-only batch path imports
transformers (`O:79`) and loads tokenizer/config (`O:109`, `O:110`); this path is separate
from decoding existing results and was not executed.

`P/pyproject.toml:17` contains partly unbounded dependency declarations, not
wheel/native hashes, a lock or an offline runtime receipt. The audit enumerates
21 non-stdlib import roots across the catalogue, including function-local ones.
Root Apache-2.0 bytes and package declaration (`P/LICENSE:1`,
`P/pyproject.toml:16`) are retained. B5's full tree has no nested BFCL file named
LICENSE/NOTICE/COPYING; this filename observation cannot establish third-party
license coverage or approve independent licensing review.

## What remains and validation

The custodian/reviewer still needs to attest the historical archive SHA-256
`57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe`, tag/commit
and every original member binding; review code-only closure, the exact concrete
handler/parser/checker/dispatch/helper graph and licensing; supply offline
Python/ABI/image/package/native identities; then deliver a separate approved
exact receipt pin. This locally authored inventory is deliberately a different,
inadmissible schema, with approval fields false/null. A local digest cannot
supply custody approval. No approved source-gate receipt was created.

After those independent prerequisites, pass the explicit source gate and derive
the complete full-call contract with fabricated differential calls. Selected
index/storage/span/split custody remains a separate blocker and is untouched.
Preserve the function-name diagnostic, selected/privacy boundaries and historical
receipts. No benchmark, H0, provider/model, GPU, package install or archive GET ran.

Offline verification and focused-check results are retained in
`reports/e3-blocker-validation.json`. The byte/import audit uses `ast.parse` only;
the explicit source gate must remain blocked without an approved pin. Registry
validation and an exact comparison against the starting commit protect all
historical records and all unowned blocker objects.

Five focused provenance/source checks passed; the unpinned explicit gate returned
the expected blocked result (exit 2). Original upstream source whitespace and raw
source-excerpt output are preserved, including their whitespace-check warnings.
