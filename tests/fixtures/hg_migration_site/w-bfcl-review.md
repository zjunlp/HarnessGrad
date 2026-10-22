# BFCL v1.3 Simple custody and code-only review — w-bfcl

**Finding: `bfcl_v3/simple-ast-contract` remains external and open.** Public-source
archive/tag/commit/member/root-license custody is now established for all 83
retained files. The complete offline third-party/native runtime and official
scorer execution remain missing. The local adapter still implements an explicit
function-name diagnostic, not the official BFCL Simple score.

This review builds on tasks 166/184/195/208/217/227, B5 and E3, after reading the
full starting blocker, its cited reviews, the journal and
`docs/independent-review-deferred.md`. The starting object is preserved in
`tests/fixtures/hg_bfcl/starting-blocker.json`. B5 already retained commit/tree
responses and 50 originals; E3 completed the remaining handlers and initializers,
producing the 83-file graph. Those files and every earlier receipt remain
unchanged. This work downloads the previously missing archive body, re-resolves
public metadata, independently recomputes the byte identities locally, and
reviews original source lines. It does not seek or claim independent human
approval. The operator withdrew those signature gates; no reviewer sign-off is
an unmet requirement here.

No upstream module was imported/executed, no dependency was installed, and no
benchmark task, fabricated differential call, model, GPU, endpoint or container
was run. `runs/`, frozen receipts and dataset roots were untouched. The complete
archive is raw custody evidence only: its task/answer/result members were never
opened or interpreted. Only the 83 allowlisted code/license/spec members were
read, without extracting the archive to disk.

## Public-source custody

Five unauthenticated public HTTPS GETs through the canonical
`HG_EGRESS_PROXY=http://172.17.0.1:17890` returned HTTP 200. TLS verification stayed
enabled and redirects were disabled. Each raw body and raw header response is
preserved in `tests/fixtures/hg_bfcl/responses/`, with exact curl argument array,
URL, UTC request times, status, byte count and recomputed SHA-256 in the adjacent
receipt. `SHA256SUMS` covers bodies, headers, receipts, scripts and derived
inventory. Every digest below was recomputed from the named bytes in this
worktree; none is copied as evidence from an earlier report.

| Custody object | Preserved raw body (relative to `tests/fixtures/hg_bfcl/`) | Measured bytes | Recomputed SHA-256 |
| --- | --- | ---: | --- |
| GitHub v1.3 ref | `responses/tag.body` | 335 | `8c0af1047736edde0312513fd217bb4168fc6fd052a221a1212cfba2d537a64f` |
| GitHub v1.3 release / V3 prose | `responses/release.body` | 19950 | `83290385b6160c577bb385d3c83bc990eead8fb57dfeeafb28071762c27592a7` |
| Immutable Git commit response | `responses/commit.body` | 3047 | `e9f5ca4365d35fe97b3bf8c5497c3859e54625871dde06b05466609892d515f8` |
| Original root license Git blob response | `responses/license-blob.body` | 15937 | `50da305e0d4fda682c3b928ab2d03994b787b2828903b2198d1ed129bcbc5825` |
| Complete codeload v1.3 tar.gz response | `responses/archive.body` | 44552456 | `57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe` |

The live lightweight ref is `refs/tags/v1.3`, pointing to commit
`ea13468e4423454d0c213704fb87cf7cb3990433`; its tree is
`b34aa53a73491f42dc69b8b18c92448d7c311f50`. These values come from the fresh
raw responses. Release prose identifies "Leaderboard V3"; `target_commitish`
is not used as an immutable revision. `verify.py:52` reconstructs the signed
Git commit object from the API payload/signature and hashes it. This checks Git
object identity; it does not independently authenticate the PGP signing key.

The archive measures **44,552,456 bytes**. Its recomputed SHA-256 agrees with the
adapter declaration at `data/datasets/bfcl_v3.py:99`. Its PAX `comment` equals the
same commit. All 83 exact `gorilla-1.3/<original_path>` members match the retained
original bytes, measured sizes, SHA-256 and reconstructed Git blob hashes.
`inventory.json` supplies the complete 83-row member custody table, including
original path, retained path, archive member, size, SHA-256 and blob identity.
No filename, size or member digest was inferred from another report.

The B5 raw tree responses are reused rather than downloaded again. Both are
rehashed here; their entries reconstruct all 93 Git tree objects and root all
83 path/blob links at the fresh commit tree. GitHub's recursive response `sha`
contains the requested commit ID; the non-recursive root response contains the
tree ID. The verifier distinguishes these and verifies actual tree encodings.

| Reused or decoded custody bytes | Bytes | Recomputed SHA-256 |
| --- | ---: | --- |
| `tests/fixtures/external_provenance_b5/responses/bfcl-tree.body` | 161306 | `f903cf4935a1528bdab0726d8894072f9c69c42e2cc6c5a2e77eb56d64053b49` |
| `tests/fixtures/external_provenance_b5/responses/bfcl-root-tree.body` | 2747 | `abc34da884e5821fe189f05aefcd29096e3c04846c391688564fd023d6f2f0bf` |
| `tests/fixtures/hg_bfcl/LICENSE.upstream` | 11357 | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

The unchanged decoded license matches both the fresh blob and
`gorilla-1.3/LICENSE`. It is Apache-2.0 (`LICENSE.upstream:1`, `:2`), consistent
with the original package declaration (`pyproject.toml:16`, resolved in the
runtime citation table below). Copyright and patent grants appear at
`LICENSE.upstream:66` and `:73`; redistribution requires a license copy,
modified-file notices, retained attribution and applicable NOTICE content
(`:89`, `:94`, `:97`, `:100`, `:106`). Trademark permission is limited (`:138`);
warranty and liability exclusions occur at `:143`, `:153`. The referenced
`LICENSE.upstream` is `tests/fixtures/hg_bfcl/LICENSE.upstream` throughout this
paragraph. The B5 full tree lists no BFCL-local LICENSE/NOTICE/COPYING filename;
this is a filename observation, not third-party license closure. The repository
license does not establish the terms of missing wheels/native dependencies.

