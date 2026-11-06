# Public documentation portability review

- The independent-review page now labels its original narrowed GAIA requirement as historical and links to the later withdrawal. Policy refusal and unauthenticated frozen receipts remain unchanged; no measurements or receipts were changed.
- The submission API introduction is neutral contract prose, and its failed-state error reference points to the top-level `error.code` in §5.3.
- Reviewed `docs/architecture.md` against the present tree, including the scaffold registry, checkpoint implementation and absence of a Mode B loop module; its current map already reflects these, so it was not edited.
- Audited the requested split, registry discovery, AIME, transfer, test lane, filesystem quota and console pages. Host-specific historical paths remain as labeled evidence; live guidance uses user-chosen locations or clearly identified examples. Data README citations exposed by the expanded link scan distinguish removed historical controls and upstream source paths from current repository files.
- Link checks cover tracked `data/` Markdown and tracked repository source targets, with synthetic regression coverage and narrow exclusions for explicitly external or historical material. This is an offline static check, not validation of remote links, datasets, historical receipts, or runtime readiness.

## Validation

- `python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-doc-portability-tests tests/quality/test_documentation_links.py tests/quality/test_documentation_commands.py` — **24 passed** in 0.31s; no skips reported.
- `git diff --check` — passed.

These focused static tests do not execute documented commands, access the network, download datasets, or validate the full system.
