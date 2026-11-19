"""Project the hash-verified ScienceWorld publication into website data."""
import argparse
import json
from pathlib import Path
import re

def project(family_dir, revision):
    family = json.loads((family_dir/'family.json').read_text())
    summary = json.loads((family_dir/'coverage.json').read_text())
    methods = []
    for item in summary:
        method = item['method']
        excluded = {r['round'] for r in item['excluded_train_rounds']}
        rows = [json.loads(s) for s in (family_dir/method/'curve.jsonl').read_text().splitlines() if s.strip()]
        if [row['round'] for row in rows] != list(range(21)):
            raise ValueError(f'unexpected training round inventory for {method}')
        methods.append({
            'id': method,
            'devRound': item['selected_round'],
            'devScore': item['dev_score'],
            'testScore': item['test_score'],
            'train': [row['score'] if row['round'] not in excluded else None for row in rows],
        })
    if {m['id'] for m in methods} != set(family['methods']):
        raise ValueError('method roster mismatch')
    return {'revision': revision, 'baseline': family['shared_baseline'],
            'taskAgent': family['task_agent'], 'metaAgent': family['meta_agent'], 'methods': methods}

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('family_dir',type=Path)
    p.add_argument('verified_receipt',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    receipt=args.verified_receipt.read_text().splitlines()
    match=re.fullmatch(r'https://huggingface.co/datasets/Xubqpanda/HarnessGrad/commit/([a-f0-9]{40})',receipt[0])
    if len(receipt)!=2 or not match or receipt[1]!='1412 files verified by size and hash':
        raise ValueError('Expected a complete, hash-verified ScienceWorld publication receipt')
    args.output.write_text(json.dumps(project(args.family_dir,match[1]),ensure_ascii=False,indent=2)+'\n')
