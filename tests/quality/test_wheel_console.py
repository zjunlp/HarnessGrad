"""Offline installed-wheel Console resources and loopback observation only."""
from __future__ import annotations

import os
import hashlib
import stat
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_console_without_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    prefixes = ("console/", "harnessgrad/", "eval/", "data/", "methods/", "tools/",
                "scenarios/", "base_harness/")
    for name in tracked:
        src = ROOT / name
        if (name in {"pyproject.toml", "README.md", "LICENSE", "driver.py"} or name.startswith(prefixes)) and src.is_file():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    (source / "console/static/local-secret.json").write_text("synthetic private data")
    (source / "console/static/local-secret.js").write_text("synthetic private data")
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.defpath, "PYTHONNOUSERSITE": "1",
           "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1"}
    wheels = tmp_path / "wheels"
    def run(argv):
        result = subprocess.run(argv, cwd=tmp_path, env=env, text=True,
                                capture_output=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheels), str(source)])
    wheel, = wheels.glob("harnessgrad-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert {"console/server.py", "console/record_reader.py", "console/submissions.py", "console/evidence_service.py",
                "console/static/index.html", "console/static/console.js",
                "console/static/app.css", "console/LICENSE-DSH"} <= names
        expected_static = {f"console/static/{p.name}" for p in (ROOT / "console/static").iterdir()
                           if p.is_file() and not p.name.endswith(".test.js")}
        assert expected_static <= names, sorted(expected_static - names)
        assert not any("local-secret" in name for name in names)
        assert not any(name.startswith("console/uat/") for name in names)
    runtime = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(runtime)
    python = str(runtime / "bin/python")
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
    shutil.rmtree(source)
    assert "usage:" in run([str(runtime / "bin/harnessgrad"), "--help"])
    assert "--port" in run([python, "-m", "console.server", "--help"])
    installed_server = run([python, "-I", "-B", "-c",
                            "from console import server; print(server.__file__)"]).strip()
    assert "--port" in run([python, installed_server, "--help"])
    run([python, "-I", "-B", "-c", """
import importlib.util, sys
from console import record_reader
spec = importlib.util.spec_from_file_location('installed_console_probe', sys.argv[1])
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
assert server.run_row is record_reader.run_row
assert server.INSTALLED
""", installed_server])
    # Make the actual installed package tree read-only, preserving executable modes.
    lib = Path(subprocess.check_output(
        [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=tmp_path, env=env, text=True).strip())
    before = {p.relative_to(lib): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in lib.rglob("*") if p.is_file() and not p.is_symlink()}
    paths = [lib, *lib.rglob("*")]
    modes = {p: stat.S_IMODE(p.stat().st_mode) for p in paths if not p.is_symlink()}
    for path, mode in modes.items():
        path.chmod(mode & ~0o222)
    state_check = r"""
import json, os, tarfile, tempfile, io
from pathlib import Path
from unittest.mock import patch
home = Path(os.environ['HOME'])
expected = home / '.local/state/harnessgrad/console'
assert not expected.exists()
from console import server
assert server.INSTALLED and server.STATE_ROOT == expected
assert not expected.exists(), 'import must not create state'
server.save_settings({'task': {'model': 'synthetic'}})
assert server.SETTINGS.is_file()
code, body = server.harness_candidate_save('synthetic', {'agent.py': '# synthetic\n', 'harness.json': json.dumps({'name':'synthetic','version':'1','entrypoint':'agent.py'})}, from_base=False)
assert code == 201, body
assert (server.CANDIDATES / 'synthetic' / 'agent.py').is_file()
trial_root = home / 'trial-runs'; trial_root.mkdir()
os.environ['HG_CONSOLE_TRIAL_RUNS'] = str(trial_root)
from types import SimpleNamespace
with patch.object(server.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')) as trial:
    server.harness_candidate_trial('synthetic', 'demo', 'trial-one')
assert trial.called
trial_argv = trial.call_args.args[0]
assert trial_argv[trial_argv.index('--work-root') + 1] == str(trial_root / 'work')
archive = home / 'fixture.tar'
with tarfile.open(archive, 'w') as tar:
    data = json.dumps({'slug': 'synthetic'}).encode()
    item = tarfile.TarInfo('bundle.json'); item.size = len(data)
    tar.addfile(item, io.BytesIO(data))
with patch.object(server, 'author_bundles', return_value={'rows': [{'slug':'synthetic', 'valid':True}]}):
    code, body = server.author_import(archive)
assert code == 201, body
assert (server.AUTHORED_ROOT / 'synthetic/bundle.json').is_file()
form = {'harness':'loop', 'dataset':'demo', 'sampling':'all', 'rounds':'1',
        'trials':'1', 'jobs':'1', 'improver':'none', 'improver_model':'', 'method':'none'}
server.BUDGET_DIR = home / 'budget'; server.BUDGET_CAPACITY = '1'
with patch.object(server, 'guard_launch', return_value=(200, {})), \
     patch.object(server, 'improvers', return_value=[]), \
     patch.object(server.subprocess, 'Popen') as spawn:
    code, result = server.api_launch(form)
assert code == 202 and spawn.called
assert server.LAUNCH_LOG.is_file() and (server.STATE_ROOT / result['log']).is_file()
assert result['argv'][result['argv'].index('--work-root') + 1] == str(expected / 'work')
assert not any(p.is_relative_to(server.PLATFORM) for p in
    (server.SETTINGS, server.CANDIDATES, server.AUTHORED_ROOT, server.LAUNCH_DIR, server.LAUNCH_LOG))
"""
    try:
        run([python, "-I", "-B", "-c", state_check])
        after = {p.relative_to(lib): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in lib.rglob("*") if p.is_file() and not p.is_symlink()}
        assert after == before
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
    # Both arbitrary --target and user-site-shaped locations must be identified
    # from installed distribution metadata, without relying on venv sysconfig.
    for name in ("target", "user/lib/python3.12/site-packages"):
        destination = tmp_path / name
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(destination), str(wheel)])
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
                 "from console import server; assert server.INSTALLED; "
                 "assert server.STATE_ROOT == __import__('pathlib').Path.home() / '.local/state/harnessgrad/console'")
        run([python, "-I", "-B", "-c", probe, str(destination)])
    # Serve a synthetic receipt on loopback in-process; never start a model or driver.
    code = '''
import json, os, threading, urllib.request
from pathlib import Path
from console import server, record_reader
from harnessgrad.evidence_export import check_export
assert server.ROOT.resolve().is_relative_to(Path(os.environ["VIRTUAL_ENV"]).resolve())
run = server.RUNS / "synthetic"
run.mkdir(parents=True)
(run / "run_meta.json").write_text(json.dumps({"run_id":"synthetic", "dataset":"demo"}))
httpd = server.ConsoleServer(("127.0.0.1", 0), server.Handler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
try:
    base = "http://127.0.0.1:%s" % httpd.server_port
    for path in ("/", "/console.js", "/app.css"):
        with urllib.request.urlopen(base + path) as response:
            assert response.status == 200 and response.read()
    with urllib.request.urlopen(base + "/api/runs") as response:
        rows = {row['id']: row for row in json.load(response)['rows']}
    assert rows['synthetic']['curve_state'] == 'missing'
    assert rows['synthetic']['last'] is rows['synthetic']['calls'] is None
    assert rows['malformed']['curve_state'] == 'unparseable'
    assert rows['malformed']['last'] is rows['malformed']['invalid'] is None
    assert rows['malformed']['curve_errors'] == 2
    with urllib.request.urlopen(base + "/api/runs/synthetic/history?from=1&to=1") as response:
        history = json.load(response)
    assert history['rounds'][0]['stored'] is False
    with urllib.request.urlopen(base + "/api/evidence/catalog") as response:
        assert json.load(response)['scope']['run_count'] == 4
    manifest = json.loads(Path(os.environ['FIXTURE_MANIFEST']).read_text())
    request = urllib.request.Request(base + '/api/evidence/export',
        data=json.dumps({'manifest': manifest}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request) as response:
        assert response.headers['Content-Disposition'] == 'attachment; filename="evidence-export.json"'
        raw = response.read()
    exported = json.loads(raw)
    assert not check_export(exported)
    assert exported['manifest']['requests'][0]['result']['value'] == 2 / 3
    attempts = {item['run_id']: item for item in exported['manifest']['attempts']}
    assert attempts['synthetic']['status'] == 'unmeasured'
    assert attempts['malformed']['status'] == 'invalid'
    assert b'private task question and answer' not in raw
    assert b'hiddenCredential' not in raw
finally:
    httpd.shutdown(); thread.join(); httpd.server_close()
'''
    runs = tmp_path / "runs"
    runs.mkdir()
    # Materialize authored synthetic data before entering the isolated interpreter;
    # no test/source modules are available to the installed process.
    import json
    from test_evidence_export import author, save_run
    save_run(runs, 'base')
    save_run(runs, 'sub', 1, tree='b')
    malformed = runs / 'malformed'
    malformed.mkdir()
    (malformed / 'curve.jsonl').write_text('broken\n[]\n')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(author()))
    env.update(HG_CONSOLE_RUNS=str(runs), VIRTUAL_ENV=str(runtime), FIXTURE_MANIFEST=str(manifest))
    run([python, "-I", "-B", "-c", code])


def test_checkout_state_override_and_legacy_defaults(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    code = """
import os
from pathlib import Path
from console import server
assert not server.INSTALLED
custom = os.environ.get('HG_CONSOLE_STATE_ROOT')
if custom:
    root = Path(custom)
    assert server.STATE_ROOT == root
    assert server.CANDIDATES == root / 'candidates'
    assert server.AUTHORED_ROOT == root / 'authored'
    assert server.ASSISTANT_THREADS == root / '.local/assistant-threads.json'
else:
    assert server.STATE_ROOT == server.ROOT
    assert server.CANDIDATES == server.PLATFORM_ROOT / 'data/candidates'
    assert server.AUTHORED_ROOT == server.PLATFORM_ROOT / 'data/authored'
assert not Path(os.environ['HOME'], 'custom').exists()
"""
    for custom in (False, True):
        selected = dict(env)
        if custom:
            selected['HG_CONSOLE_STATE_ROOT'] = str(tmp_path / 'custom')
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, env=selected,
                                text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
