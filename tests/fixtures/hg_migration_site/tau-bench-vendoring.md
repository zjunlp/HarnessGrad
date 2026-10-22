# Tau-bench assets and vendored-source inventory

Current-layout note: this is a historical audit of the named baseline. After
Stages C/D, shared paths map to `data/scaffolds/` and adapter/asset paths map to
`data/datasets/`; baseline spellings below are retained as audit evidence.
The split report records the current verification.


This audit answers the tau-bench question in `docs/data-split-plan.md` §7. It
covers every regular file under `data/tau_bench/` at HarnessGrad commit
`b9ed453f67d93c1d3f7ea9c072392f42406891e1`, including `.dockerignore` and empty
package initializers. The filesystem and `git ls-files data/tau_bench` both contain
**210 files**, not 40: **4 adapter assets, 33 vendored upstream files, 173 generated
files, and 0 unclear files**. Total: **353,325 bytes and 11,181 lines**.

This is an inventory only. No existing file was changed or moved, no dataset was
run, and no adapter or upstream module was imported. The excluded adapter paths
and `eval/` were not inspected. Byte sizes are raw lengths; line counts count
physical lines, including a final unterminated line if present (empty files have
zero lines). Paths in the inventory are relative to `data/tau_bench/`.

## Verdicts and the Stage D boundary

| Verdict | Files | Bytes | Lines | Meaning here |
| --- | ---: | ---: | ---: | --- |
| `adapter-asset` | 4 | 29,834 | 702 | Handwritten platform build configuration, CLI, and verifier |
| `vendored-upstream` | 33 | 80,236 | 2,079 | Tool implementations and tool registries staged by copying upstream source |
| `generated` | 173 | 243,255 | 8,400 | Serialized tasks, assembled policies, extracted hash module, empty package stubs, provenance receipt |
| `unclear` | 0 | 0 | 0 | No file lacks enough evidence for a verdict |
| **Total** | **210** | **353,325** | **11,181** | All present files are classified individually below |

The categories describe how each file was produced. `generated` does **not** mean
first-party content: the 165 task records, two policies, and extracted hash module
contain upstream material and must retain its origin and pin. The four empty
package stubs and the provenance receipt are generated platform packaging.

The four handwritten assets move with the adapter. Treat the 33 copied files as
pinned third-party source even if they physically remain beside the adapter.
Preserve the generated derivatives, their source mappings, and their provenance
with that dependency. Do not treat all of `lib/` as verbatim upstream: it also has
four deliberately empty generated initializers and one generated extraction.
No final directory layout is selected by this audit.

## Recorded origin and evidence key

The upstream project is [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench),
pinned to **`59a200c6d575d595120f1cb70fea53cef0632f6b`**. The record is distributed
across several checked-in files; an individual source file need not contain a URL
for its origin to be recorded.

* `data/tau_bench/PROVENANCE.json` records `checkout_commit`, `generated_by`,
  `task_files`, `task_counts`, the original `base.py` digest, and SHA-256 values
  under `files` for every present copied file and generated payload other than
  the receipt itself. Its `checkout` is a historical external path; that path was
  not accessed for this audit.
* `tools/build_tau_bench_gt.py` defines the source-to-output mappings. `_stage_lib`
  uses `shutil.copy2` for `envs/tool.py` and every domain `tools/*.py`; it writes
  empty package stubs and generates `hash.py` separately. `build` explicitly says
  `image/` and `verify/` are handwritten and regenerates only its own outputs.
* `data/tau_bench.py`'s opening docstring names the upstream repository and task
  lists. Its `downloads()` reads the manifest's commit and returns the pinned URL
  `https://github.com/sierra-research/tau-bench/archive/59a200c6d575d595120f1cb70fea53cef0632f6b.tar.gz`.
  `DOWNLOADS.md`'s `tau_bench` section records the same revision and URL.
* `data/tau_bench/Dockerfile` labels the image `harnessgrad/tau-bench` and cites
  `PROVENANCE.json` with the short upstream revision `59a200c`.

Each inventory row cites the evidence below. **P** means that the row's exact
relative path is present in `PROVENANCE.json.files`, its current SHA-256 matches,
and the manifest records the full upstream revision above. The checks found
**205 present manifest entries, all matching**, and two additional manifest
entries whose files are absent (discussed below). All **33** `vendored-upstream`
rows have **P**, a matching upstream path via the builder, and the repository URL
via the adapter/downloader. **There is no vendored file without recorded origin.**

An independent read-only comparison also fetched the pinned upstream archive
into memory, without extracting it or importing its code:

* URL: `https://codeload.github.com/sierra-research/tau-bench/tar.gz/59a200c6d575d595120f1cb70fea53cef0632f6b`.
* Observed archive size: 6,836,498 bytes; SHA-256:
  `03ba20e51590dd5f117bcb8b209f02c90035a44a0d2456da21b20eed6cf2322c`.
  This is an observation from this audit, not a pre-existing downloader digest.
* All 33 copied files match the bytes at their corresponding upstream paths.
  The full upstream `base.py` matches `base_py_sha256`, and the extracted hash
  block matches the local generated module exactly.
