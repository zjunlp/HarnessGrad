"""Trusted native source conversion entry point, never an actor interface."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from harnessgrad.dataset_runtime import admit_binding
from tools.prepare_native_research import _prepare


def main():
    request = json.load(sys.stdin)
    selected = request.pop('runtime')
    admit_binding(selected, 'service', python=sys.executable)
    _prepare(**request, preparation_runtime=selected)


if __name__ == '__main__':
    main()
