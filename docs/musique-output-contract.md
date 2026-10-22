# MuSiQue public output contract

Use the generic HarnessGrad QA prediction schema v1, supplied as
`qa/prediction.schema.json`. Write `prediction.json` and validate/submit it with
`python3 qa/bin/qa submit-json @prediction.json`:

```json
{"schema_version":1,"answer":"<short answer>","predicted_evidence":[["<public title>",0]]}
```

The placeholder citation illustrates the format; it does not identify evidence for
any task. Select evidence from the public context. Each pair contains a nonempty
title and a nonnegative integer paragraph `[idx]`, as printed in the context.
The index is not the paragraph's position in the list or a sentence number.
Titles are descriptive; upstream MuSiQue support evaluation uses only indices.
Duplicate indices count once, and extra indices (including nonexistent indices)
are false positives. No gold answers, aliases, supporting flags, decomposition,
task-specific hints, or arbitrary metadata belong in the artifact.

`prediction.json` takes precedence over `answer.txt`. A missing, null, or empty
`predicted_evidence` is an empty support prediction. A plain `answer.txt` is also
an empty support prediction; it does not opt into legacy scoring. Missing answers
are measured zero. Malformed JSON, unsupported keys, or malformed citation pairs
are unmeasured verifier failures, even when a valid `answer.txt` exists.

Default mode `answer_support` reports the official **Answer F1**, **Support F1**,
and **Answer EM** separately. Answer metrics take the best canonical-answer/alias
match. Support F1 uses the upstream paragraph-index set metric, including F1=1
when both support sets are empty. These components are unrounded per task.

The platform scalar reward is `answer_f1 * support_f1`; the platform joint EM is
`answer_em * support_em`. Neither is an official MuSiQue aggregate metric.
`correct` means joint EM=1. `data.musique.aggregate(results)` uses ordered macro
means and rounds only the three official columns to three decimals, as upstream
does. Mean joint reward is the mean of per-task products, not the product of
corpus means. Downstream platform summaries that retain only scalar rewards
cannot reconstruct the separate official columns; retain verifier `official`
records and aggregate them explicitly.

Backward compatibility requires trusted configuration `legacy_answer_only`:
`score(..., mode="legacy_answer_only")`,
`score_prediction(..., mode="legacy_answer_only")`,
`load(mode="legacy_answer_only")`, or
`verify_for(record, mode="legacy_answer_only")`. A MuSiQue load selects the mode
for subsequent module `VERIFY` maps; another `load()` resets it to the default.
Verifier argv records the selected mode. Predictions cannot choose the mode.
Legacy reward is answer F1, `correct` is Answer EM, and Support F1 is explicitly
`null` (unmeasured). Legacy and default results must not be mixed or compared as
the same policy.

Gold answers and support annotations live only in trusted `VERIFY.inputs`,
re-materialized at verification. MuSiQue verifier evidence redacts gold before
prediction parsing, including invalid-artifact paths. The public task retains
all context paragraphs in their original order.

The adapter still hosts MuSiQue-Ans v1.0 dev with the existing platform hash
partition. This is not the official hidden test set. Sufficiency evaluation for
MuSiQue-Full is outside this contract. The adapter does not add a selected-only
payload loader; requesting its full task/split maps still reads the dev pool.