* Both generated policies start with the exact upstream wiki text. This is a
  prefix check, not an independent check of their complete rule assembly.
  Upstream task records were not independently regenerated.
* The archive contains a root `LICENSE`; no copy is present under the audited
  directory. The external historical checkout was not accessed.

| Key | Evidence and source mapping |
| --- | --- |
| **A1** | `.dockerignore`'s header describes platform verification-time inputs; it excludes `gt/`, `verify/`, `PROVENANCE.json`, and bytecode from the image context. It is not a builder output. |
| **A2** | `Dockerfile` describes the platform image, sets HarnessGrad labels, installs the local CLI, and copies staged assets. It is not copied or emitted by the builder. |
| **A3** | `image/tau_cli.py`'s docstring defines the platform's `tau call/say/done` interface and log paths; the builder explicitly leaves this handwritten directory alone. It imports the domain tool registry from `TAU_LIB`. |
| **A4** | `verify/check.py`'s docstring describes running after the agent stops in a container snapshot, with platform-staged inputs and reward/evidence paths. The builder explicitly leaves this handwritten directory alone. It imports domain tool registries and the generated hash module. This platform verifier reproduces upstream scoring rather than being a copied upstream verifier. |
| **V1** | **P** plus `# Copyright Sierra` in each domain `tools/*.py`, including both tool registry initializers. `_stage_lib` copies each file verbatim; its upstream path is the inventory path with the leading `lib/` removed, at the pinned revision. All these files also match the pinned archive byte-for-byte. The registries import the listed tool classes; implementations import `tau_bench.envs.tool.Tool` and stdlib. |
| **V2** | **P** plus `_stage_lib`'s explicit `shutil.copy2(envs / "tool.py", tool_base)`. Upstream path: `tau_bench/envs/tool.py` at the pinned revision. No copyright, URL, or revision appears in this 12-line file itself; its recorded origin comes from the manifest, copier, and downloader. It also matches the pinned archive byte-for-byte. Domain tools subclass its `Tool` ABC. |
| **G1a** | **P** plus `build`'s JSON serialization loop. Source: `tau_bench/envs/airline/tasks_test.py`, list `TASKS`, at the pinned revision (`task_files.airline`). Each JSON is an indexed task record, not an upstream Python file copied under a new name. |
| **G1r** | **P** plus the same serialization loop. Source: `tau_bench/envs/retail/tasks_test.py`, list `TASKS_TEST`, at the pinned revision (`task_files.retail`). |
| **G2** | **P** plus `_stage_lib`'s `(package / "__init__.py").write_text("")`. Each stub is empty by construction; upstream package initializers are deliberately not copied, to avoid importing task definitions and LLM dependencies. An upstream-looking path alone would misclassify these files. |
| **G3** | **P** plus the `hash.py` header naming `tau_bench/envs/base.py` and its source SHA-256 `1ad0402073f0ac9ca6b527efa0706e2aa8836dd2e5c1d453ed414ecb768c6d21`, also recorded as `base_py_sha256`. `_extract_hash_module` copies the `ToHashable`/hash-function region and adds a provenance header and imports. Generated packaging of third-party source, not an independently authored hash implementation. |
| **G4a** | **P** plus `_load_policy` and `build`: assemble `tau_bench/envs/airline/wiki.md` with `tau_bench/envs/airline/rules.py` (`RULES`) at the pin. The text's final section explicitly says upstream declares no additional rules. |
| **G4r** | **P** plus `_load_policy` and `build`: assemble `tau_bench/envs/retail/wiki.md` with the `RULES` list in `tau_bench/envs/retail/rules.py` at the pin. The generated trailer explicitly identifies both upstream inputs. |
| **G5** | `generated_by: tools/build_tau_bench_gt.py` and `build`'s `json.dumps(provenance, ...)` write identify the tool and schema. The receipt is generated metadata, not upstream source; it does not hash itself. |

The inspected import graph reinforces this boundary: the adapter's replay helper,
CLI, and verifier load `tau_bench.envs.<domain>.tools`; each registry imports its
own domain's classes; those classes depend on `envs/tool.py` and stdlib. The
adapter replay helper and verifier also import `envs/hash.py`. Empty package stubs
prevent an implicit import of the full upstream agent/user stack. This graph was
read statically using the Python AST; none of these modules was executed.

## Complete file inventory

Each row has exactly one verdict. **V1**'s explicit path-mapping rule supplies a
matching upstream path for every copied tool file; **G1a/G1r** and **G4a/G4r**
supply the domain-specific source paths for generated records and policies.

