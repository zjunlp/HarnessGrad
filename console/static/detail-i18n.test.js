const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
test('English chrome never translates model text or unknown JSON', async () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'detail-i18n.js'), 'utf8'), { window });
  window.HGI18n = { t: k => window.HG_DETAIL_I18N.en[k] || k };
  let source = fs.readFileSync(path.join(__dirname, 'trace-view.js'), 'utf8');
  source = source.replace('    /* ── 绑定 ─────────────────────────────────────────── */', '    window.body = renderEventBody; window.summary = renderEvent;\n    /* ── 绑定 ─────────────────────────────────────────── */');
  const el = () => ({ innerHTML: '', textContent: '', classList: { add() {} }, querySelectorAll: () => [], setAttribute() {} });
  const nodes = new Map(), root = el();
  root.querySelector = k => { if (!nodes.has(k)) nodes.set(k, el()); return nodes.get(k); };
  vm.runInNewContext(source, { window, fetch: async () => ({ ok: true, json: async () => ({ rows: [] }) }) });
  await window.HGTraceView.mount(root).ready;
  assert.match(window.summary({kind: 'reply', step: 1}, 0), /Model reply/);
  assert.match(window.body({kind: 'reply', step: 1, text: '模型回复：原始中文 <内容>'}), /模型回复：原始中文 &lt;内容&gt;/);
  assert.match(window.body({kind: 'structured', value: { unknown: '模型回复' }}), /模型回复/);
});
test('trace module loads with no window', () => {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'trace-view.js'), 'utf8'), {});
});
