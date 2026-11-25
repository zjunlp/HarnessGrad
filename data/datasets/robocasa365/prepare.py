#!/usr/bin/env python3
"""Verify and extract the pinned RoboCasa365 simulator and its assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from zipfile import ZipFile

ASSETS = Path(__file__).resolve().parent
sys.path[:0] = [str(ASSETS), str(ASSETS.parents[2])]

from bundle import ARCHIVES, BUNDLE_DIR, RECEIPT, SOURCE_FILES, file_sha256, identify, prepared_receipt
from harnessgrad.paths import dataset_root


def extract_source(archive, dest, package):
    with tarfile.open(archive) as tar:
        for member in tar:
            parts = Path(member.name).parts
            if len(parts) > 1 and parts[1] in (package, 'LICENSE'):
                member.name = '/'.join(parts[1:])
                tar.extract(member, dest, filter='data')


def make_readonly_compatible(path):
    """Move generated object XML to private tmp while preserving asset paths."""
    source = path.read_text()
    start = source.index('        # write modified xml (and make sure')
    end = source.index('        self._regions = dict()', start)
    patched = '''        # Resolve relative asset files against the original object directory.
        for elem in root.findall("./asset/*[@file]"):
            elem.set("file", os.path.join(folder, elem.get("file")))
        xml_str = ET.tostring(root, encoding="utf8").decode("utf8")
        xml_str = self.postprocess_model_xml(xml_str)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml") as temp:
            temp.write(xml_str)
            temp.flush()
            super().__init__(
                fname=temp.name,
                name=name,
                joints=[dict(type="free", damping="0.0005")],
                obj_type="all",
                duplicate_collision_geoms=False,
                scale=scale,
            )

'''
    source = source[:start] + patched + source[end:]
    path.write_text(source.replace('import time\n', 'import tempfile\n', 1))


def prepare(root):
    destination = root / BUNDLE_DIR
    if destination.exists():
        return identify(destination)

    with tempfile.TemporaryDirectory(prefix='.robocasa365-prepare-', dir=root) as temp:
        stage = Path(temp) / BUNDLE_DIR
        for entry in ARCHIVES:
            archive = root / entry['dst']
            if file_sha256(archive) != entry['sha256']:
                raise ValueError(f'checksum mismatch for {archive}')
            print(f"Verified {archive.name}", flush=True)
            if 'package' in entry:
                extract_source(archive, stage / entry['package'], entry['package'])
            else:
                assets = stage / 'robocasa/robocasa/models/assets'
                with ZipFile(archive) as member:
                    member.extractall(assets / entry['extract_into'])

        make_readonly_compatible(stage / 'robocasa/robocasa/models/objects/objects.py')
        receipt = prepared_receipt({name: file_sha256(stage / name) for name in SOURCE_FILES})
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2) + '\n')
        bundle = identify(stage)
        try:
            stage.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise
            if identify(destination) != bundle:
                raise ValueError('another preparation published different simulator bytes')
        return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    root = Path(dataset_root('robocasa365'))
    bundle = prepare(root)
    print(f'RoboCasa365 ready at {root / BUNDLE_DIR} with bundle {bundle}')


if __name__ == '__main__':
    main()
