# Component attribution in the run archive (measured 2026-11-02)

`tools/component_attribution.py` answers the question a component-level claim needs: **when a round
improves the score, which part of the harness moved?** It reads the archive's curves and groups
rounds by the components their `editable_surface_components` names. On the archive it currently
attributes **0 of 1008 curve points**. This page says what that zero is, what the archive has to
contain before the ratio can be about attribution at all, and one command that produces the first
attributed point.

Nothing here claims a component improved anything. A round that names `control` and loses 3 pp is
still a round that named `control`; the question is whether the edit *can be attributed*, not what
it did.

## 1. What "0 of 1008" says, and what it does not

The tool reads `/mnt/20t/xubuqiang/hg-work/rsi10/runs` and reports **0 attributed points out of
1008**, for three reasons it already distinguishes (a fourth state, `declaration_unreadable`,
has no points here):

| state | points | what the record says |
| --- | ---: | --- |
| `field_absent` | 698 | the point carries no `editable_surface_components` at all, so no attribution was attempted |
| `no_component_named` | 292 | the point records at least one moved path and names no component |
| `no_path_moved` | 18 | no component and no moved path: the round moved nothing |
| `declaration_unreadable` | 0 | the field is present but no component list can be read from it |

None of the three is a failed attribution. The 698 points were written before the field existed (or
are H0 points, which no round produces); the 292 carry the field but name nothing; the 18 say the
round was a no-change round. The correct thing to read from the table is **"the archive contains no
component-level evidence"**, not "no component was ever edited" — the archive records 292 rounds
that did move a file, and the record simply cannot say which component it was.

A reader who divides by 1008 gets a third wrong reading — "attribution failed on every round". That
denominator is wrong: only **310** of the 1008 points carry the field, so only 310 were candidates
for attribution at all. The tool now prints both numbers (`points.total`, `points.with_field`), and
the line it writes to stderr is:

```
no attribution recorded: 0 of 310 curve point(s) that carry the field name a component (1008 in total)
698 run(s) included, 166 skipped; 0 attributed round(s), 1008 unattributed (310 of 1008 point(s) carry the field; 54 in discarded runs)
```

## 2. The census needed to read the ratio

The ratio "0 attributed" is only meaningful beside the size of the archive, and the pointedly
missing number is *how many of those points could have been attributed*. That is derivable, not a
guess: a point can name a component only if the harness it measured declared a `components` map, and
the archive keeps the harness tree each round measured as `state_after_round<N>/harness.json`.

| quantity | value | source |
| --- | ---: | --- |
| run directories discovered (783 in `runs/`, 81 in `runs/runs_discarded/`) | 864 | tool (`runs.discovered`) |
| runs with a `curve.jsonl` | 698 | tool (`runs.included`) |
| runs skipped: 85 without a curve, 81 labelled discarded | 166 | tool (`No data`) |
| curve points in included studies | 1008 | tool (`points.total`) |
| of those, carrying `editable_surface_components` | 310 | tool (`points.with_field`) |
| of those, naming at least one component | **0** | tool (`points.attributed`) |
| curve points in the runs the archive labels discarded | 54 | tool (`points.in_discarded_runs`) |
| points whose round has a `state_after_round<N>/harness.json` snapshot | 391 | census script below |
| of those snapshots, declaring a `components` map | **0** | census script below |
| points whose run's `run_meta.json` names harness `loop` | 781 of 783 | census script below |

**So the number of points that could have been attributed is 0, not 1008 and not 310.** Three
independent facts, all measured:

1. Of the 391 points whose harness tree is in the archive, none came from a manifest with a
   `components` map. Every snapshot `harness.json` is the pre-declaration shape
   (`name`, `version`, `path`, `entrypoint`, `backend`, `install`, `env_kinds`).
2. The 617 points with no snapshot come from the same harness. 781 of the 783 non-discarded run
   directories name `loop` (two have no `run_meta.json`), and `base_harness/loop/harness.json` has
   exactly two commits in its history: `3a8dfd0` (2026-10-22) and `e004550` (2026-11-02). The
   earlier version has no `components` key; the map arrived in `e004550`, whose message states
   the same fact in its own words -- no base harness had ever declared `components`, so zero of the
   1008 recorded curve points carry a component attribution.
