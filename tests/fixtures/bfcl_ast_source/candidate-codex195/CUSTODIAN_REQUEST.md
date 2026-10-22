# Exact outstanding code-only custodian request — Codex 195

Please provide an independently reviewed, **code-only** projection for
`https://github.com/ShishirPatil/gorilla`, repository tag `v1.3`, observed commit
`ea13468e4423454d0c213704fb87cf7cb3990433`. The official release labels this V3.
Do not send any tasks, schemas from benchmark rows, answers, heldout material,
model result files, datasets, archives or dataset-root access to this worktree.

1. **Archive and historical ref custody.** Independently attest that the pinned
   codeload archive has SHA-256
   `57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe` and binds
   `v1.3` to the commit above. For each allowed source/license/dependency file,
   attest its original `gorilla-1.3/<repository-relative-path>` member, size and
   SHA-256. Supply metadata attestations and code-only bytes; retain the archive
   and benchmark material in your trusted custody. If the digest or commit does
   not match, report the mismatch; do not silently repin this adapter.
2. **Missing checker and exact semantic graph.** Supply original
   `berkeley-function-call-leaderboard/bfcl_eval/eval_checker/ast_eval/ast_checker.py`
   (observed Git blob `214f3ba55c012691761913b6aca68ab7bc4aa38b`) and all actual
   transitively used helpers/constants, including the original files required
   from `ast_eval/type_convertor/`. Give the checker definition/signature and
   exact Simple category dispatch. Review function/call cardinality and name
   matching, required/optional/extra parameters, reference-value alternatives,
   types, nested collections, normalization and ordering with source citations.
   Do not invent helper filenames or substitute another release's checker.
3. **Parser and dispatcher dependencies.** Starting from the acquired original
   sources, provide missing `bfcl_eval/model_handler/parser/java_parser.py`,
   `parser/js_parser.py`, `model_handler/model_style.py`,
   `constants/default_prompts.py`, `constants/category_mapping.py`,
   `constants/eval_config.py`, `constants/column_headers.py`,
   `constants/model_config.py` and `bfcl_eval/utils.py`, all beneath
   `berkeley-function-call-leaderboard/`. Review the actual further import/call
   graph before supply: source with embedded benchmark payload must be rejected
   rather than declared code-only. The runner/base handler imports multi-turn
   modules; establish whether the original permitted Simple invocation needs
   those modules and supply reviewed code-only closure if it does. Missing
   modules may not be stubbed or deleted to manufacture runnable closure.
4. **Concrete decoding contract and runtime closure.** Identify the intended
   official model handler, its `decode_ast` implementation and transitively used
   parser/helper files. The acquired `BaseHandler.decode_ast` is abstract.
   Distinguish default prompting from native function-calling output handling.
   Supply original dependency specifications and an independently reviewed
   offline runtime lock (Python/ABI/image identity, package/wheel/native grammar
   hashes and licenses). Confirm the permitted invocation without executing
   benchmark predictions here. Parser expression-evaluation branches require
   runtime review before any later use.
5. **Independent admission receipt.** Validate the seven retained candidate
   files against custody evidence and original license/NOTICE coverage. Use the
   exact `harnessgrad.bfcl-ast-source.v1` schema in the parent acquisition contract
   with reviewed entrypoints, complete `requires` edges, member bindings and
   nonempty review identities. Supply the SHA-256 of the exact approved receipt
   through a separate trusted channel; computing the candidate's hash cannot
   approve it. Do not promote this incomplete candidate record.

No response on this request closes selected-index/release-storage custody,
complete selected reference preservation, verifier/runtime isolation, artifact
export, quota, cleanup or measured diagnostic review. No H0 or model run is
authorized by source acquisition.
