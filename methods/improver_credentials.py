"""Read only seeded credential stores into an in-memory exact-value redaction set.

No generic config traversal and no credential values in parser argv, environment,
diagnostics, or archive receipts. DSH YAML uses its pinned runtime's own dependency.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import subprocess

from improver_archive import ArchiveFailure, _directory

_MAX_DOCUMENT_BYTES = 1024 * 1024
_DSH_VALUES = r'''
(() => { try {
  const fs = require('node:fs');
  const {createRequire} = require('node:module');
  const yaml = createRequire(process.argv[1])('yaml');
  const doc = yaml.parseDocument(fs.readFileSync(0, 'utf8'), {prettyErrors:false, uniqueKeys:true});
  if (doc.errors.length) throw Error();
  const root = doc.toJS({maxAliasCount:100}) ?? {};
  const values = new Set();
  function map(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error();
    return value;
  }
  function fields(value, allowed) {
    map(value);
    if (Object.keys(value).some(key=>!allowed.includes(key))) throw Error();
  }
  function secret(value) {
    if (typeof value !== 'string' || !value.length) throw Error();
    values.add(value);
  }
  function stringMap(value) {
    for (const [key,item] of Object.entries(map(value))) {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw Error();
      secret(item);
    }
  }
  function grant(value, seen=new Set()) {
    if (typeof value === 'string') { if(value.length) values.add(value); return; }
    if (value === null || typeof value === 'boolean') return;
    if (typeof value === 'number' && Number.isFinite(value)) return;
    if (!value || typeof value !== 'object' || seen.has(value)) throw Error();
    seen.add(value);
    for (const item of Object.values(value)) grant(item,seen);
    seen.delete(value);
  }
  function config(value, seen=new Set()) {
    if (!value || typeof value !== 'object') return;
    if (seen.has(value)) throw Error();
    seen.add(value);
    const names = new Set(['apikey','token','apitoken','accesstoken','refreshtoken','authtoken',
      'bearertoken','authorization','password','clientsecret','secretkey','secret']);
    for (const [key,item] of Object.entries(value)) {
      if (names.has(key.replace(/[-_]/g,'').toLowerCase()) && item != null) secret(item);
      else config(item,seen);
    }
    seen.delete(value);
  }
  if (process.argv[2] === 'config') {
    map(root); config(root);
    process.stdout.write(JSON.stringify([...values]));
    return;
  }
  fields(root,['version','refs','records']);
  if (Object.keys(root).length && root.version !== 1) throw Error();
  if (root.refs != null) stringMap(root.refs);
  if (root.records != null) {
    for (const [key,record] of Object.entries(map(root.records))) {
      if (!/^[a-z][a-z0-9-]*\/[a-z][a-z0-9-]*$/.test(key)) throw Error();
      map(record);
      if (record.kind === 'api-key') {
        fields(record,['kind','key','env']);
        if ('key' in record) secret(record.key);
        if ('env' in record) stringMap(record.env);
      } else if (record.kind === 'grant') {
        fields(record,['kind','payload']);
        if (!('payload' in record)) throw Error();
        grant(record.payload);
      } else throw Error();
    }
  }
  process.stdout.write(JSON.stringify([...values]));
} catch {
  // Never print YAML diagnostics: they can quote credential-bearing source lines.
  process.exitCode = 2;
} })();
'''


def _document(path):
    """Only a private regular file in the seeded home; missing means no store."""
    parent, directory_fd = _directory(path.parent)
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError()
            data = stream.read(_MAX_DOCUMENT_BYTES + 1)
            if len(data) > _MAX_DOCUMENT_BYTES:
                raise ValueError()
            return data
    finally:
        os.close(directory_fd)


def _codex_values(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    root = json.loads(data, object_pairs_hook=unique)
    token_fields = {'id_token','access_token','refresh_token','api_key','OPENAI_API_KEY',
                    'token','authorization','password','secret'}
    allowed = token_fields | {'tokens','auth_mode','last_refresh','account_id'}
    if not isinstance(root, dict) or set(root) - allowed:
        raise ValueError()
    values = []
    def tokens(value, allowed_fields):
        if not isinstance(value, dict) or set(value) - allowed_fields:
            raise ValueError()
        for key, item in value.items():
            if key in token_fields:
                if item is not None and not isinstance(item, str):
                    raise ValueError()
                if item:
                    values.append(item)
    tokens(root, allowed)
    if root.get('tokens') is not None:
        tokens(root['tokens'], token_fields | {'account_id'})
    return values


_AUTH_CONFIG_FIELDS = frozenset({'apikey','token','apitoken','accesstoken','refreshtoken',
    'authtoken','bearertoken','authorization','password','clientsecret','secretkey','secret'})


def _config_values(value):
    values = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.replace('-','').replace('_','').lower() in _AUTH_CONFIG_FIELDS and item is not None:
                if not isinstance(item,str) or not item:
                    raise ValueError()
                values.append(item)
            else:
                values.extend(_config_values(item))
    elif isinstance(value,list):
        for item in value:
            values.extend(_config_values(item))
    return values


def _dsh_values(data, runtime, *, config=False):
    node = runtime.get('node')
    root = runtime.get('root')
    if not isinstance(node, str) or not node or not isinstance(root, str) or not root:
        raise ValueError()
    package = Path(root) / 'packages/credentials/credentials-local/package.json'
    if not Path(node).is_absolute() or not Path(node).is_file() or not package.is_file():
        raise ValueError()
    # Only YAML bytes travel on stdin. No inherited startup hooks or credentials.
    result = subprocess.run([node, '-e', _DSH_VALUES, str(package.absolute()),
                            'config' if config else 'credentials'],
        input=data, capture_output=True, timeout=10, env={'LANG':'C.UTF-8'})
    if result.returncode != 0 or len(result.stdout) > 8 * _MAX_DOCUMENT_BYTES:
        raise ValueError()
    values = json.loads(result.stdout)
    if not isinstance(values, list) or any(not isinstance(x,str) or not x for x in values):
        raise ValueError()
    return values


def seeded_credentials(workspace, style, runtime):
    """Read exact seeded stores/config auth scalars; fail closed before dispatch.

    Unrecognized configuration keys are not presumed secret. This is a known-value
    boundary, not a proof that arbitrary encoded or undisclosed secrets are absent.
    """
    try:
        home = Path(workspace) / ('.dsh-home' if style == 'dsh' else '.codex-home')
        if not home.exists() and not home.is_symlink():
            return []
        store = _document(home / ('.credentials.yaml' if style == 'dsh' else 'auth.json'))
        config = _document(home / ('settings.yaml' if style == 'dsh' else 'config.toml'))
        values = []
        if style == 'dsh':
            if store is not None:
                values.extend(_dsh_values(store,runtime))
            if config is not None:
                values.extend(_dsh_values(config,runtime,config=True))
        else:
            if store is not None:
                values.extend(_codex_values(store))
            if config is not None:
                # The launcher itself writes this exact, secret-free default.
                # It is safe to recognize without a parser in a sandbox whose
                # system Python is older than the driver's Python 3.12. Any
                # other TOML still requires a parser and fails closed if absent.
                if config != b'\nresponses_experimental_transport = "https"\n':
                    import tomllib
                    values.extend(_config_values(tomllib.loads(config.decode('utf-8'))))
        for value in list(values):
            if value.lower().startswith(('bearer ','basic ')):
                values.append(value.split(' ',1)[1].strip())
        for value in values:
            value.encode('utf-8')  # unsupported surrogate escapes fail before execution
        return sorted({value for value in values if value},key=len,reverse=True)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError, ImportError,
            subprocess.SubprocessError):
        raise ArchiveFailure('Cannot safely interpret seeded improver credentials') from None
