"""Declared editor drivers; task/solver wrappers are not workbench editors."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = ('codex', 'dsh')
UNAVAILABLE = ('opencode', 'miniswe', 'miniagent', 'openclaw')


class AssistantUnavailable(ValueError):
    def __init__(self, message, *, secrets=()):
        super().__init__(message)
        self.secrets = tuple(secrets)


class AssistantProtocolError(ValueError):
    pass


def _provider(relative):
    source = ROOT / 'base_harness' / relative
    spec = importlib.util.spec_from_file_location('_workbench_' + source.parent.name.replace('-', '_'), source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def _file(path):
    source = Path(path).expanduser().absolute()
    path = source.resolve(strict=True)
    if not path.is_file():
        raise AssistantUnavailable('editor command must name a regular executable')
    return {'source': str(source), 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _config_identity(path):
    source = Path(path).expanduser().absolute()
    try:
        source.lstat()
    except FileNotFoundError:
        return {'source': str(source), 'missing': True}
    except OSError as exc:
        raise AssistantUnavailable('selected Codex configuration cannot be inspected') from exc
    try:
        return _file(source)
    except (OSError, ValueError, RuntimeError) as exc:
        raise AssistantUnavailable('selected Codex configuration is unavailable or not a regular file') from exc


def _command(words, environment):
    if not words:
        raise AssistantUnavailable('editor command is empty')
    executable = shutil.which(os.path.expanduser(words[0]), path=environment.get('PATH', os.defpath))
    if not executable:
        raise AssistantUnavailable('selected editor executable is missing or not executable')
    return (str(Path(executable).absolute()), *words[1:])



def _reason(reason, environment, settings, *, secrets=(), tail=None):
    # Match URLs on the original text, before a short credential can alter the
    # scheme. Replacement markers are never fed through a second replacement.
    values = {**environment, 'assistant_api_key': settings.get('api_key', '')}
    text = redact_secrets(reason or 'selected editor is not configured', values, secrets=secrets, scrub_urls=True)
    if tail is not None:
        text = text.strip()[-tail:]
    return text[:800]


def redact_secrets(text, environment, *, secrets=(), scrub_urls=False):
    text = str(text or '')
    values = {str(value) for value in secrets if value}
    for name, value in environment.items():
        if not value:
            continue
        sensitive = any(marker in name.upper() for marker in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))
        if name.upper().endswith('PROXY'):
            try:
                sensitive = sensitive or bool(urlparse(str(value)).username or urlparse(str(value)).password)
            except ValueError:
                sensitive = True
        if sensitive:
            values.add(str(value))
    patterns = [r'(?P<url>https?://\S+)'] if scrub_urls else []
    if values:
        escaped = '|'.join(re.escape(value) for value in sorted(values, key=lambda value: (-len(value), value)))
        patterns.append('(?P<secret>' + escaped + ')')
    if not patterns:
        return text
    return re.sub('|'.join(patterns),
                  lambda match: '[endpoint]' if match.lastgroup == 'url' else '***', text)


def declared_secrets(declaration, environment, settings=None):
    name = str(declaration.get('api_key_env') or '')
    selected = (settings or {}).get('api_key') or environment.get(name, '')
    return (str(selected),) if name and selected else ()


@dataclass(frozen=True)
class ProcessOutput:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class AssistantBinding:
    kind: str
    command: tuple[str, ...]
    sha256: str
    resumable: bool
    custom_codex: bool = False
    details: dict = field(default_factory=dict, repr=False, compare=False)

    def summary(self):
        notice = ('Resumes sessions scoped to this Codex installation.' if self.resumable else
                  'Saved sessions are kept but not resumed: this command has no complete runtime binding.'
                  if self.kind == 'codex' else 'DSH edits are one-shot; no Codex session is reused.')
        return {'kind': self.kind, 'binding_sha256': self.sha256,
                'protocol': 'codex-json' if self.kind == 'codex' else 'dsh-text',
                'session_supported': self.resumable, 'notice': notice}

    def recheck(self):
        config = self.details.get('config')
        if config is not None and _config_identity(config['source']) != config:
            raise AssistantUnavailable('selected Codex configuration changed after admission; retry with a fresh binding')
        if self.kind == 'codex' and self.details.get('record'):
            _provider('codex/codex_runtime.py').check_files(self.details['record'])
        for recorded in self.details.get('files', []):
            if _file(recorded.get('source', recorded['path'])) != recorded:
                raise AssistantUnavailable('selected editor runtime changed; retry with a fresh binding')
        if self.kind == 'dsh':
            current = _provider('deepseek-harness/dsh_runtime.py').inventory(
                [Path(path) for path in self.details['bindings']])
            if current != self.details['inventory']:
                raise AssistantUnavailable('selected DSH runtime changed; retry with a fresh binding')

    @contextmanager
    def execution(self, target, environment, assistant):
        if self.kind == 'codex':
            clean = {key: value for key, value in environment.items()
                     if key not in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH')}
            yield clean, None
            return
        # The bundled launcher writes a fixed pin beside cwd; use its native
        # runtime/declared flags with an owned pin instead of a shared parent file.
        with tempfile.TemporaryDirectory(prefix='hg-workbench-dsh-') as temporary:
            workspace = Path(temporary)
            home = workspace / 'home'; home.mkdir()
            profiles = home / 'profiles'; profiles.mkdir(mode=0o700)
            farm = profiles / 'node_modules'; farm.mkdir(mode=0o700)
            for name, destination in self.details['farm']:
                link = farm / name
                link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                link.symlink_to(destination, target_is_directory=True)
            keep = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'LC_CTYPE',
                    'TZ', 'TERM', 'SHELL', 'USER', 'LOGNAME', 'HTTP_PROXY', 'HTTPS_PROXY',
                    'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'}
            key_name = self.details.get('api_key_env')
            env = {key: value for key, value in environment.items() if key in keep or key == key_name}
            if key_name and assistant.get('api_key'):
                env[key_name] = assistant['api_key']
            user_home = workspace / 'user'; user_home.mkdir(mode=0o700)
            temporary = workspace / 'tmp'; temporary.mkdir(mode=0o700)
            env.update(DSH_HOME=str(home), HOME=str(user_home), TMPDIR=str(temporary))
            pin = workspace / 'model.yml'
            # JSON is a YAML subset accepted by the native --patch contract.
            provider = self.details['provider']
            rows = []
            if self.details.get('base_url'):
                provider = 'harnessgrad-workbench'
                rows.append({'id': 'llm-pi-ai', 'config': {'providers': {provider: {
                    'displayName': 'Workbench editor', 'apiKeyEnv': key_name,
                    'api': 'openai-completions', 'baseURL': self.details['base_url'],
                    'models': [{'id': self.details['model'], 'name': self.details['model'],
                                'contextWindow': 128000, 'maxTokens': 8192}]}}}})
            rows.append({'id': 'agent-default-model', 'config': {
                'provider': provider, 'model': self.details['model']}})
            with os.fdopen(os.open(pin, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump(rows, stream)
            yield env, pin

    def invocation(self, target, prompt, thread, pin):
        if self.kind == 'dsh':
            return [*self.command, '--profile', self.details['profile'], '--patch', str(pin), prompt], None
        if self.custom_codex:
            prefix = list(self.command)
        else:
            prefix = [*self.command, 'exec']
            if thread:
                prefix += ['resume', thread]
            prefix += ['--json', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox']
        return prefix + ['-C', str(target), '-'], prompt

    def response(self, done):
        if self.kind == 'dsh':
            return done.stdout or '', ''
        said, thread, recognized = '', '', False
        for line in (done.stdout or '').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') in ('thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                                      'item.started', 'item.completed', 'error'):
                recognized = True
            if event.get('type') == 'thread.started':
                thread = str(event.get('thread_id') or thread)
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'agent_message' and item.get('text'):
                said = str(item['text']); recognized = True
        if done.returncode == 0 and not recognized:
            raise AssistantProtocolError('configured Codex driver did not return the declared JSON event protocol')
        return said, thread if self.resumable else ''


def _codex_binding(environment, assistant, registry_resolver, override, custom, model):
    kind = 'codex'
    helper = _provider('codex/codex_runtime.py')
    key_name = ''
    if custom:
        command = tuple(helper.select(environment, command=override))
    elif environment.get('HG_ASSISTANT_CODEX'):
        command = tuple(helper.select(environment, codex=environment['HG_ASSISTANT_CODEX']))
    else:
        resolved = registry_resolver('codex', model)
        if (not resolved.get('ok') or resolved.get('kind') != 'cli'
                or resolved.get('argv_style') not in (None, '', 'codex') or not resolved.get('path')):
            raise AssistantUnavailable(resolved.get('reason') or 'Codex requires a declared codex editor command',
                secrets=declared_secrets(resolved, environment, assistant))
        key_name = str(resolved.get('api_key_env') or '')
        command = tuple(helper.select(environment, codex=resolved['path']))
    files = [_file(command[0])]
    for word in command[1:]:
        try:
            if Path(word).is_file():
                files.append(_file(word))
        except OSError:
            pass
    # Complete native/npm closures can resume safely. Legacy opaque commands
    # keep their execution protocol, but cannot claim a bound external runtime.
    try:
        record = helper.installation(list(command))
    except ValueError:
        record = None
    resumable = bool(record and record.get('cacheable') is True and not custom)
    codex_home = Path(environment.get('CODEX_HOME') or Path(environment.get('HOME') or Path.home()) / '.codex').expanduser()
    if not codex_home.is_absolute():
        raise AssistantUnavailable('CODEX_HOME must name an absolute editor home')
    config = codex_home / 'config.toml'
    config_identity = _config_identity(config)
    payload = {'kind': kind, 'command': command, 'files': files, 'record': record,
               'home': str(codex_home), 'config': config_identity,
               'model': model, 'base_url': assistant.get('base_url', '')}
    binding = AssistantBinding(kind, command, _digest(payload), resumable, custom,
                               {'record': record, 'files': files, 'codex_home': str(codex_home), 'config': config_identity, 'api_key_env': key_name})
    return binding


def _dsh_binding(environment, assistant, registry_resolver, override, custom, model):
    kind = 'dsh'
    resolved = registry_resolver('dsh', model)
    if not resolved.get('ok') or resolved.get('kind') != 'cli' or resolved.get('argv_style') != 'dsh':
        raise AssistantUnavailable(resolved.get('reason') or 'DSH requires a declared dsh argv_style',
            secrets=declared_secrets(resolved, environment, assistant))
    runtime = resolved.get('dsh_runtime') or {}
    if not all(runtime.get(key) for key in ('root', 'node', 'entrypoint')) or not resolved.get('home'):
        raise AssistantUnavailable('DSH requires the prepared editor root, Node and role-specific home in the improver registry')
    if custom:
        supplied = _command(shlex.split(override), environment)
        if supplied != _command([resolved['path']], environment):
            raise AssistantUnavailable('custom DSH commands must be declared in the improver registry; no cross-driver command fallback')
    if assistant.get('base_url') and assistant['base_url'] != resolved.get('base_url'):
        raise AssistantUnavailable('DSH endpoint belongs to its declared editor profile; configure that profile instead of an unrelated assistant URL')
    home = Path(resolved['home']).resolve(strict=True)
    if not home.is_dir():
        raise AssistantUnavailable('prepared DSH editor home is missing')
    command = (str(Path(runtime['node']).resolve(strict=True)), str(Path(runtime['entrypoint']).resolve(strict=True)))
    if not os.access(command[0], os.X_OK):
        raise AssistantUnavailable('declared DSH Node is not executable')
    provider = _provider('deepseek-harness/dsh_runtime.py')
    bindings = [str(Path(runtime['root']).resolve(strict=True)), command[0]]
    inventory = provider.inventory([Path(path) for path in bindings])
    details = {key: str(resolved.get(key) or '') for key in ('api_key_env', 'home', 'provider', 'profile', 'model', 'base_url', 'wire')}
    if not details['provider'] or not details['profile'] or not details['model']:
        raise AssistantUnavailable('DSH editor provider/profile/model declaration is incomplete')
    if details['profile'] != 'headless':
        raise AssistantUnavailable('DSH workbench currently supports the declared headless editor profile')
    if details['base_url']:
        endpoint = urlparse(details['base_url'])
        if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or any(ord(char) < 32 for char in details['base_url'])):
            raise AssistantUnavailable('declare a credential-free HTTP(S) DSH editor base_url')
    if details['wire'] not in ('', 'chat-completions', 'openai-completions'):
        raise AssistantUnavailable('DSH custom editor route requires the declared openai-completions wire protocol')
    if not details['base_url'] and (details['provider'] != 'deepseek-official'
            or details['api_key_env'] != 'DEEPSEEK_API_KEY'):
        raise AssistantUnavailable('declare an editor base_url/key route for a custom DSH provider')
    if not details['api_key_env'] or not (assistant.get('api_key') or environment.get(details['api_key_env'])):
        raise AssistantUnavailable('configure the selected DSH editor API key in assistant settings or its declared key environment')
    details['farm'] = _provider('deepseek-harness/dsh_runtime.py')._farm(
        ROOT / 'base_harness/deepseek-harness', Path(runtime['root']))
    details.update(bindings=bindings, inventory=inventory, files=[_file(path) for path in command])
    binding = AssistantBinding(kind, command, _digest({'kind': kind, 'command': command, 'details': details}), False, details=details)
    return binding


def resolve(kind, environment, assistant, *, registry_resolver):
    if kind not in SUPPORTED:
        raise AssistantUnavailable(f'{kind}: no workbench editor driver is available; task wrappers are not editor drivers')
    override = str(environment.get('HG_CONSOLE_ASSISTANT_CMD') or '').strip()
    owner = str(environment.get('HG_CONSOLE_ASSISTANT_DRIVER') or 'codex')
    if override and owner not in SUPPORTED:
        raise AssistantUnavailable('HG_CONSOLE_ASSISTANT_DRIVER must declare codex or dsh for the command protocol')
    custom = bool(override and owner == kind)
    model = str(assistant.get('model') or '')
    builder = _codex_binding if kind == 'codex' else _dsh_binding
    return builder(environment, assistant, registry_resolver, override, custom, model)


def capabilities(environment, assistant, *, binding_resolver):
    rows = []
    for kind in (*SUPPORTED, *UNAVAILABLE):
        row = {'kind': kind, 'supported': kind in SUPPORTED, 'configured': False,
               'session_supported': False}
        if kind in UNAVAILABLE:
            row['detail'] = 'Unavailable as a workbench editor; this task wrapper has no editor protocol.'
        else:
            try:
                binding = binding_resolver(kind, environment, assistant)
                row.update(configured=True, session_supported=binding.resumable)
                row['detail'] = binding.summary()['notice']
            except (OSError, ValueError) as exc:
                row['detail'] = _reason(exc, environment, assistant,
                    secrets=exc.secrets if isinstance(exc, AssistantUnavailable) else ())
        rows.append(row)
    return {'assistants': rows}
