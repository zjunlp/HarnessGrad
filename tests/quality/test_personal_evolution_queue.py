"""Shared v1 contract, C provider and D consumer acceptance tests."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3

import pytest

from harnessgrad.personal.evolution_contracts import (
    ArtifactView,
    CandidateRequest,
    EvolutionQueuePort,
    JobRef,
    PortRejected,
    Scope,
)
from harnessgrad.personal.evolution_queue import EvolutionQueue, EvolutionQueueStore
from harnessgrad.personal.learning.reflection_contracts import CandidateQueue

FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'personal_evolution_v1.json'


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


class FixtureArtifacts:
    def __init__(self, value: dict):
        scope = Scope(**value['scope'])
        self.rows = {}
        for name in ('snapshot', 'candidate', 'changed_candidate'):
            row = value[name]
            self.rows[(*scope.key(), row['ref'], row['kind'])] = ArtifactView(
                row['ref'], row['sha256'], row['kind'], row['payload'],
            )

    def read_artifact(self, scope, ref, *, kind):
        try:
            return self.rows[(*scope.key(), ref, kind)]
        except KeyError:
            raise PortRejected('out_of_scope') from None

    def register_candidate(self, scope, snapshot_ref, files):  # pragma: no cover - queue read side only
        raise NotImplementedError


def provider(tmp_path):
    value = fixture()
    scope = Scope(**value['scope'])
    evidence = set(value['evidence_refs'])
    scenarios = set(value['scenario_ids'])
    queue = EvolutionQueue(
        EvolutionQueueStore(tmp_path / 'evolution.sqlite3'),
        FixtureArtifacts(value),
        read_evidence=lambda selected, ref: ref if selected == scope and ref in evidence else (_ for _ in ()).throw(KeyError(ref)),
        scenario_available=lambda selected, scenario: selected == scope and scenario in scenarios,
    )
    return value, scope, queue


def event_rows(page):
    return [dict(seq=event.seq, status=event.status, reason_code=event.reason_code) for event in page.events]


def test_c_provider_matches_the_shared_queue_fixture_and_replays_after_reopen(tmp_path):
    value, scope, queue = provider(tmp_path)
    request = CandidateRequest.from_dict(value['request'])

    job = queue.submit_candidate(request)
    assert queue.submit_candidate(request) == job
    assert job.scope == scope
    assert queue.read_job(job).status == value['expected']['initial_status']
    first = queue.read_events(job, after=0)
    assert event_rows(first) == value['expected']['events']
    assert first.next_after == value['expected']['next_after']
    assert queue.read_events(job, after=first.next_after).events == ()
    assert queue.read_events(job, after=first.next_after).next_after == first.next_after

    reopened = EvolutionQueue(EvolutionQueueStore(queue.store.path), queue.artifacts,
                              read_evidence=queue.read_evidence,
                              scenario_available=queue.scenario_available)
    assert reopened.submit_candidate(request) == job
    assert reopened.read_job(job) == queue.read_job(job)


def test_same_key_changed_candidate_conflicts_and_cross_project_is_hidden(tmp_path):
    value, _, queue = provider(tmp_path)
    request = CandidateRequest.from_dict(value['request'])
    job = queue.submit_candidate(request)
    changed = value['changed_candidate']
    conflicting = replace(request, candidate_ref=changed['ref'], candidate_sha256=changed['sha256'])

    with pytest.raises(PortRejected, match='idempotency_conflict'):
        queue.submit_candidate(conflicting)
    other = JobRef(job.profile_id, 'other-project', job.job_id)
    with pytest.raises(PortRejected, match='out_of_scope'):
        queue.read_job(other)
    with pytest.raises(PortRejected, match='out_of_scope'):
        queue.read_events(other, after=0)


@pytest.mark.parametrize('after', [True, -1, 2**63])
def test_event_cursor_rejects_bool_negative_and_overflow(tmp_path, after):
    value, _, queue = provider(tmp_path)
    job = queue.submit_candidate(CandidateRequest.from_dict(value['request']))
    with pytest.raises(PortRejected, match='invalid_cursor'):
        queue.read_events(job, after=after)


def test_events_page_at_fifty_without_skipping_the_boundary(tmp_path):
    value, _, queue = provider(tmp_path)
    job = queue.submit_candidate(CandidateRequest.from_dict(value['request']))
    for _ in range(49):
        queue.transition(job, 'reproducing')
    first = queue.read_events(job, after=0)
    second = queue.read_events(job, after=first.next_after)
    assert len(first.events) == 50 and first.next_after == 50
    assert [event.seq for event in second.events] == [51] and second.next_after == 51


def test_new_request_validates_snapshot_targets_evidence_and_candidate_digest(tmp_path):
    value, _, queue = provider(tmp_path)
    request = CandidateRequest.from_dict(value['request'])
    with pytest.raises(PortRejected, match='stale_snapshot'):
        queue.submit_candidate(replace(request, target_ids=('other-target',), idempotency_key='targets'))
    with pytest.raises(PortRejected, match='out_of_scope'):
        queue.submit_candidate(replace(request, evidence_refs=('missing-evidence',), idempotency_key='evidence'))
    with pytest.raises(PortRejected, match='stale_snapshot'):
        queue.submit_candidate(replace(request, candidate_sha256='0' * 64, idempotency_key='digest'))


def test_contract_rejects_unknown_fields_and_the_d_alias_uses_the_public_port():
    value = fixture()
    request = value['request']
    with pytest.raises(ValueError, match='unknown or missing'):
        CandidateRequest.from_dict({**request, 'physical_path': '/private/project'})
    with pytest.raises(ValueError, match='candidate intent'):
        CandidateRequest.from_dict({**request, 'intent': 'anything'})
    assert CandidateQueue is EvolutionQueuePort
    assert set(asdict(CandidateRequest.from_dict({**request, 'conditions': []}))) == set(request)


def test_d_style_consumer_uses_only_the_shared_fixture_and_public_protocol(tmp_path):
    """The consumer expectation intentionally has no queue-store knowledge."""
    value, _, implementation = provider(tmp_path)

    def consume(queue: EvolutionQueuePort, request: CandidateRequest):
        job = queue.submit_candidate(request)
        return queue.read_job(job), queue.read_events(job, after=0)

    view, page = consume(implementation, CandidateRequest.from_dict(value['request']))
    assert view.status == value['expected']['initial_status']
    assert event_rows(page) == value['expected']['events']


def test_queue_store_refuses_unknown_schema_objects_without_rewriting_them(tmp_path):
    path = tmp_path / 'evolution.sqlite3'
    EvolutionQueueStore(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE foreign_state(value TEXT)')
        db.execute("INSERT INTO foreign_state VALUES ('preserve')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='unrecognized'):
        EvolutionQueueStore(path)
    assert path.read_bytes() == before
