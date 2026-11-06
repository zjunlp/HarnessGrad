# Adapter integration and evidence

For current adapter requirements and validation steps, see [dataset onboarding](dataset-onboarding.md), [adapter specification](adapter-spec.md), and the dataset-specific documentation under `data/datasets/`. Current known blockers and their evidence are maintained in [`registry/dataset-blockers.json`](../registry/dataset-blockers.json).

The old full journal and lifecycle-review snapshots have been retired from this checkout. Historical line-number quotations in retained records describe those retired sources, not this page. Unavailable experiment receipts remain explicitly unverified; a historical quotation does not establish a new measurement.

A new adapter should document its source version and licensing, split, scorer identity, trusted/private data boundaries, runtime requirements, resource and network assumptions, and validation coverage. Synthetic or mock controls are useful interface tests; they do not establish benchmark scores or authorize production runs. Any reported measured result must retain a verifiable run identifier, tested harness tree identity, and its underlying receipt.

### Dataset loading gets an independent provider environment (2026-11-05)

Commit `e79c621f` adds the opt-in runtime/provider path; `759904a5` separately
documents a seeded platform venv and activated `python` commands. GSM8K is the
first migrated adapter. Its static declaration pins Python 3.12 and
`pyarrow==23.0.0` without importing its loader in the platform. Explicit
`python tools/prepare_dataset.py --dataset gsm8k` builds a dedicated external
venv at its permanent physical path, verifies its installed package set and file
inventory, then atomically publishes its identity. Ordinary checks never install,
repair or fall back to the platform's packages. A changed runtime fails admission.

The [runtime manager](../harnessgrad/dataset_runtime.py) and
[provider client](../harnessgrad/dataset_provider.py) preserve the registry's
existing APIs. The trusted worker returns public tasks separately from private
answer/verifier data. QA SETUP, VERIFY and ENV mappings construct values only for
selected tasks, including an empty selection. Fetch, lint and generated metadata
discovery avoid importing the opted-in adapter in the platform. The README now
includes preparation between fetching and running. Download checks inspect pip
entries in the selected runtime; JSON checks return a nonzero exit status when
anything is missing, including for legacy adapters.

Validation is indexed by the retained
[receipt](/home/wangyenan/.cache/hg-provider-acceptance-20261105-nvi2zkkh/validation-summary.json).
A fresh seeded platform install had no pyarrow before or after preparation. The
fetch command verified already available pinned GSM8K files; this was not a fresh
network transfer. The provider loaded all 8,792 tasks (7,473 train, 1,319 eval),
and catalog, split, private fallback answers and one selected declaration matched
the local adapter path by canonical hashes. A separate no-pip/no-pyarrow platform
fixture exercised authored Parquet rows, selected/empty resolution, private-input
separation and poisoned import-path rejection. Its actual sandboxed driver control
ran one authored task and one trial with a fixed answer: score 1, zero invalid or
harness-failed tasks and zero model calls. This is an instrument check, not a
measurement on released GSM8K tasks.

The final focused regression passed 338 tests and 38 subtests; 120 data-dependent
cases skipped, and 16 cases were deselected (12 requiring the absent chess package
and four failures reproduced on clean `ea05e260`). Installed-wheel tests separately
passed all three checks, including exact provider resources and offline synthetic
scoring. Seven additional dataset-control failures also reproduced on that clean
baseline because unrelated datasets or runtimes were unavailable. Full-suite
success is not claimed. The final regression command and JUnit report are retained
at `/tmp/hg-env-regression-complete-20261105-tzs_to47/command.json` and
`/tmp/hg-env-regression-complete-20261105-tzs_to47/junit.xml`. The live integration
can be repeated after explicit preparation with
`HG_TEST_DATASET_RUNTIME_ROOT=/absolute/prepared/store python -m pytest -q tests/quality/test_dataset_provider_integration.py`
and an external `--basetemp` directory; the tests themselves do not install or fetch.

This increment isolates trusted loader dependencies. It does not seal host system
libraries or the entire base standard library, migrate the other adapters or
service/scorer/Harness environments, or bind runtime identities into H0, caches
and resume comparisons. Direct-adapter diagnostic scripts still require their
own dependency environment. Retained manifests, logs, JUnit reports and the
authored control curve describe those scopes. Temporary fixture directories,
validation venvs and the baseline worktree were removed; cleanup is recorded in
the receipt. No model endpoint or benchmark pilot was invoked.

Follow-up review found that unexpected loader exceptions were reported as
unavailable, allowing lint to pass a broken adapter. Provider loading now reports
those exceptions as invalid; explicit missing data/configuration and missing
runtime handling keep their existing behavior. Ten authored QA rendering cases
cover code errors and missing prerequisites with and without
`--allow-unprovisioned`: six failed before the fix and all ten pass afterwards.
The focused provider, protocol, QA, registry and lint-readiness suite passed 133
tests. Commands, logs and JUnit reports are retained under
`/tmp/hg-provider-readiness-fix-20261105-uh0hn7m9`; fixture directories were cleaned.
No dependency installation, benchmark run or model call was needed.

