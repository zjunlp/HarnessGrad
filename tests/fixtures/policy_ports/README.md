# Policy port compatibility fixture

`pre_refactor_ace_skillopt.json` records synthetic outputs of the experiment
policy ports at main revision `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6`,
before the reusable ACE and SkillOpt stages were extracted. The fixture was
produced offline using `tests/quality/native_helpers.py` from that revision,
scripted editor replies and no benchmark or provider. Temporary paths are
normalized to `<WORK>`.

`tests/quality/test_policy_port_pre_refactor_compat.py` repeats the same control
against the current code and compares prompts, reported metadata and candidate
file contents. It covers one ACE reflection/curation and one SkillOpt fast round
with success, failure and cross-source merge. It does not establish equivalence
for other configurations, all error paths or full experiment execution. Do not
regenerate this evidence from the refactored implementation.

`pre_refactor_evo_harness.json` was recorded from the same original revision.
It covers a synthetic three-failure topic/general compilation and rejection of a
skill citing an unknown source. `test_evo_harness_pre_refactor_compat.py` compares
prompts, candidate files and reports with that evidence. It does not establish
complete EvoHarness equivalence or real-world skill quality.

`pre_refactor_gepa.json` was captured from LightRSI fix revision `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` using its `native_helpers.py` and a scripted editor response. `test_gepa_pre_refactor_compat.py` compares the prompt, candidate files and report in one synthetic GEPA proposal; it is not a complete method or benchmark equivalence proof.

`pre_refactor_gepa_boundaries.json` records four additional synthetic cases from the same LightRSI fix: no failing task, a duplicate instruction, an empty instruction and a non-string instruction. The same compatibility test checks their prompts, reports and candidate contents.
