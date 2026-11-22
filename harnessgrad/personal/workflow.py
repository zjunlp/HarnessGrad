"""Personal planning and candidate use cases over frozen feedback and surfaces."""
from __future__ import annotations

from harnessgrad.personal import planning, proposals, records
from harnessgrad.personal.contracts import MAX_BYTES, decode, digest, encode


def _event(db, job_id, kind):
    rows = db.execute('SELECT payload FROM evolution_events WHERE job_id=? AND type=?',
                      (job_id, kind)).fetchmany(2)
    if len(rows) > 1:
        raise ValueError(f'job has conflicting {kind} events')
    return decode(rows[0]['payload']) if rows else None


def _reject_credential(value, credential):
    if isinstance(value, str):
        if credential in value:
            raise ValueError('stage data contains the endpoint credential')
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_credential(key, credential)
            _reject_credential(item, credential)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_credential(item, credential)


def _validate_record(record, model, selected):
    fields = {'schema', 'job_id', 'project_id', 'feedback_id', 'request_sha256', 'fixture',
              'applied', 'verified', 'selected_input', 'plan'}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError('invalid planning record fields')
    feedback = selected['feedback']
    if (record['schema'] != 'harnessgrad.personal.plan.v1'
            or record['job_id'] != model.job_id or record['project_id'] != feedback['project_id']
            or record['feedback_id'] != feedback['id'] or record['request_sha256'] != digest(selected)
            or digest(record['selected_input']) != digest(selected)
            or record['fixture'] is not model.usage()['fixture']
            or record['applied'] is not False or record['verified'] is not False):
        raise ValueError('stage record does not match the planning job')
    saved = record['plan']
    if not isinstance(saved, dict) or 'checks' not in saved:
        raise ValueError('invalid saved plan')
    plan = planning.parse_reply({key: val for key, val in saved.items() if key != 'checks'}, selected)
    if encode(plan.as_dict()) != encode(saved):
        raise ValueError('saved plan checks differ from the selected targets')
    _reject_credential(record, model.settings.method.api_key)


def _planner(clarified):
    return (planning.SYSTEM + ' The clarification is the user’s answer to your saved question.'
            if clarified else planning.SYSTEM)


def _binding(selected, clarified):
    return {'request_sha256': digest(selected), 'planner_sha256': digest(_planner(clarified))}


def _plan_kind(kind, clarified):
    return f'plan.clarified.{kind}' if clarified else f'plan.{kind}'


def _plan_stage(clarified):
    return 'plan-clarification' if clarified else 'plan'


def _plan_root(model, clarified):
    root = model.store.path.parent / 'plans' / digest(model.job_id)
    return root / 'clarification' if clarified else root


def question_id(record_id: str) -> str:
    return 'q_' + record_id[:32]


def _reference(record_id, record, *, clarified=False):
    question = record['plan']['question']
    return {'record_id': record_id, 'request_sha256': record['request_sha256'],
            'decision': record['plan']['decision'],
            'question_id': question_id(record_id) if question is not None and not clarified else None}


def _freeze_request(model, selected, clarified):
    store = model.store
    binding = _binding(selected, clarified)
    kind, stage = _plan_kind('requested', clarified), _plan_stage(clarified)
    with store.connection() as db, store.transaction(db):
        prior = _event(db, model.job_id, kind)
        if prior is not None and prior != binding:
            raise ValueError('planning inputs differ from the frozen job')
        # Older interrupted attempts have a model ledger but no request event.
        expected = digest({'stage': stage, 'system': _planner(clarified), 'prompt': encode(selected)})
        calls = db.execute('SELECT input_sha256 FROM model_calls WHERE job_id=? AND stage=?',
                           (model.job_id, stage)).fetchall()
        if any(row['input_sha256'] != expected for row in calls):
            raise ValueError('planning inputs differ from the frozen job')
        if prior is None:
            store.add_event(db, model.job_id, kind, binding)


