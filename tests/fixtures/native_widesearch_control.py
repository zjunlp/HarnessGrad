"""Execute authored native controls with stdlib assertions in a managed artifact.

This is a test runner, not a replacement evaluator. It imports the original
control functions and their real pinned native scoring/CSV implementation.
No pytest package is installed into, or imported from outside, the artifact.
"""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import types
from unittest.mock import patch


class Raises:
    def __init__(self, expected, match=None):
        self.expected, self.pattern = expected, match

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if kind is None:
            raise AssertionError('expected exception was not raised')
        if not issubclass(kind, self.expected):
            return False
        if self.pattern is not None and not re.search(self.pattern, str(error)):
            raise AssertionError('exception message did not match expected control') from error
        return True


class Marks:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: lambda function: function


class Monkeypatch:
    def __init__(self, stack):
        self.stack = stack

    def setattr(self, target, name, value):
        self.stack.enter_context(patch.object(target, name, value))

    def setenv(self, name, value):
        self.stack.enter_context(patch.dict(os.environ, {name: value}))


def fail(message):
    raise AssertionError(message)


def main():
    root = Path(sys.argv[1])
    sys.path.insert(0, str(root))
    from harnessgrad.dataset_runtime import admit_binding
    request = json.load(sys.stdin)
    admit_binding(request['runtime'], 'service', python=sys.executable)
    pytest = types.ModuleType('pytest')
    pytest.mark, pytest.raises, pytest.fail = Marks(), Raises, fail
    sys.modules['pytest'] = pytest
    spec = importlib.util.spec_from_file_location('managed_wide_controls',
        root / 'tests/quality/test_native_widesearch.py')
    controls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controls)
    # Already inside the admitted conversion component: call the same conversion
    # implementation directly, retaining mock Hub/evaluator hooks in this process.
    from tools.prepare_native_research import _prepare
    controls.prepare = _prepare
    function = getattr(controls, request['function']).__wrapped__
    arguments = request['arguments']
    if 'tmp_path' in arguments:
        arguments['tmp_path'] = Path(arguments['tmp_path'])
    with ExitStack() as stack:
        import inspect
        if 'monkeypatch' in inspect.signature(function).parameters:
            arguments['monkeypatch'] = Monkeypatch(stack)
        function(**arguments)


if __name__ == '__main__':
    main()
