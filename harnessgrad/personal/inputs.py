"""Explicit user feedback; source observations and assistant output keep their provenance."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from harnessgrad.personal.contracts import MAX_DRAFT_BYTES, identifier
from harnessgrad.personal.evidence import EvidenceSelection, MAX_SELECTIONS

KINDS = ('explicit_request', 'correction', 'success_observation', 'failure_observation')


@dataclass(frozen=True)
class PersonalFeedback:
    project_id: str
    kind: str
    text: str
    evidence: tuple[EvidenceSelection, ...]
    idempotency_key: str
    draft_revision: int | None = None

    def __post_init__(self):
        identifier(self.project_id); identifier(self.idempotency_key)
        if not isinstance(self.kind, str) or self.kind not in KINDS:
            raise ValueError('feedback kind must be explicitly selected')
        if (not isinstance(self.text, str) or not self.text.strip()
                or len(self.text.encode('utf-8')) > MAX_DRAFT_BYTES):
            raise ValueError('feedback needs 1 to 16 KiB of user text')
        if (not isinstance(self.evidence, tuple) or len(self.evidence) > MAX_SELECTIONS
                or not all(isinstance(item, EvidenceSelection) for item in self.evidence)):
            raise ValueError('feedback needs at most 32 selected source excerpts')
        keys = [(item.source_instance, item.event_id, item.start, item.end) for item in self.evidence]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate evidence excerpt')
        if self.draft_revision is not None and (type(self.draft_revision) is not int or self.draft_revision < 1):
            raise ValueError('invalid submitted draft revision')

    @classmethod
    def from_dict(cls, value: dict) -> PersonalFeedback:
        if (not isinstance(value, dict)
                or set(value) != {'project_id', 'kind', 'text', 'evidence', 'idempotency_key', 'draft_revision'}
                or not isinstance(value['evidence'], list) or len(value['evidence']) > MAX_SELECTIONS):
            raise ValueError('invalid personal feedback fields')
        try:
            return cls(**{**value, 'evidence': tuple(EvidenceSelection(**item) for item in value['evidence'])})
        except TypeError:
            raise ValueError('invalid evidence selection fields') from None

    def as_dict(self) -> dict:
        return asdict(self)