def _finalize(model, selected, record_id, record, clarified):
    store = model.store
    question = record['plan']['question']
    reference = {**_reference(record_id, record, clarified=clarified), 'storage': 'job'}
    with store.connection() as db, store.transaction(db):
        if _event(db, model.job_id, _plan_kind('requested', clarified)) != _binding(selected, clarified):
            raise ValueError('published plan has no matching frozen request')
        kind = _plan_kind('recorded', clarified)
        prior = _event(db, model.job_id, kind)
        if prior is not None:
            if prior != reference:
                raise ValueError('planning stage already has a different result')
            return
        if reference['question_id'] is not None:
            db.execute('INSERT INTO questions VALUES (?,?,?,?,?)',
                       (reference['question_id'], model.job_id, question, 'open', None))
        store.add_event(db, model.job_id, kind, reference)


def _recorded_plan(model, selected, reference, clarified=False):
    if reference['request_sha256'] != digest(selected):
        raise ValueError('planning inputs differ from the frozen job')
    storage = reference.get('storage')
    if storage not in (None, 'job'):
        raise ValueError('unknown planning record storage')
    base = model.store.path.parent / 'plans'
    if clarified and storage != 'job':
        raise ValueError('clarified plan requires job storage')
    root = _plan_root(model, clarified) if storage == 'job' else base
    record_id = reference['record_id']
    record = records.read(root, record_id)
    _validate_record(record, model, selected)
    expected = _reference(record_id, record, clarified=clarified)
    if 'storage' in reference:
        expected['storage'] = 'job'
    # Older clarified plans could also record a question that the worker never offered.
    legacy = (clarified and record['plan']['question'] is not None
              and reference == {**expected, 'question_id': question_id(record_id)})
    if reference != expected and not legacy:
        raise ValueError('planning event does not match its stage record')
    return {'record_id': record_id, **record}


def _existing(model, selected, clarified):
    store = model.store
    with store.connection() as db:
        prior = _event(db, model.job_id, _plan_kind('recorded', clarified))
    if prior is not None:
        return _recorded_plan(model, selected, prior, clarified)
    root = _plan_root(model, clarified)
    published = records.recover(root)
    if published is None:
        return None
    record_id, record = published
    _validate_record(record, model, selected)
    _finalize(model, selected, record_id, record, clarified)
    return {'record_id': record_id, **record}


def _selected_input(model, project_id, feedback_id, *, skills, instruction, clarified):
    store = model.store
    feedback = store.feedback.get(project_id, feedback_id)
    selected = planning.request(feedback, skills=skills, instruction=instruction)
    if not clarified:
        return selected
    with store.connection() as db:
        reference = _event(db, model.job_id, 'plan.recorded')
        if reference is None or reference['decision'] != 'needs_input':
            raise ValueError('clarification requires an original plan question')
        row = db.execute("SELECT prompt,response FROM questions WHERE job_id=? AND id=? AND status='answered'",
                         (model.job_id, reference['question_id'])).fetchone()
    original = _recorded_plan(model, selected, reference)
    if row is None or row['prompt'] != original['plan']['question']:
        raise ValueError('clarification does not match the frozen plan')
    return planning.request(feedback, skills=skills, instruction=instruction,
                            clarification={'question': row['prompt'], 'response': row['response']})


