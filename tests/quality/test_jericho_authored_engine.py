"""Opt-in native Frotz evidence using an original, minimal Z-machine v3 program.

No commercial/public game input is read. This unsupported program only reads a
line and prints text: its zero scores/moves are NOT benchmark measurements.
Native code runs in timed child processes so a malformed fixture cannot crash pytest.
"""
from pathlib import Path
import json
import os
import struct
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _authored_story():
    # Z-machine v3 header, dictionary, object table and opcodes, written here.
    image = bytearray(2048)
    def word(address, value):
        struct.pack_into('>H', image, address, value)
    image[0] = 3
    for address, value in ((4, 0x400), (6, 0x400), (8, 0x380),
                           (10, 0x220), (12, 0x40), (14, 0x400),
                           (24, 0x3a0), (26, 1024)):
        word(address, value)
    image[18:24] = b'261107'
    image[0x280] = 100  # input buffer capacity
    image[0x320] = 10   # parse buffer capacity
    image[0x380:0x384] = bytes((0, 4, 0, 0))  # empty dictionary
    word(0x40, 1)  # status-line room: object 1
    word(0x265, 0x270)  # object 1's empty property table

    def print_literal(text):
        zchars = []
        for char in text:
            if char == ' ':
                zchars.append(0)
            elif 'a' <= char <= 'z':
                zchars.append(ord(char) - 91)
            else:
                zscii = 13 if char == '\n' else ord(char)
                zchars.extend((5, 6, zscii >> 5, zscii & 31))
        while len(zchars) % 3:
            zchars.append(5)
        result = bytearray((0xb2,))  # print, followed by packed Z-string
        for i in range(0, len(zchars), 3):
            packed = (zchars[i] << 10) | (zchars[i + 1] << 5) | zchars[i + 2]
            result.extend(struct.pack('>H', packed | (0x8000 if i + 3 == len(zchars) else 0)))
        return result

    code = print_literal('authored room\n>')
    loop = 0x400 + len(code)
    code += bytes((0xe4, 0x0f, 2, 0x80, 3, 0x20))  # sread text, parse
    code += print_literal('accepted\n>')
    position = 0x400 + len(code)
    code += bytes((0x8c,)) + struct.pack('>H', (loop - (position + 3) + 2) & 0xffff)
    image[0x400:0x400 + len(code)] = code
    word(28, sum(image[64:]) & 0xffff)
    return bytes(image)


PROBE = r'''
import importlib.util,json,sys
from pathlib import Path
root,work,mode=map(Path,sys.argv[1:])
sys.path.insert(0,str(root))
import jericho
if str(mode)=='projection':
    projection_spec=importlib.util.spec_from_file_location('authored_runtime',root/'data/datasets/jericho/runtime.py')
    projection_module=importlib.util.module_from_spec(projection_spec);projection_spec.loader.exec_module(projection_module)
    agent_runtime=projection_module.agent_runtime
    import shutil
    target=work/'projection'/'jericho'; target.mkdir(parents=True)
    for item in agent_runtime(str(Path(jericho.__file__).parent))['files']:
        if 'content' in item: (target/item['path']).write_text(item['content'])
        else: shutil.copyfile(item['from'],target/item['path'])
    for name in list(sys.modules):
        if name=='jericho' or name.startswith('jericho.'): del sys.modules[name]
    sys.path.insert(0,str(target.parent))
    import jericho
spec=importlib.util.spec_from_file_location('authored_relay',root/'data/datasets/jericho/relay.py')
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
rom=work/'authored.z3'
if str(mode)=='projection':
    env=jericho.FrotzEnv(str(rom),seed=0);env.reset()
else: env=relay._make_public_env(rom,seed=0)
assert env._seed==0 and env.bindings=={}
assert not env.is_fully_supported
assert 'authored room' in env.reset()[0]
observation,reward,done,info=env.step('look')
assert 'accepted' in observation and reward==0 and not done
assert info=={'moves':0,'score':0} and env.get_max_score()==0
assert env.get_valid_actions()==[]
try: env.get_inventory()
except AttributeError: inventory='unsupported-player-object'
else: raise AssertionError('expected absent player object in this authored story')
env.close();del env
result={'mode':str(mode),'native_reset_step':True,'fully_supported':False,
        'benchmark_measured':False,'inventory':inventory,'info':info}
if str(mode)=='relay':
    game=relay.Game(rom,0)
    assert 'accepted' in game.handle('look',{'actions':[]})['text']
    assert game.handle('do',{'actions':[],'action':'look'})['done'] is False
    assert game.handle('score',{'actions':['look']})=={'moves':0,'score':0,'maximum':0,'actions':1}
    try: game.handle('inventory',{'actions':[]})
    except RuntimeError as error: assert str(error)=='Jericho environment unavailable'
    else: raise AssertionError('inventory failure should be sanitized')
    result['relay_inventory']='sanitized-unavailable'
print(json.dumps(result))
'''


def test_authored_program_calls_native_engine_and_matches_public_projection(tmp_path):
    store = os.environ.get('HG_TEST_JERICHO_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_JERICHO_RUNTIME_ROOT to a prepared external artifact')
    from harnessgrad.dataset_runtime import resolve_execution
    runtime = resolve_execution('jericho', root=store)
    (tmp_path / 'authored.z3').write_bytes(_authored_story())
    receipts = []
    for mode in ('relay', 'projection'):
        result = subprocess.run(
            [runtime['python'], '-I', '-B', '-c', PROBE, str(ROOT), str(tmp_path), mode],
            cwd=tmp_path, env={'HOME': str(tmp_path), 'PATH': os.defpath,
                               'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8'},
            text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'not fully supported' in result.stderr
        receipts.append(json.loads(result.stdout))
    assert receipts[0]['info'] == receipts[1]['info']
    assert all(item['native_reset_step'] and not item['benchmark_measured'] for item in receipts)