A second review found the same downgrade in the provider client. Identity,
protocol and unexpected worker errors now remain invalid. Timeout and local
startup failures retain unavailable status and their error type, and lint fails
these exchanges instead of treating them as missing preparation. Runtime failures
preserve their original status and scope, including changes after readiness or
during catalog/resolve. Explicit missing prerequisites remain skippable.
Real subprocess controls cover wrong identity, malformed JSON, startup crashes,
unsuccessful exits, worker exceptions, timeouts and missing interpreters, both
with and without `--allow-unprovisioned`. The focused suite passed 172 tests;
18 of the new regression cases failed before the fix. Commands, logs and JUnit
reports are retained at `/tmp/hg-provider-client-fix-20261105-zh3md5wy`; temporary
fixtures were cleaned. These controls installed no dependencies and used no model.

### CostBench services and scorers bind managed Python artifacts (2026-11-05)

CostBench static/dynamic now share a prepared Python 3.12 execution artifact,
separate from the platform interpreter. The stdlib-only runtime manager reuses
fixed-path construction, explicit installation, atomic publication and content
verification for both provider and execution profiles. The new
[execution declaration](../data/datasets/costbench/execution-runtime.json) retains
the existing exact requirements lock. Existing GSM8K declarations and published
provider artifacts keep their v1 API and manifest compatibility.

`python tools/prepare_dataset.py --dataset costbench --runtime execution` prepares
only dependencies; adding `--check` installs nothing and does not start a service,
load a task or claim a successful score. Existing prepared CostBench data needs
no regeneration. Full `prepare.py --install` now delegates to this manager before
running its unchanged data-generation protocol under the selected Python.

Selected SETUP/VERIFY declarations bind a specific manifest, runtime ID,
interpreter, physical prefixes and role. Launch admission rechecks those exact
bytes without selecting a new publication. The service uses isolated Python,
read-only explicit code/input/runtime mounts and private HOME/temp/cache/cwd.
Only handles created by the current record/model setup are forwarded; ambient
credentials and model sockets are excluded. The scorer uses the same dependency
artifact in its existing read-only verifier sandbox. Actual interpreter and
package origins are recorded. Actor task files remain separate from the private
case, service configuration, sealed episode and scoring inputs.

Bound-service shutdown signals the actual bubblewrap child first, allowing its
episode and receipt to finish before group cleanup; the existing bounded stop
and forced cleanup remain. A delayed-write regression caught the earlier wrapper
shutdown race. Direct controls also use canonical verifier admission/sandboxing.
The aggregate report validates archives before restarting under the selected
Python; a private binding file preserves that exact selection across restart,
including when the discovery index is no longer available. The report CLI is a
trusted aggregation process, not a claim of actor sandbox execution.

Evidence is indexed in the retained
[validation summary](/home/wangyenan/.cache/hg-execution-acceptance-20261105-6pvstx5z/validation-summary.json).
Core validation passed 377 tests and 38 subtests. CostBench passed 257 distinct
cases across the initial and focused regression runs, covering static/dynamic
protocols, all dynamic modes, training compatibility, cost metrics, actor
forgery/private-input checks, preparation dispatch and report validity. Seven
negative-input fixtures now explicitly grant only their replacement private
file or database shard directory; their corruption assertions remain unchanged.
Eighteen source-dependent cases initially skipped because their fixture defaulted
to a different data root, then all passed with the actual root configured.

A fresh Python 3.13 platform without numpy, PyYAML, pydantic or openai completed
eight real CostBench static/dynamic train/test oracle/noop controls through the
same Python 3.12 service/scorer artifact. The actor could not see that runtime or
those packages. Every arm matched its expected score, with zero invalid or
unmeasured outcomes and zero Harness model calls. Service/scorer observations
confirmed isolated startup, read-only mounts and package origins in the selected
venv; cleanup completed. Two simultaneous real CostBench services also retained
independent episode state while sharing dependencies. The fresh 3.13 platform
successfully ran report aggregation under the same bound 3.12 interpreter.

Compatibility validation passed 91 tau3/verifier-runtime tests using a separately
prepared 3.12 runtime and local fixed-response model-transport fixtures. Tau3
itself is not migrated to execution bindings here. Three installed-wheel tests
passed, including exact CostBench recipe resources and metadata/helper imports;
full CostBench wheel execution is still outside that contract. One Terminal-Bench
case was deselected after reproducing its missing task checkout on clean
`656e4c8b`. No full-suite success, regenerated whole-pool gold audit, real model
inference or benchmark performance result is claimed.

Validation commands, raw failures/retries, JUnit, runtime inventories and control
receipts remain outside the repository. Temporary fixtures, validation venvs and
the comparison worktree were cleaned. Venv/base-executable identity does not seal
all host libraries or the complete standard library. H0/cache/resume identity
comparison, Harness candidate preparation and other dataset migrations remain
later work. Changes stay on `fix/environment-isolation`; no main merge was made.

### Bound services receive only invocation-owned recording handles (2026-11-05)

The review of `b93592c5` found that a bound service declaring a model but no
recording could inherit ambient `HG_RELAY_CONTEXT_PATH` and
`HG_RELAY_RECORD_PATH`. The model branch copied the platform environment, and
the bound launcher treated those copied values as handles owned by this trial.
Bound launches now receive recording handles directly from the current recording
branch. The model socket continues to come from the current broker object.
Unbound relays retain their existing environment handling.

