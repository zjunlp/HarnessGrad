# Registry discovery documentation correction

The regression description now refers to the reviewed literal catalog rather than its obsolete 28-name size. Historical validation receipts remain unchanged.

Offline validation from the worktree, with bytecode and pytest plugin autoload disabled:

```text
python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-registry-current-doc-tests tests/quality/test_documentation_links.py tests/quality/test_registry_discovery_order.py
26 passed in 1.24s
```

This is focused validation, not a full suite; no models or benchmarks were run.
