# Candidate file read security review

The candidate file GET uses the same descriptor-relative reader as workbench selections. On Linux it opens the configured candidate root, candidate directory, each intermediate directory and the leaf without following symlinks; the leaf must be a regular file. Names `.` and `..`, malformed names, absolute paths, parent traversal, NUL and empty paths are rejected. Unsupported platforms or missing no-follow/descriptor-relative capabilities fail closed. Errors are typed and sanitized; filesystem exception text and request paths are not echoed. Existing HTTP response conventions remain unchanged.

Successful responses retain candidate, normalized path, UTF-8 replacement-decoded text (including universal newline translation), line count and byte size. Unlike the formerly unbounded endpoint, files over 2 MiB now return `file-too-large`; they are not truncated. Selection retains its 1 MiB cap and requires a regular no-follow manifest.

This is not a filesystem sandbox: ancestors of the configured root are trusted; hard links are not rejected; and concurrent mutation by the same UID can change file contents. These constraints also apply to the shared selection reader. Other endpoints, including archive and write operations, are outside this change.

Offline synthetic validation: `TMPDIR=/tmp python -m pytest -q -p no:cacheprovider tests/quality/test_candidate_file_boundary.py tests/quality/test_workbench_selection_boundary.py tests/quality/test_native_workbench.py` — 37 passed. No model or network calls were used. This focused test run is not full-system validation.
