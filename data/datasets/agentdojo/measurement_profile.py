"""Public AgentDojo source declarations and conservative implementation roles.

These declarations do not verify the dataset used by an individual run. The
adapter, bridge and bootstrap are shared by loading and verification and are
therefore included in both implementation identities.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

from harnessgrad.provenance import measurement_components as identities
from harnessgrad.provenance.source_declarations import literal_assignments

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/agentdojo.py'
ASSETS = 'data/datasets/agentdojo/'
SOURCE_MANIFEST = ASSETS + 'source-manifest.json'
PREPARED_MANIFEST = ASSETS + 'prepared-manifest.json'
LOADER = {'adapter': ADAPTER, 'prepare': ASSETS + 'prepare.py',
          'bootstrap': ASSETS + 'bootstrap.py', 'bridge': ASSETS + 'bridge.py'}
SCORER = {'adapter': ADAPTER, 'bootstrap': ASSETS + 'bootstrap.py',
          'bridge': ASSETS + 'bridge.py', 'check': ASSETS + 'verify/check.py'}
METRIC = 'AgentDojo utility under fixed injection'
_SOURCE_PIN = 'd9fc402c7cd510ef3c766aa7c28fb70e18352b24b6d941a443bcbe60b1654826'
_PREPARED_PIN = 'd4b4528c6bc781a3ebe1ec3bba6c0d5057345d17243168c5ee1c6ebc58018caa'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate AgentDojo declaration key')
        result[key] = value
    return result


def _constants(raw, names):
    try:
        return literal_assignments(raw, names)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid AgentDojo source declaration') from exc


def _bootstrap_pin(raw, function_name):
    try:
        tree = ast.parse(raw)
    except SyntaxError as exc:
        raise ValueError('invalid AgentDojo bootstrap declaration') from exc
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
    if len(functions) != 1:
        raise ValueError('missing AgentDojo bootstrap pin')
    pins = [node.comparators[0].value for node in ast.walk(functions[0])
            if isinstance(node, ast.Compare) and len(node.comparators) == 1
            and isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Attribute)
            and node.left.func.attr == 'hexdigest' and isinstance(node.comparators[0], ast.Constant)
            and isinstance(node.comparators[0].value, str)
            and re.fullmatch(r'[0-9a-f]{64}', node.comparators[0].value)]
    if len(pins) != 1:
        raise ValueError('invalid AgentDojo bootstrap pin')
    return pins[0]


def _manifest(raw, expected):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('AgentDojo declaration does not match the pinned manifest')
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('invalid AgentDojo declaration')
    return value


def profile():
    adapter = (ROOT / ADAPTER).read_bytes()
    bridge = (ROOT / SCORER['bridge']).read_bytes()
    bootstrap = (ROOT / SCORER['bootstrap']).read_bytes()
    if (_bootstrap_pin(bootstrap, 'source_manifest') != _SOURCE_PIN
            or _bootstrap_pin(bootstrap, 'validate_prepared') != _PREPARED_PIN):
        raise ValueError('AgentDojo bootstrap and provenance pins disagree')
    source_raw = (ROOT / SOURCE_MANIFEST).read_bytes()
    prepared_raw = (ROOT / PREPARED_MANIFEST).read_bytes()
    source = _manifest(source_raw, _SOURCE_PIN)
    prepared = _manifest(prepared_raw, _PREPARED_PIN)
    adapter_values = _constants(adapter, {'COMMIT', 'VERSION'})
    bridge_values = _constants(bridge, {'VERSION', 'ATTACK_NAME'})
    loaded = sys.modules.get('data.datasets.agentdojo')
    if loaded is not None and any(getattr(loaded, name, None) != value
                                  for name, value in adapter_values.items()):
        raise ValueError('loaded AgentDojo declaration differs from source')
    revision = source.get('revision')
    archive = source.get('archive_sha256')
    if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)
            or not isinstance(archive, str) or not re.fullmatch(r'[0-9a-f]{64}', archive)
            or revision != adapter_values['COMMIT'] or revision != prepared.get('revision')
            or adapter_values['VERSION'] != bridge_values['VERSION']
            or bridge_values['ATTACK_NAME'] != prepared.get('attack')
            or type(prepared.get('tasks')) is not int or prepared['tasks'] < 1
            or any(not isinstance(prepared.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', prepared[k])
                   for k in ('catalog_sha256', 'prepared_sha256'))):
        raise ValueError('inconsistent AgentDojo provenance declaration')
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    scorer_files = identities.capture_implementation('scorer', ROOT, SCORER, protocol=METRIC)
    if (loader['details']['components']['adapter'] != hashlib.sha256(adapter).hexdigest()
            or scorer_files['details']['components']['adapter'] != hashlib.sha256(adapter).hexdigest()
            or loader['details']['components']['bridge'] != hashlib.sha256(bridge).hexdigest()
            or scorer_files['details']['components']['bridge'] != hashlib.sha256(bridge).hexdigest()
            or loader['details']['components']['bootstrap'] != hashlib.sha256(bootstrap).hexdigest()
            or scorer_files['details']['components']['bootstrap'] != hashlib.sha256(bootstrap).hexdigest()
            or (ROOT / SOURCE_MANIFEST).read_bytes() != source_raw
            or (ROOT / PREPARED_MANIFEST).read_bytes() != prepared_raw):
        raise ValueError('AgentDojo source changed during identity capture')
    # The declared source inventory covers code and suite data together; include
    # it conservatively in the scorer identity rather than claiming separation.
    scorer = identities.implementation('scorer', {**scorer_files['details']['components'],
        'upstream_inventory': hashlib.sha256(source_raw).hexdigest()}, protocol=METRIC)
    return {'data_source': identities.data_source('agentdojo',
        origin='AgentDojo source archive and declared prepared case inventory',
        revision=revision + ':prepared-manifest-sha256:' + hashlib.sha256(prepared_raw).hexdigest(),
        declared_artifact_sha256=archive), 'loader': loader, 'scorer': scorer}
