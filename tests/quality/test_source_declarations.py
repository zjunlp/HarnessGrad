"""Source declaration parsing never imports or executes the inspected code."""
import pytest

from harnessgrad.provenance.source_declarations import literal_assignments


def test_literals_are_read_without_executing_other_statements(tmp_path):
    marker = tmp_path / 'must-not-exist'
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\nA = 'value'\nB = 7\n"
    assert literal_assignments(source, {'A', 'B'}) == {'A': 'value', 'B': 7}
    assert not marker.exists()


def test_chained_declarations_require_explicit_permission():
    source = "A = B = 'same'\n"
    with pytest.raises(ValueError, match='missing'):
        literal_assignments(source, {'A', 'B'})
    assert literal_assignments(source, {'A', 'B'}, allow_chained=True) == {'A': 'same', 'B': 'same'}


@pytest.mark.parametrize('source', ["A = 1\nA = 2\n", "A = A = 1\n"])
def test_duplicate_declarations_are_refused(source):
    with pytest.raises(ValueError, match='duplicate'):
        literal_assignments(source, {'A'}, allow_chained=True)


def test_nonliteral_expression_is_refused():
    with pytest.raises(ValueError):
        literal_assignments("A = dangerous()\n", {'A'})
