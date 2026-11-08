"""Installed-distribution CostBench preparation entry point."""
from pathlib import Path
import runpy

def main(argv=None):
    file = Path(__file__).resolve().parents[1] / "data/datasets/costbench_dynamic/prepare.py"
    return runpy.run_path(str(file))["main"](argv)

if __name__ == "__main__":
    raise SystemExit(main())