Custody establishes that the current public archive is byte-identical to the
existing declared digest and contains the retained original source. It does not
invent historical acquisition dates, attest an unavailable earlier download,
or authorize publishing a score. The unmodified archive itself is intentionally
not an admissible code-only actor/source projection.

## Static Simple graph review

The following citation key and handler table resolve to original retained file lines.

## Citation key

A prefix is a literal repository-relative directory. A citation such as
`A:357` identifies the file below and its line, not the old source repository.

| Key | Exact retained local path |
| --- | --- |
| P | `tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/` |
| B | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/` |
| C | `tests/fixtures/bfcl_ast_source/retry-codex227/files/berkeley-function-call-leaderboard/` |
| E | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/` |
| A | `C/bfcl_eval/eval_checker/ast_eval/ast_checker.py` |
| R | `P/bfcl_eval/eval_checker/eval_runner.py` |
| H | `P/bfcl_eval/eval_checker/eval_runner_helper.py` |
| U | `P/bfcl_eval/model_handler/utils.py` |
| V | `B/bfcl_eval/utils.py` |
| M | `B/bfcl_eval/constants/model_config.py` |
| O | `B/bfcl_eval/model_handler/local_inference/base_oss_handler.py` |
| J | `B/bfcl_eval/model_handler/parser/java_parser.py` |
| S | `B/bfcl_eval/model_handler/parser/js_parser.py` |
| JC | `B/bfcl_eval/eval_checker/ast_eval/type_convertor/java_type_converter.py` |
| SC | `B/bfcl_eval/eval_checker/ast_eval/type_convertor/js_type_converter.py` |
| MT | `B/bfcl_eval/eval_checker/multi_turn_eval/multi_turn_utils.py` |
| L | `data/datasets/bfcl_v3.py` |
| QA | `data/scaffolds/qa/verify/check.py` |

## Finding: local score is a documented name diagnostic

`L:34` explicitly says “This is not the official BFCL score”; the emitted metric
repeats this at `L:230`. `QA:217` imports the adapter, gets its `score` at `QA:223`
and calls it at `QA:242`. The actual BFCL scorer implementation here is `L:217`:
it normalizes each expected/predicted name to its final dotted component
(`L:173`, `L:181`, `L:223`), parses one nonempty name per line while dropping
NONE/no-tool/no-function sentinels (`L:199`, `L:211`), and compares sorted lists
(`L:225`). Despite the text saying “set,” multiplicity is retained; this is
bare-name multiset equality. No argument or call syntax is checked. The rendered
task explicitly asks only for names (`L:283`, `L:293`). Trusted gold construction
retains extracted names (`L:491`, `L:504`), rather than the full alternatives the
upstream checker consumes.

The adapter's prose at `L:231`–`:234` that acceptable-value lists are not decidable
is not a property of the retained official checker: `A:486` directly tests value
membership and the helper branches below check structured alternatives. The
local diagnostic is a deliberate implementation boundary, not the official
full-call score. This report does not change that implementation or wording.

## Official dispatch, handler selection and metric scope

`R:474` enters `main`; `V:187` expands categories from the mapping; `R:488` and
`R:502` dispatch into `runner`. `R:494` refuses model names outside the exact
catalogue. `R:29` selects the configured class, constructs it at temperature zero,
and sets its `is_fc_model`; `R:382` uses this selection for each result file.
`M:4`–`:70` imports the entire handler catalogue at module import time, regardless
of which model is later selected.

The pinned catalogue contains **no `qwen3.5-9b-local` key or string** (fresh literal
search over the complete `M` file). For contrast, the actual source maps
`Qwen/Qwen3-0.6B` to `QwenHandler`, prompting mode, and no dot replacement
(`M:1492`, `M:1498`, `M:1501`, `M:1502`); its class inherits `OSSHandler`
(`B/bfcl_eval/model_handler/local_inference/qwen.py:5`) and therefore uses `O:55`.
The FC entry maps instead to `QwenFCHandler` (`M:1480`, `M:1486`, `M:1489`).
Choosing prompting versus FC cannot be inferred from the local model's name.
No concrete local handler mapping, response grammar or integration is established.

`R:420` defaults to Python; Java and JavaScript categories change language at
`R:421` and `R:423`. `R:432` and `R:442` load complete prompt/reference category
files with sorting; `R:444` chooses multi-turn, while Simple goes through
`R:457` into `ast_file_runner`. Relevance/irrelevance takes its own route at
`R:434`. The retained runner's whole-category loading is not selected-only
loading, and must not replace the platform's privacy boundary without integration.

`R:256` checks equal lengths only; the loop joins lists positionally at `R:263`
without asserting ID equality across them. `R:270` decodes using the concrete
handler. `V:140` requires a list of singleton dictionaries whose value is a
parameter dictionary; empty lists pass this format check (`V:146`). Simple then
requires one call (`A:44`). `R:307` invokes the six-argument `ast_checker` with
function schema, decoded output, full answer alternatives, language, category and
model name. Validity increments the correct count (`R:316`) and category accuracy
is correct count divided by all result entries (`R:332`). Decode exceptions and
wrong formats remain in that denominator (`R:271`, `R:287`); checker exceptions
at `R:307` are outside that try block. Empty result lists divide by zero. Neither
such exceptions nor provisioning failures establish a measured platform zero.

