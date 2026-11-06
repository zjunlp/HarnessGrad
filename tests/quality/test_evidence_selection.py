"""Cross-reference checks for selection and baseline citations."""
import pytest

from harnessgrad import evidence
from harnessgrad.evidence_export import build_export
from tests.quality.test_evidence_export import author, source
from tests.quality.test_evidence_manifest import pack


def test_reported_run_cannot_also_be_rejected():
    document = pack()
    document['selection']['rejected'] = [{'run_id': 'sub', 'why': 'rejected'}]
    problems, _ = evidence.check_manifest(document)
    assert any('rejects reported run sub' in problem for problem in problems)


def test_baseline_must_be_cited_by_request():
    document = pack()
    document['requests'][0]['runs'] = ['sub']
    problems, _ = evidence.check_manifest(document)
    assert any('baseline run base is not among its cited runs' in problem for problem in problems)


@pytest.mark.parametrize('change', [
    lambda doc: doc['selection'].update(rejected=[{'run_id': 'sub', 'why': 'rejected'}]),
    lambda doc: doc['requests'][0].update(runs=['sub']),
])
def test_export_refuses_contradictory_author_draft(source, change):
    document = author()
    change(document)
    with pytest.raises(evidence.EvidenceError, match='manifest_inconsistent'):
        build_export(document, source)


def test_distinct_rejected_run_and_cited_baseline_remain_valid():
    document = pack()
    document['selection']['rejected'] = [{'run_id': 'base', 'why': 'baseline only'}]
    assert evidence.check_manifest(document)[0] == []
