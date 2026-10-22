#!/usr/bin/env python3
"""The ALFWorld environment, served from a process the agent's sandbox cannot read.

What was wrong
--------------
The agent's `alf` CLI built the TextWorld environment itself, from `game.tw-pddl` in
the agent's working directory. TextWorld refuses a game file without `pddl_domain`,
`pddl_problem` and `grammar`, so the whole initial state had to travel with the task:
measured on the first `valid_unseen` game, `pddl_problem` holds the entire
object -> receptacle -> room map (`inReceptacle`, `objectAtLocation`,
`receptacleAtLocation`; 293 `:init` facts). An agent that opened the file could read
the layout instead of exploring for it, so a score taken that way is not a measurement
of ALFWorld. (Measured in the 134 archived traces: the agent read it 0 times, so the
published 0.4701 is not inflated. The hole is a hole, not a wrong number.)

What this is
------------
The environment, in a host process, reached over the platform's one transport
(`eval/service/tool_transport.py`: newline-delimited JSON over an `AF_UNIX` socket).
The CLI is a client of it and never touches a game file. Nothing here is a second
protocol: `Relay` and `call` are imported, not re-implemented.

Run it as a program, outside the sandbox::

    python3 data/datasets/alfworld/relay.py --game <path> --socket <path> \\
        [--budget 50] [--deadline-s N] [--max-calls N] [--receipt FILE]

or in-process, which is what the tests and `control.py --oracle` do.

The op set, and nothing else
----------------------------
    {"op": "look", "args": {"step": N, "budget": 50}}            -> no step consumed
    {"op": "do",   "args": {"step": N, "budget": 50, "action": "go to desk 2"}}

Both return exactly five fields::

    {"obs": <str>, "admissible": [<str>, ...], "won": <bool>, "done": <bool>,
     "step": <int>}

`episode.current()` is the only thing either op can return, and it projects the
environment's own record onto those five keys. There is no path from the game file to
a reply: the file is handed to `gym_env.build` once and never read again, and the
reply is built from `gym_env.step`'s observation, its admissible list, its `won`/`done`
flags and a counter. `pddl_domain`, `pddl_problem`, `grammar`, `walkthrough` and
`solvable` have no key to travel in, and the object ids in them have no key either --
the observations and commands the agent sees are ALFRED's *demangled* names
(`AlfredDemangler(shuffle=False)`, kept exactly as `gym_env.build` declares it), which
is what the benchmark itself shows a player.

`step` is a consistency guard, not decoration. The client says which step of the log it
is acting from; a mismatch means the live episode and the log the check will replay
have parted company, and the honest answer is a failed call (which the platform turns
into an unmeasured task), never an observation from a different episode.

An unknown command is **not** an error. `do "wibble"` is a step: TextWorld answers with
its own refusal text ("Nothing happens."), the step is counted, and `done` stays false.
Treating it as a transport fault would turn a legal, budgeted, graded action into a
platform failure.

What this module does not do
----------------------------
It does not mount itself, does not start itself, and does not decide that its own
failure is unmeasured. Two things are outside this file and are required before an
end-to-end run:

1. **The mount wiring** (`eval/containers/tool_route.py`, a parallel task). The runner
   must stage the stripped game into a directory *outside* the agent's sandbox and
   start this program with `--game` pointing at it. The bytes are the `game.tw-pddl`
   entry of `VERIFY.inputs` -- the trusted, `walkthrough`-free copy -- which is what
   `game_content()` below reads; `SETUP` no longer carries the game at all. The socket is
   then handed to the sandbox as `tool_socket=<host relay path>`, which publishes it at
   the fixed `/run/harnessgrad-tool/tool.sock` the CLI calls.
2. **Fault -> unmeasured.** A relay fault (no socket, a refused call, a deadline) makes
   `alf` exit nonzero; nothing in the platform yet maps that to "unmeasured" rather than
   to a zero. `Relay.receipt` (and `--receipt`) is the evidence that mapping needs.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

#: The transport is the platform's, and the repo root is where it lives. `relay.py` is a
#: **host-side program**, like `control.py` beside it: it runs from this checkout, on the
#: host, outside the sandbox, and it is never copied into a task's workdir.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.service.tool_transport import MAX_CALLS, Relay  # noqa: E402

#: `gym_env.py` is the one implementation of "build, reset, step": the CLI, the check and
#: this server all use it, so the observation the agent sees and the trajectory the check
#: replays cannot drift. Loaded from `lib/`, the same way `data/datasets/alfworld.py` does.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import gym_env  # noqa: E402

#: The two ops. Anything else is refused as a failed call.
OP_LOOK = "look"
OP_DO = "do"

#: The exact reply shape. Pinned here so a reply cannot grow a field by accident, and so
#: the CLI can refuse a malformed answer instead of printing it. `tests/quality/`
#: asserts every real reply has exactly these keys.
REPLY_KEYS = ("obs", "admissible", "won", "done", "step")

#: The environment's own words for a command it did not understand. Measured, not
#: assumed: `textworld` 1.7.0 answers an unparsed command with exactly this text.
REFUSAL_TEXT = "Nothing happens"


class Episode:
    """One live TextWorld episode, and the only source a reply may be built from."""

    def __init__(self, game_path, budget: int | None = None):
        if budget is None:
            budget = gym_env.STEP_BUDGET
        self.game_path = str(game_path)
        self.budget = int(budget)
        self._env = gym_env.build(self.game_path, self.budget)
        first = gym_env.reset(self._env)
        self._initial_obs = first["observation"]
        self._initial_admissible = list(first["admissible"])
        self._won = bool(first["won"])
        self._records: list[dict] = []
        self._done = False

    # -- state ---------------------------------------------------------------
    @property
    def steps(self) -> int:
        return len(self._records)

    def current(self) -> dict:
        """The five-field reply. The **only** thing a handler returns."""
        if self._records:
            last = self._records[-1]
            obs, admissible = last["obs"], last["admissible"]
        else:
            obs, admissible = self._initial_obs, self._initial_admissible
        return {"obs": obs,
                "admissible": list(admissible),
                "won": bool(self._won),
                "done": bool(self._done),
                "step": len(self._records)}

    # -- the two ops ---------------------------------------------------------
    def look(self) -> dict:
        """The last applied step's observation. Does not touch the environment.

        Deliberately **not** `env.look()`: the CLI prints the observation of the last
        command it applied, and a fresh look would describe the room even when the last
        command was, say, `take mug 3 from desk 2`. That difference is why `look` is a
        stored record and not a query.
        """
        return self.current()

    def apply(self, action: str) -> dict:
        """One `env.step`. A command TextWorld does not understand is still a step."""
        if self._done:
            # The episode is over: replaying stops at the first `done`
            # (`gym_env.replay`), so a further action is not applied and the client sees
            # a `step` that did not move. It must not be silently appended to the log.
            return self.current()
        record = gym_env.step(self._env, action)
        self._records.append({"obs": record["observation"],
                              "admissible": list(record["admissible"]),
                              "won": bool(record["won"]),
                              "done": bool(record["done"])})
        self._won = self._won or bool(record["won"])
        self._done = self._done or bool(record["done"])
        return self.current()

    def close(self) -> None:
        try:
            self._env.close()
        except Exception:  # noqa: BLE001 - a closed env is not a measurement failure
            pass


def handler(episode: Episode):
    """`handler(op, args)` for :class:`Relay`, bound to one episode."""

    def handle(op: str, args: dict) -> dict:
        expected = args.get("step")
        if expected is not None and int(expected) != episode.steps:
            raise ValueError(
                f"episode desync: the client is acting from step {expected}, the "
                f"environment is at step {episode.steps}; the log and the live episode "
                f"have parted company, so this call is refused rather than answered")
        budget = args.get("budget")
        if budget is not None and int(budget) != episode.budget:
            raise ValueError(
                f"budget mismatch: the client says {budget}, the environment was built "
                f"for {episode.budget}; an episode has one step budget, not two")
        if op == OP_LOOK:
            return episode.look()
        if op == OP_DO:
            action = args.get("action")
            if not isinstance(action, str) or not action.strip():
                raise ValueError("`do` needs a non-empty action string")
            return episode.apply(action)
        raise ValueError(f"unknown op {op!r}; this environment serves {OP_LOOK!r} and "
                         f"{OP_DO!r} only")

    return handle


class EnvServer:
    """A started `Relay` over a live `Episode`. Use as a context manager."""

    def __init__(self, game_path, socket_path, *, budget: int | None = None,
                 max_calls: int = MAX_CALLS, deadline_s: float | None = None):
        self.episode = Episode(game_path, budget)
        #: Same formula the transport uses, so a program can shut itself down when the
        #: transport has already started refusing. `None` means "no deadline".
        self.deadline_wall = (None if deadline_s is None
                              else time.monotonic() + float(deadline_s))
        try:
            # Constructed after the build, so a slow first load does not eat the
            # deadline the caller asked for the *serving* to have.
            self.relay = Relay(socket_path, handler(self.episode),
                               max_calls=max_calls, deadline_s=deadline_s)
        except Exception:
            self.episode.close()
            raise

    @property
    def socket_path(self) -> Path:
        return self.relay.path

    @property
    def receipt(self) -> dict:
        return self.relay.receipt

    def start(self) -> "EnvServer":
        try:
            self.relay.start()
        except Exception:
            self.episode.close()
            raise
        return self

    def stop(self) -> None:
        self.relay.stop()
        self.episode.close()

    def __enter__(self) -> "EnvServer":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()


def serve(game_path, socket_path, **kwargs):
    """`EnvServer` as a context manager, so a caller cannot forget to close the env."""
    return EnvServer(game_path, socket_path, **kwargs)


# -- staging the game --------------------------------------------------------
def game_content(spec: dict) -> str:
    """The stripped game text out of a `SETUP`/`VERIFY` entry list.

    `VERIFY.inputs` is the trusted copy and the one this reads (`SETUP` no longer ships
    the game). Kept here rather than in the adapter because the caller is the runner:
    it stages these bytes outside the sandbox and starts `relay.py --game <that path>`.
    The bytes are byte-for-byte what `SETUP` used to write into the agent's workdir --
    `tests/quality/test_alfworld_relay.py` checks that against all 134 games.
    """
    entries = spec.get("files")
    if entries is None:
        entries = spec.get("inputs") or []
    for entry in entries:
        if entry.get("path") == gym_env.GAME_FILENAME and "content" in entry:
            return entry["content"]
    raise KeyError(f"no inline {gym_env.GAME_FILENAME!r} entry to stage")


def stage(game_text: str, dest) -> Path:
    """Write the relay's copy of the game. The relay is the only reader of it."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(game_text, encoding="utf-8")
    return path


