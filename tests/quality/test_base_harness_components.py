"""Every launchable base harness declares which of its files implements which component.

**Why this file exists.** `harnessgrad.components` has been shipped and wired into the curve
point since `editable_surface_components` was added, but no base harness declared the map, so
in the 1008 recorded curve points **zero** carried a component attribution. A capability with
zero data is indistinguishable from a missing one: a reader could not tell "no round ever
touched a component" from "nobody ever wrote the declaration". This test pins the declaration
itself, file by file, so the next harness cannot land without one.

Four properties, in the order a broken declaration would fail them:

1. every harness in `base_harness/launchable.json` declares a map, and it names at least one
   real component (a map of only `unattributed` would satisfy "declares something" while
   attributing nothing);
2. every declared path exists, and every shipped file is either declared under a component or
   explicitly listed as `unattributed` -- no silent gaps, because a gap is what makes an edit
   read as "the map failed to name this" instead of "the harness says nobody owns this";
3. every declared name is in `components.COMPONENTS`, with `components.UNATTRIBUTED` the one
   legal non-component key;
4. `components.attribute` on a touched-file list taken from the harness's own files returns
   the components a reader would expect -- the reading is wired, not just syntactically valid.

Plus the negative case: an unknown component name raises the module's own error type. That one
runs on a **temporary copy** of a harness (there is no reason to edit a real manifest to learn
that the validator validates).

**The judgement calls this mapping had to make**, recorded here because JSON has no comments
and a declaration with no stated reasoning is the kind of thing that gets "cleaned up" by
someone who disagrees:

* *One component per file, not one file per component.* `components.py` permits a path under
  several components, and for a real single-file harness it would be defensible. These
  declarations deliberately pick the **dominant** concern instead: a mapping that says
  `loop/agent.py` is context + tools + control + memory + cost is true and useless, because it
  cannot distinguish a prompt edit from a step-limit edit -- which is the whole question the
  attribution is asked. The mixture is noted in `harnessgrad/components.py` and below.
* *`harness.json` -> `control` in all seven.* The manifest names the entrypoint and the launch
  parameters (`env_kinds`, `runtime_paths`, `install`), so its dominant answer to "what did this
  edit change" is what starts and how it starts. It is a genuinely mixed file: `env_kinds` and
  `runtime_paths` are also a safety boundary and a capability gate (`tools/validate_harness.py`).
* *The five CLI adapters -> `delegation`.* `deepseek-harness`, `miniswe`, `miniagent`,
  `openclaw`, `codex` and `opencode` all hand the task to another agent process (DSH, mini,
  the vendored MiniAgent runtime, OpenClaw, Codex, OpenCode) and translate what it did back
  into `answer.txt` + `trace.jsonl`. Their `agent.py` also builds the task prompt (context),
  enforces a deadline (reliability), counts calls/tokens (cost) and decides artifact-vs-text
  precedence (verification); delegation is dominant because that is what the file *is*.
* *`miniagent` is the one harness whose loop ships as editable source*, so it is the only one
  where `control`, `memory`, `tools` and `context` each get their own files. Its
  `runtime/trace.py` is `memory` (durable per-step state on disk) rather than `verification` or
  `reliability`; `bridge.py` is `cost` (it refuses an over-budget call before the wire and
  counts the calls) rather than `reliability`, even though it also reaps descendants.
* *`openclaw/config.json` -> `tools`*, because what it really decides is which tools exist and
  how `exec` runs; it also carries the provider/model block (cost, context) and an exec timeout
  (reliability).
* *`deepseek-harness/knobs.json` -> `context`*, because its payload is the prompt preamble and
  the task-inputs header, and that is the reason it was split out of the launcher;
  `limits.dump_config_timeout_s` is reliability. `deepseek-harness/surface.json` -> `safety`
  (it is the editable-surface permission boundary). `openclaw/runtime-identity.json` ->
  `reliability`, because `agent.py` refuses the run when the pinned version or path drifted.
* *Mocks, licences, provenance and pure data are `unattributed`.* `mock_model.py` (miniswe,
  openclaw), `README.md`, `requirements.txt`, `FLASHRSI-LICENSE.txt`, `PROVENANCE.json`,
  `dsh_farm.tsv`, `runtime/__init__.py` (empty package marker) and miniagent's
  `components.yaml` -- a vendored **FlashRSI** taxonomy (`code/skill/prompt/tool/config`) that
  matches neither this platform's ten names nor its files, and that no code here reads.
* *`verification` and `recovery` are claimed by no shipped file, and that is the honest count.*
  Answer/artifact checking exists, but inside adapters whose dominant job is delegation; no
  shipped harness rolls back, snapshots or resumes, so `recovery` has nothing to point at.
  Declaring something just to fill the vocabulary would be exactly the "mapping nobody
  believes" `components.py` warns about.
* *`opencode/.gitignore` is not declared because it is not shipped*: the platform stages a
  harness with `shutil.copytree`, and `.gitignore` is repository plumbing (see `_shipped`).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import components  # noqa: E402

BASE = ROOT / "base_harness"
LAUNCHABLE = tuple(
    json.loads((BASE / "launchable.json").read_text(encoding="utf-8"))["harnesses"])

#: The harnesses this declaration pass covered. A subset check rather than an equality one:
#: an eighth launchable harness must inherit the requirement (the parametrized tests below run
#: over `LAUNCHABLE`), while a harness *leaving* this list is a decision somebody has to see
#: here, because that is what silently removes the coverage this file exists to guarantee.
DECLARED_IN_THIS_PASS = (
    "loop", "deepseek-harness", "miniswe", "miniagent", "openclaw", "codex", "opencode")

#: Not part of the harness that gets staged into a task workspace: build output and the
#: repository's own VCS bookkeeping. `shutil.copytree` (the platform's staging call) would not
#: copy a `__pycache__` a run had not created yet either, and `*.pyc` is that same output.
SKIP_DIR_NAMES = {"__pycache__"}
SKIP_SUFFIXES = {".pyc"}
SKIP_FILE_NAMES = {".gitignore"}


def _shipped(root: Path) -> list[str]:
    """Every file the harness ships, relative and POSIX, in sorted order."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if SKIP_DIR_NAMES.intersection(relative.parts):
            continue
        if path.suffix in SKIP_SUFFIXES or path.name in SKIP_FILE_NAMES:
            continue
        found.append(relative.as_posix())
    return found


