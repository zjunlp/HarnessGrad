# Jericho runtime migration status

The adapter declares a managed loading provider and a separately bound execution
artifact. The actor receives a stdlib `jer` client and reaches one trusted
single-game relay through the platform socket. The service declaration freezes
the selected interpreter artifact, ROM digest and narrow read paths. The native
scorer manifest and score reduction are preserved. Controls obtain their private
walkthrough files from the managed provider and use the normal runner.

`RUNTIME_PROVENANCE.json` records the source archive and license. The candidate
CPython 3.12 requirements are exact versions resolved from Jericho 3.3.1's
upstream dependencies; they do not pin the operating system, the C compiler or
all native libraries. The sdist is GPL-2.0-or-later and its setup builds Frotz.
The separately prepared external artifact is not bundled in the platform wheel.
The Tsinghua and Aliyun indexes agree on the archive SHA-256; direct official
host verification was unavailable in this environment. The checked-in license
is the source archive's unmodified license.

A local control prepared the candidate artifact and, without a game file,
imported Jericho and NumPy and loaded libfrotz. This establishes a local import
and dynamic-library check only. No real ROM, task, walkthrough, benchmark score,
model or Docker path was exercised. spaCy is declared by upstream; its language
model is not included. The candidate relay reproduces the existing actor
projection's empty bindings so ordinary commands do not invoke the template
and valid-action path, but this has not been validated with a real ROM.

Synthetic checks cover the registry-to-runner route with a prepared external
execution artifact, an authored environment, an authored game input and an
explicit synthetic scorer. They exercise socket routing through native bubblewrap,
seed zero, look/do/score/inventory/done, action logs, source custody and cleanup.
The extended control uses real managed provider subprocesses (separate PID receipts),
`provider_session`, catalog/split/task loading and verification-context capture,
then the unchanged shipped `base_harness/loop`, its external managed interpreter
and real OpenAI SDK against a local authored HTTP endpoint. The endpoint scripts
stdlib `jer` commands through the real trusted relay and native bubblewrap. An
additional cold-platform invocation has no Jericho, NumPy, OpenAI or pytest
installed; the actor also checks Jericho and NumPy are absent. Public endpoint
requests and actor trace exclude the private reference, selected input path,
library directory, provider injection path and tracebacks. The injected game table,
engine and scorer are explicit synthetic substitutes; no real ROM or native
Jericho gameplay/scoring is claimed. The synthetic scorer labels its evidence
`synthetic: true`. A passing synthetic score is instrument validation only.

A separate opt-in native-engine check constructs an original minimal Z-machine
v3 byte program locally and executes actual Jericho/libfrotz reset and step in
timed subprocesses. It compares the new relay environment with the historical
public projection at seed zero. This program is explicitly unsupported by
Jericho: scores and moves remain zero, valid actions are empty, and inventory
has no player object. The relay converts that inventory exception to its generic
unavailable message. This establishes native interpreter execution for the
authored byte program only. It does not establish supported-game semantics,
reference walkthrough replay, native scorer completion, or any benchmark score.

`--rom-dir` remains accepted for existing CLI callers. The selected game is fixed
by the trusted service declaration. The legacy actor projection remains available
as historical code, while the declared actor environment uses the relay client.
No language model is installed or downloaded by this route. Empty bindings match
the previous sanitized actor projection. Real-ROM and official walkthrough
behavior remain unmeasured.

A newer [original supported-game control](NATIVE_CONTROL.md) fills the earlier
unsupported-story limitation without using a released ROM. Its original CC0 v5
story implements the unmodified engine's TextWorld ABI. Native support detection,
object inventory, score/moves and terminal state work; the real relay/client and
shipped task-bound scorer replay pass at 2/2 in two actions, with clean service
termination. This is an authored instrument control, not a benchmark score. Its
libfrotz bytes match the existing sealed-native manifest entry. The selected
execution-artifact scorer route was exercised; whole sealed-userland admission
was not claimed. The control accepts a separately provisioned native package for
that additional check, and refuses missing/damaged supplied packages.
