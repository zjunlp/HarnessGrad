"""Offline syntax/configuration protocol tests at the extracted CLI owner."""
import json
import sys

import pytest

from harnessgrad.entrypoints import experiment_config, run_parser


DEFAULTS = {'dataset': 'demo', 'sampling': 'diagnostic', 'rounds': 3,
            'trials': 1, 'jobs': 1, 'side': 'train', 'pool': None, 'split': 'declared'}


def test_supplement_recovery_requires_resumed_train_and_safe_run_id():
    _, args, _ = run_parser.parse_run_args(['--resume', '--resume-supplement-run', 'supplement-1'], DEFAULTS)
    assert args.resume_supplement_run == 'supplement-1'
    for argv in (['--resume-supplement-run', 'supplement-1'],
                 ['--resume', '--side', 'eval', '--resume-supplement-run', 'supplement-1'],
                 ['--resume', '--resume-supplement-run', '../other']):
        with pytest.raises(SystemExit):
            run_parser.parse_run_args(argv, DEFAULTS)


def test_batch_supplement_requires_unique_safe_rounds():
    _, args, _ = run_parser.parse_run_args(['--resume', '--resume-supplement-run', 'first',
        '--resume-batch-supplement', '12=second,13=third'], DEFAULTS)
    assert args.resume_batch_supplement == '12=second,13=third'
    for value in ('12=second,12=other', '12=../outside', '0=bad', '12='):
        with pytest.raises(SystemExit):
            run_parser.parse_run_args(['--resume', '--resume-supplement-run', 'first',
                '--resume-batch-supplement', value], DEFAULTS)


def test_expansion_preserves_explicit_precedence_and_original_argv(tmp_path):
    config = tmp_path / 'experiment.json'
    config.write_text(json.dumps({'dataset': 'demo', 'jobs': 2}))
    argv = ['--config', str(config), '--jobs', '4', '--rounds', '0']
    original = list(argv)
    process_argv = list(sys.argv)
    _, args, explicit = run_parser.parse_run_args(argv, DEFAULTS)
    assert (args.dataset, args.jobs, args.rounds) == ('demo', 4, 0)
    assert {'dataset', 'jobs', 'rounds'} <= explicit
    assert 'sampling' not in explicit
    assert argv == original
    assert sys.argv == process_argv


def test_parser_uses_canonical_config_help_port(monkeypatch):
    monkeypatch.setattr(run_parser, 'config_help', lambda: 'injected configuration')
    assert run_parser.build_parser().epilog == 'injected configuration'


@pytest.mark.parametrize('argv', [
    ['--expected-harness-sha256', 'abc', '--jobs', '1', '--jobs', '2'],
    ['--expected-harness-sha256', 'abc', '--no-sandbox'],
    ['--expected-harness-sha256', 'abc', '--round', '0'],
    ['--harness-runtime-binding', '/tmp/binding.json', '--resume'],
    ['--harness-runtime-binding', '/tmp/binding.json', '--h0-from', 'donor'],
])
def test_reviewed_and_binding_syntax_refuses_before_orchestration(argv):
    with pytest.raises(SystemExit) as exc:
        run_parser.parse_run_args(argv, DEFAULTS)
    assert exc.value.code == 2


def test_legacy_duplicate_and_abbreviation_behavior_remains():
    _, args, _ = run_parser.parse_run_args(['--round', '1', '--rounds', '2'], DEFAULTS)
    assert args.rounds == 2


@pytest.mark.parametrize('argv', [['--config'], ['--set'], ['--config', 'a', '--config', 'b']])
def test_malformed_expansion_is_rejected(argv):
    with pytest.raises(SystemExit):
        experiment_config.expand_experiment_argv(argv)
