# Reasoning Gym source-only journal review

Review scope: repository source at base `9066072`; no adapter import, dataset
payload access, generation, scorer/control execution, model or network use.
“Established” below means inspected source behavior, not a measured runtime.

## Metric identity and upstream boundary

- The upstream citation recorded by the adapter is
  [`open-thought/reasoning-gym`](https://github.com/open-thought/reasoning-gym),
  PyPI [`reasoning-gym==0.1.25`](https://pypi.org/project/reasoning-gym/0.1.25/)
  (`data/datasets/reasoning_gym.py:8–13,27–34,66–73`). The claimed metric is each
  family's library `Dataset.score_answer(answer, entry) -> float`, **called**
  rather than transcribed. `score()` constructs the family dataset using seed
  `20261008` and size `20`, passes the stored entry, preserves its float reward
  and sets `correct` only for reward >=1.0
  (`data/datasets/reasoning_gym.py:106–124`). This is not generic exact-match
  accuracy; partial credit is explicitly retained.
- The `official.package` string is formatted from a constant; `score()` imports
  whatever `reasoning_gym` is installed without checking its version or artifact
  digest (`data/datasets/reasoning_gym.py:106–124`). A missing family returns
  reward 0 instead of refusing (`data/datasets/reasoning_gym.py:110–113`);
  malformed/private gold validation cannot be presumed from the metric label.
- Existing tests check installed version, pool digest, sample regeneration,
  library-call correspondence and reference/empty-answer behavior for a sample per
  family (`tests/quality/test_reasoning_gym_official_scorer.py:67–99,113–146`).
  They have **not** run here; they depend on the installed library and/or external
  pool (`tests/quality/test_reasoning_gym_official_scorer.py:38–61,67–75`). No
  authenticated package wheel/source, family scorer release mapping or production
  scorer execution is established by inspecting a call site/test.
- Legacy verifier text is quote-unwrapped and stripped; structured answers are
  passed as strings to the ordinary scorer because this adapter does not enable
  evidence-aware dispatch (`data/scaffolds/qa/verify/check.py:129–134,233–242`;
  `data/datasets/reasoning_gym.py:153–173`). Import/scorer exceptions remain
  unmeasured (`data/scaffolds/qa/verify/check.py:228–247`).

## Task and split provenance

- The adapter reads only `pool.jsonl` under its external `DATASET_ROOT`, not live
  generation. Its declared pin is package `0.1.25`, seed `20261008`, 20 items per
  family and pool SHA-256
  `c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177`
  (`data/datasets/reasoning_gym.py:62–87`). `_gold()` retains family/index and the
  full entry, including scorer metadata (`data/datasets/reasoning_gym.py:94–103`).
- The checked-in preparation source enumerates 89 families, iterates them in
  family-major order, serializes each generated question and entry, and writes
  sorted-key JSONL: intended pool size is 89 x 20 = 1,780
  (`data/datasets/reasoning_gym/prepare.py:39–70,76–105`). Reference=1, empty=0,
  non-empty-answer eligibility and deterministic/offline scoring are **inherited
  probe claims**, not dynamically enforced admission rules in `build()`
  (`data/datasets/reasoning_gym/prepare.py:13–19,47–49,99–105`). The recorded
  106/89/16 arithmetic accounts separately for unbuildable `composite`
  (`data/datasets/reasoning_gym/prepare.py:15–17`).
- `_rows()` checks existence and parses JSONL; it does not check the declared
  digest, package version, family list or total count
  (`data/datasets/reasoning_gym.py:75–87`;
  `data/scaffolds/qa/read.py:149–160`). Even preparation `--check` only prints
  the existing digest and returns success without comparing the adapter pin
  (`data/datasets/reasoning_gym/prepare.py:108–123`). The digest comparison lives
  in an unexecuted test (`tests/quality/test_reasoning_gym_official_scorer.py:72–75`).
- The pool is labelled `eval`, with no supplied `study` split; IDs use file order
  as `reasoning_gym/eval/<index>`. QASpec imposes the hash-third platform division;
  `OFFICIAL_EVAL=None` makes the official side the loaded pool, not an independent
  heldout benchmark split (`data/datasets/reasoning_gym.py:153–173`;
  `data/scaffolds/qa/base.py:160–181,235–258,290–312`;
  `data/scaffolds/registry.py:218–248`).
- Not established: actual pool bytes/count/eligibility, authenticated generator
  and package dependency identities, all-entry selection-rule validity, stable
  provenance across environments, selected-manifest/privacy custody or any fresh
  control/measurement. These require independent hash/coverage/identity evidence
  beyond the unchecked loader (`data/datasets/reasoning_gym.py:80–87,106–124`;
  `AGENTS.md:75–81`). `ORACLE_SOURCE="released"` is a declaration even though the
  pool is generated (`data/datasets/reasoning_gym.py:8–13,58–60`).

## Runtime requirements: source verified versus declared

- **Source verified contract:** inherited `DEFAULT_ENV` and task environments are
  `{"kind":"files"}`; no dataset image or services are specified
  (`data/datasets/reasoning_gym.py:167–173`;
  `data/scaffolds/qa/base.py:438–456`). Files environments reject image and other
  exec-only fields, and accept omitted/`none` network
  (`data/scaffolds/registry.py:352–361`). This verifies a source contract, not a
  local interpreter, image, package installation or isolation observation.
- **Declared external dependency:** `reasoning-gym==0.1.25` in the platform Python
  interpreter, with its own dependency tree; the manifest warns that `[scoring]`
  extras introduce an LLM-judge cascade and asserts the selected families avoid
  it. It also records a click/ray conflict as an inherited warning, not a current
  host check (`data/datasets/reasoning_gym.py:140–149`). No package installation,
  extras verification or deterministic/offline execution happened here. The
  preparation path also imports the external library
  (`data/datasets/reasoning_gym/prepare.py:76–95`).
- **Source verified verifier wiring:** the `command` verifier runs `python3
  qa/lib/verify/check.py`, stages inline gold (including the full entry), adapter
  and QA source and declares reward/evidence paths
  (`data/scaffolds/qa/base.py:339–385,478–482`;
  `data/datasets/reasoning_gym.py:94–103`). It does not declare the external pool
  file as an input: the adapter docstring's “pool file travels” statement is
  broader than the actual inline-entry contract
  (`data/datasets/reasoning_gym.py:41–43`; `data/scaffolds/qa/base.py:350–383`).
  The scoring call needs the entry and installed package, not a pool lookup
  (`data/datasets/reasoning_gym.py:106–124`).
- **Not verified offline/pristine:** the host verifier rematerializes inputs, but
  its sandbox call omits `network`, with an unsandboxed branch also present
  (`eval/verification/verifier.py:486–494,549–573`). The sandbox binds the host
  interpreter prefix; its network default is `None`, and only explicit `none`
  adds `--unshare-net` (`eval/containers/sandbox.py:180,194–210,403–419`). Thus the
  adapter's importability/offline claims are not fresh installed-runtime evidence.
  Transitive closure, no-network enforcement, pristine scorer identity, private
  gold custody, quotas and cleanup remain unverified.

## Gate disposition

**Closed at source-review level:** `dataset-journal-gap` documentation coverage
(`journal-coverage`): claimed library metric/call, generated-pool/split pin and
runtime requirements are now documented. The same `measurement` record remains
as a next-gate evidence carrier; work status does not assert readiness
(`docs/dataset-blockers.md:10–21`).

**Next gate:** `source-provenance`, then `scorer-offline-closure`: independently
authenticate package/generator artifacts and the intended pool digest/manifest,
then establish deterministic dependency closure without scoring extras in the
actual verifier runtime. Neither loader nor scorer enforces the complete pin
(`data/datasets/reasoning_gym.py:75–87,106–124,140–149`).

**Still open, each by name:**

| Gate | Evidence this review does not supply |
| --- | --- |
| `source-provenance`, `selected-only-privacy`, `metric-comparability` | Authentic generated pool/eligibility/selected manifest, private entry custody, package identity and comparable family/float-reward coverage; see sections above. |
| `runtime-observations` | Effective backend enforcement (`docs/dataset-blockers.json:20–31`). |
| `paired-filesystem-quotas` | Hard byte/inode enforcement (`docs/dataset-blockers.json:34–55`). |
| `scorer-offline-closure` | Integrated transitive dependency/network closure (`docs/dataset-blockers.json:58–69`). |
| `pristine-verifier` | Independent fresh trusted scoring-runtime identity (`docs/dataset-blockers.json:72–83`). |
| `artifact-only-state-export` | Allowlisted data-only transfer (`docs/dataset-blockers.json:86–97`). |
| `official-scorer-identity` | Authenticated library release and fresh installed-runtime scorer execution (`docs/dataset-blockers.json:100–111`). |
| `snapshot-owned-cleanup` | Applicable lifecycle/owned-absence evidence; files kind is not a discharge (`docs/dataset-blockers.json:12,114–125`). |
| `pilot-guidance-conflict` | Current planning reconciliation (`docs/dataset-blockers.json:128–139`). |
| `measurement-coverage` (oracle, do-nothing, diagnostic/pilot receipts) | No family checks/control/measurement ran; all dataset gates precede the one-task diagnostic (`AGENTS.md:59–65,75–81`). |

No production/H0 authorization follows (`docs/dataset-blockers.json:5–6`).
