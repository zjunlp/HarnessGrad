# README visual refresh review

## Preservation and inventory

- The title and citation retain their wording with requested capitalization; badges, every original paragraph and all section anchors remain. The complete harness/method/benchmark table is unchanged, now in a Markdown-enabled `<details>` block. Its existing test continues to require every declared adapter and the exact declared count. The historical τ² and removal discussion is unchanged in a separate dated `<details>` block.
- All existing figures, contributors block, growth curves, screenshots, recipes, SHA examples, documentation, citation and links remain in the README. The new diagram is an additional original, static, accessible SVG.
- The registry currently has 56 declared adapter modules (`declared_benchmarks()`), not 100+. The generated summary says 56 and notes prerequisites. `tools/contributors.py --write` updates it; `--check` detects drift. At 100 or more it conservatively says 100+. Tests cover 99, 100 and 123 and stale detection.
- The original inventory table is intentionally not generated, preserving its runnable/refusal claims verbatim. When adapters change, maintainers must update the table and re-evaluate those claims; the existing row guard catches missing names/count. The dated historical paragraph describes the earlier state, not a promise about future adapters.
- Featured integrations are examples of recently integrated or updated work, not a ranking. GUI and embodied expansion are identified as future plans; the existing workbench remains documented.

## Checks

- `python3 tools/contributors.py --check`, `--write`, `--check`: passed; existing generated contributor outputs unchanged.
- `python3 -m pytest -q tests/quality/test_contributors.py tests/quality/test_documentation_links.py tests/quality/test_documentation_commands.py --basetemp=/tmp/harnessgrad-pytest-readme-refresh`: 29 passed.
- `python3 driver.py --help` and `HG_AGENT_BACKEND=mock python3 driver.py --config experiments/quickstart.json --check-only`: passed; no model or GPU used.
- SVG parsed with Python's XML parser; no external resources. `git diff --check` passed.
- Initial test invocation named nonexistent `test_commands.py`; corrected to `test_documentation_commands.py`. `python3 -m harnessgrad --help` is unsupported (no package `__main__`); verified the documented driver CLI instead.
