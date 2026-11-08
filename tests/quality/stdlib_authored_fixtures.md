# Offline BFCL / 2Wiki test fixtures

`test_bfcl_v3_dataset.py` writes authored question, answer and irrelevance JSONL
files and runs the adapter's real loader. Its small count pins exist only in its
private test module. These controls verify the name-only proxy, question/answer
separation and schema handling; they do not prove the identity of released data.

`test_twowikimultihopqa_official_scorer.py` uses authored dev JSON plus alias JSONL
for loader, privacy, normalization, alias and native sandbox controls. Imports use
private module instances rooted outside ambient dataset storage. The default tests
also pin the reviewed transcribed scorer region. Historical release measurements
remain unchanged and require explicit selection:

- `HG_TEST_TWOWIKI_DATA_ROOT=/existing/2wiki/root` enables original-source digest /
  transcription checks, the 500-row upstream differential, historical normalization
  and alias counts, the complete dev-pool control, and the independent witness check.
  The source archive, evaluator, dev JSON, alias JSONL and witness are read only as
  needed. Present dev/alias/evaluator bytes must match production digests. The
  original script also needs an existing `ujson`; the witness needs existing Arrow.
- `HG_TEST_BFCL_DATA_ROOT=/existing/bfcl/root` enables the released-pool loader check.

No test fetches artifacts or installs dependencies. Unset opt-ins and missing
explicit artifacts report a skip naming the missing input. Existing malformed
payloads and mismatched digests fail. Ordinary `HG_DATA_ROOT` or server settings do
not enable these integrations.

Run offline controls with an external pytest scratch directory:

```sh
python -m pytest tests/quality/test_bfcl_v3_dataset.py tests/quality/test_twowikimultihopqa_official_scorer.py --basetemp=/tmp/hg-stdlib-old-controls -q -rs
```

The separate `test_stdlib_adapter_acceptance.py` additionally runs cold empty-venv
registry-selected sandbox controls for these and the other stdlib adapters.