def plan_feedback(model, project_id: str, feedback_id: str, *, skills=None, instruction=None,
                  clarified: bool = False) -> dict:
    """Plan over caller-selected virtual text, with no project reads or execution.

    Input binding precedes model dispatch. Published output can be recovered
    without new inference or budget, including after the model job completes.
    """
    store = model.store
    selected = _selected_input(model, project_id, feedback_id, skills=skills, instruction=instruction,
                               clarified=clarified)
    _reject_credential(selected, model.settings.method.api_key)
    with store.connection() as db:
        job = db.execute('SELECT project_id FROM jobs WHERE id=?', (model.job_id,)).fetchone()
        if job is None or job['project_id'] != project_id:
            raise ValueError('planning job belongs to another project')
    prior = _existing(model, selected, clarified)
    if prior is not None:
        return prior
    with model:
        prior = _existing(model, selected, clarified)
        if prior is not None:
            return prior
        _freeze_request(model, selected, clarified)
        reply = model.for_stage(_plan_stage(clarified), system_prompt=_planner(clarified))(encode(selected))
        _reject_credential(reply, model.settings.method.api_key)
        plan = planning.parse_reply(reply, selected)
        record = decode(encode({'schema': 'harnessgrad.personal.plan.v1', 'job_id': model.job_id,
            'project_id': project_id, 'feedback_id': feedback_id, 'request_sha256': digest(selected),
            'fixture': model.usage()['fixture'], 'applied': False, 'verified': False,
            'selected_input': selected, 'plan': plan.as_dict()}))
        record_id = records.write(_plan_root(model, clarified), record)
        _finalize(model, selected, record_id, record, clarified)
    return {'record_id': record_id, **record}


def _candidate_record(plan, proposal, revision, request):
    return {'schema': 'harnessgrad.personal.candidate.v1', 'plan_id': plan['record_id'],
            'job_id': plan['job_id'], 'project_id': plan['project_id'],
            'feedback_id': plan['feedback_id'], 'fixture': plan['fixture'],
            'applied': False, 'verified': False, 'request_sha256': request['request_sha256'],
            'stage': proposal.stage, 'revision': revision, 'files': proposal.files, 'diff': proposal.diff}


def _prepared_candidate(db, job_id):
    row = db.execute("SELECT payload FROM evolution_events WHERE job_id=? AND type='candidate.prepared' "
                     'ORDER BY seq DESC LIMIT 1', (job_id,)).fetchone()
    return decode(row['payload']) if row else None


def _check_candidate(db, model, plan, record_id, record, reference, prepared):
    job_id = model.job_id
    request = _event(db, job_id, 'candidate.requested')
    if (request is None or set(request) != {'plan_id', 'request_sha256'}
            or request['plan_id'] != plan['record_id'] or not isinstance(request['request_sha256'], str)):
        raise ValueError('candidate has no matching frozen request')
    revision, files = record.get('revision'), record.get('files')
    budget = db.execute('SELECT revisions FROM model_budgets WHERE job_id=?', (job_id,)).fetchone()
    if type(revision) is not int or not 1 <= revision <= budget['revisions'] or not isinstance(files, dict):
        raise ValueError('candidate has an invalid reserved revision or file mapping')
    expected_ref = {**request, 'record_id': record_id, 'revision': revision}
    if reference is not None and reference != expected_ref:
        raise ValueError('candidate event does not match its record')
    call_number = None
    if prepared is not None:
        call_number = prepared.get('call')
        if type(call_number) is not int or prepared != {**expected_ref, 'call': call_number}:
            raise ValueError('candidate does not match its prepared publication')
    call = db.execute("SELECT 1 FROM model_calls WHERE job_id=? AND stage='proposal' "
                      "AND input_sha256=? AND status='completed' AND (? IS NULL OR seq=?) LIMIT 1",
                      (job_id, request['request_sha256'], call_number, call_number)).fetchone()
    if call is None:
        raise ValueError('candidate has no completed proposal call')
    expected = _candidate_record(plan, proposals.review(plan, files), revision, request)
    if encode(record) != encode(expected):
        raise ValueError('candidate does not match its plan and rendered diff')
    _reject_credential(record, model.settings.method.api_key)
    return expected_ref


