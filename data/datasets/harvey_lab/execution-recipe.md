# Harvey worker execution recipe v1 (2026-11-07)

`execution-requirements.in` reproduces the dependency declarations in the release
wheel METADATA, plus upstream Anthropic compatibility pin 0.88.0 and the fixed
HG source projection's filelock/huggingface-hub imports. The exact lock is a NEW
resolution, not an upstream or historical lock. Resolved using uv 0.10.11
`uv pip compile --python-version 3.12 --no-header ...`; public index used during
validation: https://pypi.tuna.tsinghua.edu.cn/simple (default PyPI TLS failed).

Explicit preparation only:

```sh
python tools/prepare_dataset.py --dataset harvey_lab --runtime execution
```

Alternatively call `harnessgrad.dataset_runtime.prepare_execution('harvey_lab')`.
Set `HG_DATASET_RUNTIME_ROOT` to an external owned artifact store when needed.
Checks and scoring never install packages. `HG_JUDGE_HARVEY_PYTHON` no longer
selects an interpreter; the managed execution artifact is mandatory.

Acquire the original release wheel from
https://github.com/harveyai/harvey-labs/releases/download/v1.2.0/lab_core-1.2.0-py3-none-any.whl
(revision f93ae2ac2831e65892fea83094cfb50149bc2e59), and set
`HG_JUDGE_HARVEY_WHEEL` to its absolute regular path. Required SHA256:
`5bfd70664a99c16a97a35bf61a3845a9890477168d11978d58864f986c9f53ba`.
This source is deliberately separate from the index dependency lock: an index
package named lab-core is not a substitute. Each worker hashes and snapshots the
whole wheel, then imports its original source and distribution metadata. The
existing upstream evaluator inventory check remains unchanged. Retain its MIT
license in the source artifact. Pandoc >=3.5 remains an explicit external tool;
provider credentials/endpoints remain trusted host settings described in README.

Validation: independently prepared a new managed artifact on Linux x86_64 Python
3.12.4; actual `harvey_worker.py --probe` returned lab_core=1.2.0,
anthropic=0.88.0, openai=3.46.0, exit 0. Artifact ID:
`da610ceb2a7edb5fe117ff642ed5af201f9265c2e9ed17bab041f8f58b64c924`.
Wheel bytes were copied from an existing local artifact only after authenticating
against the repository's fixed release digest; no claim of new download custody.
No judge/model requests, legal-task run or document conversion were performed.
No benchmark capability, paid-provider behavior, or known cost is established.

Follow-up validation: two consecutive actual worker probes using `-I -B`, each
followed by `admit_binding` of the same artifact, passed without rebuilding or
changing the dependency inventory. Public verdicts expose runtime/module identity
commitments only; exact artifact bindings and host executable paths stay internal.
Private case commitments remain owned by the central verification-context layer.
