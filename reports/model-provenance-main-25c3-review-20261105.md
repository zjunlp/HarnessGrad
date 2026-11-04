# Model source provenance: main 25c3d8a review candidate

This independent candidate applies the model-source producer and its synthetic tests on
main `25c3d8ae38a77141492e790325184d6abcbc2c3c`. The older report describes its original
base and earlier observations; it does not describe a fresh weight hash or process check
on this snapshot.

Validation: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
--basetemp=/tmp/hg-model-source-25c3-tests-20261105
 tests/quality/test_local_model_provenance.py tests/quality/test_codex_runtime_paths.py
 tests/quality/test_console_launch_models.py` yielded **71 passed, 1 skipped**. The tests
use synthetic sources; no deployed model weights or GPU contents were read. `git diff
--check 25c3d8a..HEAD` passed before this report.

The producer records source-file hashes and process declarations, not GPU-resident
bytes, endpoint behavior, executable authenticity, an atomic filesystem snapshot, or
historical model identities. Its hard-coded two-port, model alias and resource settings
limit it to the specific declared deployment. Use on another deployment needs an
independent change and review. This candidate authorizes neither a merge nor a run.