| Path (under `data/tau_bench/`) | Bytes | Lines | Verdict | What it is | Evidence |
| --- | ---: | ---: | --- | --- | --- |
| `.dockerignore` | 209 | 6 | `adapter-asset` | Docker build-context exclusions | A1 |
| `Dockerfile` | 2,605 | 48 | `adapter-asset` | Shared platform container build recipe | A2 |
| `PROVENANCE.json` | 20,788 | 223 | `generated` | Builder receipt: revision, source mappings, counts, output digests | G5 |
| `gt/airline_000.json` | 1,526 | 50 | `generated` | Airline task 0: serialized ground-truth record | G1a |
| `gt/airline_001.json` | 870 | 16 | `generated` | Airline task 1: serialized ground-truth record | G1a |
| `gt/airline_002.json` | 2,391 | 110 | `generated` | Airline task 2: serialized ground-truth record | G1a |
| `gt/airline_003.json` | 1,445 | 45 | `generated` | Airline task 3: serialized ground-truth record | G1a |
| `gt/airline_004.json` | 1,275 | 50 | `generated` | Airline task 4: serialized ground-truth record | G1a |
| `gt/airline_005.json` | 1,255 | 50 | `generated` | Airline task 5: serialized ground-truth record | G1a |
| `gt/airline_006.json` | 712 | 28 | `generated` | Airline task 6: serialized ground-truth record | G1a |
| `gt/airline_007.json` | 809 | 28 | `generated` | Airline task 7: serialized ground-truth record | G1a |
| `gt/airline_008.json` | 2,370 | 82 | `generated` | Airline task 8: serialized ground-truth record | G1a |
| `gt/airline_009.json` | 4,283 | 160 | `generated` | Airline task 9: serialized ground-truth record | G1a |
| `gt/airline_010.json` | 1,686 | 56 | `generated` | Airline task 10: serialized ground-truth record | G1a |
| `gt/airline_011.json` | 1,283 | 50 | `generated` | Airline task 11: serialized ground-truth record | G1a |
| `gt/airline_012.json` | 283 | 9 | `generated` | Airline task 12: serialized ground-truth record | G1a |
| `gt/airline_013.json` | 846 | 16 | `generated` | Airline task 13: serialized ground-truth record | G1a |
| `gt/airline_014.json` | 1,274 | 47 | `generated` | Airline task 14: serialized ground-truth record | G1a |
| `gt/airline_015.json` | 770 | 9 | `generated` | Airline task 15: serialized ground-truth record | G1a |
| `gt/airline_016.json` | 738 | 23 | `generated` | Airline task 16: serialized ground-truth record | G1a |
| `gt/airline_017.json` | 689 | 9 | `generated` | Airline task 17: serialized ground-truth record | G1a |
| `gt/airline_018.json` | 549 | 9 | `generated` | Airline task 18: serialized ground-truth record | G1a |
| `gt/airline_019.json` | 1,346 | 43 | `generated` | Airline task 19: serialized ground-truth record | G1a |
| `gt/airline_020.json` | 971 | 42 | `generated` | Airline task 20: serialized ground-truth record | G1a |
| `gt/airline_021.json` | 408 | 9 | `generated` | Airline task 21: serialized ground-truth record | G1a |
| `gt/airline_022.json` | 1,485 | 62 | `generated` | Airline task 22: serialized ground-truth record | G1a |
| `gt/airline_023.json` | 1,890 | 59 | `generated` | Airline task 23: serialized ground-truth record | G1a |
| `gt/airline_024.json` | 799 | 9 | `generated` | Airline task 24: serialized ground-truth record | G1a |
| `gt/airline_025.json` | 1,413 | 46 | `generated` | Airline task 25: serialized ground-truth record | G1a |
| `gt/airline_026.json` | 1,359 | 62 | `generated` | Airline task 26: serialized ground-truth record | G1a |
| `gt/airline_027.json` | 1,154 | 44 | `generated` | Airline task 27: serialized ground-truth record | G1a |
| `gt/airline_028.json` | 1,558 | 76 | `generated` | Airline task 28: serialized ground-truth record | G1a |
| `gt/airline_029.json` | 1,251 | 58 | `generated` | Airline task 29: serialized ground-truth record | G1a |
| `gt/airline_030.json` | 1,508 | 70 | `generated` | Airline task 30: serialized ground-truth record | G1a |
| `gt/airline_031.json` | 1,319 | 52 | `generated` | Airline task 31: serialized ground-truth record | G1a |
| `gt/airline_032.json` | 1,737 | 67 | `generated` | Airline task 32: serialized ground-truth record | G1a |
| `gt/airline_033.json` | 3,416 | 182 | `generated` | Airline task 33: serialized ground-truth record | G1a |
| `gt/airline_034.json` | 1,552 | 64 | `generated` | Airline task 34: serialized ground-truth record | G1a |
| `gt/airline_035.json` | 1,093 | 22 | `generated` | Airline task 35: serialized ground-truth record | G1a |
| `gt/airline_036.json` | 942 | 22 | `generated` | Airline task 36: serialized ground-truth record | G1a |
| `gt/airline_037.json` | 716 | 16 | `generated` | Airline task 37: serialized ground-truth record | G1a |
| `gt/airline_038.json` | 817 | 16 | `generated` | Airline task 38: serialized ground-truth record | G1a |
| `gt/airline_039.json` | 585 | 16 | `generated` | Airline task 39: serialized ground-truth record | G1a |
| `gt/airline_040.json` | 1,053 | 46 | `generated` | Airline task 40: serialized ground-truth record | G1a |
| `gt/airline_041.json` | 493 | 16 | `generated` | Airline task 41: serialized ground-truth record | G1a |
| `gt/airline_042.json` | 561 | 16 | `generated` | Airline task 42: serialized ground-truth record | G1a |
| `gt/airline_043.json` | 872 | 34 | `generated` | Airline task 43: serialized ground-truth record | G1a |
| `gt/airline_044.json` | 658 | 24 | `generated` | Airline task 44: serialized ground-truth record | G1a |
| `gt/airline_045.json` | 985 | 29 | `generated` | Airline task 45: serialized ground-truth record | G1a |
| `gt/airline_046.json` | 1,195 | 35 | `generated` | Airline task 46: serialized ground-truth record | G1a |
| `gt/airline_047.json` | 667 | 22 | `generated` | Airline task 47: serialized ground-truth record | G1a |
| `gt/airline_048.json` | 448 | 16 | `generated` | Airline task 48: serialized ground-truth record | G1a |
| `gt/airline_049.json` | 471 | 9 | `generated` | Airline task 49: serialized ground-truth record | G1a |
| `gt/retail_000.json` | 1,270 | 51 | `generated` | Retail task 0: serialized ground-truth record | G1r |
| `gt/retail_001.json` | 1,248 | 49 | `generated` | Retail task 1: serialized ground-truth record | G1r |
| `gt/retail_002.json` | 1,625 | 90 | `generated` | Retail task 2: serialized ground-truth record | G1r |
| `gt/retail_003.json` | 1,845 | 97 | `generated` | Retail task 3: serialized ground-truth record | G1r |
| `gt/retail_004.json` | 2,107 | 110 | `generated` | Retail task 4: serialized ground-truth record | G1r |
| `gt/retail_005.json` | 1,157 | 46 | `generated` | Retail task 5: serialized ground-truth record | G1r |
| `gt/retail_006.json` | 1,198 | 55 | `generated` | Retail task 6: serialized ground-truth record | G1r |
| `gt/retail_007.json` | 1,206 | 55 | `generated` | Retail task 7: serialized ground-truth record | G1r |
| `gt/retail_008.json` | 1,195 | 55 | `generated` | Retail task 8: serialized ground-truth record | G1r |
| `gt/retail_009.json` | 1,203 | 55 | `generated` | Retail task 9: serialized ground-truth record | G1r |
| `gt/retail_010.json` | 1,167 | 40 | `generated` | Retail task 10: serialized ground-truth record | G1r |
| `gt/retail_011.json` | 1,446 | 57 | `generated` | Retail task 11: serialized ground-truth record | G1r |
| `gt/retail_012.json` | 1,296 | 52 | `generated` | Retail task 12: serialized ground-truth record | G1r |
| `gt/retail_013.json` | 1,358 | 58 | `generated` | Retail task 13: serialized ground-truth record | G1r |
| `gt/retail_014.json` | 1,223 | 54 | `generated` | Retail task 14: serialized ground-truth record | G1r |
| `gt/retail_015.json` | 1,226 | 61 | `generated` | Retail task 15: serialized ground-truth record | G1r |
| `gt/retail_016.json` | 1,531 | 74 | `generated` | Retail task 16: serialized ground-truth record | G1r |
| `gt/retail_017.json` | 1,097 | 54 | `generated` | Retail task 17: serialized ground-truth record | G1r |
| `gt/retail_018.json` | 1,103 | 49 | `generated` | Retail task 18: serialized ground-truth record | G1r |
| `gt/retail_019.json` | 1,337 | 61 | `generated` | Retail task 19: serialized ground-truth record | G1r |
| `gt/retail_020.json` | 1,762 | 85 | `generated` | Retail task 20: serialized ground-truth record | G1r |
| `gt/retail_021.json` | 2,072 | 95 | `generated` | Retail task 21: serialized ground-truth record | G1r |
| `gt/retail_022.json` | 1,612 | 72 | `generated` | Retail task 22: serialized ground-truth record | G1r |
| `gt/retail_023.json` | 2,134 | 105 | `generated` | Retail task 23: serialized ground-truth record | G1r |
| `gt/retail_024.json` | 500 | 12 | `generated` | Retail task 24: serialized ground-truth record | G1r |
| `gt/retail_025.json` | 1,123 | 48 | `generated` | Retail task 25: serialized ground-truth record | G1r |
| `gt/retail_026.json` | 1,399 | 65 | `generated` | Retail task 26: serialized ground-truth record | G1r |
| `gt/retail_027.json` | 1,276 | 55 | `generated` | Retail task 27: serialized ground-truth record | G1r |
| `gt/retail_028.json` | 1,975 | 94 | `generated` | Retail task 28: serialized ground-truth record | G1r |
| `gt/retail_029.json` | 1,543 | 66 | `generated` | Retail task 29: serialized ground-truth record | G1r |
| `gt/retail_030.json` | 2,191 | 101 | `generated` | Retail task 30: serialized ground-truth record | G1r |
| `gt/retail_031.json` | 2,029 | 91 | `generated` | Retail task 31: serialized ground-truth record | G1r |
| `gt/retail_032.json` | 2,097 | 98 | `generated` | Retail task 32: serialized ground-truth record | G1r |
| `gt/retail_033.json` | 1,812 | 54 | `generated` | Retail task 33: serialized ground-truth record | G1r |
| `gt/retail_034.json` | 1,732 | 56 | `generated` | Retail task 34: serialized ground-truth record | G1r |
| `gt/retail_035.json` | 1,519 | 63 | `generated` | Retail task 35: serialized ground-truth record | G1r |
| `gt/retail_036.json` | 2,107 | 86 | `generated` | Retail task 36: serialized ground-truth record | G1r |
| `gt/retail_037.json` | 2,107 | 86 | `generated` | Retail task 37: serialized ground-truth record | G1r |
| `gt/retail_038.json` | 2,023 | 82 | `generated` | Retail task 38: serialized ground-truth record | G1r |
| `gt/retail_039.json` | 1,508 | 62 | `generated` | Retail task 39: serialized ground-truth record | G1r |
| `gt/retail_040.json` | 1,188 | 38 | `generated` | Retail task 40: serialized ground-truth record | G1r |
| `gt/retail_041.json` | 2,381 | 97 | `generated` | Retail task 41: serialized ground-truth record | G1r |
| `gt/retail_042.json` | 2,347 | 97 | `generated` | Retail task 42: serialized ground-truth record | G1r |
| `gt/retail_043.json` | 1,477 | 56 | `generated` | Retail task 43: serialized ground-truth record | G1r |
| `gt/retail_044.json` | 1,197 | 51 | `generated` | Retail task 44: serialized ground-truth record | G1r |
| `gt/retail_045.json` | 1,452 | 51 | `generated` | Retail task 45: serialized ground-truth record | G1r |
| `gt/retail_046.json` | 1,700 | 62 | `generated` | Retail task 46: serialized ground-truth record | G1r |
| `gt/retail_047.json` | 1,701 | 62 | `generated` | Retail task 47: serialized ground-truth record | G1r |
| `gt/retail_048.json` | 1,224 | 52 | `generated` | Retail task 48: serialized ground-truth record | G1r |
| `gt/retail_049.json` | 1,714 | 79 | `generated` | Retail task 49: serialized ground-truth record | G1r |
| `gt/retail_050.json` | 927 | 16 | `generated` | Retail task 50: serialized ground-truth record | G1r |
| `gt/retail_051.json` | 1,189 | 56 | `generated` | Retail task 51: serialized ground-truth record | G1r |
| `gt/retail_052.json` | 1,321 | 49 | `generated` | Retail task 52: serialized ground-truth record | G1r |
| `gt/retail_053.json` | 1,166 | 52 | `generated` | Retail task 53: serialized ground-truth record | G1r |
| `gt/retail_054.json` | 2,136 | 93 | `generated` | Retail task 54: serialized ground-truth record | G1r |
| `gt/retail_055.json` | 2,049 | 101 | `generated` | Retail task 55: serialized ground-truth record | G1r |
| `gt/retail_056.json` | 1,501 | 61 | `generated` | Retail task 56: serialized ground-truth record | G1r |
| `gt/retail_057.json` | 538 | 9 | `generated` | Retail task 57: serialized ground-truth record | G1r |
| `gt/retail_058.json` | 1,646 | 57 | `generated` | Retail task 58: serialized ground-truth record | G1r |
| `gt/retail_059.json` | 2,120 | 58 | `generated` | Retail task 59: serialized ground-truth record | G1r |
| `gt/retail_060.json` | 1,149 | 45 | `generated` | Retail task 60: serialized ground-truth record | G1r |
| `gt/retail_061.json` | 1,057 | 49 | `generated` | Retail task 61: serialized ground-truth record | G1r |
| `gt/retail_062.json` | 1,630 | 49 | `generated` | Retail task 62: serialized ground-truth record | G1r |
| `gt/retail_063.json` | 1,914 | 65 | `generated` | Retail task 63: serialized ground-truth record | G1r |
| `gt/retail_064.json` | 1,418 | 74 | `generated` | Retail task 64: serialized ground-truth record | G1r |
| `gt/retail_065.json` | 677 | 30 | `generated` | Retail task 65: serialized ground-truth record | G1r |
| `gt/retail_066.json` | 951 | 43 | `generated` | Retail task 66: serialized ground-truth record | G1r |
| `gt/retail_067.json` | 1,152 | 48 | `generated` | Retail task 67: serialized ground-truth record | G1r |
| `gt/retail_068.json` | 943 | 40 | `generated` | Retail task 68: serialized ground-truth record | G1r |
| `gt/retail_069.json` | 977 | 37 | `generated` | Retail task 69: serialized ground-truth record | G1r |
| `gt/retail_070.json` | 852 | 25 | `generated` | Retail task 70: serialized ground-truth record | G1r |
| `gt/retail_071.json` | 1,378 | 35 | `generated` | Retail task 71: serialized ground-truth record | G1r |
| `gt/retail_072.json` | 1,511 | 35 | `generated` | Retail task 72: serialized ground-truth record | G1r |
| `gt/retail_073.json` | 585 | 23 | `generated` | Retail task 73: serialized ground-truth record | G1r |
| `gt/retail_074.json` | 904 | 30 | `generated` | Retail task 74: serialized ground-truth record | G1r |
| `gt/retail_075.json` | 704 | 23 | `generated` | Retail task 75: serialized ground-truth record | G1r |
| `gt/retail_076.json` | 866 | 26 | `generated` | Retail task 76: serialized ground-truth record | G1r |
| `gt/retail_077.json` | 680 | 23 | `generated` | Retail task 77: serialized ground-truth record | G1r |
| `gt/retail_078.json` | 1,135 | 42 | `generated` | Retail task 78: serialized ground-truth record | G1r |
| `gt/retail_079.json` | 712 | 23 | `generated` | Retail task 79: serialized ground-truth record | G1r |
| `gt/retail_080.json` | 783 | 23 | `generated` | Retail task 80: serialized ground-truth record | G1r |
| `gt/retail_081.json` | 711 | 24 | `generated` | Retail task 81: serialized ground-truth record | G1r |
| `gt/retail_082.json` | 712 | 23 | `generated` | Retail task 82: serialized ground-truth record | G1r |
| `gt/retail_083.json` | 619 | 20 | `generated` | Retail task 83: serialized ground-truth record | G1r |
| `gt/retail_084.json` | 655 | 20 | `generated` | Retail task 84: serialized ground-truth record | G1r |
| `gt/retail_085.json` | 574 | 23 | `generated` | Retail task 85: serialized ground-truth record | G1r |
| `gt/retail_086.json` | 956 | 35 | `generated` | Retail task 86: serialized ground-truth record | G1r |
| `gt/retail_087.json` | 1,448 | 58 | `generated` | Retail task 87: serialized ground-truth record | G1r |
| `gt/retail_088.json` | 628 | 17 | `generated` | Retail task 88: serialized ground-truth record | G1r |
| `gt/retail_089.json` | 664 | 25 | `generated` | Retail task 89: serialized ground-truth record | G1r |
| `gt/retail_090.json` | 738 | 17 | `generated` | Retail task 90: serialized ground-truth record | G1r |
| `gt/retail_091.json` | 1,117 | 35 | `generated` | Retail task 91: serialized ground-truth record | G1r |
| `gt/retail_092.json` | 885 | 32 | `generated` | Retail task 92: serialized ground-truth record | G1r |
| `gt/retail_093.json` | 630 | 23 | `generated` | Retail task 93: serialized ground-truth record | G1r |
| `gt/retail_094.json` | 630 | 23 | `generated` | Retail task 94: serialized ground-truth record | G1r |
| `gt/retail_095.json` | 630 | 23 | `generated` | Retail task 95: serialized ground-truth record | G1r |
| `gt/retail_096.json` | 1,019 | 40 | `generated` | Retail task 96: serialized ground-truth record | G1r |
| `gt/retail_097.json` | 908 | 35 | `generated` | Retail task 97: serialized ground-truth record | G1r |
| `gt/retail_098.json` | 1,010 | 35 | `generated` | Retail task 98: serialized ground-truth record | G1r |
| `gt/retail_099.json` | 1,572 | 45 | `generated` | Retail task 99: serialized ground-truth record | G1r |
| `gt/retail_100.json` | 1,451 | 38 | `generated` | Retail task 100: serialized ground-truth record | G1r |
| `gt/retail_101.json` | 914 | 35 | `generated` | Retail task 101: serialized ground-truth record | G1r |
| `gt/retail_102.json` | 1,403 | 48 | `generated` | Retail task 102: serialized ground-truth record | G1r |
| `gt/retail_103.json` | 1,406 | 48 | `generated` | Retail task 103: serialized ground-truth record | G1r |
| `gt/retail_104.json` | 1,663 | 58 | `generated` | Retail task 104: serialized ground-truth record | G1r |
| `gt/retail_105.json` | 1,700 | 57 | `generated` | Retail task 105: serialized ground-truth record | G1r |
| `gt/retail_106.json` | 860 | 25 | `generated` | Retail task 106: serialized ground-truth record | G1r |
| `gt/retail_107.json` | 699 | 23 | `generated` | Retail task 107: serialized ground-truth record | G1r |
| `gt/retail_108.json` | 957 | 36 | `generated` | Retail task 108: serialized ground-truth record | G1r |
| `gt/retail_109.json` | 565 | 24 | `generated` | Retail task 109: serialized ground-truth record | G1r |
| `gt/retail_110.json` | 1,644 | 47 | `generated` | Retail task 110: serialized ground-truth record | G1r |
| `gt/retail_111.json` | 1,547 | 47 | `generated` | Retail task 111: serialized ground-truth record | G1r |
| `gt/retail_112.json` | 1,309 | 48 | `generated` | Retail task 112: serialized ground-truth record | G1r |
| `gt/retail_113.json` | 1,253 | 48 | `generated` | Retail task 113: serialized ground-truth record | G1r |
| `gt/retail_114.json` | 618 | 24 | `generated` | Retail task 114: serialized ground-truth record | G1r |
| `image/tau_cli.py` | 11,605 | 301 | `adapter-asset` | Platform command-line wrapper around domain tools | A3 |
| `lib/tau_bench/__init__.py` | 0 | 0 | `generated` | Empty package stub isolating the staged tool package | G2 |
| `lib/tau_bench/envs/__init__.py` | 0 | 0 | `generated` | Empty package stub isolating the staged tool package | G2 |
| `lib/tau_bench/envs/airline/__init__.py` | 0 | 0 | `generated` | Empty package stub isolating the staged tool package | G2 |
| `lib/tau_bench/envs/airline/tools/__init__.py` | 1,088 | 33 | `vendored-upstream` | Airline tool registry: 14 class imports and ALL_TOOLS | V1 |
| `lib/tau_bench/envs/airline/tools/book_reservation.py` | 10,282 | 226 | `vendored-upstream` | Airline BookReservation tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/calculate.py` | 1,266 | 35 | `vendored-upstream` | Airline Calculate tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/cancel_reservation.py` | 1,528 | 50 | `vendored-upstream` | Airline CancelReservation tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/get_reservation_details.py` | 1,082 | 34 | `vendored-upstream` | Airline GetReservationDetails tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/get_user_details.py` | 1,028 | 34 | `vendored-upstream` | Airline GetUserDetails tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/list_all_airports.py` | 1,633 | 70 | `vendored-upstream` | Airline ListAllAirports tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/search_direct_flight.py` | 2,014 | 50 | `vendored-upstream` | Airline SearchDirectFlight tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/search_onestop_flight.py` | 3,240 | 74 | `vendored-upstream` | Airline SearchOnestopFlight tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/send_certificate.py` | 1,765 | 52 | `vendored-upstream` | Airline SendCertificate tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/think.py` | 980 | 30 | `vendored-upstream` | Airline Think tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/transfer_to_human_agents.py` | 1,144 | 35 | `vendored-upstream` | Airline TransferToHumanAgents tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/update_reservation_baggages.py` | 3,268 | 84 | `vendored-upstream` | Airline UpdateReservationBaggages tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/update_reservation_flights.py` | 6,070 | 138 | `vendored-upstream` | Airline UpdateReservationFlights tool implementation | V1 |
| `lib/tau_bench/envs/airline/tools/update_reservation_passengers.py` | 2,712 | 64 | `vendored-upstream` | Airline UpdateReservationPassengers tool implementation | V1 |
| `lib/tau_bench/envs/hash.py` | 1,336 | 35 | `generated` | Upstream database hash functions extracted with header/imports | G3 |
| `lib/tau_bench/envs/retail/__init__.py` | 0 | 0 | `generated` | Empty package stub isolating the staged tool package | G2 |
| `lib/tau_bench/envs/retail/tools/__init__.py` | 1,302 | 38 | `vendored-upstream` | Retail tool registry: 16 class imports and ALL_TOOLS | V1 |
| `lib/tau_bench/envs/retail/tools/calculate.py` | 1,324 | 36 | `vendored-upstream` | Retail Calculate tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/cancel_pending_order.py` | 3,320 | 78 | `vendored-upstream` | Retail CancelPendingOrder tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/exchange_delivered_order_items.py` | 5,370 | 126 | `vendored-upstream` | Retail ExchangeDeliveredOrderItems tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/find_user_id_by_email.py` | 1,132 | 34 | `vendored-upstream` | Retail FindUserIdByEmail tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/find_user_id_by_name_zip.py` | 2,013 | 50 | `vendored-upstream` | Retail FindUserIdByNameZip tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/get_order_details.py` | 1,087 | 34 | `vendored-upstream` | Retail GetOrderDetails tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/get_product_details.py` | 1,104 | 34 | `vendored-upstream` | Retail GetProductDetails tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/get_user_details.py` | 1,021 | 34 | `vendored-upstream` | Retail GetUserDetails tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/list_all_product_types.py` | 1,035 | 31 | `vendored-upstream` | Retail ListAllProductTypes tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/modify_pending_order_address.py` | 3,212 | 89 | `vendored-upstream` | Retail ModifyPendingOrderAddress tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/modify_pending_order_items.py` | 5,707 | 129 | `vendored-upstream` | Retail ModifyPendingOrderItems tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/modify_pending_order_payment.py` | 4,364 | 111 | `vendored-upstream` | Retail ModifyPendingOrderPayment tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/modify_user_address.py` | 2,920 | 84 | `vendored-upstream` | Retail ModifyUserAddress tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/return_delivered_order_items.py` | 3,632 | 82 | `vendored-upstream` | Retail ReturnDeliveredOrderItems tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/think.py` | 1,160 | 34 | `vendored-upstream` | Retail Think tool implementation | V1 |
| `lib/tau_bench/envs/retail/tools/transfer_to_human_agents.py` | 1,200 | 34 | `vendored-upstream` | Retail TransferToHumanAgents tool implementation | V1 |
| `lib/tau_bench/envs/tool.py` | 233 | 12 | `vendored-upstream` | Abstract Tool interface used by both domains | V2 |
| `policy/airline.txt` | 6,233 | 74 | `generated` | Airline wiki and rules assembled into policy text | G4a |
| `policy/retail.txt` | 6,716 | 94 | `generated` | Retail wiki and rules assembled into policy text | G4r |
| `verify/check.py` | 15,415 | 347 | `adapter-asset` | Platform snapshot verifier and evidence writer | A4 |

