"""B3 planning from frozen feedback, using scripted replies and synthetic projects."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.personal import records
from harnessgrad.personal.contracts import decode, digest, encode
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.fixtures import ScriptedModelClient
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import ModelLimits, load_model_settings
from harnessgrad.personal.service import PersonalService
from harnessgrad.personal.workflow import plan_feedback

ROOT = Path(__file__).resolve().parents[2]
KEY = 'synthetic-planning-credential'
SURFACE = ('AGENTS.md', 'Run the selected task.')


def reply(**changes):
    return {'decision': 'plan_ready', 'intent': 'preference',
            'objective': 'Run relevant tests after Python edits.', 'reason': 'An explicit user expectation.',
            'targets': ['AGENTS.md'], 'criteria': ['A relevant test command precedes the final reply.'],
            'question': None, 'reproduction': {'scenario': 'Python fixture change',
            'input': 'Edit the selected fixture.', 'expected': 'Observe the test command in the trace.'}, **changes}


@pytest.fixture
def selected(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'AGENTS.md').write_text('Original user text.')
    service = PersonalService(tmp_path / 'state')
    project_id = service.add_project(project)['id']
    feedback = service.submit_feedback({'project_id': project_id, 'kind': 'explicit_request',
        'text': '以后修改 Python 后先运行相关测试。', 'evidence': [],
        'idempotency_key': 'request-one', 'draft_revision': None})
    return service, project_id, feedback['id'], project


def session(selected, responses, **options):
    service, project, _, _ = selected
    settings = load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
        'HG_METHOD_MODEL': 'scripted-plan', 'HG_METHOD_API_KEY': KEY})
    client = ScriptedModelClient(responses)
    model = ModelSession(service.store, project, settings, fixture=True, client=client, **options)
    return model, client


def test_feedback_to_plan_is_persisted_scoped_idempotent_and_unevaluated(selected):
    service, project, feedback, root = selected
    model, client = session(selected, [reply()])
    plan = plan_feedback(model, project, feedback, instruction=SURFACE)
    assert plan['fixture'] and not plan['applied'] and not plan['verified']
    assert plan['plan']['intent'] == 'preference'
    assert plan['plan']['checks'] == ['target_scope', 'source_unchanged', 'same_scenario']
    assert plan['selected_input']['feedback']['missing_context']
    assert plan['selected_input']['feedback']['kind'] == 'explicit_request'
    assert str(root) not in client.requests[0]['messages'][1]['content']
    reopened = EventStore(service.store.path)
    resumed = ModelSession(reopened, project, model.settings, fixture=True, client=client, job_id=model.job_id)
    assert plan_feedback(resumed, project, feedback, instruction=SURFACE) == plan
    assert len(client.requests) == 1
    assert records.read(service.root / 'plans' / digest(model.job_id), plan['record_id'])['plan'] == plan['plan']
    assert (root / 'AGENTS.md').read_text() == 'Original user text.'
    with service.store.connection() as db:
        assert db.execute("SELECT count(*) FROM evolution_events WHERE type='plan.recorded'").fetchone()[0] == 1
        assert KEY not in '\n'.join(db.iterdump())
    assert KEY not in encode(records.read(service.root / 'plans' / digest(model.job_id), plan['record_id']))


@pytest.mark.parametrize('decision', ['needs_input', 'no_change'])
def test_stopped_plan_keeps_reason_and_only_needs_input_opens_question(selected, decision):
    service, project, feedback, _ = selected
    question = 'Which task and expected test output should be compared?' if decision == 'needs_input' else None
    model, _ = session(selected, [reply(decision=decision, targets=[], criteria=[],
                                      reproduction=None, question=question)])
    result = plan_feedback(model, project, feedback)
    assert result['plan']['decision'] == decision and result['plan']['reproduction'] is None
    with EventStore(service.store.path).connection() as db:
        questions = db.execute('SELECT job_id,prompt,status FROM questions').fetchall()
    assert len(questions) == (1 if question else 0)
    if question:
        assert tuple(questions[0]) == (model.job_id, question, 'open')


@pytest.mark.parametrize('instruction', [('../AGENTS.md', 'text'), ('/tmp/AGENTS.md', 'text'),
                                      ('AGENTS.md', None)])
def test_invalid_surface_is_rejected_before_model_call(selected, instruction):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    with pytest.raises(ValueError):
        plan_feedback(model, project, feedback, instruction=instruction)
    assert not client.requests and model.usage()['logical_calls'] == 0
    assert not (service.root / 'plans').exists()


@pytest.mark.parametrize('change', [
    {'targets': ['../secret']}, {'targets': ['skills/not-selected.md']},
    {'criteria': []}, {'intent': 'behavior_fix'}, {'decision': 'applied'},
    {'reproduction': {'command': 'run arbitrary code'}}, {'reason': KEY},
])
def test_model_output_cannot_expand_scope_invent_failure_or_execute(selected, change):
    service, project, feedback, root = selected
    model, client = session(selected, [reply(**change)])
    with pytest.raises(ValueError):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert len(client.requests) == 1 and model.usage()['logical_calls'] == 1
    assert not (service.root / 'plans').exists()
    assert not any(event['type'] == 'plan.recorded' for event in service.store.job_events(model.job_id))
    assert (root / 'AGENTS.md').read_text() == 'Original user text.'


def test_plan_retry_cannot_change_surface_or_bypass_the_shared_budget(selected):
    _, project, feedback, _ = selected
    model, client = session(selected, [reply()], limits=ModelLimits(max_calls=1))
    plan_feedback(model, project, feedback, instruction=SURFACE)
    with pytest.raises(ValueError, match='frozen job'):
        plan_feedback(model, project, feedback, instruction=('AGENTS.md', 'Changed text.'))
    with model, pytest.raises(ValueError, match='logical model call limit'):
        model.for_stage('proposal')('A second model call would exceed the shared budget.')
    assert len(client.requests) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_failed_attempt_freezes_inputs_before_another_dispatch(selected, legacy):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply(targets=['unknown.md']), reply()])
    with pytest.raises(ValueError, match='undiscovered'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    if legacy:
        with service.store.connection() as db:
            db.execute("DELETE FROM evolution_events WHERE job_id=? AND type='plan.requested'", (model.job_id,))
    with pytest.raises(ValueError, match='frozen job'):
        plan_feedback(model, project, feedback, instruction=('AGENTS.md', 'Different inputs.'))
    assert len(client.requests) == 1


def test_failed_attempt_can_retry_the_same_input_with_the_same_budget(selected):
    _, project, feedback, _ = selected
    model, client = session(selected, [reply(targets=['unknown.md']), reply()])
    with pytest.raises(ValueError):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert plan_feedback(model, project, feedback, instruction=SURFACE)['plan']['decision'] == 'plan_ready'
    assert len(client.requests) == model.usage()['logical_calls'] == 2


@pytest.mark.parametrize('finished', [False, True])
def test_persisted_plan_remains_readable_after_execution_ends(selected, finished):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    original = plan_feedback(model, project, feedback, instruction=SURFACE)
    if finished:
        service.store.models.complete(model.job_id)
    else:
        with service.store.connection() as db, service.store.transaction(db):
            db.execute('UPDATE model_budgets SET elapsed=1000 WHERE job_id=?', (model.job_id,))
    assert plan_feedback(model, project, feedback, instruction=SURFACE) == original
    assert len(client.requests) == 1


def test_published_plan_recovers_failed_database_finalization(selected, monkeypatch):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply(decision='needs_input', targets=[], criteria=[],
        reproduction=None, question='Which scenario should be compared?')])
    original_add = service.store.add_event
    def interrupted(db, job_id, kind, payload):
        if kind == 'plan.recorded':
            raise RuntimeError('synthetic database interruption')
        return original_add(db, job_id, kind, payload)
    monkeypatch.setattr(service.store, 'add_event', interrupted)
    with pytest.raises(RuntimeError, match='synthetic database interruption'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    monkeypatch.setattr(service.store, 'add_event', original_add)
    result = plan_feedback(model, project, feedback, instruction=SURFACE)
    assert result['plan']['decision'] == 'needs_input' and len(client.requests) == 1
    with service.store.connection() as db:
        assert db.execute('SELECT count(*) FROM questions WHERE job_id=?', (model.job_id,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM evolution_events WHERE job_id=? AND type='plan.recorded'",
                          (model.job_id,)).fetchone()[0] == 1


@pytest.mark.parametrize('field', ['selected_input', 'plan'])
def test_cached_record_binding_is_validated_beyond_its_own_digest(selected, field):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    original = plan_feedback(model, project, feedback, instruction=SURFACE)
    value = {key: val for key, val in original.items() if key != 'record_id'}
    if field == 'selected_input':
        value[field]['surfaces']['AGENTS.md'] = 'Changed input.'
    else:
        value[field]['targets'] = ['unknown.md']
    new_id = records.write(service.root / 'plans' / digest(model.job_id), value)
    with service.store.connection() as db, service.store.transaction(db):
        row = db.execute("SELECT payload FROM evolution_events WHERE job_id=? AND type='plan.recorded'",
                         (model.job_id,)).fetchone()
        payload = {**decode(row['payload']), 'record_id': new_id}
        db.execute("UPDATE evolution_events SET payload=? WHERE job_id=? AND type='plan.recorded'",
                   (encode(payload), model.job_id))
    with pytest.raises(ValueError):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert len(client.requests) == 1


def test_quoted_credential_in_model_output_is_rejected(selected):
    service, project, feedback, _ = selected
    key = 'synthetic"credential\\sentinel'
    settings = load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
        'HG_METHOD_MODEL': 'scripted-plan', 'HG_METHOD_API_KEY': key})
    client = ScriptedModelClient([reply(reason=key)])
    model = ModelSession(service.store, project, settings, fixture=True, client=client)
    with pytest.raises(ValueError, match='credential'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert not list((service.root / 'plans').rglob('*.json'))


@pytest.mark.parametrize('field', ['decision', 'question_id'])
def test_cached_event_metadata_must_match_the_published_plan(selected, field):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    plan_feedback(model, project, feedback, instruction=SURFACE)
    with service.store.connection() as db, service.store.transaction(db):
        row = db.execute("SELECT payload FROM evolution_events WHERE job_id=? AND type='plan.recorded'",
                         (model.job_id,)).fetchone()
        value = {**decode(row['payload']), field: 'unknown'}
        db.execute("UPDATE evolution_events SET payload=? WHERE job_id=? AND type='plan.recorded'",
                   (encode(value), model.job_id))
    with pytest.raises(ValueError, match='event does not match'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert len(client.requests) == 1


def test_legacy_flat_record_remains_readable(selected):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    original = plan_feedback(model, project, feedback, instruction=SURFACE)
    value = {key: val for key, val in original.items() if key != 'record_id'}
    assert records.write(service.root / 'plans', value) == original['record_id']
    with service.store.connection() as db, service.store.transaction(db):
        row = db.execute("SELECT payload FROM evolution_events WHERE job_id=? AND type='plan.recorded'",
                         (model.job_id,)).fetchone()
        reference = decode(row['payload'])
        reference.pop('storage')
        db.execute("UPDATE evolution_events SET payload=? WHERE job_id=? AND type='plan.recorded'",
                   (encode(reference), model.job_id))
    assert plan_feedback(model, project, feedback, instruction=SURFACE) == original
    assert len(client.requests) == 1


def test_request_can_ask_for_failure_context_without_claiming_a_reproduction(selected):
    _, project, feedback, _ = selected
    model, _ = session(selected, [reply(decision='needs_input', intent='behavior_fix', targets=[],
        criteria=[], question='Which scenario demonstrates the failure?', reproduction=None)])
    result = plan_feedback(model, project, feedback)
    assert result['plan']['decision'] == 'needs_input' and result['plan']['reproduction'] is None
    assert not result['verified']


def test_cached_plan_corruption_is_refused_without_another_call(selected):
    service, project, feedback, _ = selected
    model, client = session(selected, [reply()])
    result = plan_feedback(model, project, feedback, instruction=SURFACE)
    artifact = records._native_path(service.root / 'plans' / digest(model.job_id) / (result['record_id'] + '.json'))
    artifact.write_text('{}')
    with pytest.raises(ValueError, match='digest'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert len(client.requests) == 1


def test_project_binding_is_checked_before_dispatch(selected):
    service, project, feedback, root = selected
    other = root.parent / 'other'; other.mkdir()
    other_id = service.add_project(other)['id']
    model, client = session((service, other_id, feedback, other), [reply()])
    with pytest.raises(ValueError, match='another project'):
        plan_feedback(model, project, feedback, instruction=SURFACE)
    assert not client.requests


@pytest.mark.parametrize('truncated', [False, True])
def test_behavior_plan_retains_selected_observation_and_incomplete_context(selected, truncated):
    service, project, _, _ = selected
    source = {'event_id': 'observed', 'session_id': 'selected', 'source_instance': 'synthetic',
              'timestamp': '2026-11-18T00:00:00Z', 'role': 'assistant',
              'text': 'Fixture reply claimed completion before running tests.',
              'truncated': truncated, 'source': 'synthetic'}
    service.import_session(project, {'schema': 'harnessgrad.personal.import.v1', 'events': [source]})
    row = service.store.evidence.search(project, 'synthetic', 'selected')['events'][0]
    feedback = service.submit_feedback({'project_id': project, 'kind': 'failure_observation',
        'text': '请先复现这个偏差。', 'idempotency_key': 'failure', 'draft_revision': None,
        'evidence': [{key: row[key] for key in ('source_instance', 'session_id', 'event_id', 'sha256')}
                     | {'start': 0, 'end': len(row['text'])}]})
    model, client = session(selected, [reply(intent='behavior_fix')])
    if truncated:
        with pytest.raises(ValueError, match='complete selected evidence'):
            plan_feedback(model, project, feedback['id'], instruction=SURFACE)
    else:
        result = plan_feedback(model, project, feedback['id'], instruction=SURFACE)
        observed = result['selected_input']['feedback']['evidence'][0]
        assert observed['provenance'] == 'assistant_output' and not result['verified']
    assert len(client.requests) == 1


def test_skill_plan_uses_only_the_selected_virtual_file(selected):
    _, project, feedback, _ = selected
    model, _ = session(selected, [reply(targets=['skills/testing.md'])])
    result = plan_feedback(model, project, feedback, skills={'skills/testing.md': 'Fixture testing Skill.'})
    assert result['plan']['targets'] == ['skills/testing.md']
    assert 'skill_structure' in result['plan']['checks']


def test_stage_records_are_immutable_and_detect_corruption(tmp_path):
    root = tmp_path / 'plans'
    value = {'schema': 'synthetic', 'decision': 'no_change'}
    record_id = records.write(root, value)
    assert records.write(root, value) == record_id
    assert records.read(root, record_id) == value
    artifact = root / (record_id + '.json')
    artifact.write_text('{}')
    with pytest.raises(ValueError, match='digest'):
        records.read(root, record_id)
    with pytest.raises(ValueError):
        records.write(root, value)


def test_stage_reader_rejects_hardlinks_and_recovery_refuses_ambiguous_outputs(tmp_path):
    root = tmp_path / 'plans'
    record_id = records.write(root, {'result': 'first'})
    original = root / (record_id + '.json')
    alias = tmp_path / 'linked.json'
    os.link(original, alias)
    with pytest.raises(ValueError, match='single-link'):
        records.read(root, record_id)
    alias.unlink()
    records.write(root, {'result': 'second'})
    with pytest.raises(ValueError, match='ambiguous'):
        records.recover(root)


def test_stage_publication_supports_long_native_paths(tmp_path):
    root = tmp_path / ('a' * 80) / ('b' * 65)
    value = {'result': 'long-path fixture'}
    record_id = records.write(root, value)
    assert len(str(root / (record_id + '.json'))) > 260
    assert records.read(root, record_id) == value


def test_stage_record_directory_cannot_traverse_a_link(tmp_path):
    target = tmp_path / 'target'; target.mkdir()
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0
    else:
        alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='directory must be ordinary'):
        records.write(alias / 'plans', {'private': 'content'})
    assert list(target.iterdir()) == []


def test_stage_publication_recovers_process_exit_after_link(tmp_path):
    root = tmp_path / 'plans'
    code = '''
import os,sys
from pathlib import Path
from harnessgrad.personal import records
original = records.os.link
def interrupted(source, destination):
    original(source, destination)
    os._exit(9)
records.os.link = interrupted
records.write(Path(sys.argv[1]), {'result': 'published'})
'''
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, str(root)],
                             capture_output=True, timeout=10)
    assert process.returncode == 9, process.stderr
    record_id, value = records.recover(root)
    assert value == {'result': 'published'}
    assert records.read(root, record_id) == value
    assert not list(root.glob('.pending-*'))


def test_cli_plan_demo_is_an_explicit_offline_flow(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'harnessgrad.cli', 'personal', 'plan-demo',
        '--state-root', str(tmp_path / 'state'), '--project', str(project)],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['fixture'] and receipt['scripted_calls'] == 1 and receipt['real_model_calls'] == 0
    assert receipt['plan']['decision'] == 'plan_ready' and receipt['plan']['targets'] == ['AGENTS.md']
    assert list(project.iterdir()) == []
