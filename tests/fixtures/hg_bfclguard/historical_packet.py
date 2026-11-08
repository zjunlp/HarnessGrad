"""Retained historical graph integrity, distinct from current live analysis."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORICAL_PINS = {'sources.jsonl': '2cae0c5961866cb2b6434eddf868d2822f0eca861bc3432882e6ca4e11152141', 'edges.jsonl': 'd45fb8f4c33a1ff6414e82be7d4bbab510d1f2f306356d9df068da54884a4573', 'closures.jsonl': '73d0738c21cc32536b067c72866cb9baa0379ca6c8ed5238a9af4089904d761c', 'mechanisms.jsonl': '713fc5898c1b4a8c90a742244de41a9caad8238ca604355bfe02a6bfa4687f23', 'references.jsonl': 'fd1f50950c56a920d98151c96c6f5a2d3db8fb287bf801d3520626cd16ea5521', 'summary.json': 'c0c43f31a16d02305e1ad27a0c84a0a61247cfd7b1c1e8eaa32fd885c0eed8a9', 'dynamic-observation.json': 'eac0d72592c58b28e933bb1e5ebd62a5d0793a7e611a69013e8b5672edabc1fc', 'gate-observations.jsonl': 'bce37bc603bc71cc34333ba1e38ee3b61933dd1d8e38f668c9814e5fcc2a3730', 'gate.py': 'fbb7dbd1dbf71aec5f694f3659da80197d7f1b024026aa897c95017c09294059', 'history-v1/reachability.py.txt': '6859c79f1d26351e580b0151799ca47e33827e7cbbff1689f9ec6b43fca7133c', 'history-v1/controls.py.txt': '2c5b30f36e3745ec24ec8ba29081bcad8b845e58665f8d88f6e268d568d2c44c'}


def check_historical_packet(directory=None):
    directory = HERE if directory is None else Path(directory)
    for name, expected in HISTORICAL_PINS.items():
        try:
            raw = (directory / name).read_bytes()
        except FileNotFoundError as exc:
            raise ValueError('historical graph packet missing: ' + name) from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('historical graph packet identity changed: ' + name)
    return {'scope': 'retained historical bytes only; no current graph or whole-program claim',
            'generation_source_commit': None, 'retention_snapshot': '4c235a22d6d978c8d30b13cfcb2f98384fd4da58',
            'files_verified': len(HISTORICAL_PINS)}