def _declared_paths(declared: dict[str, list[str]]) -> set[str]:
    return {path for files in declared.values() for path in files}


def test_the_launchable_list_still_names_the_harnesses_this_pass_covered():
    """护栏本身也要有护栏:清单缩水时下面每一条参数化测试都会"通过"。"""
    assert set(DECLARED_IN_THIS_PASS) <= set(LAUNCHABLE), (
        "a harness this declaration pass covered is no longer launchable -- the parametrized "
        "tests below would silently stop checking it")
    for name in LAUNCHABLE:
        assert (BASE / name / "harness.json").is_file(), (
            f"{name} is in launchable.json but has no harness.json")


@pytest.mark.parametrize("name", LAUNCHABLE)
def test_every_launchable_harness_declares_components(name):
    """声明是这一层的全部内容:没有它,曲线点上的归因永远是空的。"""
    declared = components.declared_components(BASE / name)
    assert declared, (
        f"{name}/harness.json declares no `components` map, so every round of this harness "
        f"records `editable_surface_components.components: []` -- a capability with no data "
        f"is indistinguishable from a missing one")
    real = [key for key in declared if key in components.COMPONENTS]
    assert real, (
        f"{name} declares only {sorted(declared)}; a map that names no real component "
        f"attributes nothing while looking declared")


@pytest.mark.parametrize("name", LAUNCHABLE)
def test_every_declared_name_is_a_component_or_the_unattributed_value(name):
    """词汇表是封闭的;`unattributed` 是唯一合法的非组件取值。"""
    declared = components.declared_components(BASE / name)
    unknown = sorted(set(declared) - set(components.COMPONENTS) - {components.UNATTRIBUTED})
    assert not unknown, (
        f"{name} declares {unknown}, which are neither one of the ten components "
        f"({', '.join(components.COMPONENTS)}) nor {components.UNATTRIBUTED!r}")
    assert components.UNATTRIBUTED not in components.COMPONENTS, (
        "the unattributed value must stay out of the vocabulary: it is the absence of a "
        "component, and putting it in COMPONENTS would make attribute() return it as one")


