# AIME

The adapter uses GAIR-NLP/AIME-Preview at commit
`7011a9eae7c4b78071f27b6f77f618d65608cfb0`. The 60 problems include 30 from 2024
and 15 each from 2025 I and II.
The source archive has SHA-256
`1cb676694f93ca7cfbcc4f160ace9176262f4822fcfa1acecd47ebfd9d6863c9`.

## Setup

Use the standard [HarnessGrad setup](../../../docs/getting-started.md) with Python 3.12 or
later. Set `HG_DATA_ROOT` for shared storage or `HG_DATA_AIME` for an AIME-specific
directory. The adapter uses the platform's native `files` environment and the Python
standard library. Dataset scoring requires no container, dataset-specific Python
packages, or model judge. Your chosen harness has its own runtime and model requirements.

Run these commands from the repository root.

```sh
python3 tools/datasets.py --fetch --dataset aime
python3 tools/datasets.py --check --dataset aime
```

## Task selection and scoring

| Run option | Task pool |
| --- | --- |
| `--split declared` (the CLI default) | All 60 tasks, with no train/eval separation |
| `--split official --side eval` | All 60 tasks as evaluation tasks |
| `--split default` | HarnessGrad's deterministic hash split, with 37 train and 23 eval tasks |

Use `--split official --side eval` to evaluate the full pool. Choose a held-out split
before training a method if you want to measure generalization. Record the split and the
number of tasks scored when reporting results.

The agent receives the problem and submits an answer through `qa submit`. The verifier
receives the reference answer separately after the agent exits. It strips surrounding
whitespace from the submission and compares it to the released answer as an exact string.
A missing submission or a mismatch receives zero.

## Validation

```sh
python3 data/scaffolds/qa/control.py --dataset aime --oracle
python3 data/scaffolds/qa/control.py --dataset aime --noop
python3 -m pytest tests/quality/test_aime_official_scorer.py
```

The oracle and no-op controls check one task by default. They should score 1 and 0,
respectively. Add `--platform` to each control command to exercise the platform's native
sandbox and verifier. The differential test compares the local scorer with the pinned
Harbor scorer on test cases. The [original port review](../../../docs/dataset-reviews/aime.md)
records the controls and source checks. The available evidence covers scorer behavior
and individual controls. A side-by-side benchmark run against Harbor has yet to be recorded.
