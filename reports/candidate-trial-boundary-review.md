# Candidate trial boundary review

Base: a3f3dec. Scope: Console candidate trial only, documentation and offline tests.

## Lifecycle and safety

The ordinary driver creates the run directory with `exist_ok=True` (driver.py:1578–1583), and resolves and recursively removes an existing `<work-root>/<run-id>/workspace` before setup (1886–1918). Merely checking existence is therefore unsafe. The trial endpoint now exclusively creates the run and work ID directories, using directory-fd operations and no-follow traversal. The workspace does not exist when handed to the unmodified driver. Competing cooperating requests cannot both acquire either identity; a work collision removes only the newly created, still-empty run reservation if its inode still matches. After handoff nothing is removed by the endpoint, including after timeout or failure. The driver itself remains unchanged.

This is not protection against same-UID hostile processes or a malicious administrator swapping paths after validation. The deployment administrator must dedicate and protect the configured existing root and ancestors from untrusted writers; direct driver CLI runs and other noncooperating writers must not use either namespace. The driver consumes pathname arguments, not directory fds. Do not deploy if that trust condition cannot be met. The endpoint rejects absent, relative, symlink-containing, platform-contained or platform-ancestor storage roots and does not create the configured root. Compatibility: explicit HG_CONSOLE_TRIAL_RUNS is now required; repeated IDs conflict, and `work`, `.`, `..` are reserved.

The unchanged command uses `--sampling all --rounds 0`; it may evaluate the full selected dataset, not one task, with associated cost. Launch OSError returns 503 and preserves both reservations.

## Verification

`python -m pytest -q tests/quality/test_candidate_trial_boundary.py --basetemp=/tmp/hg-trial-test-candidate-19432`: 11 passed. Fixtures replace the subprocess, use temporary storage and synchronize two actual local processes; no driver, benchmark, model or network is invoked. This is not full-system validation.
