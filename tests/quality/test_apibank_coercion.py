"""`ab_env.ApiBank.api_call` must coerce a declared `bool` the way the official executor does.

The defect
----------
`data/datasets/api_bank/lib/ab_env.py` reproduces `ToolManager.api_call` from the vendored tree,
and until this test it coerced only `int` and `float`, passing every other declared type through
untouched. The official code (`tool_manager.py:166-169` of the vendored tree) has one more branch:

    elif required_type == 'bool':
        processed_parameters[input_key] = input_value == 'True'
    else:
        raise Exception('invalid parameter type.')

So the string `"False"` is officially the boolean `False`, and a real `True` is **also** `False`
(the comparison is against the string, exactly as written upstream). Under the old helper the
string `"False"` stayed a string.

Why it is observable (but does not move the published number)
------------------------------------------------------------
`apis/timed_switch.py` declares `'on': {'type': 'bool'}` and its checker compares
`response['input']['on'] != groundtruth['input']['on']`; the gold rows store a JSON boolean. So
the coercion is part of the metric. It changes nothing in the published 262/508 reproduction
because all 3 `TimedSwitch` rows (and all 5 `CancelTimedSwitch` rows) name `device_id`, which
neither class declares -- they already fail with `invalid parameter name` before any argument is
coerced. The rule is still wrong, and a future sample that names only declared parameters would
hit it.

The test double
---------------
`ApiBank` is built with `__new__` and a hand-populated `classes`/`databases` so the coercion is
exercised **without** unpacking the vendored tree or importing the 56 API modules (that import
pulls `torch` and takes minutes). The fake class declares what `TimedSwitch` declares in the
parts that matter: a `bool`-typed `on`, and the same checker comparison.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tarfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The staged vendored tree (`data/datasets/api_bank.py::DATASET_ROOT`), read directly from the
#: tarball so the shipped `timed_switch.py` is exercised without unpacking 340 files.
VENDOR_TARBALL = (Path(os.environ.get("HG_API_BANK_DATA", "/mnt/20t/xubuqiang/datasets/api-bank"))
                  / "_src" / "api-bank-vendor.tar.gz")


def _load_ab_env():
    """Import `ab_env.py` by path: `data.datasets.api_bank` is the refusing adapter module.

    The directory `data/datasets/api_bank/` cannot be reached as `data.datasets.api_bank.lib`
    because the adapter module `data/datasets/api_bank.py` owns that name, which is also why
    nothing in the tree imports the helper yet.
    """
    path = ROOT / "data" / "datasets" / "api_bank" / "lib" / "ab_env.py"
    spec = importlib.util.spec_from_file_location("ab_env_coercion_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingApi:
    """The smallest stand-in for a vendored API: records the kwargs it was called with."""

    description = "test double"
    input_parameters: dict = {}
    output_parameters: dict = {}
    database_name = None

    def __init__(self) -> None:
        self.received: dict | None = None

    def call(self, **kwargs):
        self.received = kwargs
        return {"api_name": "Recording", "input": dict(kwargs), "output": "success",
                "exception": None}

    # ``timed_switch.py``'s checker, reduced to the line this defect is about.
    def check_api_call_correctness(self, response, groundtruth) -> bool:
        return response["input"]["on"] == groundtruth["input"]["on"]


def _bank(input_parameters: dict):
    ab_env = _load_ab_env()
    bank = ab_env.ApiBank.__new__(ab_env.ApiBank)
    bank.root = ROOT
    bank.databases = {}
    bank.unavailable = {}
    bank._inited = {}
    bank.classes = {"Switch": _RecordingApi}
    _RecordingApi.input_parameters = input_parameters
    return bank


#: Pinned against `tool_manager.py:166-169` -- ``input_value == 'True'``, nothing cleverer.
BOOL_CASES = [
    ("True", True),
    ("False", False),
    ("false", False),
    ("TRUE", False),
    ("", False),
    ("1", False),
    (True, False),     # a real bool is not the *string* 'True'
    (False, False),
    (1, False),
    (None, False),
]


@pytest.mark.parametrize("raw, expected", BOOL_CASES)
def test_declared_bool_is_the_string_comparison_upstream_uses(raw, expected):
    bank = _bank({"on": {"type": "bool", "description": "switch on or off"}})
    bank.api_call("Switch", {"on": raw})
    received = bank._inited["Switch"].received["on"]
    assert received is expected, (
        f"declared bool + {raw!r} must coerce to {expected!r} (official: "
        f"`input_value == 'True'`), got {received!r}")


def test_the_gold_timed_switch_value_now_matches_its_recorded_result():
    """End-to-end on the shape that matters: param `"False"`, recorded result `false`."""
    bank = _bank({"on": {"type": "bool"}})
    recorded = {"input": {"on": False}, "output": "success", "exception": None}
    reply = bank.api_call("Switch", {"on": "False"})
    assert reply["input"]["on"] is False
    assert _RecordingApi().check_api_call_correctness(reply, recorded) is True


@pytest.mark.parametrize("raw, expected", [("42", 42), (42, 42), (-7, -7), ("0", 0)])
def test_declared_int_is_unchanged(raw, expected):
    bank = _bank({"n": {"type": "int"}})
    bank.api_call("Switch", {"n": raw})
    received = bank._inited["Switch"].received["n"]
    assert received == expected and isinstance(received, int) and not isinstance(received, bool)


@pytest.mark.parametrize("raw, expected", [("3.5", 3.5), (3.5, 3.5), (2, 2.0)])
def test_declared_float_is_unchanged(raw, expected):
    bank = _bank({"x": {"type": "float"}})
    bank.api_call("Switch", {"x": raw})
    received = bank._inited["Switch"].received["x"]
    assert received == expected and isinstance(received, float)


@pytest.mark.parametrize("kind", ["str", "list", "list(str)"])
def test_declared_string_and_list_types_pass_through(kind):
    value = ["a", "b"] if kind.startswith("list") else "text"
    bank = _bank({"v": {"type": kind}})
    bank.api_call("Switch", {"v": value})
    assert bank._inited["Switch"].received["v"] == value


@pytest.mark.parametrize("kind", ["dict", "boolean", "Int", "List(str)"])
def test_an_unknown_declared_type_raises_exactly_like_upstream(kind):
    """Upstream's final `else` is `raise Exception('invalid parameter type.')`.

    No declared type in the vendored tree is outside the handled set (`str` 121, `list(str)` 4,
    `int` 4, `list` 3, `bool` 1), so this cannot cost an API-Bank row -- it is fidelity, not a
    behaviour change with a measured victim.
    """
    bank = _bank({"v": {"type": kind}})
    with pytest.raises(Exception) as excinfo:
        bank.api_call("Switch", {"v": "anything"})
    assert type(excinfo.value) is Exception, (
        "upstream raises a bare Exception, not AssertionError/TypeError: "
        f"got {type(excinfo.value).__name__}")
    assert str(excinfo.value) == "invalid parameter type."
    assert "Switch" not in bank._inited, "the API must not be called when a type is rejected"


def test_an_undeclared_parameter_name_still_raises_first():
    """The blast radius: the 8 TimedSwitch/CancelTimedSwitch rows die on `device_id`."""
    bank = _bank({"name": {"type": "str"}, "time": {"type": "str"},
                  "on": {"type": "bool"}})
    with pytest.raises(AssertionError) as excinfo:
        bank.api_call("Switch", {"device_id": "123456", "time": "2022-03-01 22:00:00",
                                 "on": "False"})
    assert "invalid parameter name" in str(excinfo.value)


@pytest.mark.skipif(not VENDOR_TARBALL.is_file(),
                    reason="the vendored API-Bank tarball is not staged on this host")
def test_the_shipped_timed_switch_class_receives_a_boolean(monkeypatch):
    """The same rule, against the real vendored class rather than a double.

    `apis/timed_switch.py` needs only `apis.API` from its package, so a one-attribute stand-in
    keeps this fast: importing the real `apis` package loads all 56 APIs (torch included).
    """
    package = types.ModuleType("apis")

    class API:  # noqa: D401 - the base class the module inherits from
        """Stand-in for `apis.api.API`."""

    package.API = API
    monkeypatch.setitem(sys.modules, "apis", package)

    with tarfile.open(VENDOR_TARBALL) as archive:
        source = archive.extractfile("api-bank/apis/timed_switch.py").read().decode()
    module = types.ModuleType("apis.timed_switch")
    exec(compile(source, "api-bank/apis/timed_switch.py", "exec"), module.__dict__)

    assert module.TimedSwitch.input_parameters["on"]["type"] == "bool"
    bank = _bank({})
    bank.classes = {"TimedSwitch": module.TimedSwitch}
    reply = bank.api_call("TimedSwitch", {"name": "smart bulb",
                                          "time": "2022-03-01 22:00:00",
                                          "on": "False"})
    assert reply["input"]["on"] is False
    gold = {"input": {"name": "smart bulb", "time": "2022-03-01 22:00:00", "on": False},
            "output": "success", "exception": None}
    assert module.TimedSwitch().check_api_call_correctness(reply, gold) is True
