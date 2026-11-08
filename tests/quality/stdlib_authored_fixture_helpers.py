"""Import old QA adapters against a test-owned root, never ambient dataset storage."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

from harnessgrad import paths

ROOT = Path(__file__).resolve().parents[2]


def adapter_at(name, root):
    spec = importlib.util.spec_from_file_location(
        'authored_test_' + name, ROOT / 'data/datasets' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    # The adapter captures this root at import. Its real loader still runs; an
    # absent fixture may populate QASpec's missing-payload cache, which callers
    # clear after writing their authored bytes.
    with patch.object(paths, 'dataset_root', return_value=Path(root)):
        spec.loader.exec_module(module)
    return module
