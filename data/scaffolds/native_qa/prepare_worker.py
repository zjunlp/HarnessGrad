"""Private conversion process; invoked only through the admitted runtime."""
from pathlib import Path
import json
import sys

# -I removes the checkout from import search; this reviewed script adds its owner.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.scaffolds.native_qa.prepare import _read_native, native_sources


def main():
    request = json.load(sys.stdin)
    root = Path(request['root'])
    sources = ([(v.partition('=')[0], _read_native(v.partition('=')[2]))
                for v in request['local']] if request['local'] else
               native_sources(request['dataset'], root))
    index = []
    for number, (split, rows) in enumerate(sources):
        relative = f'converted/{number:02d}.jsonl'
        target = root / relative
        target.parent.mkdir(exist_ok=True)
        with target.open('w', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        index.append({'split': split, 'path': relative})
    (root / 'conversion.json').write_text(json.dumps(index), encoding='utf-8')


if __name__ == '__main__':
    main()
