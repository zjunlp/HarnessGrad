"""Prepare tau3_bench through its source-only managed controller."""
from pathlib import Path
import hashlib
import types


def main():
    path = Path(__file__).resolve().with_name('_tau_prepare.py')
    raw = path.read_bytes()
    module = types.ModuleType('_tau_prepare_entry')
    module.__file__ = str(path)
    module.__dict__['__executed_source_sha256__'] = hashlib.sha256(raw).hexdigest()
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module.load('tau3_bench').main()


if __name__ == '__main__':
    raise SystemExit(main())