3. Every dated run predates that commit. The newest run id is
   `appworld-repair-dgm-r15-0d8a4ee_3-20261023T0028Z` (2026-10-23); 697 of the 698 runs with a
   curve carry a date, spread 2026-10-17 … 2026-10-23; the one undated run
   (`probe-dream_rsi-r6-pencil`) has no snapshot.

Read together: **the field was recorded, the map was not written until 2026-11-02, and every
archived round was measured before that.** The honest headline is not "0 of 1008 attributed" but
"0 of 1008 attributable, because the declaration that attribution reads did not exist while these
rounds ran".

Two smaller facts keep the denominator auditable rather than merely large:

* **54 curve points sit in runs the archive labels discarded** (`runs_discarded/`, 81 directories of
  which 54 have a curve). The tool counts them and prints the count (`points.in_discarded_runs`)
  instead of leaving a reader to wonder whether 1008 is the whole archive. 1008 + 54 = 1062, the
  figure the tool's docstring used to assert without showing it.
* **The field reaches only the rounds, not H0.** In a run started after the declaration, round 0
  still carries no `editable_surface_components` (it is written per round, together with the touched
  paths); see §4. So a future archive's denominator is not "all points" either.

The same conclusion holds over every `runs/` root under `/mnt/20t/xubuqiang/hg-work/`
(`rsi10`, `rsi9`, `alfworld-h1`, `aime-diag`, `ds-pilots`): 896 runs discovered, 720 included, 1049
curve points, 329 carrying the field, 306 `no_component_named`, 23 `no_path_moved`, 0 attributed.
The archive is frozen at 2026-10-23; these numbers were measured on 2026-11-02.

### The 292 are not "outside the declared map"

Against the map `base_harness/loop` declares *today* (`control: ["agent.py", "harness.json"]`,
`unattributed: ["requirements.txt"]`), re-attributing the recorded touched paths gives:

| touched paths recorded on the point | points | would name |
| --- | ---: | --- |
| `agent.py` | 276 | `control` |
| `agent.py`, `artifacts/proposal_card.json` | 9 | `control` (the second path is unclaimed) |
| `agent.py`, `alfworld_solver.py` | 5 | `control` (the second path is unclaimed) |
| `agent.py`, `alf_planner.py` | 2 | `control` (the second path is unclaimed) |
| none recorded | 18 | — (nothing moved) |

**292 of 292 name `control`.** None of them is outside the map the harness declares; they are
inside it. That is why the tool's state for them is now `no_component_named` rather than
`outside_declared_map` (§5): `components.attribute` writes
`{"components": [], "unattributed": [paths]}` both for a harness that declared no map and for one
whose map misses the path, so "outside" is not a claim the record supports, and here it is false.

## 3. The ten components

The vocabulary is the closed list in `harnessgrad/components.py:COMPONENTS`; a harness declares
which of its files implement which component in `harness.json`'s `components` map, which is a map
for reading and never a fence (`docs/adapter-spec.md` §3). The glosses below are the ones this
repository already uses: a harness is *"tools, prompts, control flow, memory, how many models it
calls"* (`docs/writing_a_harness.md:9,178`; `report/09_experiments.tex` names "context builders,
tool policies, retry rules, memory structures, and stopping conditions").

`base_harness/launchable.json` lists seven launchable harnesses. `oracle` is not one of them and,
uniquely, declares no map at all. Derived from the checked-in manifests:

| component | declared by | files (union) | reachable by a method in |
| --- | ---: | --- | --- |
| `context` | 3 / 7 | `agent.yaml`, `knobs.json`, `prompt.md`, `prompts/extract.md`, `prompts/system.md`, `runtime/workspace.py`, `skills/main.md` | deepseek-harness, miniagent, openclaw |
| `tools` | 2 / 7 | `config.json`, `runtime/editor.py`, `runtime/reader.py`, `runtime/tool_result.py`, `runtime/tools.py`, `tool_descriptions/*.json`, `tools/read_skill.py`, `tools/shell.py` | miniagent, openclaw |
| `control` | 7 / 7 | `agent.py`, `harness.json`, `runtime/agent.py`, `runtime/llm.py` | loop, miniagent |
| `memory` | 1 / 7 | `runtime/trace.py` | miniagent |
| `verification` | **0 / 7** | — | — |
| `recovery` | **0 / 7** | — | — |
| `delegation` | 6 / 7 | `agent.py` | codex, deepseek-harness, miniagent, miniswe, openclaw, opencode |
| `reliability` | 1 / 7 | `runtime-identity.json` | openclaw |
| `cost` | 1 / 7 | `bridge.py` | miniagent |
| `safety` | 2 / 7 | `runtime/web_config.py`, `surface.json` | deepseek-harness, miniagent |

