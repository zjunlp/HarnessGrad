# CLI improver probe credentials

The task agent and the CLI improver are separate credential roles. A custom CLI
provider's tool-call probe uses only the environment variable explicitly named
by that provider's `env_key`. If that variable is absent or empty, the gate
refuses with `credential-unset` and capability **unprobed**, without an HTTP
request. It does not classify missing authentication as unsupported tools.

Without `env_key`, tokenless custom services receive only the fixed nonsecret
`local` placeholder. The probe never implicitly borrows the task-agent key.
Earlier versions did so when a selected key was missing; that fallback is removed.
Configure an explicit CLI probe credential for authenticated custom providers.
A deliberately shared key can still be selected with `env_key="HG_AGENT_API_KEY"`;
that is the operator's explicit choice, not an automatic role fallback.

This does not add OAuth or inspect personal bearer/token stores. If CLI-managed
authentication cannot supply an explicitly selected probe key, the existing
`HG_IMPROVER_SKIP_TOOL_GATE=1` override remains an explicit unprobed operator
choice; it is not a successful capability check. Built-in CLI providers whose
endpoint cannot be discovered retain their existing unprobed behavior.

Both `/responses` and `/chat/completions` probes remain; `/responses` determines
tool capability for the Codex-style CLI. Declaration/model identity, timeout,
response parsing and the ordinary agent-oriented standalone probe command are
unchanged. The regression uses two owned loopback services and fake credentials,
not a live model or provider.
