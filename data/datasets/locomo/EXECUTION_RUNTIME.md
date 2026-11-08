LoCoMo keeps Harbor's scoring rules and source provenance in `specs/source.json`.
The recorded upstream provenance identifies its original NLTK 3.10.3 dependency;
the verifier execution runtime instead pins NLTK 3.10.4 and its full dependency
closure in `requirements.lock`. It does not download NLTK corpora.

Prepare and check this separate artifact with `python tools/prepare_dataset.py
--dataset locomo --runtime execution` and the same command plus `--check`.
The platform loader and report aggregation do not require NLTK. A verifier needs
the selected execution artifact; if it is unavailable the run is not measured.
