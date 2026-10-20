# `hg_bfclrt/` moved out of the repository (2026-10-20)

The BFCL runtime audit's raw material — 424 files, 31 MiB — used to live here. It is the
largest tracked fixture in the repository (63 % of everything under `tests/fixtures/`), and
nothing in `tests/quality` reads it: the BFCL tests use `hg_bfclcontain`, `hg_bfclguard`,
`hg_bfclattest` and `bfcl_ast_source` instead. What lives in `hg_bfclrt` is the *evidence
pile* behind one audit (installed-file inventories, runtime gaps, licences), not a fixture
any test loads.

**Where it is now:** `/mnt/20t/xubuqiang/hg-work/archive/bfclrt-fixture-20261020/` on the
machine that produced it, with the same file list that was committed here.

**What still points at it:** `registry/proposals/*bfcl*` and a handful of
`registry/w-*validation.txt` records cite `tests/fixtures/hg_bfclrt/...` as the place a
number came from. Those citations are now stale paths; the file names they name still exist
in the archive above. `tests/fixtures/hg_bfclattest/{collect,validate,update}.py` reference
paths under `hg_bfclrt/` and are not executed by any test.

Moving it out took the repository from 116 tracked fixture files… to be exact: from 424
tracked files under this directory to none, and shrinks what a reader clones by 29 MiB
without making any test fail. If this audit is ever revisited, copy the archive back rather
than regenerating it: the inventories describe a specific machine's runtime, which is the
whole point of keeping them.
