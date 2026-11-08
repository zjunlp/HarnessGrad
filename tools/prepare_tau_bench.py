"""Import historical pinned tau-bench assets into an external prepared root."""
from __future__ import annotations

import runpy
from pathlib import Path


def main():
    from data import datasets
    directory = Path(next(iter(datasets.__path__))) / 'tau_bench'
    runpy.run_path(str(directory / 'prepared.py'), run_name='__main__')


if __name__ == '__main__':
    main()
