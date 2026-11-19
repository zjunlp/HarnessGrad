const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('./personal-feedback.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const value = () => ({project_id: 'one', kind: 'explicit_request', text: '先跑测试', evidence: [], draft_revision: 1});

test('double click shares one in-flight request; a later explicit submit has its own key', async () => {
  let resolve, keys = 0; const calls = [];
  const controller = create({newKey: () => `request-${++keys}`, submit: data => { calls.push(data); return new Promise(done => {resolve = done;}); }});
  const first = controller.send(value()), second = controller.send(value());
  await Promise.resolve();
  assert.equal(calls.length, 1); resolve({id: 'recorded'});
  assert.deepEqual(await Promise.all([first, second]), [{id: 'recorded'}, {id: 'recorded'}]);
  const later = controller.send(value()); await Promise.resolve(); resolve({id: 'another'}); await later;
  assert.deepEqual(calls.map(call => call.idempotency_key), ['request-1', 'request-2']);
});

test('a lost acknowledgement retries the exact payload and key without mutating user input', async () => {
  const calls = []; let available = false;
  const controller = create({newKey: () => 'stable-key', submit: async data => {
    calls.push(data); if (!available) throw Error('response lost'); return {id: 'same-effect'};
  }});
  const input = value();
  await assert.rejects(controller.send(input), /response lost/);
  available = true; assert.deepEqual(await controller.send(input), {id: 'same-effect'});
  assert.equal(calls[0], calls[1]); assert.equal(input.idempotency_key, undefined);
});

test('a different project stays independent; edits cannot replace a running request', async () => {
  let resolve; const calls = [];
  const controller = create({newKey: () => 'scoped-key', submit: data => {
    calls.push(data); return data.project_id === 'one' ? new Promise(done => {resolve = done;}) : Promise.resolve({id: 'other'});
  }});
  const first = controller.send(value());
  await assert.rejects(controller.send({...value(), text: 'edited'}), /仍在提交/);
  assert.deepEqual(await controller.send({...value(), project_id: 'two'}), {id: 'other'});
  resolve({id: 'first'}); await first;
  assert.deepEqual(calls.map(call => call.project_id), ['one', 'two']);
});

async function page({failedHistory = false, readReceipt} = {}) {
  const nodes = new Map(), submitted = [], proposals = []; let generated = 0;
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: id === 'feedback-kind' ? 'explicit_request' : '', disabled: false, dataset: {}, children: [], options: [],
      classList: {add() {}, toggle() {}}, setAttribute() {}, focus() {}, contains() {return false;},
      replaceChildren(...values) {this.children = values; this.options = [...values];},
      add(value) {this.options.push(value);}, append(...values) {this.children.push(...values);},
      prepend(value) {this.children.unshift(value);},
      get selectedOptions() {return this.options.filter(option => option.value === this.value);}
    });
    return nodes.get(id);
  }
  const document = {body: node('body'), getElementById: node, querySelector: node,
    createElement: () => node(`generated-${++generated}`), addEventListener() {}};
  const response = value => ({ok: true, status: 200, json: async () => value});
  const fetch = async (url, options) => {
    if (url.endsWith('/health')) return response({instance:'fixture',capabilities:{checks:[]},projects:[{id:'one',root:'synthetic'}]});
    if (url.endsWith('/draft')) return response(options.method === 'POST' ? {revision:1} : {text:'',revision:0});
    if (url.endsWith('/sessions')) return response({sessions:[{source_instance:'source',session_id:'session',event_count:1}]});
    if (url.includes('/evidence?')) return response({events:[{source_instance:'source',session_id:'session',event_id:'event',sha256:'a'.repeat(64),seq:1,role:'user',text:'前😀后',truncated:false}],has_more:false,fts_available:true,next_before:1});
    if (url.endsWith('/feedback') && options.method === 'POST') {
      submitted.push(JSON.parse(options.body)); return response({id:'recorded-id',evidence_count:submitted[0].evidence.length});
    }
    if (url.endsWith('/feedback')) {
      if (submitted.length && failedHistory) throw Error('list unavailable');
      return response({feedback: submitted.map(row => ({...row, id: 'recorded-id'}))});
    }
    if (url.endsWith('/feedback/recorded-id')) {
      const receipt = {...submitted[0], id: 'recorded-id'};
      return response(readReceipt ? await readReceipt(receipt) : receipt);
    }
    if (url.endsWith('/feedback/recorded-id/proposal')) {
      proposals.push(options.method);
      return response({proposal: null});
    }
    return response({revision:1});
  };
  const storage = {getItem: () => null, removeItem() {}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'personal.js'),'utf8'), {
    document, fetch, window: {HGPersonalDraft: require('./personal-draft.js'), HGPersonalWindow: require('./personal-window.js'),
      HGPersonalFeedback:{create}, HGPersonalProposal: require('./personal-proposal.js'),
      crypto:{randomUUID:()=> 'fixture-key'}, scrollTo() {}},
    localStorage:storage,sessionStorage:storage,setTimeout,clearTimeout,URLSearchParams,
    Option: function(text,value) {this.textContent=text;this.value=value;}
  });
  for (let i=0;i<3;i++) await new Promise(resolve => setImmediate(resolve));
  return {node,submitted,proposals};
}