Real bubblewrap controls cover all four model/recording combinations with stale
recording paths in the platform environment. The synthetic stale context is
explicitly mounted read-only, so the controls check that the service ignores it
even when it is readable. Recorded services use their current trial context;
unrecorded services receive neither recording variable and create no episode.
Model access, delayed record finalization and cleanup remain covered.

Before the fix, the model-without-recording control failed and the other three
combinations passed. After the fix, all 42 launch, diagnostic and import-direction
tests passed, together with 38 subtests. One intermediate verifier launch was
refused because the test driver venv was under `/tmp`; recreating that venv under
the user cache satisfied the existing sandbox policy. Commands, raw failures and
JUnit are retained at `/tmp/hg-relay-handle-fix-20261105-gik3gt9i`; temporary test
environments and workspaces were cleaned. No model inference or main merge was
performed.

### Managed dataset artifacts bind H0, cache and resume (2026-11-05)

`dataset_runtime_identity` now records the provider that actually loaded the
data and selected task-to-service/verifier artifact associations. A normalized
artifact table avoids repeating a shared CostBench binding for every task. A
bounded registry provider session selects once, then checks that exact artifact
for subsequent requests, including after its publication index changes. Ordinary
registry callers retain their previous provider behavior.

Run metadata, H0 and curve points carry the optional identity. Cache keys,
entries and completed progress records carry task projections. Batched projection
keeps this linear in task count. Live admission precedes cache, whole-point and
H0 reuse, including paths that launch no scorer. Train curves retain the complete
run-plan identity when later rounds measure only a subset. Resume checks metadata,
all prior points and completed eval progress before metadata writes or checkpoint
restaging; the platform-crossing flag does not bypass these checks. Frozen
references with changed/missing identities retain their score but cannot support
paired improvement or capability-floor claims. Source checkpoint formats are
unchanged. Unmanaged adapters keep their old record shape.

H0 preparation preflights opted-in providers and adapters declaring the static
`MANAGED_EXECUTION_RUNTIME` discovery reference under the effective child
environment and selected split/tasks. It installs nothing. Configuration,
payload, nested evaluation result, metadata and first point must agree before
publication/reuse. Curve-only donor reconstruction preserves identity. The helper
also derives the mean from per-task results when a raw evaluation omits `score`;
the actual creation/reuse CLI now prints a numeric score for that existing payload
shape. Artifact identity is exact, including physical paths; rebuilding the same
lock into a new instance does not silently reuse the old environment's score.

Validation retained at `/tmp/hg-runtime-lifecycle-20261105-ix_aotx5` covers 521
distinct passing cases across the broad and focused runs, including 89 new
controls, 38 passing subtests and three installed-wheel cases. One DSH shared-H0
test failed because this machine has no configured DSH root; the identical
failure was reproduced on clean `c2e73b52`. One old `smoke-stage1` record test
skipped because that historical run is absent. No full-suite success is claimed.
Raw failures/retries, case-to-report index, commands and JUnit remain preserved.

An authored Parquet control on a Python 3.13 platform without Arrow used its real
3.12 provider to create, share and resume H0. Republishing a fresh runtime instance
changed identity and rejected old lookup/reuse/resume without rewriting prior
records or checkpoint state. Real CostBench static/dynamic selected train tasks
likewise matched H0 preflight to actual service/scorer/metadata identities on a
platform without their packages; both controls were measured with zero invalid
tasks. They used fixture/mock actors, not inference or benchmark performance
measurement. The actual H0 CLI separately completed creation and reuse. A
baseline/current cache control showed an invalid runtime binding reused a cached
score on `c2e73b52` and became invalid without a cached score after this change.

Exact manifests, successful controls and logs remain outside the source checkout.
Temporary test environments, workspaces and the comparison worktree were cleaned.
No user dataset/runtime was replaced and no main merge was made. Identity still
covers only explicitly managed dataset artifacts, not a sealed OS, unmanaged
dataset dependencies or Harness candidate dependency preparation/lifecycle.

### Candidate-owned Python preparation and lifecycle (2026-11-06)

Harnesses may opt into `runtime={kind:python, python:X.Y,
requirements:requirements.lock}`. The new stdlib-only
`python_harness_runtime` manager shares inventory/atomic publication helpers with
dataset runtimes while keeping its own schema and trust boundary. A complete
flat exact lock is resolved and checked before wheel-only installation in a
sparse bubblewrap builder. Automatic candidate preparation is offline; explicit
`configure_harness --fetch` permits downloads, without Python downloads, sdists,
local/editable dependencies or install hooks. Installed metadata is read with a
trusted base `-I -S` interpreter, so package startup never runs on the host.

Preparation follows candidate staging and commit, once before reuse or
measurement. Source-only edits share dependency artifacts; lock changes prepare
another artifact. An append-only receipt records success/refusal and preparation
time, which enters wall time without consuming tokens or task trials. Failed
candidates retain source and remain unmeasured. Managed staging checks source
ownership before Git initialization writes `.gitignore`; a canary regression
verified that an escaping alias cannot modify an external file or destroy an
existing workspace. Runtime prefixes cannot overlap platform/candidate/work
trees. Existing generated local environments are excluded from source copies.

