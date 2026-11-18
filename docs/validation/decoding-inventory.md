# Text subprocess decoding inventory

This is a source-syntax review of the scope in `tests/quality/test_harness_decoding.py`, not evidence that every subprocess succeeds or that malformed output can never interrupt a run. The scan covers live Python under `base_harness`, `eval`, `data`, `harnessgrad` and `tools`, plus the embedded actor in `tools/diagnose_appworld.py`. It excludes `__pycache__`, generated `data/candidates` and `data/**/verify/check.py` as the test specifies. At this check there were no untracked Python files in that scan scope.

The original main revision `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6`, LightRSI fix revision `a0f837fdf1abd85664be0cb99ea2ffe431e406d8`, and the refactor after `962c15bf` were compared from source archives using the same scanner:

| Source area | Original main | LightRSI fix | Refactor |
| --- | ---: | ---: | ---: |
| `base_harness` | 12 | 12 | 12 |
| `eval` | 15 | 15 | 15 |
| `data` | 56 | 56 | 56 |
| `harnessgrad` | 10 | 10 | 14 |
| `tools` | 45 | 45 | 41 |
| **Total** | **138** | **138** | **138** |

The original and fix trees have the same call expressions after excluding the decoding keyword. Between the fix and refactor trees, all but one expression also match under that normalization; the remaining expression is the dataset-runtime H0 preflight, whose repository-root parent index changed to match the module's new directory. Its resulting working directory remains the repository root. Three calls moved from `tools/improver.py` to `harnessgrad/improvers/resolver.py`; one moved from `tools/validate_harness.py` to `harnessgrad/execution/harness_validation.py`. Other relocated calls stay within their original source area. The former expected inventory of 118 was already stale in the original main tree.

The reviewed refactor inventory comprises 105 `replace` calls, two `surrogateescape` calls, 18 explicit `strict` identity/configuration calls with individual reasons, and 13 implicit-strict exceptions with individual reasons. The latter include source-bound scorer/native helpers, whose malformed output fails measurement or identity admission; they are not a claim that malformed output is readable. Synthetic controls cover the specifically reviewed scoring, preparation and readiness boundaries. Do not update the count or add a broad exception merely to accommodate future changes: review the changed calls, preserve scorer custody and keep failure classification explicit.
