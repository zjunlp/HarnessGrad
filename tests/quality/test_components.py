"""The harness-component vocabulary and the attribution of an edit to it.

Two things are pinned here, and the first is the one that rots silently:

1. **There is one list.** `tools/dataset_classes.py::FUNCTIONS` and
   `harnessgrad.components.COMPONENTS` are the same object, because "which component does a
   dataset exercise" and "which component did a round's edit touch" are the same decomposition
   of a harness. Two copies is how a taxonomy rots -- the moment one grows, every claim phrased
   over the other is silently about a different set -- so the dataset side re-exports this one.

2. **Attribution is a reading, never a fence.** The platform's central design decision is that
   a harness is *entirely* editable; `INTERFACE.md` §4.1-4.3 draws the boundary around a
   harness's `method/` directory and its `accept` rule and nowhere else. So these tests pin
   that an edit the map cannot name is **reported as unattributed and never refused** --
   collapsing it into an empty component list would read as "nothing changed", which is the
   opposite of what happened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import components  # noqa: E402


def _harness(tmp_path: Path, declaration=None) -> Path:
    root = tmp_path / "h"
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "probe", "version": "1.0", "entrypoint": "agent.py"}
    if declaration is not None:
        manifest["components"] = declaration
    (root / "harness.json").write_text(json.dumps(manifest))
    (root / "agent.py").write_text("print('hello')\n")
    return root


def test_the_dataset_taxonomy_and_the_harness_side_are_one_list():
    """单一来源。分开维护两份,就是分类表腐烂的方式。"""
    sys.path.insert(0, str(ROOT / "tools"))
    import dataset_classes  # noqa: PLC0415

    assert dataset_classes.FUNCTIONS == components.COMPONENTS
    # Identity, not just equality: an equal-but-separate tuple is the copy that goes stale.
    assert dataset_classes.FUNCTIONS is components.COMPONENTS
    assert len(components.COMPONENTS) == 10
    assert len(set(components.COMPONENTS)) == 10


def test_a_harness_with_no_declaration_is_not_an_error(tmp_path):
    """没声明是合法答案,不是错误。强制声明会逼每个 harness 编一份自己都不信的映射。"""
    assert components.declared_components(_harness(tmp_path)) == {}
    # A tree with no manifest at all answers the same way rather than raising.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert components.declared_components(empty) == {}


def test_a_declaration_is_read_sorted_and_deduplicated(tmp_path):
    root = _harness(tmp_path, {"control": ["agent.py", "agent.py"],
                               "context": ["prompts.py", "agent.py"]})
    assert components.declared_components(root) == {
        "control": ["agent.py"], "context": ["agent.py", "prompts.py"]}


def test_the_vocabulary_is_closed(tmp_path):
    root = _harness(tmp_path, {"prompting": ["agent.py"]})
    with pytest.raises(components.ComponentDeclarationError, match="not a harness component"):
        components.declared_components(root)


def test_a_path_may_not_escape_the_harness(tmp_path):
    for bad in ("../outside.py", "/etc/passwd"):
        root = _harness(tmp_path / bad.replace("/", "_").replace(".", "_"),
                        {"control": [bad]})
        with pytest.raises(components.ComponentDeclarationError, match="not inside the harness"):
            components.declared_components(root)


@pytest.mark.parametrize("declaration", [
    ["control"],                       # a list, not a component -> files map
    {"control": "agent.py"},           # a string, not a list of paths
    {"control": [1]},                  # a path that is not a string
    {"control": [""]},                 # an empty path
])
def test_a_malformed_declaration_raises_rather_than_reading_as_empty(tmp_path, declaration):
    """读不出来就说读不出来。静默当成"没有声明",等于把一个 harness 的错误记成"未归因"。"""
    root = _harness(tmp_path, declaration)
    with pytest.raises(components.ComponentDeclarationError):
        components.declared_components(root)


def test_unreadable_json_raises(tmp_path):
    root = _harness(tmp_path)
    (root / "harness.json").write_text("{not json,")
    with pytest.raises(components.ComponentDeclarationError, match="not readable JSON"):
        components.declared_components(root)


def test_an_edit_is_attributed_to_every_component_that_claims_it():
    """一个文件可以同时属于多个组件 —— 单文件 harness 的 `agent.py` 本来就是这样。"""
    declared = {"control": ["agent.py"], "context": ["agent.py"], "memory": ["mem.py"]}
    got = components.attribute(["agent.py"], declared)
    assert got == {"components": ["context", "control"], "unattributed": []}


def test_an_edit_the_map_cannot_name_is_reported_not_refused():
    """**这条是重点。** 未声明的编辑仍然发生了,必须能被读出来。"""
    declared = {"control": ["agent.py"]}
    got = components.attribute(["agent.py", "new_helper.py"], declared)
    assert got["components"] == ["control"]
    assert got["unattributed"] == ["new_helper.py"], (
        "an edit the map cannot place must not vanish: an empty component list would read as "
        "'nothing changed'")


def test_no_declaration_reports_everything_as_unattributed():
    """harness 没声明时,每个被移动的路径都不可归因 —— 而不是"什么都没动"。"""
    got = components.attribute(["agent.py", "prompts.py"], {})
    assert got == {"components": [], "unattributed": ["agent.py", "prompts.py"]}


def test_nothing_touched_attributes_to_nothing():
    assert components.attribute([], {"control": ["agent.py"]}) == {
        "components": [], "unattributed": []}