test('the page converts selected UTF-16 offsets to Unicode source offsets before submission', async () => {
  const ui = await page();
  ui.node('session-select').value = JSON.stringify(['source','session']); await ui.node('session-select').onchange();
  const children = ui.node('events').children[0].children;
  children[1].selectionStart = 1; children[1].selectionEnd = 3; await children[3].onclick();
  ui.node('feedback').value = 'Retain selected source'; await ui.node('submit-feedback').onclick();
  assert.equal(ui.submitted.length,1);
  assert.deepEqual(ui.submitted[0].evidence[0], {source_instance:'source',session_id:'session',event_id:'event',sha256:'a'.repeat(64),start:1,end:2});
  assert.equal(ui.node('feedback').value,'Retain selected source');
});

test('an acknowledged feedback remains visibly recorded when refreshing its history fails', async () => {
  const ui = await page({failedHistory:true});
  ui.node('feedback').value = 'Explicit request without history'; await ui.node('submit-feedback').onclick();
  assert.equal(ui.submitted.length,1); assert.equal(ui.submitted[0].evidence.length,0);
  assert.match(ui.node('bubble').textContent,/recorded-id/); assert.match(ui.node('bubble').textContent,/无需重复提交/);
  assert.equal(ui.node('submit-feedback').disabled,false);
});

test('submission and history open the same proposal without creating a second task', async () => {
  const ui = await page();
  ui.node('feedback').value = 'A saved request';
  await ui.node('submit-feedback').onclick();
  assert.equal(ui.node('proposal-page').hidden, false);
  assert.equal(ui.node('records-page').hidden, true);
  assert.match(ui.node('proposal-status').textContent, /反馈已保存/);
  await ui.node('proposal-records').onclick();
  assert.equal(ui.node('records-page').hidden, false);
  const [, reopen] = ui.node('feedback-history').children[0].children;
  await reopen.onclick();
  assert.equal(ui.node('proposal-page').hidden, false);
  assert.match(ui.node('proposal-feedback').textContent, /A saved request/);
  assert.deepEqual(ui.proposals, ['GET', 'GET']);
  assert.equal(ui.submitted.length, 1);
});

for (const failed of [false, true]) {
  test(`collapsing ignores a late history ${failed ? 'error' : 'receipt'}`, async () => {
    let finishRead;
    const ui = await page({readReceipt: receipt => new Promise((resolve, reject) => {
      finishRead = () => failed ? reject(Error('history unavailable')) : resolve(receipt);
    })});
    ui.node('feedback').value = 'A saved request';
    await ui.node('submit-feedback').onclick();
    await ui.node('proposal-records').onclick();
    const [, reopen] = ui.node('feedback-history').children[0].children;
    const opening = reopen.onclick();
    ui.node('collapse').onclick();
    finishRead();
    await opening;
    assert.equal(ui.node('body').dataset.mode, 'bar');
    assert.equal(ui.node('proposal-page').hidden, true);
    assert.equal(ui.node('bubble').hidden, true);
    assert.deepEqual(ui.proposals, ['GET']);
  });
}
