#!/usr/bin/env python3
"""Selected episode controller; actor operations cannot seal, reset or read gold."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets.memoryagentbench.lib.episode import Episode, ProtocolError
from data.datasets.memoryagentbench.lib.storage import validate_service
from eval.service.tool_transport import Relay


class Session:
    def __init__(self, payload):
        self.payload = validate_service(payload)
        self.episode = Episode(payload['episode_id'], payload['chunks'], payload['questions'])
        self.rejected = 0

    def handle(self, op, args):
        expected = {'next': set(), 'ack': {'chunk_id', 'request_id'},
                    'submit': {'question_id', 'answer', 'request_id'}}
        try:
            if op not in expected or set(args) != expected[op]:
                raise ProtocolError('invalid_operation', 'only next, ack and submit are allowed')
            if 'request_id' in args and (not isinstance(args['request_id'], str)
                                        or len(args['request_id'].encode()) > 256):
                raise ProtocolError('invalid_argument', 'request ID exceeds 256 UTF-8 bytes')
            if op == 'submit' and (not isinstance(args['answer'], str)
                                  or len(args['answer'].encode()) > 65536):
                raise ProtocolError('invalid_argument', 'answer exceeds 65536 UTF-8 bytes')
            return getattr(self.episode, op)(**args)
        except ProtocolError as exc:
            self.rejected += 1
            return {'accepted': False, 'error': exc.code, 'message': str(exc)}

    def record(self):
        return {'schema': 'hg-mab-service-seal-v1', 'task_id': self.payload['task_id'],
                'input_sha256': self.payload['input_sha256'], 'seal': self.episode.seal()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=4096)
    parser.add_argument('--deadline-s', type=float, default=14460)
    args = parser.parse_args()
    session = Session(json.loads(args.episode.read_text()))
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    with Relay(args.socket, session.handle, max_calls=args.max_calls,
               deadline_s=args.deadline_s) as relay:
        clean = done.wait(args.deadline_s)
    Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps(session.record(), ensure_ascii=False))
    args.receipt.write_text(json.dumps({'schema': 'hg-mab-relay-receipt-v1',
        'task_id': session.payload['task_id'], 'protocol_rejections': session.rejected,
        'stopped_cleanly': clean, 'transport': relay.receipt}))
    return 0 if clean and not (relay.receipt['errors'] or relay.receipt['refused']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
