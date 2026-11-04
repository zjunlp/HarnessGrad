// Run with: node --test console/static/trace-view.test.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function renderer() {
  // Expose the private renderer to exercise the same shared module both pages mount.
  const source = fs.readFileSync(path.join(__dirname, 'trace-view.js'), 'utf8')
    .replace('    /* ── 绑定 ─────────────────────────────────────────── */',
      '    window.renderEventForTest = renderEvent;\n    /* ── 绑定 ─────────────────────────────────────────── */');
  const element = () => ({ innerHTML: '', textContent: '', classList: { add() {} }, querySelectorAll: () => [], setAttribute() {} });
  const elements = new Map();
  const root = element();
  root.querySelector = (key) => { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); };
  root.querySelectorAll = () => [];
  const context = { window: {}, fetch: async () => ({ ok: true, json: async () => ({ rows: [] }) }) };
  vm.runInNewContext(source, context);
  context.window.HGTraceView.mount(root);
  return context.window.renderEventForTest;
}

test('structured JSON is pretty, complete and escaped including unknown fields', () => {
  const render = renderer();
  const html = render({ kind: 'structured', line: 7, value: { custom: '<img src=x onerror="x">', nested: [1, null, false] } }, 0);
  assert.match(html, /行 7/);
  assert.match(html, /&lt;img src=x onerror=\\&quot;x\\&quot;&gt;/);
  assert.match(html, /&quot;nested&quot;: \[/);
  assert.doesNotMatch(html, /<img/);
  assert.match(render({ kind: 'structured', value: null }, 0), />null<\/pre>/);
});

test('malformed raw lines and long JSON retain content and expansion', () => {
  const render = renderer();
  const html = render({ kind: 'raw', text: '<script>bad &', line: 3 }, 0);
  assert.match(html, /&lt;script&gt;bad &amp;/);
  assert.doesNotMatch(html, /<script>/);
  const long = render({ kind: 'structured', value: Array.from({ length: 20 }, (_, i) => i) }, 0);
  assert.match(long, /data-expand/);
  assert.match(long, /19\n\]/);
});

test('legacy reply retains all JSON fields and truncation notice; commands expand', () => {
  const render = renderer();
  const reply = render({ kind: 'reply', step: 1, text: JSON.stringify({ tool: 'bash', command: 'ls', unknown: '<payload>' }), truncated: true }, 0);
  assert.match(reply, /&quot;unknown&quot;: &quot;&lt;payload&gt;&quot;/);
  assert.match(reply, /回复被截断/);
  const command = render({ kind: 'command', step: 1, output: Array(16).fill('line').join('\n'), output_chars: 79, truncated: true }, 0);
  assert.match(command, /data-expand/);
  assert.match(command, /输出被裁剪/);
});
