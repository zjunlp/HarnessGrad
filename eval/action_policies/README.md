# Local action policies

An action policy can be enabled for a sandboxed native episode with
`--action-policy-socket /absolute/path/to/predict.sock`. The runner gives the actor
`action_policy_client.py` and exposes the existing episode tool through a private
per-episode route. The service receives public observations and an instruction.
The benchmark relay executes the returned actions and retains its own score.

Choose the endpoint in the run configuration. Its description includes the
checkpoint hashes, backend configuration, environment contract, and action
horizon. The policy identity and environment contract enter the verification
context, the prepared run reservation, and the runtime commitment for a declared
treatment. Restarting the same service at a new socket path preserves those
identities. The driver preflights the service with `--check-only` and
`--prepare-only` using `describe`. Recreate prepared reservations made with the
earlier socket-path binding. `tools/prepare_h0.py` accepts
`--action-policy-socket` and checks the same policy binding before measuring or
reusing an H0. Measurements captured under different policy or route code
require a fresh evaluation.

## GR00T N1.5 on RoboCasa365

Use the RoboCasa Isaac-GR00T fork at `9d7d7a9eb7ad30bd8ce30448d9ab53a918b45b10`.
Checkout with `GIT_LFS_SKIP_SMUDGE=1` to skip demo media. Use a separate Python
3.10 environment with PyTorch 2.5.1 and torchvision 0.20.1 for CUDA 12.4, a
matching FlashAttention 2.7.4.post1 wheel, and the dependencies in
`gr00t_n15-requirements.txt`, including Ray Core. The RoboCasa365 simulator
uses its managed Python 3.11 runtime.

The public weights for this profile are in `robocasa/robocasa365_checkpoints` at
`c484448aba1a9b60a04c9b0ca117241518ea69f3`, under
`gr00t_n1-5/multitask_learning/checkpoint-120000`. The local checkpoint directory
needs `config.json`, `experiment_cfg/metadata.json`, the safetensors index, and
its two safetensors shards.

From the checkout root, start the service with the policy environment's Python
and a socket in a private directory owned by the run operator.

```sh
policy_socket_dir=$(mktemp -d)
/path/to/gr00t-venv/bin/python -m eval.action_policies.gr00t_n15 \
  --socket "$policy_socket_dir/predict.sock" \
  --source /path/to/Isaac-GR00T \
  --checkpoint /path/to/checkpoint-120000 \
  --data-config panda_omron --embodiment new_embodiment \
  --control panda-omron-delta-20hz-v1 --device cuda:0
```

Pass `"$policy_socket_dir/predict.sock"` to the driver's `--action-policy-socket`
option. Within a HarnessGrad process, episodes using the same socket queue
predictions in arrival order and send one prediction request at a time. The
queue uses each episode's remaining time budget. Environment calls can run
while another episode is using the policy. Each independent HarnessGrad
process has its own queue.

The service starts a private local Ray instance with one actor on the CUDA
device selected by `--device` (relative to `CUDA_VISIBLE_DEVICES`, when set).
The actor loads the checkpoint once and serves one prediction at a time.
The service gives each prediction dispatched to the ready actor 10 seconds.
On expiry it terminates that actor and starts a replacement. HarnessGrad allows
up to 15 seconds for the prediction RPC, including time for the timeout reply.

The timed-out prediction fails the episode as unmeasured. A following
prediction waits separately for the replacement to load the checkpoint, with
up to 120 seconds for recovery. Once ready, the replacement gets a fresh
10 seconds for the next prediction. The service checks the worker identity
again before using it. Queue, recovery, and prediction waits are all capped
by the remaining episode time. If the episode budget expires first, the
runner uses its normal episode-timeout handling.

The per-episode receipt records delegated requests, predictions, queue wait,
queue timeouts, recovery wait (`recovery_seconds`), policy request time, model
inference time, actual delegated steps, clipped control values, and time spent
calling the environment. The runner supplies independent noise seeds for each
episode step.

## Adding another configuration

A service implements `describe` and `predict` over the UNIX tool transport. Its
description declares the named camera and state fields, named action fields,
action horizon, and control contract. It can declare a positive finite
`predict_timeout_s` for the client's wait after dispatch, with 60 seconds as
the fallback. A service that declares `recovery_timeout_s` can respond to
`predict` with `{"status": "recovering"}`. The client then polls the
`predict_ready` operation for `{"ready": true}` before resubmitting the
observation. A service-side prediction deadline can return `{"status": "timeout"}`.
The description incorporates the deployed checkpoint and implementation
identity. A compatible environment supplies a port in
`data/action_policy_ports` and exposes `observe` and `step_many` through its
native episode relay. The current port carries current-frame RGB observations.
Each configured GR00T checkpoint selects its own upstream data and embodiment
configuration. The public actor tool uses the same instruction and step-count
arguments across these configurations.
