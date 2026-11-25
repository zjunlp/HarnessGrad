"""Offline controls for image capture, action correlation and train projection."""

import base64
import hashlib
import json
import struct
import zlib

import pytest

from eval.service.http_observation import HTTPObservation
from eval.service.visual_evidence import ImageArchive
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
