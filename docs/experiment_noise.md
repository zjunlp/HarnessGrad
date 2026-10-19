# How big a score move is a measurement, and how big is noise

Six modules cite this file (`data/datasets/alfworld.py`, `data/datasets/reasoning_gym.py`,
`harnessgrad/records.py`, `harnessgrad/loops/mode_a.py`,
`harnessgrad/store/schema.py`, `tests/quality/test_identity_and_verdicts.py`) and it was never
committed: the citations pointed at a note that lived in the working conversation. It is
written down here because a rule that only exists in one person's memory is not a rule the
next reader can apply.

Everything below is a measurement made on this machine; the source of each number is named so
it can be re-read rather than believed.

## The smallest move the task count allows

A benchmark scored `k` of `n` tasks can move in steps of `1/n` and no smaller. On ALFWorld's
`train100` that is 1 percentage point; on the 20-task arm used for the noise measurement it is
**5 points**. So "0.200 vs 0.100 on 20 tasks" is a two-task difference, not a factor of two,
and a curve drawn from 20 tasks cannot resolve anything below 5 points no matter how stable
the harness is.

`data/datasets/reasoning_gym.py` names the other half of this: several of its families return
partial credit, so a task can move without crossing an all-or-nothing boundary. On a binary
reward a task either solves or does not, and the smallest visible event is one task.

## ALFWorld, same arm, five batches

Measured with `qwen3.5-9b-local`, the same harness, the same 20 tasks, the same seed,
`temperature 0`, five batches:

| batch | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| tasks solved, of 20 | 10 | 8 | 11 | 11 | 11 |

**SD 1.30 tasks = 6.5 percentage points, and 4 of the 20 tasks changed side at least once.**
The spread is not the harness moving: nothing about the harness changed between batches. It is
the environment (ALFWorld's admissible-action sampling and the room layouts) plus a local
endpoint that is not bit-deterministic under load.

**The rule that follows: a move smaller than roughly 6 points on 20 tasks is not evidence, and
every ALFWorld number must be quoted with its task count.** At 134 tasks (`valid_unseen`) the
same 6.5-point figure is the level at which the sign test needs about six net discordant tasks
before it reaches p<0.05. `STATUS.md` §5 states that threshold per pool; the numbers here are
what it is derived from.

## Two recorded false gains

Both are the same failure: a score moved, nothing about the harness had changed, and the
record did not say so.

1. **A published `+0.200` from an unedited tree** — cited in `harnessgrad/loops/mode_a.py` as
   the precedent for recording `unreachable_edits`. The tree the platform measured was the
   base harness, and the number that came back was higher than the base's.
2. **`v9-dgm`, 2026-10-05** — round 1 scored **0.200 against H0's 0.100**, and the candidate
   was `agent.py` byte-identical to H0 plus `agent_backup.py` (a 635-line copy of the original)
   and `agent_new.py` (an 88-line fragment nothing imports). The edit could not have moved the
   score, and the curve read as a real gain.

The platform's answer is not to suppress those points — a measurement that happened is a
measurement — but to put the reason on the point: `unreachable_edits` when nothing the
entrypoint can load is different, `identity_dead_surface` when the editable surface did not
change, and `test_rate: null` (not `0`) when no check reported a tally, so "the harness scored
well" and "the harness did nothing" cannot be read as the same record.

## What is *not* claimed here

This note quantifies same-arm noise for ALFWorld on this machine. It is **not** a claim that a
6-point move is meaningless on a 134-task pool, where the same per-task variance averages
down by `sqrt(34)`; it is a claim about what a 20-task arm can resolve. Do not carry the 6.5
points across pools or across benchmarks without measuring it there.
