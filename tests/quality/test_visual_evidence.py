"""Offline controls for image capture, action correlation and train projection."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

from eval.service.http_observation import HTTPObservation
from eval.service.visual_evidence import ImageArchive, reuse_round_zero, round_manifests
from harnessgrad.experiment.channel import _stage_visual_evidence


def _png(rgb):
    def chunk(name, content):
        return struct.pack('>I', len(content)) + name + content + struct.pack('>I', zlib.crc32(name + content))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + rgb)) + chunk(b'IEND', b''))


def _send(connection, operation, data, label, *, chunked=False):
    url = 'data:image/png;base64,' + base64.b64encode(data).decode()
    part = ({'type': 'input_image', 'image_url': url} if operation == 'responses'
            else {'type': 'image_url', 'image_url': {'url': url}})
    key = 'input' if operation == 'responses' else 'messages'
    message = ({'type': 'function_call_output', 'output': [part]} if operation == 'responses'
               else {'content': [part]})
    body = json.dumps({key: [message], 'private_text': 'not an artifact'}).encode()
    header = f'POST /v1/{operation} HTTP/1.1\r\nX-HarnessGrad-Visual-Request: {label}\r\n'
    if chunked:
        header += 'Transfer-Encoding: chunked\r\n\r\n'
        body = f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n'
    else:
        header += f'Content-Length: {len(body)}\r\n\r\n'
    connection.feed('client', header.encode() + body[:19])
    connection.feed('client', body[19:])
    connection.feed('upstream', b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')


@pytest.mark.parametrize('operation,chunked', [('chat/completions', False), ('responses', True)])
def test_deduplicates_images_and_preserves_distinct_tool_events(tmp_path, operation, chunked):
    archive = ImageArchive(tmp_path, 2)
    trial = archive.start_trial('train', 'trial-real')
    http = HTTPObservation(trial)
    connection = http.connection()
    red, blue = _png(b'\xff\x00\x00'), _png(b'\x00\x00\xff')
    trace = []
    for number, data in enumerate((red, blue, red), 1):
        label = f'{number:032x}'
        _send(connection, operation, data, label, chunked=chunked)
        trace.append(json.dumps({'visual_request': {
            'id': label, 'model_step': number, 'tool_step': number - 1,
            'images': [{'sha256': hashlib.sha256(data).hexdigest(), 'label': 'wrist'}],
        }}))
    trial.finish('\n'.join(trace), available=True)
    hidden = archive.start_trial('not-admitted', None)
    hidden.finish('', available=False)
    channel = tmp_path / 'method'
    _stage_visual_evidence(channel, archive.root, [{'round': 2}], ['train'])

    assert len(list(archive.images.iterdir())) == 2
    index = (channel / 'history' / 'visual' / 'round-2.jsonl').read_text()
    assert 'not-admitted' not in index and 'private_text' not in index
    item = json.loads(index)
    assert item['trial_id'] == 'trial-real'
    assert [call['tool_step'] for call in item['calls']] == [0, 1, 2]
    for call, data in zip(item['calls'], (red, blue, red)):
        assert call['http_status'] == 200 and call['response_complete']
        image = call['images'][0]
        assert image['label'] == 'wrist'
        assert (archive.images / f'{image["sha256"]}.png').read_bytes() == data
    assert http.report()['complete']


def test_retry_replaces_logical_index_and_h0_reuse_copies_only_selected_images(tmp_path):
    donor, recipient = tmp_path / 'donor', tmp_path / 'recipient'
    red, green, blue = _png(b'\xff\x00\x00'), _png(b'\x00\xff\x00'), _png(b'\x00\x00\xff')
    archive = ImageArchive(donor, 0)

    def record(archive, task, trial_id, image):
        trial = archive.start_trial(task, trial_id, trial_number=1)
        _send(HTTPObservation(trial).connection(), 'responses', image, 'a' * 32)
        trial.finish('', available=True)
        return trial

    first = record(archive, 'train', 'trial-1', red)
    # Keep an earlier UUID index as a pre-upgrade run would have on disk.
    (index,) = archive.manifests.glob('*.json')
    historical = index.read_bytes()
    resumed = ImageArchive(donor, 0)
    retried = record(resumed, 'train', 'trial-1', green)
    (archive.manifests / f"{first.identity['capture_id']}.json").write_bytes(historical)
    record(resumed, 'excluded', 'trial-1', blue)
    assert len(list(archive.manifests.glob('trial-*.json'))) == 2

    assert reuse_round_zero(donor, recipient, ['train']) == 1
    assert reuse_round_zero(donor, recipient, ['train']) == 1
    copied = recipient / 'visual-evidence' / 'train'
    recorded = round_manifests(copied, 0, ['train', 'excluded'])
    assert len(recorded) == 1 and recorded[0]['capture_id'] == retried.identity['capture_id']
    assert [path.read_bytes() for path in (copied / 'images').iterdir()] == [green]
    _stage_visual_evidence(tmp_path / 'method', copied, [{'round': 0}], ['train'])
    output = tmp_path / 'method' / 'history' / 'visual' / 'round-0.jsonl'
    [row] = output.read_text().splitlines()
    assert json.loads(row)['trial_id'] == 'trial-1'
    assert reuse_round_zero(tmp_path / 'legacy', recipient, ['train']) == 0


def test_visual_index_through_a_runs_symlink_opens_in_method_sandbox(tmp_path):
    from eval.containers import sandbox

    if shutil.which('bwrap') is None:
        pytest.skip('bwrap is not installed')
    platform, runs, work, method, base = (tmp_path / name for name in
                                         ('platform', 'physical-runs', 'work', 'method', 'base'))
    for directory in (platform, runs, work, method, base):
        directory.mkdir()
    runs_link = platform / 'runs'
    runs_link.symlink_to(runs, target_is_directory=True)

    archive = ImageArchive(runs / 'study', 0)
    trial = archive.start_trial('train', 'trial-1')
    png = _png(b'\xff\x00\x00')
    _send(HTTPObservation(trial).connection(), 'responses', png, 'a' * 32)
    trial.finish('', available=True)

    visual_root = runs_link / 'study' / 'visual-evidence' / 'train'
    _stage_visual_evidence(method, visual_root, [{'round': 0}], ['train'])
    index = method / 'history' / 'visual' / 'round-0.jsonl'
    image = json.loads(index.read_text())['calls'][0]['images'][0]
    # Mode A mounts the physical images directory while the platform tree is hidden.
    images = (visual_root / 'images').resolve()
    assert Path(image['path']).parent == images
    command = sandbox.command(
        [sys.executable, '-I', '-B', '-c',
         'import sys; from pathlib import Path; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())',
         image['path']], platform=platform, work_root=work, task_dir=method, repo=base,
        env={'PATH': os.defpath, 'HOME': str(method)}, network='none', visible_paths=(images,))
    result = subprocess.run(command, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == png
