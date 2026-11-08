"""Actor output state and safe collection, independent of verification and service lifetime."""
from __future__ import annotations

from dataclasses import dataclass

from eval.run import trial_evidence


@dataclass(frozen=True)
class CollectedOutput:
    answer_path: object
    answer: str
    trace: str


class TaskOutputCapture:
    def __init__(self, workdir, evidence):
        self.workdir, self.evidence = workdir, evidence
        self.process = None
        self.error = None
        self.attempted = False
        self.collected = False

    def capture_pending(self):
        if self.evidence is not None and not self.collected:
            self.evidence.update(trial_evidence.capture_output(
                self.workdir, attempted=self.attempted, process=self.process, error=self.error))

    def collect(self, *, stepwise):
        if self.evidence is not None:
            self.capture_pending()
            self.collected = True
        answer_path = self.workdir / 'answer.txt'
        trace_path = self.workdir / 'trace.jsonl'
        if self.evidence is not None:
            answer = self.feedback_text(self.evidence.get('answer') or '') if not stepwise else ''
            trace = self.feedback_text(self.evidence.get('trace') or '')
        else:
            answer = answer_path.read_text() if not stepwise and answer_path.exists() else ''
            trace = trace_path.read_text() if trace_path.exists() else ''
        return CollectedOutput(answer_path, answer, trace)

    @staticmethod
    def feedback_text(value):
        # Feedback preserves read_text newline behavior; archival bytes stay raw.
        return value.replace('\r\n', '\n').replace('\r', '\n')