"Reachable" excludes the top-level `harness.json`, which `methods/apply.py:38` refuses to rewrite
(a method that could rewrite its own manifest could rename the thing it is measured as). That is
the whole of `control`'s file list on codex, deepseek-harness, miniswe, openclaw and opencode, so on
those five harnesses `control` is declared and **unreachable**: a round there cannot move a file the
map calls `control`. Only `loop` (`agent.py`) and `miniagent` (`runtime/agent.py`,
`runtime/llm.py`) can.

Per component, what a round would have to edit, and which method does it from its own code:

**`context`** — what the model is told before it acts: the system prompt, the extraction prompt, the
skill text, and the workspace/config knobs that assemble them. `miniagent` is where the split is
visible: `agent.yaml` points at `prompts/system.md` and `prompts/extract.md`, and
`runtime/workspace.py` loads them; `deepseek-harness` puts the same decision in `knobs.json`
(`prompt.preamble`, `prompt.append_inputs`, `prompt.inputs_header`), so a method edits a value
instead of a launcher; `openclaw` has one `prompt.md`. **No shipped method edits it from code.** All
nine published ports hand their framing to `methods/editor.py`'s `ask`, and the model chooses the
file; the method's code names no path.

**`tools`** — the tool schemas, the tool implementations, and the allow-list. In `miniagent` these
are `runtime/tools.py`, `runtime/reader.py`, `runtime/editor.py`, `runtime/tool_result.py`,
`tool_descriptions/*.json` and `tools/*.py`; in `openclaw` the allow-list is `config.json`'s
`tools.allow` (`read`, `write`, `edit`, `exec`). **No shipped method edits it from code** — same
reason: model-authored edits via `editor.ask`.

**`control`** — the loop itself: how many steps, when it stops, how it parses, retries and submits.
`loop` is one file (`agent.py`) plus the manifest; `miniagent` splits it into `runtime/agent.py`
(ReAct loop, step budget) and `runtime/llm.py` (model calls). This is the one component a shipped
method edits deterministically: **`methods/raise_step_cap/run.py:41-53`** rewrites the single
`MAX_STEPS` line in `base_harness/loop/agent.py` from 32 to 50 (and refuses on any other harness,
because the marker is absent). **`methods/echo_base/run.py:37-49`** inserts a hard-coded regex
answer-extractor into `agent.py`. Both land on `control` under `loop`'s map.

**`memory`** — what the harness keeps between steps or sessions; `miniagent`'s only declared file is
`runtime/trace.py`, the file-as-bus trace that every step is appended to. `deepseek-harness` and
`openclaw` declare no memory file at all. **No shipped method edits it from code.**

**`verification`** — checking the result before the harness declares success. **No launchable base
harness declares a file for it**, so no round on a shipped harness can be attributed to it, however
many runs are made. A harness that wanted a verification claim would first have to name the file
that does the checking (for example a `runtime/verify.py` in `miniagent`).

**`recovery`** — what the harness does after a failure: retries, fallbacks, re-planning. Also
declared by **no launchable harness**. `loop` has retry knobs (`submit_retries`, `parse_retries`,
`repeat_nudge_limit`) but its declaration puts `agent.py` under `control`, and it has no second
file to name — the retries are in the same loop.

**`delegation`** — splitting work across sub-agents or routing between models. In six harnesses this
is `agent.py`, the entrypoint that would spawn or route; `miniagent` also keeps its SDK bridge in
`bridge.py`, but that file is declared as `cost`, not as `delegation`. **No shipped method determines it
from code.** `agent.py` is the file the editor most often rewrites, and which component that is
depends on the harness's map -- `control` on `loop`, `delegation` on the other six that declare it --
not on the path. That is the whole reason attribution is read per harness.

**`reliability`** — what the harness promises about its own runtime: `openclaw` declares
`runtime-identity.json` (source, version, upstream commit, node/entrypoint paths). It is a
declaration file, and a round could edit it, but no method does; `openclaw` is the only harness that
declares the component. (In `tools/dataset_classes.py` `UNCOVERED_FUNCTIONS` also records that only
one dataset, `tau3_bench`, makes `reliability` score-relevant.)

