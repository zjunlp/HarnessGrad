"""Maintain scoped learning items while C owns version writes and authorization."""
from __future__ import annotations

from dataclasses import asdict, replace

from harnessgrad.personal.contracts import digest, encode, identifier
from harnessgrad.personal.evidence import MAX_SELECTIONS
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.maintenance_contracts import (
    CurrentVersion, ItemState, LearningItem, LearningPage, MaintenanceArtifacts, MaintenanceDecision,
    MaintenanceRequest, OperationRef, OperationView, RollbackRequest, TaskContext, VersionEntry, VersionPort,
    VersionReceipt,
)
from harnessgrad.personal.learning.maintenance_store import (
    CandidateDraft, MaintenanceRecord, MaintenanceStore, Revision,
)
from harnessgrad.personal.learning.reflection_contracts import (
    CandidateQueue, CandidateRequest, JobRef, PortRejected, ReflectionInput, SnapshotRef,
)
from harnessgrad.personal.learning.reflection_store import ReflectionStore
from harnessgrad.personal.learning.selected_text import (
    has_exact_anchor, read_snapshot_files, register_text_candidate, render_edit, whole_lines,
)
from harnessgrad.personal.learning.store import LearningStore


def _anchor(body: str, text: str) -> str:
    canonical = text.replace('\r\n', '\n').strip('\n')
    for spelling in (canonical, canonical.replace('\n', '\r\n')):
        if spelling and has_exact_anchor(body, spelling):
            return spelling
    raise PortRejected('stale_snapshot')


def _scoped(scope: LearningScope, job: JobRef | None) -> bool:
    return job is not None and (job.profile_id, job.project_id) == scope.key()


def _rollback_base(value: Revision) -> str | None:
    receipt = value.receipt
    if (not value.rolled_back and receipt is not None
            and value.bound_version_ref in (None, receipt.after_version_ref)
            and any(entry.status == 'applied' for entry in receipt.entries)):
        return receipt.before_version_ref
    return None


def _ordered_versions(
    versions: tuple[CurrentVersion, ...], targets: tuple[str, ...],
) -> tuple[CurrentVersion, ...]:
    by_target = {version.target_id: version for version in versions}
    if len(by_target) != len(versions) or by_target.keys() != set(targets):
        raise PortRejected('invalid_version_view')
    return tuple(by_target[target] for target in targets)


def _matches_transition(before: CurrentVersion, after: CurrentVersion, entry: VersionEntry | None) -> bool:
    if entry is None:
        return before.content_sha256 == after.content_sha256
    return (before.content_sha256, after.content_sha256) == (entry.before_sha256, entry.after_sha256)


