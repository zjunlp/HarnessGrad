const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(__dirname + '/i18n.js', 'utf8');
function boot(browser, initial = {}, blocked = false) {
  const store = { ...initial }; let reloads = 0, alerts = 0;
  const localStorage = { getItem(k) { if (blocked) throw Error(); return store[k] || null; }, setItem(k,v) { if (blocked) throw Error(); store[k] = v; }, removeItem(k) { if (blocked) throw Error(); delete store[k]; } };
  const document = { documentElement: {}, readyState: 'loading', addEventListener() {}, querySelectorAll() { return []; } };
  const window = { HG_SHELL_I18N: { en: {hello:'Hello'}, zh: {hello:'你好'} } };
  vm.runInNewContext(source, { window, document, localStorage, navigator: { language: browser }, location: { reload() { reloads++; } }, alert() { alerts++; } });
  return { api:window.HGI18n, document, store, get reloads(){return reloads}, get alerts(){return alerts} };
}
test('browser default and explicit choice', () => {
  assert.equal(boot('zh-TW').api.t('hello'), '你好');
  assert.equal(boot('fr').document.documentElement.lang, 'en');
  assert.equal(boot('zh', {'hg.language':'en'}).api.t('hello'), 'Hello');
});
test('selection persists and reset returns to browser', () => {
  const c = boot('zh'); c.api.setLanguage('en'); assert.equal(c.store['hg.language'], 'en'); assert.equal(c.reloads, 1);
  assert.equal(boot('zh', c.store).api.language, 'en'); c.api.setLanguage('auto'); assert.equal(c.store['hg.language'], undefined);
  assert.equal(boot('zh', c.store).api.language, 'zh');
});
test('blocked storage reports failure without losing page state', () => {
  const c = boot('en', {}, true); c.api.setLanguage('zh'); assert.equal(c.reloads, 0); assert.equal(c.alerts, 1);
});
