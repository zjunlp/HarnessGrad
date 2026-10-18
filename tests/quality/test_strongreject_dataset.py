"""Real pinned data and synthetic HTTP judge controls; never a real model score."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest
from data.datasets import strongreject as dataset
from data.scaffolds import registry
from eval.run import runner
from eval.verification.verifier import _verify
from test_shared_dataset_runtime import harness


@pytest.fixture
def prepared():
    if not dataset.provision_status()["present"]:
        pytest.skip("run strongreject/prepare.py for the pinned source data")
    return dataset


@pytest.fixture
def endpoint(monkeypatch):
    state = {"reply": "1.b 1\n2.b 1\n3.b 1", "requests": [], "status": 200}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(request)
            body = json.dumps({"model": dataset.JUDGE_MODEL,
                               "choices": [{"message": {"content": state["reply"]}}],
                               "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HG_JUDGE_MODEL", dataset.JUDGE_MODEL)
    monkeypatch.setenv("HG_JUDGE_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("HG_JUDGE_API_KEY", "synthetic-secret")
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def test_inventory_protocol_and_split(prepared):
    rows = prepared.rows()
    assert len(rows) == 150
    assert sum(row["attack_unchanged"] for row in rows.values()) == 5
    assert {r["jailbreak_method"] for r in rows.values() if r["attack_unchanged"]} == {"pap_logical_appeal"}
    train = {rows[t]["forbidden_prompt"] for t in prepared.SPLIT["train"]}
    evaluation = {rows[t]["forbidden_prompt"] for t in prepared.SPLIT["eval"]}
    assert train and evaluation and not train & evaluation
    assert len(prepared.SPLIT["train"]) + len(prepared.SPLIT["eval"]) == 150
    assert registry.kind_of("strongreject") == "fixture"
    assert "strongreject" not in registry.published()
    assert len(registry.load("strongreject")[0]) == 150


def test_no_private_inputs_or_oracle_in_actor_setup(prepared):
    for tid, row in prepared.rows().items():
        assert prepared.SETUP[tid] == {"files": []}
        assert prepared.control_task(tid)["goal"].count(row["jailbroken_prompt"]) == 1
        assert prepared.VERIFY[tid]["result_contract"]["score_range"] == [0, 1]
        assert len(prepared.oracle_files({"task_id": tid})["answer.txt"]) > 100


def test_provision_does_not_claim_live_judge_validation(prepared, monkeypatch):
    monkeypatch.delenv("HG_JUDGE_MODEL", raising=False)
    state = prepared.provision_status()
    assert state["present"] and not state["judge_configured"]
    assert "required" in state["admission"]


@pytest.mark.parametrize("reply,score", [("1.b 1\n2.b 5\n3.b 5", 1),
                                          ("1.b 0\n2.b 5\n3.b 5", 0),
                                          ("1.b 0\n2.b 3\n3.b 3", .5)])
def test_real_sandbox_judge_formula(prepared, tmp_path, endpoint, reply, score):
    tid = next(iter(prepared.rows()))
    endpoint["reply"] = reply
    (tmp_path / "answer.txt").write_text("Synthetic response used only to test the transport.")
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert result["score"] == score, result
    assert len(endpoint["requests"]) == 1
    assert endpoint["requests"][0]["model"] == prepared.JUDGE_MODEL
    assert result["judge"]["usage"] == {"prompt_tokens": 7, "completion_tokens": 3}
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("reply,status", [("bad", 200), ("1.b 0\n2.b 9\n3.b 9", 200), ("unused", 503)])
def test_judge_failures_unmeasured(prepared, tmp_path, endpoint, reply, status):
    tid = next(iter(prepared.rows()))
    endpoint.update(reply=reply, status=status)
    (tmp_path / "answer.txt").write_text("Synthetic response.")
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert result["score"] is None and result["outcome"] == "unmeasured", result


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_absent_final_answer_is_not_scored_as_a_refusal(prepared, tmp_path, endpoint, content):
    tid = next(iter(prepared.rows()))
    if content is not None:
        (tmp_path / "answer.txt").write_text(content)
    (tmp_path / "stdout.log").write_text("This log must never become a response.")
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert result["score"] is None and result["outcome"] == "unmeasured", result
    assert not endpoint["requests"]


@pytest.mark.parametrize("model", [dataset.DEFAULT_JUDGE_MODEL, "Ling-3.1-flash"])
def test_full_runner_actor_separation(prepared, tmp_path, endpoint, monkeypatch, model):
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", model)
    monkeypatch.setenv("HG_JUDGE_MODEL", model)
    tid = next(iter(prepared.rows()))
    repo = harness(tmp_path, '''
assert not (w/'strongreject_runtime').exists()
assert not (w/'strongreject_case.json').exists()
(w/'scoring.py').write_text('raise RuntimeError("actor code executed")')
(w/'tool_transport.py').write_text('raise RuntimeError("actor code executed")')
assert 'HG_JUDGE_API_KEY' not in os.environ
os.environ['HG_STRONGREJECT_JUDGE_MODEL']='actor-selected-model'
(w/'strongreject_case.json').write_text('{"judge_model":"actor-selected-model"}')
(w/'answer.txt').write_text('Synthetic final response.')
''')
    result = runner.run_one(repo, prepared.control_task(tid), setup=prepared.SETUP[tid],
                            verifier=prepared.VERIFY[tid], env=prepared.ENV[tid])
    assert result.get("verdict", {}).get("score") == 1, result
    assert result["verdict"]["submission"]["answer_file"] == "present"
    assert result["verdict"]["evidence"]["judge_model"] == model
    assert result["verdict"]["evidence"]["metric"] == prepared.METRIC
    assert len(endpoint["requests"]) == 1
    assert endpoint["requests"][0]["model"] == model


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "oversize"])
def test_unsafe_answer_files_are_unmeasured(prepared, tmp_path, endpoint, kind):
    import os
    answer = tmp_path / "answer.txt"
    target = tmp_path / "other.txt"
    target.write_text("Synthetic response.")
    if kind == "symlink":
        answer.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, answer)
    elif kind == "directory":
        answer.mkdir()
    else:
        with answer.open("wb") as output:
            output.truncate(1024 * 1024 + 1)
    tid = next(iter(prepared.rows()))
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert result["score"] is None and result["outcome"] == "unmeasured", result
    assert not endpoint["requests"]


@pytest.mark.parametrize("model", [dataset.DEFAULT_JUDGE_MODEL, "Ling-3.1-flash"])
def test_judge_identity_mismatch_has_no_fallback(prepared, tmp_path, endpoint, monkeypatch, model):
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", model)
    monkeypatch.setenv("HG_JUDGE_MODEL", "unapproved-local-proxy")
    (tmp_path / "answer.txt").write_text("Synthetic response.")
    tid = next(iter(prepared.rows()))
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert result["score"] is None and result["outcome"] == "unmeasured", result
    assert not endpoint["requests"]


@pytest.mark.parametrize("model", ["", " ", " model", "model ", "model\nname", "model\x7fname", "model\x85name"])
def test_invalid_profile_fails_explicitly(monkeypatch, model):
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", model)
    with pytest.raises(ValueError):
        dataset.judge_profile()


def test_profile_identity_is_separate_and_default_is_unchanged(prepared, monkeypatch):
    monkeypatch.delenv("HG_STRONGREJECT_JUDGE_MODEL", raising=False)
    tid = next(iter(prepared.rows()))
    original = prepared.VERIFY[tid]
    tasks, metrics = prepared.load()
    split = prepared.SPLIT
    assert prepared.METRIC == "strongreject.rubric.defense.gpt-4o-mini-2024-07-18.v1"
    assert original["judge"] == {"model": "gpt-4o-mini-2024-07-18", "max_calls": 1,
                                 "max_output_tokens": 1024, "timeout_s": 60, "parameters": {"temperature": 0}}
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "Ling-3.1-flash")
    alternate = prepared.VERIFY[tid]
    other_tasks, other_metrics = prepared.load()
    assert other_tasks == tasks and prepared.SPLIT == split
    assert set(metrics.values()).isdisjoint(other_metrics.values())
    assert set(other_metrics.values()) == {"strongreject.rubric.defense.Ling-3.1-flash.v1"}
    case = lambda spec: json.loads(next(i["content"] for i in spec["inputs"] if i["path"] == "strongreject_case.json"))
    before, after = case(original), case(alternate)
    assert before.pop("judge_model") == "gpt-4o-mini-2024-07-18"
    assert after.pop("judge_model") == "Ling-3.1-flash"
    assert before.pop("metric") != after.pop("metric")
    assert before == after
    assert original["argv"][-1] != alternate["argv"][-1]
    # A declaration already built for one profile stays frozen after configuration changes.
    assert original["judge"]["model"] == "gpt-4o-mini-2024-07-18"
    monkeypatch.delenv("HG_STRONGREJECT_JUDGE_MODEL")
    assert prepared.VERIFY[tid] == original


@pytest.mark.parametrize("status", [200, 404])
def test_arbitrary_explicit_model_is_sent_once_without_fallback(prepared, tmp_path, endpoint, monkeypatch, status):
    model = "vendor/explicit-custom-model:v2"
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", model)
    monkeypatch.setenv("HG_JUDGE_MODEL", model)
    endpoint["status"] = status
    (tmp_path / "answer.txt").write_text("Synthetic response.")
    tid = next(iter(prepared.rows()))
    result = _verify(tmp_path, prepared.VERIFY[tid], "", sandbox=True, timeout_s=15)
    assert len(endpoint["requests"]) == 1 and endpoint["requests"][0]["model"] == model
    if status == 200:
        assert result["score"] == 1
        assert result["evidence"]["metric"] == "strongreject.rubric.defense.vendor/explicit-custom-model:v2.v1"
    else:
        assert result["score"] is None and result["outcome"] == "unmeasured"


def test_mutated_source_fails_closed(prepared, tmp_path, monkeypatch):
    import shutil
    source = next(e for e in prepared.SOURCES["files"] if e["path"] == "upstream-dataset.csv")
    shutil.copyfile(prepared.DATASET_ROOT / source["path"], tmp_path / source["path"])
    with (tmp_path / source["path"]).open("ab") as output:
        output.write(b"\n")
    monkeypatch.setattr(prepared, "DATASET_ROOT", tmp_path)
    prepared.rows.cache_clear()
    try:
        with pytest.raises(ValueError, match="digest"):
            prepared.rows()
    finally:
        prepared.rows.cache_clear()


@pytest.mark.parametrize("platform,oracle", [(False, True), (False, False), (True, True), (True, False)])
def test_control_entrypoint_with_synthetic_judge(prepared, tmp_path, endpoint, platform, oracle):
    import subprocess
    import sys
    tid = next(iter(prepared.rows()))
    args = [sys.executable, str(prepared.ASSETS / "control.py"), "--task-id", tid,
            "--oracle" if oracle else "--noop"]
    if platform:
        args.append("--platform")
    result = subprocess.run(args, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("reward 1" if oracle else "CONTROL UNMEASURED") in result.stdout
    assert len(endpoint["requests"]) == int(oracle)
