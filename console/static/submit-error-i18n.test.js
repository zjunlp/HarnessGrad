const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const read = name => fs.readFileSync(__dirname + '/' + name, 'utf8');
const window = {}; vm.runInNewContext(read('launch-i18n.js'), {window});
const source = read('submit.js');
function errors(language) {
  const start = source.indexOf('  const ERROR_MESSAGES =');
  const end = source.indexOf('  const statusText =', start);
  const extract = source.slice(start, end) + '\nthis.errorText = errorText;';
  const ctx = {t: key => window.HG_LAUNCH_I18N[language][key], document: {getElementById(){}}};
  vm.runInNewContext(extract, ctx); return ctx.errorText;
}
test('storage guidance preserves backend detail in both languages', () => {
  for (const lang of ['en','zh']) {
    const output = errors(lang)('service-not-ready', 'configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform');
    assert.match(output, /configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform/);
    assert.match(output, /HG_CONSOLE_SUBMISSIONS_ROOT=\/tmp\/hg-console-submissions/);
  }
});
test('error prefixes translate while raw detail and field survive', () => {
  const en = errors('en')('unsafe-path', 'raw <detail>', 'archive');
  assert.match(en, /Unsafe archive path/); assert.match(en, /raw <detail>/); assert.match(en, /archive/);
  assert.match(errors('zh')('unsafe-path'), /越界路径/);
});
test('confirmation has independent placeholders and no POST in tests', () => {
  for (const lang of ['en','zh']) assert.match(window.HG_LAUNCH_I18N[lang]['submit.confirm'], /\{file\}.*\{scenario\}/);
  assert.match(source, /t\("submit.confirm"\)/);
});
