# Egress: how a task's check reaches the internet

## Why this is needed at all

A Terminal-Bench task's check is a shell script that runs *after* the harness has stopped.
Measured over the checkout: **83 of 89** of those scripts bootstrap their own test runner
before testing anything —

```bash
apt-get install -y curl
curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh
uvx -p 3.13 -w pytest==8.4.1 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
```

On this host that fetch is blocked (direct `astral.sh` fails; a container got HTTP 403 while
`pypi.org` from the same container returned 200). So the check never ran, wrote no
`ctrf.json`, and the platform recorded "reward 0" — a task the *harness* appeared to fail,
when in fact nobody had measured it.

## The chain

```
task check (in a container)
  └─ HTTP(S)_PROXY = http://172.17.0.1:17890      <- HG_EGRESS_PROXY, set by eval/run/runner.py:_check_env
       └─ tools/egress_forwarder.py  172.17.0.1:17890 -> 127.0.0.1:17891
            └─ mihomo (our own)  127.0.0.1:17891         <- ~/.config/harnessgrad-proxy/
                 └─ the internet
```

Two hops because the proxy binds the host's loopback and a container cannot reach it; the
forwarder is bound to the docker bridge address only, so containers can use it and the LAN
cannot. `172.17.0.1` is the default `docker0` gateway — check yours with `ip route`.

## Running it

```bash
~/.config/harnessgrad-proxy/start-all.sh     # our mihomo (if down) + the bridge forwarder
# it prints:  HG_EGRESS_PROXY=http://172.17.0.1:17890
HG_EGRESS_PROXY=http://172.17.0.1:17890 python3 driver.py --harness loop --dataset terminal_bench ...
```

Put `HG_EGRESS_PROXY` in `.env` to make every run use it.

## What lives where, and why not in the repository

| file | what |
|---|---|
| `~/.config/harnessgrad-proxy/config.yaml` | the config mihomo actually runs (0600: it holds node credentials) |
| `~/.config/harnessgrad-proxy/sub1.url` | the subscription URL (0600: it is a credential) |
| `~/.config/harnessgrad-proxy/transform.py` | subscription → our config: our listener port, loopback bind, and the provider's `GEOIP` rules dropped (they need a geo database mihomo would fetch from GitHub at startup — the thing this proxy exists to make reachable) |
| `~/.config/harnessgrad-proxy/update.sh` | re-fetch the subscription and restart (`restart.sh`) |
| `~/.config/harnessgrad-proxy/restart.sh` | restart *our* mihomo only |
| `tools/egress_forwarder.py` | the bridge forwarder (in the repository: it is platform code) |

Secrets stay out of the repository on purpose: `docs/` and `tools/` are hashed as part of
the platform, and a subscription token does not belong in a hashed file that gets copied
around. The config is regenerated from the subscription, never hand-edited.

## What is deliberately *not* proxied

**Never the harness.** A harness reaches its model through the platform (the model gateway
when the endpoint is host-local, or the provider directly when it is not), and putting an
HTTP proxy in front of that breaks the model calls. `eval/run/runner.py:_check_env` hands the
proxy to the check's process only.

## Verifying it

```bash
curl -x http://127.0.0.1:17891 -LsS -o /dev/null -w '%{http_code}\n' https://astral.sh/uv/0.9.5/install.sh
docker run --rm --network bridge alexgshaw/cancel-async-tasks:20251031 \
  bash -lc 'apt-get update -qq && apt-get install -y -qq curl >/dev/null &&
            curl -x http://172.17.0.1:17890 -LsS -o /dev/null -w "%{http_code}\n" https://astral.sh/uv/0.9.5/install.sh'
HG_EGRESS_PROXY=http://172.17.0.1:17890 python3 driver.py --harness loop_plain ... \
  --dataset terminal_bench --tasks cancel-async-tasks --rounds 0
# the verdict should carry `check_report /logs/verifier/ctrf.json (N failed)` and the
# failing test names, instead of "the check wrote no ctrf.json"
```

## Renewal

Subscriptions expire. `update.sh` re-fetches and restarts; `transform.py` keeps our
overrides so the regenerated config stays ours. Nothing in the platform depends on the
provider: point `HG_EGRESS_UPSTREAM` at a different local proxy and the rest still works.
