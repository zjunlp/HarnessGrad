"""Per-request workbench edits and the assistant thread-file owner.

No server imports or request-global flags. Process execution, settings, thread
storage and snapshots are explicit capabilities; the service owns only its copy
and the helper call it starts. Existing continuation/write behavior is retained.
"""
from __future__ import annotations
import difflib
import json
import os
import pathlib
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from contextlib import contextmanager
import tempfile
import threading
if __package__:
    from .candidates import valid_workbench_candidate_name
    from .record_reader import read_json
    from . import assistant_drivers
else:
    from candidates import valid_workbench_candidate_name
    from record_reader import read_json
    import assistant_drivers

ASSISTANT_KINDS = assistant_drivers.SUPPORTED + assistant_drivers.UNAVAILABLE


@dataclass(frozen=True)
class WorkbenchRequest:
    name: str
    instruction: str
    selection: str = ''
    kind: str = 'codex'
    new_thread: bool = False


class ThreadStore:
    """Versioned binding scopes; legacy candidate-only sessions are Codex-only."""
    _process_lock = threading.RLock()
    _metadata = 'workbench.sessions.v2/metadata'

    def __init__(self, path):
        self.path = path

    def read(self):
        data = read_json(self.path)
        return data if isinstance(data, dict) else {}

    @contextmanager
    def locked(self):
        with self._process_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (self.path.with_name(self.path.name + '.lock')).open('a+b') as handle:
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield self.read()
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    def write(self, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', dir=self.path.parent,
                    prefix='.' + self.path.name + '-', delete=False, encoding='utf-8') as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(data, ensure_ascii=False, indent=1) + '\n')
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remember(self, candidate, thread):
        with self.locked() as data:
            data[candidate] = thread
            self.write(data)

    def _state(self, data):
        state = data.get(self._metadata)
        if state is None:
            state = {'version': 2, 'legacy_owner': {}, 'sessions': {}}
            data[self._metadata] = state
        if (not isinstance(state, dict) or state.get('version') != 2
                or not isinstance(state.get('legacy_owner'), dict) or not isinstance(state.get('sessions'), dict)):
            raise ValueError('assistant session binding metadata is invalid')
        return state

    def _scope(self, candidate, binding):
        return json.dumps([candidate, binding.kind, binding.sha256], separators=(',', ':'))

    def session(self, candidate, binding, *, new_thread=False):
        if not binding.resumable or new_thread:
            return None
        with self.locked() as data:
            state = self._state(data)
            scoped = state['sessions'].get(self._scope(candidate, binding))
            if isinstance(scoped, str) and scoped:
                return scoped
            # Claim a legacy session only once, for the first complete Codex
            # binding. A changed binary/runtime must never reclaim that legacy key.
            legacy = data.get(candidate)
            if binding.kind == 'codex' and isinstance(legacy, str) and legacy:
                owner = state['legacy_owner'].get(candidate)
                if owner is None:
                    state['legacy_owner'][candidate] = binding.sha256
                    state['sessions'][self._scope(candidate, binding)] = legacy
                    self.write(data)
                    return legacy
            return None

    def remember_session(self, candidate, binding, thread):
        if not binding.resumable or not thread:
            return
        with self.locked() as data:
            state = self._state(data)
            state['sessions'][self._scope(candidate, binding)] = thread
            if binding.kind == 'codex':
                state['legacy_owner'].setdefault(candidate, binding.sha256)
                data[candidate] = thread  # Retain legacy readers without sharing other scopes.
            self.write(data)


def assistant_argv(env: dict, thread: str | None = None) -> list[str]:
    """Use configured argv, otherwise Codex exec with optional saved-thread resume.

    These CLI flags do not establish an assistant sandbox.
    """
    configured = shlex.split(env.get("HG_CONSOLE_ASSISTANT_CMD", ""))
    if configured:
        return configured
    codex = env.get("HG_ASSISTANT_CODEX") or shutil.which("codex")
    if not codex:
        raise ValueError("没有可用的助手 CLI：设置 HG_CONSOLE_ASSISTANT_CMD，或把 codex 装上")
    common = ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
    if thread:
        return [codex, "exec", "resume", thread, *common]
    # Persist sessions so later requests can resume the recorded thread.
    return [codex, "exec", *common]