Native files actors start the exact bound venv with isolated startup, a trusted
source bootstrap, sanitized Python environment, read-only dependencies and
private HOME/temp/`.state`. Ignored channel/history/state paths are masked, and
exact admitted runtime mounts under private `/tmp` survive the hides without
revealing adjacent files. The platform interpreter projection is disabled for
this actor only; scorer/provider/service environments do not inherit actor
dependencies. Containers, observed/cooperative workers, agent package
projections, external runtime paths and unsandboxed launches are explicitly
unsupported. Legacy/Bun behavior and existing base Harness recipes are retained.
Validator static checks accept the new contract; both legacy smoke entrances
refuse it and direct callers to a sandboxed driver control.

`harness_runtime_identity` binds all owned source bytes (including gitignored
source), the dependency declaration and actual artifact. It stays separate from
fixed dataset identity because dependencies are legitimate candidate edits.
Cache and H0 check all recorded copies and live artifacts before reuse. H0 tools
also bind staged source, support explicit Harness selection, and hand an exact
managed binding to the driver. Train resume validates each checkpoint's own
source/artifact, while eval resume checks selected source and completed progress
before writes. Managed snapshots now naturally start at round 0 and use saved
source-only states, preserving the existing portable schema and parent chain.
Restoring after physical checkpoint deletion needs no H0 remeasurement or
dependency rebuild.

Parent validation covers 840 distinct passing cases, including 135 new controls,
offline installed-wheel checks and actual sandboxed actors/H0 CLI creation and
reuse. Later targeted validation covers automatic portable snapshots and the
installed managed modules. One DSH shared-H0 test failed identically on clean
`49a5e631` because this machine has no configured DSH root. Two explicit opt-in
provider controls were not enabled, and one historical `smoke-stage1` test skipped
because its record is absent. No full-suite success or benchmark performance is
claimed. Commands, raw failures/retries, JUnit, case index, source hashes and
selected control receipts are retained outside source at
`/tmp/hg-python-candidate-20261106-vl_hpflt`.

This phase does not migrate all base Harnesses or seal system libraries. Old
dependency instances must remain available while runs still reference their
exact physical manifests. No real inference, main merge or user dataset/runtime
replacement was performed.

### Correct candidate source identity and checkpoint eval provenance (2026-11-06)

The three review findings against `f1920ebd` are fixed. Source fingerprints now
hash a canonical, schema-tagged list of paths, executable flags and content
digests. Arbitrary binary content cannot impersonate another file record.
Bindings use version 2; ambiguous version-1 bindings are refused before artifact
admission, H0/cache reuse or resume. Historical records are not rewritten or
silently upgraded. Dependency artifact schema, recipe and keys are unchanged,
so their valid prepared contents remain reusable for new measurements.

The managed actor bootstrap adds the validated entrypoint directory before the
Harness root. Nested sibling imports have ordinary script precedence, root
imports still work, and `-I -B`, environment sanitization and task isolation stay
in force. Checkpoint eval now requires the selected managed curve point's exact
original binding and admits its retained source/artifact before preparation or
eval record writes. Missing, null, malformed, wrong-round or damaged provenance
cannot select a newer publication or fall back to the initial run binding.
Legacy unopted checkpoints retain optional curve provenance.

Validation covers 375 distinct passing cases, including 17 new regressions,
38 passing subtests, installed-wheel checks and actual bubblewrap actors. The
binary framing control keeps the Git tree unchanged while splitting ignored
binary content into executable Python source: its old binding is refused, its
old cached score is unused, and a new binding reuses dependencies while measuring
the changed answer. Nested-entry controls verify sibling precedence, root/wheel
imports and ambient-path isolation. Lifecycle controls publish artifact B after
training with A, retain A for valid eval, and refuse missing/damaged provenance
before prepare or measurement. V1 H0/resume controls preserve all prior bytes.

One historical `smoke-stage1` test skipped because its record is absent. An
extended eval-coverage test has an existing mock that accepts only positional
arguments while the baseline caller passes identity keywords; its failure was
reproduced on clean `f1920ebd` and left outside this fix. Initial packaging checks
also exposed missing build tools/inactive Python PATH in the borrowed test
environment; an offline task-owned tools venv passed the corrected invocation
without modifying the user's environment. Raw attempts, JUnit, source hashes,
commands, independent review and cleanup receipts are preserved at
`/tmp/hg-candidate-review-fixes-20261106-sqfmyxij`. No real model inference or main
merge was performed.

### Enforce source directories that survive file-only snapshots (2026-11-06)

The empty-directory finding against `81be3510` is addressed by a single source
policy for managed Python: every non-generated directory must be a parent of a
retained owned file. Empty directories and directories populated only by filtered
state, environments or bytecode are refused. The common source fingerprint check
already guards static validation, Git staging, preparation, direct admission,
cache/H0 reuse and checkpoint recovery, so unsupported input cannot reach actor
measurement. Staging refusal preserves an existing workspace and the input tree;
initial and method-produced invalid sources remain retained, unmeasured evidence.
Platform state and filtered local environments keep their separate treatment,
and unopted Harness copying is unchanged.

