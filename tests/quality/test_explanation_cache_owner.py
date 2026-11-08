"""One explicit explanation cache owns memory/recency/lock/disk for each app."""
from console.explanation import ExplainCache


def test_cache_deep_copies_reads_and_new_owner_restores_disk(tmp_path):
    cache = ExplainCache()
    payload = {'evidence': ['one', 'two'], 'claim': 'fixture'}
    cache.put('key', payload, directory=tmp_path)
    read = cache.get('key', directory=tmp_path)
    read['evidence'].append('caller mutation')
    assert cache.get('key', directory=tmp_path)['evidence'] == ['one', 'two']
    other = ExplainCache()
    assert other.memory == {} and other.get('key', directory=tmp_path) == payload
    assert other.memory is not cache.memory and other.lock is not cache.lock


def test_missing_corrupt_and_wrong_key_disks_do_not_create_state(tmp_path):
    directory = tmp_path / 'missing'
    cache = ExplainCache()
    assert cache.get('key', directory=directory) is None and not directory.exists()
    directory.mkdir()
    path = cache.path('key', directory=directory)
    for content in ('not json', '[]', '{"key":"other","response":{}}', '{"key":"key","response":[]}'):
        path.write_text(content)
        assert cache.get('key', directory=directory) is None
    assert cache.memory == {} and cache.order == []


def test_recency_is_bounded_in_one_owner():
    cache = ExplainCache()
    for n in range(130):
        cache.remember(str(n), {'value': n})
    assert len(cache.memory) == len(cache.order) == 128
    assert '0' not in cache.memory and '1' not in cache.memory
    cache.remember('2', {'value': 'updated'})
    assert cache.order[-1] == '2' and len(cache.order) == 128