The leaderboard's **Simple AST aggregate** is the unweighted mean of Python
`simple`, `java`, and `javascript` (`H:212`, `H:218`, `H:219`, `H:222`, `H:38`).
Python category accuracy, a selected Python subset diagnostic, and this aggregate
have different coverage/denominators. Missing-category values can affect the
aggregate even when its display says N/A (`H:45`, `H:53`).

## Complete Simple acceptance rules in the retained checker

| Condition / comparison | File+line and exact behavior |
| --- | --- |
| Route/cardinality | `A:33` selects parallel before multiple; otherwise `A:44` requires exactly one decoded call, then `A:52` takes the first function schema and first answer item. |
| Reference/name | `A:330` takes the first value of the answer dictionary; `A:332` gets the function name from the schema. `A:75` normalizes model `_` to `/`; if a schema name has dots, config lookup at `A:78` may map dots to underscores. `A:346` requires that resulting name in the decoded call. The outer format check is what ensures only one key; the Simple checker alone tests membership. It does not drop namespaces as the local diagnostic does. |
| Required params | Each schema-required parameter must occur (`A:357`). |
| Unexpected params | Every supplied param must occur both in schema properties and reference alternatives (`A:365`, `A:366`). |
| Language conversion | Python uses `A:11` vocabulary with `tuple -> list` and `any -> str` (`A:419`). Java and JS index their type maps before checking map membership (`A:378`, `A:399`), require string parser output (`A:381`, `A:402`), and invoke converters (`A:392`, `A:396`, `A:413`, `A:417`). Unknown map keys/converter failures may raise. |
| Tuple / numeric normalization | Expected tuple accepts tuples converted to lists (`A:428`) and already-list values through the type map, an upstream documented false-positive (`A:427`). Python exact integers are converted to float for expected float (`A:432`); bool is not accepted via that exact-type integer branch. |
| Type / variables | `A:115` uses exact `type` equality. `A:68` takes the first nonempty-string alternative's type. A mismatch between that type and schema type marks a variable (`A:111`); a supplied value with the alternative's type can also be accepted as a variable (`A:151`, `A:154`). This is not strict schema-only validation. |
| Nested type behavior | Arrays/tuples consult `items.type` (`A:421`) and validate one inner level (`A:121`, `A:126`). The code's `flag=True` path skips inner checking for non-list alternative items (`A:124`, `A:138`); a scalar/optional marker can therefore bypass the inner loop. The fallback at `A:151` sets `is_variable` but does not restore `valid` after an inner failure; that failure remains invalid. These static observations are not fabricated differential results. |
| String values | Nonvariable strings use `string_checker` (`A:474`) and `A:166` normalization: remove space/comma/dot/slash/hyphen/underscore/asterisk/caret, lowercase, convert single to double quotes (`A:171`, `A:172`). Compare against normalized string alternatives (`A:175`, `A:182`). Variables bypass that branch. |
| Lists / tuples | Nonvariable lists (`A:480`) compare ordered whole lists against alternatives; only direct string elements are normalized (`A:194`, `A:200`, `A:209`, `A:216`). No set/order-free interpretation and no general recursive normalization. |
| Dictionaries | `A:458` calls `A:228`: try each nonempty-marker dictionary alternative; reject unexpected keys (`A:245`), require each provided value to be in that key's alternatives after direct-string normalization (`A:253`, `A:268`), and allow absent keys only when their value alternatives contain empty string (`A:277`, `A:278`). No general deep-dictionary recursion. |
| List of dictionaries | `A:466` invokes `A:291`, checks equal length against each alternative (`A:301`), and applies dict checker pairwise in order (`A:308`). |
| Other / variable values | Membership `value in possible_answer[param]` (`A:486`) with Python equality after conversion; this is not arbitrary tolerance or execution of the benchmark function. |
| Omitted optional params | Every omitted reference parameter needs empty string in its alternatives (`A:495`, `A:496`), even if schema does not call it required. Required schema params cannot be omitted using this marker because `A:357` runs first. |

These are source semantics with file+line support, not a revised scorer. Malformed
reference/schema inputs are not uniformly converted to false verdicts. The
checker compares parsed name/arguments against reference alternatives; it does
not call the requested tool as part of Simple AST scoring.

## Parser/helper review and expression evaluation

Prompting decoder `U:695` strips backticks/newlines/spaces and supplies brackets.
Python parsing `U:241` uses `input_str.strip("[]'")` and `ast.parse(..., mode="eval")`.
It either processes one call or requires every collection element be a call
(`U:246`, `U:249`). `U:263` preserves dotted function attributes, visits keyword
arguments only (`U:274`), ignores positional arguments, and overwrites repeated
keyword keys in its dictionary (`U:276`). An unpacked keyword can have key None;
the checker/format path must determine resulting acceptability.

`U:280` recursively resolves constants (including Ellipsis as string), lists,
dicts, tuples, names, nested calls and subscriptions. Unary operations all take
negative operand `.value` (`U:286`, `U:287`), without checking which unary operator;
nonconstant operands can raise. Names become identifier strings (`U:303`);
no-keyword nested calls become source strings, while keyword-bearing calls become
nested call dictionaries (`U:305`). Unsupported AST nodes raise (`U:322`).

**Python AST parsing is not a nonexecuting parser on all branches:**
`U:302` directly evaluates unparsed binary-expression text with ordinary Python
builtins/globals available. There is no restricted globals dictionary or grammar
allowlist there, and nested call operands inside a binary expression can therefore
execute. `U:313` similarly calls `eval` in the lambda branch, but indexes a usual
lambda expression body as `body[0].value`; ordinary lambda AST bodies may raise
before reaching eval. Both branches need containment in any execution design.
`U:230` and `U:646` are distinct `ast.literal_eval` helper sites (literal-only,
not equivalent to ordinary eval).

