"""Isolated, credential-free GAIA Parquet decoder (JSON protocol on stdin/stdout)."""
import hashlib
import io
import json
from pathlib import Path
import sys

# -I removes checkout paths; add only this reviewed worker's repository owner.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from harnessgrad import dataset_runtime


def main():
    request = json.load(sys.stdin)
    dataset_runtime.admit_binding(request['binding'], 'service', python=sys.executable)
    import pyarrow.parquet as pq

    tables = []
    for item in request['inputs']:
        body = Path(item['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            raise ValueError('GAIA source digest mismatch')
        # The bytes checked above are precisely the bytes decoded below.
        tables.append(pq.ParquetFile(io.BytesIO(body)).read(use_threads=False).to_pylist())
    json.dump(tables, sys.stdout)


if __name__ == '__main__':
    main()
