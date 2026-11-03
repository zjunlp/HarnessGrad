"""Inject a task-local host completion without global model/credential state."""

from contextvars import ContextVar

completion = ContextVar("widesearch_completion")


def llm_completion(*, messages, model_config_name):
    return completion.get()(messages)
