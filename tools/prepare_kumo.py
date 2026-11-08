"""Run KUMO's pinned preparation from a checkout or installed distribution."""
from pathlib import Path
import runpy


def main(argv=None):
    from data.datasets import kumo
    module = runpy.run_path(str(Path(kumo.ASSETS) / 'prepare.py'))
    return module['main'](argv)


if __name__ == '__main__':
    raise SystemExit(main())
