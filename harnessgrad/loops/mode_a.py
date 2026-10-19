"""Mode A: the platform owns the round loop and calls the method once per round.

One round is one exchange -- hand the method the current harness, take back the one
candidate it names, measure it with the platform's own scorer, and continue from what was
measured. The method is external so that the same method can be applied to any base harness,
and so two methods can be compared from one starting point.

The loop is where "a round that did not complete is not a point" is enforced: the edit and
its measurement both have to happen, or the step is retried and eventually recorded as a
failed step rather than drawn on the curve.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import shlex
import shutil
import sys
import tempfile
import time

from ckpt.git_state import commit_state
from ckpt.git_state import reset_hard
from eval.observe.console import CONSOLE as console
from eval.run.runner import evaluate
from harnessgrad import PLATFORM_API_VERSION
from harnessgrad import code_metrics
from harnessgrad import components
from harnessgrad import config as platform_config
from harnessgrad import policies
from harnessgrad.channel import _stage_for_method
from harnessgrad.environments import _env_record
from harnessgrad.environments import _report_harness_failed
from harnessgrad.environments import _report_invalid
from harnessgrad.identity import _identity, _improver_identity, _improver_runtime
from harnessgrad.methods import _call_method
from harnessgrad.methods import _touched_paths
from harnessgrad.records import _cost_of
from harnessgrad.records import _curve_point
from harnessgrad.records import _keep_method_logs
from harnessgrad.records import _require_untampered
from harnessgrad.records import _save_round_diff
from harnessgrad.records import _save_round_state
from harnessgrad.records import _save_traces
from harnessgrad.records import _save_verdicts
from harnessgrad.records import _scored
from harnessgrad.records import level_fields as _level_fields
from harnessgrad.records import test_rate_fields as _test_rate_fields
from harnessgrad.records import _write_curve
from harnessgrad.records import _write_run_meta
import eval.run.candidate as candidate


def _no_loadable_change(changed: list[str], unreachable: list[str]) -> dict | None:
    """A candidate whose every changed file is outside the entrypoint's import closure.

    `unreachable` is `code_metrics.measure()`'s `unreachable_modules`: the `.py` files that
    are not in the entrypoint's transitive import closure. The entrypoint itself is never in
    that list (it is the root of the closure), so a candidate that edited the entrypoint --
    or any module it imports -- comes back clean without needing to be named here, and a
    non-Python change (a dependency, a config file) is not in the list either and therefore
    also comes back clean. Only the fully dead case is reported.

    Measured (`v9-dgm`, 2026-10-05): round 1 scored **0.200 against H0's 0.100** and the
    candidate was `agent.py` byte-identical to H0 plus `agent_backup.py` (a 635-line copy of
    the original) and `agent_new.py` (an 88-line fragment nothing imports). The score could
    not have moved because of the edit. A curve point that does not say so reads as a real
    gain, and this platform has already published one false `+0.200` from an unedited tree
    (`docs/experiment_noise.md`).

    Recorded rather than enforced: a harness may load a module by scanning a directory, which
    no import graph can see, so this is a strong claim and not a proof. What it is for is the
    reading -- nothing the entrypoint can load is different, so the score is not evidence
    about the edit.
    """
    if not changed or not unreachable:
        return None
    dead = set(unreachable)
    if not all(path in dead for path in changed):
        return None
    return {
        "changed": sorted(changed),
        "why": ("every file this candidate changed is outside the entrypoint's import "
                "closure, so nothing the entrypoint can load is different: the score is "
                "not evidence about this edit"),
    }


def _identical_prior(curve: list[dict], sha: str, trials: int) -> dict | None:
    """The earlier point that already measured **this exact harness**, or None.

    `identity.harness_sha` is a *content address* -- `ckpt.git_state.commit_state`
    returns the tree hash, not the commit sha, precisely so that "the same harness" has
    one name. So equality here means the candidate carries byte-identical content to a
    state this run already measured, and measuring it again would buy a replicate of a
    number the record already holds.

    The trials count is part of the question: a single measurement is not the mean of
    three, and reusing one for the other would answer something the run did not ask.
    """
    for point in reversed(curve):
        if (point.get("identity") or {}).get("harness_sha") != sha:
            continue
        if (point.get("sampling") or {}).get("trials", trials) != trials:
            continue
        return point
    return None


def _copy_round_traces(run_dir: Path, source_round: int, target_round: int) -> None:
    """Give the reused round its predecessor's traces, so the record stays complete.

    The method's own channel is staged from the base traces (`_stage_for_method`), so this
    is about the run directory a reader inspects -- a round that says "reused" and has no
    traces at all would look like a round that failed to produce any.
    """
    src = run_dir / f"round{source_round}_traces"
    dst = run_dir / f"round{target_round}_traces"
    if src.is_dir() and not dst.exists():
        shutil.copytree(src, dst)


def _keep_endpoint_records(source: Path, run_dir: Path, round_no: int) -> None:
    """Move the endpoint conversations into the run's record, beside the CLI transcripts.

    The method wrote them under `method_root` (its sandbox does not mount the run
    directory); this is the same trip the trajectory takes. Best-effort: a run whose
    records cannot be kept is still a run that ran, so a failure here is logged, never
    raised.
    """
    if not source.is_dir():
        return
    dest = run_dir / "method_logs" / f"round-{round_no}" / "endpoint"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # Recursive: `editor.ask` writes under `<dir>/endpoint/`, so a flat glob here
        # copied nothing -- the same silent-empty record the move exists to prevent.
        for path in sorted(source.rglob("*.json")):
            shutil.copy2(path, dest / path.name)
    except OSError as exc:
        console.note(f"could not keep the endpoint records of round {round_no}: {exc}",
                     level="warn")
        return
    shutil.rmtree(source, ignore_errors=True)


def _run_mode_a(args, work, man, run_id, mode, curve, h0_sha, base, tasks,
                task_ids, scorable, sampling, cache, cumulative,
                train_ids=None, split=None, setups=None,
                verifiers=None, verifier_kinds=None, envs=None,
                improver=None, start_steps: int = 0,
                resumed_from: int | None = None) -> list[dict]:
    """Mode A: the platform owns the loop and calls an EXTERNAL method each round.

    One round = hand the method the current harness, take back the one candidate
    it produced, measure it, and continue from there. The method is external for
    the same reason it is in mode B: so the same method can be applied to any base
    harness, and so two methods can be compared from one starting point.

    Mode A and mode B differ only in who owns the schedule. Here the platform
    decides how many rounds to run and what each starts from; the method decides
    what one step is. A method that owns its own search -- RRSI with its annealed
    budget, DGM with its archive -- runs in mode B, because re-wiring its
    schedule would change the thing being measured.
    """
    argv = args.method_entrypoint
    if not argv:
        print("mode A needs --method-entrypoint pointing at an external method",
              file=sys.stderr)
        return curve
    if isinstance(argv, str):
        argv = shlex.split(argv)

    # The method contract's version is the platform's to declare. A harness has
    # nothing to do with methods any more, so asking a harness for it would be
    # asking the wrong artifact -- and after round 1 the workspace holds a
    # candidate anyway.
    api_version = PLATFORM_API_VERSION
    run_dir = Path(args.runs_root) / run_id
    # Where the method's inputs go: outside the platform **and outside the work root**.
    #
    # Two moves, for two different reasons, and both were forced by trying to sandbox
    # a method:
    #
    #   * out of the run directory, because that is inside the platform and hiding the
    #     platform would have hidden the method's own inputs;
    #   * out of the work root, because the sandbox re-binds the work root read-only
    #     *after* opening the writable hole -- and a read-only bind of a parent wins
    #     over a writable bind of its child. Measured: a method got `Read-only file
    #     system` writing its own trajectory.
    #
    # A temporary directory has neither property, and `task_dir` re-binds it after the
    # hides, which is the same mechanism the harness's own working directory uses.
    method_root = Path(tempfile.mkdtemp(prefix=f"hg-method-{run_id}-")).resolve()
    # **The pristine tree, for the whole run.** Mode B handed the method H0 and nothing else;
    # mode A hands the current incumbent. Those are two different search strategies and both
    # are legitimate -- DGM samples parents from an archive, RRSI builds on the state it
    # decided to keep -- so the request now carries *both* and the method chooses. One copy
    # per run, made before any step, because it must not move as the run goes on.
    pristine = method_root / "base_harness"
    shutil.copytree(work, pristine, ignore=shutil.ignore_patterns(".git"))

    #: Rounds whose step did not finish -- the edit sequence never applied. Recorded
    #: rather than only printed: "3 rounds but 2 steps" is a fact a reader comparing two
    #: curves needs, and the console's scrollback is not a record.
    failed_steps: list[dict] = []

    #: How many times **one step** may be attempted before the run gives up on it.
    #:
    #: A step is two phases (propose, then apply) and **only a step that finished both
    #: advances the counter** -- `r` is always `steps_done + 1`, so the curve's rounds
    #: stay contiguous and the method's own round index keeps matching the record.
    #:
    #: Measured, and the reason this is a retry rather than a `break`: a 5-round run
    #: ended after one round because a single reply came back as prose instead of JSON.
    #: `editor.fail` deliberately passes `stop=False` for exactly this reason -- "ending
    #: a run on one transient blip would cost a method the rest of its budget" -- and a
    #: driver that broke out of the loop contradicted the method's own intent.
    #:
    #: **Per step, not per run.** The first version of this bound the *total* attempts at
    #: `rounds * STEP_ATTEMPTS` while the comment claimed it was per step. Measured: a run
    #: whose step 4 kept hitting `APITimeoutError` had spent 1h31m and was still going,
    #: because steps 1-3 needed one attempt each and left the whole remaining quota to one
    #: stuck step. A budget that one step can consume is not a budget for the step.
    STEP_ATTEMPTS = 3
    #: **The budget counts *measured states*, not method calls.**
    #:
    #: `--rounds N` used to mean "call the method N times, one candidate each". That is the
    #: one shape an RSI rule cannot use: its content is *how to spend a budget under noise*
    #: (RRSI's annealed `b_t`, DGM's child counts, SICA's confidence bound). Mode B went the
    #: other way -- the method owned the schedule, handed over a whole sequence, and got no
    #: scores to spend the budget on. Two halves of one loop, and neither could express a
    #: selection rule.
    #:
    #: Now one engine: a submission carries one or more steps, every step is measured in
    #: order, and the method is called again **iff** the budget has room and it did not
    #: stop. One step per call is the old mode A (feedback between candidates); ten steps in
    #: one call is the old mode B (a batch, no feedback inside it); three and then three
    #: more is what neither mode could say.
    def _invoke_method(r: int):
        """One method call: stage the evidence, hand over a pristine base, parse the reply.

        Returns `(reply, trajectory)`, or `(None, None)` when the call itself failed -- the
        caller reads that as "no submission this attempt" and retries, bounded by
        `STEP_ATTEMPTS`. Kept out of the loop body so the loop can be about the *budget*.
        """
        prior = curve[-1]
        # The whole curve so far, not just the incumbent: a method whose contribution
        # is its selection rule needs the series, and the cheap way to give it is the
        # points already recorded rather than a new mechanism.
        #
        # Traces, but only the train half. Everything about the split is
        # bookkeeping except this line: a method that can read an eval trace can
        # edit the harness against the tasks it is about to be scored on, and the
        # curve that results is indistinguishable from improvement.
        _stage_for_method(work, prior, base["traces"], history=curve,
                          task_ids=train_ids, run_dir=run_dir,
                          tasks=tasks, setups=setups, verifiers=verifiers,
                          result=base)

        base_dir = (method_root / f"round{r}_base").resolve()
        if base_dir.exists():
            shutil.rmtree(base_dir)
        shutil.copytree(work, base_dir, ignore=shutil.ignore_patterns(".git"))

        method_ws = (method_root / f"round{r}_ws").resolve()
        method_ws.mkdir(parents=True, exist_ok=True)
        traj_path = (method_root / f"round{r}_trajectory.json").resolve()

        t_gen = time.time()
        # The method call is the other long silence in a round, and it is the one where
        # "is it thinking or is it dead?" is hardest to answer from outside: the method
        # is an external process the platform does not log. A phase record here is the
        # difference between a 20-minute blank and "RRSI is still generating".
        with console.reporter.phase("agent", round_no=r, task_id="method",
                                   budget_s=args.method_timeout):
            reply = _call_method(work, argv, {
                "platform_api_version": api_version,
                "mode": "A",
                "base_harness": str(base_dir),
                # H0, unchanged since the run started. A method whose rule picks its own
                # parent (DGM's archive, HyperAgents' population, Meta-Harness's frontier)
                # needs this; one that always refines the incumbent ignores it.
                "pristine_harness": str(pristine),
                "workspace": str(method_ws),
                "round_index": r,
                "incumbent_score": prior["score"],
                # How many states this run will still measure. A method that plans its own
                # budget -- RRSI's `b_t`, DGM's child counts -- needs the size of the pot,
                # and until now the only number it got was "which round this is".
                "epochs_total": args.rounds,
                "epochs_measured": steps_done,
                "task_ids": task_ids,
                "train_task_ids": list(train_ids or []),
                "trajectory_out": str(traj_path),
                # **The improver the platform resolved, by path as well as by name.**
                # A method cannot look it up for itself: its sandbox hides the platform,
                # so `tools/improver.py` is not on its disk and neither is
                # `improvers/skill.md` (which travels in the channel). Handing it over
                # is also what keeps the record honest -- this is the same resolution the
                # curve point names, so the method cannot run a different one.
                "improver": _improver_identity(improver),
            }, args.method_timeout, sandboxed=args.sandbox,
                scratch=method_root, cwd=method_ws, readonly=(base_dir,),
                runtime=_improver_runtime(improver), improver=improver)
        gen_s = time.time() - t_gen
        cumulative["wall_clock_s"] += gen_s
        cumulative["generation_tokens"] += int(reply.get("generation_tokens") or 0)
        # Before anything is decided about this round: the method's own log is evidence
        # either way, and the directory holding it is deleted when the round ends.
        _keep_method_logs(method_ws, run_dir, r)

        if "_error" in reply:
            # The stderr tail goes in the *note*, not only in the `detail` event beside
            # it. Both are recorded, but a reader of `console.log` sees the notes and not
            # the details -- measured on the first `--improver codex` run, whose three
            # failures read as a bare `exit 1` in the log while the reason
            # ("no usable improver") sat one event away.
            stderr = " ".join((reply.get("_stderr") or "").split())
            why = reply["_error"] + (f" -- {stderr[-400:]}" if stderr else "")
            console.note(f"round {r}: method entrypoint failed: {why}", level="error")
            console.detail(**{k: reply.get(k) for k in ("_stderr", "_stdout")})
            console.round_line(r, "entrypoint failed", level="error",
                               note=stderr[-120:])
            failed_steps.append({"round": r, "why": f"entrypoint failed: {why}"[:400]})
            return None, None, 0.0
        if not traj_path.exists():
            console.note(f"round {r}: method wrote no trajectory", level="error")
            console.round_line(r, "no trajectory", level="error")
            failed_steps.append({"round": r, "why": "the method wrote no trajectory"})
            return None, None, 0.0

        try:
            traj = json.loads(traj_path.read_text())
        except (OSError, ValueError) as exc:
            console.note(f"round {r}: the trajectory is unreadable: {exc}", level="error")
            failed_steps.append({"round": r,
                                 "why": f"unreadable trajectory: {exc}"[:400]})
            return None, None, 0.0, 0.0
        return reply, traj, gen_s

    #: What the method said about its own search, recorded from the first trajectory that
    #: declares it. Mode B read these from the trajectory; with one engine there is no mode
    #: to imply them, so they come from the method -- which is where they were always true.
    declared_shape: dict = {}
    # **A resumed run continues the count, it does not restart it.** `--rounds` is the
    # total number of measured rounds, so re-running the same command after a crash picks
    # up at `last + 1` instead of appending a second set of rounds.
    steps_done, step_attempts = start_steps, 0
    pending: list[dict] = []      # steps of the current submission, not yet measured
    submission_stop = False       # that submission asked to finish
    steps_in_submission = 0
    steps_truncated = 0
    while steps_done < args.rounds:
        if pending:
            r = steps_done + 1
            step = pending.pop(0)
            step_index = steps_in_submission - len(pending)
        else:
            if submission_stop:
                break
            r = steps_done + 1
            step_attempts += 1
            if step_attempts > STEP_ATTEMPTS:
                console.note(
                    f"step {r}: gave up after {STEP_ATTEMPTS} attempts", level="error")
                console.round_line(r, "gave up", level="error")
                failed_steps.append({
                    "round": r,
                    "why": f"no completion in {STEP_ATTEMPTS} attempts; the run stops here "
                           f"because a step that cannot finish is not a step"})
                break
            # **Where an endpoint call keeps its conversation.** The CLI improver's
            # transcript already lands in this round's `method_logs`; a proposal that goes
            # through the chat endpoint has no such transcript, so the method is told where
            # to write one. Cleared afterwards so a later call cannot inherit a stale path.
            # **The record is written where the method can actually write.** Its sandbox
            # does not mount the run directory -- the trajectory travels the same way, into
            # `method_root` under /tmp -- so pointing this at `runs/...` made every write
            # fail silently (measured 2026-10-18: zero endpoint records for a run whose
            # design call is `endpoint_only=True`). It lands beside the trajectory and is
            # copied into the run's own `method_logs` right after the call.
            call_log_dir = method_root / f"round{r}_endpoint"
            os.environ["HG_CALL_LOG_DIR"] = str(call_log_dir)
            try:
                reply, traj, gen_s = _invoke_method(r)
            finally:
                os.environ.pop("HG_CALL_LOG_DIR", None)
                _keep_endpoint_records(call_log_dir, run_dir, r)
            if reply is None:
                continue
            # **The declared rule is nested, and this loop looked at the top level.** The
            # producer is `methods/editor.py:report`, which writes
            # `trajectory["provenance"]["acceptance_rule"] = {"text", "source",
            # "calibrated"}`; the consumer here read `trajectory["acceptance_rule"]`. So the
            # key was never found, `run_meta.acceptance_rule` was written by nothing, and
            # because the trajectory lives in a scratch directory the driver deletes at the
            # end of the round, **the rule a method declared reached no durable record at
            # all**. Measured 2026-10-06: `grep -l acceptance_rule runs/*/run_meta.json` is
            # empty across 84 runs, including the ones whose method declared one.
            #
            # Both spellings are accepted rather than moving the writer: a trajectory from a
            # method that put it at the top level is not wrong, and the record is more useful
            # than the convention.
            provenance = traj.get("provenance")
            provenance = provenance if isinstance(provenance, dict) else {}
            for key in ("trajectory_shape", "curve_drawn", "nominated",
                        "selection_pool_size"):
                if key in traj and key not in declared_shape:
                    declared_shape[key] = traj[key]
            rule = traj.get("acceptance_rule") or provenance.get("acceptance_rule")
            if rule and "acceptance_rule" not in declared_shape:
                declared_shape["acceptance_rule"] = rule
            submitted = list(traj.get("steps") or [])
            if not submitted:
                console.note(f"round {r}: the trajectory has no steps", level="error")
                console.round_line(r, "no steps", level="error")
                failed_steps.append({"round": r, "why": "the trajectory has no steps"})
                continue
            # A single-step submission keeps the old default (`stop` = "nothing changed");
            # a **batch** is a statement that the method had its say, so it stops unless it
            # says otherwise. Without that rule the ports that hand over one step per call
            # would be re-invoked for the rest of the budget.
            changed = bool(reply.get("changed", True))
            submission_stop = bool(reply.get(
                "stop", True if len(submitted) > 1 else not changed))
            room = args.rounds - steps_done
            if len(submitted) > room:
                steps_truncated += len(submitted) - room
                console.note(
                    f"round {r}: the submission carried {len(submitted)} steps and the "
                    f"budget has room for {room}; the rest were not measured (recorded as "
                    f"`steps_truncated`)", level="warn")
                submitted = submitted[:room]
            pending = submitted
            steps_in_submission = len(submitted)
            # One method call can buy several measurements, so its generation time is
            # shared across them: charging it to each step would multiply the method's
            # cost by the batch size, and a cost-aware rule (RRSI's `Delta C`) reading a
            # number four times too large is worse than reading none.
            gen_share = gen_s / max(1, len(submitted))
            step_index = 0
            continue
        # **The method's own name, from where it actually is.** It travels in the
        # trajectory's `provenance`, beside `steps` -- not in a step, and not in the
        # method's stdout (`emit` carries only the platform's own fields). Measured the
        # hard way: the first version of this read `step["method"]` and every point came
        # back with no method at all, which is the gap this is meant to close.
        step_method = ((traj.get("provenance") or {}).get("method")
                       or (step.get("provenance") or {}).get("method")
                       or step.get("method") or reply.get("method"))
        src = step.get("harness_dir")
        if not src or not Path(src).is_dir():
            console.note(f"round {r}: method named no readable harness_dir "
                         f"({src!r})", level="error")
            console.round_line(r, "no readable harness_dir", level="error")
            failed_steps.append({"round": r,
                                 "why": f"no readable harness_dir ({src!r})"})
            continue

        # **A step is two phases, and a half-finished step is not a measurement.**
        #
        # `editor.fail` writes a trajectory whose one step points at the pristine base and
        # carries `method_reported.error`. Measuring that used to produce an ordinary
        # curve point whose label said "method error" and whose score was the base
        # harness's -- a number about a candidate the method never produced. Measured:
        # `UnparseableReply` (the model answered with prose instead of the JSON envelope)
        # was recorded as `round 1  method error  train 0.000`, which reads as a weak
        # harness rather than as a lost round.
        step_error = ((step.get("method_reported") or {}).get("error")
                      or step.get("error"))
        if step_error:
            console.note(f"round {r}: the step did not finish -- {step_error}",
                         level="error")
            console.round_line(r, "step failed", level="error", note=str(step_error)[:120])
            failed_steps.append({"round": r, "why": str(step_error)[:400]})
            continue

        # The channel belongs in the method's input tree and nowhere else. `base_dir` is
        # already a copy of it, so `work` -- the tree that gets committed and measured --
        # can be rid of it immediately rather than at the swap. Before this, a run whose
        # last attempt failed ended with the goal and the verifier's gold sitting in its
        # final workspace.
        shutil.rmtree(work / "_harnessgrad", ignore_errors=True)

        # Swap the candidate in and measure it as the platform's own.
        reset_hard(work, h0_sha)
        for child in list(work.iterdir()):
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(Path(src), work, dirs_exist_ok=True)
        # **The channel is never part of a measured tree.** `src` is method-supplied, so
        # it can name anything -- and on the error path `editor.fail` names the pristine
        # `base_dir`, which is a copy of `work` taken *after* staging and therefore
        # carries `_harnessgrad/`. Measured: a failed round put the channel back into
        # `work`, and from there into `state_after_round<N>` -- which is the tree an eval
        # run starts from. The harness runs inside `work`, so that put the instruction and
        # the verifier's gold inside the subject's own reach; §4.7's boundary is the
        # channel **to the method**, not a directory the artifact can read.
        shutil.rmtree(work / "_harnessgrad", ignore_errors=True)

        # **The platform re-checks the candidate, because the method is not trusted.**
        # The method validates its own output too (`methods/editor.py`), which is what
        # lets it re-propose before reporting -- but a check performed by the thing being
        # measured is a claim, and this is the platform's own. A candidate that is not a
        # harness is recorded as **unmeasured** rather than scored: there is no
        # measurement to report, and a `0.000` here would be indistinguishable from a
        # harness that ran and was weak (§2.5.7's distinction, one level up).
        candidate_problems = candidate.validate(work, base_manifest=man)
        has_manifest = not candidate_problems
        sha = commit_state(work, f"round {r}: {step.get('label', 'method step')}")
        diff = _save_round_diff(run_dir, r, work,
                                curve[-1]["identity"]["harness_sha"], sha)
        # **What changed, measured -- as opposed to what the method said it changed.**
        #
        # `edits_applied` used to be the method's own `files` list, and on `B2-llm`
        # (2026-10-04) that list said `["agent.py"]` for two rounds whose patch was
        # **0 bytes** and whose `harness_sha` never moved: the local model rewrote the
        # same broken file with the same broken content, and the record read as two new
        # candidates that were never new. The diff is the platform's own, it is already
        # computed for the panel, and it cannot be wrong about whether the tree moved.
        # The method's claim is kept beside it (`files_claimed`) rather than dropped: a
        # method that names a file it did not change is itself a finding.
        changed_files = [f["path"] for f in (diff.get("files") or [])]
        _save_round_state(run_dir, r, work)
        # **An identical candidate costs nothing to measure.** A method without an early
        # stop that has nothing left to change still has to spend its remaining rounds,
        # and re-running 100 task evaluations to learn a number the record already holds
        # is the most expensive way to say "no change". The point is still written -- the
        # round happened, the method's budget moves, and the curve keeps its shape -- but
        # it carries `reused_from` and the same per-task scores instead of a fresh sample.
        prior_point = (_identical_prior(curve, sha, args.trials)
                       if (has_manifest and getattr(args, "reuse_identical", False)) else None)
        if prior_point is not None:
            reused_from = prior_point.get("round")
            _copy_round_traces(run_dir, reused_from, r)
            console.note(
                f"round {r}: the candidate is byte-identical to round {reused_from} "
                f"({sha[:12]}); reusing that measurement instead of spending "
                f"{len(tasks)} task runs", level="info")
            res = {"per_task": dict(prior_point.get("per_task") or {}),
                   "invalid": [], "harness_failed": [],
                   "trials": (prior_point.get("sampling") or {}).get("trials", args.trials),
                   "reused_from": reused_from,
                   "cached_tasks": sorted(prior_point.get("per_task") or {})}
        else:
            t0 = time.time()
            res = (evaluate(work, tasks, scorable, cache, harness_sha=sha, jobs=args.jobs,
                            trials=args.trials, progress_dir=run_dir,
                            sandbox=args.sandbox, setups=setups, verifiers=verifiers,
                        envs=envs, run_id=run_id, run_seed=os.environ["HARNESSGRAD_SEED"],
                        recordings_root=run_dir / "recordings",
                        round_no=r)
                   if has_manifest else None)
        _require_untampered(res)
        _report_invalid(res, args.dataset)
        _report_harness_failed(res, args.dataset)
        # **Say it about the round, not only about each task.** `_report_harness_failed`
        # prints one line per task, and the curve then still carried an ordinary `0.000`.
        # Measured (`codex-dgm`, 2026-10-05): two rounds whose candidate could not be
        # invoked at all were indistinguishable, on the curve, from two rounds that ran
        # and lost. The distinction decides what a reader does next -- a weak harness is a
        # result, a harness that cannot start is a candidate to reject.
        startup_failure = (res or {}).get("startup_failure")
        if startup_failure:
            skipped = startup_failure.get("not_run") or []
            console.note(
                f"round {r}: the harness never started on any task "
                f"({startup_failure['tasks']} measured, first "
                f"{startup_failure['first_task']}, exit {startup_failure['exit_code']}) -- "
                f"the score stands but it is not evidence about the harness: "
                f"{startup_failure.get('reason')}"
                + (f"; {len(skipped)} task(s) left unrun" if skipped else ""),
                level="error")
        dt = time.time() - t0
        if res is not None:
            cumulative["evaluation_trials"] += len(tasks)
            cumulative["env_generation_tokens"] += (res.get("env_usage") or {}).get(
                "input", 0) + (res.get("env_usage") or {}).get("output", 0)
        cumulative["wall_clock_s"] += dt

        point = _curve_point(
            run_id=run_id, round_index=r,
            identity=_identity(man, sha, mode,
                               method_model=reply.get("method_model") or "n/a",
                               declared=(res or {}).get("identity_declared"),
                               improver=improver,
                               method=step_method,
                               jobs=args.jobs, trials=args.trials,
                               gpu_budget=getattr(args, "gpu_budget", None)),
            sampling=sampling,
            scores=[res["per_task"][t] for t in _scored(res, task_ids)],
            task_ids=_scored(res, task_ids),
            train_scores=[res["per_task"][t] for t in _scored(res, train_ids)],
            train_ids=_scored(res, train_ids),
            invalid=(res or {}).get("invalid"),
            harness_failed=(res or {}).get("harness_failed"),
            model_gateway=(res or {}).get("model_gateway"),
            harness_runtime=(res or {}).get("harness_runtime"),
            evaluation_protocols=(res or {}).get("evaluation_protocols"),
            split=split,
            env_record=_env_record(envs, task_ids if res else []),
            cost=_cost_of(res, len(tasks), dt + gen_share,
                          step.get("claimed_cost")),
            cumulative=cumulative,
            n_trials=(res or {}).get("trials"), score_std=(res or {}).get("score_std"),
            per_task_std=(res or {}).get("per_task_std"),
            per_task_trials=(res or {}).get("per_task_trials"),
            cached_tasks=(res or {}).get("cached_tasks"),
            **_test_rate_fields(res),
            **_level_fields(res, _scored(res, task_ids)),
            label=step.get("label", ""),
            hypothesis=reply.get("hypothesis"),
            edits_applied=changed_files,
            files_claimed=reply.get("files"),
            candidate_problems=candidate_problems,
            method_reported=step.get("method_reported") or {})
        point["edit_kind"] = step.get("edit_kind")
        # **A reused measurement says so.** The score is the earlier state's score, so a
        # reader -- or a statistic like "best of N rounds" -- must be able to tell a
        # replicate from an independent observation. `measured_by_platform` stays true:
        # the platform did measure this harness, just at an earlier round.
        if resumed_from is not None:
            # Every point this invocation writes came after a resume; the marker keeps a
            # stitched curve readable as stitched.
            point["resumed_from"] = resumed_from
        if (res or {}).get("reused_from") is not None:
            point["reused_from"] = res["reused_from"]
            point["reused_reason"] = "candidate content identical to an already-measured state"
        # **The shape of the submission this step came from.** `steps_in_submission > 1`
        # means the method handed over a batch and therefore saw no score between these
        # states -- the one thing that separates "used the feedback" from "guessed a
        # path", and neither mode A nor mode B could record it before.
        point["step_index"] = step_index
        point["steps_in_submission"] = steps_in_submission
        # **Which state the method decided to keep.** A selection rule's whole content is
        # accept-or-keep, and the platform already let a method act on it (hand back an
        # earlier tree from `states/`, which is what `harnessx`'s port does). What was
        # missing is the record: `score` is the *candidate's* measurement, so a curve of
        # candidate scores cannot show what the rule kept. A step may name
        # `incumbent_round`; the point then carries that round and its score, and the
        # "kept" series can be read beside the measured one.
        kept = step.get("incumbent_round")
        if kept is None:
            point["kept_round"] = r
            point["kept_score"] = point.get("score")
        else:
            try:
                kept_round = int(kept)
            except (TypeError, ValueError):
                kept_round = r
            kept_point = next((q for q in curve if q.get("round") == kept_round), None)
            point["kept_round"] = kept_round
            point["kept_score"] = (kept_point or {}).get("score")
        # **What the round says about the *harness*, not about the tasks.** `None` unless
        # no task in the round got the harness started at all (`eval/run/runner.py` decides).
        # On the point because "the candidate never ran" and "the candidate ran and lost"
        # are both `score 0.000` and only one of them is a measurement of quality.
        point["startup_failure"] = startup_failure
        point["measured_by_platform"] = res is not None
        if res is None:
            # Named problems, not "no harness.json": a candidate can be rejected for a
            # manifest that no longer parses, an entrypoint that is gone, Python that does
            # not compile, or a widened `env_kinds`. The old single sentence covered one
            # of those four, so the other three reached a reader as `score 0.000`.
            point["unmeasured"] = {
                "reason": "the candidate harness is not one the platform can measure, so "
                          "there is no measurement to report",
                "problems": candidate_problems,
                "harness_dir": src,
            }
        point["verifier_kinds"] = verifier_kinds
        # Against the **previous round**, not against H0. Measured: with H0 as the
        # baseline, rounds 4 and 5 reported `touched: ["agent.py"]` while their tree was
        # byte-identical to round 3's -- a reader concludes "the method edited something"
        # from a round where nothing moved. `_save_round_diff` already got this right
        # (`from:` is the previous point's tree); this field did not, and it is the one
        # the method-facing page quotes as "what moved this round".
        point["editable_surface_touched"] = _touched_paths(
            work, curve[-1]["identity"]["harness_sha"], sha)
        # **Which component moved, when the harness says which is which.** `components` in
        # `harness.json` is a map for reading, never a fence: the platform's design is that a
        # harness is *entirely* editable -- `INTERFACE.md` §4.1-4.3 draws the boundary around
        # a harness's `method/` directory and its `accept` rule and nowhere else -- so a path
        # the map does not name is recorded as `unattributed` rather than refused. Without
        # this, "the harness evolved" has no referent: `editable_surface_touched: ["agent.py"]`
        # says a file moved but not which of the ten components improved.
        #
        # Read from `work`. `harness.json` is a protected file (`cli_improver.propose` reports
        # it, and every method prompt forbids editing it), so the map cannot legitimately
        # change under a round's feet; if a method changes it anyway, the path appears in
        # `editable_surface_touched` and in `unattributed` where a reader can see it.
        try:
            point["editable_surface_components"] = components.attribute(
                point["editable_surface_touched"], components.declared_components(work))
        except components.ComponentDeclarationError as exc:
            # A malformed declaration is a fact about the harness, not a reason to lose the
            # round's measurement. The raw paths are already on the point above.
            point["editable_surface_components"] = {
                "components": [], "unattributed": point["editable_surface_touched"],
                "declaration_error": str(exc)}
        # ------------------------------------------------------------------
        # Anthropic's two evaluator-side policies, **recorded rather than enforced**
        # (`harnessgrad/policies.py` states why): a geometric mean whose lowest term binds,
        # and the capability-floor check that disqualifies a candidate "whatever its score"
        # when its interval on any task lies entirely below the baseline's. This platform has
        # no acceptance rule -- the method owns that -- so the numbers go on the point and a
        # method that wants the gate reads them from there.
        #
        # The baseline is the run's own H0 point by default: it is measured on the same task
        # set, by the same platform, and it is the thing "did this edit help" is already
        # about. `--baseline` replaces it with a **frozen reference** -- one point, measured
        # once and reused by every method that follows. That is what cross-method comparison
        # needs: a per-run H0 puts every method on its own axis, which is the confounding this
        # platform exists to remove. The price is that nothing re-measures the frozen
        # reference, so this run checks and records whether it still describes the run in
        # front of it (`reference_problems`) rather than assuming it does.
        #
        # Recorded only where a baseline exists and both sides have the studied breakdown, so
        # H0 itself and an eval-side point carry neither field rather than a null.
        studied = policies.studied_per_task(point)
        if args.baseline:
            try:
                baseline_point = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                console.note(f"--baseline {args.baseline!r} is not a readable curve point: "
                             f"{exc}", level="error")
                return 2
            if not isinstance(baseline_point, dict):
                console.note(f"--baseline {args.baseline!r} must hold one curve-point object",
                             level="error")
                return 2
            baseline_kind = "frozen"
        else:
            baseline_point = curve[0] if curve else None
            baseline_kind = "same-run"
        baseline_studied = (policies.studied_per_task(baseline_point)
                            if baseline_point else {})
        if res is not None and studied and baseline_studied:
            # One description of the reference, so the two statistics below name the same
            # thing and a reader does not have to reconcile them.
            point["baseline"] = {
                "kind": baseline_kind,
                "run_id": baseline_point.get("run_id"),
                "round": baseline_point.get("round"),
                "path": args.baseline or None,
                "problems": policies.reference_problems(
                    baseline_point, point,
                    reference_tasks=baseline_studied, current_tasks=studied),
            }
            point["score_geomean"] = round(policies.geomean(list(studied.values())), 6)
            point["capability_floor"] = {
                "baseline": f"round-{baseline_point.get('round', 0)}",
                **policies.floor_violations(
                    studied, baseline_studied,
                    candidate_std=(res.get("per_task_std") or {}),
                    baseline_std=(baseline_point.get("per_task_std") or {})),
            }
            # **The other direction.** `capability_floor` records only where a candidate is
            # *below* the baseline, because Anthropic's criterion is a disqualification.
            # Nothing asked where it is above, and `score_ci95` cannot answer it: that interval
            # is computed over *tasks*, so it does not shrink when a run repeats them -- on
            # alfworld's 134 tasks at a mean of 0.47 it is roughly +/-0.09 wide, which is
            # wider than the effect a harness edit produces. The same tasks appear on both
            # points, so the comparison is paired and the discordant pairs are the signal.
            # Recorded beside the floor, not instead of it: one says "this candidate is
            # safely no worse", the other says "this candidate is better than chance allows".
            point["improvement"] = {
                "baseline": f"round-{baseline_point.get('round', 0)}",
                **policies.improvement(studied, baseline_studied),
            }
        # **What the candidate is, as code.** HarnessDev measured this on the harnesses
        # their creators wrote: "of 169 new functions or classes, 113 are reachable from
        # the entry point, 31 are reachable only through dead code, and 25 have no caller"
        # (§4.3). Without it, a candidate that appended 25 uncalled functions scores
        # exactly like one that changed nothing -- and dead code that never runs looks
        # like work in a diff, in a token count, and in "14 edits this round". Recorded
        # from the tree the platform is about to measure, including when the candidate was
        # rejected (`src` is set before validation), because "what did the method actually
        # do" is the question a rejected candidate raises. A diagnostic, never a score:
        # `harnessgrad/code_metrics.py` states the three ways static analysis is wrong.
        if src:
            point["candidate_code"] = code_metrics.measure(Path(src))
            # **A candidate the entrypoint cannot load.** Measured (`v9-dgm`, 2026-10-05):
            # round 1 scored 0.200 against H0's 0.100 -- a `+0.100` improvement -- and the
            # candidate was `agent.py` **byte-identical to H0** plus two new files nothing
            # imports (`agent_backup.py`, a 635-line copy of the original, and
            # `agent_new.py`, an 88-line fragment). The score could not have moved because
            # of the edit, and a reader without this field has no way to tell that from a
            # real gain. The same class of dead work HarnessDev counted, at file
            # granularity, from an analysis the platform already runs.
            #
            # Recorded, not enforced: a harness may load a module by scanning a directory,
            # which no import graph sees, so this is a strong claim and not a proof. What it
            # is *for* is the reading -- a curve point that says "nothing the entrypoint can
            # load changed" cannot be evidence of an improvement, whatever its score.
            unloadable = _no_loadable_change(
                changed_files, (point["candidate_code"] or {}).get("unreachable_modules") or [])
            if unloadable:
                point["no_loadable_change"] = unloadable
        curve.append(point)
        _write_curve(run_dir / "curve.jsonl", curve)
        # The step finished both phases, so it counts -- and the next step starts with a
        # fresh attempt budget rather than inheriting this step's leftovers.
        steps_done += 1
        step_attempts = 0

        if res is not None:
            base = res
            _save_verdicts(run_dir, r, res, task_ids)
            _save_traces(run_dir, r, res.get("traces") or {}, train_ids, split,
                         trial_traces=res.get("trial_traces"))
        # `changed: false` was doing two jobs, and mode A conflated them:
        #
        #   "I have nothing more to say"   a method that is finished
        #   "I reject this candidate"      a method whose contribution IS a filter
        #
        # The second must not end the run. RRSI screen-rejects a candidate and then
        # retries after bounded repair; TTHE's rollback gate keeps the incumbent and
        # waits for the next batch. Under the old rule both lost the rest of their
        # budget to a single rejection -- measured: `methods/rrsi` and
        # `methods/tthe` stopped after one method round on a base harness scoring
        # 0.0, while every eager method got three. A platform whose promise is a
        # fair comparison cannot score cautious methods on fewer rounds than eager
        # ones.
        #
        # `stop` was decided when the submission was read, because it is a statement about
        # the *submission*, not about this step: a batch of three is finished when all
        # three are measured, and each of them is still a step. The note is per step.
        note = reply.get("note")
        if note is not None:
            note = str(note)[:160]
        elif submission_stop and not pending:
            note = "no change reported" if not changed else "method asked to stop"
        elif not changed:
            note = "no change this round; continuing"
        console.round_line(r, step.get("label") or "method step", point, note=note)
        if submission_stop and not pending:
            break

    reset_hard(work, h0_sha)
    shutil.rmtree(method_root, ignore_errors=True)
    _write_run_meta(
        Path(args.runs_root) / run_id,
        # The run's final write carries the same effective configuration as the
        # driver's first write. Written here too because this is the write that
        # completes the record; a reader of the finished file must not have to know
        # which call site happened to include it. Credentials are present/absent only.
        config=platform_config.dump(
            run=platform_config.run_config(
                args, run_id, seed=os.environ.get("HARNESSGRAD_SEED"), mode=mode)),
        trajectory_shape=declared_shape.get("trajectory_shape", "sequence"),
        curve_drawn=declared_shape.get("curve_drawn", "per_round"),
        # Who nominated which state, when the method says. `None` means it did not say --
        # which is a fact, and now the *method's* answer rather than the mode's default.
        nominated=declared_shape.get("nominated"),
        **({"acceptance_rule": declared_shape["acceptance_rule"]}
           if declared_shape.get("acceptance_rule") else {}),
        **({"selection_pool_size": declared_shape["selection_pool_size"]}
           if "selection_pool_size" in declared_shape else {}),
        improver=improver or None,
        failed_steps=failed_steps,
        rounds_measured=len([p for p in curve if p.get("measured_by_platform")]),
        # A submission larger than the remaining budget is truncated, and that is a fact a
        # reader needs: the method asked for more states than the run paid for.
        steps_truncated=steps_truncated,
        steps_measured=steps_done,
        final_score=curve[-1].get("score") if curve else None,
        # Both sides of the last measured point. `final_score` alone is the exam
        # only, and a run whose train side moved while this did not is the finding
        # most worth being able to see from the run directory.
        final_train_score=curve[-1].get("train_score") if curve else None,
        split=split,
        wall_clock_s=round(cumulative["wall_clock_s"], 1))
    return curve
