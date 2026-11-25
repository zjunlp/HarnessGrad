"""Bounded train-side image capture at the platform model route."""

import base64
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4


REQUEST_ID_HEADER = 'X-HarnessGrad-Visual-Request'
MAX_REQUEST_BYTES = 16 << 20
MAX_IMAGE_BYTES = 4 << 20
MAX_TRIAL_BYTES = 128 << 20
MAX_TRIAL_IMAGES = 4096
_MIME_EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg'}


def _image_parts(operation, payload):
    if not isinstance(payload, dict):
        raise ValueError('invalid model payload')
    messages = payload.get('messages' if operation == 'chat.completions' else 'input')
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        if operation == 'responses' and message.get('type') == 'function_call_output':
            content = message.get('output')
        else:
            content = message.get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if operation == 'responses' and part.get('type') == 'input_image':
                yield part.get('image_url')
            elif operation == 'chat.completions' and part.get('type') == 'image_url':
                image = part.get('image_url')
                yield image.get('url') if isinstance(image, dict) else None


def _decode_image(url):
    if not isinstance(url, str) or not url.startswith('data:'):
        return None, None, 'external_image_reference'
    header, _, encoded = url.partition(',')
    media = header.removeprefix('data:').removesuffix(';base64')
    if media not in _MIME_EXTENSIONS or header != f'data:{media};base64':
        return None, None, 'unsupported_image_format'
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        return None, None, 'image_too_large'
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        return None, None, 'invalid_image_encoding'
    if len(data) > MAX_IMAGE_BYTES:
        return None, None, 'image_too_large'
    if media == 'image/png':
        valid = data.startswith(b'\x89PNG\r\n\x1a\n') and data.endswith(b'IEND\xaeB`\x82')
    else:
        valid = data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')
    return (media, data, None) if valid else (None, None, 'invalid_image_bytes')


def _atomic_file(path, data, *, exclusive=False):
    tmp = path.parent / f'.{uuid4().hex}.tmp'
    try:
        with tmp.open('xb') as stream:
            stream.write(data)
        tmp.chmod(0o444)
        if exclusive:
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        else:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ImageArchive:
    def __init__(self, run_dir, round_no):
        self.root = Path(run_dir).resolve() / 'visual-evidence' / 'train'
        self.round = round_no
        self.images = self.root / 'images'
        self.manifests = self.root / 'rounds' / f'round-{round_no}'
        self.images.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.trials = {}

    def start_trial(self, task_id, trial_id):
        with self.lock:
            number = self.trials.get(task_id, 0) + 1
            self.trials[task_id] = number
        return TrialImages(self, task_id, number, trial_id)


class TrialImages:
    def __init__(self, archive, task_id, trial, trial_id):
        self.archive = archive
        self.identity = {'round': archive.round, 'task_id': task_id, 'trial': trial,
                         'trial_id': trial_id, 'capture_id': uuid4().hex}
        self.bytes = 0
        self.references = 0
        self.seen = set()
        self.calls = []

    def start_request(self, operation, headers):
        entry = {'request': len(self.calls) + 1, 'operation': operation, 'images': [],
                 'http_status': None, 'response_complete': False}
        label = headers.get(REQUEST_ID_HEADER, '')
        if re.fullmatch(r'[0-9a-f]{32}', label):
            entry['client_request_id'] = label
        self.calls.append(entry)
        return entry

    def capture(self, entry, body):
        try:
            images = _image_parts(entry['operation'], json.loads(body))
            for index, value in enumerate(images):
                if self.references >= MAX_TRIAL_IMAGES:
                    entry['capture_status'] = 'trial_image_limit'
                    return
                self.references += 1
                media, data, error = _decode_image(value)
                row = {'index': index}
                entry['images'].append(row)
                if error:
                    row['status'] = error
                    continue
                digest = hashlib.sha256(data).hexdigest()
                name = f'{digest}.{_MIME_EXTENSIONS[media]}'
                if name not in self.seen and self.bytes + len(data) > MAX_TRIAL_BYTES:
                    row['status'] = 'trial_byte_limit'
                    continue
                if name not in self.seen:
                    target = self.archive.images / name
                    if not target.is_file():
                        _atomic_file(target, data, exclusive=True)
                    self.seen.add(name)
                    self.bytes += len(data)
                row.update(status='captured', sha256=digest, media_type=media, bytes=len(data), file=name)
            entry['capture_status'] = 'complete'
        except (ValueError, RecursionError):
            entry['capture_status'] = 'invalid_json'
        except OSError:
            entry['capture_status'] = 'storage_error'

    def finish(self, trace, *, available):
        links = _trace_links(trace)
        for call in self.calls:
            link = links.get(call.get('client_request_id'))
            if link is not None:
                _link_call(call, link)
        report = {'schema': 'harnessgrad.visual-trial.v1', **self.identity,
                  'status': 'recorded' if available else 'route_unavailable', 'calls': self.calls}
        name = self.identity['capture_id'] + '.json'
        _atomic_file(self.archive.manifests / name, json.dumps(report, ensure_ascii=False).encode() + b'\n')


def _trace_links(trace):
    links = {}
    for line in trace.splitlines():
        if '"visual_request"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        link = row.get('visual_request') if isinstance(row, dict) else None
        request_id = link.get('id') if isinstance(link, dict) else None
        if isinstance(request_id, str) and re.fullmatch(r'[0-9a-f]{32}', request_id):
            links[request_id] = link
    return links


def _link_call(call, link):
    attachments = link.get('images')
    if not isinstance(attachments, list) or len(attachments) != len(call['images']):
        return
    if not all(isinstance(attachment, dict) and row.get('sha256') and
               row['sha256'] == attachment.get('sha256')
               for row, attachment in zip(call['images'], attachments)):
        return
    for key in ('model_step', 'tool_step', 'environment_step'):
        if type(link.get(key)) is int and link[key] >= 0:
            call[key] = link[key]
    for row, attachment in zip(call['images'], attachments):
        label = attachment.get('label')
        if isinstance(label, str) and len(label) <= 128:
            row['label'] = label
