"""One place that decides how a run describes itself.

Why this exists
---------------
A run has two readers and they want different things:

    a person at a terminal    a few aligned lines, as it happens
    the console UI            events it can lay out as chips, rows and a table

Formatting those in two separate places guarantees they drift, and a log that has
drifted from the run is worse than an ugly one: it becomes evidence of something
that did not happen. So the driver narrates once, through here, and the text and
`events.jsonl` come out of the same call.

`events.jsonl` is appended and flushed per event rather than written at the end,
so a run killed mid-round still has an honest record up to the moment it died --
which is exactly the run whose log someone needs to read.

Events are the *record*; the text is a rendering of it. Where the two disagree,
the event is right.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: label column in the header / artifact blocks. 10, not 9: `workspace` is
#: exactly 9 characters and its value ran into the label with no space between.
PAD = 10

_MARK = {"info": "·", "warn": "!", "error": "✗"}

#: Phase labels come from `eval/observe/phases.py`, which owns the vocabulary. There used to be a
#: copy here and another one in the panel's JavaScript; three copies of eight names is how
#: adding a phase turns into a raw identifier on one screen and a translated string on
#: another.
from eval.observe.phases import PHASE_SHORT, PHASE_ZH  # noqa: F401  (re-exported for the panel)


def _secs(v) -> str:
    """`95s`, `12m30s`. Seconds stop being readable somewhere around two minutes."""
    try:
        s = float(v)
    except (TypeError, ValueError):
        return "—"
    if s < 90:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def num(v, nd: int = 3) -> str:
    """Format a score, or `n/a`.

    NaN is spelled `n/a` on purpose: it is what the platform writes when it
    produced no number, and printing `0.000` there would read as a real score.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return "n/a"
    return f"{v:.{nd}f}"


def _split_label(split, n_tasks: int) -> str:
    if not split:
        return f"no train/eval split — all {n_tasks} scored"
    return (f"train {len(split.get('train', []))} / "
            f"eval {len(split.get('eval', []))}")


