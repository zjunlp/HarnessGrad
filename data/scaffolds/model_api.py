"""Non-reasoning Chat Completions profiles.

Only explicit model families are admitted; newer, pro, coding and gateway aliases
must not inherit capabilities from a prefix. Endpoint availability is separate.
Interface definition: docs/dataset-runtime-interfaces.md, verifier model channel.
"""
import re


# Preserve the adapter's original GPT-4o defaults. New switch support is scoped
# to the explicitly selected models, not all releases from these providers.
_OPENAI_PLAIN = {'gpt-4o'}
_OPENAI_NONE = {'gpt-6-sol', 'gpt-6-luna'}
_DEEPSEEK = {'deepseek-flash'}  # Official API ID for DeepSeek-V4.1-Flash.


def non_reasoning_profile(model):
    """Resolve the wire protocol without changing the requested model ID."""
    name = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', model)
    if name in _OPENAI_PLAIN:
        return {'provider': 'openai', 'mode': 'native',
                'token_parameter': 'max_tokens', 'fields': {}}
    if name in _OPENAI_NONE:
        return {'provider': 'openai', 'mode': 'disabled',
                'token_parameter': 'max_completion_tokens',
                'fields': {'reasoning_effort': 'none'}}
    if model in _DEEPSEEK:
        return {'provider': 'deepseek', 'mode': 'disabled',
                'token_parameter': 'max_tokens',
                'fields': {'thinking': {'type': 'disabled'}}}
    if model == 'glm-5.2':
        return {'provider': 'zai', 'mode': 'disabled',
                'token_parameter': 'max_tokens',
                'fields': {'thinking': {'type': 'disabled'}}}
    if model == 'mimo-v2.6-flash':
        return {'provider': 'mimo', 'mode': 'disabled',
                'token_parameter': 'max_completion_tokens',
                'fields': {'thinking': {'type': 'disabled'}}}
    if model == 'Ling-3.1-flash':
        return {'provider': 'ling', 'mode': 'disabled',
                'token_parameter': 'max_tokens',
                'fields': {'thinking': {'type': 'disabled'}}}
    raise ValueError(
        f'Model {model!r} has no supported non-reasoning Chat Completions profile. '
        'MemoryAgentBench requires a native non-reasoning model or a documented '
        'reasoning-off mode; low/minimal and larger token budgets do not qualify.')


def request_options(model, parameters):
    """Return effective non-secret options and explicitly recorded deviations."""
    profile = non_reasoning_profile(model)
    params = dict(parameters)
    omitted = {}
    fixed = {}
    if profile['provider'] in ('deepseek', 'zai', 'mimo', 'ling'):
        # These providers do not document a seed parameter in their API schemas.
        if 'seed' in params:
            omitted['seed'] = params.pop('seed')
        # The current flash model fixes top_p at 1 in non-thinking mode.
        if model == 'deepseek-flash':
            if 'top_p' in params:
                omitted['top_p'] = params.pop('top_p')
            fixed['top_p'] = 1.0
    return {**profile, 'parameters': params, 'omitted_parameters': omitted,
            'provider_fixed_parameters': fixed}


def check_non_reasoning_response(message, usage):
    """Reject positive evidence that a server ignored the requested mode.

    Missing telemetry is not proof of zero reasoning: admission also relies on
    the documented model profile and a compatible endpoint.
    """
    if any(message.get(key) for key in ('reasoning_content', 'reasoning')):
        raise ValueError('model returned reasoning despite the required non-reasoning mode')
    details = usage.get('completion_tokens_details') or {}
    if (usage.get('reasoning_tokens', 0) != 0
            or isinstance(details, dict) and details.get('reasoning_tokens', 0) != 0):
        raise ValueError('model consumed reasoning tokens despite the required non-reasoning mode')
