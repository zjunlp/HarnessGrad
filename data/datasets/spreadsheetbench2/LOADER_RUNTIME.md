# SpreadsheetBench-2 execution and native calculation

Loading task metadata uses the platform standard library. The official evaluator
and its upstream workbook-opening helper require openpyxl==3.1.2 and tqdm==4.66.4
(`requirements.txt`, matching the pinned provenance). The execution lock adds
openpyxl's et-xmlfile dependency, pinned at 2.0.0. Neither official source imports
Pillow. This runtime is distinct from SpreadsheetBench v1 and other native scorers.

Prepare the Python artifact explicitly, outside the checkout:

```sh
python tools/prepare_dataset.py --dataset spreadsheetbench2 --runtime execution --python 3.12
python tools/prepare_dataset.py --dataset spreadsheetbench2 --runtime execution --check --json
```

Prepare LibreOffice/UNO separately, using an existing explicit native deployment:

```sh
python -m data.scaffolds.spreadsheet_runtime.native prepare --soffice /usr/bin/soffice --python /usr/bin/python3 --directory /absolute/private/cache/spreadsheet-native
export HG_SPREADSHEET_NATIVE_MANIFEST=/absolute/private/cache/spreadsheet-native/manifest.json
```

Only new trusted task selection reads that environment setting. The adapter
rehashes native resources and captures canonical manifest bytes into private,
task-bound scorer inputs. Workers receive no ambient native manifest environment.
They use their private authenticated manifest and staged native helper, never the
external deployment's unbound launcher. `runtime-context.json` binds the selected
managed Python runtime ID, native manifest SHA256, and exact source SHA256 values.
The platform admits the managed interpreter and runtime artifact before launch;
the check validates executing source, source copies, interpreter path, native
files, upstream provenance and Python package versions again. A self-described
native manifest is consistency evidence, not an authorization token: selection
and private input custody are required. Do not select actor-created manifests or
actor-writable native deployments.

The check starts with `-I -B` and compiles only staged helper source, never imports
from the actor cwd or reads its Python bytecode caches. Native UNO runs under the
separately inventoried system interpreter and UNO binding; scorer openpyxl/tqdm
remain in the managed execution artifact. The selected tqdm source is staged into
the private UNO sandbox. The native manifest inventories the UNO interpreter and
binding, native resources/libraries/fonts; its OS and Python standard library
remain host compatibility dependencies. This is not a hermetic native closure.

Only the specific workbooks being recalculated enter the private bubblewrap
namespace. No actor cwd, evaluator working tree, host HOME, credentials or host
network is exposed to LibreOffice. Its UNO client and listener share the same
private network namespace. Existing formula calculation, iteration count 100,
epsilon 0.0001, save filter, and official regression/modification accuracy remain
upstream. Load policies disable macro execution and external document updates;
macro-bearing or external-workbook-link inputs are explicitly unmeasured.
Missing native resources, unsupported active content or dependency/runtime failure
produce measured:false and no reward. A completed official mismatch/missing output
retains official zero. Symlink and hardlinked actor outputs are refused; the
accepted file is copied with O_NOFOLLOW into owned scratch before native loading.

Read-only `provision_status()` requires prepared data, admitted managed execution
artifact and verified explicit native deployment. It never installs or launches a
workbook. Missing dependencies report an unknown/unavailable state and actionable
prepare commands. No Python-only installation is treated as formula/UNO support.

No native software is redistributed. See the shared native runtime README for
LibreOffice/component/font licenses and isolation limits. Official v2 scorer
license uncertainty remains recorded in PROVENANCE.json; this change asserts no
new redistribution license.


Actor editing tools now come from the same explicitly prepared dependency-only
artifact, captured with an independent actor role in `ENV.task_python`. Its
read-only venv supplies `python`/`python3` with openpyxl 3.1.2 to task shell tools;
the default Loop keeps its own absolute SDK interpreter. No scorer source, golden
workbook, artifact manifest or native manifest is staged for the actor. This route
requires sandboxed files without workers or observed/container backends. The
private environment/context and runtime identity include actor proof; missing or
changed artifacts fail with the existing explicit execution-prepare command.
Previous measurements without this actor proof require a new run for reuse.
See `../spreadsheetbench/runtime.md` and the task-Python section of
`docs/dataset-runtime-interfaces.md` for the full role and visibility contract.

Native global-site compatibility: trusted task selection embeds only original-pin
UNO entry-file snapshots in private `native-runtime.json`. The nested native
namespace restores those exact files into an otherwise empty, read-only global
site directory. All unrelated system Python packages remain hidden; no global
site exemption, platform pip install, or changed historical native pin is used.
Snapshot bytes are checked against the original native SHA256 before use.
