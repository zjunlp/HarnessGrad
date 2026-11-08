# Historical snapshot acquisition and licensing audit

Checked 2026-11-07. The [machine-readable receipt](SOURCE_LICENSE_AUDIT.json)
records public URLs, observed response hashes and source-member hashes. This is
source/provenance evidence, not a legal determination or full dataset acceptance.

## What the public source establishes

The actual [PyPI 0.1.25 metadata](https://pypi.org/pypi/reasoning-gym/0.1.25/json)
reports `license_expression: Apache-2.0`. The downloaded source archive matched
PyPI's SHA-256 `932247c760dd23e09fb14d013d29ec4c8804e62846bd50473f6915dc854adad2`
and size 6,789,585 bytes. No dependency installation or package execution was
needed to inspect it. Its root `LICENSE` is Apache License 2.0 and its
`pyproject.toml` declares `license = "Apache-2.0"`.

That declaration has concrete component boundaries:

| Source member in the 0.1.25 archive | Observed evidence |
| --- | --- |
| `reasoning_gym/induction/acre/acre.py:3–4` | “Culled and Adapted from https://github.com/WellyZhang/ACRE”; “Licensed under GPL-3.0”. `acre` is one of the retained 89 families. The upstream ACRE `main/LICENSE` separately returned GPLv3 text, but mutable `main` does not identify the exact ancestral revision. |
| `reasoning_gym/code/contrib/bfit/LICENSE.md` | MIT, copyright 2020 elikaski. |
| `reasoning_gym/games/contrib/sokoban/LICENSE` | MIT, copyright 2023 Bruno Andrade. |
| `reasoning_gym/logic/contrib/logic_puzzle/README.md` | Credits `nouhadziri/faith-and-fate` and says “License: MIT”; this is the packaged README's claim. |
| `reasoning_gym/code/codeio.py` | Reads bundled `codeio.jsonl.gz`; selected task descriptions and reference code enter the question. It describes snippets transformed by an LLM from curated raw code. The original source and rights chain for those snippets was not established by this inspection. |
| `reasoning_gym/arc/arc_1d.py` | Credits Rust generators from `optozorax/arc_1d`, machine-converted to Python. The exact ancestral revisions and output rights were not traced. |

Apache-2.0 §4 and the MIT notices contain redistribution conditions for their
respective works. The GPL statement is a reason to investigate the relevant
content and obligations, not proof that every generated question is itself
GPL-covered. Conversely, calling data “generated” does not establish that
embedded descriptions/code are free of third-party rights. A complete
family/content-source and notice assessment remains outstanding; these examples
are not an exhaustive license inventory.

## What was checked for the exact historical bytes

The required pool remains 3,315,981 bytes, 1,780 rows, SHA-256
`c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177`.
The earliest inspected repository commit `a6d36c2` already contains that digest,
a host-local data-root reference and the procedural recipe. The inspected adapter and preparation recipe do not provide a public pool URL,
authenticated acquisition record, original generation-runtime receipt or
distribution authorization. A retained local file matches the pin;
its import receipt establishes local copying and byte identity only.

The public-source checks were deliberately bounded:

- The actual 0.1.25 source archive contains no member named `pool.jsonl`.
- The [v0.1.25 GitHub release](https://github.com/open-thought/reasoning-gym/releases/tag/v0.1.25)
  API response has zero release assets. GitHub's source archives are generator
  source, not the historical pool.
- The releases API requested up to 20 entries and returned five releases
  (`v0.1.25`, `v0.1.24`, `v0.1.23`, `v0.1.22`, `v0.1.20`), all with zero assets.
- The packaged README explains procedural generation and a script for making
  Hugging Face datasets. It does not identify this adapter's seed/family
  selection, exact pool digest or an endpoint distributing those exact bytes.

These observations do not prove that no copy exists anywhere. They do establish
that the inspected package/release paths do not supply a verified public source
for this exact snapshot. No unverified dataset URL was promoted to a default,
no generation was rerun, and the retained pool was not served or uploaded.

## What an operator can verify now

An operator who has independently established authority to use a retained local
snapshot can run:

```
HG_DATA_REASONING_GYM=/absolute/new/nonexistent/directory \
  python -m tools.prepare_reasoning_gym --import-pool /authorized/local/pool.jsonl
HG_DATA_REASONING_GYM=/absolute/new/nonexistent/directory \
  python -m tools.prepare_reasoning_gym --check
```

These paths are placeholders. A fresh local control during this audit performed
that import/check against the retained original: the exact pin and byte count
matched, the copied file contained 1,780 rows, and a second import was refused
without changing the successful receipt or pool. The source was read only and
remained unchanged. This validates the acquisition mechanism with real retained
bytes; it does not authenticate the file's earlier provenance or establish a
right to redistribute it.

`--snapshot-url` is available for an independently authorized endpoint. No
public endpoint serving this exact hash has been verified, so no executable
public download URL can honestly be supplied here. Its HTTP/proxy/deadline
controls use self-authored local responses, not the retained pool. The historical
native scorer controls (1/0/0.65) remain selected scorer evidence, separate from
this source-license audit. Neither those controls nor a successful import grants
distribution rights or reproduces the historical generation environment.

To close this boundary, obtain a stable authorized exact-hash endpoint, a
producer/publisher provenance record, and the applicable content licenses and
notices. Preserve the pin while that evidence is missing. No legal/public-ready
claim should be inferred from `pool.snapshot.json`.
