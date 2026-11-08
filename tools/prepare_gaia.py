"""Prepare or inspect GAIA data from an installed distribution."""
from pathlib import Path
import runpy
import sys


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = runpy.run_path(str(root / 'data/datasets/gaia/prepare.py'))
    return module['cli_main'](argv)


if __name__ == '__main__':
    raise SystemExit(main())
