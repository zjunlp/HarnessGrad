'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {create, describe} = require('./personal-proposal');

test('watches the selected feedback until its real Diff is ready', async () => {
  const seen = [];
  const next = [{status: 'checking'}, {status: 'ready', result: {diff: '-old\n+new', paths: ['AGENTS.md']}}];
  const view = create({start: async () => ({proposal: {status: 'planning'}}),
    read: async (project, feedback) => {
      assert.equal(project, 'project-a'); assert.equal(feedback, 'feedback-a');
      return {proposal: next.shift()};
    }, render: state => seen.push(state), wait: async () => {}});
  await view.show('project-a', 'feedback-a', true);
  assert.deepEqual(seen.map(s => s.proposal?.status), [undefined, 'planning', 'checking', 'ready']);
  assert.equal(describe(seen.at(-1).proposal).diff, '-old\n+new');
  assert.match(describe(seen.at(-1).proposal).message, /未应用、未复测/);
});

test('switching project drops the earlier project’s late response', async () => {
  const seen = [];
  let resolveOld;
  const view = create({read: id => id === 'a' ? new Promise(resolve => { resolveOld = resolve; })
    : Promise.resolve({proposal: null}), render: state => seen.push(state)});
  const old = view.show('a', 'first');
  view.reset();
  await view.show('b', 'second');
  resolveOld({proposal: {status: 'ready', result: {diff: 'old project'}}});
  await old;
  assert.equal(seen.at(-1).project, 'b');
  assert.equal(seen.at(-1).proposal, null);
});

test('network or service failures leave a readable receipt state without a fake Diff', async () => {
  const errors = [];
  const view = create({start: async () => { throw Object.assign(Error('busy'), {code: 'proposal_busy'}); },
    render: state => errors.push(state)});
  await view.show('project', 'receipt', true);
  assert.match(errors.at(-1).error, /稍后可从这条记录/);
  assert.equal(describe(errors.at(-1).proposal).diff, undefined);
});

test('only an actionable question offers a reply or cancellation', () => {
  const waiting = describe({status: 'needs_input', result: {question: 'Which tests?', question_id: 'q_test'}});
  assert.match(waiting.message, /需要补充信息/);
  assert.equal(waiting.question, 'q_test');
  assert.equal(waiting.cancellable, true);
  const exhausted = describe({status: 'failed', result: {code: 'clarification_exhausted', question: 'What command?'}});
  assert.match(exhausted.message, /未生成提案/);
  assert.match(exhausted.detail, /补充机会已用完.*What command\?/);
  assert.equal(exhausted.question, '');
  assert.equal(exhausted.cancellable, false);
  const legacy = describe({status: 'needs_input', result: {question: 'Which tests?', question_id: null}});
  assert.match(legacy.message, /无法继续回复/);
  assert.equal(legacy.cancellable, false);
});

test('polls the receipt after a stream failure and resumes from the last event ID', async () => {
  const cursors = [], rendered = [];
  const states = [{status: 'planning'}, {status: 'planning'}, {status: 'checking'}, {status: 'ready'}];
  const view = create({read: async () => ({proposal: states.shift()}), wait: async () => {},
    updates: async (_project, _receipt, after) => {
      cursors.push(after);
      if (cursors.length === 2) throw Error('connection dropped');
      return cursors.length === 1 ? 'id: 2\nevent: proposal\ndata: {}\n\n' : 'id: 3\n\n';
    }, render: state => rendered.push(state)});
  await view.show('project', 'receipt');
  assert.deepEqual(cursors, [0, 2, 2]);
  assert.equal(rendered.at(-1).proposal.status, 'ready');
});

test('reset aborts the current event stream and prevents a late update', async () => {
  const rendered = [];
  let started;
  const waiting = new Promise(resolve => { started = resolve; });
  const view = create({read: async () => ({proposal: {status: 'planning'}}), wait: async () => {},
    updates: (_project, _receipt, _after, signal) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), {once: true});
      started(signal);
    }), render: state => rendered.push(state)});
  const old = view.show('project', 'receipt');
  const signal = await waiting;
  view.reset();
  await old;
  assert.equal(signal.aborted, true);
  assert.equal(rendered.at(-1), null);
});
