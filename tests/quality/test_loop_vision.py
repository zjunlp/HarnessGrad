"""Loop Vision sends image parts while preserving Loop's shell interaction."""
import base64
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGO4UyEHAAOmAXPCqsYuAAAAAElFTkSuQmCC'
)
RED_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'
)


@pytest.fixture
def agent(monkeypatch):
    path = ROOT / 'base_harness/loop-vision/agent.py'
    spec = importlib.util.spec_from_file_location('loop_vision_test', path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr(harness, 'MAX_STEPS', 5)
    monkeypatch.setattr(harness, 'FINISH_NUDGE_STEPS', 2)
    return harness


def images(messages):
    return [part for message in messages if isinstance(message['content'], list)
            for part in message['content'] if part['type'] == 'image_url']


def test_visual_actions_attach_files_and_replace_the_previous_frames(agent, monkeypatch, tmp_path):
    (tmp_path / 'source.png').write_bytes(PNG)
    actions = iter([
        {'tool': 'bash', 'command': 'cp source.png a.png && echo first', 'images': ['a.png']},
        {'tool': 'bash', 'command': 'cp source.png b.png && echo second', 'images': ['b.png']},
        {'tool': 'bash', 'command': 'echo plain text'},
        {'answer': 'done'},
    ])
    history = []

    def complete(messages):
        history.append(deepcopy(messages))
        return json.dumps(next(actions))

    monkeypatch.setattr(agent, '_complete', complete)
    assert agent.run({'goal': 'visual control'}, tmp_path) == 'done'
    assert not images(history[0])
    attachment, = images(history[1])
    assert base64.b64decode(attachment['image_url']['url'].split(',', 1)[1]) == PNG
    assert 'first' in history[1][-1]['content'][0]['text']
    assert history[2][3]['content'].endswith('Image 1: a.png')
    assert len(images(history[2])) == len(images(history[3])) == 1
    assert 'second' in history[2][-1]['content'][0]['text']
    assert 'plain text' in history[3][-1]['content']

    trace = [json.loads(line) for line in (tmp_path / 'trace.jsonl').read_text().splitlines()]
    assert trace[0]['harness_identity']['harness'] == 'loop-vision'
    records = [row['image_attachments'] for row in trace if 'image_attachments' in row]
    assert records[0] == [{
        'path': 'a.png', 'media_type': 'image/png', 'bytes': len(PNG), 'sha256': sha256(PNG).hexdigest(),
    }]
    assert base64.b64encode(PNG).decode() not in json.dumps(trace)


def test_repeated_command_and_finish_reminders_work_with_images(agent, monkeypatch, tmp_path):
    (tmp_path / 'view.png').write_bytes(PNG)
    action = {'tool': 'bash', 'command': 'true', 'images': ['view.png']}
    calls = []

    def complete(messages):
        calls.append(deepcopy(messages))
        return json.dumps(action if len(calls) < 4 else {'answer': 'done'})

    monkeypatch.setattr(agent, '_complete', complete)
    assert agent.run({'goal': 'visual control'}, tmp_path) == 'done'
    assert 'same command' in calls[2][-1]['content'][0]['text']
    assert '2 step(s) left' in calls[3][-1]['content'][0]['text']
    assert len(images(calls[3])) == 1


@pytest.mark.parametrize(
    'outputs, exit_codes, frames',
    [
        (('step=32', 'step=64'), (0, 0), (PNG, PNG)),
        (('failed', 'failed'), (1, 2), (None, None)),
        (('step=32', 'step=32'), (0, 0), (PNG, RED_PNG)),
    ],
    ids=['text', 'exit-code', 'image-at-same-path'],
)
def test_repeated_command_with_changed_result_does_not_nudge(
    agent, monkeypatch, tmp_path, outputs, exit_codes, frames,
):
    action = {'tool': 'bash', 'command': 'cat output.txt; exit "$(cat status.txt)"'}
    if frames[0] is not None:
        action['images'] = ['view.png']
    observations = iter(zip(outputs, exit_codes, frames))

    def complete(messages):
        content = messages[-1]['content']
        text = content[0]['text'] if isinstance(content, list) else content
        assert 'same command' not in text
        result = next(observations, None)
        if result is None:
            return json.dumps({'answer': 'done'})
        output, status, frame = result
        (tmp_path / 'output.txt').write_text(output)
        (tmp_path / 'status.txt').write_text(str(status))
        if frame is not None:
            (tmp_path / 'view.png').write_bytes(frame)
        return json.dumps(action)

    monkeypatch.setattr(agent, '_complete', complete)
    assert agent.run({'goal': 'visual control'}, tmp_path) == 'done'


@pytest.mark.parametrize(
    'action_images, result_images, exit_code, attached_names',
    [
        (None, {'left': 'left.png', 'right': 'right.png', 'wrist': 'wrist.png'}, 0,
         ['left.png', 'right.png', 'wrist.png']),
        (['manual.png'], {'left': 'left.png'}, 0, ['manual.png']),
        ([], {'left': 'left.png'}, 0, []),
        (None, {'left': 'left.png'}, 1, []),
        (None, {'outside': '../outside.png'}, 0, []),
        (None, ['left.png'], 0, []),
    ],
)
def test_images_in_shell_results(agent, monkeypatch, tmp_path,
                                 action_images, result_images, exit_code, attached_names):
    for name in ('left.png', 'right.png', 'wrist.png', 'manual.png'):
        (tmp_path / name).write_bytes(PNG)
    (tmp_path / 'observation.json').write_text(
        json.dumps({'instruction': 'Look at the scene.', 'images': result_images})
    )
    action = {'tool': 'bash', 'command': f'cat observation.json; echo diagnostic >&2; exit {exit_code}'}
    if action_images is not None:
        action['images'] = action_images
    calls = []

    def complete(messages):
        calls.append(deepcopy(messages))
        return json.dumps(action if len(calls) == 1 else {'answer': 'done'})

    monkeypatch.setattr(agent, '_complete', complete)
    assert agent.run({'goal': 'visual control'}, tmp_path) == 'done'
    observation = calls[1][-1]['content']
    text = observation[0]['text'] if attached_names else observation
    assert 'Look at the scene.' in text
    assert len(images(calls[1])) == len(attached_names)
    for part in images(calls[1]):
        assert base64.b64decode(part['image_url']['url'].split(',', 1)[1]) == PNG
    trace = [json.loads(line) for line in (tmp_path / 'trace.jsonl').read_text().splitlines()]
    command = next(row for row in trace if 'command' in row)
    assert [record['path'] for record in command.get('image_attachments', [])] == attached_names


def test_invalid_image_paths_are_rejected_before_running_the_command(agent, monkeypatch, tmp_path):
    actions = iter([
        {'tool': 'bash', 'command': 'touch should-not-exist', 'images': ['../outside.png']},
        {'answer': 'done'},
    ])
    calls = []

    def complete(messages):
        calls.append(deepcopy(messages))
        return json.dumps(next(actions))

    monkeypatch.setattr(agent, '_complete', complete)
    assert agent.run({'goal': 'validate input'}, tmp_path) == 'done'
    assert not (tmp_path / 'should-not-exist').exists()
    assert 'relative to the working directory' in calls[1][-1]['content']


def test_attachment_errors_and_failed_command_do_not_send_images(agent, tmp_path):
    work = tmp_path / 'task'
    work.mkdir()
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    (work / 'link.png').symlink_to(outside)
    (work / 'linked-dir').symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(work / 'fifo.png')
    (work / 'good.png').write_bytes(PNG)
    (work / 'bad.png').write_text('not an image')
    with (work / 'big.png').open('wb') as large:
        large.truncate(agent.MAX_IMAGE_BYTES + 1)
    paths = [Path(name) for name in
             ('link.png', 'linked-dir/outside.png', 'fifo.png', 'bad.png', 'big.png')]
    parts, errors = agent._image_attachments(work, paths, 0)
    assert not parts and all(row.get('error') for row in errors)
    parts, errors = agent._image_attachments(work, [Path('good.png')], 1)
    assert not parts and errors == [{'path': 'good.png', 'error': 'shell command failed'}]


def test_trimming_counts_only_text_in_a_multimodal_message(agent):
    pic = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'a' * 500}}
    history = [
        {'role': 'system', 'content': 's'},
        {'role': 'user', 'content': 't'},
        {'role': 'assistant', 'content': 'old'},
        {'role': 'user', 'content': 'old'},
        {'role': 'assistant', 'content': 'look'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'observation'}, pic]},
    ]
    assert agent._trim_history(history, budget=10) == 2
    assert images(history) == [pic]