A retained marker such as `.gitkeep` makes directory presence explicit. Such a
file is hashed and snapshotted even when Git ignores it. Supported directories
therefore round-trip through the existing snapshot schema as file parents;
no directory-only artifact format or implicit source mutation is introduced.
Source schema and binding version are 3. V1 and v2 histories are preserved but
cannot be upgraded for score reuse or resume, because v2 did not certify this
directory policy. Dependency artifact schema/keys remain unchanged and their
valid contents can still serve new source bindings.

Validation covers 320 distinct passing cases and 38 passing subtests. Eighteen
new or extended cases cover empty/generated-only directories, direct preparation
and H0 lookup, refusal before workspace deletion or builds, initial/candidate
unmeasured handling, v2 rejection, and a real bubblewrap/cache/snapshot control.
The positive control restores an ignored directory marker with the original
binding and answer; deleting the marker is refused while the directory is empty,
and removing the directory requires a new binding and fresh score while reusing
the dependency artifact. Packaging, imports, file snapshots and ordinary H0/
resume regression checks also pass. Commands, JUnit, selected control receipts,
source hashes and cleanup evidence are retained outside the checkout at
`/tmp/hg-empty-source-fix-20261106-ducnxdwi`. No real model inference or main merge
was performed.

### Migrate MiniAgent to a managed Python environment (2026-11-06)

MiniAgent 0.2.0 is the first shipped base Harness using the managed native
`files` route. Its manifest selects CPython 3.12 and a complete 20-package wheel
lock. The three direct requirements remain the resolver input; the complete lock
is included in the component map, adapter provenance and installed wheel. All
19 pinned upstream files and their original hashes are unchanged.

The adapter launches its native worker with the same bound interpreter and
`-I -B`, and removes ambient Python import paths from the child environment.
It no longer expects a platform dependency overlay. The existing SDK, native
tool, submission, usage and timeout controls now run inside real bubblewrap
sandboxes with prepared wheels. Local scripted HTTP endpoints use the existing
Unix model route; mock cases use HTTPX MockTransport. Neither invokes a model.

Actual native-tool probes verify that the worker and shell child use the exact
bound Python, dependency writes are denied, platform packages and the platform
venv are absent, and concurrent tasks have separate HOME and state. A separate
post-exit scorer sees its own private input and cannot import or read actor
dependencies. Source edits reuse the dependency artifact; an incomplete native
dependency lock is refused before installation or measurement.

The shipped Harness also passes H0 creation and unchanged reuse, shared-H0
training, identical-candidate reuse, and training continuation after deleting
physical round-0/1 checkpoints. The continuation restores source-only snapshots,
keeps old points unchanged and extends their parent chain. Eval measures the new
retained checkpoint with the exact same artifact; eval resume reconstructs the
point from unchanged progress without executing completed tasks again. These
demo scores are valid mock zeros, not benchmark performance measurements.

Final lanes passed 56 migration/packaging cases and 218 focused regressions,
with no skips. Six OpenClaw-named cases and 22 broader preset/H0 cases were
explicitly deselected in those lanes. A separate four-task preset probe also
passes the managed sandbox route. The initial mixed lane's OpenClaw runtime-not-
prepared failure was reproduced on clean `93d35f82`; it is outside this migration.
No full-suite or live-model success is claimed. Commands, JUnit, raw attempts,
selected runtime/curve/progress evidence and cleanup receipts are retained at
`/tmp/hg-miniagent-managed-20261106-67rlf40b`.

Preparation was entirely offline, using cached wheels and a task-owned tools
environment with no OpenAI, HTTPX, PyYAML or requests installed in the platform.
Other base Harnesses retain their declared setup. Container/exec, workers and
unsandboxed managed actors remain unsupported. No user environment, real
dataset, pinned scorer or main branch was modified.

### Extend QA provider isolation to TriviaQA and HotpotQA (2026-11-06)

TriviaQA and HotpotQA now explicitly declare the QA provider, CPython 3.12 and
`pyarrow==23.0.0`, alongside GSM8K. Registry loading uses their prepared provider
artifacts; missing preparation cannot fall back to platform packages. Their
runtime declarations ship in the installed wheel. Download metadata names the
separate preparation step, including HotpotQA's previously undeclared Arrow
dependency, and the generated inventories reflect both recipes. Existing data
locations remain usable after preparation. Historical measurements are preserved
and cannot acquire the new runtime identity by implicit score reuse or resume.

Authored Parquet controls use a fresh platform venv without pip, Arrow or model
SDKs and reject even attempted platform imports of the two adapters. They check
TriviaQA's original row indices under its 2,000-row systematic train sample,
alias preservation, HotpotQA's private supporting facts, selected task
declarations and independent runtime receipts. Static download/doc inspection,
target-environment package checks and loading checks work without putting Arrow
in the platform. Missing runtime, missing data and corrupt data remain distinct;
corruption still fails lint with `--allow-unprovisioned`.

