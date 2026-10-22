#!/usr/bin/env python3
"""Publish the host's egress proxy to the docker bridge, for task checks to use.

Why this exists
---------------
The machine's proxy (our own mihomo, `~/.config/harnessgrad-proxy/`) binds `127.0.0.1`, and
a container cannot reach the host's loopback -- it has its own. A task's check, however,
often needs the internet: measured across the Terminal-Bench checkout, **83 of 89**
`test.sh` files bootstrap their own test runner with `curl https://astral.sh/uv/... | sh`.
Without a route, the check never runs, and the platform recorded that as a task the
*harness* failed.

So this forwards `172.17.0.1:<port>` to the host's proxy with the same byte pump the model
gateway uses. It is bound to the docker bridge address only: containers can use it, the LAN
cannot. The platform then hands `HG_EGRESS_PROXY=http://172.17.0.1:<port>` to the check's
process (`eval/run/runner.py:_check_env`), and never to the harness -- a harness reaches its
model through the platform, and a proxy in front of that breaks it.

    python3 tools/egress_forwarder.py        # 172.17.0.1:17890 -> 127.0.0.1:17891
    ~/.config/harnessgrad-proxy/start-all.sh # that, plus the proxy itself

`HG_EGRESS_UPSTREAM` and `HG_EGRESS_PORT` change the upstream and the published port; the
defaults are the ones `docs/egress.md` documents.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.service.modelgate import ModelGateway  # noqa: E402


def main() -> int:
    bridge = os.environ.get("HG_EGRESS_BIND", "172.17.0.1")
    port = int(os.environ.get("HG_EGRESS_PORT", "17890"))
    upstream = os.environ.get("HG_EGRESS_UPSTREAM", "127.0.0.1:17891")
    host, _, raw_port = upstream.partition(":")
    gateway = ModelGateway(bind_host=bridge, target=(host, int(raw_port)),
                           path="", port=port).start()
    print(f"egress proxy for containers: {gateway.base_url} -> {upstream}", flush=True)
    print(f"put this in .env:  HG_EGRESS_PROXY={gateway.base_url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        gateway.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
