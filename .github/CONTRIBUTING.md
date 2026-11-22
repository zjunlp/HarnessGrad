# Contributing to HarnessGrad

Thank you for helping make agent Harnesses easier to build, study and improve.
You can contribute a Harness, an RSI method, a dataset adapter, documentation,
a bug fix, or an improvement to the user experience.

## 🧭 Find a place to start

- Browse [open issues](https://github.com/zjunlp/HarnessGrad/issues), including
  `good first issue` and `help wanted` labels.
- Read the [documentation map](../docs/README.md) and the
  [first-use guide](../docs/getting-started.md).
- For integrations, see [bring your own Harness](../docs/bring-your-own-harness.md),
  [bring your own method](../docs/bring-your-own-method.md), or
  [dataset onboarding](../docs/dataset-onboarding.md).
- For security concerns, use the [security policy](SECURITY.md).

## 🛠️ Make a change

1. Open an issue for a bug or a larger idea so its scope can be discussed.
2. Create a branch in your fork or an isolated worktree; keep existing work and
   experiment records intact.
3. Follow the [installation instructions](../docs/getting-started.md). Read the
   relevant interface and tests before changing runtime behavior.
4. Add or update focused tests when behavior changes. Use synthetic data and
   offline controls by default; never include credentials or private evaluation
   inputs in issues, patches, logs, screenshots or pull requests.
5. Open a pull request and describe the problem, the change, and what you checked.
   If a check could not be run, say so and explain why.

See the [test guide](../docs/test-suite.md) for test selection and environment
requirements. The Quality workflow runs automatically on pull requests: its
gating checks and advisory report have different scopes. A green check does not
by itself mean the full suite, real models or all benchmarks were run.

## 🔬 Experiments and evidence

Keep measured, failed and unmeasured results distinct. Do not alter historical
receipts, pinned scorer artifacts or private answers as part of an unrelated
change. Dataset sources, runtime identities and scoring protocols can affect
whether runs are comparable; document changes that affect them. See the
[measurement policy](../docs/refusal-policy.md) and
[interface contract](../INTERFACE.md).

## 🤝 Community

Please follow our [Code of Conduct](CODE_OF_CONDUCT.md). Contributions and
questions from first-time contributors are welcome.

Maintainers can use the [release and repository guide](RELEASING.md) when
preparing labels or a release.
