"""Formal round inputs carry public evidence without changing candidate snapshots."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))

import editor
from eval.ckpt.git_state import commit_state, init_repo, materialize
from eval.ckpt.source_tree import copy_harness
from harnessgrad.loops import mode_a


class RoundInspected(Exception):
    """Stop before any candidate measurement or external method execution."""


def test_generated_bun_state_is_filtered_before_candidate_validation(tmp_path, monkeypatch):
    from eval.containers import bun_runtime

    work = tmp_path / "work"
    (work / "engine").mkdir(parents=True)
    manifest = {"name": "probe", "version": "1", "entrypoint": "agent.py",
                "runtime": {"kind": "bun", "source": "engine",
                            "entrypoint": "main.ts", "package": "probe"}}
    (work / "harness.json").write_text(json.dumps(manifest))
    (work / "agent.py").write_text("print('source only')\n")
    (work / "engine/main.ts").write_text("export const version = 0;\n")
    init_repo(work)
    sha = commit_state(work, "source")
    candidate = tmp_path / "candidate"
    copy_harness(work, candidate)
    (candidate / "engine/main.ts").write_text("export const version = 1;\n")
    generated = candidate / "engine/.opencode-state/broken.py"
    generated.parent.mkdir()
    generated.write_text("def broken(:\n")
    # Only the external Bun parser process is replaced. Source validation,
    # runtime declarations, copying, commits and the measurement gate stay real.
    monkeypatch.setattr(bun_runtime, "runtime_settings",
                        lambda: (tmp_path / "bun", tmp_path / "cache"))
    monkeypatch.setattr(bun_runtime, "_run", lambda *a, **kw: '{"ok":true}')
    monkeypatch.setenv("HARNESSGRAD_SEED", "1")

    def submit(work_arg, argv, request, *args, **kwargs):
        Path(request["trajectory_out"]).write_text(json.dumps({
            "steps": [{"harness_dir": str(candidate), "label": "filtered source"}]}))
        return {"changed": True}

    def inspect_evaluation(copied, *args, **kwargs):
        assert not (copied / "engine/.opencode-state").exists()
        assert generated.is_file()
        assert (copied / "engine/main.ts").read_text() == "export const version = 1;\n"
        raise RoundInspected

    monkeypatch.setattr(mode_a, "_call_method", submit)
    monkeypatch.setattr(mode_a, "evaluate", inspect_evaluation)
    args = SimpleNamespace(method_entrypoint=["unused"], runs_root=tmp_path / "runs",
                           method_timeout=1, sandbox=False, rounds=1,
                           jobs=1, trials=1, dataset="demo", baseline=None,
                           harness="probe", side="train", sampling="all",
                           work_root=tmp_path)
    point = {"round": 0, "score": 0.0, "score_kind": "train",
             "identity": {"harness_sha": sha}}
    with pytest.raises(RoundInspected):
        mode_a._run_mode_a(
            args, work, manifest, "filtered", "A", [point], sha,
            {"traces": {}}, [{"task_id": "train", "goal": "public goal"}],
            ["train"], None, {}, {}, {"wall_clock_s": 0, "generation_tokens": 0},
            train_ids=["train"])


@pytest.mark.parametrize("bun", [True, False], ids=["bun", "python"])
def test_formal_round_preserves_public_channel_and_cli_dispatch(tmp_path, monkeypatch, bun):
    """Dropping the round channel must fail before a method can reach an endpoint."""
    work = tmp_path / "work"
    work.mkdir()
    manifest = {"name": "probe", "version": "1", "entrypoint": "agent.py"}
    if bun:
        manifest["runtime"] = {"kind": "bun", "source": "src", "entrypoint": "src/agent.ts",
                               "package": "probe"}
        (work / "src").mkdir()
        (work / "src/agent.ts").write_text("export const version = 0;\n")
    (work / "harness.json").write_text(json.dumps(manifest))
    (work / "agent.py").write_text("print('source only')\n")
    init_repo(work)
    sha = commit_state(work, "source")
    if bun:
        for name in ("node_modules", ".opencode-state", ".state"):
            (work / name).mkdir(exist_ok=True)
            (work / name / "private-runtime").write_text("RUNTIME_SECRET")
    stale = work / "_harnessgrad"
    stale.mkdir()
    (stale / "private-eval.json").write_text("STALE_EVAL_SECRET")
    run_dir = tmp_path / "runs" / "probe"
    past_traces = run_dir / "round0_traces"
    past_traces.mkdir(parents=True)
    (past_traces / "train.jsonl").write_text('{"reply":"past train"}\n')
    (run_dir / "private-reference.patch").write_text("REFERENCE_SECRET")
    improver = {"name": "dsh", "kind": "cli", "path": "/unused/dsh",
                "argv_style": "dsh"}
    point = {"round": 0, "score": 0.0, "score_kind": "train",
             "identity": {"harness_sha": sha, "improver": improver}}
    base = {"traces": {"train": '{"reply":"current train"}\n',
                        "eval": '{"reply":"PRIVATE_EVAL_TRACE"}\n'},
            "trial_traces": {"train": ['{"reply":"train trial"}\n'],
                             "eval": ["PRIVATE_EVAL_TRIAL"]}}
    method_root = tmp_path / "method-input"
    method_root.mkdir()
    monkeypatch.setattr(mode_a.tempfile, "mkdtemp", lambda **kwargs: str(method_root))
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    monkeypatch.setattr(editor, "model_settings", lambda: pytest.fail("endpoint dispatch"))

    def cli_reply(prompt, root, parent, identity):
        assert identity == improver
        assert parent == root
        assert (root / "_harnessgrad/SKILL.md").read_text() == (
            ROOT / "harnessgrad/improvers/skill.md").read_text()
        return {"no_change": "offline CLI dispatch verified"}

    monkeypatch.setattr(editor, "_ask_cli", cli_reply)

    def inspect_round(work_arg, argv, request, *args, **kwargs):
        root = Path(request["base_harness"])
        assert editor.cli_improver_identity(root) == improver
        assert editor.ask("inspect public evidence", base=root) == {
            "no_change": "offline CLI dispatch verified"}
        channel = root / "_harnessgrad"
        assert (channel / "traces/train.jsonl").read_text() == '{"reply":"current train"}\n'
        assert (channel / "traces/train.trial1.jsonl").read_text() == '{"reply":"train trial"}\n'
        assert (channel / "history/traces/round-0/train.jsonl").read_text() == '{"reply":"past train"}\n'
        assert json.loads((channel / "history/round-0.json").read_text())["identity"]["improver"] == improver
        assert not (channel / "traces/eval.jsonl").exists()
        assert not (channel / "tasks/eval.json").exists()
        for path in channel.rglob("*"):
            if path.is_file():
                assert not any(secret in path.read_text() for secret in (
                    "PRIVATE_EVAL_TRACE", "PRIVATE_EVAL_TRIAL", "PRIVATE_EVAL_GOAL",
                    "REFERENCE_SECRET", "RUNTIME_SECRET", "STALE_EVAL_SECRET"))
        index = json.loads((channel / "states/index.json").read_text())
        archived = Path(index["round-0"]["harness_dir"])
        assert archived == channel / "states/round-0"
        assert (archived / "agent.py").read_text() == "print('source only')\n"
        assert not (archived / "_harnessgrad").exists()
        assert not (archived / "node_modules").exists()
        assert not (archived / ".opencode-state").exists()
        if bun:
            candidate = tmp_path / "candidate"
            copy_harness(root, candidate)
            assert not (candidate / "_harnessgrad").exists()
            assert not (candidate / "node_modules").exists()
            assert not (candidate / ".opencode-state").exists()
            assert not (candidate / ".state").exists()
            assert not (root / "node_modules").exists()
            assert not (root / ".opencode-state").exists()
            assert not (root / ".state").exists()
        snapshot = tmp_path / "snapshot"
        materialize(work, sha, snapshot)
        assert not (snapshot / "_harnessgrad").exists()
        raise RoundInspected

    monkeypatch.setattr(mode_a, "_call_method", inspect_round)
    args = SimpleNamespace(method_entrypoint=["unused"], runs_root=tmp_path / "runs",
                           method_timeout=1, sandbox=False, rounds=1)
    with pytest.raises(RoundInspected):
        mode_a._run_mode_a(
            args, work, manifest, "probe", "A", [point], sha, base,
            [{"task_id": "train", "goal": "public train goal"},
             {"task_id": "eval", "goal": "PRIVATE_EVAL_GOAL"}],
            ["train"], None, {}, {}, {"wall_clock_s": 0, "generation_tokens": 0},
            train_ids=["train"], improver=improver)


def _bun_candidate(root, source_name="opencode"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps({
        "name": "opencode-agent", "version": "0.1.0", "entrypoint": "agent.py",
        "backend": "cli",
        "runtime": {"kind": "bun", "source": source_name,
                    "entrypoint": "packages/cli/src/index.ts", "package": "@opencode/cli"},
    }))
    (root / "agent.py").write_text("raise SystemExit(0)\n")
    return root


def _external_source(base, source_name="opencode"):
    source = base / source_name
    (source / "packages/cli/src").mkdir(parents=True, exist_ok=True)
    (source / "packages/cli/src/index.ts").write_text("// entry\n")
    return source


def test_a_declared_external_bun_source_is_admitted(tmp_path):
    """A harness may name its source outside the candidate, through `runtime_paths`.

    `opencode` is a 224 MB monorepo with 4,689 packages. Requiring it inside the candidate
    would put 224 MB into every `state_after_round<N>/` and into every content address, so the
    rule is "inside the candidate, or declared in the manifest" -- and declared is executable:
    the platform binds exactly the paths a manifest names.
    """
    from eval.containers import bun_runtime

    external = _external_source(tmp_path / "prior-art")
    candidate = _bun_candidate(tmp_path / "candidate")
    manifest = json.loads((candidate / "harness.json").read_text())
    manifest["runtime_paths"] = [str(external)]
    (candidate / "harness.json").write_text(json.dumps(manifest))

    spec, source, entry, is_external = bun_runtime.owned_source(candidate)
    assert is_external is True
    assert source == external.resolve()
    assert entry == (external / "packages/cli/src/index.ts").resolve()


def test_an_undeclared_external_bun_source_is_refused(tmp_path):
    from eval.containers import bun_runtime

    _external_source(tmp_path / "elsewhere")
    candidate = _bun_candidate(tmp_path / "candidate")
    with pytest.raises(bun_runtime.RuntimeUnavailable) as refused:
        bun_runtime.owned_source(candidate)
    assert "runtime_paths" in str(refused.value)


def test_a_source_inside_the_candidate_is_still_owned(tmp_path):
    from eval.containers import bun_runtime

    candidate = _bun_candidate(tmp_path / "candidate")
    _external_source(candidate, "opencode")
    spec, source, entry, is_external = bun_runtime.owned_source(candidate)
    assert is_external is False
    assert source.is_relative_to(candidate.resolve())
