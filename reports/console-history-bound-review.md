# Console history range review

Base: `ee60e3fbaa61aa84d3dc1d2540033524a65144a9` (origin/main). Scope: Console history API, its sole static caller, documentation and synthetic tests.

## Finding and change

The history handler previously accepted a nine-digit end round and the helper iterated and allocated one row per requested round, performing filesystem operations even for missing rounds. A loopback caller could exhaust resources. Both the HTTP handler and helper now validate an ordered, nonnegative, inclusive interval of at most 1000 requested rounds, with at most nine digits per endpoint, before run lookup or iteration. HTTP errors are 400 and helper errors contain no partial result. Valid sparse results retain empty rows; patch truncation and other existing row semantics are unchanged. Requests `0..999` and `1..1000` pass, `0..1000` fails. For the special `0..0` baseline, the previous fabricated round 1 is replaced with an empty list, documented in the API README. The editing screen reports API errors rather than silently treating them as empty history; callers can request long histories in segments.

## Verification

- `TMPDIR=$(mktemp -d /tmp/hg-history-test.XXXXXX) python -m pytest -q -p no:cacheprovider tests/quality/test_console_history_bound.py tests/quality/test_console_http_security.py` — 98 passed in 51.41s (before final 0..0 adjustment).
- `TMPDIR=$(mktemp -d /tmp/hg-history-test.XXXXXX) python -m pytest -q -p no:cacheprovider tests/quality/test_console_history_bound.py tests/quality/test_console.py` — 21 passed in 28.69s (after 0..0 adjustment).
- Tests use temporary synthetic patches and a temporary loopback HTTP server; invalid helper calls monkeypatch file reads and stats to fail, and confirm no run lookup or rows. No real models, benchmarks, external network, or production services used. Browser UI was not exercised; tests are focused, not full-system validation.
