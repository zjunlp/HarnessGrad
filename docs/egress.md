# Verifier network egress

Some trusted task verifiers require network access to install or fetch declared dependencies. Prefer pinned offline dependencies where possible. A failed fetch or unavailable verifier is an infrastructure failure, not evidence that the evaluated harness failed the task.

`HG_EGRESS_PROXY` configures an HTTP proxy for eligible verifier processes. The actor/harness is not given verifier-only proxy settings merely because a verifier needs them. Provision and secure the upstream proxy separately; HarnessGrad does not manage proxy subscriptions, credentials, or host services. Do not put credentials in public configuration or receipts.

For a Docker verifier whose approved upstream proxy listens only on the host loopback, `tools/egress_forwarder.py` can bridge a specifically configured host address and port. Configure `HG_EGRESS_BIND`, `HG_EGRESS_PORT`, and `HG_EGRESS_UPSTREAM` for the deployment; the chosen bind must not unintentionally expose the proxy to an untrusted network. Confirm firewall and container-network behavior in that deployment. A forwarder does not create policy, authenticate callers, guarantee network isolation, or make an otherwise invalid evaluation publishable.

Use network-enabled verifier paths only when the dataset contract explicitly permits them. Record the verifier dependency, network policy, and failure status; do not silently reclassify infrastructure failures as zero scores.
