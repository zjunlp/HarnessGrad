"""Stale inventory counts must not hide missing subprocess decoding policies."""
from __future__ import annotations

from test_harness_decoding import EXPECTED_CALLS, STRICT_IDENTITY_SITES, _decoding_guard_findings, _decoding_problem


def test_guard_reports_both_inventory_drift_and_unreviewed_sites():
    counts = {**EXPECTED_CALLS, 'tools': EXPECTED_CALLS['tools'] + 1}
    findings = _decoding_guard_findings(counts, ['tools/example.py:42'])
    assert len(findings) == 2
    assert 'inventory changed' in findings[0]
    assert 'tools/example.py:42' in findings[1]


def test_guard_still_fails_for_either_kind_of_finding():
    assert _decoding_guard_findings(EXPECTED_CALLS, ['tools/example.py:42'])
    assert _decoding_guard_findings({}, [])
    assert _decoding_guard_findings(EXPECTED_CALLS, []) == []


def test_guard_distinguishes_reviewed_identity_from_unreviewed_modes():
    site = next(iter(STRICT_IDENTITY_SITES))
    assert _decoding_problem(site, 'strict') is None
    assert _decoding_problem('tools/unknown.py:1', 'strict')
    assert _decoding_problem('tools/unknown.py:1', 'ignore')
    assert _decoding_problem('tools/unknown.py:1', '<dynamic>')
    assert _decoding_problem('tools/unknown.py:1', None)
    assert _decoding_problem('tools/unknown.py:1', 'replace') is None
    assert _decoding_problem('tools/unknown.py:1', 'surrogateescape') is None


def test_scanner_extracts_literal_and_dynamic_error_modes(tmp_path, monkeypatch):
    import test_harness_decoding as guard
    monkeypatch.setattr(guard, 'ROOT', tmp_path)
    source = tmp_path / 'sample.py'
    source.write_text("import subprocess\n"
                      "subprocess.run([], text=True, errors='ignore')\n"
                      "subprocess.run([], text=True, errors=policy)\n"
                      "subprocess.run([], text=True)\n")
    assert guard._subprocess_text_modes(source) == [(2, 'ignore'), (3, '<dynamic>'), (4, None)]
