"""The configuration layer: one declaration, one snapshot, one record.

These tests pin the three properties the layer exists for:

* **The surface is declared.** Every variable the platform reads has an area, a field,
  a default, and a line of prose; nothing is read with `os.environ.get(...)` at a point
  of use any more, so an operator's `.env` is described in one place.
* **A run reads one snapshot.** `current()` is built once and every reader sees the same
  value; `reset()` drops it (which is what a test that configures the platform does).
* **The record is safe and complete.** `dump()` is JSON-serialisable and carries the
  agent's model and backend plus the run's harness and dataset, and a credential is
  recorded as `{"present": bool}` -- never by value.

The last test class checks the migration itself: a module that used to read an `HG_*`
variable at the point of use now honours the snapshot, by setting the variable and
calling the function.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import config                                       # noqa: E402


# ------------------------------------------------------------- the surface ---

def test_the_surface_has_exactly_the_declared_variables():
    """Declared `HG_*` variables, plus the improver registry's named secret.

    The count is not the point -- the declaration is. This test makes a *new* variable
    impossible to add silently: it would have to be counted here, next to the table a
    reader uses to answer "what does this platform read?".
    """
    hg_names = [name for name in config.ENV_NAMES if name.startswith("HG_")]
    assert len(hg_names) == 42, hg_names
    assert len(config.ENV_NAMES) == len(set(config.ENV_NAMES))
    # The three credentials the repository's rule names. `DEEPSEEK_API_KEY` is the
    # default improver registry entry's `api_key_env`, so it is declared beside it.
    secrets = {row["variable"] for row in config.describe() if row["secret"]}
    assert secrets == {"HG_AGENT_API_KEY", "HG_METHOD_API_KEY", "HG_ENV_API_KEY",
                       "DEEPSEEK_API_KEY", "HG_JUDGE_API_KEY"}


def test_describe_names_every_variable_with_a_default_and_a_reason():
    rows = config.describe()
    assert len(rows) == len(config.ENV_NAMES)
    assert {row["variable"] for row in rows} == set(config.ENV_NAMES)
    for row in rows:
        assert row["what"], row
        assert row["area"] in {"agent", "method", "improver", "environment",
                               "sandbox", "egress", "runtime", "storage", "judge"}
        if row["secret"]:
            # Never a value, not even a default: a credential's state is a fact, its
            # bytes are not the platform's to print.
            assert row["default"] == "<redacted>", row


def test_every_default_is_non_secret_or_redacted_in_describe():
    for row in config.describe():
        if not row["secret"] and row["default"] is not None:
            assert not isinstance(row["default"], str) or "sk-" not in row["default"]


# ---------------------------------------------------------------- loading ---

def test_load_reads_the_mapping_it_is_given_not_the_process_environment(monkeypatch):
    monkeypatch.setenv("HG_AGENT_MODEL", "from-the-process")
    cfg = config.load({})
    assert cfg.agent.model is None
    assert cfg.agent.backend == "mock"
    assert config.load({"HG_AGENT_MODEL": "from-the-mapping"}).agent.model \
        == "from-the-mapping"


def test_load_defaults_to_the_process_environment(monkeypatch):
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    assert config.load().method.model == "m"


def test_defaults_are_the_point_of_use_defaults():
    cfg = config.load({})
    assert (cfg.agent.backend, cfg.agent.model, cfg.agent.base_url,
            cfg.agent.api_key) == ("mock", None, "", None)
    assert cfg.method.timeout_s == 180
    assert cfg.method.parse_retries == 2
    assert cfg.method.history_rounds == 0
    assert cfg.method.source_limit == 200000
    assert cfg.improver.timeout_s == 1800
    assert cfg.improver.args == ""
    assert cfg.egress.port == 17890
    assert cfg.sandbox.bwrap is None
    assert cfg.runtime.overlay_root is None
    assert cfg.storage.pull_timeout_s == 900


def test_empty_is_unset_for_an_optional_field_but_a_real_value_for_a_string():
    # `os.environ.get("HG_AGENT_MODEL") or None` collapsed "" to None; the field does too.
    assert config.load({"HG_AGENT_MODEL": ""}).agent.model is None
    # `os.environ.get("HG_AGENT_BACKEND", "mock")` kept "" as a value; the field does too.
    assert config.load({"HG_AGENT_BACKEND": ""}).agent.backend == ""
    # `HG_CODEX_HOME_SOURCE` is passed to `Path(...)`, where "" and unset differ.
    assert config.load({"HG_CODEX_HOME_SOURCE": ""}).improver.codex_home_source == ""
    assert config.load({}).improver.codex_home_source is None


def test_an_int_parse_keeps_the_point_of_use_behaviour():
    assert config.load({"HG_EGRESS_PORT": "9000"}).egress.port == 9000
    # `int(os.environ.get("HG_EGRESS_PORT", "17890"))` raised on ""; so does this.
    with pytest.raises(ValueError):
        config.load({"HG_EGRESS_PORT": ""})
    # ...but `int(os.environ.get("HG_METHOD_HISTORY_ROUNDS", "0") or "0")` fell back.
    assert config.load({"HG_METHOD_HISTORY_ROUNDS": ""}).method.history_rounds == 0
    assert config.load({"HG_PULL_TIMEOUT_S": ""}).storage.pull_timeout_s == 900


# --------------------------------------------------------------- snapshot ---

def test_current_is_one_object_and_reset_rebuilds(monkeypatch):
    monkeypatch.setenv("HG_AGENT_MODEL", "snap")
    config.reset()
    first = config.current()
    assert first.agent.model == "snap"
    assert config.current() is first
    config.reset()
    assert config.current() is not first
    assert config.current().agent.model == "snap"


def test_current_rebuilds_when_the_environment_changes_underneath_it(monkeypatch):
    """The snapshot watches the declared variables, so a test's `monkeypatch.setenv`
    is not silently ignored. In a run that never mutates its own environment -- every
    run, and every read after `.env` is folded in -- it is built exactly once."""
    monkeypatch.setenv("HG_AGENT_MODEL", "first")
    config.reset()
    assert config.current().agent.model == "first"
    same = config.current()
    monkeypatch.setenv("HG_AGENT_MODEL", "second")
    assert config.current().agent.model == "second"
    assert config.current() is not same


# ----------------------------------------------------------------- record ---

def test_dump_is_json_serialisable_redacts_secrets_and_carries_the_models():
    cfg = config.load({"HG_AGENT_BACKEND": "openai", "HG_AGENT_MODEL": "m",
                       "HG_AGENT_API_KEY": "sk-do-not-write-me",
                       "HG_METHOD_API_KEY": "sk-method", "DEEPSEEK_API_KEY": "sk-ds"})
    out = config.dump(cfg)
    assert json.loads(json.dumps(out)) == out
    assert out["agent"]["backend"] == "openai"
    assert out["agent"]["model"] == "m"
    assert out["agent"]["api_key"] == {"present": True}
    assert out["method"]["api_key"] == {"present": True}
    assert out["improver"]["api_key"] == {"present": True}
    text = json.dumps(out)
    assert "sk-do-not-write-me" not in text
    assert "sk-method" not in text
    assert "sk-ds" not in text


def test_dump_reports_absence_as_a_fact_not_a_missing_field():
    out = config.dump(config.load({}))
    assert out["agent"]["api_key"] == {"present": False}


def test_dump_carries_the_run_block_so_the_whole_question_is_one_record():
    args = argparse.Namespace(
        harness="loop", dataset="demo", side="train", sampling="all", rounds=1,
        trials=1, jobs=1, sandbox=True, mode="A", runs_root="runs",
        work_root="../harnessgrad_work", method_entrypoint=None)
    run = config.run_config(args, "r1", seed="s")
    out = config.dump(config.load({"HG_AGENT_MODEL": "m"}), run=run)
    assert out["run"]["harness"] == "loop"
    assert out["run"]["dataset"] == "demo"
    assert out["run"]["seed"] == "s"
    assert json.loads(json.dumps(out)) == out


# ------------------------------------------------------------- migration ---

def test_the_migrated_modules_read_the_snapshot_not_the_raw_environment(monkeypatch):
    """One read per migrated area, through the function that used to read `os.environ`."""
    monkeypatch.setenv("HG_AGENT_MODEL", "env-model")
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    config.reset()

    from harnessgrad.identity import _backend_of, _conflict, _model_of
    assert _model_of(None) == "env-model"
    assert _backend_of(None) == "openai"
    assert _conflict({"agent_model": "declared"}) == {
        "harness": "declared", "platform_env": "env-model",
        "note": "the harness did not use HG_AGENT_MODEL; the declaration is what ran"}

    monkeypatch.setenv("HG_ENV_MODEL", "env-side-model")
    config.reset()
    from eval.service.services import service_env
    env = service_env([{"name": "sim", "port": 1, "needs_model": True}],
                      task_id="t", run_seed="s")
    assert env["HG_ENV_MODEL"] == "env-side-model"

    monkeypatch.setenv("HG_OVERLAY_ROOT", "/tmp/hg-overlays-test")
    config.reset()
    from eval.containers import harness_runtime
    assert harness_runtime.overlays_root() == Path("/tmp/hg-overlays-test")


@pytest.mark.parametrize('global_model,override,expected', [
    (None, None, 'official'), ('global', None, 'global'),
    (None, 'specific', 'specific'), ('global', 'specific', 'specific'),
    ('global', '', 'global'),
])
def test_judge_model_precedence(global_model, override, expected):
    env = {} if global_model is None else {'HG_JUDGE_MODEL': global_model}
    assert config.load(env).judge.resolve_model(override=override, default='official') == expected


def test_judge_model_resolution_requires_a_valid_identity():
    judge = config.load({}).judge
    with pytest.raises(ValueError, match='configure a judge model'):
        judge.resolve_model()
    for model in (' invalid ', 'invalid\nmodel', 'invalid\x7fmodel'):
        with pytest.raises(ValueError, match='model ID'):
            judge.resolve_model(override=model)


def test_strongreject_override_is_recorded_and_snapshot_watched(monkeypatch):
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'first-model')
    first = config.current()
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'second-model')
    second = config.current()
    assert first.judge.strongreject_model == 'first-model'
    assert second.judge.strongreject_model == 'second-model'
    assert config.dump(second)['judge']['strongreject_model'] == 'second-model'
    assert 'HG_STRONGREJECT_JUDGE_MODEL' in {r['variable'] for r in config.describe()}
    assert config.load({'HG_STRONGREJECT_JUDGE_MODEL': ''}).judge.strongreject_model is None
