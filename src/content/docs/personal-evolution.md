---
title: Evolve your harness
description: Start the local personal workspace and inspect evidence from a feedback session.
---

[Xiao Lü](https://github.com/zjunlp/HarnessGrad#evolve-personal-harness) demonstrates how a correction can turn into a scoped change proposal.

## Start the local workspace

From the HarnessGrad checkout, install the package and seed a synthetic session. Replace the project and state paths with an existing project and a separate state directory.

~~~bash
python -m pip install .
harnessgrad personal demo --state-root /path/to/state --project /path/to/project
harnessgrad personal serve --state-root /path/to/state
~~~

Open the printed loopback URL and select that service’s connection file. The example needs no model calls. The personal service runs separately from the experiment Console.

For scoped feedback, Codex compatibility and the native desktop shell, see the [personal evolution guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/personal-evolution.md) and the [desktop setup guide](https://github.com/zjunlp/HarnessGrad/blob/main/desktop/personal/README.md).
