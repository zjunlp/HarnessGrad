"""Checks that keep a run from silently invalidating itself.

Two things must hold for a measurement to mean anything, and the operating system
enforces neither. Both were measured on this platform rather than assumed, by
running a harness whose entrypoint probes its own surroundings and reports what it
found:

    up1/driver.py, up2/driver.py            absent      (the `..` climb is closed)
    abs /…/HarnessGrad/.env                 READABLE
    abs /…/HarnessGrad/driver.py            READABLE
    abs /…/HarnessGrad/eval/metrics.py      READABLE
    write /…/HarnessGrad/_probe             WROTE

  1. **The harness cannot reach the platform by accident.** The workspace used to
     be `runs/<id>/workspace`, inside the platform tree and two `..` from the
     driver, the scorer and `.env`. It now lives outside; the relative climb is
     gone and a run refuses to start if the two ever overlap again.

  2. **The method cannot rewrite the platform.** The platform's whole claim is
     that scoring is its own -- a method that edits the scorer is not being
     measured, it is marking its own paper. Same for the harness, which is
     arbitrary code the platform did not write.

Neither can be made *impossible* without a real sandbox (container, user
namespace, bind mount), and the measurement above shows what that costs: an
absolute path still reaches everything, because the subject runs as the same user.
So the second half is detection, and it is where the guarantee actually lives --
the platform hashes its own files around every method call and every evaluation
and **fails the run, naming the paths**, if anything moved.

Three claims, three statuses, kept apart on purpose:

    the harness cannot reach the platform by `..`     enforced (workspace moved)
    the harness cannot reach the platform at all       detected (hashes, per run)
    the method cannot reach the platform at all        detected (hashes, per call)

Read the numbers above before trusting a score from a harness you did not write.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

#: What counts as the platform: everything a run reads to decide what a number
#: means. `.env` is here because it holds the credentials the measurement is paid
#: for, and `docs/` because it is the written contract an adapter is written
#: against.
#:
#: `harnessgrad/` is where the platform's implementation lives now -- the entrypoint
#: `driver.py` wires it up. It is the referee in the most literal sense (it decides what a
#: round is, what a curve point contains and which failures are the platform's own), so it
#: is hashed like the rest. A move that left it out would have put the whole scoring path
#: outside the check while the tuple still looked complete.
#:
#: `base_harness/` is deliberately **not** here. It is a candidate, not a referee:
#: every run copies it into a workspace first, and the copy is what gets edited.
#: Listing it would flag the one write the platform exists to make. A run's
#: workspaces live outside the platform root entirely, so no legitimate write
#: lands in the paths below.
PLATFORM = ("driver.py", "harnessgrad", "eval", "ckpt", "data", "tools", "methods",
            "tests", "docs", "improvers", "INTERFACE.md", ".env")

#: Top-level entries that are deliberately **not** hashed, each with its reason.
#:
#: The list above is hand-written, and a hand-written list of "everything that matters"
#: is a list that goes stale the moment the tree grows. `improvers/` was added while this
#: comment was being written and was **not** covered: it holds the default `skill.md`
#: handed to the improver and the registry that decides *which* improver runs -- both
#: referee-side, both editable by a method without the check firing. That is the
#: platform's central claim, so the gap matters.
#:
#: The fix is not a longer list, it is a check: every top-level entry must be either in
#: `PLATFORM` or in here with a written reason
#: (`tests/quality/test_integrity.py::test_every_top_level_entry_is_classified`).
#: Adding a directory then forces a decision instead of silently widening the hole.
NOT_PLATFORM = {
    "base_harness": "a **candidate**, not a referee: each run copies it into a workspace "
                    "and the copy is what a method edits, so hashing it would flag the "
                    "one write the platform exists to make",
    "current_harness": "output of `tools/export_harnesses.py`, derived from runs; "
                       "nothing a run reads",
    "install.sh": "provisions a machine (`bubblewrap`, `git`, `python3`); it does not "
                  "decide what a number means",
    "README.md": "the front door for a human, not something a run reads",
    "DOWNLOADS.md": "**generated** by `tools/datasets.py --write` from each loader's "
                    "`downloads()`; it changes whenever a dataset is added and decides "
                    "nothing a run reads (the loaders do). Hashing it would invalidate "
                    "every past run's certification for a reason that cannot change a "
                    "number -- the same argument as `web/`",
    "LICENSE": "the licence text; hashing it would change nothing about a measurement",
    ".env.example": "the template for `.env`; it holds placeholders, and `.env` itself "
                    "(which holds the credentials) is hashed",
    ".harnessgrad-state": "runtime state written by `tools/harnessgrad.sh` (pid files, "
                          "a log); generated, like `runs/`",
    ".pytest_cache": "pytest's own cache; generated by running the tests",
    "assets": "published figures and the logo (`assets/`): images a human looks at, produced "
              "by `tools/plot-*.py` from `runs/`. No path a run executes reads them, so "
              "hashing them would fail a certification over a picture",
    "contrib": "the contribution door (`contrib/`): a submission schema, one example "
               "submission and the validator that checks handed-in harnesses. It decides "
               "what a *contributor* may submit, never what a number means; no run reads it",
    "website": "the published site (`website/`, built with `pnpm build` from `runs/` through "
               "`tools/export-site.py`): a **human** reads it, a run does not, and the build "
               "output changes on every build -- the same argument as `web/`. Classified "
               "2026-10-19, when `test_every_top_level_entry_is_classified` caught it after "
               "the site moved from `web/` to `website/`: `web/` stayed in the list and the "
               "new directory was never written down on either side",
    "console": "the read-only audit console (`console/`): it **reads** `runs/` and the "
               "platform's own exports and writes nothing; no run executes it, so editing it "
               "cannot change what a measurement does. Listed here rather than in PLATFORM "
               "for the same reason as `web`: a human reads it, a run does not",
    "reports": "audit, evidence and review output that used to sit in the repository root "
               "and was moved here so the root stays navigable. It is written by humans and "
               "review sessions after a measurement, never read by a run; hashing it would "
               "fail a certification because someone added a report",
    "registry": "the dataset-blocker register, its withdrawal record, the closure assessment and "
                "the worker receipts that back them (`registry/dataset-blockers.json` and "
                "friends). It is the bookkeeping a *review session* writes about which datasets "
                "may run and what evidence they owe; no path a run executes reads it -- checked "
                "with `grep -rn registry/dataset-blockers driver.py harnessgrad eval data "
                "methods`, which returns only tests and tools -- and hashing it would fail a "
                "certification every time somebody recorded a blocker or closed one. Same "
                "category as `reports/`, and classified here on 2026-10-15 after "
                "`test_every_top_level_entry_is_classified` caught the migration that created "
                "this directory: `1825cd0e` moved the registry out of `docs/` and never said "
                "which side the new top-level entry belonged to, so the guard failed in `main` "
                "and the sweep did not report it because a collection error earlier in its batch "
                "had already skipped the file.",
    "conftest.py": "root pytest configuration: it guards the test runner's temporary "
                   "directory placement before collection. It shapes how the tests are "
                   "run, never what a number means, and no run reads it",
    "AGENTS.md": "operating rules for the agents and humans who work *on* the platform -- "
                 "review discipline, pilot policy, prohibited shortcuts. It governs how the "
                 "platform is changed, not what a number means, and no run reads it",
}

#: Where those names live, resolved from this file rather than from the working
#: directory: `driver.py` runs from the platform root, but the tests do not.
PLATFORM_ROOT = Path(__file__).resolve().parents[2]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


#: One digest per file, reused while the file's identity is unchanged.
#:
#: `snapshot()` is taken **around every method call** (`harnessgrad/methods.py:235`) and at
#: both ends of a run (`eval/run/runner.py:1400`/`:1777`), and it hashed the whole platform
#: each time: measured 2026-10-19 on `mistral`, 20,817 files and **2.4 s per call** -- twice
#: per method call, so a 48-way batch pays seconds of pure I/O for every step it takes and
#: the bill lands on the measurement instead of on the check.
#:
#: The fix is not a weaker check, it is a cheaper one. `st_ctime_ns` is in the identity on
#: purpose: a writer can put `mtime` back with `os.utime`, but **nothing in user space can
#: set `ctime`**, so a file that is rewritten and re-dated still fails the identity test and
#: is re-hashed. Any write changes `ctime`, which is exactly what this check must notice.
_DIGEST_CACHE: dict[str, tuple[tuple[int, int, int, int, int], float, str]] = {}
_CACHE_STATS = {"hit": 0, "miss": 0}

#: **A digest this fresh is not trusted.** File timestamps come from the kernel's coarse
#: clock, so a write and the `os.utime` that re-dates it can land in the same tick and leave
#: `(size, mtime, ctime)` identical while the bytes differ. Measured 2026-10-19: that window
#: is a few milliseconds, and a rewrite inside it reused a stale digest in the test below.
#: Entries younger than this are re-read; the method call this check wraps takes seconds, so
#: the cost is paid once and the reuse still happens where it matters.
_CACHE_MIN_AGE_S = 0.05


def cache_stats() -> dict[str, int]:
    """How many digests this process reused, and how many it had to read."""
    return dict(_CACHE_STATS)


def reset_cache() -> None:
    """Drop the cache and its counters. Tests use this; a run never needs to."""
    _DIGEST_CACHE.clear()
    for key in _CACHE_STATS:
        _CACHE_STATS[key] = 0


def _identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _digest_cached(path: Path) -> str:
    """`_digest`, unless this exact file state was already hashed by this process."""
    if os.environ.get("HG_INTEGRITY_NO_CACHE"):
        _CACHE_STATS["miss"] += 1
        return _digest(path)
    identity = _identity(path.stat())
    key = str(path)
    cached = _DIGEST_CACHE.get(key)
    if (cached is not None and cached[0] == identity
            and time.monotonic() - cached[1] >= _CACHE_MIN_AGE_S):
        _CACHE_STATS["hit"] += 1
        return cached[2]
    _CACHE_STATS["miss"] += 1
    digest = _digest(path)
    _DIGEST_CACHE[key] = (identity, time.monotonic(), digest)
    return digest


def snapshot(root: Path) -> dict[str, str]:
    """Content digest of every platform file.

    Not small, whatever this docstring used to say: 20,817 files on 2026-10-19, ~2.4 s of
    reading per call, and it is called twice per method call. Digests are reused while a
    file's `(dev, inode, size, mtime, ctime)` is unchanged (`_digest_cached`); the *walk*
    is not cached, so a file that appeared or disappeared is still seen.
    """
    out: dict[str, str] = {}
    for name in PLATFORM:
        target = root / name
        if target.is_file():
            out[name] = _digest_cached(target)
        elif target.is_dir():
            for p in sorted(target.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts \
                        and not p.name.endswith(".pyc"):
                    out[str(p.relative_to(root))] = _digest_cached(p)
    return out


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """What changed: added, removed, modified."""
    b, a = set(before), set(after)
    return {
        "added": sorted(a - b),
        "removed": sorted(b - a),
        "modified": sorted(k for k in b & a if before[k] != after[k]),
    }


def content_sha(root: Path) -> str:
    """One digest for the whole platform: what decided what this run's numbers mean.

    **A run's numbers are only comparable with another run's if the two ran the same
    platform, and until now nothing on disk said which platform that was.** Measured
    2026-10-05: 82 run directories, and not one recorded the content of the code that
    produced it -- so a reader could not tell a curve measured before a fix from one
    measured after, and an experiment invalidated mid-flight (a platform file edited while
    the driver ran) was indistinguishable from a valid one. `git rev-parse HEAD` is not
    enough: it says nothing about an uncommitted edit, which is exactly the case that
    produced the invalid run.

    Domain-separated by field so a rename cannot collide with a content change: the same
    property `ckpt.git_state.content_sha` gives a harness, applied to the referee.
    """
    snap = snapshot(root)
    h = hashlib.sha256()
    for name in sorted(snap):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(snap[name].encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def workspace_is_outside_platform(workspace: Path, root: Path) -> bool:
    """A workspace nested in the platform is reachable from the harness by `..`.

    This is the cheap half of isolation and the half that actually holds: a
    subprocess cannot walk out of a directory tree it was never placed inside.
    """
    try:
        workspace.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False
