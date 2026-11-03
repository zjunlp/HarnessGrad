# Contribution boards

Four boards live in [`registry/leaderboards.json`](../registry/leaderboards.json) and are
rendered into the README's [Contribution boards](../README.md#contribution-boards) section by
`tools/leaderboards.py`. This page is the rule a pull request is reviewed against.

## Why a record file instead of a table

A board is a claim about who contributed what. A hand-edited table is a claim that goes stale
the first time a pull request lands: the row is added in one place, remembered in neither, and
the next person copies last quarter's numbers. So the record is JSON, the table is generated,
and `tools/leaderboards.py --check` fails when the two disagree. A contributor's whole change is
one row plus the command.

```bash
python3 tools/leaderboards.py --check   # in CI
python3 tools/leaderboards.py --write   # after editing the record
```

## What a row must carry

Every entry, on any board:

| field | rule |
| --- | --- |
| `name` | matches the directory (`base_harness/<name>`), the preset (`experiments/method-<name>.json`) or the adapter (`data/datasets/<name>.py`) it names; a row that names nothing in the tree is refused, because a name we cannot run is not a contribution |
| `contributor` | a GitHub handle, so a question about that entry has a person to @ |
| `since` | the date the entry was first included |
| `adoption` | independent reproductions reported in the pull request that added or updated the row; a declared number, not a scraped metric |
| `note` | one line: what the thing is, not what it scores |
| `contact` | optional: an address the website can show, so a reader can ask you about the entry. It is stored in the record and **never rendered into the README** — the table on GitHub shows the handle, and a public repository should not be where an email address is harvested from |

`tools/leaderboards.py` refuses a row whose `path`, `preset` or `adapter` does not exist, whose
handle is not a handle, whose adoption is not an integer ≥ 1, or that appears twice on a board.

## Ranking

The three support boards rank by `adoption`, ties by name. The order is therefore a declared
number rather than an impression, and a reader can see why a row is where it is.

## The results board: what counts as a result

A results entry needs all of:

| field | why |
| --- | --- |
| `benchmark`, `metric` | a score is meaningless without the axis it was measured on |
| `receipt` | a file committed in this repository: the run's `end-summary.json`, a `curve.jsonl`, or a results-matrix export |
| `harness_sha` | the 40-hex tree or commit id the score was measured on, so the candidate is a commit and not a description |
| `score` | a number, comparable only inside its `(benchmark, metric)` group |
| `contributor` | the person who can be asked how it was produced |

Scores are grouped by `(benchmark, metric)` and ranked inside a group. There is no
cross-benchmark column: a BBEH accuracy and a Terminal-Bench pass rate are not the same axis,
and a table that puts them in one column invents a comparison that no run supports.

An entry with a score and no receipt is refused rather than displayed, because the alternative
is a leaderboard whose numbers cannot be checked — which is worse than an empty one. Until the
first admissible entry arrives, the board says so.

## What this is not

Not a claim about which harness, method or benchmark is *best*. Adoption counts how many
independent reproductions were reported to us; it does not rank quality, and a new entry
starts at 1 by construction. The boards exist so a reader can find the contributor of an entry
and ask, not so a number can be quoted without the receipt behind it.
