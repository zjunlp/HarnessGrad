"""Offline evidence verification, metadata comparison and tightly scoped diff audit.

--write records derived observations and hashes after review; --check verifies
those records. Neither mode imports an adapter, opens payloads or makes requests.
No output of this verifier is a custody record or approval digest.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = '13fa17c451eb0cb44adcf5126fa25ccd345c47c8'
B6 = 'reports/b6-blocker-evidence/'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_bytes())


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


PINS = {
    'docs/evidence/lcb740/prior-selection.inert.json': '683d0a0e689b4579ffd8f61a3e672299c8e944014efcc9cffa75e99af3512d38',
    'docs/evidence/lcb740/prior-launch.json': 'b2402fc26b4ea0c675c7880ffd72e5272793df084ee9471aebcb06022ed8e168',
    'docs/task-207-livecodebench-operator-packet.md': '5b490cb33868811e623606c6c35c6c7f695dd5bf39ea2f382b5baa0e1049927f',
    B6 + 'twowiki-hub-proxy-revision/hop-0.body': '6340521cf170fac8e82785cd21a5e9941306ea6ee54963438b6555561b09d21a',
    B6 + 'twowiki-hub-proxy-tree/hop-0.body': '0726213cab74f08258be4080918684821a5b3c313f51043a089d79ddcc6947eb',
    B6 + 'lcb-hub-proxy-revision/hop-0.body': '501d60839cc3f592b476d5d79b1093f29ade0144fb3318f1b261d3ece274e4d4',
    B6 + 'lcb-hub-proxy-tree/hop-0.body': '91b24116907e1c5e5ed962fa90afe8583a36e97148e0ef6ed8369df21b27dee8',
    B6 + 'twowiki-upstream-readme/README.upstream.md': 'd1831fa4b1a8fdcc1729565e30e38b3d8b955534486eb690fd5ea54c1232955f',
    B6 + 'twowiki-upstream-current-readme/hop-0.body': 'bb0cf42315c4e65ccf8fda8e5a1a76fd76bf81552763b9bbd6230bc3077ddba0',
    B6 + 'twowiki-hub-proxy-loader/hop-1.body': 'fab06a1a2d4731e1aefb415252f1613d93754bd94ca0b2ba1f6c268af93711c4',
    B6 + 'lcb-hub-proxy-builder/hop-1.body': '973e37989fed0f069c8246776e918d0d96600d64a3730cd6242dd76bccf2f7bb',
    B6 + 'preserved-receipts/twowiki-imposed-split.json': '3236a75876d8afea722255cb8d12c32aba64b327c19f15234af1c2325923c18f',
    B6 + 'preserved-receipts/twowiki-original-run-meta.json': 'c381baf1514ba903bd515bbe44092ae4cc0a96be34c5bb672e096557491bbda0',
    B6 + 'preserved-receipts/twowiki-recovery-run-meta.json': 'f0c91f731006e4b760916c481262873a0808e36a1ec3b8e714b73026ed9a27a4',
    B6 + 'preserved-receipts/twowiki-original-curve.jsonl': '14790c0c322981ad2e8497d368bee29de971526e4499a22a2e77be05b60eff0f',
    B6 + 'preserved-receipts/twowiki-recovery-curve.jsonl': '0f27ad9146371b2e9e318d0cce8b4f3769eeb33e209948e780c5a3a3ec034f20',
    'tests/fixtures/twowikimultihopqa_upstream_codex197/LICENSE': 'b8c1d7752e71597a45121774b6be93df38a81e1b748be15c063c0759f11e00ea',
}


def derive():
    inherited = []
    for path, expected in PINS.items():
        raw = (ROOT / path).read_bytes()
        assert digest(raw) == expected, path
        inherited.append(dict(path=path, sha256=expected, bytes=len(raw)))
    responses, failures = [], []
    for receipt_path in sorted(HERE.glob('*/receipt.json')):
        for hop, receipt in enumerate(json.loads(receipt_path.read_bytes())):
            if 'status' not in receipt:
                assert 'error' in receipt and 'sha256' not in receipt
                failures.append(dict(acquisition=receipt_path.parent.name, **receipt))
                continue
            body_path = receipt_path.parent / f'hop-{hop}.body'
            headers_path = receipt_path.parent / f'hop-{hop}.headers'
            body = body_path.read_bytes()
            assert digest(body) == receipt['sha256'] and len(body) == receipt['bytes']
            assert digest(headers_path.read_bytes()) == receipt['headers_sha256']
            assert receipt['complete'] is True
            if receipt['method'] == 'HEAD':
                assert body == b''
            responses.append(dict(acquisition=receipt_path.parent.name,
                                  body_path=str(body_path.relative_to(ROOT)), **receipt))
    lcb_meta = read(B6 + 'lcb-hub-proxy-revision/hop-0.body')
    card = (HERE / 'lcb-pinned-card/hop-1.body').read_bytes()
    assert git_blob(card) == next(x['blobId'] for x in lcb_meta['siblings'] if x['rfilename'] == 'README.md')
    lcb_history = json.loads((HERE / 'lcb-pinned-history/hop-0.body').read_bytes())
    assert lcb_history[0]['id'] == lcb_meta['sha']
    assert {x['id'] for x in lcb_history if x['title'] == 'add v6'} == {
        '819a13a5c0347c1bd2f5600a35bc9ac9695d461b', '503416f80ac447d31f252d8434023786ce45dd0c'}
    refs = json.loads((HERE / 'lcb-refs/hop-0.body').read_bytes())
    assert refs['tags'] == [] and refs['branches'][0]['targetCommit'] == lcb_meta['sha']
    inert = read('docs/evidence/lcb740/prior-selection.inert.json')
    launch = read('docs/evidence/lcb740/prior-launch.json')
    packet = (ROOT / 'docs/task-207-livecodebench-operator-packet.md').read_text()
    # This parses the existing unissued template as a target, never as an attestation.
    target = json.loads(packet.split('```json\n', 1)[1].split('```', 1)[0])
    selected, = inert['selected_reference_records']
    mapping = dict(task_id=selected['task_id'], question_id=selected['question_id'],
                   row_index=int(selected['task_id'].rsplit('/', 1)[1]), side=selected['side'],
                   revision=inert['source']['dataset_revision'],
                   pool_sha256=inert['source']['declared_pool_sha256'],
                   split_sha256=inert['source']['inferred_original_full_split_sha256'],
                   public_statement_sha256=launch['public_statement_sha256'])
    for key, value in mapping.items():
        assert target[key] == value
    assert mapping['revision'] == lcb_meta['sha']
    assert selected['mapping_status'] == 'FILENAME_INFERRED_NOT_POOL_ATTESTED'
    assert inert['active'] is inert['execution_authorized'] is inert['source']['pool_hash_freshly_verified'] is False
    assert inert['source']['metadata_attestation_required'] is True
    assert hashlib.sha256(mapping['task_id'].encode()).digest()[0] % 3 != 0
    ranked = sorted(inert['selection']['eligible_pool_task_ids'],
                    key=lambda task: (digest(('livecodebench\0' + task).encode()), task))
    assert ranked[:1] == inert['selection']['selected_task_ids'] == [mapping['task_id']]
    tw_meta = read(B6 + 'twowiki-hub-proxy-revision/hop-0.body')
    tw_history = json.loads((HERE / 'twowiki-pinned-history/hop-0.body').read_bytes())
    assert tw_history[0]['id'] == tw_meta['sha'] and len(tw_history) == 2
    assert tw_history[0]['title'] == 'Upload folder using huggingface_hub'
    for name in ('twowiki-releases', 'twowiki-tags'):
        assert json.loads((HERE / name / 'hop-0.body').read_bytes()) == []
    issues = json.loads((HERE / 'twowiki-license-issues/hop-0.body').read_bytes())
    assert issues['total_count'] == 0 and issues['incomplete_results'] is False
    artifacts = {x['rfilename']: x['lfs']['sha256'] for x in tw_meta['siblings'] if 'lfs' in x}
    assert artifacts['data/dev.json'] == '79f77ae104088ea8e25b1a65dbece768d45771194663bc5660ec9a98070dadf5'
    assert artifacts['data/id_aliases.json'] == 'f08ffcb6c2cefca9bdbe86b4248d6ad7a7743762d3f7264c14ff0bae85726fb6'
    split = read(B6 + 'preserved-receipts/twowiki-imposed-split.json')
    canonical = json.dumps({side: sorted(split[side]) for side in ('train', 'eval')}, sort_keys=True).encode()
    split_hash = digest(canonical)
    assert split_hash == '161b79332301a152d9ff53ffff073b2f9b307054bc510764068fb48a89bc17b4'
    runs = {}
    for kind in ('original', 'recovery'):
        meta = read(B6 + f'preserved-receipts/twowiki-{kind}-run-meta.json')
        # Only metadata fields below are compared; no scores are reviewed/recomputed.
        curve = read(B6 + f'preserved-receipts/twowiki-{kind}-curve.jsonl')
        ids = curve['sampling']['task_ids']
        assert curve['run_id'] == meta['run_id']
        assert curve['split'] == meta['split'] == split and meta['split_sha'] == split_hash
        assert set(ids) <= set(split['train']) and len(ids) == len(set(ids))
        runs[kind] = dict(run_id=meta['run_id'], selected_count=len(ids), selected_task_ids=ids,
                          selected_ids_sha256_json_compact=digest(json.dumps(ids, separators=(',', ':')).encode()),
                          split_sha256=split_hash, raw_artifact_binding_authenticated=False)
    return dict(schema_version=1, inherited_evidence=inherited, responses=responses,
                failures=failures, livecodebench=dict(
                    compared_declarations=mapping, task207_target_matches_inert_declarations=True,
                    inert_sha256=PINS['docs/evidence/lcb740/prior-selection.inert.json'],
                    rank_selection_matches=True, local_hash_train_rule_matches=True,
                    complete_catalog_acquired=False, authenticated_row_record_acquired=False,
                    independent_approval_received=False, row_attested=False,
                    pool_freshly_verified_here=False), twowikimultihopqa=dict(
                    hub_revision=tw_meta['sha'], hub_advertised_artifacts=artifacts,
                    historical_selections=runs, original_release_digest_custody_acquired=False,
                    dataset_alias_rights_attested=False, alias_id_coverage_attested=False,
                    selected_manifest_linkage_authenticated=False),
                production_authorized=False, benchmark_execution=False, payload_access=False)


def scope():
    baseline = json.loads(subprocess.check_output(['git', 'show', BASE + ':registry/dataset-blockers.json'], cwd=ROOT))
    current = read('registry/dataset-blockers.json')
    owned = {('livecodebench', 'row-attestation'), ('twowikimultihopqa', 'hub-data-provenance')}
    for old_dataset, new_dataset in zip(baseline['datasets'], current['datasets'], strict=True):
        assert old_dataset['dataset'] == new_dataset['dataset']
        for old, new in zip(old_dataset['blockers'], new_dataset['blockers'], strict=True):
            if (new_dataset['dataset'], new['id']) in owned:
                assert new['status'] == old['status'] == 'external'
                assert new['next_action'] == old['next_action']
                # Only observations and appended evidence are authorized registry edits.
                assert new['evidence'][:len(old['evidence'])] == old['evidence']
                old['observed'] = new['observed']
                old['evidence'] = new['evidence']
    assert baseline == current, 'unowned registry change'
    changed = subprocess.check_output(['git', 'diff', '--name-only', BASE], cwd=ROOT, text=True).splitlines()
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    for name in changed + untracked:
        assert name.startswith(('docs/', 'reports/', 'tests/fixtures/upstream_provenance_e2/')), name
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    assert args.write != args.check
    derived = derive()
    result = HERE / 'comparison.json'
    sums = HERE / 'SHA256SUMS'
    if args.write:
        result.write_text(json.dumps(derived, indent=2) + '\n')
        sums.write_text(''.join(f'{digest(p.read_bytes())}  {p.relative_to(HERE)}\n'
                               for p in sorted(HERE.rglob('*')) if p.is_file() and p != sums))
    else:
        assert json.loads(result.read_bytes()) == derived
        listed = set()
        for line in sums.read_text().splitlines():
            expected, name = line.split('  ', 1)
            assert digest((HERE / name).read_bytes()) == expected, name
            listed.add(name)
        assert listed == {str(p.relative_to(HERE)) for p in HERE.rglob('*') if p.is_file() and p != sums}
    assert scope()
    print(json.dumps(dict(valid=True, response_hops=len(derived['responses']),
                          preserved_refusals_or_failures=len(derived['failures']),
                          inherited_files_verified=len(PINS), registry_scope_valid=True,
                          closed_blockers=0, independent_approval=False, production_authorized=False)))


if __name__ == '__main__':
    main()