## Findings and limits of the provenance record

1. **The 40-file figure and blanket “adapter assets” description are stale.**
   The actual directory has 210 tracked files, of which 165 are generated task
   records. `lib/` has 38 files with three different origins: 33 upstream copies,
   four generated package stubs, and one generated extraction. This is the
   ownership boundary that Stage D must preserve.
2. **No unprovenanced vendored file was found.** All 33 copied source files have
   a recorded revision and matching output digest; 32 also have Sierra copyright
   headers. The headerless `envs/tool.py` is still traceable through the explicit
   copy operation. It would be incorrect to report its missing inline header as
   missing repository provenance. The five present files absent from the digest
   map are the four handwritten adapter assets and the receipt itself; none is
   untracked third-party code.
3. **Two recorded generated databases are absent, intentionally ignored by git.**
   `PROVENANCE.json.files` also lists `db/airline.json` and `db/retail.json`, but
   neither exists in this worktree. `.gitignore` explicitly ignores
   `data/tau_bench/db/` and says to regenerate the databases. The Dockerfile still
   has `COPY db /opt/tau/pristine`, and the adapter refers to `DB_DIR`; inventory
   completeness does not imply a runnable environment. These absent payloads are
   not included in the 210-file inventory, and were not regenerated. Their
   recorded SHA-256 values are:

   * `db/airline.json`: `f212eeda89b986239ddfb1a9b3d07d2b388112869864655ec9e5cc7ab07ab41c`.

   * `db/retail.json`: `ae10e8a7f23bedf88c90a528efa2d59d27cae24b5ce1f0d28768b39257bf9f0d`.

