# Contribution graph: who did what, and when

The record is [`registry/leaderboards.json`](../registry/leaderboards.json); the drawings are
`.github/assets/contributors-<board>.svg` (one per board); the list of every entry per person is
[`docs/contributors.md`](contributors.md). All three come from
`python3 tools/contributors.py --write`, and `--check` fails when they disagree. This page is
the rule a pull request is reviewed against.

## Why a graph rather than a ranking

A table with three rows reads as a scoreboard: someone has 87, someone has 2, and the number
invites being read as quality. The graph keeps the same record and answers the question a reader
actually has — *this person contributed these benchmarks, this method, this piece of the
platform, in this month* — and a hover states the entries behind a square. Ordering on the left
is by credited entries, and the right-hand column repeats the total **and** the rank, so the
order is stated rather than implied by position.

## What a credit is

One entry per credited artefact, in one of five kinds:

| kind | what it means | the field that names it |
| --- | --- | --- |
| `harness` | a directory under `base_harness/` that solves tasks | `path` |
| `method` | an RSI method with a preset under `experiments/` | `preset` |
| `benchmark` | an adapter under `data/datasets/` | `adapter` |
| `platform` | a piece of the instrument itself (storage, readiness, identity, records) | `path` |
| `result` | a measured score with a receipt | `receipt` + `harness_sha` |

Every declared adapter must carry a credit: the check fails when one is missing, because a
catalogue of 59 that credits 6 is a claim nobody can act on.

## Fields

| field | rule |
| --- | --- |
| `contributor` | a handle that appears in `people`; a credit for an unknown person is refused |
| `since` | the month the entry was adopted into this record. Not a claim about the first commit: the history was squashed for release, so this is the honest date available |
| `note` | one line: what the thing is, not what it scores |
| `contact` | optional; stored in the record for the website, **never rendered into the README**. The graph shows a handle, and a public repository should not be where an email address is harvested from |

## Getting on it

Low bar on purpose: a contribution is admitted because it *ran*, not because it scored. Three
steps, and `tools/contribute.py` performs the sequence and refuses an entry whose artefact does
not exist before a reviewer sees it:

```bash
python3 tools/contribute.py harness --name my-harness --path base_harness/my-harness \
    --contributor your-handle --contact you@example.com
python3 tools/contributors.py --write
# then open a pull request: one sentence about how you ran it is the whole review request
```

There is no target score and no minimum coverage. The one rule with evidence behind it is the
`result` kind: a benchmark+metric group, a receipt committed in this repository, and the tree
hash the score was measured on. Scores rank inside one group and never across groups.

## What the graph does not claim

`since` is adoption month, so the x-axis is when the catalogue learned about something, not
when the code was typed. Rows with few entries are not less valuable, they are recent or narrow.
The website draws the same record with a date-range picker; this drawing is the static one.