Actual sandboxed MiniAgent controls score one authored task for each dataset
using MockTransport and the ordinary QA scorer. Runtime identities identify both
the dataset provider and candidate artifact. Native-tool probes confirm that
Arrow, the provider artifact and private gold are absent from the actor. The
installed-wheel controls also execute each QA scorer without Arrow, including
GSM8K, while pinned scorer code, data revisions and metrics remain unchanged.

Validation covers 309 distinct passing tests and 38 passing subtests, with no
skips. The first lane passed 298 cases and exposed a missing file-presence marker
in the new installed HotpotQA fixture; adding that authored marker preserves the
patched-reader test contract. The packaging/import rerun passed all 19 cases and
38 subtests. Generated DATASETS.md matches its source declarations. Arrow was
installed only in task-owned provider and test-writer environments; the empty
platform controls stayed separate. Commands, raw attempts, JUnit, selected
controlled traces/receipts and cleanup evidence are retained at
`/tmp/hg-qa-provider-migration-20261106-cwxsv3m1`.

The remaining base Harness inventory is documented: loop/miniswe retain both
files and exec, and external CLI/Bun Harnesses keep their explicit preparation
contracts. Those routes need their own migration decisions. This phase changes
neither their supported environments nor the managed native-files boundary. All
new controls used cached dependencies and authored data; no released benchmark
trial, real model, shared runtime replacement or main merge was performed.

### Route QA controls and host inventories through providers (2026-11-06)

The public-entry finding against `fb8f32d2` is fixed for all three opted-in QA
datasets: GSM8K, TriviaQA and HotpotQA. The shared QA oracle/noop command obtains
one selected task and its declarations through the prepared provider, in both
local and platform modes. The provider evaluates reference submission/evidence
builders and callable noop expectations; the platform receives a data snapshot
and runs the existing CLI, scorer and sandbox. Ordinary actor tasks never receive
this private reference snapshot. Unknown control ids now retain their diagnostic
in a checked provider response and fail before workdir creation.

The options API, host split inventory and dataset classifier obtain counts,
split metadata, environment/verifier kinds and a sample public setup through a
separate inventory operation. It avoids constructing the entire verifier pool;
the ordinary catalog still does not access execution declarations. Static split
rendering reads declared QA policy from trusted source without starting the
provider, importing its adapter or requiring Arrow/runtime preparation. Existing
generated split and dataset inventories remain unchanged and pass their checks.
The installed wheel now includes the oracle control Harness's manifest.

Empty-platform controls reject attempted adapter/Arrow imports and exercise all
three datasets' local and sandboxed oracle/noop modes, discovery/counts, missing
runtime and unknown-id behavior. Installed-wheel controls also execute oracle
and noop without Arrow. Protocol controls cover private-reference separation,
single-task selection, malformed responses, reference identity and finite noop
expectations. Authored TriviaQA rows now include their canonical values among
their aliases so they satisfy the oracle contract as well as the mock alias arm.

The passing focused lanes cover 252 distinct tests and 38 subtests, with no skips.
Broader legacy classifier checks stop at AppWorld's existing external storage
requirement; the failure was reproduced on clean `fb8f32d2`. Configuring an empty
root instead exposes its absent cryptography dependency. Additional released-
data 2Wiki checks also need external storage, reproduced on the same baseline;
they do not establish a regression or a full-suite pass. Their raw failures are
retained separately. No dependency was added to the user's platform to bypass
these prerequisites. Commands, JUnit, baseline comparisons, controlled traces,
source hashes and cleanup receipts are retained at
`/tmp/hg-provider-entry-fix-20261106-7w4qjgem`.

Dataset dependency keys, artifact schemas, pinned scorer code and metric semantics
are unchanged. All successful task controls used authored data, cached wheels
and no model inference. No main merge was performed.

### Give the default Loop Harness managed files and exec environments (2026-11-06)

Loop now declares complete OpenAI SDK locks and an explicit managed Python
contract for both environment kinds. Native files tasks use the existing CPython
3.12 artifact. An exec image with CPython gets a separately prepared venv with a
read-only copy of its own executable and a lock selected by Python minor version;
its standard and system libraries remain image-owned. An image explicitly lacking
Python uses the exact bound native artifact after compatibility admission inside
that image. Broken discovery, unsupported versions and incompatible artifacts fail
explicitly. Preparing an image bypasses its entrypoint and never mounts candidate
source; actor execution preserves the image's startup behavior. Both mounted task
directories and container-state tasks have controls for setup before startup.

Candidate receipts bind native and image artifacts, pinned images, interpreter
bytes, source and all locks. Checkpoint/cache admission cannot install, repair or
select a newer publication. Initial evaluation of an existing checkpoint may
explicitly prepare additional target images from its unchanged locks, while
retaining its original native artifact, image artifacts and evaluated-from
identity. Resume remains strict. H0 selection and configuration share the selected
environment contract. Actor dependencies are read-only and excluded from scorer
containers and committed container-state snapshots; task HOME, temporary files
and state are private. A snapshot-resume control also fixes comparison of a
restored external candidate against its generated source-ignore receipt.

