"""Faithful explicit BBEH whole-group exclusion, with full source custody."""
import hashlib
import json

import pytest

from data.scaffolds.native_qa import Dataset, bbeh_selection
from data.scaffolds.native_qa.prepare import prepare


def source(tmp_path):
    # The duplicate group includes conflicting answers; no one label may win.
    rows = [
        {'task': 'language', 'input': 'Repeated question', 'target': 'PRIVATE_A'},
        {'task': 'language', 'input': 'Repeated question', 'target': 'PRIVATE_B'},
        {'task': 'language', 'input': 'Repeated question', 'target': 'PRIVATE_A'},
        {'task': 'space', 'input': 'Also repeated', 'target': 'PRIVATE_C'},
        {'task': 'space', 'input': 'Also repeated', 'target': 'PRIVATE_C'},
        {'task': 'unique', 'input': 'Keep this question', 'target': 'PRIVATE_D'},
        {'task': 'unique', 'input': 'Keep a second question', 'target': 'PRIVATE_E'},
    ]
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(rows))
    return path, rows


def test_default_refuses_and_explicit_exclude_removes_entire_conflicting_groups(tmp_path):
    path, rows = source(tmp_path)
    default = tmp_path / 'default'
    prepare('bbeh', default, ['test=' + str(path)])
    with pytest.raises(ValueError, match='duplicate native'):
        Dataset('bbeh', default).load()
    filtered = tmp_path / 'filtered'
    manifest = prepare('bbeh', filtered, ['test=' + str(path)], duplicate_questions='exclude')
    source_file = filtered / manifest['files'][0]['path']
    raw = source_file.read_bytes()
    strict_manifest = json.loads((default / 'source-manifest.json').read_text())
    assert manifest['files'] == strict_manifest['files']
    assert raw == (default / strict_manifest['files'][0]['path']).read_bytes()
    assert manifest['files'][0]['rows'] == manifest['split_rows']['test'] == 7
    assert len(raw.splitlines()) == 7
    assert manifest['files'][0]['sha256'] == hashlib.sha256(raw).hexdigest()
    assert [json.loads(line) for line in raw.splitlines()] == rows
    tasks, _ = Dataset('bbeh', filtered).load()
    assert len(tasks) == 2
    assert {t['goal'].split('\n\n')[0] for t in tasks} == {'Keep this question', 'Keep a second question'}
    receipt = manifest['selection']
    assert receipt['duplicate_questions'] == 'exclude'
    assert (receipt['source_row_count'], receipt['excluded_duplicate_rows'], receipt['retained_row_count']) == (7, 5, 2)
    assert len(receipt['excluded_question_ids']) == 2
    assert all(len(value) == 64 for value in receipt['excluded_question_ids'])
    assert 'PRIVATE_' not in json.dumps(receipt)


@pytest.mark.parametrize('mutation', ['omit-group', 'extra-group', 'source-count', 'excluded-count',
                                    'retained-count', 'boolean-count', 'bad-policy', 'extra-field'])
def test_exclusion_receipt_is_recomputed_from_full_digest_bound_source(tmp_path, mutation):
    path, _ = source(tmp_path)
    root = tmp_path / 'prepared'
    manifest = prepare('bbeh', root, ['test=' + str(path)], duplicate_questions='exclude')
    receipt = manifest['selection']
    if mutation == 'omit-group': receipt['excluded_question_ids'].pop()
    elif mutation == 'extra-group': receipt['excluded_question_ids'].append('0' * 64)
    elif mutation == 'source-count': receipt['source_row_count'] -= 1
    elif mutation == 'excluded-count': receipt['excluded_duplicate_rows'] -= 1
    elif mutation == 'retained-count': receipt['retained_row_count'] += 1
    elif mutation == 'boolean-count': receipt['source_row_count'] = True
    elif mutation == 'bad-policy': receipt['duplicate_questions'] = 'first'
    elif mutation == 'extra-field': receipt['excluded_answers'] = ['PRIVATE_A']
    (root / 'source-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='BBEH'):
        Dataset('bbeh', root).load()


def test_filtered_data_bytes_cannot_replace_the_complete_source(tmp_path):
    path, _ = source(tmp_path)
    root = tmp_path / 'prepared'
    manifest = prepare('bbeh', root, ['test=' + str(path)], duplicate_questions='exclude')
    row_file = root / manifest['files'][0]['path']
    row_file.write_bytes(row_file.read_bytes().splitlines(keepends=True)[-1])
    with pytest.raises(ValueError, match='checksum mismatch'):
        Dataset('bbeh', root).load()


def test_exclude_policy_is_bbeh_only_and_order_independent(tmp_path):
    with pytest.raises(ValueError, match='only for BBEH'):
        prepare('searchqa', tmp_path / 'uncreated', duplicate_questions='exclude')
    assert not (tmp_path / 'uncreated').exists()
    ids = ['a'*64, 'a'*64, 'b'*64, 'c'*64]
    assert bbeh_selection(ids, 'exclude') == bbeh_selection(list(reversed(ids)), 'exclude')


def test_legacy_manifest_without_selection_retains_the_strict_default(tmp_path):
    path, _ = source(tmp_path)
    root = tmp_path / 'legacy'
    manifest = prepare('bbeh', root, ['test=' + str(path)])
    manifest.pop('selection')
    (root / 'source-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='duplicate native'):
        Dataset('bbeh', root).load()
