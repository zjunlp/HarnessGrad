# RoboCasa365

This adapter lets a Harness control a PandaOmron robot in RoboCasa365's 3D kitchens. The Agent receives the task instruction, five robot state vectors and three 256 × 256 RGB camera views. It sends the official Gym action fields. The simulator runs in a separate Python 3.11 environment.

## Evaluation protocol

The default roster is the test set for the official multi-task leaderboard and uses the official `pretrain` kitchens and object split. The adapter pins RoboCasa 1.0.1 and its per-class horizons, including the 1.5× horizon update. Each leaderboard class has 50 independently seeded rollouts. The official evaluator samples scenarios procedurally, and the fixed seeds in this adapter are HarnessGrad's reproducible sample. [Official leaderboard](https://robocasa.ai/leaderboard.html) · [Evaluation protocol](https://robocasa.ai/docs/build/html/benchmarking/benchmarking_overview.html) · [Pinned task registry](https://github.com/robocasa/robocasa/blob/7bfc60c9d26c02506d4265cbe0e86b35e6e25d42/robocasa/utils/dataset_registry.py)

| Test set | Atomic-Seen | Composite-Seen | Composite-Unseen | Episodes |
| --- | ---: | ---: | ---: | ---: |
| Leaderboard | 18 × 50 | 16 × 50 | 16 × 50 | 2,500 |

Each episode scores 1 if an executed action reaches RoboCasa's native Gym success predicate within the class horizon. It scores 0 otherwise. The Agent sees observations throughout the episode. A trusted service records the first successful step and supplies a platform-sealed episode to the verifier after the Agent exits. Missing or failed service evidence is unmeasured. Overall and group scores average per-class success rates. [Pinned Gym step](https://github.com/robocasa/robocasa/blob/7bfc60c9d26c02506d4265cbe0e86b35e6e25d42/robocasa/wrappers/gym_wrapper.py#L355) · [Official baseline evaluator](https://github.com/robocasa-benchmark/Isaac-GR00T/blob/9d7d7a9eb7ad30bd8ce30448d9ab53a918b45b10/gr00t/eval/simulation.py#L125)

## Local setup

Run from the HarnessGrad checkout. The host needs Linux, Bubblewrap, Python 3.12+ for HarnessGrad, Python 3.11 and `uv` for the managed simulator, and Mesa's OSMesa software renderer. The simulator uses CPU rendering and has no Docker or NVIDIA dependency. Budget about 11 GB for the downloads and additional space for extracted assets.

```bash
export HG_DATA_ROBOCASA365=/absolute/path/to/datasets/robocasa365
python3 tools/prepare_dataset.py --dataset robocasa365 --runtime execution --python /absolute/path/to/python3.11
python3 tools/datasets.py --dataset robocasa365 --fetch
python3 data/datasets/robocasa365/prepare.py
python3 tools/datasets.py --dataset robocasa365 --check
```

For Ubuntu or Debian, install the system `libosmesa6` package. For a user-local install on a compatible host, download and extract that package with `apt-get download libosmesa6` and `dpkg-deb -x`. Place a copy of its versioned `libOSMesa.so.8` under `$HG_DATA_ROBOCASA365/native/libOSMesa.so.8`. Its system-library dependencies must also be available on the host.

Preparation verifies the upstream source and six asset archives against SHA-256 pins. The simulator loads RoboCasa and robosuite from those pinned source archives. This uses the upstream robosuite source's optional IK packaging, which permits RoboCasa's required NumPy 2.2.5. The prepare step changes the location of generated object XML to the service's private temporary directory, with asset paths still resolved against the original object folder. Code and kitchen assets are credited to the RoboCasa project under its published licenses. [RoboCasa installation](https://robocasa.ai/docs/build/html/introduction/installation.html) · [RoboCasa licenses](https://github.com/robocasa/robocasa/tree/7bfc60c9d26c02506d4265cbe0e86b35e6e25d42#license)

## Running an evaluation

Use `--dataset robocasa365 --split official --side eval` with the chosen harness
checkpoint and model to evaluate the 2,500 leaderboard episodes. The default
split also selects only the test set, with an empty training side. List its IDs
with `python3 -m data.datasets.robocasa365 --pool leaderboard` and pass a
comma-separated subset via `--tasks`. Use
`python3 -m data.datasets.robocasa365 --aggregate /path/to/curve-point.json` to
report the three leaderboard group scores and coverage. The report marks a
result as complete when all 2,500 episodes have measured scores.

### Full task catalog

Add `--pool all` to load 2,815 episodes across all 365 classes. This includes
the 2,500 test episodes and one fixed-seed episode for each of the 315 additional
classes. List the additional IDs with
`python3 -m data.datasets.robocasa365 --pool catalog`. These episodes are
available for explicit study splits. The default split leaves them unassigned.

Use `--pool all --split experiments/splits/robocasa365/study.json` to opt into the
frozen catalog study. It assigns 252 catalog episodes to `--side train` and 63
to validation via `--side eval`. Each episode represents a different task class.
The validation selection uses seed 0 and the SHA-256 order of
`robocasa365-catalog-v1:0:<task_id>` within each stratum. It selects 9 atomic
tasks, 44 composite tasks with upstream horizons, and 10 composite tasks with
estimated horizons. The remaining catalog episodes are study tasks.

Validate a trained checkpoint using the same split file with `--side eval` and
`--from-run`. For official test evaluation of that checkpoint, use
`--pool all --split official --side eval --from-run <training run>` and the
chosen round. HarnessGrad validates the training manifest against the full
roster and checks that the entire leaderboard is held out from study and
validation. `--split official` assigns only the leaderboard and has an empty
training side.

The additional list follows the pinned upstream task documentation. For 267 of
the additional classes the upstream registry supplies a horizon. The other 48
are composite tasks. Their HarnessGrad horizons are 2.25 times the documented
median human rollout length at 20 Hz, rounded up to a multiple of 150 steps.
The multiplier approximates the median ratio observed for composite tasks with
both published horizons and rollout lengths. [Upstream task list](https://github.com/robocasa/robocasa/blob/7bfc60c9d26c02506d4265cbe0e86b35e6e25d42/docs/composite_tasks/task_attributes.json) · [Upstream rollout lengths](https://github.com/robocasa/robocasa/blob/7bfc60c9d26c02506d4265cbe0e86b35e6e25d42/docs/composite_tasks/composite_episode_lengths.js)

In the actor sandbox, run `python3 robocasa365_client.py observe --image-dir views`. The harness can also import `robocasa365_client.call` to obtain the same observation with the camera frames as base64-encoded PNGs. Sensor and action keys preserve the official Gym names. The client supports one action with `step ACTION.json` or up to 32 consecutive actions with `step_many CHUNK.json`. Use `--image-dir views` to save the latest three views after either operation. The action schema is in `robocasa365_controls.json`.

Select `--harness loop-vision` with a vision-capable model endpoint to attach the saved camera PNGs to model requests. [Loop Vision setup](../../../base_harness/loop-vision/README.md)

Run `python3 data/datasets/robocasa365/control.py` to check kitchen startup, the three views, a zero action, and the sealed zero score through HarnessGrad without a model.

HarnessGrad allows up to 120 seconds for the simulator service to start for each episode.

The adapter provides simulation and scoring. The harness supplies the policy, action selection and stopping logic. The full leaderboard pool allows 4,522,500 simulator steps at the published horizons. Wall-clock time depends on policy latency, episode length and host parallelism. A shorter study run can select a subset with `--tasks`. Reference demonstrations are separate releases with their own initial states. The leaderboard supplies no oracle actions for the sampled evaluation episodes.
