import json, os, pathlib, signal, subprocess, sys, tempfile, time, uuid
ROOT = pathlib.Path('/mnt/20t/xubuqiang/hg-wt/w-banhung')
sys.path.insert(0, str(ROOT))
os.environ['HG_AGENT_BACKEND'] = 'mock'
os.environ['TMPDIR'] = '/tmp/banhung-tmp'

if len(sys.argv) > 1:
    from eval.run import runner
    mode, repo = sys.argv[1], pathlib.Path(sys.argv[2])
    start = time.monotonic()
    result = runner.run_one(repo, {'task_id': 'synthetic-timeout', 'goal': 'synthetic'},
        timeout_s=0.3, sandbox=mode == 'sandbox',
        verifier={'kind': 'answer', 'expected': 'completed'})
    print(json.dumps({'elapsed_s': round(time.monotonic()-start, 3), 'result': result}), flush=True)
    raise SystemExit

for mode in ('host', 'sandbox'):
    with tempfile.TemporaryDirectory(prefix='repro-', dir='/tmp/banhung-tmp') as td:
        root = pathlib.Path(td)
        repo = root / 'work' / 'run' / 'workspace'
        repo.mkdir(parents=True)
        token = 'banhung-' + uuid.uuid4().hex
        (repo / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py', 'name': 'synthetic', 'version': '1'}))
        # Parent exits immediately. Its child inherits both captured pipes and lives 30s.
        (repo / 'agent.py').write_text(
            'import subprocess, sys\n'
            f'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", {token!r}])\n')
        env = {**os.environ, 'HARNESSGRAD_WORK_ROOT': str(root / 'work')}
        print('MODE', mode, 'inner_timeout_s=0.3 outer_timeout_s=2', flush=True)
        start = time.monotonic()
        proc = subprocess.Popen([sys.executable, __file__, mode, str(repo)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=2)
            print('RETURNED', proc.returncode, 'outer_elapsed_s', round(time.monotonic()-start,3))
            print(out.strip())
            print('stderr', repr(err))
        except subprocess.TimeoutExpired:
            print('OUTER_TIMEOUT: real launch did not return within 2s')
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate(timeout=2)
            print('stderr', repr(err))
        match = subprocess.run(['pgrep', '-af', token], capture_output=True, text=True)
        print('POST_RUN pgrep -af', token, 'rc=', match.returncode, 'stdout=', repr(match.stdout))
        for row in match.stdout.splitlines():
            try:
                os.kill(int(row.split()[0]), signal.SIGKILL)
            except ProcessLookupError:
                pass
        print('task_trees', [str(p) for p in pathlib.Path('/tmp/banhung-tmp').glob('hg-task-*')])
