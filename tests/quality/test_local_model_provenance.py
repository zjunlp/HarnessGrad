"""No model/server required: fail closed on incomplete or misleading sources."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('provenance', Path(__file__).parents[2]/'tools/produce_local_model_provenance.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_digest_is_order_independent_and_content_bound():
    assert m.digest({'a': 1, 'b': 2}) == m.digest({'b': 2, 'a': 1})
    assert m.digest({'a': 1}) != m.digest({'a': 2})


def test_sources_fail_closed(tmp_path):
    (tmp_path/'weight').write_bytes(b'abc')
    assert m.manifest(tmp_path, ['weight'])['weight']['sha256'] == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    with pytest.raises(ValueError):
        m.manifest(tmp_path, ['missing'])
    (tmp_path/'link').symlink_to(tmp_path/'weight')
    with pytest.raises(ValueError):
        m.manifest(tmp_path, ['link'])
    with pytest.raises(ValueError):
        m.manifest(tmp_path, ['../weight'])


def test_process_rejects_unrelated_process(tmp_path):
    import os
    with pytest.raises(ValueError, match='does not declare'):
        m.process_binding(os.getpid(), tmp_path)


@pytest.fixture
def sources(tmp_path):
    import json
    (tmp_path/'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'layer': 'old.safetensors'}}))
    (tmp_path/'old.safetensors').write_bytes(b'old source')
    (tmp_path/'new.safetensors').write_bytes(b'new source')
    for name in m.TOKENIZER + m.CONFIG:
        (tmp_path/name).write_bytes(b'{}')
    return tmp_path


def test_index_changed_between_selection_and_hashing_is_refused(sources, monkeypatch):
    import json
    original = m.manifest
    def changed(root, names):
        (root/'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'layer': 'new.safetensors'}}))
        return original(root, names)
    monkeypatch.setattr(m, 'manifest', changed)
    with pytest.raises(ValueError, match='index changed'):
        m.produce(sources, [100, 200])


def _mock_process(monkeypatch, tmp_path, argv):
    proc = tmp_path/'proc'/'100';proc.mkdir(parents=True)
    (proc/'stat').write_text('100 (synthetic) ' + ' '.join(['S'] + ['0']*18 + ['123']))
    (proc/'cmdline').write_bytes(('\0'.join(argv)+'\0').encode())
    monkeypatch.setattr(m, 'Path', lambda path: tmp_path/'proc' if str(path)=='/proc' else Path(path))


def _argv(root):
    return ['python', '-m', 'vllm.entrypoints.openai.api_server', '--model', str(root),
            '--gpu-memory-utilization', '0.80', '--max-model-len', '262144',
            '--served-model-name', 'qwen3.5-9b-local', '--port', '8001']


@pytest.mark.parametrize('tokenizer', [['--tokenizer', '/different'], ['--tokenizer=/different']])
def test_both_separate_tokenizer_spellings_refuse(tmp_path, monkeypatch, tokenizer):
    _mock_process(monkeypatch, tmp_path, _argv(tmp_path)+tokenizer)
    with pytest.raises(ValueError, match='separate tokenizer'):
        m.process_binding(100, tmp_path)


@pytest.mark.parametrize('extra', [
    ['--tokenizer', '/same', '--tokenizer=/different'],
    ['--port=8002'], ['--model=/different'], ['--max-model-len=1'],
])
def test_duplicate_critical_flags_refuse(tmp_path, monkeypatch, extra):
    _mock_process(monkeypatch, tmp_path, _argv(tmp_path)+extra)
    with pytest.raises(ValueError, match='duplicate'):
        m.process_binding(100, tmp_path)


def test_root_in_unrelated_argument_is_not_a_model_binding(tmp_path, monkeypatch):
    argv=_argv(tmp_path);argv[argv.index('--model')]='--download-dir'
    _mock_process(monkeypatch, tmp_path, argv)
    with pytest.raises(ValueError, match='does not declare'):
        m.process_binding(100, tmp_path)


@pytest.mark.parametrize('style', ['separate', 'equals', 'serve', 'python-script-serve', 'python-module-serve'])
def test_supported_model_and_flag_spellings_omit_secrets(tmp_path, monkeypatch, style):
    import json
    argv=_argv(tmp_path)
    if style=='equals':
        argv=argv[:3]+[argv[i]+'='+argv[i+1] for i in range(3,len(argv),2)]
    elif style=='serve':
        argv=['vllm','serve',str(tmp_path)]+argv[5:]
    elif style=='python-script-serve':
        argv=['python3','/example/bin/vllm','serve',str(tmp_path)]+argv[5:]
    elif style=='python-module-serve':
        argv=['python3','-m','vllm.entrypoints.cli.main','serve',str(tmp_path)]+argv[5:]
    argv+=['--tokenizer='+str(tmp_path),'--api-key','DO-NOT-RECORD','--unknown-secret=ALSO-PRIVATE']
    _mock_process(monkeypatch, tmp_path, argv)
    result=m.process_binding(100,tmp_path)
    assert result['flags']['--port']=='8001'
    assert result['model_path']==str(tmp_path)
    assert 'DO-NOT-RECORD' not in json.dumps(result)
    assert 'ALSO-PRIVATE' not in json.dumps(result)


def test_produce_bindings_match_exact_manifest_hashes(sources, monkeypatch):
    monkeypatch.setattr(m,'process_binding',lambda pid,root: {
        'pid':pid,'start_ticks':'123','model_path':str(root),
        'flags':{'--port':'8001' if pid==100 else '8002'}})
    result=m.produce(sources,[100,200])
    assert set(result['weights'])=={'old.safetensors','model.safetensors.index.json'}
    for binding in result['bindings_by_port'].values():
        assert binding['weights_sha256']==m.digest(result['weights'])
        assert binding['tokenizer_sha256']==m.digest(result['tokenizer'])
        assert binding['deployment_sha256']==m.digest(result['deployment'])


def test_serve_inside_unrelated_flag_is_not_a_model_binding(tmp_path, monkeypatch):
    argv=_argv(tmp_path);argv[argv.index('--model')]='--download-dir'
    argv+=['--arbitrary', 'serve', str(tmp_path)]
    _mock_process(monkeypatch, tmp_path, argv)
    with pytest.raises(ValueError, match='does not declare'):
        m.process_binding(100, tmp_path)


@pytest.mark.parametrize('prefix', [
    ['python3', 'unrelated.py'],
    ['unrelated'],
    ['python3', '-m', 'unrelated'],
    ['vllm', 'unrelated-subcommand'],
    ['unrelated', '-m', 'vllm.entrypoints.openai.api_server'],
])
def test_unrelated_invocation_with_all_server_flags_is_refused(tmp_path, monkeypatch, prefix):
    _mock_process(monkeypatch, tmp_path, prefix+_argv(tmp_path)[3:])
    with pytest.raises(ValueError, match='recognized vLLM invocation'):
        m.process_binding(100, tmp_path)


@pytest.mark.parametrize('alias', ['-m', '-t', '-m=/different', '-t=/different'])
def test_short_model_tokenizer_overrides_refuse(tmp_path, monkeypatch, alias):
    _mock_process(monkeypatch, tmp_path, _argv(tmp_path)+[alias, '/different'])
    with pytest.raises(ValueError, match='unsupported short'):
        m.process_binding(100, tmp_path)


def test_index_drift_after_weight_hashing_refuses(sources, monkeypatch):
    original = m.manifest
    def changed(root, names):
        result = original(root, names)
        if set(names) == set(m.CONFIG):
            (root/'model.safetensors.index.json').write_text('{}')
        return result
    monkeypatch.setattr(m, 'manifest', changed)
    monkeypatch.setattr(m, 'process_binding', lambda pid, root: {})
    with pytest.raises(ValueError, match='index changed during collection'):
        m.produce(sources, [100, 200])


def test_existing_output_refused_before_sources_read(tmp_path, monkeypatch):
    output = tmp_path/'receipt.json'
    output.write_text('old immutable receipt')
    monkeypatch.setattr(m.sys, 'argv', ['producer', '--model-dir', str(tmp_path),
                                     '--pid', '100', '--output', str(output)])
    monkeypatch.setattr(m, 'produce', lambda *args: pytest.fail('must not read sources'))
    with pytest.raises(SystemExit):
        m.main()
    assert output.read_text() == 'old immutable receipt'
