# External method repair brief

Work in the existing isolated parent worktree on codex/fix-onboarding-first-four-20261103. Other workers edit disjoint files; do not run git write commands or modify their files.

Own only methods/protocol.py, tools/validate_method.py, docs/bring-your-own-method.md, and a narrowly scoped new validator test file. Parent adds the README link. Do not change harnessgrad/methods.py or driver.py.

First write and run failing behavioral regressions for (1) a valid method directory outside the repository, (2) a method returning JSON but omitting trajectory, and (3) an invalid/empty trajectory. Fix path display and align trajectory validation with the real unified engine, including legitimate no-change responses. Read the engine before deciding what is valid.

Correct the protocol evidence directory to base_harness/_harnessgrad. Document an executable minimal external-method walkthrough using existing noop/echo_base helpers where appropriate; avoid introducing a second method abstraction or redundant examples. Follow existing Python style and keep platform dependencies standard-library only.

No real model calls, paid endpoints, benchmark pilots, dependency installation, unrelated refactors, or secret values in output. Test roots must be outside source. For Linux tests, WSL lightrsi-ubuntu has python3 and bwrap; pytest can be imported from /mnt/c/Users/Tony Yu/AppData/Local/Programs/Python/Python310/Lib/site-packages with plugin autoload and bytecode disabled. The supported source path is /mnt/c/Users/Tony Yu/.codex/worktrees/hg-latest-review/HarnessGrad. Avoid full driver runs while other workers edit source.

Write full results, failing/passing commands, changed files, and any concerns to reports/onboarding-method-20261103-report.md. Return a short status only. Do not spawn subagents. Parent reviews and commits your scoped changes.
