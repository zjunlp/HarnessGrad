# Runtime observation: the missing producer

**Status (2026-10-18):** implemented for opt-in fixed workers and their combined verifier
via `eval/containers/observed.py` (`backend=observed-v1`). This route reads cgroup v2,
mount/statvfs, privilege and network facts and calls `admit_runtime` before candidate execution.
See [the dataset runtime contract](dataset-runtime-interfaces.md#71-docker-worker-与组合-verifier).
The legacy single-actor exec/container-state routes remain refused. The historical analysis
below describes those routes, not the new observed worker backend.

## The gap, precisely

`eval/admission/runner_admission.py::admit_runtime` is a strict checker. It compares a
declared policy against an **observed** one and refuses on any mismatch:

| refusal code | what it means |
| --- | --- |
| `resource_enforcement_unproven` | `observed.resource_backend` is not `cgroup-v1` / `cgroup-v2` |
| `deadline_enforcement_unproven` | `observed.timeout_backend` is not `host-supervisor` |
| `resource_limit_mismatch` | observed resources differ from the declared policy |
| `network_enforcement_unproven` | `observed.network != "none"` or `attachments != []` |
| `writable_root_unbounded` | `observed.read_only_root` is not `True` |
| `writable_surface_inventory_mismatch` | observed filesystem paths differ from the policy's |
| `filesystem_enforcement_unproven` | a surface's `backend` is not `tmpfs` / `project-quota` |
| `shared_quota_scope` | two surfaces report the same `scope_id` |
| `filesystem_limit_mismatch` | a surface's observed bytes/inodes differ from the policy |
| `stale_observation` | `observed.binding` is not the binding of the task being admitted |
| `invalid_evidence_hash` | `evidence_sha256` is missing or not a sha256 |

A repository-wide search finds those names **only in that file**. `runtime_observed` is a
parameter of `eval/admission/scorer_backend.py::admit` — supplied by the caller, produced by
nobody. There is no cgroup reader anywhere.

So the platform can check a fact it never collects, and
`eval/run/runner.py::_refuse_unobserved_policies` refuses every `exec` / container task before
staging with `backend_observation_unavailable`. **That is the whole reason a
container-backed dataset cannot be measured here.**

## Why this is not "just relax the gate"

Two different things, and only one of them is engineering:

* **A — produce the observation.** Write the thing that asks the kernel what limits are in
  force. This is what this brief specifies. When it exists, container datasets are admitted
  *with evidence*, and their scores mean what they say.
* **B — relax the refusal.** A policy decision for the operator, not a task. The decision
  record `docs/enforcement-proof-decision.md:45-46` deliberately kept this contract out of the
  2026-10-15 withdrawals, and the audit in `docs/paper-fidelity-audit-handoff.md:404` frames it
  as "(a) intended; or (b) the gate needs relaxing". Doing B as well as A would remove the
  property the platform exists to provide: a number nobody can look behind.

The one escape that exists today is
`driver.py --authorize-unobserved-diagnostic <reason>`: one train-side task, one trial, one job,
round 0, no method, and a record with `policy_enforcement_observed`, `measured`,
`production_authorized` and `comparable` all `False`. It answers "does the adapter run at all".
It cannot produce a comparable score, by design.

## The contract

`observed` is passed as the third argument of `admit_runtime(binding, policy, observed)`.
Note that `_object` is a **set equality** check, not a subset one: the dict must carry exactly
these nine keys, no more and no fewer.

```python
observed = {
    "binding":         {"adapter_id": str, "run_id": str, "trial_id": str,
                        "manifest_sha256": str},      # must equal the task's own binding
    "evidence_sha256": str,                           # sha256 over whatever the observer returns
    "resources":       {"cpu_millis": int, "memory_bytes": int,
                        "swap_bytes": int,            # the only key allowed to be 0/negative
                        "pids": int, "timeout_ms": int},
    "resource_backend": "cgroup-v1" | "cgroup-v2",
    "timeout_backend":  "host-supervisor",
    "network":          "none",
    "attachments":      [],                            # no extra network attachments
    "read_only_root":   True,
    "filesystems":      {"/abs/normalised/path": {
                            "bytes": int, "inodes": int,
                            "backend": "tmpfs" | "project-quota",
                            "scope_id": str,       # must be unique across the surfaces
                            "evidence_sha256": str}},
}
```

Rules the checker enforces and the observer must therefore satisfy:

* `observed["binding"]` equals the binding of the task being admitted (`stale_observation`
  otherwise) — the observation is bound to one task, not to a run;
* `observed["resources"]` equals `policy["resources"]` exactly, key for key;
* filesystem paths are absolute, `posixpath.normpath`-clean, not `/`, and at most
  `MAX_SURFACES = 32` of them; the observed path set equals the policy's;
* `policy["network"]` must already be `"none"` — a policy asking for anything else is refused
  before observation is even consulted (`network_policy_not_supported`).

## Where it plugs in

1. **The checker already exists** — call it, do not reimplement it:
   `eval/admission/runner_admission.py::admit_runtime(binding, policy, observed)`.
2. **The shape to copy** is the one existing caller:
   `eval/admission/scorer_backend.py:93`, which is handed `runtime_policy` and
   `runtime_observed` and calls `resources.admit_runtime(...)`. Its own admission receipt shows
   how the result is recorded (`evidence=dict(..., resources=resource_check)`).
3. **The two call sites that must learn about a task's observation** are the two calls to
   `_refuse_unobserved_policies` in `eval/run/runner.py` (line ~169 for the single-task path,
   line ~1147 for the round path). Today the function only has `diagnostic_authorization` as an
   alternative to refusing; it needs a second alternative that is *evidence*, not permission —
   an admitted runtime observation for that task and trial.

## One more gate, which observation cannot open: the network

A container task has a second blocker, and it is worse than a missing fact because it is a
**policy precondition**:

```python
# eval/admission/runner_admission.py:151
_require(policy["network"] == "none", "network_policy_not_supported")
```

This runs before any observation is consulted, so a perfect observer still refuses a task whose
declared policy is anything but `none`.

**An earlier version of this file also cited `eval/policy/runtime_policy.py::admit_native_network`
as a rejection point here. That was wrong, and the dataset side caught it:** the runner calls it
under `if not is_exec:` (`eval/run/runner.py:251-252`), so it governs the native `files` path
only. A Docker `exec` task does not pass through it. The refusal that stops such a task today is
`_refuse_unobserved_policies`; the validator above is what it meets next.

### The harder half: the platform's own gateway network

`admit_runtime` also requires `observed["network"] == "none"` **and** `observed["attachments"] == []`
(line 164). For a Docker task that calls a model, the platform cannot currently satisfy either,
and the reason is the platform's own plumbing rather than anything the task declares:

* `eval/containers/container.py:1040` — at create time the platform builds an **internal network**
  and publishes the model gateway on that network's gateway address;
* `eval/run/runner.py:473` — *"With a gateway published, the harness joins the internal network"*;
* `eval/run/runner.py:655` — the network is removed per run, which is why it is per-run state at
  all;
* `eval/containers/container_state.py:6` names "a model gateway, a published network" among the
  things a container-state run has to get right.

So it is not only a task-declared `services` network that fails the check: **a task that declares
`network: "none"` and needs a model has a network attachment anyway, because the platform
attaches it.** Reading limits and attachments back will therefore report a network for exactly
the tasks the validator demands have none.

This is a sharper problem than "relax the gate", and an earlier version of this section answered it
wrongly. It proposed splitting networks into **task-declared** and **platform-authored** and
recording the second kind. The dataset side showed that split does not hold: **both** the model
gateway network **and** a dataset's service network are created by the platform calling
`services.create_network()`, and the service containers are started, attached and cleaned up by
the platform too. A network can therefore be platform-created *and* task-declared at once, so the
proposed categories overlap and cannot carry a rule.

### The rule

The property the check exists for is not *who called `create_network`*. It is **whether the
platform authors the topology and can enumerate every endpoint that becomes reachable**. Judge
every channel by that:

* a network whose membership, hostnames or ports come from the **dataset declaration** fails it:
  the platform is outside that network and cannot enumerate what a container inside it can reach.
  Recording the network name and an attachment list would satisfy the letter of the check while
  losing the point of it. **This is why "record a platform-authored network" is withdrawn here and
  not adopted**, and why a `services` declaration stays refused;
* a **platform-authored channel with a fixed, explicit operation list** passes it: the platform
  says which socket, which operations, and with what bounds, and can verify the list.

**Route for both of a container task's comms: the socket.** The material service and the model
both go over AF_UNIX, and the container keeps `network: "none"` with no attachments:

| | approved surface |
| --- | --- |
| model | one fixed route (`/v1/chat/completions`), as the existing relay already pins |
| a task's material service | an **explicit operation list** authored by the platform — for a `next` / `ack` / `submit` client, those three, and nothing else |

Not approved: a general-purpose relay, a proxy, and any channel whose operations the dataset
chooses.

### What that costs, and what it does not change

The two existing `admit_runtime` checks **do not change**: `policy["network"] == "none"` and
`observed["network"] == "none"` with `observed["attachments"] == []` stay exactly as they are,
because a socket is not a network attachment. Recording the authorized channels is an
**additive** observation field — an extension of the check, not a relaxation of it.

The work this implies on the exec path is real and is the item to schedule first:

* the model gateway must stop being published on a Docker network for a task that runs with
  `network: "none"`, so the exec path needs the same AF_UNIX model route the native path uses —
  and that route must carry an external HTTPS API and a full episode, which neither existing relay
  does today (see below);
* the task's service must be reachable over a socket the platform authors, which means a new
  relay, a new mount, and lifecycle and cleanup for it;
* the observation collector must read the channels back and the validator must record them.

If the socket route turns out to be infeasible for the model, then accepting a recorded
platform-authored **network** is a separate contract change, and it must be argued explicitly —
it is not implied by this brief.

### The socket precedent, and its limits

`eval/service/unix_model_relay.py` is documented as *"Agent-only HTTP over AF_UNIX, with a fixed
localhost:8002 model route"*, and `eval/service/unix_stepwise_relay.py` hands a harness
`HG_STEPWISE_SOCKET`. In both, the service is platform-side, is published as an **AF_UNIX
socket**, and the harness is told the path through an environment variable — with
`network: "none"` intact.

Two limits, reported by the dataset side and now checked here: the model relay is written
against a **fixed route** (`HOST, PORT, ROUTE = "127.0.0.1", 8002, "/v1/chat/completions"`) with a
small probe-call budget, and `NativeModelRoute` refuses anything but plaintext HTTP —
`eval/service/native_model_route.py:60-62` raises `GatewayUnavailable("native offline model route
requires a plain HTTP base URL")`. Neither carries an external HTTPS API and a full episode
unmodified, so this is a shape to extend, not a component to reuse as-is.

A workdir that is an ordinary bind mount also cannot be admitted: each declared surface must
report `backend` in `("tmpfs", "project-quota")` with matching `bytes`/`inodes` and a unique
`scope_id`. `tmpfs` gives both trivially; `project-quota` needs the host provisioning named in
`docs/filesystem-quota-backends.md`.

## Prerequisite: decide this on the machine that will run the dataset

**This is the go / no-go, and it is not a code question.** cgroup limits exist only where the
host's hierarchy allows the user to create a subtree and write limit files. Run these on the
machine that will execute the dataset — the answers are not asserted here:

```bash
cat /sys/fs/cgroup/cgroup.controllers        # cgroup v2 controllers available to us
cat /sys/fs/cgroup/cgroup.subtree_control    # what is delegated to children
cat /proc/self/cgroup                        # which subtree we are in
mkdir /sys/fs/cgroup/hg-probe-$$ && \
  cat /sys/fs/cgroup/cgroup.subtree_control && \
  echo '+memory +cpu' > /sys/fs/cgroup/cgroup.subtree_control
# then, inside the new subtree:
echo $((256*1024*1024)) > memory.max && cat memory.max
cat /sys/fs/cgroup/hg-probe-$$/cpu.max
rmdir /sys/fs/cgroup/hg-probe-$$
```

* If the writes succeed, A is implementable on that host.
* If they are denied, the host needs administrator provisioning before any code matters. The
  two roles and what each owes are already written down in
  `docs/filesystem-quota-backends.md` ("Host operator (external)" and "Backend implementer
  (pending code + runtime)").

On a shared machine this is the likely blocker: `/` is shared with other users, and cgroup
delegation is normally an administrator's decision.

## Deliverable

One module that collects the observation for a running container and returns the dict above,
plus the wiring at the two runner call sites.

**The observer must read the kernel, not echo the policy.** That is the entire point: if the
module returns the numbers it was asked for, it is a restatement of the declaration and the
platform is no better off than before. Read `memory.max`, `cpu.max`, `pids.max` and the
process's own cgroup path back out of the filesystem after the container is running; enumerate
the actual network attachments; and for the deadline, record *which* process is the supervisor
and its observed deadline.

## Acceptance

The network and attachment requirements are **unchanged** by the route above: `network` is
`"none"` and `attachments` is `[]`, because a socket is not an attachment. Nothing here relaxes
them, and the channel record is additive.

Three tests, and the second and third are the ones that matter:

1. **It admits.** A container started with a declared policy produces an observation that
   `admit_runtime` accepts, the authorized channels are recorded with their operation lists, and
   the resulting curve point carries the admitted limits.
2. **It refuses a liar.** Feed `admit_runtime` an observation whose `resources` do not match the
   policy, whose `network` is `"bridge"`, whose `attachments` is non-empty, whose
   `resource_backend` is missing, whose `read_only_root` is `False`, and whose two surfaces share
   a `scope_id`. Each must be refused with the code in the table above.
3. **It refuses a channel it did not authorize.** A run whose observation names a socket the
   policy did not declare, or whose relay served an operation outside the declared list, is
   refused rather than scored. Without this test the channel record is a formality: the point of
   enumerating operations is that the enumeration is checked.

Tests 2 and 3 are the ones that prove anything. A test that only proves the happy path proves
nothing here: the value of this module is what it does when the enforcement, or the channel, is
*not* what was declared. The existing tests in `tests/quality/test_runner_admission.py` are the
place to add these.

## What not to do

* Do not treat a launch flag, a config value, an environment variable, an adapter's own claim or
  a cached verdict as an observation. `runner.py`'s docstring says so explicitly, and it is the
  same class of mistake as fabricating a score.
* Do not have the process that *applies* the limit also be the only thing that *certifies* it
  without reading the kernel back.
* Do not add a path that yields a comparable score without the admission receipt. `measured`,
  `production_authorized` and `comparable` stay `False` until an observation says otherwise.
* Do not read operator authorization as observation. The existing contract already states that
  permission "is never treated as backend observation or production admission".

## Definition of done

* [ ] the observation for a running container is collected from the kernel, not restated;
* [ ] `admit_runtime` accepts it for a correctly limited container;
* [ ] every refusal code above has a test that triggers it;
* [ ] a curve point from such a run carries the admitted limits, so a reader can see the basis;
* [ ] `docs/paper-fidelity-audit-handoff.md:404` can be closed as answered by measurement
      rather than by relaxing the gate.
