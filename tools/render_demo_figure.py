#!/usr/bin/env python3
"""Render the checked-in mock-demo receipt; matplotlib is an optional docs tool.

This never launches a task, fetches data or turns a mock curve into a benchmark
result. The JSON source records actual demo-run hashes, counts and observations.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.github/assets/demo-curve.json'))
    parser.add_argument('--output', type=Path, default=Path('.github/assets/demo-curve.svg'))
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    if source.get('schema') != 'harnessgrad.offline-demo-figure.v1' or not source.get('mock_only'):
        parser.error('this renderer only accepts the explicitly labeled offline demo receipt')
    try:
        import matplotlib
    except ImportError:
        raise SystemExit('Optional figure regeneration requires: python3 -m pip install matplotlib') from None
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with plt.rc_context({'font.family': 'DejaVu Sans', 'svg.fonttype': 'none', 'font.size': 10}):
        figure, axis = plt.subplots(figsize=(8.4, 3.6), layout='constrained')
        for run, color in zip(source['runs'], ('#2563eb', '#64748b')):
            if run['dataset'] != 'demo' or run['agent_backend'] != 'mock' or run['side'] != 'train':
                parser.error('the figure must contain only mock demo training observations')
            points = run['points']
            axis.plot([p['round'] for p in points], [p['score'] for p in points],
                      marker='o', linewidth=2, label=run['label'], color=color)
        axis.set(xlabel='Platform measurement round', ylabel='Demo training score', ylim=(-.07, 1.12), xticks=[0, 1, 2])
        axis.grid(axis='y', alpha=.2)
        axis.spines[['right', 'top']].set_visible(False)
        axis.legend(loc='center right', frameon=False)
        figure.suptitle('Offline pipeline check: actual recorded mock runs', fontweight='bold')
        counts = sorted({run['task_count'] for run in source['runs']})
        axis.set_title(f'{"/".join(map(str, counts))} study tasks from demo; scripted edits; no benchmark claim',
                       fontsize=9, color='#475569')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.output, metadata={'Date': None, 'Creator': 'tools/render_demo_figure.py'})
        plt.close(figure)
    if args.output.suffix.lower() == '.svg':
        # Matplotlib's path attributes contain insignificant line-end spaces.
        # Normalize this generated asset so review whitespace checks stay useful.
        args.output.write_text('\n'.join(line.rstrip() for line in args.output.read_text().splitlines()) + '\n')
    print(args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