**`cost`** — how many model calls the harness makes and what it accounts for them. `miniagent`
declares `bridge.py`, the native-process bridge that translates I/O for the MiniAgent loop and
carries the transport observer's call counts. **No shipped method edits it from code.**

**`safety`** — what the harness refuses or confines: `miniagent` declares `runtime/web_config.py`
(the endpoint configuration the web tools and preflight share), `deepseek-harness` declares
`surface.json` (the DSH runtime profile a patch is applied to). Both are configuration, and a round
could edit either; no method does from code.

**What this section cannot say, and why.** Nine of the ten components have no method that edits
them *in code*. That is not an oversight in the ports: the platform's design puts the edit in the
model's hands (`methods/editor.py` `ask`), and each method supplies framing, not a patch. The
archive even shows this happening — 9 of the 292 moved-path points also touched
`artifacts/proposal_card.json`, which is TTHE's proposal card, not harness source. So "which
component does method X edit" is a question only the round's `editable_surface_components` can
answer, and that is exactly the field the 2026-11-02 archive does not have.

## 4. What would make this number real

A run produces attributed points when three things hold, and the third is the one the old archive
failed:

1. **the harness declares `components`.** One key in `harness.json`; all seven launchable harnesses
   have one since 2026-11-02 (`base_harness/*/harness.json`), `oracle` does not (it is never
   edited). A declaration that covers every shipped file is what makes `no_component_named` mean
   "outside the ten" instead of "the map was never written".
2. **the round edits a path the map names.** On `loop` that means `agent.py` and nothing else:
   `harness.json` is declared `control` but `methods/apply.py:38` refuses to rewrite it.
3. **the round is measured.** `harnessgrad/loops/mode_a.py:897-919` writes
   `editable_surface_touched` (the paths `git diff` moved, against the *previous* round) and
   `editable_surface_components` (`components.attribute()` of those paths against the declaration
   in the harness tree) on every round ≥ 1. Nothing else has to be enabled; it is on by default.

The first attributed point in this repository was produced by a known edit, so the field has a
known answer to check against: `methods/raise_step_cap` raises `loop`'s `MAX_STEPS` and reports
`agent.py` and nothing else. Run on `demo` with the mock backend, in this worktree, under `/tmp`:

```bash
export TMPDIR=/tmp HG_AGENT_BACKEND=mock
python3 driver.py --dataset demo --sampling all --harness loop --rounds 2 \
    --run-id t21-attributed \
    --method-entrypoint "python $PWD/methods/raise_step_cap/run.py" \
    --runs-root /tmp/hg-t21-runs --work-root /tmp/hg-t21-work
```

Measured 2026-11-02: 5.95 s wall, 4 demo tasks, `0.000` at rounds 0/1/2 (the mock backend does not
answer the tasks, so **the score does not move — nothing here is an improvement claim**). What moved
is the record. The curve the run wrote:

| round | `editable_surface_touched` | `editable_surface_components` |
| ---: | --- | --- |
| 0 | *(field absent: H0 point)* | *(field absent)* |
| 1 | `["agent.py"]` | `{"components": ["control"], "unattributed": []}` |
| 2 | `[]` | `{"components": [], "unattributed": []}` |

Round 1 is the first **attributed** point in the archive: the edit landed on a declared path, and the
harness named the component. Round 2 is `no_path_moved` — `raise_step_cap` finds its marker already
replaced and reports no change, which is why the driver's summary says "no change reported". Running
the attribution tool over that run says so:

```
$ python3 tools/component_attribution.py --runs /tmp/hg-t21-runs
Runs: 1 discovered, 1 included, 0 skipped (all in [No data](#no-data)). Curve points in included studies: 3, attributed 1, unattributed 2.
**The denominator that matters.** 2 of those 3 curve point(s) carry `editable_surface_components`, so an attribution was attempted on them. ...
1 of 2 curve point(s) that carry the field name a component (1 of 3 in total). Every count below is a count of those attributed rounds; an unattributed round may have touched any component and is never counted as one.
...
| control   | 1      | 1    | raise_step_cap 1 |
```

