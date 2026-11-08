# Spreadsheet native runtime

`native.py` inventories an explicitly selected existing LibreOffice deployment.
It requires bubblewrap for all workbook processing. It does **not** install packages, download binaries, stop existing listeners, or
claim a Python virtualenv supplies LibreOffice/UNO. Prepare into a private cache:

```sh
python -m data.scaffolds.spreadsheet_runtime.native prepare \
  --soffice /usr/bin/soffice --python /usr/bin/python3 \
  --directory /absolute/private/cache/spreadsheet-native
export HG_SPREADSHEET_NATIVE_MANIFEST=/absolute/private/cache/spreadsheet-native/manifest.json
python -m data.scaffolds.spreadsheet_runtime.native check --manifest "$HG_SPREADSHEET_NATIVE_MANIFEST"
```

Preparation requires a native deployment with a resolvable `soffice.bin`, a Python
interpreter that really imports `uno` and `pyuno`, `ldd`, and fontconfig `fc-list`.
The manifest records SHA256 identities of the complete LibreOffice installation
(including filters, registries, bundled fonts), Python executable, UNO binding,
loader-resolved ELF dependencies, fontconfig configuration and discovered font
files. Missing libraries or UNO fail preparation. Every check rehashes recorded
files; callers must treat exceptions as unknown/unmeasured, never as zero reward.

This is a **verified host installation**, not a copied or hermetic native closure.
Host OS, loader resolution, Python standard library, added fonts/configuration and
dynamic plugin lookup remain host dependencies. Pinning existing file hashes does
not prohibit discovery of newly added host files. Do not describe this manifest as
an offline pristine scorer, container, or independently provisioned LibreOffice.
A truly portable closure requires a separately reviewed distribution artifact and
OS/loader/font closure; this helper deliberately makes no such claim.

`require_runtime()` validates `HG_SPREADSHEET_NATIVE_MANIFEST` and returns
`soffice` (private launcher), `soffice_binary`, `program_dir`, and `python`.
`runtime_env(record, scratch)` creates private HOME, XDG and temporary directories
from a minimal environment (fixed locale/PATH and selected UNO paths). API keys,
model credentials, Python overrides and actor environment never reach native LO,
its UNO interpreter, or prepare-time native probes.

Task selection also snapshots the already pinned native UNO Python entry files
(`uno.py`, `unohelper.py`, or `pyuno` extension when located directly in a global
site directory) into base64 `source_snapshots` inside the existing private native
manifest. Snapshot capture uses O_NOFOLLOW and requires ordinary single-link files;
decoded bytes must match the original `files` SHA256 exactly. It does not add new
package pins or edit an existing host manifest. A missing/unpinned required helper
remains unknown and requires explicit preparation of a new deployment identity.

This allows the verifier to keep all unrelated global Python packages hidden.
Read-only native checking validates those bound snapshot bytes in memory without
reopening a masked host global-site path or writing any file. Only when building
the owned native sandbox are component files materialized under digest names. The
nested sandbox constructs an empty site directory, binds those individual files
back at their original UNO import paths, then remounts that directory read-only.
The `/work/native-components` alias is separately read-only too. No entire global
site directory is copied or exempted from the platform's package privacy mask.
The snapshots stay within the existing task-bound `native-runtime.json`; they are
not projected into actor or method runtime inputs. Manual prepare/check without
snapshots keeps its original host-file verification behavior and diagnostics.

Workbook processing runs in bubblewrap with a fresh user/PID/network namespace,
private `/tmp`, no host sockets, no user HOME, and no evaluator/actor working
directory. Read-only mounts contain OS runtime resources (`/usr`, library trees,
font configuration and recorded native resources); these are public host runtime
resources rather than a minimal closure. Only the selected input single file is
mounted for CLI conversion and a fresh dedicated output directory is writable.
V2 copies only the workbooks to recalculate into an independent scratch mount;
its real tqdm package and upstream helper are staged there. The LibreOffice UNO
listener and Python client execute in the same private network namespace, so the
upstream socket transport remains available without exposing a host listener.

Macro-bearing workbooks and external-workbook link packages fail as unknown;
they are never silently scored after link removal. UNO loads additionally request
NEVER_EXECUTE macros and NO_UPDATE external documents. The private LO profile
sets maximum macro security. This restricts active input behavior; unsupported
external-link/macro benchmark cases require separate review. Internal formula
`calculateAll`, iterative settings, save filter and official metric are retained.
Network-dependent formulas cannot access host/external services. No raw UNO error
or output (which could contain workbook values) is returned on conversion failure.

The standalone copied launcher validates identity before execing bubblewrap; it
preserves caller PID ownership. CLI private scratch is created under
`/tmp/hg-lo-convert-sandbox-*`, retained for owner-managed cleanup after process
exit. Caller-supplied LO profile paths are replaced by a profile inside that
sandbox, never mounted from host. Output must be an empty dedicated directory.
The caller remains responsible for timeout cleanup of its own conversion; the
bubblewrap PID namespace and `--die-with-parent` bound native process lifetime.
V2 sandbox scratch is cleaned after each call. No unrelated process is stopped.

V1 can consume `soffice` as its existing conversion executable; stage this
standalone helper/manifest through trusted scorer provisioning, never import code
from an actor working directory. The v2 adapter calls
`isolated_uno_recalculate(record, trusted_upstream_source, workbook_paths)`.

No native binary, library, font or workbook is redistributed by this directory.
LibreOffice is MPL-2.0 (with component-specific licenses); the selected host build,
Python/UNO, system libraries and fonts retain their own licenses. The inventory is
identity evidence, not a license grant or software bill of materials with audited
license attribution. Review distribution licenses before copying any native closure.

Validation on 2026-11-07: host LibreOffice 7.3.7.2 30(Build:2), system Python 3.10
with actual UNO binding. A self-authored XLSX containing `=SUM(2,3)` produced cached
numeric value 5 through both the bubblewrap CLI conversion and the pinned helper
inside its private UNO namespace. The inventory follows resource directory
symlinks (notably Debian's `/etc/libreoffice/registry`). Boundary controls confirmed
synthetic API secrets absent, private sentinel/HOME inaccessible, host loopback
listener inaccessible and external TCP unavailable. Focused tests: 5 passed.
No benchmark input, model call or metric dataset was used. These probes establish
formula/UNO availability and tested namespace boundaries on this host, not complete
native metric correctness, cross-platform portability, or a pristine deployment.
