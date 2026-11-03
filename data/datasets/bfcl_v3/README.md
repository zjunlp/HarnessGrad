# bfcl_v3: the derived selected index

The adapter is `data/datasets/bfcl_v3.py`. This directory holds the one program that
produces what the adapter requires but the release does not ship: `selected-index.v1.json`.

```sh
python3 tools/datasets.py --fetch --dataset bfcl_v3   # tarball (sha256-verified) + extraction
python3 data/datasets/bfcl_v3/prepare.py              # derive the index; refuses a wrong tarball
python3 data/datasets/bfcl_v3/prepare.py --check      # re-derive and compare; writes nothing
python3 data/scaffolds/qa/control.py --dataset bfcl_v3 --oracle   # expect reward 1
python3 data/scaffolds/qa/control.py --dataset bfcl_v3 --noop     # expect reward 0
```

`--dataset` filters on the **registry** name, `bfcl_v3`. The dash spelling (`bfcl-v3`)
matches no dataset, prints nothing and exits 0 — a no-op that looks like success.

## Why it is derived and not downloaded

The reader in `bfcl_v3.py` addresses rows **by byte span**, so a selection is cheap and
provably does not touch a row it was not asked for. That design needs an index of
`{"offset", "length", "sha256"}` triples per selected task, and it refuses to build one on
the fly (`_selected_records()`: "No scan/build fallback is permitted"). The index is not
upstream's artifact — it is this platform's mapping onto upstream's bytes — so it cannot be
inside `ShishirPatil/gorilla@v1.3`. Nothing in the release is modified to make one.

`provision_status()` counts the index as part of "am I provisioned": an adapter that
reported present while its own control raised "selected index is unavailable" would be
answering the wrong question.

## The schema, and where it is enforced

| field | value | read at |
| --- | --- | --- |
| `schema_version` | `1` (an `int`, compared with `type(...) is int`) | `bfcl_v3.py:383` |
| `repo_tag` | `v1.3` | `bfcl_v3.py:385` |
| `tarball_sha256` | `5785…b7fe`, the tag tarball's digest | `bfcl_v3.py:386` |
| `tasks` | task id -> `{question_id, question[, answer]}` | `bfcl_v3.py:387`, `:392` |
| `question` / `answer` span | `{offset >= 0, length > 0 <= 1 MiB, sha256: 64 hex}` | `bfcl_v3.py:397-405` |
| `answer` | present **iff** the subset is `simple` | `bfcl_v3.py:396` |
| span identity | the span's bytes parse to a row whose `id` is `question_id` | `bfcl_v3.py:412-421` |

Extra top-level keys are ignored by the reader. The builder adds `derived_from`: the tarball
digest it measured, and each source file's byte length, sha256 and row count, so a reader can
re-derive the artifact instead of trusting it.

## No selection decision is made here

The builder indexes **every row of both subsets** — 400 `simple` + 240 `irrelevance` = 640
tasks — under the same positional ids the full-pool reader derives (`simple/<i>` is row `i`
of `BFCL_v3_simple.json`). The pool is exactly the two official evaluation categories
(`OFFICIAL_EVAL = None`, `SPLIT = None`), so there is no study/eval division to invent and no
subset to choose. An index holding fewer tasks would be a selection judgement; the builder
makes none.

## Refusals

- Missing tarball: names `python3 tools/datasets.py --fetch --dataset bfcl_v3`.
- Tarball sha256 differs from `TARBALL_SHA256`: refuses, with both digests and the same
  fetch command. An index derived from other bytes would carry a digest that is true of the
  pin and false of its own spans.
- An extracted source that differs from the pinned member: refuses, because the spans would
  address bytes that are not the release's.
- A source with the wrong row count, a duplicate id, an answer id the question file does not
  have, or a row over the reader's 1 MiB cap: refuses before writing anything.

`build()` then re-reads every span from the files on disk (`verify()`) and compares the
written bytes to what it derived, so a written index is one that the loader can resolve.

## What this does not claim

The index is **not authenticated**. Its `repo_tag`, `tarball_sha256` and row digests are
assertions that happen to be reproducible from the pinned tarball; external index custody,
release provisioning and immutable source storage remain separate production gates. A digest
does not make released bytes confidential — the gold answers are in `possible_answer/` and
never reach a task (`score()` redacts `expected_functions`).