def _read_candidate(model, plan, root):
    store, job_id = model.store, model.job_id
    with store.connection() as db:
        reference = _event(db, job_id, 'candidate.recorded')
        prepared = _prepared_candidate(db, job_id)
        if reference is None and prepared is None:
            return None
        if reference is not None:
            record_id = reference.get('record_id')
            record = records.read(root, record_id)
            _check_candidate(db, model, plan, record_id, record, reference, prepared)
            return {'record_id': record_id, **record}
        # Recovery and publication share this transaction lock. Normal reads do not.
        with store.transaction(db):
            reference = _event(db, job_id, 'candidate.recorded')
            prepared = _prepared_candidate(db, job_id)
            record_id = prepared.get('record_id')
            if reference is None:
                if not isinstance(record_id, str):
                    raise ValueError('invalid prepared candidate reference')
                published = records.recover(root, record_id=record_id)
                if published is None:
                    return None
                _, record = published
            else:
                record = records.read(root, record_id)
            expected_ref = _check_candidate(db, model, plan, record_id, record, reference, prepared)
            if reference is None:
                store.add_event(db, job_id, 'candidate.recorded', expected_ref)
            return {'record_id': record_id, **record}


def propose_candidate(model, project_id: str, feedback_id: str, plan_id: str, *,
                      skills=None, instruction=None, clarified: bool = False) -> dict:
    """Freeze one logical candidate from a ready plan using the same job budget.

    Supply the original virtual surfaces again to verify the plan binding. The
    complete result mapping can be passed to a host adapter for later rendering.
    """
    store, job_id = model.store, model.job_id
    selected = _selected_input(model, project_id, feedback_id, skills=skills, instruction=instruction,
                               clarified=clarified)
    with store.connection() as db:
        reference = _event(db, job_id, _plan_kind('recorded', clarified))
    if reference is None or reference['record_id'] != plan_id:
        raise ValueError('candidate requires a recorded plan from this job')
    plan = _recorded_plan(model, selected, reference, clarified)
    if plan['plan']['decision'] != 'plan_ready':
        raise ValueError('candidate requires a ready plan')

    root = store.path.parent / 'candidates' / digest(job_id)
    previous = _read_candidate(model, plan, root)
    if previous is not None:
        return previous
    prompt = proposals.prompt_for(plan)
    _reject_credential(prompt, model.settings.method.api_key)
    if len(prompt.encode('utf-8')) + len(proposals.SYSTEM.encode('utf-8')) > model.limits.max_input_bytes:
        raise ValueError('proposal input exceeds the task byte limit')
    request = {'plan_id': plan_id, 'request_sha256': digest({
        'stage': 'proposal', 'system': proposals.SYSTEM, 'prompt': prompt})}
    with model:
        previous = _read_candidate(model, plan, root)
        if previous is not None:
            return previous
        budget = model.usage()
        if budget['remaining_calls'] <= 0:
            raise ValueError('logical model call limit reached')
        with store.connection() as db, store.transaction(db):
            frozen = _event(db, job_id, 'candidate.requested')
            if frozen is None:
                store.add_event(db, job_id, 'candidate.requested', request)
            elif frozen != request:
                raise ValueError('proposal inputs differ from the frozen job')
        revision = model.reserve_revision()
        ask = model.for_stage('proposal', system_prompt=proposals.SYSTEM)

        def checked_ask(body):
            reply = ask(body)
            _reject_credential(reply, model.settings.method.api_key)
            return reply

        proposal = proposals.propose(plan, prompt, ask=checked_ask)
        record = _candidate_record(plan, proposal, revision, request)
        _reject_credential(record, model.settings.method.api_key)
        if len(encode(record).encode('utf-8')) >= MAX_BYTES:
            raise ValueError('candidate record exceeds 1 MiB')
        record_id = digest(record)
        reference = {**request, 'record_id': record_id, 'revision': revision}
        # Commit the exact validated result before publishing to the filesystem.
        with store.connection() as db, store.transaction(db):
            store.add_event(db, job_id, 'candidate.prepared', {**reference, 'call': budget['logical_calls'] + 1})
        with store.connection() as db, store.transaction(db):
            if records.write(root, record) != record_id:
                raise ValueError('published candidate digest differs from the prepared candidate')
            store.add_event(db, job_id, 'candidate.recorded', reference)
    return {'record_id': record_id, **record}
