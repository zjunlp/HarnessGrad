"""
File-as-Bus: every run is a directory the future evolve agent can grep/cat.

  runs/run_<ts>/
      question.txt
      trace.jsonl        — every step (LLM turn, tool call, tool result)
      searches/q_NNN.json
      pages/url_<hash>.md
      answer.md          — final answer
      summary.json       — counters + stop reason

Trace events are intentionally verbose. Cost is disk; benefit is that
"why did the agent fail step 12" is answerable by reading files.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, root: Path, question: str, *, config: dict | None = None):
        secrets = set()

        def collect(value, credential=False):
            if isinstance(value, dict):
                for key, item in value.items():
                    collect(
                        item,
                        credential
                        or key.upper().endswith(
                            ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
                        ),
                    )
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item, credential)
            elif credential and isinstance(value, str) and value:
                secrets.add(value)

        collect(config)
        self._secrets = sorted(
            secrets
            | {
                value
                for name, value in os.environ.items()
                if value
                and name.upper().endswith(("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
            },
            key=len,
            reverse=True,
        )
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        suffix = uuid.uuid4().hex[:6]
        self.dir = root / f"run_{ts}_{suffix}"
        self.dir.mkdir(parents=True, exist_ok=False)
        (self.dir / "searches").mkdir(exist_ok=True)
        (self.dir / "pages").mkdir(exist_ok=True)
        (self.dir / "question.txt").write_text(self.redact(question), encoding="utf-8")
        self._trace = (self.dir / "trace.jsonl").open("w", encoding="utf-8")
        self._t0 = time.time()
        self._page_idx = 0
        self.event("run_start", question=question)

    def redact(self, value: Any) -> Any:
        """Remove runtime API credentials from persisted and returned values."""
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        return value

    def write_json(self, path: Path, value: Any) -> None:
        serialized = json.dumps(
            self.redact(value),
            ensure_ascii=False,
            default=lambda item: self.redact(str(item)),
            indent=2,
        )
        path.write_text(serialized, encoding="utf-8")

    # ------------------------------------------------------------------
    def event(self, kind: str, **payload: Any) -> None:
        rec = {
            "t": round(time.time() - self._t0, 3),
            "kind": kind,
            **payload,
        }
        self._trace.write(
            json.dumps(
                self.redact(rec),
                ensure_ascii=False,
                default=lambda item: self.redact(str(item)),
            )
            + "\n"
        )
        self._trace.flush()

    # ------------------------------------------------------------------
    def save_search(self, idx: int, query: str, results_text: str) -> None:
        path = self.dir / "searches" / f"q_{idx:03d}.json"
        self.write_json(path, {"query": query, "results": results_text})

    def save_page(
        self, url: str, focus: str, extracted: str, raw_text: str = ""
    ) -> str:
        self._page_idx += 1
        h = hashlib.md5(url.encode()).hexdigest()[:10]
        path = self.dir / "pages" / f"p_{self._page_idx:03d}_{h}.md"
        body = [
            f"<!-- url: {url} -->",
            f"<!-- focus: {focus} -->",
            "",
            "## Extracted Notes",
            "",
            extracted,
        ]
        if raw_text:
            body += ["", "## Raw Page Snapshot", "", raw_text]
        path.write_text(self.redact("\n".join(body)), encoding="utf-8")
        return str(path)

    def save_answer(self, answer: str) -> None:
        (self.dir / "answer.md").write_text(self.redact(answer), encoding="utf-8")

    def save_summary(self, summary: dict) -> None:
        self.write_json(self.dir / "summary.json", summary)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self) -> None:
        try:
            self._trace.close()
        except Exception:
            pass
