"""Manual transport-only control: GET /v1/models; never generate or run a dataset.

Run with: TMPDIR=/tmp python3 tests/quality/native_model_route_control.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.containers import sandbox
from eval.policy.runtime_policy import admit_native_network
from eval.service.native_model_route import NativeModelRoute

PROBE = r'''
import json, os, signal, socket, urllib.request
assert os.environ['HG_AGENT_BACKEND'] == 'openai'
out = {'network_namespace': os.readlink('/proc/self/ns/net'),
       'backend': os.environ['HG_AGENT_BACKEND']}
def timeout(*args):
    raise TimeoutError('control deadline')
signal.signal(signal.SIGALRM, timeout)
def check(label, fn):
    signal.alarm(4)
    try:
        out[label] = {'ok': True, 'value': fn()}
    except (OSError, TimeoutError) as exc:
        out[label] = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    finally:
        signal.alarm(0)
def models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(os.environ['HG_AGENT_BASE_URL'] + '/models', timeout=3) as r:
        data = json.loads(r.read())
        return {'status': r.status, 'model_ids': [m['id'] for m in data['data']]}
def tcp():
    with socket.create_connection(('1.1.1.1', 80), timeout=3):
        return 'connected to 1.1.1.1:80'
check('model_metadata', models)
check('dns_example_com', lambda: sorted({r[4][0] for r in socket.getaddrinfo('example.com', 443)}))
check('public_tcp', tcp)
out['client_base_url'] = os.environ['HG_AGENT_BASE_URL']
print(json.dumps(out, sort_keys=True))
'''


def main():
    env_spec = {"kind": "files", "network": "none"}
    admit_native_network(env_spec, {"entrypoint": "transport-probe.py"}, "openai")
    print(json.dumps({"admission": "ADMITTED", "env": env_spec, "backend": "openai",
                      "model_endpoint": "http://127.0.0.1:8002/v1",
                      "inference_requested": False}, sort_keys=True), flush=True)
    with tempfile.TemporaryDirectory(prefix="netroute-live-", dir="/tmp") as tmp:
        work, task = Path(tmp) / "work", Path(tmp) / "task"
        work.mkdir()
        task.mkdir()
        route = NativeModelRoute("http://127.0.0.1:8002/v1").start()
        directory = route.directory
        try:
            plan = sandbox.command(["/usr/bin/python3", "-c", PROBE], platform=ROOT,
                                   work_root=work, task_dir=task, repo=work,
                                   env={'HG_AGENT_BACKEND': 'openai'},
                                   network="none", model_socket=route.socket_path,
                                   model_path=route.path)
            results = {}
            for label, isolate in (("isolated", True), ("same_mounts_without_unshare_net", False)):
                cmd = plan if isolate else [part for part in plan if part != "--unshare-net"]
                proc = subprocess.run(cmd, capture_output=True, text=True, env={}, timeout=25)
                print(json.dumps({"control": label, "returncode": proc.returncode,
                                  "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()},
                                 sort_keys=True), flush=True)
                assert proc.returncode == 0
                results[label] = json.loads(proc.stdout)
            isolated, inherited = results.values()
            assert isolated["model_metadata"]["ok"] and inherited["model_metadata"]["ok"]
            assert not isolated["dns_example_com"]["ok"] and not isolated["public_tcp"]["ok"]
            assert inherited["dns_example_com"]["ok"] and inherited["public_tcp"]["ok"]
            assert isolated["network_namespace"] != os.readlink("/proc/self/ns/net")
            assert inherited["network_namespace"] == os.readlink("/proc/self/ns/net")
            print(json.dumps({"relay": route.account()}, sort_keys=True), flush=True)
        finally:
            route.stop()
            print(json.dumps({"relay_directory_removed": not directory.exists()}, sort_keys=True), flush=True)
    print(json.dumps({"temporary_control_directory_removed": not Path(tmp).exists(),
                      "result": "PASS"}, sort_keys=True))


if __name__ == "__main__":
    main()
