const { test } = require('node:test');
const assert = require('node:assert/strict');
const { rows, selected } = require('./launch-methods.js');

test('known names are concise and IDs and catalogue order are preserved', () => {
  const names = {
    ahe:'AHE', dgm:'DGM', gepa:'GEPA', adas:'ADAS', ace:'ACE', skillopt:'SkillOpt',
    opro:'OPRO', meta_harness:'Meta-Harness', evo_harness:'Evo-Harness', evoprompt:'EvoPrompt',
    arbor:'Arbor', darwinx:'DarwinX', modular_rsi:'Modular RSI', dream_rsi:'Dream-RSI',
    harnessx:'HarnessX', hyperagents:'HyperAgents', lightrsi:'LightRSI', rrsi:'RRSI',
    sica_ci:'SICA', tthe:'TTHE', noop:'No-op', raise_step_cap:'Raise step cap', llm_improver:'LLM Improver'
  };
  const input = ['a', ...Object.keys(names), 'b', 'echo_base', 'A', 'future_<"&'].map(name => ({ name, title: 'description must not appear' }));
  assert.deepEqual(rows(input, 'None'), [
    { value:'none', label:'None' }, ...Object.entries(names).map(([value,label]) => ({value,label})),
    {value:'A',label:'A'}, {value:'future_<"&',label:'future_<"&'}
  ]);
});
test('selection is always visible, preserving current then valid default', () => {
  const options = rows([{name:'a'}, {name:'gepa'}, {name:'unknown'}], 'None');
  assert.equal(selected(options, 'none', 'gepa'), 'none');
  assert.equal(selected(options, 'unknown', 'gepa'), 'unknown');
  assert.equal(selected(options, 'a', 'gepa'), 'gepa');
  assert.equal(selected(options, '', 'a'), 'none');
});
