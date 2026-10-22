"""Fabricated, test-only BFCL source slices. Never an official scorer entrypoint.

No acquired file is modified. Imports/module initialization are deliberately not
executed: only the explicitly named original AST nodes below are compiled. The
Python/non-dotted-schema scope needs no model map or Java/JS runtime substitutes.
The I/O tripwire is test instrumentation, NOT containment supplied by BFCL.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PREFIX = "berkeley-function-call-leaderboard/bfcl_eval/"
CANDIDATE = "tests/fixtures/bfcl_ast_source/candidate-codex195/files/" + PREFIX
RETRY = "tests/fixtures/bfcl_ast_source/retry-codex227/files/" + PREFIX
EXTERNAL = "tests/fixtures/external_provenance_b5/bfcl/files/" + PREFIX
PARSER = CANDIDATE + "model_handler/utils.py"
CHECKER = RETRY + "eval_checker/ast_eval/ast_checker.py"
FORMAT = EXTERNAL + "utils.py"
PLATFORM = "data/datasets/bfcl_v3.py"
SCOPES = ["tests/fixtures/bfcl_ast_source", "tests/fixtures/external_provenance_b5/bfcl",
          "data/scaffolds/qa"]
EXTRA_PATHS = [PLATFORM, "data/scaffolds/qa/cli/qa", "data/__init__.py",
               "data/scaffolds/__init__.py", "data/datasets/__init__.py"]
PARSER_NAMES = {"ast_parse", "resolve_ast_call", "resolve_ast_by_type",
                "default_decode_ast_prompting", "convert_value", "_convert_value"}
PLATFORM_NAMES = {"bare_name", "parse_prediction", "gold_names", "score"}
SINK_NAMES = {"eval", "exec", "compile", "ast.parse", "ast.literal_eval", "getattr",
              "__import__", "importlib.import_module", "importlib.__import__",
              "subprocess.Popen", "subprocess.run", "subprocess.call",
              "subprocess.check_call", "subprocess.check_output", "os.system", "os.popen"}


def inventory():
    paths = {p.relative_to(ROOT).as_posix() for scope in SCOPES
             for p in (ROOT / scope).rglob("*.py")}
    paths.update(EXTRA_PATHS)
    rows = []
    sinks = []
    for name in sorted(paths):
        raw = (ROOT / name).read_bytes()
        rows.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        tree = ast.parse(raw, filename=name)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            if (callee not in SINK_NAMES and not callee.startswith(("pickle.", "yaml."))):
                continue
            cursor = node
            owner = "<module>"
            while cursor in parents:
                cursor = parents[cursor]
                if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = cursor.name
                    break
            sinks.append(dict(path=name, line=node.lineno, function=owner,
                              sink=callee, expression=ast.get_source_segment(raw.decode(), node)))
    return rows, sorted(sinks, key=lambda r: (r["path"], r["line"]))


def load_slice(path, names=None):
    """Compile exact retained nodes, preserving original bodies and line numbers.

    CHECKER selects every function and its type constants; other slices
    select named functions only. No module import, handler/config or runner is
    instantiated, and no absent helper is replaced with an invented helper.
    """
    raw = (ROOT / path).read_bytes()
    pins = json.loads((HERE / "source-inventory.json").read_text())
    pin = next(row for row in pins if row["path"] == path)
    assert hashlib.sha256(raw).hexdigest() == pin["sha256"], "retained source drift"
    tree = ast.parse(raw, filename=path)
    nodes = [node for node in tree.body
             if (isinstance(node, ast.FunctionDef) and (names is None or node.name in names))
             or (names is None and isinstance(node, ast.Assign))]
    if names is not None:
        assert {node.name for node in nodes} == names, "source slice changed"
    assert not any(node.decorator_list for node in nodes if isinstance(node, ast.FunctionDef))
    namespace = {"ast": ast, "re": re, "builtins": __import__("builtins")}
    code = compile(ast.Module(body=nodes, type_ignores=[]), path, "exec")
    with no_external_dependencies():
        exec(code, namespace)
    return namespace


_ACTIVE = []


def _audit(event, args):
    if _ACTIVE and (event == "open" or event == "import"
                    or event.startswith(("socket.", "subprocess.", "ctypes.", "os.exec",
                                         "os.spawn", "os.system", "os.fork", "os.posix_spawn"))):
        _ACTIVE[-1].append(event)
        raise RuntimeError("bfclcontain external-dependency tripwire: " + event)


sys.addaudithook(_audit)


@contextmanager
def no_external_dependencies():
    """Also fail after return if retained code swallowed a tripwire exception."""
    violations = []
    _ACTIVE.append(violations)
    try:
        yield
    finally:
        _ACTIVE.pop()
        assert not violations, "external dependency attempted: " + repr(violations)


def differential(cases):
    platform = load_slice(PLATFORM, PLATFORM_NAMES)
    upstream = load_slice(CHECKER)
    format_check = load_slice(FORMAT, {"is_function_calling_format_output"})
    records = []
    for original in cases:
        case = copy.deepcopy(original)
        assert case["id"].startswith("fabricated_")
        assert case["language"] == "Python" and case["category"] == "simple"
        assert all("." not in fn["name"] for fn in case["schema"]), "model map would be required"
        with no_external_dependencies():
            # This explicit adapter maps decoded calls to the platform's documented
            # answer grammar. It does NOT pretend the platform accepts full calls.
            prediction = "\n".join(name for call in case["calls"] for name in call)
            gold = {"functions": platform["gold_names"](case["reference"]), "subset": "simple"}
            platform_result = platform["score"](prediction, gold)
            if not format_check["is_function_calling_format_output"](case["calls"]):
                upstream_result = {"valid": False, "error_type": "ast_decoder:decoder_wrong_output_format"}
            else:
                upstream_result = upstream["ast_checker"](
                    case["schema"], case["calls"], case["reference"], "Python", "simple",
                    "UNBOUND-FABRICATED-NO-MODEL-MAP")
            pair = [platform_result["correct"], upstream_result["valid"]]
            comparison = ("agree_accept" if pair == [True, True] else
                          "agree_reject" if pair == [False, False] else
                          "projection_accepts_upstream_rejects" if pair == [True, False] else
                          "projection_rejects_upstream_accepts")
        records.append(dict(id=case["id"], input=case, platform_prediction=prediction,
                            platform=platform_result, upstream=upstream_result,
                            comparison=comparison))
        assert pair == case["expected_verdicts"], (case["id"], pair)
    return records


def boundary_controls():
    parser = load_slice(PARSER, PARSER_NAMES)
    platform = load_slice(PLATFORM, PLATFORM_NAMES)
    events = []

    def probe():
        events.append("entered")
        return 7

    parser["__bfclcontain_probe__"] = probe
    platform["__bfclcontain_probe__"] = probe
    # Safe controls precede ONE finding. No shell/file/network payloads and no
    # further exploit escalation are attempted after the confirmed finding.
    controls = [
        ("expression_function_name", "(__bfclcontain_probe__())(value=7)",
         [{"": {"value": 7}}]),
        ("quoted_code_argument", 'fabricated_echo(value="__bfclcontain_probe__()")',
         [{"fabricated_echo": {"value": "__bfclcontain_probe__()"}}]),
        ("nested_dispatch_without_binop", "fabricated_echo(value=fabricated_inner(value=__bfclcontain_probe__()))",
         [{"fabricated_echo": {"value": {"fabricated_inner": {"value": "__bfclcontain_probe__()"}}}}]),
    ]
    results = []
    for name, payload, _ in controls:
        events.clear()
        with no_external_dependencies():
            actual = platform["score"](payload, {"functions": ["fabricated_echo"], "subset": "simple"})
        assert actual["correct"] is False and events == []
        results.append(dict(id="platform_raw_" + name, input=payload, observed=actual,
                            probe_events=[], unsafe_observation="probe_events contains entered",
                            outcome="raw_call_text_rejected_without_execution"))
    for name, payload, expected in controls:
        events.clear()
        with no_external_dependencies():
            actual = parser["default_decode_ast_prompting"](payload)
        assert actual == expected and events == [], (name, actual, events)
        results.append(dict(id=name, input=payload, observed=actual,
                            probe_events=list(events), unsafe_observation="probe_events contains entered",
                            outcome="probe_not_executed_for_this_input"))
    for name, type_name in [("convert_value", "list"), ("_convert_value", "dict")]:
        payload = "__bfclcontain_probe__()"
        with no_external_dependencies():
            actual = parser[name](payload, type_name)
        assert actual == payload and not events
        results.append(dict(id=name + "_literal_eval", input=payload, type_name=type_name,
                            observed=actual, probe_events=[],
                            unsafe_observation="probe_events contains entered",
                            outcome="literal_eval_refused_nonliteral_and_returned_string"))
    payload = "fabricated_echo(value=__bfclcontain_probe__()+0)"
    with no_external_dependencies():
        actual = parser["default_decode_ast_prompting"](payload)
    assert actual == [{"fabricated_echo": {"value": 7}}] and events == ["entered"]
    results.append(dict(id="binop_execution_FINDING", input=payload, observed=actual,
                        probe_events=list(events), unsafe_observation="probe_events contains entered",
                        outcome="UNCONTAINED_STOP_no_further_execution_path_probes"))
    return results


def main():
    # Exact source inventory and fixture data are loaded before the no-I/O region.
    rows, sinks = inventory()
    assert rows == json.loads((HERE / "source-inventory.json").read_text())
    assert sinks == [json.loads(line) for line in (HERE / "sinks.jsonl").read_text().splitlines()]
    cases = json.loads((HERE / "cases.json").read_text())
    records = differential(cases)
    controls = boundary_controls()
    for record in records:
        print(json.dumps({"kind": "differential", **record}, sort_keys=True))
    for record in controls:
        print(json.dumps({"kind": "boundary", **record}, sort_keys=True))
    print(json.dumps(dict(kind="summary", differential_cases=len(records), boundary_controls=len(controls),
                          uncontained=True, official_scorer_executed=False,
                          model_binding="UNKNOWN", source_gate_satisfied=False), sort_keys=True))


if __name__ == "__main__":
    main()