So the mechanism works end to end: **declared path → moved path → `components.attribute()` →
attributed point → a row in the tool's table.** The 0-of-1008 is the absence of step 1 in the old
archive, not a failure of step 3.

Two limits worth stating before anyone plans a run around this:

* **On `loop` only `control` is attributable** — the other nine components have no declared file
  there. A run that needs `context` or `tools` in the answer has to use `miniagent` (7 components
  declared, all reachable) or `openclaw` (5 declared, 4 reachable: its `control` is `harness.json`
  alone).
* **One attributed point is not a result.** It is a record that the round moved a declared path;
  `docs/harness-evolution-protocol.md` §4 still requires the paired test, the floor guard and a
  control arm before a round counts as an improvement. The mock run above scores `0.000` at every
  round and is not an experiment.

## 5. What changed in the tool while writing this

Three things were wrong or missing, all of them about making the ratio readable rather than about
the computation (commit message carries the schema change):

* **`outside_declared_map` → `no_component_named`.** The old state name asserted that the edit was
  outside the harness's declared map. `components.attribute` cannot tell that case from a harness
  that declared no map, and on this archive the assertion is false for all 292 points (§2). The
  state now names what the record shows.
* **The denominator is printed.** `points.with_field` and `points.without_field` are new; the
  markdown headline and the stderr line read "0 of 310 … (1008 in total)" instead of "0 of 1008".
* **Discarded runs report their curve points.** `points.in_discarded_runs` (54 here) and the
  per-run `points` column in `No data` make the exclusion visible instead of implicit.

`tests/quality/test_component_attribution.py` pins all three (14 passed), including one that a point
whose moved path *is* inside the checked-in map is never called "outside".

## 6. Reproduce

All commands were run from the `/tmp/hg-t21` worktree on 2026-11-02, with `export TMPDIR=/tmp`.