The focused validation union contains 540 distinct passing cases and 38 passing
subtests. One legacy overlay-import case skips because no old-style overlays were
prepared; managed SDK imports are exercised separately. Coverage includes an
empty platform with neither pip nor SDK/Arrow, actual native SDK requests to an
authored local endpoint, five native/image/fallback placements, private scoring
inputs, repeated-task state, image entrypoints, H0 reuse, training/cache/snapshot
resume, cross-image checkpoint evaluation and evaluation resume. Actual image
controls used locally available CPython 3.12.8 slim and Python-absent Ubuntu 24.04;
native controls used CPython 3.12.16. Other declared Python minors have lock
resolution checks, not measured image execution. Container SDK controls use
HTTPX MockTransport and authored tasks; container model-gateway connectivity and
real inference are not established by these results. Plain-container lifecycle
controls supply actual kernel observations without widening production backend
admission policy. No full-suite, released-benchmark or live-model success is
claimed. Raw attempts, commands, JUnit, final case inventory, selected receipts
and cleanup evidence are retained at
`/tmp/hg-loop-managed-20261106-o2dntxga`.

The Quick start explicitly prepares Loop with `--fetch`; offline preparation
uses cached wheels, and checking/listing an existing binding installs nothing.
The platform needs neither pip nor the SDK for managed Loop. Unopted Harnesses
retain their existing preparation paths. No user environment, pinned scorer,
shared service or main branch was changed.

### Preserve task-image Python and tool configuration (2026-11-06)

The P1 against `0d6e96af` is fixed: activating the actor venv through container
environment variables also changed Loop's task shell, hiding image packages and
custom tools. Container actors now retain the task image's PATH and Python
configuration, including an existing task venv. Their own absolute bound
interpreter and `-I -B` startup continue to exclude ambient Python imports. The
ordinary container environment allowlist still prevents host PATH/Python/venv
configuration from being forwarded. Native files retains its managed interpreter
path. The no-Python image fallback supplies the actor without implicitly adding
Python to task shell commands. Harness helpers needing actor packages must use
`sys.executable` with isolated startup.

Four new authored-image controls cover system Python and an existing image venv,
each under mount and container-state execution. Independent positive controls
establish that image-installed modules, PYTHONPATH modules and a custom PATH tool
work before actor preparation. The actual Loop then runs the same tools and
imports using both python and python3, with successful shell exits and fresh
scoring. Its SDK remains in the read-only actor artifact, task modules remain
absent from actor imports, and task/scorer imports cannot see the SDK. Host
environment poison values do not replace image configuration. The earlier tests
no longer require container task commands to select the actor interpreter.

Validation passed all 249 focused cases with no skips: 36 managed Loop/placement
controls and 213 native-actor, H0, lifecycle, snapshot/resume, dataset identity and
installed-wheel checks. Native controls used CPython 3.12.16; image controls used
locally available CPython 3.12.8 slim, its authored derivatives and Python-absent
Ubuntu 24.04. Dependencies came from offline caches. SDK transports used authored
local responses or MockTransport; no real inference, released benchmark or
full-suite result is claimed. Commands, JUnit, traces, positive controls, selected
runtime manifests and cleanup receipts are retained at
`/tmp/hg-loop-task-env-20261106-ghfg8iq0`. No main merge or shared service change
was performed.

### Unified cold-start acceptance of migrated environments (2026-11-06)

The migrated profiles passed a unified acceptance run on rebased `9415a714`.
The README's seeded platform venv, editable installation and sandbox check ran
with a new HOME and initially empty installer cache. Each of the three QA
providers, CostBench execution runtime, MiniAgent, native Loop and Loop image
preparation then used its own initially empty uv cache and fresh artifact locations
in external stores.
The actual preparation CLIs downloaded dependencies; no wheelhouse or previous
publication supplied them. Missing-runtime checks preceded preparation. Provider
preparation initially reported data_missing after installing dependencies; authored
Parquet was subsequently written by the provider interpreter and all three
loading checks passed. The platform stayed free of Arrow, SDK, HTTPX, PyYAML,
requests and numpy. A separate seedless platform exercised managed execution.

The 36 distinct completed integration cases cover QA rows/private inputs and
public checks, GSM8K plus Loop H0/cache/snapshot train and eval resume, MiniAgent
worker/state/scorer isolation and lifecycle, and Loop's native/image/fallback
placements, task Python and tool configuration, entrypoints and exact-binding
cross-image evaluation. Controls for the original declarations reused the
cold-prepared artifacts; authored image variants built from the caches populated
in this acceptance run.
Explicit republishing in the lifecycle control kept prior bindings intact.
CostBench static/dynamic controls used authored no-op cases with the unchanged,
digest-verified runtime/scorer source; both produced expected measured zeros.
They checked actual service/scorer interpreters, read-only package origins and
independent episode state. They do not validate released task performance or
dynamic intervention execution.

Read-only provider/Harness checks passed with an installer that refuses execution
and a separate absent cache: no installer calls, cache creation or original
publication changes occurred. Seven serial corruption controls changed only an
owned artifact's pyvenv.cfg, confirmed rejection without repair, restored its
exact bytes/mode and verified the same identity again. A cold wheel build also
installed into a new seedless platform outside the checkout: the installed CLI,
all three providers and both Harness declarations worked without pip or their
runtime packages in that platform.

