#!/usr/bin/env python3
"""Native files web capability via the existing private AF_UNIX tool relay.

Reuse the platform's selected provider, credentials, SSRF checks and fetch parser.
No actor can select an upstream endpoint or obtain provider credentials. This avoids
Docker networking and an unreachable host TCP gateway in a network=none namespace.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.service.tool_transport import Relay
from tools.web_gateway import WebGateway, ProviderError


def main():
    p = argparse.ArgumentParser()
    for name in ('case-spec', 'socket', 'receipt'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--deadline-s', type=float)
    p.add_argument('--max-calls', type=int)
    p.add_argument('--max-line-bytes', type=int)
    a = p.parse_args()
    spec = json.loads(a.case_spec.read_text())
    if set(spec) != {'task_id', 'dataset'}:
        raise ValueError('web relay requires selected task binding')
    gateway = WebGateway(record_path=Path(os.environ['HG_RELAY_RECORD_PATH']).with_name('web-requests.jsonl'))
    stop = threading.Event()
    failure = None
    lock = threading.Lock()
    def handle(op, args):
        nonlocal failure
        with lock:
            try:
                if op == 'search' and set(args) <= {'query', 'num'} and isinstance(args.get('query'), str):
                    count = args.get('num', 10)
                    if type(count) is not int or not 1 <= count <= 100:
                        return {'error': 'num must be an integer between 1 and 100'}
                    return gateway.search_request(args['query'], count)
                if op == 'fetch' and set(args) <= {'url', 'query'} and isinstance(args.get('url'), str):
                    return gateway.fetch_request(args['url'], args.get('query'))
                return {'error': 'only search(query,num?) and fetch(url,query?) are allowed'}
            except ProviderError as exc:
                failure = failure or 'web_provider_unavailable'
                return {'error': str(exc), 'upstream_status': exc.status}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        with Relay(a.socket, handle, max_calls=a.max_calls,
                   deadline_s=a.deadline_s, max_line_bytes=a.max_line_bytes) as relay:
            clean = stop.wait(a.deadline_s)
            with lock:
                Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({
                    **spec, 'failure': failure, 'search_provider': gateway.search,
                    'fetch_provider': gateway.fetch}))
            a.receipt.write_text(json.dumps({'transport': relay.receipt, 'stopped_cleanly': clean,
                'task_id': spec['task_id'], 'failure': failure}))
    finally:
        # The HTTP server was never started: only its vetted provider operations
        # are used behind the platform's private UNIX capability.
        gateway.server.server_close()
    return 0 if clean and not failure else 3


if __name__ == '__main__':
    raise SystemExit(main())
