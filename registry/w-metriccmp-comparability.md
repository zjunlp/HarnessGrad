# w-metriccmp: local versus upstream metric comparability

Source-level review only. All examples below are invented mathematical inputs, not
dataset answers or observed executions. The proposal chooses separate reporting for
three different metrics and retains an undetermined original-benchmark comparison
for StrategyQA. No blocker is closed and no execution is authorized.

## Evidence scope and common local processing

**FOUND:** retained upstream source views were retrieved from local Git objects and
copied unchanged into `registry/w-metriccmp-sources/`. The exact `git show` commands
and observed outputs are in `registry/w-metriccmp-validation.txt`. Original Git
locations, observed source-view sizes/digests, and recorded upstream revision URLs
are in `registry/w-metriccmp-sources/provenance.json:3`, `:13`, `:23`, `:34`, `:45`
and `:53`. These are readable comparison sources, not runtime imports or new
upstream authentication. The SpreadsheetBench view equals the retained API source
after linewise trailing-whitespace removal, not byte-for-byte
(`registry/w-metriccmp-sources/provenance.json:41`). Installed custody and row/task
inventories are inherited, not recomputed here.

**ABSENT, bounded scope:** the worktree directories `tests/fixtures/hg_e1_review`,
`tests/fixtures/hg_wprov`, and `tests/fixtures/hg_wjc2` were absent at initial
inspection. Only the ignored `hg_wprov/README.md` was subsequently restored
unchanged for the registry checker; the other two directories remain absent.
Reproducible commands and exact outputs are recorded in the validation
transcript, including restoration. This is not machine-wide absence:
retained Git objects supplied the source views above. **UNKNOWN:** other machines,
unsearched filesystem/package locations, fresh network source, runtime behavior,
and completeness of installed upstream dependencies.

For all three text QA adapters, the effective local input includes shared processing.
The plain `answer.txt` route unwraps **one** matching pair of single quotes, double
quotes or backticks, then strips surrounding whitespace. The optional validated
`prediction.json` route uses its `answer` string directly; it bypasses this unwrap.
Decisive lines (`data/scaffolds/qa/verify/check.py:132`, `:135`, `:251`):

```python
_QUOTED = re.compile(r"^\s*(?P<q>['\"`])(?P<body>.*)(?P=q)\s*$", re.S)
def unwrap(answer: str) -> str:
    match = _QUOTED.match(answer)
    return match.group("body") if match else answer
    prediction = (str(structured["answer"]) if structured is not None
                  else unwrap(answer).strip())
