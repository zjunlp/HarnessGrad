# Managed native component runtime

Prepare explicitly with `python -m tools.prepare_dataset --dataset workbench --runtime execution`.
Ordinary loading, setup, service admission and scoring never install packages.
Missing or changed artifacts fail closed. Platform pandas/numpy installations and
legacy `HG_JUDGE_WIDESEARCH_*` interpreter overrides do not satisfy readiness.

This is a newly resolved isolation recipe, not a recovered historical lock.
`requirements.in` records the selected component's direct import requirements;
`requirements.lock` pins the complete Python 3.12 dependency closure resolved on
2026-11-07 using uv and https://pypi.tuna.tsinghua.edu.cn/simple (TLS verification
enabled). Native algorithms and the source pins in
`data/scaffolds/native_research/PROVENANCE.json` and each vendor `upstream.json`
are unchanged. This recipe does not establish scoring equivalence across package
versions or constitute a benchmark/model measurement.

Dependencies follow native WorkBench tool/evaluator imports. The acquired source pyproject at revision `49c7dfd00c03d384ec59ea57374f50b766aa5613` declares `numpy>=1.26,<2`, `pandas>=2.1`, Python >=3.12. Its local pyproject SHA256 is `7ec47461f4f3ee8e4739a251f9d033408641800f0334e01d71a71e46b25ef18d`; this file is supporting local acquisition evidence, not a newly authenticated release. Inference, visualization and model-client extras are omitted from this native tool/outcome component. Both relay service and verifier bind this exact artifact; the native session inherits that admitted interpreter.