4. **A revision pin does not attest to a clean original checkout.** The builder's
   `_git_commit` only calls `git rev-parse HEAD`; it does not record dirty state,
   upstream remote identity, or source-file digests for tasks/wiki/rules. Staged
   output digests do detect later local drift. The hash extraction additionally
   pins its full original `base.py` bytes. No archive SHA-256 is recorded by
   `downloads()` or its rendered `DOWNLOADS.md` entry. These are limits of the
   provenance record, not missing origin for individual copied files.
5. **Upstream license material is not bundled in this directory.** All 210 paths
   are listed above; none is an upstream license/notice. The repository root's
   `LICENSE` is HarnessGrad's MIT license with its own copyright attribution;
   that alone does not establish the vendored dependency's license. The 32
   Sierra headers should be retained. The appropriate upstream license/notice
   packaging remains a parent decision; this audit makes no legal conclusion.

## What could not be determined

* Whether the historical source checkout was clean when these outputs were
  generated, or whether historical task/policy inputs had local modifications.
  The commit and output digests do not record that fact.
* Whether all generated task and complete policy bytes reproduce unchanged from
  the pinned archive. The audit established their recorded source mapping and
  output integrity; independent archive checks covered copied code, the hash
  extraction, and wiki prefixes only.
* The actual bytes, sizes, line counts, or current reproducibility of the two
  absent databases. Only their recorded digests and generation recipe are
  available inside this worktree; no external checkout was read or rebuild run.