```

The scorer receives that prediction and the trusted gold
(`data/scaffolds/qa/verify/check.py:254-260`). An absent answer file supplies `""`
(`:194`); unreadable input, invalid structured predictions and scorer exceptions
are unmeasured (`:195-197`, `:203-219`, `:261-265`). Those conditions do not become
ordinary wrong-answer zeros.

**Common aggregation, applicable to each dataset below:** trials of the **same
task** are averaged, then measured task means receive equal weight. Decisive lines:
`scores = [_score_of(res) for res in measured]`,
`score = sum(scores) / len(scores)`, `per_task[tid] = score`
(`eval/run/runner.py:1225-1232`). An unmeasured trial makes the runner return the
task as invalid (`eval/run/runner.py:1145-1151`), and invalid tasks are recorded
separately and skipped (`:1203-1205`). Each of the four declarations is
`"aggregation": "arithmetic_mean", "denominator": "measured_tasks"`
(`data/aggregation_policies.json:20`, `:25`, `:26`, `:27`). The denominator is
read from that declaration (`data/scaffolds/aggregation.py:32-38`) and the mean is
`sum(xs) / denominator if denominator else 0.0`
(`eval/run/metrics.py:43-44`). The curve receives the scores for IDs actually in
`per_task` (`harnessgrad/records.py:116-125`; `driver.py:1218-1219`) and calls this
mean (`harnessgrad/records.py:211`). That helper's empty-input fallback is not evidence of
a measured zero: diagnostic reporting instead returns `None` when no outcome is
measured and separately counts invalid/unmeasured tasks/trials
(`eval/policy/diagnostic_policy.py:125-144`). No actual coverage or mean is measured
in this review.

Registry integration must retain established custody and the bounded first-five
selection record, without treating mixed sides as a ready single-side diagnostic
(`registry/w-sel-manifests.md:7-9`, `:71`, `:123`, `:149`, `:187`). Existing next actions are
at `registry/dataset-blockers.json:625`, `:770`, `:832`, `:876`. Their obsolete
shared-gate clauses must not be copied forward: the operator withdrew
`scorer-offline-closure` and `artifact-only-state-export`, retaining limits rather
than certifying equivalence (`registry/withdrawn-requirements.md:18-22`, `:44-65`).
The adapters here declare command verifiers (`data/scaffolds/qa/base.py:484`;
`data/datasets/spreadsheetbench.py:231-242`). These declarations do not themselves
prove pristine execution. The proposals retain dataset-specific verifier/runtime
obligations from the existing next actions, while preserving the shared
withdrawals and the future pristine opt-in boundary
(`registry/withdrawn-requirements.md:60-67`).

## physicsquestions

### 1. Local metric — FOUND

The target encoding is parsed into quoted accepted spellings: `_TARGET` recognizes
`Can be one of [...]`, matching is against `str(target).strip()`, and `_QUOTED`
extracts items; malformed/empty encodings raise instead of falling back
(`data/datasets/physicsquestions.py:69-87`). `_gold` passes the resulting list as
`accepted` (`:135-137`). After the common input processing above, the prediction is
stringified and whitespace-stripped. Each reference is stringified,
whitespace-stripped, then **all trailing periods** are removed. Case, internal
spacing and units are otherwise exact; there is no numeric tolerance. Decisive
comparison and per-task reward (`data/datasets/physicsquestions.py:100-108`):

```python
    return str(prediction).strip() == str(ground_truth).strip().rstrip(".")
    accepted = list(gold.get("accepted") or [])
    hit = any(exact_str_match(prediction or "", want) for want in accepted)
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
```

The `return` and following assignments quoted here belong to separate functions;
they are the decisive lines, not an executable excerpt. Task aggregation is the
common measured-task arithmetic mean declared at `data/aggregation_policies.json:20`.

### 2. Upstream metric — FOUND, with a processing limit

The retained BIG-bench task header declares `exact_str_match`, `bleu`, and an
explicit `output_regex`
(`registry/w-metriccmp-sources/physics-task-header.txt:11-16`). The pinned revision
is recorded in `registry/w-metriccmp-sources/provenance.json:7`, `:17`. BIG-bench
passes configured `stop_string`/`output_regex` into `model.generate_text`
(`registry/w-metriccmp-sources/bigbench-json-task.py.txt:551-558`, `:633-640`), stores
the returned string as `generative_response` (`:651-652`), and measures it against
the targets (`registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:519-524`).
The metric itself neither strips references nor predictions. Decisive rule and
aggregation (`registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:273-279`):

```python
    for target, response in zip(targets, responses):
        if not isinstance(target, list):
            target = [target]
        if response in target:
            count += 1
    return {"exact_str_match": count / len(targets)}