class MaintenanceService:
    """D3 entry points for item maintenance, context selection and C reconciliation."""

    def __init__(self, store: LearningStore, artifacts: MaintenanceArtifacts,
                 queue: CandidateQueue, versions: VersionPort):
        self.items = MaintenanceStore(store)
        self.reflections = ReflectionStore(store)
        self.artifacts = artifacts
        self.queue = queue
        self.versions = versions

    def track_reflection(self, request: ReflectionInput, *, legacy_text: str | None = None) -> LearningItem:
        """Import a durable D2 submission. Older proposals need an explicit, exact text anchor."""
        scope = request.scope
        key = 'item_' + digest([*scope.key(), self.reflections.key(request)])
        record = self.reflections.read(request)
        if (record is None or record.draft is None or record.draft.experience is None
                or record.draft.files is None or record.candidate is None or not _scoped(scope, record.job)):
            raise PortRejected('reflection_not_submitted')
        with self.items.lock(scope):
            if prior := self.items.head(scope, key):
                return prior.item
            experience = record.draft.experience
            text = experience.text or legacy_text
            if text is None:
                raise PortRejected('experience_anchor_required')
            text = _anchor(record.draft.files[experience.path], text)
            if experience.text is None:
                before = read_snapshot_files(self.artifacts, scope, request.snapshot)
                if experience.path not in before or whole_lines(text) in whole_lines(before[experience.path]):
                    raise PortRejected('invalid_experience_anchor')
            candidate = record.candidate
            if ((candidate.profile_id, candidate.project_id) != scope.key()
                    or candidate.target_ids != request.snapshot.target_ids
                    or candidate.evidence_refs != request.evidence_refs):
                raise PortRejected('invalid_reflection_receipt')
            item = LearningItem(key, 1, experience.kind, 'proposed', text, candidate.target_ids,
                                candidate.evidence_refs, candidate.conditions, record.job)
            revision = Revision(item, candidate, experience.path, experience.topic_id, experience.criterion)
            self.items.append(scope, (revision,))
            return item

    def list_learning_items(self, scope: LearningScope, *, after: str | None = None) -> LearningPage:
        prefix = 'items_' + digest(scope.key())[:24] + ':'
        if after is not None and (not isinstance(after, str) or not after.startswith(prefix)):
            raise PortRejected('invalid_cursor')
        position = identifier(after[len(prefix):]) if after is not None else ''
        page = tuple(value.item for value in self.items.heads(scope, after=position, limit=51))
        results = page[:50]
        return LearningPage(results, prefix + results[-1].item_id if results else after, len(page) > 50)

    def read_history(self, scope: LearningScope, item_id: str) -> tuple[LearningItem, ...]:
        return tuple(value.item for value in self.items.history(scope, identifier(item_id)))

    def _current(self, scope: LearningScope, targets: tuple[str, ...]) -> tuple[CurrentVersion, ...]:
        return _ordered_versions(self.versions.read_current_versions(scope, targets), targets)

    def _package(self, scope: LearningScope, targets: tuple[str, ...], ref: str | None) -> tuple[CurrentVersion, ...]:
        if ref is None:
            raise PortRejected('invalid_version_receipt')
        return _ordered_versions(self.versions.read_version_targets(scope, ref, targets), targets)

    def _check_binding(self, scope: LearningScope, value: Revision) -> None:
        if not value.versions or value.receipt is None:
            raise PortRejected('version_conflict')
        # Older local records pinned current IDs based on hashes alone.
        if not value.versions_verified:
            recorded = self._package(scope, value.item.target_ids, value.receipt.after_version_ref)
            if recorded != value.versions:
                raise PortRejected('version_conflict')

    def select_context(self, scope: LearningScope, context: TaskContext) -> tuple[LearningItem, ...]:
        targets, tags = set(context.target_ids), set(context.condition_tags)
        selected = tuple(value for value in self.items.heads(scope)
                         if value.item.state == 'active' and value.versions
                         and set(value.item.target_ids) <= targets
                         and set(value.item.conditions) <= tags)
        if not selected:
            return ()
        selected_targets = tuple(dict.fromkeys(target for value in selected for target in value.item.target_ids))
        try:
            current = {version.target_id: version for version in self._current(scope, selected_targets)}
        except (PortRejected, OSError):
            return ()
        eligible = []
        for value in selected:
            if any(current[version.target_id] != version for version in value.versions):
                continue
            try:
                self._check_binding(scope, value)
            except (PortRejected, OSError):
                continue
            eligible.append(value.item)
        # Re-read heads after C's lookup so a concurrent disable cannot revive an old selection.
        heads = {value.item.item_id: value.item.revision for value in self.items.heads(scope)}
        return tuple(item for item in eligible if heads.get(item.item_id) == item.revision)

    def request_maintenance(self, request: MaintenanceRequest) -> MaintenanceDecision:
        scope = request.scope
        with self.items.lock(scope):
            prior = self.items.action(scope, 'request_key', request.idempotency_key)
            if prior is not None:
                if prior.request != request:
                    raise PortRejected('idempotency_conflict')
                return self._prepare_candidate(prior)
            decision_id = 'maintenance_' + digest([*scope.key(), request.idempotency_key])
            updates = ()
            try:
                values = tuple(self._item(scope, item.item_id) for item in request.items)
                if any(value.item.revision != ref.expected_revision for value, ref in zip(values, request.items)):
                    raise PortRejected('revision_conflict')
                record, updates = self._plan(request, decision_id, values)
            except PortRejected as exc:
                record = MaintenanceRecord(request, MaintenanceDecision(decision_id, 'conflict',
                                                                        reason_code=exc.reason_code),
                                           request.items[0].expected_revision)
            self.items.save_action(record, updates)
            return self._prepare_candidate(record)

    def _plan(self, request: MaintenanceRequest, decision_id: str,
              values: tuple[Revision, ...]) -> tuple[MaintenanceRecord, tuple[Revision, ...]]:
        value = values[0]
        decision = MaintenanceDecision(decision_id, 'recorded')
        if request.action == 'correct':
            correction = self._correction(request, value)
            status = 'needs_candidate' if correction else 'no_change'
            return MaintenanceRecord(request, replace(decision, status=status), value.item.revision, correction), ()
        if request.action == 'merge':
            merged = self._merge(request.scope, values)
            outcome = decision if merged else replace(decision, status='no_change')
            return MaintenanceRecord(request, outcome, merged[0].item.revision if merged else value.item.revision), merged
        if request.action == 'restore':
            if value.rolled_back:
                draft = self._restore_candidate(request.scope, value)
                return MaintenanceRecord(request, replace(decision, status='needs_candidate'),
                                         value.item.revision, draft), ()
            updated = self._restore(request.scope, value)
        else:
            state = 'disabled' if request.action == 'disable' else 'archived'
            updated = self._retire(request.scope, value, state)
            if updated is not None and (restore_version := _rollback_base(value)) is not None:
                decision = replace(decision, status='needs_rollback', job=value.item.job,
                                   restore_version_ref=restore_version)
        if updated is None:
            return MaintenanceRecord(request, replace(decision, status='no_change'), value.item.revision), ()
        return MaintenanceRecord(request, decision, updated.item.revision), (updated,)

    def _retire(self, scope: LearningScope, value: Revision, state: ItemState) -> Revision | None:
        if value.item.state == state:
            return None
        if value.item.state == 'archived':
            raise PortRejected('item_archived')
        self._check_rollback_pending(scope, value)
        return value.next(state=state)

    def _restore(self, scope: LearningScope, value: Revision) -> Revision | None:
        if value.item.state in ('active', 'proposed'):
            return None
        if value.merged_into is not None:
            raise PortRejected('merged_item')
        self._check_rollback_pending(scope, value)
        if value.versions:
            self._check_current(scope, value)
            return value.next(state='active')
        if value.receipt is not None:
            raise PortRejected('restore_requires_review')
        return value.next(state='proposed')

    def _check_rollback_pending(self, scope: LearningScope, value: Revision) -> None:
        for action in self.items.rollbacks(scope, value.item.item_id):
            if action.revision == value.item.revision and (
                    action.rollback is None or self._operation(action.rollback).status in
                    ('queued', 'running', 'rolled_back')):
                raise PortRejected('rollback_pending')

    def _merge(self, scope: LearningScope, values: tuple[Revision, ...]) -> tuple[Revision, ...]:
        first, *others = values
        if all(value.merged_into == first.item.item_id for value in others):
            return ()
        identity = (first.item.kind, first.path, first.item.target_ids, frozenset(first.item.conditions),
                    whole_lines(first.item.summary))
        for value in values:
            other = (value.item.kind, value.path, value.item.target_ids, frozenset(value.item.conditions),
                     whole_lines(value.item.summary))
            if other != identity or value.item.state != 'active' or value.versions != first.versions:
                raise PortRejected('merge_requires_review')
            self._check_binding(scope, value)
        self._check_current(scope, first)
        writers = [value for value in values if _rollback_base(value) is not None]
        if len({_rollback_base(value) for value in writers}) > 1:
            raise PortRejected('merge_requires_review')
        binding = writers[0] if writers else first
        evidence = tuple(dict.fromkeys(ref for value in values for ref in value.item.evidence_refs))
        if len(evidence) > MAX_SELECTIONS:
            raise PortRejected('evidence_limit')
        kept = first.next(evidence_refs=evidence, job=binding.item.job,
                          version_receipt_ref=binding.item.version_receipt_ref)
        return (replace(kept, candidate=binding.candidate, receipt=binding.receipt, versions_verified=True,
                        bound_version_ref=binding.bound_version_ref),
                *(replace(value.next(state='archived'), merged_into=first.item.item_id) for value in others))

    def _correction(self, request: MaintenanceRequest, value: Revision) -> CandidateDraft | None:
        text = request.correction
        assert text is not None
        if value.item.state != 'active':
            raise PortRejected('item_not_active')
        self._check_current(request.scope, value)
        if whole_lines(value.item.summary) == whole_lines(text):
            return None
        snapshot, files = self._freeze(request.scope, value)
        if value.path not in files:
            raise PortRejected('stale_snapshot')
        old = _anchor(files[value.path], value.item.summary)
        if whole_lines(text) in whole_lines(files[value.path]):
            raise PortRejected('duplicate_existing_experience')
        try:
            updated = render_edit(files, value.path, old, text, value.item.evidence_refs[0])
        except PortRejected:
            raise
        except ValueError as exc:
            raise PortRejected('invalid_correction') from exc
        return CandidateDraft(snapshot, updated)

    def _restore_candidate(self, scope: LearningScope, value: Revision) -> CandidateDraft:
        if value.merged_into is not None:
            raise PortRejected('merged_item')
        snapshot, files = self._freeze(scope, value)
        candidate = value.candidate
        artifact = self.artifacts.read_artifact(scope, candidate.candidate_ref, kind='text_candidate')
        if (artifact.ref != candidate.candidate_ref or artifact.kind != 'text_candidate' or artifact.truncated
                or artifact.sha256 != candidate.candidate_sha256 or set(artifact.payload) != {'files'}
                or digest(artifact.payload) != candidate.candidate_sha256):
            raise PortRejected('invalid_candidate_artifact')
        restored = artifact.payload['files']
        if (not isinstance(restored, dict) or restored.keys() != files.keys()
                or {path for path in files if restored[path] != files[path]} != {value.path}):
            raise PortRejected('restore_requires_review')
        _anchor(restored[value.path], value.item.summary)
        return CandidateDraft(snapshot, dict(restored))

    def _freeze(self, scope: LearningScope, value: Revision) -> tuple[SnapshotRef, dict[str, str]]:
        self._check_current(scope, value)
        snapshot = self.artifacts.freeze_snapshot(scope, value.item.target_ids)
        if snapshot.target_ids != value.item.target_ids:
            raise PortRejected('out_of_scope')
        files = read_snapshot_files(self.artifacts, scope, snapshot)
        self._check_current(scope, value)
        return snapshot, files

    def _prepare_candidate(self, record: MaintenanceRecord) -> MaintenanceDecision:
        if record.draft is None or record.decision.candidate is not None:
            return record.decision
        request = record.request
        saved = self._subject(record)
        self._check_current(request.scope, saved)
        snapshot, files = record.draft.snapshot, record.draft.files
        artifact = register_text_candidate(self.artifacts, request.scope, snapshot, files)
        candidate = replace(saved.candidate, snapshot_ref=snapshot.snapshot_ref, snapshot_sha256=snapshot.snapshot_sha256,
                            candidate_ref=artifact.ref, candidate_sha256=artifact.sha256,
                            evidence_refs=saved.item.evidence_refs, idempotency_key=record.decision.decision_id)
        self.items.record_result(record, 'candidate', encode(asdict(candidate)))
        return replace(record.decision, candidate=candidate)

    def submit_candidate(self, scope: LearningScope, decision_id: str) -> MaintenanceDecision:
        with self.items.lock(scope):
            record = self._action(scope, decision_id, 'needs_candidate')
            if record.decision.job is not None:
                return record.decision
            decision = self._prepare_candidate(record)
            saved = self._subject(record)
            assert decision.candidate is not None
            self._begin_dispatch(record, saved, decision.candidate)
            job = self.queue.submit_candidate(decision.candidate)
            if not _scoped(scope, job):
                raise PortRejected('out_of_scope')
            self.items.record_result(record, 'job_id', identifier(job.job_id))
            return replace(decision, job=job)

    def request_rollback(self, scope: LearningScope, decision_id: str, authorization_ref: str) -> OperationRef:
        identifier(authorization_ref)
        with self.items.lock(scope):
            record = self._action(scope, decision_id, 'needs_rollback')
            if record.rollback is None:
                value = self._subject(record)
            else:
                value = self.items.revision(scope, record.request.items[0].item_id, record.revision)
                if value is None or record.dispatch_sha256 is None:
                    raise PortRejected('invalid_maintenance_decision')
            job, restore = record.decision.job, record.decision.restore_version_ref
            assert job is not None and restore is not None
            request = RollbackRequest(job, restore, value.versions, authorization_ref, decision_id)
            self._begin_dispatch(record, value, request)
            if record.rollback is not None:
                return record.rollback
            try:
                operation = self.versions.request_rollback(request)
            except PortRejected as exc:
                if exc.reason_code in ('authorization_required', 'rollback_conflict'):
                    self.items.clear_rejected_rollback(record)
                raise
            if operation.job != job:
                raise PortRejected('out_of_scope')
            self.items.record_result(record, 'operation_id', identifier(operation.operation_id))
            return operation

    def _begin_dispatch(
        self, record: MaintenanceRecord, value: Revision, request: CandidateRequest | RollbackRequest,
    ) -> None:
        fingerprint = digest(asdict(request))
        if record.dispatch_sha256 is not None:
            if record.dispatch_sha256 != fingerprint:
                raise PortRejected('idempotency_conflict')
            return
        self._check_current(record.request.scope, value)
        self.items.record_result(record, 'dispatch_sha256', fingerprint)

    def reconcile_learning_item(self, scope: LearningScope, item_id: str, operation: OperationRef) -> LearningItem:
        if not _scoped(scope, operation.job):
            raise PortRejected('out_of_scope')
        with self.items.lock(scope):
            value = self._item(scope, item_id)
            earlier = self.items.operation_revision(scope, operation)
            if earlier is not None:
                if earlier.item.item_id != item_id:
                    raise PortRejected('out_of_scope')
                self.items.append(scope, self._carry_forward(scope, earlier))
                return value.item
            view = self._operation(operation)
            if (view.kind, view.status) == ('apply', 'applied'):
                updated = self._applied(scope, value, operation, view.version_receipt_ref)
            elif (view.kind, view.status) == ('rollback', 'rolled_back'):
                updated = self._rolled_back(scope, value, operation, view.version_receipt_ref)
            else:
                raise PortRejected(view.reason_code or view.status)
            self.items.append(scope, (updated, *self._carry_forward(scope, updated)), operation)
            return updated.item

    def _carry_forward(self, scope: LearningScope, changed: Revision) -> tuple[Revision, ...]:
        receipt = changed.receipt
        if receipt is None or not receipt.before_version_ref or receipt.before_version_ref == receipt.after_version_ref:
            return ()
        entries = {entry.target_id: entry for entry in receipt.entries}
        updates = []
        for value in self.items.heads(scope):
            targets = value.item.target_ids
            if (value.item.item_id == changed.item.item_id or value.item.state != 'active'
                    or not entries.keys() & set(targets)):
                continue
            try:
                self._check_binding(scope, value)
                before = self._package(scope, targets, receipt.before_version_ref)
                if before != value.versions:
                    continue
                after = self._package(scope, targets, receipt.after_version_ref)
                if not all(_matches_transition(old, new, entries.get(old.target_id))
                           for old, new in zip(before, after)):
                    continue
                if self._current(scope, targets) != after:
                    continue
                snapshot = self.artifacts.freeze_snapshot(scope, targets)
                if snapshot.target_ids != targets:
                    raise PortRejected('out_of_scope')
                files = read_snapshot_files(self.artifacts, scope, snapshot)
                _anchor(files.get(value.path, ''), value.item.summary)
                if self._current(scope, targets) != after:
                    continue
            except (PortRejected, OSError):
                continue
            updates.append(replace(value.next(), versions=after, versions_verified=True,
                                   bound_version_ref=receipt.after_version_ref))
        return tuple(updates)

    def _applied(
        self, scope: LearningScope, value: Revision, op: OperationRef, ref: str | None,
    ) -> Revision:
        candidate, text = value.candidate, value.item.summary
        existing_submission = value.item.state == 'proposed' and value.item.job == op.job
        if not existing_submission:
            record = self.items.action(scope, 'job_id', op.job.job_id)
            if (record is None or record.request.items[0].item_id != value.item.item_id
                    or record.revision != value.item.revision or record.decision.candidate is None):
                raise PortRejected('stale_operation')
            if record.request.action == 'correct' and value.item.state == 'active':
                assert record.request.correction is not None
                text = record.request.correction
            elif not (record.request.action == 'restore' and value.rolled_back):
                raise PortRejected('stale_operation')
            self._check_binding(scope, value)
            candidate = record.decision.candidate
        receipt, versions = self._receipt(scope, value, op, ref, 'applied',
                                          expected_before=None if existing_submission else value.versions)
        if receipt.candidate_sha256 != candidate.candidate_sha256:
            raise PortRejected('candidate_mismatch')
        return replace(value.next(state='active', summary=text, job=op.job, version_receipt_ref=receipt.receipt_ref),
                       candidate=candidate, versions=versions, receipt=receipt,
                       rolled_back=False, versions_verified=True, bound_version_ref=None)

    def _rolled_back(
        self, scope: LearningScope, value: Revision, op: OperationRef, ref: str | None,
    ) -> Revision:
        record = self.items.action(scope, 'operation_id', op.operation_id)
        if (record is None or record.rollback != op or record.request.items[0].item_id != value.item.item_id
                or record.revision != value.item.revision or value.item.state not in ('disabled', 'archived')
                or value.receipt is None):
            raise PortRejected('stale_operation')
        self._check_binding(scope, value)
        receipt, current = self._receipt(scope, value, op, ref, 'rolled_back', expected_before=value.versions)
        before = {entry.target_id: entry.before_sha256 for entry in value.receipt.entries}
        if any(entry.after_sha256 != before[entry.target_id] for entry in receipt.entries):
            raise PortRejected('rollback_version_mismatch')
        return replace(value.next(version_receipt_ref=receipt.receipt_ref), versions=current,
                       receipt=receipt, rolled_back=True, versions_verified=True, bound_version_ref=None)

    def _receipt(
        self, scope: LearningScope, value: Revision, op: OperationRef, ref: str | None, status: str,
        *, expected_before: tuple[CurrentVersion, ...] | None,
    ) -> tuple[VersionReceipt, tuple[CurrentVersion, ...]]:
        if ref is None:
            raise PortRejected('missing_version_receipt')
        receipt = self.versions.read_version_receipt(op.job, ref)
        entries = {entry.target_id: entry for entry in receipt.entries}
        if (receipt.operation != op or receipt.receipt_ref != ref or receipt.after_version_ref is None
                or len(entries) != len(receipt.entries) or entries.keys() != set(value.item.target_ids)
                or any(entry.status not in (status, 'unchanged')
                       or (entry.status == 'unchanged' and entry.before_sha256 != entry.after_sha256)
                       for entry in receipt.entries)):
            raise PortRejected('invalid_version_receipt')
        targets = value.item.target_ids
        after = self._package(scope, targets, receipt.after_version_ref)
        if any(entries[version.target_id].after_sha256 != version.content_sha256 for version in after):
            raise PortRejected('invalid_version_receipt')
        if expected_before is not None:
            before = self._package(scope, targets, receipt.before_version_ref)
            if before != expected_before:
                raise PortRejected('version_conflict')
            if any(entries[version.target_id].before_sha256 != version.content_sha256 for version in before):
                raise PortRejected('invalid_version_receipt')
        if self._current(scope, targets) != after:
            raise PortRejected('version_conflict')
        if status == 'applied' and any(version.version_id is None or version.content_sha256 is None for version in after):
            raise PortRejected('invalid_version_receipt')
        return receipt, after

    def _operation(self, reference: OperationRef) -> OperationView:
        view = self.versions.read_operation(reference)
        if view.operation != reference:
            raise PortRejected('out_of_scope')
        return view

    def _check_current(self, scope: LearningScope, value: Revision) -> None:
        self._check_binding(scope, value)
        if self._current(scope, value.item.target_ids) != value.versions:
            raise PortRejected('version_conflict')

    def _item(self, scope: LearningScope, item_id: str) -> Revision:
        value = self.items.head(scope, identifier(item_id))
        if value is None:
            raise PortRejected('out_of_scope')
        return value

    def _action(self, scope: LearningScope, decision_id: str, status: str) -> MaintenanceRecord:
        record = self.items.action(scope, 'id', identifier(decision_id))
        if record is None:
            raise PortRejected('out_of_scope')
        if record.decision.status != status:
            raise PortRejected('invalid_maintenance_decision')
        return record

    def _subject(self, record: MaintenanceRecord) -> Revision:
        value = self._item(record.request.scope, record.request.items[0].item_id)
        if value.item.revision != record.revision:
            raise PortRejected('revision_conflict')
        return value