* Whether a previously built image matches this manifest. The Dockerfile's
  provenance label is a source citation, not a receipt attesting image contents.
* Which final third-party directory and license/notice packaging Stage D should
  use. That is a migration decision, beyond classifying the current files.

## Suggested replacement for the tau-bench bullet in §7

> `data/tau_bench/` has been inventoried in `docs/tau-bench-vendoring.md`: 210
> tracked files comprise 4 handwritten adapter assets, 33 copied upstream source
> files, and 173 generated files (0 unclear). Upstream is
> `sierra-research/tau-bench` at
> `59a200c6d575d595120f1cb70fea53cef0632f6b`; all copied files have recorded
> provenance and matching local SHA-256 values. Generated tasks, policies, and
> the extracted hash module also retain third-party lineage. Stage D must
> preserve this distinction rather than treating the entire directory as
> first-party adapter assets. Two generated databases are recorded but ignored
> and absent here; reproduction and third-party license packaging remain
> separate questions. See the inventory for source mappings and limits.

The parent can also replace the §1 “40 files” count and qualify its blanket asset
description. `docs/data-split-plan.md` was not edited.

## Validation

The audit enumerated all filesystem files including hidden files, compared that
set to `git ls-files`, checked every present payload against its recorded SHA-256,
checked all 165 task IDs/domain/index triples and contiguous domain ranges
(airline 0–49; retail 0–114), read the builder and imports statically, and compared
the 33 upstream copies and extracted hash block to the pinned archive. Policy
verification against the archive was limited to each wiki prefix; full upstream
regeneration was not attempted. The builder's `--check` option was not run:
it calls `build()` and rewrites the assets before checking them.

No test was added: the requested condition, a vendored file with no recorded
origin, was not found. No pytest file or full test suite was run for this
inventory-only document change.
