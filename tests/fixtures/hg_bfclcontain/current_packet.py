"""Explicit versioned BFCL review packet; never regenerate while checking."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINAL_PACKET_PINS = {'source-inventory.json': '36d8e865bccc4f17dd5cbd93ab52157271fbe815e5dbb0353967534b6690ba8f', 'sinks.jsonl': 'fbd94e4b2c78c71204bde2525a057242e70dc45d11bd6a64a13c0a06b59d119a', 'boundary-review.jsonl': '71cf46e77cc1b074ee136f80855ce8fe826736477548b757f56e84801ae36ac8', 'cases.json': '22098f422503198c33a820af09b1b00d20fe095ab82e629c43c7edc6b0a34c84', 'observations.jsonl': 'a7e849b2a6d28ec8d49be7dcee0b7a63d628f97e34e32524f58c7f7735ddfa87'}
CURRENT_PACKET_SHA256 = '7df886a0af1e6cca9f95db33797d069ef4d21d8ce470cdbeb345495da6e10996'


def read_current_packet(directory=None):
    directory = HERE if directory is None else Path(directory)
    for name, expected in ORIGINAL_PACKET_PINS.items():
        try:
            raw = (directory / name).read_bytes()
        except FileNotFoundError as exc:
            raise ValueError('original BFCL packet missing: ' + name) from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('original BFCL packet identity changed: ' + name)
    try:
        raw = (directory / 'current-v2/packet.json').read_bytes()
    except FileNotFoundError as exc:
        raise ValueError('current BFCL packet missing; no automatic regeneration') from exc
    if hashlib.sha256(raw).hexdigest() != CURRENT_PACKET_SHA256:
        raise ValueError('current BFCL packet identity changed')
    packet = json.loads(raw)
    if packet['schema'] != 'harnessgrad.bfcl-source-review.v2' or packet['derived_from'] != ORIGINAL_PACKET_PINS:
        raise ValueError('BFCL packet origin relation changed')
    if packet['source_gate_satisfied'] is not False or packet['official_scorer_executed'] is not False:
        raise ValueError('source review cannot authorize an official scorer')
    return packet