```

BLEU is a separate metric dispatched at
`registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:498-505`; the local reward
returns only the binary hit (`data/datasets/physicsquestions.py:103-114`).
**UNKNOWN:** the concrete upstream model implementation's application of the regex,
and matched few-shot/generation settings or complete task/subtask score reporting.
Only `json_task.py`'s argument forwarding is established here; no response extractor
is reconstructed from memory. This limit does not prevent comparison of the two
readable metric functions at their response boundary.

### 3. Determination — different-and-why

The exact differing rule is local reference `.strip().rstrip(".")` versus
upstream unchanged membership in the target list (sources above). Synthetic target
list `["12 m."]`, supplied generated response and local answer `"12 m"`:
local prediction is `"12 m"`; local reference becomes `"12 m"`; equality is true,
reward **1**. Upstream tests `"12 m" in ["12 m."]`, which is false; on this
singleton input its `exact_str_match` is **0/1 = 0**. The response itself fits the
task's numeric-unit regex (`registry/w-metriccmp-sources/physics-task-header.txt:16`). This is a proof that the
metric definitions differ, not a claim that this invented target occurs in the
release or that any release score changed. Local input quote handling and upstream
generation processing are additional unaligned contracts, not assumed equivalent.

### 4. Report-separately statement

PhysicsQuestions is reported as local asymmetric normalized accepted-spelling exact match on the ReWOO-vendored table: plain answer-file submissions are unwrapped by one matching quote pair and whitespace-trimmed (structured answer strings bypass that step); the scorer trims the prediction and compares it with any accepted spelling after trimming that reference and removing its trailing periods, returning binary reward and averaging measured task means. This differs from BIG-bench exact_str_match's unchanged target-list membership and does not report BLEU or establish matched BIG-bench generation/processing/aggregation equivalence; source/split/selection and invalid/unmeasured coverage must accompany any result. Sources: data/scaffolds/qa/verify/check.py:132-137, :251-260; data/datasets/physicsquestions.py:100-108; data/aggregation_policies.json:20; eval/run/runner.py:1225-1232; registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:273-279; registry/w-metriccmp-sources/physics-task-header.txt:11-16.

## sportsunderstanding

### 1. Local metric — FOUND

Gold labels are stripped/lowercased and restricted to the two labels
(`data/datasets/sportsunderstanding.py:57`, `:112-116`). After the shared QA
processing, the answer and label are lowercased. Label matching uses **ASCII-letter
negative lookarounds**, not Python Unicode word boundaries: adjacent digits,
underscores or non-ASCII letters do not block a match. Decisive lines
(`data/datasets/sportsunderstanding.py:71-85`):

```python
    pattern = r"(?<![A-Za-z])" + re.escape(str(label).lower()) + r"(?![A-Za-z])"
    return re.search(pattern, str(prediction).lower()) is not None
    return all(substring_choice(prediction, label) for label in LABELS)
    want = str(gold.get("answer", "")).lower()
    both = names_both(prediction or "")
    hit = (not both) and substring_choice(prediction or "", want)
    return {
        "reward": 1.0 if hit else 0.0,
```

These lines span the matching, dual-label and score helpers. A correct label may
appear inside longer prose; naming both labels scores zero, regardless of context
or negation. Task means are aggregated as above
(`data/aggregation_policies.json:25`). The returned metadata's literal
`"metric": "multiple_choice_grade (the correct label named)"`
(`data/datasets/sportsunderstanding.py:87`) does not change the implemented rule.

### 2. Upstream metric — FOUND

The task header selects `multiple_choice_grade`
(`registry/w-metriccmp-sources/sports-task-header.txt:12`, `:18-20`), with source
revision recorded at `registry/w-metriccmp-sources/provenance.json:7`.
The upstream computation uses candidate likelihoods, not the generated answer's
words. Decisive comparison, reward and task aggregation
(`registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:39-43`):

```python
    for sample in samples:
        choice = sample["choice"][argmax(sample["log_prob"])]
        count += sample["target_scores"][choice]
    return count / len(samples)
```

The candidate conditional log probabilities come from `model.cond_log_prob`, with
absolute or logsumexp-normalized values forwarded to that metric
(`registry/w-metriccmp-sources/bigbench-json-task.py.txt:610-631`, `:647-650`).
Ties use deterministic SHA-256-based pseudorandom tie breaking, not text matching
(`registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:554-560`). **UNKNOWN:**
matched upstream prompt/model/candidate-likelihood execution. No missing source is
needed for the metric distinction itself.

### 3. Determination — different-and-why

Synthetic target scores `{"plausible": 1, "implausible": 0}`, local target
`"plausible"`, generated answer `"plausible or implausible"`, ordered candidates
`["plausible", "implausible"]`, candidate log probabilities `[-1, -2]`:
local matching finds both labels, so `both=True`, `hit=False`, reward **0**.
Upstream's unique argmax is index 0, choice `"plausible"`, so target score **1**;
on one sample its aggregate is **1/1 = 1** (rules cited above). This example
explicitly supplies upstream's extra input: a text answer/target pair alone cannot
determine a candidate-likelihood score. The differing decision statistic is label
occurrence plus dual-label rejection versus log-probability argmax. No assumption
that generated text must agree with that argmax is justified by either definition.

### 4. Report-separately statement

SportsUnderstanding is reported as local generated-text label accuracy on the ReWOO-vendored table: after shared QA answer handling, lowercase the text and match the correct label using ASCII-letter boundaries, reject answers containing both plausible and implausible, assign binary reward, and average measured task means. It is a different metric from BIG-bench multiple_choice_grade, which selects the maximal-candidate-log-probability choice and averages its target_scores value; no candidate-likelihood or upstream protocol equivalence is claimed. Report source/split/selection and invalid/unmeasured coverage explicitly. Sources: data/scaffolds/qa/verify/check.py:132-137, :251-260; data/datasets/sportsunderstanding.py:71-85; data/aggregation_policies.json:25; eval/run/runner.py:1225-1232; registry/w-metriccmp-sources/bigbench-task-metrics.py.txt:39-43.

## strategyqa

### 1. Local metric — FOUND

The target is stripped, validated as `Yes` or `No`, and retained as the answer
(`data/datasets/strategyqa.py:60`, `:118-122`). After shared input handling, the
scorer stringifies, lowercases, deletes `string.punctuation` characters, removes
articles `a|an|the` using regex word boundaries, then collapses whitespace by
splitting/joining. It compares the **whole normalized strings**, not first tokens.
Decisive lines (`data/datasets/strategyqa.py:69-93`):

```python
        return re.sub(r"\b(a|an|the)\b", " ", text)
        return " ".join(text.split())
        return "".join(ch for ch in text if ch not in set(string.punctuation))
        return text.lower()
    return white_space_fix(remove_articles(remove_punc(lower(str(s)))))
    return normalize_answer(prediction) == normalize_answer(ground_truth)
    want = str(gold.get("answer", ""))
    hit = exact_match(prediction or "", want)
    return {
        "reward": 1.0 if hit else 0.0,
```

These are separate helper bodies and the score assignment, in source order.
Aggregation is the measured-task arithmetic mean above
(`data/aggregation_policies.json:27`).

### 2. Upstream metric — ReWOO FOUND; original benchmark ABSENT in stated scope

The retained ReWOO evaluator is also byte-equal to the installed evaluator view
(`registry/w-metriccmp-sources/provenance.json:23-31`). It performs:
`return white_space_fix(remove_articles(remove_punc(lower(s))))`, with the same
lowercase, punctuation deletion, article regex and whitespace collapse helpers
(`registry/w-metriccmp-sources/rewoo-Evaluator.py.txt:16-30`). It then uses
`em = (norm_pred == norm_label)` (`:188-190`) and
`result["avg_em"] = np.nanmean(self.eval_data["em"])` (`:174`). This establishes
the string-input comparison and finite EM averaging rules, not the entire runner.
Its `get_metrics` also calls F1 and an LLM accuracy path (`:191-193`), and its
prediction parser is algorithm-dependent: CoT uses
`str(output).split("\n")[-1].replace("Answer:", "")`, whereas IO/React/PWS strip
newline characters (`:195-205`). The local inline score has no such parser
(`data/datasets/strategyqa.py:89-101`).

**ABSENT in enumerated scope:** no original-StrategyQA scorer or original split
definition was identified by the explicit source-path inventory filter (suffix .py, .md or .txt, with basename containing strategy, evaluator, evaluation or metric) over current
`tests/fixtures/{hg_e1_review,hg_wprov,hg_wjc2}`, installed
`/mnt/20t/xubuqiang/datasets/strategyqa`, recovered
`/mnt/20t/xubuqiang/hg-work/siterecover`, and those three fixture subtrees at Git
commits `62d85b1b` and `d04aa512`. The validation transcript contains the exact
Python inventory command, suffix/name filters and all matches. Only the ReWOO
evaluator and local adapter snapshots appear as relevant StrategyQA scorer
sources there. This bounded search does not prove global absence; arbitrary
generic filenames, other package locations and other Git history remain
**UNKNOWN**. No original benchmark metric is reconstructed from adapter comments
or registry prose.

The adapter labels the vendored table `test`
(`data/datasets/strategyqa.py:177-183`, `:197-199`), while the scaffold imposes its
hash division when there is no study split (`data/scaffolds/qa/base.py:123-131`,
`:299-312`). Neither is an authenticated original-StrategyQA split definition.

### 3. Determination — undetermined-because-original-metric-and-split-source-unavailable-in-searched-scope

Conditional result: for the same supplied **string** prediction and label at the
normalization/equality boundary, local EM and ReWOO EM are equivalent. ReWOO's
normalization composition above is identical apart from local `str(s)` coercion;
for strings that coercion is identity. For identical finite per-item EM inputs,
`nanmean` equals arithmetic mean. This is neither original-benchmark equivalence
nor complete answer-processing/execution equivalence.

An additional, strictly bounded **different** subfinding concerns ReWOO CoT
processing. Synthetic raw answer `"Answer: Yes"`, target `"Yes"`: the local
whole-answer normalization yields `"answer yes"` versus `"yes"`, hence **0**;
ReWOO's CoT branch first removes `"Answer:"`, yielding `" Yes"`, normalized to
`"yes"`, hence **1** (`rewoo-Evaluator.py.txt:198-199`, `:188-190`;
`data/datasets/strategyqa.py:66-93`). This does not assert that an actual harness
used CoT or establish the unknown original benchmark rule. No discriminating
example against an unread original scorer is invented.

### 4. Report-separately statement

StrategyQA is reported as local normalized whole-answer exact match on the ReWOO-vendored table: after shared QA input handling, lowercase strings, delete ASCII string.punctuation, remove the articles a/an/the, collapse whitespace, compare the whole strings, and average binary rewards over measured task means. The string comparison matches the retained ReWOO EM definition on identical string inputs, but local answer processing is not ReWOO's algorithm-dependent parser, and the original StrategyQA scorer and split definition were not identified in the enumerated local search scope. Do not label this original-benchmark accuracy or infer original split comparability; F1 and LLM-judge accuracy are excluded. Sources: data/scaffolds/qa/verify/check.py:132-137, :251-260; data/datasets/strategyqa.py:66-101; data/aggregation_policies.json:27; eval/run/runner.py:1225-1232; registry/w-metriccmp-sources/rewoo-Evaluator.py.txt:16-30, :174, :185-205; bounded search command/output in registry/w-metriccmp-validation.txt.

## spreadsheetbench

### 1. Local metric — FOUND

The adapter chooses one init/golden workbook pair per task using deterministic
filename patterns/fallbacks (`data/datasets/spreadsheetbench.py:120-140`,
`:145-169`). It binds `golden.xlsx` and `task.json`, including the task's
`answer_position`, to the command verifier (`:224-257`). The plain answer text is
a **filename**, stripped; its first line is stripped again and an empty value
defaults to `result.xlsx`. A path outside the work directory is unmeasured; an
absent named artifact is measured zero
(`data/datasets/spreadsheetbench/verify/check.py:338-362`). This is separate from
the QA text normalization above.

If formulas are present the result workbook is recalculated through LibreOffice
before comparison; missing LibreOffice/recalculation failure is unmeasured
(`data/datasets/spreadsheetbench/verify/check.py:364-391`). Both workbooks are
opened using `data_only=True`; workbook-load failure raises
(`:174-178`). Values are transformed exactly as follows (`:63-76`):

```python
    if isinstance(v, (int, float)):
        v = round(float(v), 2)
    elif isinstance(v, datetime.time):
        v = str(v)[:-3]
    elif isinstance(v, datetime.datetime):
        v = round(datetime_to_float(v), 0)
    elif isinstance(v, str):
        try:
            v = round(float(v), 2)
        except ValueError:
            pass
```

The date origin is `datetime.datetime(1899, 12, 30)` and serial-day conversion
uses `delta.days + delta.seconds / 86400.0` (`:79-83`). Thus booleans follow the
numeric branch; nonnumeric strings retain case, punctuation and whitespace; the
time rule is literally `str(v)[:-3]`, not a general assertion about fractional
seconds. Empty string and `None` compare equal; otherwise transformed types must
match and values must be equal (`:86-99`):

```python
    if (v1 == "" and v2 is None) or (v1 is None and v2 == ""):
        return True
    if (v1 == "" and v2 == "") or (v1 is None and v2 is None):
        return True
    if type(v1) != type(v2):
        return False
    if v1 == v2:
        return True
```

Every named cell in each comma-separated answer-position component must match;
a missing result sheet is false and the final decision is `all(result_list)`
(`data/datasets/spreadsheetbench/verify/check.py:146-160`, `:180-212`). Fill/font
comparisons are absent from this local comparator (`:146-160`). A completed true
comparison yields reward **1**, false yields **0**, with the range-zero exception
described below; unexpected failures are unmeasured (`:386-401`). Task/trial
aggregation is the common rule (`data/aggregation_policies.json:26`), not averaging
three distinct workbook variants.

### 2. Upstream metric — FOUND

Retained `evaluation/evaluation.py` at the revision in
`registry/w-metriccmp-sources/provenance.json:38` defines the same cell-value
transform/type/equality functions
(`registry/w-metriccmp-sources/spreadsheetbench-evaluation.py.txt:12-47`), named-cell
comparison with fill/font checks commented out (`:123-152`), `data_only=True`
loading (`:155-163`), and `all(result_list)` over ranges (`:169-188`).
Workbook-load failure returns false upstream (`:162-163`); the outer evaluator
also catches other exceptions as false (`:216-220`).

The published implementation's soft/hard definitions are **FOUND**, not inferred
from local comments. Decisive lines
(`registry/w-metriccmp-sources/spreadsheetbench-evaluation.py.txt:212-222`):

```python
        for test_case_idx in range(3):
            gt_path = f"{dataset_path}/spreadsheet/{data['id']}/{test_case_idx + 1}_{data['id']}_answer.xlsx"
            proc_path = f"{dataset_path}/spreadsheet/{data['id']}/{test_case_idx + 1}_{data['id']}_input.xlsx"
            # proc_path = f"{dataset_path}/outputs/{opt.setting}_{opt.model}/{test_case_idx + 1}_{data['id']}_output.xlsx"
            try:
                result, _ = compare_workbooks(gt_path, proc_path, data['instruction_type'], data['answer_position'])
            except:
                result = False
            test_case_results.append(int(result))
        soft_restriction = test_case_results.count(1) / len(test_case_results)
        hard_restriction = 0 if 0 in test_case_results else 1
```

This source writes one record with all three case results and soft/hard values
per task (`:223-232`). **UNKNOWN:** paper manuscript wording, any further
cross-task reporting implementation beyond this per-task JSON, and matched
upstream recalculation/execution configuration. The three-case soft/hard rule
itself is directly readable in upstream scorer source, so no paper prose is
needed to distinguish it from one-variant reward.

### 3. Determination — different-and-why

For identical well-formed named ranges and already cached cell values, the
local/upstream cell predicates have matching branches (sources above). Complete
metric equivalence fails at **variant aggregation** and **failure classification**.
Synthetic well-formed singleton range `Sheet1!A1`, with target value `1` in each
of three variants and submitted cached values `[1, 0, 0]`: both cell predicates
produce `[True, False, False]` after float conversion/rounding. The harness's
single supplied first-variant reward is **1**. Upstream produces
`test_case_results=[1,0,0]`, soft **1/3**, hard **0**. With one task and one trial,
the local measured-task mean remains **1**. This is a definition-based example,
not a claim about real variant results
(`check.py:63-99`, `:393-401`; upstream `:212-228`; common aggregation above).

Additionally, a synthetic non-workbook submission triggers a local formula/load
measurement failure and produces no score (`check.py:364-391`), while upstream
load/caller guards record **0** (`spreadsheetbench-evaluation.py.txt:159-163`,
`:216-220`). It would be incorrect to force such unmeasured local trials into
the official-range-zero policy merely to make denominators appear equal.

### Official-range-zero compatibility policy and affected task

The exact local policy is an early check **only for colon ranges**. After
splitting a comma component, it strips whitespace/outer single quotes from the
range part and calls the inherited `parse_cell_range`. A **ValueError** returns
`False` with the literal prefix `official-equivalent measured zero:`; single-cell
positions bypass the parser and unexpected exceptions propagate
(`data/datasets/spreadsheetbench/verify/check.py:184-199`):

```python
        _cell = sheet_cell_range.split("!")[-1].strip().strip("'")
        if ":" in _cell:
            try:
                _ = parse_cell_range(_cell)
            except ValueError as exc:
                return False, (
                    f"official-equivalent measured zero: the task's answer_position "
```

The false result is marked `measured=True`, reward/score **0**, preserving that
message in the evidence (`check.py:393-400`). This is compatibility with the
official caller's exception-to-zero behavior, not a completed cell comparison
or proof of semantic gradability. The upstream parser calls `int(start_row)` and
`int(end_row)` (`spreadsheetbench-evaluation.py.txt:93-110`); `A:G` supplies empty
row strings, so `int("")` raises. Its caller catches that error as false and
appends `int(result)` (`:216-220`).

**FOUND, prior evidence rather than a new audit:** the retained audit enumerates
400 installed metadata rows and records exactly one affected task, **283-32**,
with two failing `A:G` components and answer position
`Sheet3'!A:G,'Sheet4'!A:G`
(`registry/w-metriccmp-sources/spreadsheetbench-prior-audit.txt:10-19`). It also
records hash-eval false (`:20-21`), consistent with the adapter's hash split
definition (`data/datasets/spreadsheetbench.py:355-357`, `:370-375`). That is not
a new pool or held-out-answer read. The count applies only to the prior audit's
installed verified_400 metadata scope; other releases and any changed metadata
are **UNKNOWN**. If selected and the verifier reaches this range check after
successful formula/load prerequisites, 283-32 contributes a measured zero to the
denominator. It is not excluded or silently repaired; infrastructure failures
earlier in the path remain unmeasured. No new gradability, selection, score or
coverage receipt is established here.

### 4. Report-separately statement

SpreadsheetBench is reported as local one-released-variant named-cell value accuracy under the official-range-zero compatibility policy: use the adapter-selected workbook pair, recalculate submitted formulas when needed, compare cached values after the specified numeric/date/time/string transformations and empty-value/type checks, assign binary reward, and average measured task means. Colon-range parser ValueError yields an explicitly identified measured zero, including the previously audited 283-32 A:G case when selected and reached; this is not a completed comparison or authenticated semantic gradability. Infrastructure failures remain invalid/unmeasured and receive no published score. This one-variant metric is different from the upstream three-variant soft_restriction mean and hard_restriction all-pass rule; report eligible/selected/task/trial/invalid/unmeasured coverage and retain that denominator distinction. Sources: data/datasets/spreadsheetbench.py:120-140; data/datasets/spreadsheetbench/verify/check.py:63-99, :174-212, :364-401; data/aggregation_policies.json:26; eval/run/runner.py:1225-1232; registry/w-metriccmp-sources/spreadsheetbench-evaluation.py.txt:212-228; registry/w-metriccmp-sources/spreadsheetbench-prior-audit.txt:10-19.
