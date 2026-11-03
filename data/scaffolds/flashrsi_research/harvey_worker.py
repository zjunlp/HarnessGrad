#!/usr/bin/env python3
"""Isolated host entry to the unchanged FlashRSI/official Harvey evaluator."""
from __future__ import annotations
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from native import activate
activate()
from flashrsi.integrations import _harvey_lab_grade


@contextlib.contextmanager
def completed_generations_only():
    """Preserve native requests while rejecting incomplete provider responses.

    The official judge can parse valid-looking JSON before a truncated response
    finishes. That must not turn a provider fault into a completed HG score.
    This observes real SDK return objects; it does not synthesize a verdict.
    """
    import anthropic
    import openai
    original_openai, original_anthropic = openai.OpenAI, anthropic.Anthropic

    def OpenAI(*args, **kwargs):
        client = original_openai(*args, **kwargs)
        create = client.responses.create
        def completed(*args, **kwargs):
            result = create(*args, **kwargs)
            if result.status != 'completed':
                raise RuntimeError('native OpenAI generation did not complete')
            return result
        client.responses.create = completed
        return client

    def Anthropic(*args, **kwargs):
        client = original_anthropic(*args, **kwargs)
        stream = client.messages.stream
        class CheckedStream:
            def __init__(self, context): self.context=context
            def __enter__(self): self.inner=self.context.__enter__();return self
            def __exit__(self,*exc): return self.context.__exit__(*exc)
            def __getattr__(self,name): return getattr(self.inner,name)
            def get_final_message(self):
                result = self.inner.get_final_message()
                if result.stop_reason != 'end_turn':
                    raise RuntimeError('native Anthropic generation did not complete')
                return result
        client.messages.stream = lambda *args, **kwargs: CheckedStream(stream(*args, **kwargs))
        return client

    openai.OpenAI, anthropic.Anthropic = OpenAI, Anthropic
    try: yield
    finally: openai.OpenAI, anthropic.Anthropic = original_openai, original_anthropic


def main():
    if sys.argv[1:] == ['--probe']:
        try:
            _harvey_lab_grade.check()
            from lab_core.evaluation import run_eval
            result={'lab_core':importlib.metadata.version('lab-core'),
                    'anthropic':importlib.metadata.version('anthropic'),
                    'openai':importlib.metadata.version('openai')}
            print(json.dumps(result));return 0
        except Exception as exc:
            print(type(exc).__name__,file=sys.stderr);return 2
    root=Path(sys.argv[1]).resolve()
    request=json.load(sys.stdin)
    try:
        with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), completed_generations_only():
            result=_harvey_lab_grade.evaluate(root,request)
        print(json.dumps(result,allow_nan=False));return 0
    except Exception as exc:
        print('native Harvey evaluator failed: '+type(exc).__name__,file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
