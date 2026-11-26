"""Poll one opted-in source page and durably queue eligible evidence for D2."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from typing import Callable

from harnessgrad.personal.contracts import digest, encode, identifier
from harnessgrad.personal.evidence import excerpt
from harnessgrad.personal.learning.contracts import (
    LearningPolicy,
    LearningPolicyUpdate,
    LearningScope,
    LearningSource,
    PollResult,
    SourceCapability,
    SourceConflict,
    SourceItem,
    SourcePage,
    TriggerDecision,
    TriggerInput,
)
from harnessgrad.personal.learning.store import LearningStore
from harnessgrad.personal.learning.trigger import decide_trigger, ignore_reason


def _access_issue(policy: LearningPolicy, source_id: str) -> str | None:
    if not policy.enabled:
        return 'learning_disabled'
    if source_id not in policy.source_ids:
        return 'source_revoked'
    return None


def _selected_excerpt(scope: LearningScope, item: SourceItem) -> tuple[str | None, dict | None]:
    if item.selection is None or item.own_event:
        return None, None
    ref = 'd1e_' + digest([*scope.key(), item.selection.as_dict()])
    row = {'sha256': item.event_sha256, 'body': encode(item.event.as_dict())}
    return ref, excerpt(row, item.selection)


class LearningService:
    """D1's offline entry point, with a source adapter and trusted confirmation verifier."""

    def __init__(
        self, store: LearningStore, source: LearningSource, *,
        confirm_policy: Callable[[LearningPolicyUpdate], bool] | None = None,
    ):
        self.store = store
        self.source = source
        self.confirm_policy = confirm_policy

    def read_learning_policy(self, scope: LearningScope) -> LearningPolicy:
        return self.store.read_learning_policy(scope)

    def update_learning_policy(self, request: LearningPolicyUpdate) -> LearningPolicy:
        return self.store.update_learning_policy(request, self.confirm_policy)

    def probe_source(self, scope: LearningScope, source_id: str) -> SourceCapability:
        capability = self.source.probe_source(scope, identifier(source_id))
        if capability.source_id != source_id:
            raise ValueError('capability is outside the selected learning source')
        return capability

    def poll_source(
        self, scope: LearningScope, source_id: str, *, limit: int = 50, now: datetime | None = None,
    ) -> PollResult:
        identifier(source_id)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('source page limit must be between 1 and 50')
        if now is not None:
            if now.utcoffset() is None:
                raise ValueError('learning poll time must include a UTC offset')
            now = now.astimezone(timezone.utc)
        with closing(self.store.connection()) as db:
            policy = self.store.policy(db, scope)
            before = self.store.checkpoint(db, scope, source_id)
        if issue := _access_issue(policy, source_id):
            return PollResult(before, reason_code=issue)
        capability = self.probe_source(scope, source_id)
        if capability.status != 'ready':
            return PollResult(before, reason_code=capability.reason_code or 'source_unavailable')

        latest = self.read_learning_policy(scope)
        if latest.revision != policy.revision:
            return PollResult(before, reason_code=_access_issue(latest, source_id) or 'checkpoint_changed')

        try:
            page = self.source.read_source(scope, source_id, cursor=before, limit=limit)
        except SourceConflict:
            return PollResult(before, reason_code='source_conflict')
        if (len(page.events) > limit or any(item.source_id != source_id for item in page.events)
                or (page.events and (not isinstance(page.next_cursor, str)
                                     or not page.next_cursor or page.next_cursor == before))
                or (not page.events and (page.next_cursor != before or page.has_more))):
            raise ValueError('invalid page from the selected learning source')
        capability = self.probe_source(scope, source_id)
        if capability.status != 'ready':
            return PollResult(before, reason_code=capability.reason_code or 'source_unavailable')

        with self.store.transaction() as db:
            latest = self.store.policy(db, scope)
            current = self.store.checkpoint(db, scope, source_id)
            if issue := _access_issue(latest, source_id):
                return PollResult(current, reason_code=issue)
            if latest.revision != policy.revision or current != before:
                return PollResult(current, reason_code='checkpoint_changed')
            decision_time = now if now is not None else datetime.now(timezone.utc)
            return self._process_page(db, latest, source_id, before, page, decision_time)

    def _process_page(
        self, db: sqlite3.Connection, policy: LearningPolicy, source_id: str,
        before: str | None, page: SourcePage, now: datetime,
    ) -> PollResult:
        if not page.events:
            return PollResult(before)
        scope = policy.scope
        used, last = self.store.trigger_history(db, scope, now.date().isoformat())
        receipts = []
        duplicates = 0
        for item in page.events:
            prior = self.store.original(db, scope, item)
            conflict = prior is not None and prior.event_sha256 != item.event_sha256
            reason = ignore_reason(item)
            if prior and not conflict and prior.decision.status != 'deferred':
                # Older stores used missing_evidence for an unclassified user excerpt.
                pending_label = (prior.decision.reason_code == 'unclassified_feedback'
                                 or (prior.decision.reason_code == 'missing_evidence'
                                     and prior.evidence_ref is not None
                                     and reason in (None, 'one_off_request')))
                if not pending_label or item.request_kind == 'unspecified':
                    duplicates += 1
                    continue
            ref, selected = _selected_excerpt(scope, item)
            if conflict:
                decision = TriggerDecision('deferred', reason_code='source_conflict')
            elif reason is not None:
                decision = TriggerDecision('skipped', reason_code=reason)
            elif (prior and ref != prior.evidence_ref
                  and (prior.evidence_ref is not None or prior.decision.reason_code != 'unclassified_feedback')):
                decision = TriggerDecision('skipped', reason_code='selection_changed')
            else:
                decision = decide_trigger(TriggerInput(
                    scope, (ref,) if ref else (), policy, policy.max_reflections_per_day - used, last, now,
                ))
            receipt = self.store.record(db, scope, item, decision, ref, selected, now)
            if receipt:
                receipts.append(receipt)
            if decision.status == 'deferred':
                # Keep the cursor before the page. Earlier final receipts deduplicate the replay.
                return PollResult(before, tuple(receipts), duplicates, reason_code=decision.reason_code)
            if decision.status == 'triggered':
                used += 1
                last = now
        assert page.next_cursor is not None  # Validated before the transaction.
        self.store.save_checkpoint(db, scope, source_id, page.next_cursor)
        return PollResult(page.next_cursor, tuple(receipts), duplicates, page.has_more)
