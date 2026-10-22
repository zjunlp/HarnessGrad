"""Fabricated gate controls and real platform scorer probes, stdlib only.

No retained decoder is imported/executed. Gate bypass uses an inert decoder spy;
the already observed unsafe result is cited, not re-executed. The dynamic probe
imports the actual platform adapter and invokes the actual shared check.main.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from gate import guarded_decode, preflight


GATE_CASES = [
    ("fabricated_direct_call", "fabricated_echo(value=__probe__())", "bfclguard_call_in_argument"),
    ("fabricated_ordinary", "fabricated_echo(value={'items': [1, -2, 'inert __probe__()'], 'ok': True})", None),
    ("fabricated_prior_finding", "fabricated_echo(value=__bfclcontain_probe__()+0)", "bfclguard_binop_contains_call"),
    ("fabricated_reversed_finding", "fabricated_echo(value=1 + __probe__())", "bfclguard_binop_contains_call"),
    ("fabricated_nested_call", "fabricated_echo(value={'items': [__probe__(x=1)]})", "bfclguard_call_in_argument"),
    ("fabricated_literal_binop", "fabricated_echo(value=1+2)", "bfclguard_binop_eval"),
    ("fabricated_lambda", "fabricated_echo(value=lambda: 1)", "bfclguard_nonliteral_argument"),
    ("fabricated_multiple_calls", "[fabricated_echo(value=1), fabricated_other(value='safe')]", None),
    ("fabricated_positional", "fabricated_echo(__probe__())", "bfclguard_argument_unpack_or_positional"),
    ("fabricated_star_keywords", "fabricated_echo(**{'value': 1})", "bfclguard_argument_unpack_or_positional"),
    ("fabricated_subscript", "fabricated_echo(value=items[0])", "bfclguard_nonliteral_argument"),
    ("fabricated_bad_syntax", "fabricated_echo(value=", "bfclguard_parse_refused"),
]


def gate_controls():
    observations = []
    for name, text, reason in GATE_CASES:
        entered = []

        def spy(raw, language):
            entered.append(dict(text=raw, language=language))
            return "inert-spy-return"

        verdict = guarded_decode(text, spy)
        if reason:
            assert verdict == dict(status="unmeasured", measured=False, reason=reason,
                                   production_authorized=False)
            assert entered == [] and "reward" not in verdict
        else:
            assert verdict == dict(status="decoded", output="inert-spy-return", production_authorized=False)
            assert entered == [dict(text=text, language="Python")]
        guarded_calls = list(entered)
        entered.clear()
        spy(text, "Python")  # Safe bypass control: never invoke the retained sink.
        assert entered == [dict(text=text, language="Python")]
        observations.append(dict(id=name, text=text, verdict=verdict,
                                 guarded_decoder_calls=guarded_calls, without_gate_spy_calls=list(entered)))
    assert preflight("x()", "Java")["reason"] == "bfclguard_unsupported_language"
    assert preflight("x" * 65537)["reason"] == "bfclguard_input_bound"
    assert preflight("fabricated_echo(value=[" + ",".join(["1"] * 4100) + "])")["reason"] == "bfclguard_ast_bound"
    assert preflight("fabricated_echo(value=" + "[" * 70 + "1" + "]" * 70 + ")")["reason"] == "bfclguard_ast_bound"
    return observations


class DecoderTripwire:
    """Detect package import AND loading by arbitrary alias/source slice filename.

    Import, audit-exec and profile-call hooks are deliberately redundant. The
    hooks are test observations, not production security boundaries.
    """
    def __init__(self):
        self.events = []

    @staticmethod
    def retained(filename):
        return "/bfcl_eval/model_handler/" in str(filename).replace("\\", "/")

    def trip(self, kind, value):
        self.events.append(dict(kind=kind, value=value))
        raise RuntimeError("bfclguard decoder tripwire: " + kind)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "bfcl_eval" or fullname.startswith("bfcl_eval."):
            self.trip("import", fullname)
        return None

    def audit(self, event, args):
        if event == "exec" and self.retained(args[0].co_filename):
            self.trip("exec", args[0].co_filename)
        if event == "import" and (args[0] == "bfcl_eval" or args[0].startswith("bfcl_eval.")):
            self.trip("import", args[0])
        if event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "ctypes.")):
            self.trip("external_operation", event)
        if event == "open":
            filename = str(args[0])
            if (filename.startswith("/dev/nvidia") or "/xubuqiang/datasets/" in filename
                    or "/tests/fixtures/bfcl_ast_source/" in filename):
                self.trip("forbidden_open", filename)

    def profile(self, frame, event, arg):
        if event == "call" and self.retained(frame.f_code.co_filename):
            self.trip("decoder_call", frame.f_code.co_name)


def retained_modules():
    return sorted(name for name, module in sys.modules.copy().items()
                  if name == "bfcl_eval" or name.startswith("bfcl_eval.")
                  or DecoderTripwire.retained(getattr(module, "__file__", "")))


def dynamic_probe(scratch):
    """Fresh process required: assert module absence both before and after scoring."""
    assert not retained_modules()
    tripwire = DecoderTripwire()
    # Positive instrumentation controls do not import retained source or run eval.
    for kind, operation in [
        ("import", lambda: tripwire.find_spec("bfcl_eval.model_handler.utils")),
        ("exec", lambda: tripwire.audit("exec", (compile("pass", "/synthetic/bfcl_eval/model_handler/utils.py", "exec"),))),
    ]:
        try:
            operation()
        except RuntimeError:
            pass
        else:
            raise AssertionError("inactive tripwire")
        assert tripwire.events[-1]["kind"] == kind
    namespace = {}
    exec(compile("def resolve_ast_by_type(value):\n    return None\n",
                 "/synthetic/bfcl_eval/model_handler/utils.py", "exec"), namespace)
    previous_profile = sys.getprofile()
    try:
        sys.setprofile(tripwire.profile)
        namespace["resolve_ast_by_type"](None)
    except RuntimeError:
        pass
    finally:
        sys.setprofile(previous_profile)
    assert tripwire.events[-1] == dict(kind="decoder_call", value="resolve_ast_by_type")
    positive_events = list(tripwire.events)
    tripwire.events.clear()
    sys.meta_path.insert(0, tripwire)
    sys.addaudithook(tripwire.audit)
    sys.setprofile(tripwire.profile)
    sys.path.insert(0, str(ROOT))
    from data.datasets import bfcl_v3
    from data.scaffolds.qa.verify import check
    # Real adapter imports must stay lazy; no source rows have been loaded.
    assert bfcl_v3._RECORDS == [] and bfcl_v3._LOAD_ERROR is None
    cases = [("fabricated_name", "ns.fabricated_echo", True),
             ("fabricated_expression", "fabricated_echo(value=1 + __probe__())", False),
             ("fabricated_nested", "fabricated_echo(value=__probe__(x=1))", False),
             ("fabricated_string", "fabricated_echo(value='__probe__()')", False)]
    rows = []
    gold = {"functions": ["fabricated_echo"], "subset": "simple"}
    for name, text, expected in cases:
        result = bfcl_v3.score(text, gold)
        assert result["correct"] is expected
        assert result["reward"] == float(expected)
        with tempfile.TemporaryDirectory(prefix="bfclguard-case-", dir=scratch) as temporary:
            directory = Path(temporary)
            answer, gold_path = directory / "answer.txt", directory / "gold.json"
            reward, evidence = directory / "reward.txt", directory / "evidence.json"
            answer.write_text(text)
            raw = json.dumps(gold, sort_keys=True).encode()
            gold_path.write_bytes(raw)
            output = io.StringIO()
            with redirect_stdout(output):
                code = check.main(["--dataset", "bfcl_v3", "--answer", str(answer),
                                   "--gold", str(gold_path), "--prediction", str(directory / "absent.json"),
                                   "--task-id", name, "--gold-sha256", hashlib.sha256(raw).hexdigest(),
                                   "--reward-file", str(reward), "--evidence-file", str(evidence)])
            record = json.loads(evidence.read_text())
            assert code == (0 if expected else 1)
            assert record["measured"] is True and record["correct"] is expected
            assert record["scorer_module"] == "data.datasets.bfcl_v3"
            assert record["scorer_input"]["verified"] is True
            assert reward.read_text() == ("1\n" if expected else "0\n")
            rows.append(dict(id=name, text=text, direct_correct=result["correct"], check_exit=code,
                             check_stdout=output.getvalue(), reward_text=reward.read_text(),
                             metric=record["official"]["metric"], scorer_module=record["scorer_module"],
                             scorer_input_verified=record["scorer_input"]["verified"]))
        assert not retained_modules() and not tripwire.events
    assert bfcl_v3._RECORDS == [] and bfcl_v3._LOAD_ERROR is None
    loaded = sorted({str(Path(module.__file__).resolve().relative_to(ROOT))
                     for module in sys.modules.copy().values()
                     if getattr(module, "__file__", None)
                     and Path(module.__file__).resolve().is_relative_to(ROOT)})
    sys.setprofile(previous_profile)
    sys.meta_path.remove(tripwire)
    return dict(cases=rows, retained_sys_modules=retained_modules(), sink_events=tripwire.events,
                loaded_repository_files=loaded, positive_tripwire_events=positive_events)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dynamic", action="store_true")
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    if args.dynamic:
        assert args.scratch and args.scratch.is_dir()
        print(json.dumps(dynamic_probe(args.scratch), sort_keys=True))
    else:
        for record in gate_controls():
            print(json.dumps(record, sort_keys=True))