# -- the program -------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Serve one ALFWorld TextWorld episode over a UNIX socket.")
    parser.add_argument("--game", required=True,
                        help="the stripped game file (outside the agent's sandbox)")
    parser.add_argument("--socket", required=True, help="the AF_UNIX path to serve on")
    parser.add_argument("--budget", type=int, default=gym_env.STEP_BUDGET,
                        help="step budget; must equal the CLI's (default %(default)s)")
    parser.add_argument("--deadline-s", type=float, default=None,
                        help="stop answering this many seconds after the server starts")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    parser.add_argument("--receipt", default=None,
                        help="write Relay.receipt here on shutdown")
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    if not Path(args.game).is_file():
        print(f"error: no game file at {args.game}", file=sys.stderr)
        return 2
    stop = threading.Event()

    def _stop(_signum, _frame):
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # not on the main thread
            pass
    with serve(args.game, args.socket, budget=args.budget,
               max_calls=args.max_calls, deadline_s=args.deadline_s) as server:
        print(f"relay ready on {args.socket} (game {args.game}, budget "
              f"{server.episode.budget})", flush=True)
        # The deadline is the transport's: `call` is refused once it passes, and the
        # loop below is only about staying alive until someone stops us.
        while not stop.wait(0.5):
            if (server.deadline_wall is not None
                    and time.monotonic() > server.deadline_wall):
                break
    if args.receipt:
        path = Path(args.receipt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(server.receipt, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
