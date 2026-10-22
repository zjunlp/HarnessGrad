# Jericho validation limits

The production sandbox requires a harness work root outside the platform tree.
The authorized filesystem scope is only `/mnt/20t/xubuqiang/Study/HarnessGrad-evidence-archive/codex/109`, so a production
Jericho platform control cannot be staged in this session. The blocker test pins
the refusal; the sandbox must not be weakened to make the control run here.

`test_curve_points_carry_the_verifier_kind_and_not_its_arguments` also hits this
constraint when pytest temporary storage is kept inside the authorized worktree.
The production sandbox verifier cases have the same staging requirement and
were excluded from the passing focused regression command.

Released game-suite and oracle tests were not run: they select evaluation games
and replay released walkthroughs. Validation uses synthetic fixtures and a
code-only installed-package probe, without loading the release game table, ROMs,
or calling a model. Running production game controls requires a separately
authorized game and an external staging directory.
