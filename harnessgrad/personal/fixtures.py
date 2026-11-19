"""Small public synthetic evidence, independent of any user's daily sessions."""


def example() -> dict:
    rows = [('user', '请修改这个合成项目的数据处理函数。'),
            ('assistant', '示例：修改了函数，尚未运行测试。'),
            ('user', '以后本项目修改 Python 代码后，请先运行相关测试。'),
            ('tool', '示例测试输出：2 passed；这是合成记录，不是本机测试结果。'),
            ('assistant', '示例任务结束；下一任务是否遵循要求尚未观察。')]
    return {'schema': 'harnessgrad.personal.import.v1', 'events': [
        {'event_id': f'demo-{i}', 'session_id': 'demo-session', 'source_instance': 'public-fixture-v1',
         'timestamp': f'2026-11-17T00:00:0{i}Z', 'role': role, 'text': text,
         'truncated': False, 'source': 'synthetic'} for i, (role, text) in enumerate(rows)]}


class ScriptedModelClient:
    """Explicit offline replies; no HTTP transport or measured provider usage."""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, *, model, temperature, messages, deadline=None):
        import json
        from types import SimpleNamespace
        self.requests.append({'model': model, 'temperature': temperature, 'messages': messages})
        reply = next(self.replies)
        content = reply if isinstance(reply, str) else json.dumps(reply, allow_nan=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=None, transport_attempts=0, unknown_transport_attempts=0)


def model_demo(service, project_root) -> dict:
    """Two stages resume one persisted budget; a third call is refused offline."""
    from harnessgrad.personal.event_store import EventStore
    from harnessgrad.personal.model import ModelSession
    from harnessgrad.personal.profile import ModelLimits, load_model_settings
    project = service.add_project(project_root)
    settings = load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
                                    'HG_METHOD_MODEL': 'scripted-fixture',
                                    'HG_METHOD_API_KEY': 'fixture-only-unused-credential'})
    limits = ModelLimits(max_calls=2, execution_seconds=30)
    client = ScriptedModelClient([{'fixture': True, 'decision': 'needs_input'},
                                  {'fixture': True, 'decision': 'no_change'}])
    first = ModelSession(service.store, project['id'], settings, limits=limits, fixture=True, client=client)
    with first:
        planned = first.for_stage('plan')('Synthetic request: run relevant tests after editing Python.')
    resumed = ModelSession(EventStore(service.store.path), project['id'], settings,
                           job_id=first.job_id, limits=limits, fixture=True, client=client)
    exhausted = False
    with resumed:
        proposed = resumed.for_stage('propose')('Synthetic evidence is insufficient for an edit.')
        try:
            resumed('This call must not reach the scripted client.')
        except ValueError as exc:
            if str(exc) != 'logical model call limit reached':
                raise
            exhausted = True
    service.store.models.complete(first.job_id)
    return {'fixture': True, 'applied': False, 'job_id': first.job_id,
            'plan': planned, 'proposal': proposed, 'budget_exhausted': exhausted,
            'scripted_calls': len(client.requests), 'real_model_calls': 0, 'usage': resumed.usage()}


def plan_demo(service, project_root) -> dict:
    """B2 feedback to a persisted B3 plan with a scripted model and virtual surface."""
    import uuid

    from harnessgrad.personal.model import ModelSession
    from harnessgrad.personal.profile import load_model_settings
    from harnessgrad.personal.workflow import plan_feedback

    project = service.add_project(project_root)
    feedback = service.submit_feedback({
        'project_id': project['id'], 'kind': 'explicit_request',
        'text': '以后修改 Python 代码后，先运行相关测试。', 'evidence': [],
        'idempotency_key': 'plan-demo-' + uuid.uuid4().hex, 'draft_revision': None})
    reply = {'decision': 'plan_ready', 'intent': 'preference',
             'objective': 'Run relevant tests after editing Python.',
             'reason': 'The user stated a new project expectation.', 'targets': ['AGENTS.md'],
             'criteria': ['The trace shows a relevant test command before the final reply.'],
             'question': None, 'reproduction': {'scenario': 'Synthetic Python change',
             'input': 'Edit the fixture function and report the result.',
             'expected': 'Observe whether the relevant tests run before reporting completion.'}}
    settings = load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
                                    'HG_METHOD_MODEL': 'scripted-fixture',
                                    'HG_METHOD_API_KEY': 'fixture-only-unused-credential'})
    client = ScriptedModelClient([reply])
    model = ModelSession(service.store, project['id'], settings, fixture=True, client=client)
    record = plan_feedback(model, project['id'], feedback['id'], instruction=('AGENTS.md', 'Fixture instructions.'))
    return {**record, 'scripted_calls': len(client.requests), 'real_model_calls': 0}