The final case union has no failures or skips. Initial acceptance-script mistakes
(an overwritten preparation receipt field and an absent authored work directory)
are retained separately; only corrected completed controls count. No product
code change was needed. Python 3.12.16, uv, bubblewrap and local CPython 3.12.8 slim
and Python-absent Ubuntu 24.04 images were prerequisites. This measures cold Python
dependency preparation, not OS/toolchain installation or container image pulls.
Model responses and task data were authored; no real model or released benchmark
trial ran. Commands, receipts, original attempts, JUnit, source provenance, runtime
manifests and cleanup records are retained at
`/tmp/hg-cold-accept-20261106-s25tt30e`.

### MiniSWE managed Python migration (2026-11-06)

MiniSWE now declares managed native CPython 3.12 and ordinary container exec
placements for CPython 3.10 through 3.13. Complete wheel locks retain upstream
mini-SWE-agent 2.4.6 and LiteLLM 1.75.9. The actual mini CLI, prompts, planning
loop, submission sentinel and trajectory conversion remain in use. Its subprocess
uses the bound interpreter with isolated startup and the distribution's absolute
mini.yaml, excluding task-owned Python packages and configuration. Task shell
commands retain image Python, existing venv, PYTHONPATH and custom tools. Mini's
HOME, global configuration and scratch remain private to each task. The CLI's
provider-qualified model name is recorded separately from the caller's model
identity so H0 reuse does not invent a model conflict.

Cold preparation used fresh external stores and initially empty installer caches
for native dependencies and local image placements. Actual CLI/LiteLLM requests
went to an authored local HTTP server. Native files and CPython 3.11, 3.12 and
3.13 images, plus Python-absent Ubuntu 24.04 fallback, passed both mount and
container-state controls. Authored image variants exercised system and existing
venv task Python, PYTHONPATH-only modules and custom tools. Checks covered
read-only dependencies, actor/task/scorer import separation, private verifier
inputs, repeated task state, H0/cache reuse, snapshot train/eval resume and exact
historical bindings after republishing and extending to a new evaluation image.

The final focused union passed 164 distinct tests and 38 subtests, with no failures
or skips: 18 managed integration cases, 27 MiniSWE compatibility cases and 119
runtime/package/architecture regressions. Three existing vendored SyntaxWarnings
remain. A wheel installed into a fresh seedless platform ran the actual CLI
outside the checkout. Platform Python had no pip, MiniSWE, LiteLLM, SDK or Arrow;
run/check admission invoked no installer. Missing-store admission created nothing.
Four serial corruption controls changed only owned pyvenv.cfg files, verified
rejection without repair, then restored exact bytes/mode and the same identities.
Mini's transitive Arrow belongs to the actor, independently of provider Arrow.

Python 3.12.16, uv, bubblewrap and preimported images were prerequisites; this
does not measure OS installation or image pulls. The CPython 3.10 closure was
resolved but not executed in an image. No real model, released benchmark or full
test-suite result is claimed. Initial authored assertion/fixture mistakes are
retained separately and excluded from the final union. Commands, logs, JUnit,
source provenance, original test trees and runtime manifests are retained at
`/tmp/hg-miniswe-managed-20261106-86ug1bp0`.

### MiniSWE configuration paths containing equals (2026-11-06)

An external runtime directory containing `=` prepared and admitted successfully,
but mini-SWE-agent 2.4.6 interpreted its absolute mini.yaml argument as a
configuration override. A new actual-CLI regression reproduced the missing
configuration failure before the fix. Managed startup now opens the bound
configuration directory read-only and inherits its descriptor into the unchanged
`python -I -B -m minisweagent.run.mini` process. The CLI reads
`/proc/self/fd/<descriptor>/mini.yaml`, which keeps its filename and contains no
operator path syntax. This uses the existing Linux sandbox's proc filesystem.
The descriptor closes on exit or failure; trace identity retains the actual bound
configuration path. Legacy unopted startup and pinned dependency files are unchanged.

All 54 selected tests passed with no skips: 18 actual-CLI placement/task-image
controls, 27 MiniSWE compatibility checks and nine installed-package checks.
Five new cases exercise runtime paths containing both `=` and spaces: native
files, image Python mount/container-state and Python-absent fallback in both
states. They also retain task-owned package/configuration poison and private-input
isolation checks. Normal paths cover image CPython 3.11, 3.12 and 3.13; authored
image system Python/venv, PYTHONPATH and custom tools remain available to tasks.
Five other managed readiness/lifecycle cases were deselected. The initial package
test tools lacked setuptools; an owned temporary tool venv supplied the build
prerequisites and the package checks then passed. That setup error and the expected
pre-fix regression failure are retained separately from the passing union.

Only synthetic tasks and local scripted responses ran, with preimported images;
no real model, released benchmark, CPython 3.10 image or full-suite validation is
claimed. Commands, JUnit, original attempts, traces and source/runtime provenance
are retained at `/tmp/hg-miniswe-config-20261106-_civffdo`.
