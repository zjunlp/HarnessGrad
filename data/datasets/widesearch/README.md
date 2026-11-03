# WideSearch native evaluation and explicit trusted runtime

This adapter uses FlashRSI's fixed native WideSearch evaluator projection at
`e9c7667945f959d62da27204649a82f94d1b6705`, upstream evaluator revision
`9825ba7b140b71d81b364793f86dabe4cfed6749`, data revision
`6531a7e5b497d44c8912407e0cb3dc95bd98cc09`.

Preparation preserves native query/evaluation dictionaries and private gold CSV
bytes. Public tasks contain the query, not the gold CSV or evaluation answers.
The private `wide_query` uses `harnessgrad.widesearch-query.csv.v1`, strict base64
of the original CSV, and the native row's `_gold_sha256`. The verifier checks that
binding and repeats native `read_csv`, column normalization and required-column
selection, preserving native float parsing, NaNs and integer dtypes. Legacy cases
containing a DataFrame JSON `answer` must be explicitly prepared again; they are
rejected rather than silently accepted with rounded reference values.
Markdown table extraction, key alignment, native per-column preprocessing,
semantic column judging and all seven native table metrics remain native. The
headline `score` is the upstream binary table success value, not a reweighted
P/R/F1. Native alias merging can produce secondary rates above one; the adapter
does not clip them or change the binary success rule.

The original evaluator swallows exceptions and malformed judge output into zeros.
HG preserves completed wrong-answer zeros but marks provider failures and malformed
native alignment/row-score JSON as unmeasured. This is an explicit completion guard,
not a substitute grader. No actor-supplied correct event can grade the task.

## Dependencies and runtime configuration

The trusted verifier needs pandas, numpy, dateparser, loguru, datasets and their
dependencies. Default readiness checks the current platform interpreter. To use a
separate already provisioned interpreter, configure both host-only fields:

```bash
export HG_JUDGE_WIDESEARCH_PYTHON=/absolute/scorer-venv/bin/python
export HG_JUDGE_WIDESEARCH_RUNTIME_PATHS='["/absolute/scorer-venv", "/absolute/base-python-prefix"]'
```

The selected interpreter is preflighted with `-I` and imports the actual native
dependencies. Its absolute executable and read-only directories populate the
existing `VERIFY.runtime_paths` contract. Both executable and symlink target must
remain inside the declared runtime/system directories. Hidden `/tmp`, the platform
checkout, linked directories and actor task storage are not admitted. These
`HG_JUDGE_` settings and judge credentials are filtered from actor environment.
No dependency is installed or fetched during import, load or scoring.

Preparation itself also needs native data-reading dependencies. The selected
scorer runtime does not pretend to supply them to an unrelated preparation process.

The fixed judge model remains explicit in the prepared manifest and verifier-only
broker. A local/custom judge is a custom measurement, not proof of the publisher's
original hosted judge identity or public-web task completion.

## Instrument controls

`tests/quality/test_flashrsi_widesearch.py` constructs a native query and a private
one-row CSV as authored protocol controls. It runs the real vendored evaluator and
HG sandbox/verifier broker against localhost scripted HTTP responses: positive
one, completed wrong-answer zero, provider HTTP failure unmeasured, malformed
judge JSON unmeasured, and gold/key isolation. It performs no public web research,
uses no paid model, and supplies no benchmark capability or H0 authorization.
Additional differential controls compare all metrics and exact DataFrame dtypes
against direct native loading for 17-digit floats, tiny floats, NaNs and large
integers; they also check private CSV/case hash binding and a single explicit fetch.