def snapshot(root: pathlib.Path) -> dict[str, str]:
    """每个文件的当前内容，用来算"这一句到底改了什么"。"""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".json", ".md", ".txt", ".sh", ".yaml", ".yml"):
            try:
                out[str(path.relative_to(root))] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


class WorkbenchRejected(Exception):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload


def validate_request(root, name, instruction, kind):
    if kind not in ASSISTANT_KINDS:
        raise WorkbenchRejected(400, {"error": "unknown-assistant", "detail": f"可用的助手：{list(ASSISTANT_KINDS)}"})
    if not isinstance(instruction, str) or not instruction.strip():
        raise WorkbenchRejected(400, {"error": "no-instruction"})
    source = root / str(name)
    if (not valid_workbench_candidate_name(name) or source.is_symlink()
            or not (source / "harness.json").is_file()):
        raise WorkbenchRejected(404, {"error": "candidate-not-found", "detail": str(name)})
    return source


def prepare_copy(root, name, instruction, kind):
    source = validate_request(root, name, instruction, kind)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = root / f"{name}-edit-{stamp}"
    if target.exists():
        raise WorkbenchRejected(409, {"error": "duplicate-candidate", "detail": str(target)})
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return target


def run_turn(run_process, binding, target, env, pin, text, thread):
    binding.recheck()
    argv, stdin = binding.invocation(target, text, thread, pin)
    try:
        done = run_process(argv, input=stdin, cwd=str(target), capture_output=True,
                           text=True, timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        return None, '', ''
    binding.recheck()
    # Parse the declared wire protocol before redacting display text: even a
    # short local placeholder key must not rewrite JSON event/field names.
    said, thread = binding.response(done)
    if done.returncode != 0:
        # failed_turn owns the one diagnostic scrub/clip pass on original text.
        return assistant_drivers.ProcessOutput(done.returncode, '', done.stderr or ''), said, thread
    secrets = assistant_drivers.declared_secrets(binding.details, env)
    said = assistant_drivers.redact_secrets(said, env, secrets=secrets)
    if binding.kind == 'dsh':
        said = said.strip()[-8000:]
    safe = assistant_drivers.ProcessOutput(done.returncode, '',
        assistant_drivers.redact_secrets(done.stderr, env, secrets=secrets))
    return safe, said, thread



class WorkbenchService:
    def __init__(self, root, *, threads, load_settings, binding_resolver, run_process, snapshot):
        self.root = root
        self.threads, self.load_settings = threads, load_settings
        self.binding_resolver, self.run_process, self.snapshot = binding_resolver, run_process, snapshot

    def edit(self, request: WorkbenchRequest, *, environment):
        name, instruction, selection, kind = request.name, request.instruction, request.selection, request.kind
        settings = {}
        try:
            validate_request(self.root, name, instruction, kind)
            settings = self.load_settings()['assistant']
            binding = self.binding_resolver(kind, environment, settings)
            binding.recheck()
        except WorkbenchRejected as exc:
            return exc.status, exc.payload
        except (ValueError, OSError) as exc:
            return 503, {'error': 'assistant-not-configured',
                         'detail': assistant_drivers._reason(exc, environment, settings,
                             secrets=exc.secrets if isinstance(exc, assistant_drivers.AssistantUnavailable) else ())}
        try:
            target = prepare_copy(self.root, name, instruction, kind)
        except WorkbenchRejected as exc:
            return exc.status, exc.payload
        before = self.snapshot(target)
        env = dict(environment)
        for key, variable in (('base_url', 'HG_AGENT_BASE_URL'), ('model', 'HG_AGENT_MODEL'), ('api_key', 'HG_AGENT_API_KEY')):
            if settings.get(key):
                env[variable] = settings[key]
        prompt = instruction.strip()
        if selection.strip():
            prompt += ('\n\n下面是这次要看的范围（行号是文件里的真实行号，不要改动范围之外的内容）：\n' + selection.strip())
        try:
            thread = self.threads.session(str(name), binding, new_thread=request.new_thread)
            resumed = thread is not None
            with binding.execution(target, env, settings) as (child_env, pin):
                done, said, tid = run_turn(self.run_process, binding, target, child_env, pin, prompt, thread)
                if done is None:
                    return 504, {'error': 'assistant-timeout', 'candidate': target.name, 'assistant': binding.summary()}
                if done.returncode != 0:
                    return self.failed_turn(done, target, binding, said, environment, settings)
                if tid:
                    self.threads.remember_session(str(name), binding, tid)
                    thread = tid
                continued = False
                if self.snapshot(target) == before and done.returncode == 0:
                    done2, said2, tid2 = run_turn(self.run_process, binding, target, child_env, pin,
                        '继续执行：不要再复述计划，直接在仓库里改文件，改完用一句话说明改了什么。', thread)
                    if done2 is None:
                        return 504, {'error': 'assistant-timeout', 'candidate': target.name, 'assistant': binding.summary()}
                    if done2.returncode != 0:
                        return self.failed_turn(done2, target, binding, said2, environment, settings)
                    if done2.returncode == 0:
                        continued = True
                        if said2:
                            said = said2
                        if tid2:
                            self.threads.remember_session(str(name), binding, tid2)
                            thread = tid2
            code, payload = self.finish(name, target, before, done, said, continued, thread)
            payload['assistant'] = {**binding.summary(), 'session_resumed': resumed}
            return code, payload
        except assistant_drivers.AssistantProtocolError as exc:
            return 502, {'error': 'assistant-protocol', 'detail': str(exc), 'candidate': target.name}
        except (ValueError, OSError) as exc:
            return 503, {'error': 'assistant-runtime',
                         'detail': assistant_drivers._reason(exc, environment, settings), 'candidate': target.name}

    def failed_turn(self, done, target, binding, said, environment, settings):
        return 422, {'error': 'assistant-exit', 'exit': done.returncode, 'candidate': target.name,
                     'detail': assistant_drivers._reason(done.stderr or said or 'Editor process failed', environment, settings,
                         secrets=assistant_drivers.declared_secrets(binding.details, environment, settings),
                         tail=8000 if binding.kind == 'dsh' and not done.stderr else None),
                     'assistant': binding.summary()}

    def finish(self, name, target, before, done, said, continued, thread):
        after = self.snapshot(target)
        diff, changed = [], []
        for path in sorted(set(before) | set(after)):
            if before.get(path) == after.get(path):
                continue
            changed.append(path)
            diff.extend(difflib.unified_diff((before.get(path) or "").splitlines(True),
                                             (after.get(path) or "").splitlines(True),
                                             fromfile=f"a/{path}", tofile=f"b/{path}", n=2))
        if not changed:
            shutil.rmtree(target, ignore_errors=True)
            return 200, {"candidate": None, "changed": [], "diff": "",
                         "message": said or "助手没有改动任何文件",
                         "stderr_tail": (done.stderr or "")[-600:]}
        # Report syntax for changed Python files without executing or accepting the edit.
        syntax = {}
        for path in changed:
            if path.endswith(".py"):
                try:
                    compile((target / path).read_text(encoding="utf-8", errors="replace"), path, "exec")
                    syntax[path] = "ok"
                except SyntaxError as exc:
                    syntax[path] = f"{exc.msg} (line {exc.lineno})"
        return 200, {"candidate": target.name, "changed": changed,
                     "diff": "".join(diff)[:20000], "exit": done.returncode,
                     "message": said, "syntax": syntax,
                     "session": thread, "continued": continued,
                     "stderr_tail": (done.stderr or "")[-600:]}
