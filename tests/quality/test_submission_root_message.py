"""Submission setup failures must give actionable, safely rendered guidance."""
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'console/static/submit.js'


def test_scenario_root_configuration_error_is_actionable_and_escaped():
    program = r'''
const fs = require('fs');
const vm = require('vm');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {dataset: {}, style: {}, value: '', options: [{}],
    addEventListener() {}, appendChild() {}, dispatchEvent() {}});
  return elements.get(id);
}
global.window = {};
vm.runInThisContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[1]), 'launch-i18n.js'), 'utf8'));
window.HGI18n = {t: key => window.HG_LAUNCH_I18N.zh[key] || key};
global.location = {search: ''};
global.document = {getElementById: element, querySelectorAll: () => [],
  querySelector: () => element('query'), readyState: 'complete'};
global.fetch = async () => ({ok: false, json: async () => ({error: {
  code: 'service-not-ready', detail: process.argv[2]}})});
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
setImmediate(() => console.log(element('submit-global-error').innerHTML));
'''
    def render(detail):
        return subprocess.run(['node', '-e', program, str(SCRIPT), detail],
                              check=True, capture_output=True, text=True).stdout

    message = render('configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform')
    assert 'HG_CONSOLE_SUBMISSIONS_ROOT=/tmp/hg-console-submissions' in message
    assert '重启服务' in message
    assert '平台目录之外' in message
    assert '持久' in message
    assert 'bwrap' not in message
    other = render('specific & <unsafe> detail')
    assert 'specific &amp; &lt;unsafe&gt; detail' in other
    assert 'bwrap' not in other
