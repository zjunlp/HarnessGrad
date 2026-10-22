"""Shared eight-stage runtime contracts and event recording.

The runtime is deliberately method agnostic.  It records what a method or the
platform observed; it does not implement an improvement algorithm.
"""

from .events import (
    ArtifactKind, ArtifactRef, ContentKind, Evidence, EventRecorder, EventValidationError,
    ImplementationMode, Lifecycle, Owner, ParentRef, ProtocolEvent, Role, Scope, Stage,
    StageID, StageResult, artifact, event, sha256_bytes,
)
from .validator import ProtocolError, validate_events, validate_event, schema_errors

__all__ = [
    "ArtifactKind", "ArtifactRef", "ContentKind", "Evidence", "EventRecorder",
    "EventValidationError", "ImplementationMode", "Lifecycle", "Owner", "ParentRef",
    "ProtocolEvent", "Role", "Scope", "Stage", "StageID", "StageResult", "artifact",
    "event", "sha256_bytes", "ProtocolError", "validate_events", "validate_event",
    "schema_errors",
]
