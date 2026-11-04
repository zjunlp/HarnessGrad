const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function load() {
  const source = fs.readFileSync(path.join(__dirname, 'trace-view.js'), 'utf8')
    .replace('    /* ── 绑定 ─────────────────────────────────────────── */',
      '    window.testApi = { renderEvent, renderEventBody, commandView };\n    /* ── 绑定 ─────────────────────────────────────────── */');
  const elements = new Map();
  const element = () => ({ innerHTML: '', textContent: '', classList: { add() {} }, querySelectorAll: () => [], setAttribute() {} });
  const root = element(); root.querySelector = key => { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); };
  root.querySelectorAll = () => [];
  const context = { window: {}, fetch: async () => ({ ok: true, json: async () => ({ rows: [] }) }) };
  vm.runInNewContext(source, context); context.window.HGTraceView.mount(root);
  return context.window.testApi;
}

test('large commands render only a collapsed summary initially', () => {
  const { renderEvent } = load();
  const html = renderEvent({kind: 'command', step: 0, exit: 0, command: 'secret-contents-' + 'x'.repeat(10000)}, 0);
  assert.match(html, /^<details class="trace-event" data-event="0">/);
  assert.match(html, /执行命令/); assert.match(html, /成功/);
  assert.doesNotMatch(html, /secret-contents/); assert.doesNotMatch(html, /<details[^>]* open/);
});

test('shell-quoted --code is decoded for reading and original retained', () => {
  const { commandView, renderEventBody } = load();
  const cmd = `./aw action --task demo --code 'print('"'"'hello'"'"')\nprint(2)'`;
  const parsed = commandView(cmd);
  assert.equal(parsed.parsed, true); assert.equal(parsed.code, "print('hello')\nprint(2)");
  const html = renderEventBody({kind:'command', step:0, exit:0, command:cmd, output:''},0);
  assert.match(html, /按 shell 引号解码/); assert.match(html, /print\('hello'\)\nprint\(2\)/);
  assert.match(html, /查看原始命令/);
});

test('unsafe shell expressions are never interpreted and retain exact original', () => {
  const { commandView, renderEventBody } = load();
  for (const cmd of ['echo $(touch /tmp/never)', "aw --code 'unclosed", 'echo x | sh']) {
    assert.equal(commandView(cmd).parsed, false);
    assert.match(renderEventBody({kind:'command', command:cmd, output:''},0), /cmd-line/);
  }
});

test('other records describe recognizable fields and preserve escaped raw JSON', () => {
  const { renderEvent, renderEventBody } = load();
  const event = {kind:'structured',value:{type:'tool_call',message:'<script>bad</script>',extra:{x:1}}};
  const summary=renderEvent(event,0); assert.match(summary,/其他记录/); assert.match(summary,/tool_call/);
  assert.doesNotMatch(summary,/structured|JSON/);
  const body=renderEventBody(event,0); assert.match(body,/&lt;script&gt;/); assert.match(body,/查看原始数据/);
});