Java and JS parser modules instantiate tree-sitter `Language`/`Parser` and set
the language at import (`J:1`, `J:4`, `J:6`, `S:1`, `S:4`, `S:6`). Thus these
native modules are reached through shared utility imports even for Python
prompting. Java visits first method invocation and preserves names/object prefix
(`J:108`), takes named assignments plus some unnamed identifiers (`J:82`), and
rejects duplicate argument names collected as lists (`J:121`). JS similarly
returns its first recognized expression-statement call (`S:49`, `S:54`), extracts
assignment values as source strings with outer quote trimming (`S:22`–`:29`) and
rejects duplicate-name lists (`S:57`). Neither is a JVM/Node execution engine.

Java converter `JC:6` applies regex-directed numeric/bool parsing; array/list/map
conversion uses source-text regexes/splitting (`JC:57`, `JC:70`, `JC:121`, `JC:143`),
with some failures returned as source strings (`JC:118`, `JC:140`, `JC:162`).
Set/Hashtable/Queue/Stack conversion explicitly raises (`JC:39`, `JC:43`, `JC:45`).
JS converter `SC:5` similarly converts primitive text, arrays/dicts (`SC:42`),
returns source strings on collection failure (`SC:99`, `SC:101`, `SC:127`), and
recurses text collections (`SC:118`, `SC:121`). No bare eval site was found in
either converter or either tree-sitter parser. Runtime API/ABI compatibility of
these native parser calls is still untested.

### Catalogue eval-site scope (all bare eval calls in the 81 Python files)

| Exact site | Route and effect |
| --- | --- |
| `U:302`, `U:313` | Shared prompting keyword-value resolver; ordinary expression eval as described above, including transitively from MiniCPM-FC `E/bfcl_eval/model_handler/local_inference/minicpm_fc.py:318` (fc2dict uses shared resolver). The separate MiniCPM conversion helper at `:281` uses literal_eval; that is not the fc2dict scoring route. |
| `B/bfcl_eval/model_handler/local_inference/arch.py:167` | Generation-response processing `_parse_query_response_prompting` at `:153` evals strings emitted by tag extraction; `decode_ast` at `:18` only accepts preprocessed list/dicts. Not direct AST decoding of a retained list result. |
| `E/bfcl_eval/model_handler/local_inference/granite_3.py:125` | Tag extractor parses JSON first at `:117`, then evals string elements of the resulting list; generation-response preprocessing calls extractor (`:40`, `:56`). `decode_ast` at `:133` validates list/dicts only. |
| `E/bfcl_eval/model_handler/local_inference/phi_fc.py:195` | Similar extractor: JSON attempt at `:187`, eval each string list element at `:194`; `decode_ast` at `:86` validates list/dicts only. |
| `E/bfcl_eval/model_handler/local_inference/llama_3_1.py:198` | Direct `decode_ast` path: remove tag (`:186`), semicolon branch JSON-decodes segments (`:188`, `:193`); otherwise eval entire response (`:198`), wrap dictionary and project name/parameters. This is a direct Simple decoding execution surface. |
| `E/bfcl_eval/model_handler/local_inference/llama_3_1.py:218` | Separate `decode_execute` route evals same response form; outside Simple AST caller. |
| `E/bfcl_eval/model_handler/local_inference/qwen_fc.py:38` | Separate `decode_execute` branch attempts eval of a string item, but the prior list/all-dictionary guard (`:33`) returns before a string item can reach this branch. `decode_ast` at `:19` does tag/JSON extraction (`:270`, `:278`) and rejects non-dict items (`:23`); no bare eval is on that direct AST route. |
| `MT:93` | Multi-turn-only execution helper: dynamically imports eight string-named modules (`MT:7`, `MT:48`) and evals calls after a small name denylist (`MT:90`). Imported support is reached unconditionally from BaseHandler (`P/bfcl_eval/model_handler/base_handler.py:12`) and eval_runner (`R:18`); Simple's branch does not call that helper. The dynamic function-module bodies are outside retained Simple projection. |

No direct Name-call `exec` or `compile` site was found by static AST traversal in
these retained Python files. This scoped search is not a claim that third-party
libraries, attribute calls or unavailable dynamic modules cannot execute code.

## Handler catalogue decoding review coverage

The table maps every retained handler class to the actual override or inherited
path. Line is class declaration for inherited routes, and decode declaration when
present. Upstream names are linked to the exact local custody file, which also
avoids confusing original paths with differently named retained local files.
Prompting/inherited paths reach shared resolver eval above; JSON-only and
preprocessed-result paths do not inherit that assumption automatically. The full
catalogue remains an import-time dependency even if only one route is selected.

