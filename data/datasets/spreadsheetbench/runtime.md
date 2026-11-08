# SpreadsheetBench execution dependencies

Dataset discovery, loading and source identity use the platform standard library.
They neither import openpyxl nor install packages. Fetching the release no longer
installs a platform or actor workbook library.

Prepare the verifier explicitly:

```sh
python tools/prepare_dataset.py --dataset spreadsheetbench --runtime execution
python tools/prepare_dataset.py --dataset spreadsheetbench --runtime execution --check
```

The declaration pins Python 3.12, the upstream reader openpyxl 3.1.2 and its complete
Python dependency closure (et-xmlfile 2.0.0). The existing managed runtime artifact
records interpreter, installed byte inventory and requirement identity. Each
selected verifier captures an immutable binding and launches with `-I -B`;
missing, changed or incompatible artifacts fail rather than use platform Python.
No actor editing library is promised by this verifier dependency declaration.

Golden workbook and task metadata retain the task-bound private scorer input
inventory. Public source identity hashes current file bytes and does not require
local Git history. The trusted scorer loads its declared scorer-input helper from
source bytes beside the staged entry, never actor cwd or unchecked Python caches.

## Formula backend

Prepare an existing native installation explicitly into a private directory:

```sh
python -m data.scaffolds.spreadsheet_runtime.native prepare \
  --soffice /usr/bin/soffice --python /usr/bin/python3 \
  --directory /absolute/private/cache/spreadsheet-native
export HG_SPREADSHEET_NATIVE_MANIFEST=/absolute/private/cache/spreadsheet-native/manifest.json
```

Verifier selection validates that manifest and binds its exact contents as a
private scorer input. The worker receives `--native-manifest` explicitly; it does
not rely on environment forwarding. Missing configuration fails with prepare
instructions and readiness reports unknown. There is no host PATH fallback.
The native helper itself is an explicitly staged, trusted source input. Workers
run that helper directly with `-I -B`, never an unverified external launcher.

Formula conversion validates the private manifest again and executes LibreOffice
inside bubblewrap with private HOME, profile, scratch and network/PID namespaces.
Only the selected workbook and fresh output directory are mounted for conversion;
actor cwd, evaluator private inputs, model/API credentials and host sockets do not
reach the engine. Macro and external-workbook link packages are rejected as
unmeasured. Native failure leaves no reward. Formula-free workbook comparisons do
not launch LibreOffice, although selected verifier identity still commits to its
native configuration. Bound measured mismatches exit zero with reward zero to
satisfy the managed command result contract; legacy manual mismatch exit codes
remain unchanged.

The existing comparison and fresh-cache validation are retained: private copies
lose stale formula caches, Calc recalculates, and the scorer verifies new caches
before comparing. The actor's submitted bytes are never rewritten. A result may
not name a task-bound private input such as the golden workbook.

This deployment is a **verified host installation**, not a hermetic or independently
provisioned native closure. The manifest inventories selected installation bytes,
resources, fonts and resolved libraries; host OS/loader, runtime discovery and
newly installed host resources remain dependencies. Evidence records the native
manifest digest, version and scope limitations. The public scoring profile also
hashes native helper source bytes. No native binaries, libraries or fonts are
redistributed by this adapter; their original licenses apply.

SpreadsheetBench v2 has a separate evaluator and is not covered by this v1 scoring
contract. Its shared native backend has the same verified-host scope limitation.

## Actor editing Python

Both SpreadsheetBench adapters now select `ENV.task_python` with an `actor` role
binding to their explicitly prepared execution dependency artifact. This reuses
the published package inventory; it does not install anything into the platform,
add packages to the Loop SDK, or redistribute scorer code or workbooks. Prepare
with the existing `tools/prepare_dataset.py --dataset <name> --runtime execution`
command. Actor tools and the verifier can share dependency bytes because those
bytes are mounted read-only and re-admitted before each role starts.

In sandboxed files tasks, `python` and `python3` on the task shell PATH now include
openpyxl 3.1.2. The harness still starts with its separate absolute interpreter;
its SDK imports and model credential route are unchanged. Only the starting
workbook is staged for the actor. Neither native manifests, artifact manifests,
private golden workbooks nor scoring sources are actor inputs. No platform source
tree is added to the actor import path. Exec/container tasks, workers and observed
backends are unsupported for this task-Python field and fail explicitly.

The environment binding participates in verification_context and the private
runtime identity's actor role. Launch, cache, H0, resume and identical-round reuse
recheck its recorded artifact inventory without consulting a newer discovery
index. Existing records remain readable; records predating the actor binding do
not prove this editing environment and require a new run rather than reuse.
Public runtime reports show only dataset, runtime_id, actor role and read-only
status. Method round/history summaries omit binding paths, task associations and
plan digests; spreadsheet native/context manifests are omitted from method task
pages. Private run records retain the full bindings for admission and comparison.

Validation uses authored workbooks only: the unchanged default Loop received
local fake HTTP tool actions, edited them with the real task openpyxl package,
and reached the actual v1/v2 native verifiers with correct=1 / wrong=0 controls.
The platform control venv had no pip, openpyxl, OpenAI SDK or httpx before or after
execution. Task probes observed read-only dependency mounts and denied package
rewrites, hidden original/staged gold, hidden manifests and hidden scorer source.
These controls establish the tested Linux task-tool path, not benchmark scores,
container support, portability or a hermetic native deployment.

Native global-site compatibility: trusted task selection embeds only original-pin
UNO entry-file snapshots in private `native-runtime.json`. The nested native
namespace restores those exact files into an otherwise empty, read-only global
site directory. All unrelated system Python packages remain hidden; no global
site exemption, platform pip install, or changed historical native pin is used.
Snapshot bytes are checked against the original native SHA256 before use.
