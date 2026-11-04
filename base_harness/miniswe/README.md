# MiniSWE native adapter

The adapter already invokes mini-SWE-agent 2.4.6. `requirements.txt` pins that
version and compatible LiteLLM 1.75.9; no native prompt, context policy or strategy
is changed. Dependencies now come from an overlay built for the executing Python
interpreter instead of an operator's Python 3.12 user-site mount.

Provision dependencies separately before a run:

```bash
python3 tools/configure_harness.py --harness base_harness/miniswe --python 3.12
```

Exec also requires an overlay for the image's interpreter (Python 3.11 for the
pinned actor below). Overlay provenance records transitive distributions. The
existing configure verifier can mistake mini-SWE-agent's import banner for an
error; the native runner control below checks actual imports/execution. This
MiniSWE-only increment does not change that general verifier.

The manifest declares files and ordinary exec. The focused exec regression uses
actual `run_one` in actor
`sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb`,
a genuine Docker/kernel backend observation, network none and the ABI-matched
read-only Python 3.11 overlay. Native mock drives upstream shell execution and
artifact delivery; source read-only/cwd/private-input/trace/usage/tree identity
checks accompany a fresh synthetic post-exit verifier. Native trajectory and
runtime provenance must both identify mini-SWE-agent 2.4.6.

This proves the ordinary interface on this image/ABI. It does not measure official
AppWorld services or scoring, every task image, observed workers, complete
lifecycle isolation or production H0 authorization. No real model or benchmark
is run. The terminating upstream submission sentinel remains represented as an
unexecuted trajectory action. Changing the manifest changes submission identity;
future experiments need a newly frozen identity.