| Class | Exact retained local file | Decoder / inheritance citation |
| --- | --- | --- |
| `ClaudeHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/claude.py` | decode_ast:31 |
| `CohereHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/cohere.py` | decode_ast:28 |
| `DatabricksHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/databricks.py` | decode_ast:27 |
| `DeepSeekAPIHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/deepseek.py` | class:18; inherits OpenAICompletionsHandler |
| `DMCitoHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/dm_cito.py` | class:6; inherits MiningHandler |
| `FireworksHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/fireworks.py` | class:9; inherits OpenAICompletionsHandler |
| `FunctionaryHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/functionary.py` | class:6; inherits OpenAICompletionsHandler |
| `GeminiHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/gemini.py` | decode_ast:51 |
| `GoGoAgentHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/gogoagent.py` | class:7; inherits OpenAICompletionsHandler |
| `GorillaHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/gorilla.py` | decode_ast:16 |
| `GrokHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/grok.py` | class:9; inherits OpenAICompletionsHandler |
| `LingAPIHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/ling.py` | class:17; inherits OpenAICompletionsHandler |
| `MiningHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/mining.py` | decode_ast:21 |
| `MistralHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/mistral.py` | decode_ast:26 |
| `NemotronHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/nemotron.py` | decode_ast:83 |
| `NexusHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/nexus.py` | decode_ast:119 |
| `NovaHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/nova.py` | decode_ast:30 |
| `NovitaHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/novita.py` | class:8; inherits OpenAICompletionsHandler |
| `NvidiaHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/nvidia.py` | decode_ast:23 |
| `OpenAICompletionsHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/openai_completion.py` | decode_ast:28 |
| `OpenAIResponsesHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/openai_response.py` | decode_ast:39 |
| `QwenAPIHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/qwen.py` | class:11; inherits OpenAICompletionsHandler |
| `QwenAgentThinkHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/qwen.py` | class:206; inherits OpenAICompletionsHandler |
| `QwenAgentNoThinkHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/qwen.py` | class:305; inherits QwenAgentThinkHandler |
| `WriterHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/writer.py` | class:9; inherits OpenAICompletionsHandler |
| `YiHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/yi.py` | decode_ast:23 |
| `BaseHandler` | `tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/base_handler.py` | decode_ast:654 |
| `ArchHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/arch.py` | decode_ast:18 |
| `OSSHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/base_oss_handler.py` | decode_ast:55 |
| `BielikHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/bielik.py` | class:7; inherits OSSHandler |
| `BitAgentHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/bitagent.py` | decode_ast:63 |
| `DeepseekHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/deepseek.py` | decode_ast:13 |
| `DeepseekCoderHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/deepseek_coder.py` | decode_ast:23 |
| `DeepseekReasoningHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/deepseek_reasoning.py` | class:11; inherits OSSHandler |
| `Falcon3FCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/falcon_fc.py` | decode_ast:26 |
| `GemmaHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/gemma.py` | class:10; inherits OSSHandler |
| `GlaiveHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/glaive.py` | decode_ast:13 |
| `GLMHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/glm.py` | decode_ast:51 |
| `GraniteFunctionCallingHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/granite.py` | decode_ast:57 |
| `Granite3FCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/granite_3.py` | decode_ast:133 |
| `HammerHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/hammer.py` | decode_ast:91 |
| `HermesHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/hermes.py` | decode_ast:59 |
| `LlamaHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/llama.py` | class:5; inherits OSSHandler |
| `LlamaHandler_3_1` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/llama_3_1.py` | decode_ast:185 |
| `MiniCPMHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/minicpm.py` | class:5; inherits OSSHandler |
| `MiniCPMFCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/minicpm_fc.py` | decode_ast:57 |
| `MistralFCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/mistral_fc.py` | decode_ast:18 |
| `PhiHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/phi.py` | decode_ast:10 |
| `PhiFCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/phi_fc.py` | decode_ast:86 |
| `QuickTestingOSSHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/quick_testing_oss.py` | class:12; inherits OSSHandler |
| `QwenHandler` | `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/qwen.py` | class:5; inherits OSSHandler |
| `QwenFCHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/qwen_fc.py` | decode_ast:19 |
| `SalesforceLlamaHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/salesforce_llama.py` | decode_ast:62 |
| `SalesforceQwenHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/salesforce_qwen.py` | decode_ast:64 |
| `ThinkAgentHandler` | `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/local_inference/think_agent.py` | decode_ast:137 |

Direct override grouping, reviewed from each listed function body:

- Prompt-text normalization before shared AST parser: Claude non-FC, Databricks,
  Gemini non-FC, Gorilla, Mistral non-FC, Nemotron, Nexus, Nvidia, OpenAI
  completions/responses non-FC, Deepseek, Phi. These can reach `U:302`.
- JSON/tool-call projection: Claude/Mistral/OpenAI FC branches, Cohere, Mining,
  Yi, BitAgent, Falcon3-FC, Glaive, GLM, Granite, Hammer, Hermes, Qwen-FC,
  Salesforce-Llama/Qwen, ThinkAgent. They have distinct delimiter/JSON failures;
  outputs still need `V:140` and `A:44`–`:504`. Glaive converts non-Python arguments
  to strings; GLM can return a list containing a string (later rejected by format).
- Preprocessed list routes: Gemini FC, Nova, Arch, DeepseekCoder, Granite3-FC,
  Mistral-FC, Phi-FC. Each trusts previously constructed result shape to varying
  degrees, while format checking imposes singleton-call dictionaries afterward.
- Llama-3.1 has direct response eval; MiniCPM-FC `fc2dict` uses the shared keyword
  resolver. They are distinct from literal-only JSON paths.
- Every inheritance row above was checked against its retained class declaration
  and parent decode override; BaseHandler itself raises NotImplementedError
  (`P/bfcl_eval/model_handler/base_handler.py:658`).

## What is missing for official execution

The current local implementation never calls the retained official AST checker.
There is no exact local-model handler mapping/full-call response grammar, no
executed full-call differential contract, and no observed official scorer
execution bound to selected-only inputs. The upstream whole-category loader,
positional list join, reference-bearing failure logs (`R:282`, `R:302`, `R:329`),
and expression-evaluation branches require an actual selected-only trusted/offline
integration rather than a replacement of diagnostic claims.

Runtime closure is also not established by source acquisition: catalogue imports,
parser native extensions, interpreter/stdlib ABI, and provider/helper transitive
packages need pinned offline bytes and an execution-bound runtime. Constructor
`O:40` creates an OpenAI client; that is not evidence of an HTTP request or of
execution. Generation path `O:79` imports transformers and `O:109`/`:110` load
model configuration/tokenizer with optional remote code (`O:100`, `O:106`); these
are separate generation dependencies, not an authorization to generate here.
Simple AST needs tree-sitter native parser modules, not Java or Node binaries.

These are concrete unmet implementation/runtime/execution obligations. A withdrawn
reviewer signature is not among them. The blocker must remain external under the
operator's explicit closure rule while these obligations remain unmet.

## Reproduction of this static review

The integrity check read each inventory member's local bytes and compared
`hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()` to its inventory field;
no stored digest was copied into this report. The inventory raw-byte SHA-256
recomputed in this review is `23ded7d2a3db0540eef64faf945e3b3392ef6d5f95b8dbbd2793e656626cfefa`.
To reproduce named-expression sites without importing upstream source:

```python
import ast, json
from pathlib import Path
inventory = json.loads(Path('tests/fixtures/e3_bfcl_source/inventory.json').read_bytes())
for row in inventory['files']:
    if not row['path'].endswith('.py'):
        continue
    tree = ast.parse(Path(row['path']).read_bytes(), filename=row['path'])
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {'eval', 'exec', 'compile'}):
            print(row['path'], node.lineno, node.func.id)
