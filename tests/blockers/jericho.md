# Jericho validation limits

This note records a historical validation constraint. The production sandbox
requires a harness work root outside the platform tree. During that review,
filesystem authorization covered only the evidence checkout, with no external
staging directory, so a production Jericho platform control was not run.
The refusal was retained as an expected boundary.

The historical `test_curve_points_carry_the_verifier_kind_and_not_its_arguments`
case hit the same constraint when pytest temporary storage was kept inside the
authorized worktree. Production sandbox verifier cases had the same staging
requirement and were excluded from the reported passing focused regression command.

Released game-suite and oracle tests were not run: they select evaluation games
and replay released walkthroughs. Validation used synthetic fixtures and a
code-only installed-package probe, without loading the release game table, ROMs,
or calling a model. Running production game controls requires a separately
authorized game and an external staging directory.