```bash
# 1. the tool's own answer + the two denominators
python3 tools/component_attribution.py \
    --runs /mnt/20t/xubuqiang/hg-work/rsi10/runs \
    --json /tmp/attr.json --md /tmp/attr.md

# 1b. the same over every runs/ root under hg-work
python3 tools/component_attribution.py --runs \
  /mnt/20t/xubuqiang/hg-work/rsi10/runs,/mnt/20t/xubuqiang/hg-work/rsi9/runs,\
/mnt/20t/xubuqiang/hg-work/alfworld-h1/runs,/mnt/20t/xubuqiang/hg-work/aime-diag/runs,\
/mnt/20t/xubuqiang/hg-work/ds-pilots/runs --json /tmp/attr-all.json

# 2. the census: runs, curves, points, field, and how many points had a declared map
python3 - <<'EOF'
import json, sys
sys.path.insert(0, ".")
from tools import component_attribution as ca

ROOT = "/mnt/20t/xubuqiang/hg-work/rsi10/runs"
runs = curves = points = field = named = snapshots = with_map = 0
discarded = {"runs": 0, "with_curve": 0, "points": 0}
for run_dir, is_discarded in ca._discover([ROOT]):
    runs += 1
    if is_discarded:
        discarded["runs"] += 1
        if (run_dir / "curve.jsonl").is_file():
            ignored, problem = ca._read_curve(run_dir / "curve.jsonl")
            if not problem:
                discarded["with_curve"] += 1
                discarded["points"] += len(ignored)
        continue
    if not (run_dir / "curve.jsonl").is_file():
        continue
    curves += 1
    for point in ca._read_curve(run_dir / "curve.jsonl")[0]:
        points += 1
        block = point.get("editable_surface_components")
        if isinstance(block, dict):
            field += 1
            named += 1 if block.get("components") else 0
        manifest = run_dir / f"state_after_round{point.get('round')}" / "harness.json"
        if manifest.is_file():
            snapshots += 1
            mapping = json.loads(manifest.read_text()).get("components") or {}
            with_map += 1 if mapping else 0
print(dict(runs=runs, runs_with_curve=curves, points=points, with_field=field,
           naming_a_component=named, points_with_snapshot=snapshots,
           snapshots_declaring_a_map=with_map, discarded=discarded))
EOF

# 3. the declaration's date; the manifest before it has no `components`
git log --format='%h %ad %s' --date=short --follow -- base_harness/loop/harness.json
git show 3a8dfd0:base_harness/loop/harness.json
git log -1 --format='%h %ad %s' --date=short e004550 -- base_harness/loop/harness.json

# 4. the newest run in the archive, and how the run ids are dated
python3 - <<'EOF'
import os, re
root = "/mnt/20t/xubuqiang/hg-work/rsi10/runs"
stamp = re.compile(r"(\d{8}T\d{4,6}Z)")
days = {}
for name in sorted(os.listdir(root)):
    if not os.path.isdir(os.path.join(root, name)):
        continue
    if not os.path.isfile(os.path.join(root, name, "curve.jsonl")):
        continue
    match = stamp.search(name)
    days[match.group(1)[:8] if match else "(undated)"] = \
        days.get(match.group(1)[:8] if match else "(undated)", 0) + 1
print(dict(sorted(days.items())))
EOF

# 5. the reconstruction in §2: what today's loop map would have made of the recorded paths
python3 - <<'EOF'
import json, os, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, ".")
from harnessgrad import components as C

root = "/mnt/20t/xubuqiang/hg-work/rsi10/runs"
declared = C.declared_components(Path("base_harness/loop"))
touched, named = Counter(), Counter()
for name in sorted(os.listdir(root)):
    curve = os.path.join(root, name, "curve.jsonl")
    if not os.path.isfile(curve):
        continue
    for line in open(curve):
        if not line.strip():
            continue
        point = json.loads(line)
        if not isinstance(point.get("editable_surface_components"), dict):
            continue
        paths = point.get("editable_surface_touched")
        if not isinstance(paths, list) or not paths:
            touched[("(none)",)] += 1
            continue
        touched[tuple(sorted(paths))] += 1
        named[tuple(C.attribute(paths, declared)["components"])] += 1
print("today's loop map:", declared)
print("touched path sets:", dict(touched))
print("would name:", dict(named))
EOF

# 6. where each component is declared, and whether a method can reach it
python3 - <<'EOF'
import json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, ".")
from harnessgrad.components import COMPONENTS

PROTECTED = {"harness.json"}   # methods/apply.py:38, top level only
launch = json.loads(Path("base_harness/launchable.json").read_text())["harnesses"]
rows = defaultdict(list)
for name in sorted(launch):
    manifest = json.loads((Path("base_harness") / name / "harness.json").read_text())
    for component, files in (manifest.get("components") or {}).items():
        if component in COMPONENTS:
            rows[component].append((name, files))
for component in COMPONENTS:
    entries = rows.get(component, [])
    reachable = [h for h, files in entries
                 if any(not (Path(f).parent == Path(".") and Path(f).name in PROTECTED)
                        for f in files)]
    print(f"{component:13s} {len(entries)}/7  reachable={','.join(reachable) or '(none)'}")
EOF

# 7. how each method produces its candidate (model-authored edit vs programmatic)
for m in methods/*/; do
  [ -f "$m/run.py" ] || continue
  printf "%-14s kind=%-10s editor_calls=%s programmatic_agent_edit=%s\n" \
    "$(basename "$m")" \
    "$(python3 -c "import json;print(json.load(open('$m/method.json')).get('kind','-'))" 2>/dev/null)" \
    "$(grep -c 'editor\.\(ask\|ask_for_json\|propose_and_apply\)' "$m/run.py" 2>/dev/null)" \
    "$(grep -c 'agent\.write_text\|(dest / "agent.py")' "$m/run.py" 2>/dev/null)"
done

# 8. the first attributed point: one measured round on a declared path, mock backend
export HG_AGENT_BACKEND=mock
python3 driver.py --dataset demo --sampling all --harness loop --rounds 2 \
    --run-id t21-attributed \
    --method-entrypoint "python $PWD/methods/raise_step_cap/run.py" \
    --runs-root /tmp/hg-t21-runs --work-root /tmp/hg-t21-work
python3 - <<'EOF'
import json
for line in open("/tmp/hg-t21-runs/t21-attributed/curve.jsonl"):
    if not line.strip():
        continue
    point = json.loads(line)
    print(point.get("round"),
          "touched:", point.get("editable_surface_touched", "(absent)"),
          "components:", point.get("editable_surface_components", "(absent)"))
EOF
python3 tools/component_attribution.py --runs /tmp/hg-t21-runs

# 9. the tool's own tests
python3 -m pytest tests/quality/test_component_attribution.py --basetemp=/tmp/pytest-t21 -q
```