```

## Offline third-party/native runtime inventory

The runtime citation key extends the preceding key; shared abbreviations identify the same files.

## Citation key

Every `key:line` citation means the exact retained filename in this table and
that original line number. Prefixes below are literal checked-in directories:

- P = `tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/`
- B = `tests/fixtures/external_provenance_b5/bfcl/files/berkeley-function-call-leaderboard/`
- E = `tests/fixtures/e3_bfcl_source/files/berkeley-function-call-leaderboard/`
- C = `tests/fixtures/bfcl_ast_source/retry-codex227/files/berkeley-function-call-leaderboard/`

| Key | Exact filename (prefix plus suffix) |
| --- | --- |
| PP | P + `pyproject.toml` |
| R | P + `bfcl_eval/eval_checker/eval_runner.py` |
| H | P + `bfcl_eval/eval_checker/eval_runner_helper.py` |
| A | C + `bfcl_eval/eval_checker/ast_eval/ast_checker.py` |
| M | B + `bfcl_eval/constants/model_config.py` |
| EC | B + `bfcl_eval/constants/eval_config.py` |
| U | P + `bfcl_eval/model_handler/utils.py` |
| BH | P + `bfcl_eval/model_handler/base_handler.py` |
| O | B + `bfcl_eval/model_handler/local_inference/base_oss_handler.py` |
| Q | B + `bfcl_eval/model_handler/local_inference/qwen.py` |
| JP | B + `bfcl_eval/model_handler/parser/java_parser.py` |
| JSP | B + `bfcl_eval/model_handler/parser/js_parser.py` |
| JC | B + `bfcl_eval/eval_checker/ast_eval/type_convertor/java_type_converter.py` |
| JSC | B + `bfcl_eval/eval_checker/ast_eval/type_convertor/js_type_converter.py` |
| MT | B + `bfcl_eval/eval_checker/multi_turn_eval/multi_turn_utils.py` |
| CLA | B + `bfcl_eval/model_handler/api_inference/claude.py` |
| CO | B + `bfcl_eval/model_handler/api_inference/cohere.py` |
| GEM | B + `bfcl_eval/model_handler/api_inference/gemini.py` |
| MIS | B + `bfcl_eval/model_handler/api_inference/mistral.py` |
| NOV | B + `bfcl_eval/model_handler/api_inference/nova.py` |
| AQ | B + `bfcl_eval/model_handler/api_inference/qwen.py` |
| WR | B + `bfcl_eval/model_handler/api_inference/writer.py` |
| MC | E + `bfcl_eval/model_handler/local_inference/minicpm_fc.py` |
| LIC | `tests/fixtures/bfcl_ast_source/candidate-codex195/files/LICENSE` |

## What importing/scoring retained Simple actually needs

`R:16` imports the checker; `A:1` immediately imports the model catalogue.
`H:11` and `R:23` also import that same catalogue. All concrete handler imports
in `M:4` through `M:70` are top-level imports, so importing the official
checker or evaluation runner requires catalogue dependencies even when only
one local Python Simple decoder will be selected. Provider imports below are
therefore an import-time runtime requirement; this is not evidence that every
provider is called while scoring.

The Qwen prompting example inherits `OSSHandler` (`Q:1`, `Q:5`, `Q:7`), which
imports `requests`, `openai`, `overrides` and `tqdm` (`O:9`, `O:19` through
`O:21`). Its constructor builds an OpenAI client (`O:36` through `O:40`), and
its existing-result AST decoder calls the default parser (`O:55`, `O:56`).
Client construction is part of the scorer's handler construction at `R:29`
through `R:35`; it is not a completion request. Other selected handlers can
have credential checks or provider client construction, for example GEM:33
through GEM:38 and NOV:23 through NOV:28. Their constructors require separate
handling in a genuinely offline invocation.

The shared parser imports both Java and JavaScript parser modules eagerly
(`U:13`, `U:14`) along with `tenacity` (`U:15`). Each parser imports the native
Tree-sitter binding and its grammar, constructs a `Language`, constructs a
`Parser` and sets its language at module initialization (`JP:1` through
`JP:7`; `JSP:1` through `JSP:7`). Thus these grammar/binding binaries are an
import-time requirement even for the Python prompting route. They cannot be
omitted merely because this diagnostic selects Python.

The scorer runner also imports `python-dotenv` and `tqdm` (`R:25`, `R:26`);
the helper imports NumPy and pandas (`H:6`, `H:7`). `EC:34` and `EC:35` create
the result and score directories at import time. An offline review or import
receipt must account for these writes and set an isolated `BFCL_PROJECT_ROOT`
(`EC:17`), rather than treating an import as wholly inert.

The Python value resolver uses Python `ast` and `eval` for binary expressions
and its lambda branch (`U:1`, `U:280`, `U:300` through `U:313`). The runtime
identity and permitted expression grammar matter; pinning a parser filename
does not make these branches sandboxed. Java/JS converters are Python
regex/type conversion (`JC:1` through `JC:6`, `JSC:1` through `JSC:5`). Static
inspection of all 81 retained Python files found no JVM, `javac`, Java executable,
Node.js or JavaScript-engine subprocess for this Simple AST route. The native
dependency here is Tree-sitter; a JVM or Node.js should not be invented as a
Simple requirement.

## Imported third-party packages: source-level pin status

The inventory's Python files contain 21 non-standard-library import roots.
Version constraints below are copied as constraints from original source,
not asserted to be an installed version. Every row remains **artifact-unpinned**:
none of these declarations supplies a wheel/sdist SHA-256 or the transitive
offline dependency and license closure.

| Imported root / distribution | Original import evidence | Original declaration | Source-level version status and scope |
| --- | --- | --- | --- |
| `dotenv` / `python-dotenv` | R:25 | PP:25 `>=1.0.1` | Lower-bounded; runner import |
| `tqdm` | R:26, O:21 | PP:19 | Unbounded; runner/handler import |
| `numpy` | H:6 | PP:20 `==1.26.4` | Exact version; helper import; native wheel identity absent |
| `pandas` | H:7 | PP:21 | Unbounded; helper import; native wheel identity absent |
| `tree_sitter` / `tree_sitter` | JP:1, JSP:1 | PP:26 `==0.21.3` | Exact version; eager native binding |
| `tree_sitter_java` / `tree-sitter-java` | JP:2 | PP:27 `==0.21.0` | Exact version; eager native grammar |
| `tree_sitter_javascript` / `tree-sitter-javascript` | JSP:2 | PP:28 `==0.21.4` | Exact version; eager native grammar |
| `tenacity` | U:15, CO:16 | PP:39 `>=8.5.0` | Lower-bounded; shared import and retry decorators |
| `requests` | O:9 | PP:18 | Unbounded; catalogue/local handler import |
| `openai` | O:19, AQ:6 | PP:29 `>=1.86.0` | Lower-bounded; catalogue/local handler import |
| `overrides` | BH:19, O:20, Q:2 | PP:41 | Unbounded; base classes/decorators at import |
| `anthropic` | CLA:5, CLA:6 | PP:31 `==0.53.0` | Exact version; eager catalogue SDK |
| `cohere` | CO:7 | PP:32 `==5.13.3` | Exact version; eager catalogue SDK |
| `google` (`google.genai`) / `google-genai` | GEM:17 through GEM:19 | PP:36 `==1.24.0` | Exact version; eager catalogue SDK |
| `mistralai` | MIS:16 | PP:30 `==1.7.0` | Exact version; eager catalogue SDK |
| `boto3` | NOV:4 | PP:42 | Unbounded; eager catalogue SDK |
| `qwen_agent` / `qwen-agent` | AQ:8 | PP:37 | Unbounded; eager catalogue SDK |
| `writerai` / `writer-sdk` | WR:6 | PP:40 `>=2.1.0` | Lower-bounded; eager catalogue SDK |
| `datamodel_code_generator` / `datamodel-code-generator` | MC:5, MC:13 through MC:15 | PP:35 `==0.25.7` | Exact version; eager catalogue dependency; helper also branches on version at MC:150 through MC:163 |
| `transformers` | O:79, inside `batch_inference` | No direct declaration in PP:17 through PP:43 | Unpinned direct import; generation-only local batch route |
| `wandb` | H:434, conditional function-local import | PP:69 `==0.18.5` optional extra | Exact optional version; reporting network path only when `WANDB_BFCL_PROJECT` enables H:432 through H:442 |

The package declares additional dependencies not explicitly imported by the
retained 81-file projection: `pathlib` (unbounded, `PP:22`; also a Python
standard-library module), `huggingface_hub` (unbounded, `PP:23`), `pydantic`
(`>=2.8.2`, `PP:24`), `typer` (`>=0.12.5`, `PP:33`), `tabulate` (`>=0.9.0`,
`PP:34`) and `mpmath` (`==1.3.0`, `PP:38`). They are declarations, not proof
that these imports are unused throughout the full package or absent from
transitive dependencies. The entrypoint `bfcl_eval.__main__:cli` (`PP:46`)
is outside this retained graph; this review makes no CLI dependency closure
claim.

Build tooling is also partly unpinned: `setuptools>=61.0`, `wheel`, `build`,
`setuptools-scm[toml]>=8.0` (`PP:2`, `PP:3`). `requires-python = ">=3.10"`
(`PP:15`) pins neither an interpreter build nor ABI. The version is dynamic
(`PP:7`), with a date-shaped tag regex and fallback `0.0.0.dev0` (`PP:71`
through `PP:73`); a `v1.3` source tag does not itself establish a built wheel's
package version.

## Generation and expanded-scope dependencies

Decoding retained results does not call `batch_inference` (`O:63`). Only that
generation path imports transformers (`O:79`), loads tokenizer/config files
(`O:109`, `O:110`) and starts an external server. The local-file branch sets
`local_files_only=True` and `trust_remote_code=True` (`O:97` through `O:101`);
the model-ID branch still trusts remote code and lacks that local-only flag
(`O:103` through `O:110`). These are generation source findings, not operations
performed here.

| Generation binary/module | Original invocation | Declaration | Pin status |
| --- | --- | --- | --- |
| `vllm` executable (`vllm serve`) | O:123 through O:139 | PP:67 `vllm==0.8.5` optional extra | Exact package version; executable, wheel, GPU/native runtime and model files unpinned |
| `python -m sglang.launch_server` | O:144 through O:161 | PP:68 `sglang[all]` optional extra | Interpreter and package unbounded; artifacts/native/model dependencies unpinned |

Readiness calls and completion work are inside the batch path (`O:208`,
`O:220` through `O:240`), rather than `decode_ast`. GPU stacks, tokenizer/model
custody and these servers are unnecessary for a bounded existing-result Simple
decoder/checker review, but necessary if scope expands to generation.

Multi-turn imports occur eagerly (`R:18`, `BH:12`), but the eight function
module names at `MT:7` through `MT:15` are resolved by
`importlib.import_module` inside the multi-turn execution function at `MT:24`
and `MT:48`. Simple's branch does not require executing those dynamic bodies.
No evidence in this review closes their runtime or licensing for an expanded
multi-turn claim.

## Native/Python/offline closure and licensing still missing

The retained graph establishes native grammar demand and exact *declared*
Tree-sitter versions, not a runnable offline combination. The actual grammar
extension binaries, their language-pointer interface and Tree-sitter's
`Language(..., name)`, `set_language` and `sexp` APIs (`JP:4`, `JP:7`, `JP:13`,
`JSP:4`, `JSP:7`, `JSP:14`) must be bound to compatible built artifacts.
Missing evidence includes the chosen Python binary/version/build/ABI;
platform image or OS identity; immutable wheel/sdist and full transitive
dependency manifest; the installed grammar/binding and NumPy/pandas native
objects; their architecture/ABI and dynamically linked libraries; offline
installation and import receipt in an isolated writable root; an explicit
no-egress runtime configuration; and license/notice bytes for each third-party
artifact. No system package list, installed environment or library resolution
was queried here, so no actual libc/BLAS/CUDA identity is claimed.

The BFCL package declares Apache 2.0 (`PP:16`), and retained root license bytes
identify Apache License Version 2.0 (`LIC:1`, `LIC:2`). The terms permit
reproduction/distribution under the stated conditions (`LIC:66` through
`LIC:71`), require license copies, modified-file notices and retained
attribution (`LIC:94` through `LIC:104`), and require any included NOTICE
attributions on redistribution (`LIC:106` through `LIC:115`). The root BFCL
license is not a substitute for the SDKs', compiled bindings', grammar
sources' or transitively vendored libraries' licenses and notices. Their
licenses cannot be assigned from distribution names alone.

The following values were recomputed from the named bytes during this review;
they are not digests transcribed from another report:

| Retained bytes | Recomputed SHA-256 |
| --- | --- |
| `tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/pyproject.toml` | `20d86859dc988e442c13a6c4f3567094bd8f2d4a269d59015337bdf6aeba7dd3` |
| `tests/fixtures/bfcl_ast_source/candidate-codex195/files/LICENSE` | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

This code-only runtime inventory therefore supports leaving
`bfcl_v3/simple-ast-contract` open for actual offline native/package closure
and official scorer execution. It supplies neither an execution receipt nor
an official score.

## Final disposition and remaining work

The current public v1.3 archive, tag/commit/tree chain, all 83 retained member
bindings and root Apache-2.0 license are pinned by recomputed bytes. The handler,
parser, checker, dispatch, helpers and expression-evaluation branches are reviewed
above with file+line. None of this is an official scorer execution receipt.

The unmet portions are concrete:

- Choose and document the concrete handler/response grammar and model-config
  behavior for the local model; the historical catalogue has no such key.
- Obtain a complete offline package/native runtime: exact Python/build/ABI and
  platform identity, every direct/transitive artifact and hash, compatible
  Tree-sitter/grammar/native objects and linked libraries, and applicable
  third-party license/notice bytes. Version constraints alone do not establish it.
- Integrate the original checker with full-call trusted selected schemas and
  reference alternatives while preserving bounded selected reads and redacted
  failures. The official whole-category loader and positional joins do not
  establish that integration; arbitrary expression evaluation needs containment.
- Establish the full-call contract with fabricated differentials in that runtime
  and record actual official scorer execution with exact source/runtime/input/
  metric coverage and receipt identities. Neither occurred in this code-only task.

These are the still-unmet source-gate/full-call/runtime/execution parts of the
starting next_action. No human approval, independent sign-off or separately
"approved" hash is required. The new inventory supplies an ordinary exact
SHA-256 integrity pin in `SHA256SUMS`; it is not mislabeled as a measurement or
as approval. No legacy source-gate helper or safety/privacy gate was modified
or bypassed. This task does not claim that the test-only source-gate helper has
accepted a complete runnable projection.

Accordingly **the blocker did not close**: its status remains `external`, family
`official-scorer-execution`, and no other blocker is removed or downgraded.
The registry observation records the custody/review progress; its next_action
now names the remaining technical work without the withdrawn approval clauses.
Historical source candidates, approval flags and incomplete receipts remain
intact. Production stays diagnostic-only and `production_authorized=false`.

Exact offline validation commands and results are in
`reports/w-bfcl-validation.txt`. They check custody bytes, existing source/registry
regressions and preservation of unrelated blockers/history; they do not measure
BFCL performance. The initial verifier draft confused the recursive response's
commit-valued `sha` with the root-tree ID and refused before reading the archive;
the final verifier distinguishes them and reconstructs all 93 tree objects.

Raw HTTP headers retain CRLF, the HTTP status trailing space and final header terminators exactly. Full staged whitespace checking reports these raw-byte warnings; authored text passes the check with only `responses/*.headers` excluded. No raw response was normalized to satisfy whitespace lint.
