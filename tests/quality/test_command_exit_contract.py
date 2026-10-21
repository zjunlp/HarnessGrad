"""A failed scorer cannot publish a stale reward under explicit strict completion."""
import subprocess
import sys

import pytest

from data.scaffolds.runtime_extensions import validate_result
from eval.verification import verifier


@pytest.mark.parametrize('required,code,score', [
    (None, 3, 1), (False, 3, 1), (True, 3, 1), (True, 78, 0),
    (True, 0, 0), (True, 0, 1),
])
@pytest.mark.parametrize('native', [False, True])
def test_command_completion_uses_process_exit_before_reward(tmp_path, monkeypatch, required, code, score, native):
    contract = {'version': 1, 'score_range': [0, 1]}
    if required is not None:
        contract['require_exit_zero'] = required
    spec = {'kind': 'command', 'argv': [sys.executable, 'check.py'],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': contract,
            'inputs': [{'path': 'check.py', 'content':
                "from pathlib import Path\nimport sys\n"
                f"Path('verifier/reward.txt').write_text({str(score)!r})\n"
                "Path('verifier/evidence.json').write_text('{\"measured\":true}')\n"
                "Path('verifier').chmod(0o555)\n"
                f"sys.exit({code})\n"}]}
    class Native:
        def run(self, argv, *, workdir, **kwargs):
            return subprocess.run(argv, cwd=workdir, text=True, capture_output=True)
    if required and code:
        monkeypatch.setattr(verifier, '_read_json', lambda *a: pytest.fail('failed scorer evidence was read'))
    try:
        result = verifier._verify(tmp_path, spec, '', sandbox=False, timeout_s=5,
                                  **({'native_runtime': Native()} if native else {}))
        # The output remains present and undeletable by its directory permissions;
        # refusing the score must not depend on the scorer deleting it successfully.
        assert (tmp_path / 'verifier/reward.txt').read_text() == str(score)
        if required and code:
            assert result['score'] is None and result['outcome'] == 'unmeasured'
            assert result['exit_code'] == code and 'requires exit 0' in result['unmeasured']
        else:
            assert result['score'] == score
    finally:
        (tmp_path / 'verifier').chmod(0o755)


@pytest.mark.parametrize('value', [0, 1, 'true', None, []])
def test_completion_flag_requires_boolean(value):
    with pytest.raises(ValueError, match='must be boolean'):
        validate_result({'version': 1, 'score_range': [0, 1], 'require_exit_zero': value})