@pytest.mark.parametrize("name", LAUNCHABLE)
def test_every_shipped_file_is_declared_or_explicitly_unattributed(name):
    """没有静默的空洞:漏声明的文件会被读成"映射没认出它",而不是"harness 说它不属于任何组件"。"""
    root = BASE / name
    declared = components.declared_components(root)
    claimed = _declared_paths(declared)
    shipped = set(_shipped(root))

    missing_on_disk = sorted(path for path in claimed if not (root / path).is_file())
    assert not missing_on_disk, (
        f"{name} declares {missing_on_disk}, which are not files in the harness -- a moved or "
        f"deleted file leaves an attribution that claims an edit to something that is not there")

    unaccounted = sorted(shipped - claimed)
    assert not unaccounted, (
        f"{name} ships {unaccounted} without declaring them. Add each to the component it "
        f"implements, or to `{components.UNATTRIBUTED}` if no component owns it")

    stale = sorted(claimed - shipped)
    assert not stale, (
        f"{name} declares {stale}, which the shipped-file rule does not see -- either the file "
        f"is not there or it is one of the excluded plumbing files")


@pytest.mark.parametrize("name, touched, expected", [
    # `loop` is the single-file ReAct loop: the whole harness is one control file, and its
    # `requirements.txt` is a dependency list no component owns.
    ("loop", ["agent.py"], ["control"]),
    ("loop", ["agent.py", "new_helper.py"], ["control"]),
    # `miniagent` is the vendored runtime: four components really do live in four files.
    ("miniagent", ["agent.py", "runtime/agent.py", "prompts/system.md", "bridge.py"],
     ["context", "control", "cost", "delegation"]),
    # `deepseek-harness` separates the prompt payload from the launcher, which is the point of
    # `knobs.json` existing at all.
    ("deepseek-harness", ["agent.py", "knobs.json"], ["context", "delegation"]),
    ("openclaw", ["prompt.md", "config.json"], ["context", "tools"]),
])
def test_attribution_reads_a_rounds_touched_files(name, touched, expected):
    """接上了,不只是语法正确:真 harness 的真文件必须落回读者预期的组件。"""
    declared = components.declared_components(BASE / name)
    got = components.attribute(touched, declared)
    assert got["components"] == expected, got
    claimed_touched = got["unattributed"]
    assert claimed_touched == sorted(
        [path for path in touched if path not in _declared_paths(declared)]), got


@pytest.mark.parametrize("name, touched", [
    # A data file the harness itself says nobody owns: it sorts under `unattributed`, and it
    # must NOT be swallowed into an empty pair of lists (that would read as "nothing moved").
    ("loop", ["requirements.txt"]),
    ("deepseek-harness", ["dsh_farm.tsv"]),
])
def test_a_declared_unattributed_file_is_reported_as_unattributed(name, touched):
    declared = components.declared_components(BASE / name)
    assert components.UNATTRIBUTED in declared
    got = components.attribute(touched, declared)
    assert got == {"components": [], "unattributed": sorted(touched)}, got


def test_an_unknown_component_name_is_refused(tmp_path):
    """反例:一个不在词汇表里的组件名必须抛模块自己的错误类型。

    在**临时副本**上做,因为要验证的是校验器,不是某个真 harness 的清单。
    """
    copied = tmp_path / "loop"
    shutil.copytree(BASE / "loop", copied, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = copied / "harness.json"
    declaration = json.loads(manifest.read_text(encoding="utf-8"))

    broken = dict(declaration["components"], prompting=["agent.py"])
    manifest.write_text(json.dumps(dict(declaration, components=broken), indent=2),
                        encoding="utf-8")
    with pytest.raises(components.ComponentDeclarationError, match="not a harness component"):
        components.declared_components(copied)

    # The real harness is untouched: the copy is where the bad name lives.
    assert "prompting" not in json.loads(
        (BASE / "loop" / "harness.json").read_text(encoding="utf-8"))["components"]


def test_a_path_claimed_by_a_component_and_by_unattributed_is_refused(tmp_path):
    """自相矛盾要报错,而不是由"哪个键先被读到"决定归因。"""
    copied = tmp_path / "loop"
    shutil.copytree(BASE / "loop", copied, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = copied / "harness.json"
    declaration = json.loads(manifest.read_text(encoding="utf-8"))
    broken = dict(declaration["components"])
    broken[components.UNATTRIBUTED] = ["agent.py"]
    manifest.write_text(json.dumps(dict(declaration, components=broken), indent=2),
                        encoding="utf-8")
    with pytest.raises(components.ComponentDeclarationError, match="both"):
        components.declared_components(copied)
