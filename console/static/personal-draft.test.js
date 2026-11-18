const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('./personal-draft.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, failed) => { resolve = done; reject = failed; });
  return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(options = {}) {
  const stored = new Map(), calls = [], removed = [], errors = [];
  function controller(extra = {}) {
    return create({
      load: async id => stored.get(id) || {text: '', revision: 0},
      save: async (id, text, revision) => {
        calls.push({id, text, revision});
        const value = {text, revision: revision + 1}; stored.set(id, value); return value;
      },
      clearLegacy: id => removed.push(id), onError: error => errors.push(error), delayMs: 10000,
      ...options, ...extra
    });
  }
  return {controller, stored, calls, removed, errors};
}

test('restored drafts stay project-scoped and cleared drafts ignore old browser text', async () => {
  const f = fixture({readLegacy: () => 'Obsolete feedback'}), first = f.controller();
  first.change('one', '中文反馈'); first.change('two', 'Other project');
  await first.flushAll(); first.dispose();
  f.stored.set('cleared', {text: '', revision: 2});
  const restarted = f.controller();
  assert.equal((await restarted.read('one')).text, '中文反馈');
  assert.equal((await restarted.read('two')).text, 'Other project');
  assert.equal((await restarted.read('cleared')).text, '');
  await restarted.flush('cleared'); assert.equal(f.calls.length, 2);
  restarted.dispose();
});

test('edits during a save are serialized and flush waits for the latest acknowledgement', async () => {
  const pending = deferred(), f = fixture();
  const controller = f.controller({save: async (id, text, revision) => {
    f.calls.push({id, text, revision});
    if (f.calls.length === 1) await pending.promise;
    return {revision: revision + 1};
  }});
  controller.change('one', 'First');
  let complete = false;
  const saving = controller.flush('one').then(() => { complete = true; });
  await tick();
  controller.change('one', 'Latest');
  const another = controller.flush('one');
  await tick();
  assert.equal(f.calls.length, 1); assert.equal(complete, false);
  pending.resolve(); await Promise.all([saving, another]);
  assert.deepEqual(f.calls, [{id: 'one', text: 'First', revision: 0}, {id: 'one', text: 'Latest', revision: 1}]);
  controller.dispose();
});

test('legacy storage is removed only after a successful stable save', async () => {
  const pending = deferred(), f = fixture({readLegacy: () => 'Legacy feedback'});
  const controller = f.controller({save: async () => { await pending.promise; return {revision: 1}; }});
  assert.equal((await controller.read('one')).text, 'Legacy feedback');
  const saving = controller.flush('one'); await tick();
  assert.deepEqual(f.removed, []);
  pending.resolve(); await saving;
  assert.deepEqual(f.removed, ['one']); controller.dispose();
});

test('autosave errors retain local text; manual retry waits for a successful save', async () => {
  const failed = deferred(), f = fixture(); let available = false;
  const controller = f.controller({delayMs: 0, onError: error => failed.resolve(error),
    save: async () => { if (!available) throw Error('conflict'); return {revision: 1}; }});
  controller.change('one', 'Keep this text');
  assert.match((await failed.promise).message, /conflict/);
  await assert.rejects(controller.flush('one'), /conflict/);
  assert.equal((await controller.read('one')).text, 'Keep this text');
  assert.deepEqual(f.removed, []);
  available = true; await controller.flush('one'); assert.deepEqual(f.removed, ['one']);
  controller.dispose();
});

test('the UTF-8 limit rejects dispatch without clearing the editor', async () => {
  const f = fixture(), controller = f.controller();
  const text = '字'.repeat(6000);
  controller.change('one', text);
  await assert.rejects(controller.flush('one'), /16 KiB/);
  assert.equal((await controller.read('one')).text, text);
  assert.deepEqual(f.calls, []); controller.dispose();
});

function page(fetch) {
  const nodes = new Map(), handlers = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', disabled: false, dataset: {}, options: [], children: [],
      classList: {add() {}, toggle() {}}, setAttribute() {}, focus() {},
      replaceChildren(...values) { this.children = values; this.options = [...values]; },
      add(value) { this.options.push(value); }, contains() { return false; },
      get selectedOptions() { return this.options.filter(option => option.value === this.value); }
    });
    return nodes.get(id);
  }
  const document = {body: node('body'), getElementById: node, querySelector: node,
    addEventListener: (name, handler) => handlers.set(name, handler)};
  const window = {HGPersonalDraft: {create}, HGPersonalWindow: require('./personal-window.js')};
  const storage = {getItem: () => null, removeItem() {}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'personal.js'), 'utf8'), {
    window, document, fetch, sessionStorage: storage, localStorage: storage,
    setTimeout, clearTimeout, Option: function(text, value) { this.textContent = text; this.value = value; }
  });
  return {node, key: event => handlers.get('keydown')(event)};
}

test('Escape before connection still collapses without trying to save an unselected project', async () => {
  const loading = deferred();
  const app = page(() => loading.promise);
  assert.doesNotThrow(() => app.key({key: 'Escape'}));
  await tick();
  assert.equal(app.node('body').dataset.mode, 'bar');
  assert.equal(app.node('feedback').disabled, true);
});

test('Escape during draft loading cannot replace an existing draft with empty text', async () => {
  const loading = deferred(), requests = [];
  const response = value => ({status: 200, ok: true, json: async () => value});
  const app = page(async (url, options) => {
    requests.push({url, method: options.method});
    if (url.endsWith('/health')) return response({instance: 'fixture', capabilities: {checks: []},
      projects: [{id: 'project', root: 'synthetic-project'}]});
    if (url.endsWith('/draft')) return loading.promise;
    if (url.endsWith('/sessions')) return response({sessions: []});
    return response({events: [], next_after: 0, total: 0});
  });
  await tick();
  assert.equal(app.node('feedback').disabled, true);
  app.key({key: 'Escape'});
  loading.resolve(response({text: 'Existing server draft', revision: 1}));
  await tick();
  assert.equal(app.node('feedback').value, 'Existing server draft');
  assert.equal(app.node('feedback').disabled, false);
  assert.equal(requests.filter(request => request.method === 'POST').length, 0);
});