class Console:
    """Writes the human narrative and the machine record from one call."""

    def __init__(self, events_path=None, stream=None):
        self._stream = stream
        self._fh = None
        self._seq = 0
        #: Set by `reporter` on first use. Held rather than rebuilt per call so that
        #: every progress event has one `seq` sequence with the rest of the run's
        #: events -- two `seq: 1`s in one file is the signature the console UI reads as
        #: "two runs written into one directory" (see `attach`).
        self._reporter = None
        if events_path is not None:
            self.attach(events_path)

    # ---------------------------------------------------------- plumbing ---

    @property
    def out(self):
        return self._stream if self._stream is not None else sys.stdout

    def attach(self, events_path) -> None:
        """Start recording to `events_path`. Called once, before the first event.

        Opened for **truncation, not append**. The per-event flush already gives
        durability within a run, so appending across runs buys nothing -- and it
        cost something real: reusing a run id left the previous run's events in the
        file, so the console rendered two runs as one stream. Measured: a run
        directory recorded as `rrsi` held three `seq: 1` headers and showed a
        previous `DGM` run's rounds, which reads exactly like "I picked RRSI and it
        ran DGM". The driver records `seq` per `Console`, so a second `seq: 1` in
        one file is the signature.
        """
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if events_path:
            path = Path(events_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("w", encoding="utf-8")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def emit(self, kind: str, human: str | None = None, stream=None, **fields):
        """The single writer. Every public method below ends up here."""
        self._seq += 1
        if self._fh is not None:
            record = {"seq": self._seq, "kind": kind, **fields}
            self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
        if human is not None:
            print(human, file=stream if stream is not None else self.out)

    # --------------------------------------------------------- narrative ---

    def header(self, *, run_id, harness, version, mode, dataset, n_tasks,
               model, backend, live=True, sandbox=None, split=None, argv=None,
               side=None):
        """The block that says what is about to be measured.

        Everything a reader needs in order to know whether to trust the numbers
        is here, and nothing that only the UI form knows: the model env block
        used to be copied into the log as `#` comments, which put configuration
        (and a masked key name) in the middle of the run's evidence.
        """
        lines = [run_id, ""]
        lines.append(f"  {'harness':<{PAD}}{harness} {version}")
        lines.append(f"  {'mode':<{PAD}}{mode} · "
                     + ("platform drives the loop" if mode == "A"
                        else "method drives its own loop"))
        # Which side **this run** is about, then what the dataset's split is. The two
        # used to be one line reading "train 4 / eval 2", which on a run that had just
        # been narrowed to two eval tasks read as though the run covered both -- the
        # number that matters is the run's, and the split is context for it.
        lines.append(f"  {'data':<{PAD}}{dataset} · {n_tasks} tasks on the "
                     f"{side + ' side' if side else 'whole set'} · "
                     + _split_label(split, n_tasks))
        lines.append(f"  {'model':<{PAD}}{model or '(none)'} · {backend}"
                     + ("" if live else "   ← not a live model"))
        if sandbox:
            lines.append(f"  {'sandbox':<{PAD}}{sandbox}")
        self.emit("header", human="\n".join(lines) + "\n",
                  run_id=run_id, harness=harness, version=version, mode=mode,
                  dataset=dataset, n_tasks=n_tasks, model=model, backend=backend,
                  live=live, sandbox=sandbox, split=split, argv=argv, side=side,
                  # The panel's progress strip names phases; the vocabulary ships with
                  # the run rather than being duplicated in the panel's JavaScript.
                  phase_labels=PHASE_SHORT)

    def round_line(self, round_no: int, label: str = "", point: dict | None = None,
                   note: str | None = None, level: str = "info"):
        """One round, streamed as it finishes.

        Streamed rather than only tabulated because a live run's whole value is
        watching it happen; the table at the end is the record, this is the
        progress.
        """
        measured = point is not None and point.get("measured_by_platform", True)
        scores = ""
        if point is not None and measured:
            # Named by `score_kind`, like the table. This line said "eval" on every run
            # including training ones, which is the same mislabelling the field exists
            # to prevent -- and it is the line somebody watching a live run reads first.
            kind = "train" if point.get("score_kind") == "training" else "eval"
            scores = f"  {kind} {num(point.get('score'))}"
            if point.get("train_score") is not None:
                scores += f"   train {num(point['train_score'])}"
        elif point is not None:
            scores = "  not measured"
        head = f"  round {round_no}  {label[:26]:<26}{scores}"
        if note:
            head += f"   {_MARK.get(level, '·')} {note}"
        # The method's own sentence for why it did this, when it wrote one. Printed
        # because it answers the question a reader actually has: the label is the
        # method's *summary*, and a method can make that summary misleading -- RRSI
        # writes "RRSI rule: no edit" for a decision its **model** made -- while the
        # hypothesis is the reason. It was recorded nowhere before this.
        hypothesis = (point or {}).get("method_hypothesis")
        if hypothesis:
            head += f"\n{'':>10}{str(hypothesis).strip()[:150]}"
        self.emit(
            "round", human=head, round=round_no, label=label, note=note,
            level=level,
            eval=None if point is None else point.get("score"),
            train=None if point is None else point.get("train_score"),
            # Carried so that `eval=` above is not a mislabel on a training run. The
            # key names are historical and the UI reads them, so they stay; what must
            # not stay is a reader having to know which kind of run they are looking at
            # before they can interpret the number in that key.
            side=(point or {}).get("side"),
            score_kind=(point or {}).get("score_kind"),
            measured=measured,
            harness_sha=(point or {}).get("identity", {}).get("harness_sha"),
            hypothesis=(point or {}).get("method_hypothesis"),
        )

    def results(self, curve: list[dict], split=None, title="results"):
        """The table. Columns that carry no information are not printed.

        `sel` used to be a fixed column reading `?` on every row of every run
        this platform has ever done, because mode A never sets it. A column that
        is constant noise trains the reader to skip the table.
        """
        # `show_train` used to be `bool(split)`, which is now always true and always
        # wrong: under separate sides a point has a real second side never, so the
        # column would be a copy of the first one. What it is for is telling a reader
        # that two sets were measured, so that is what it checks.
        show_train = any(p.get("train_score") is not None for p in curve)
        show_sel = any(
            (p.get("selection_effect") or {}).get("selected_on_reported_set")
            is not None for p in curve)

        # The first column is named for what `score` *is* on this curve. Calling a
        # training score "eval" is the mislabelling `score_kind` exists to prevent.
        first = "train" if (curve and curve[-1].get("score_kind") == "training") else "eval"
        head = f"    {'round':>5}  {first:>7}"
        if show_train:
            head += f"  {'train':>7}"
        head += f"  {'95% CI':<15}  {'by':<9}"
        if show_sel:
            head += f"  {'sel':<4}"
        head += "  harness"

        rows = [f"  {title}", head]
        for p in curve:
            measured = p.get("measured_by_platform", True)
            if measured:
                lo, hi = p["score_ci95"]
                ci = f"[{lo:.2f}, {hi:.2f}]" if lo == lo else "n/a"
                score, by = num(p["score"]), "platform"
            else:
                claimed = (p.get("method_reported") or {}).get("score")
                score = num(claimed) if isinstance(claimed, (int, float)) else "n/a"
                by, ci = "method", "not measured"
            row = f"    {p.get('round', 0):>5}  {score:>7}"
            if show_train:
                row += f"  {num(p.get('train_score')):>7}"
            row += f"  {ci:<15}  {by:<9}"
            if show_sel:
                sel = (p.get("selection_effect") or {}).get(
                    "selected_on_reported_set")
                row += f"  {({True: 'yes', False: 'no', None: '—'}[sel]):<4}"
            row += "  " + (p.get("identity", {}).get("harness_sha", "")[:12]
                           if measured else "(unmaterialized)")
            rows.append(row)
        self.emit("results", human="\n".join(rows) + "\n", curve=curve,
                  title=title, split=split)

    def artifact(self, label: str, path):
        self.emit("artifact", human=f"  {label:<{PAD}}{path}",
                  label=label, path=str(path))

    def note(self, message: str, level: str = "info", human: str | None = None):
        self.emit("note", human=human if human is not None else
                  f"  {_MARK.get(level, '·')} {message}",
                  stream=sys.stderr if level == "error" else None,
                  level=level, message=message)

    def text(self, body: str, stream=None, kind: str = "text"):
        self.emit(kind, human=body, stream=stream, body=body)

    def detail(self, **fields):
        """Key/value context attached to the note that preceded it."""
        self.emit("detail", **{k: v for k, v in fields.items() if v})

    # ---------------------------------------------------------- progress ---

    def log(self, *, source: str, task: str, body: str, exit_code=None,
            round_no: int = 0, detail: str = "", level: str = "info") -> None:
        """Something a subject or a check **printed**, kept as a record.

        The gap this closes, measured on `loop-terminal_bench-26452c`: four of five tasks
        scored 0.0 and the run held no explanation for any of them. The harness's stdout
        and stderr were read into `result["stderr"]` and dropped unless the exit code was
        non-zero, and the verifier's own output only ever reached a `verdict["detail"]`
        that was never persisted. So "why is this 0?" could only be answered by
        re-running the task by hand. A score without the output that produced it is a
        number a reader has to take on faith.

        Kept as its own kind rather than folded into `note` because it carries a body
        (which can be long) and a source (harness / check / method). The terminal gets one
        line; the panel gets the body and renders it collapsed.
        """
        first = ""
        for line in (body or detail or "").splitlines():
            if line.strip():
                first = line.strip()[:110]
                break
        head = f"  {_MARK.get(level, '·')} {source} · {task}"
        if exit_code is not None:
            head += f" · exit {exit_code}"
        if first:
            head += f" — {first}"
        self.emit("log", human=head, stream=sys.stderr if level == "error" else None,
                  source=source, task=task, exit_code=exit_code, round=round_no,
                  detail=str(detail or "")[:2000], level=level,
                  body=str(body or "")[:20000])

    def progress(self, kind: str, **fields) -> None:
        """One progress record from `eval/observe/progress.py`.

        Split from the other event kinds on purpose: this is the only writer a *long*
        run uses, so it must be cheap and it must never print. Measured on a
        100-minute round of five tasks: every phase boundary plus a beat every 10s is
        ~40 events per task, and putting those on stdout would bury the round table
        that is the thing a person scrolls back for. The panel gets all of them; the
        terminal gets the four that say a task finished or went over budget.
        """
        # Local import: `eval/observe/progress.py` imports nothing from here (its `emit` is
        # injected), so this is not a cycle -- it is here because only this method needs
        # the vocabulary, and importing it at module scope would make `console` depend
        # on the progress module for every other event.
        from eval.observe.progress import PHASE_BUDGET_S

        task = fields.get("task") or ""
        round_no = fields.get("round", 0)
        who = f"round {round_no} · 第 {fields.get('index', '?')}/{fields.get('total', '?')} 题 {task}"

        if kind == "task":
            human = None
            if fields.get("status") != "start":
                detail = fields.get("detail") or ""
                human = (f"  {_MARK['error'] if fields.get('status') == 'failed' else '✓'} "
                         f"{who} — {fields.get('status')} "
                         f"({_secs(fields.get('elapsed_s'))})"
                         + (f" — {detail}" if detail else ""))
                stream = sys.stderr if fields.get("status") == "failed" else None
                self.emit("task", human=human, stream=stream, **fields)
                return
            self.emit("task", human=None, **fields)
            return

        if kind == "phase":
            if fields.get("status") == "start":
                self.emit("phase", human=None, **fields)
                return
            human = None
            # A phase that failed is the one progress line a person *must* see: it is
            # the difference between "still working" and "the harness never ran".
            if fields.get("status") == "failed":
                human = (f"  {_MARK['error']} {who} · "
                         f"{PHASE_ZH.get(fields.get('phase'), fields.get('phase'))} "
                         f"失败 ({_secs(fields.get('elapsed_s'))}) — "
                         f"{fields.get('detail') or ''}")
            self.emit("phase", human=human,
                      stream=sys.stderr if human else None, **fields)
            return

        if kind == "over":
            # Not a separate record. This *is* a note -- one line a person reads as it
            # happens -- and emitting both kinds for it would put the same sentence in
            # the file twice, which the panel would then render twice. The fields stay,
            # so a panel that wants to mark the phase by name still can.
            budget = fields.get("budget_s") or PHASE_BUDGET_S.get(fields.get("phase"))
            message = (f"{who} · {PHASE_ZH.get(fields.get('phase'), fields.get('phase'))} "
                       f"已跑 {_secs(fields.get('elapsed_s'))},超过它自己声明的 "
                       f"{_secs(budget)} —— 不是失败,但也不再是「看起来卡住」了。")
            self.emit("note", human=f"  {_MARK['warn']} {message}", level="warn",
                      message=message, **fields)
            return

        # Beats. Recorded, never printed: one every 10s for an hour is 360 lines.
        self.emit(kind, human=None, **fields)

    @property
    def reporter(self):
        """A `progress.Reporter` that writes into this console's event stream.

        One per console, built on first use. It used to be built per call, which was
        invisible while only the driver used it and would have been a bug the moment
        two callers each held one: `seq` is per console, so two reporters would still
        share the counter through `emit` -- but two objects meaning one stream is a
        thing to get wrong later, and there is no reason to want two.
        """
        from eval.observe.progress import Reporter
        if self._reporter is None:
            self._reporter = Reporter(self.progress)
        return self._reporter


#: The driver's console. `main()` attaches it to the run's events file; anything
#: imported before that still prints, it just records nothing yet.
CONSOLE = Console()
