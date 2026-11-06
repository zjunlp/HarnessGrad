# Workbench selection boundary review

Base: ee60e3fbaa61aa84d3dc1d2540033524a65144a9; isolated branch `codex/public-cleanup-after-rewrite`. Offline synthetic fixtures only; no models, experiments or benchmarks.

## Finding and change

`POST /api/workbench/edit` previously read the selection before the edit function checked the name. The name regex admits `.` and `..`; absolute names override the base, and path operations followed symlinks. The selection could therefore disclose another candidate or host file to the assistant.

The route now rejects invalid candidate names with `404 candidate-not-found` before selection reads or assistant invocation. `workbench_edit` retains its own check and additionally rejects a candidate directory symlink. Selection uses descriptor-relative directory opens with `O_NOFOLLOW` and checks a regular non-symlink manifest and selection file. Each candidate/path component is opened relative to the preceding descriptor. Unsafe, missing, unsupported or oversized selections yield `400 bad-selection`; error details do not include file contents. Selected files are limited to 1 MiB and requested ranges to 10,000 lines; decoding remains UTF-8 with replacement and Python splitlines semantics. Platforms without the required primitives fail closed. `CANDIDATES` is trusted configuration, including its ancestors; its final component cannot be a symlink for selection reads.

## Scope and remaining risks

This secures the selection read against symlink substitution during path traversal on systems providing the tested primitives, not the whole edit pipeline. `workbench_edit` still uses path-based validation and `copytree`/snapshots, which follow nested symlinks and are subject to concurrent mutation; even the candidate directory symlink check is not race-proof. Do not treat edit execution as a filesystem sandbox or use untrusted writable candidate trees without further isolation. Hard links, bind mounts and attacker-controlled trusted base ancestors are outside this guarantee. Several other candidate APIs still use the permissive shared regex and require separate security review, particularly save/source and trial. This patch intentionally does not assert those APIs are safe.

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-selection-pytest tests/quality/test_workbench_selection_boundary.py tests/quality/test_candidate_materialisation.py`: **17 passed**.
- `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-selection-console-tests tests/quality/test_console.py tests/quality/test_console_http_security.py tests/quality/test_console_snapshot_rounds.py tests/quality/test_console_launch_preflight.py tests/quality/test_console_portable_paths.py tests/quality/test_console_url_redaction.py tests/quality/test_console_trace_json.py`: **141 passed**.

Synthetic tests cover invalid candidate names, traversal, absolute selection, nested/leaf/candidate symlinks, symlink to named sibling, bounded reads, unsupported primitives and Unicode/invalid UTF-8. The assistant is mocked and rejected requests are checked for no assistant call and no secret in the response. These tests do not establish race freedom for unrelated paths, full system behavior, or security against hostile mounts/hard links.
