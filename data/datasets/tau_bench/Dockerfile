# tau-bench as one shared image: the environment for every retail and airline task.
#
# What the agent can see
# ---------------------
#   /app                         workdir; SETUP stages the HTTP client as ./tau.
#                                Database and episode log belong to the tau service,
#                                not to the harness filesystem.
#   /opt/tau/lib/tau_bench       the upstream tool implementations for both domains,
#                                with the task definitions removed. The service
#                                imports them; they are not secrets.
#   /opt/tau/policy              `<domain>.txt` = wiki.md + rules.py; `tau policy`.
#   /opt/tau/pristine            the clean database per domain, for reference. The
#                                service loads its task-bound database from here.
#   /usr/local/bin/tau           image shim; SETUP stages the matching client so
#                                existing image pins need no rebuild.
#
# What the agent must **not** see
# -------------------------------
# Nothing under `gt/` or `verify/` is copied in, and the build context excludes both.
# In particular no `tasks*.py` from the checkout travels: those files are the answer
# key, and the whole point of staging `lib/` from the tool directories is that they
# never enter this image. The verification procedure greps for them.
FROM docker.m.daocloud.io/library/python:3.11-slim

LABEL org.opencontainers.image.title="harnessgrad/tau-bench" \
      org.opencontainers.image.description="tau-bench retail+airline tools, data and policy behind the tau CLI" \
      harnessgrad.dataset="tau_bench" \
      harnessgrad.provenance="data/datasets/tau_bench/PROVENANCE.json (upstream sierra-research/tau-bench @ 59a200c)"

WORKDIR /app

# Set before anything imports the tools: a `__pycache__` written here would be a second,
# unmanaged copy of each tool module inside the image.
ENV PYTHONDONTWRITEBYTECODE=1

COPY lib /opt/tau/lib
COPY policy /opt/tau/policy
COPY db /opt/tau/pristine
COPY image/tau_cli.py /usr/local/bin/tau_cli.py

# Future image builds include the HTTP client too. SETUP supplies ./tau independently
# so the declared existing image can launch the service without an image rebuild.
RUN printf '#!/bin/sh\nexec python3 /usr/local/bin/tau_cli.py "$@"\n' \
        > /usr/local/bin/tau \
 && chmod 0755 /usr/local/bin/tau /usr/local/bin/tau_cli.py \
 && python3 -B -c "import sys; sys.path.insert(0, '/opt/tau/lib'); \
import tau_bench.envs.retail.tools, tau_bench.envs.airline.tools; \
print('tau tools:', len(tau_bench.envs.retail.tools.ALL_TOOLS), 'retail,', \
      len(tau_bench.envs.airline.tools.ALL_TOOLS), 'airline')"
