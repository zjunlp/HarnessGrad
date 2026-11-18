"""Read-only executable probing; never reads a daily Codex home or transcript."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import tempfile

from harnessgrad.core.readiness import Readiness


@dataclass(frozen=True)
class AdapterCapabilities:
    version: str | None
    checks: tuple[Readiness, ...]

    def as_dict(self) -> dict:
        return {'adapter': 'codex', 'version': self.version,
                'checks': [check.as_dict() for check in self.checks]}


def version_output(executable: Path) -> str:
    # Explicit binary only, private startup environment, no account/config reuse.
    with tempfile.TemporaryDirectory(prefix='hg-personal-version-') as directory:
        env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'PATH') if key in os.environ}
        env.update(HOME=directory, USERPROFILE=directory, CODEX_HOME=directory,
                   APPDATA=directory, LOCALAPPDATA=directory, TMP=directory, TEMP=directory)
        result = subprocess.run([str(executable), '--version'], env=env, cwd=directory,
                                capture_output=True, timeout=5,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0 or len(result.stdout) > 4096 or len(result.stderr) > 4096:
            raise ValueError('Codex version probe failed')
        return result.stdout.decode('utf-8').strip()


class CodexAdapter:
    def probe(self, executable: Path | None, *, managed_hooks: bool = False,
              read_version=version_output) -> AdapterCapabilities:
        version = None
        status, detail = 'unavailable', 'No explicit native Codex executable was selected.'
        if executable is not None:
            try:
                path = executable.expanduser()
                if path.is_symlink() or not path.is_file():
                    raise FileNotFoundError
                output = read_version(path.resolve())
                match = re.fullmatch(r'codex-cli (\d+\.\d+\.\d+)(?:[ -].*)?', output)
                if not match:
                    raise ValueError('unrecognized version')
                version = match[1]
                parts = tuple(int(p) for p in version.split('.'))
                status = 'ready' if parts >= (0, 154, 0) else 'unavailable'
                detail = ('Executable version measured; protocol and permissions need separate checks.'
                          if status == 'ready' else 'Older than the stage-0 reference; protocol support unverified.')
            except FileNotFoundError:
                detail = 'Selected native Codex executable is missing or a link.'
            except PermissionError:
                status, detail = 'refused', 'Permission denied for the selected version probe.'
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
                status, detail = 'invalid', 'Selected executable could not provide a bounded Codex version.'
        checks = (
            Readiness(status, 'codex.executable_version', detail),
            Readiness('ready', 'personal.synthetic_import', 'Only explicitly imported synthetic sessions.'),
            Readiness('unchecked', 'codex.existing_session_observation', 'No daily session discovery or hook installed.'),
            Readiness('refused' if managed_hooks else 'unchecked', 'codex.hooks',
                      'Managed-hook policy declared; no registration attempted.' if managed_hooks else 'Native trust not inspected or granted.'),
            Readiness('refused', 'codex.harness_writes', 'Stage A is read-only; no install or apply entry point.'),
        )
        return AdapterCapabilities(version, checks)
