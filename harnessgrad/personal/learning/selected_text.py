"""Validate frozen text surfaces and render exact, read-only candidate edits."""
from __future__ import annotations

from collections.abc import Mapping
import re

from harnessgrad.improvement.stages import text_edit_proposal
from harnessgrad.personal.contracts import MAX_BYTES, digest, encode, identifier
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.reflection_contracts import ArtifactPort, ArtifactRef, PortRejected, SnapshotRef


def surface_kind(path: str) -> str | None:
    if path == 'instruction.md':
        return 'instruction'
    if isinstance(path, str) and re.fullmatch(r'skills/[0-9a-f]{64}\.md', path):
        return 'skill'
    return None


def read_snapshot_files(artifacts: ArtifactPort, scope: LearningScope, snapshot: SnapshotRef) -> dict[str, str]:
    artifact = artifacts.read_artifact(scope, snapshot.snapshot_ref, kind='virtual_snapshot')
    if (artifact.ref, artifact.sha256) != (snapshot.snapshot_ref, snapshot.snapshot_sha256):
        raise PortRejected('stale_snapshot')
    if artifact.kind != 'virtual_snapshot' or artifact.truncated or set(artifact.payload) != {'files'}:
        raise PortRejected('invalid_snapshot')
    files = artifact.payload['files']
    if (not isinstance(files, Mapping) or len(files) != len(snapshot.target_ids)
            or any(surface_kind(path) is None or not isinstance(text, str) for path, text in files.items())
            or len(encode(dict(files)).encode('utf-8')) > MAX_BYTES):
        raise PortRejected('invalid_snapshot')
    return dict(files)


def register_text_candidate(
    artifacts: ArtifactPort, scope: LearningScope, snapshot: SnapshotRef, files: dict[str, str],
) -> ArtifactRef:
    expected = digest({'files': files})
    artifact = artifacts.register_candidate(scope, snapshot.snapshot_ref, files.copy())
    if (artifact.kind, artifact.sha256) != ('text_candidate', expected):
        raise PortRejected('invalid_candidate_artifact')
    identifier(artifact.ref)
    return artifact


def whole_lines(text: str) -> str:
    return '\n' + text.replace('\r\n', '\n').strip('\n') + '\n'


def has_exact_anchor(text: str, anchor: str) -> bool:
    pattern = r'(?:\A\ufeff|^)' + re.escape(anchor)
    if not anchor.endswith('\n'):
        pattern += r'(?=\r?\n|\Z)'
    return re.search(pattern, text, re.MULTILINE) is not None


def render_edit(files: dict[str, str], path: str, old: str, new: str, evidence_ref: str) -> dict[str, str]:
    edit = {'path': path, 'old': old, 'new': new}
    proposal = text_edit_proposal(files, (path,), evidence_ref, prompt='', ask=lambda _: {'edits': [edit]})
    if surface_kind(path) == 'skill':
        prefix = re.match(r'\A\ufeff?---\r?\n.*?^---(?:\r?\n|\Z)', files[path], re.MULTILINE | re.DOTALL)
        if prefix is None or not proposal.files[path].startswith(prefix[0]):
            raise PortRejected('skill_frontmatter_changed')
    return proposal.files
