// Run with: node --test console/static/personal-window.test.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create, shouldSubmit, shouldCollapse} = require('./personal-window.js');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function fixture(invoke = () => Promise.resolve(), timeoutMs = 5000) {
  const calls = [], views = [], focused = [], errors = [];
  const controller = create({
    invoke: action => { calls.push(action); return invoke(action); },
    render: mode => views.push(mode), focusInput: () => focused.push(true),
    onError: () => errors.push(true), timeoutMs
  });
  return {controller, calls, views, focused, errors};
}

test('hover preserves focus; clicking the expanded panel requests input focus', async () => {
  const f = fixture();
  await f.controller.hover();
  assert.deepEqual(f.calls, ['input']);
  assert.deepEqual(f.focused, []);
  await f.controller.open();
  assert.equal(f.controller.mode(), 'input');
  assert.deepEqual(f.calls, ['input', 'input', 'focus']);
  assert.deepEqual(f.views, ['input', 'input']);
  assert.equal(f.focused.length, 1);
});

test('focus waits for successful native expansion', async () => {
  const gate = deferred();
  const started = deferred();
  const f = fixture(action => { if (action === 'input') { started.resolve(); return gate.promise; } return Promise.resolve(); });
  const opening = f.controller.open();
  await started.promise;
  assert.deepEqual(f.calls, ['input']);
  assert.deepEqual(f.focused, []);
  gate.resolve();
  await opening;
  assert.deepEqual(f.calls, ['input', 'focus']);
  assert.equal(f.focused.length, 1);
});

test('collapse supersedes an unfinished focus request', async () => {
  const gate = deferred();
  const f = fixture(action => action === 'input' ? gate.promise : Promise.resolve());
  const opening = f.controller.open();
  const closing = f.controller.show('bar');
  gate.resolve();
  await Promise.all([opening, closing]);
  assert.equal(f.controller.mode(), 'bar');
  assert.deepEqual(f.calls, ['input', 'bar']);
  assert.deepEqual(f.focused, []);
});

test('native resize commands are serialized during rapid mode changes', async () => {
  const gate = deferred();
  const started = deferred();
  const f = fixture(action => { if (action === 'input') { started.resolve(); return gate.promise; } return Promise.resolve(); });
  const first = f.controller.show('input'), second = f.controller.show('details');
  await started.promise;
  assert.deepEqual(f.calls, ['input']);
  gate.resolve();
  await Promise.all([first, second]);
  assert.deepEqual(f.calls, ['input', 'details']);
  assert.equal(f.controller.mode(), 'details');
});

test('dragging suppresses hover resizing until the drag finishes', async () => {
  const gate = deferred();
  const f = fixture(action => action === 'drag' ? gate.promise : Promise.resolve());
  const drag = f.controller.action('drag');
  await f.controller.hover();
  assert.equal(f.controller.mode(), 'bar');
  assert.equal(f.controller.dragging(), true);
  gate.resolve();
  await drag;
  await f.controller.hover();
  assert.deepEqual(f.calls, ['drag', 'input']);
  assert.equal(f.controller.dragging(), false);
});

for (const [name, invoke] of [
  ['failed resize', () => Promise.reject(Error('unavailable'))],
  ['unresponsive resize', () => new Promise(() => {})]
]) test(`${name} reports failure without claiming input focus`, async () => {
  const f = fixture(invoke, 15);
  assert.equal(await f.controller.open(), false);
  assert.equal(f.errors.length, 1);
  assert.deepEqual(f.focused, []);
});

test('unknown actions do not reach the native bridge', async () => {
  const f = fixture();
  assert.equal(await f.controller.action('execute arbitrary text'), false);
  assert.deepEqual(f.calls, []);
});

test('IME Enter/Escape and Shift+Enter are not application submit/collapse', () => {
  for (const key of ['Enter', 'Escape']) {
    assert.equal(shouldSubmit({key, isComposing:true}, false), false);
    assert.equal(shouldCollapse({key, isComposing:true}, false), false);
    assert.equal(shouldSubmit({key}, true), false);
    assert.equal(shouldCollapse({key}, true), false);
  }
  assert.equal(shouldSubmit({key:'Enter', shiftKey:true}, false), false);
  assert.equal(shouldSubmit({key:'Enter'}, false), true);
  assert.equal(shouldCollapse({key:'Escape'}, false), true);
});
