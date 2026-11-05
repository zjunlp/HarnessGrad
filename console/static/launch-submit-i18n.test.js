const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8');
const window = {};
vm.runInNewContext(read('launch-i18n.js'), { window });
const dict = window.HG_LAUNCH_I18N;

test('both dictionaries cover all explicit keys on both pages', () => {
  for (const name of ['launch.html', 'submit.html', 'launch.js', 'submit.js']) {
    for (const [, key] of read(name).matchAll(/(?:data-i18n(?:-placeholder|-aria-label)?=|t\()["'](ls\.\d+)["']/g)) {
      for (const lang of ['en', 'zh']) assert.ok(dict[lang][key], `${name}: ${lang} ${key}`);
    }
  }
});
test('dictionary loads before loader and page code', () => {
  for (const name of ['launch.html', 'submit.html']) {
    const html = read(name);
    assert.ok(html.indexOf('/launch-i18n.js') < html.indexOf('/i18n.js'));
    assert.ok(html.indexOf('/i18n.js') < html.indexOf(name.replace('.html', '.js')));
  }
});
test('dynamic translations preserve preflight data and secrets stay out of GET', () => {
  const js = read('launch.js');
  assert.match(js, /task_key_present: Boolean\(card\.task_api_key\)/);
  assert.match(js, /meta_key_present: Boolean\(card\.meta_api_key\)/);
  assert.match(js, /\(d\.preflight \|\| \{\}\)\.detail/);
  assert.ok(!js.slice(js.indexOf('const q ='), js.indexOf('try {', js.indexOf('const q ='))).includes('task_api_key: card.task_api_key'));
});
