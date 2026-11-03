"""ADAS: the meta agent reads every design it has measured, then builds the next.

    score the harness this run started from; it seeds the archive
    repeat max_steps times:
        hand the best design so far to the meta agent as a directory
        it edits freely, then reflects on its own edit num_reflections times
        score the result on dev and add it to the archive, whatever it scored
    promote the highest-scoring design in the archive

Every other method here reasons from the *current* version: a rollout, its own
last edit, a population of prompts. ADAS reasons from the whole history of
designs it has built -- the archive goes into the prompt in full, and the point
of a design that scored badly is that the next round can see it did.

This is a port of the paper's mechanism, not of its code. Upstream's meta agent
writes a ``forward()`` function into a fixed Python framework; here a design is
the harness directory itself, which is why the Worker must be one that can edit
files. What is preserved is the search: a cumulative archive, a meta agent that
reads it to propose the next design, and admission by measured score. See
docs/adas.md for the full list of differences.

Upstream: https://github.com/ShengranHu/ADAS
Commit: TODO -- pin the reviewed upstream revision (network access unavailable
    during this migration; see docs/adas.md).
"""

from __future__ import annotations

from flashrsi.algorithms import _replies
from flashrsi.algorithms.adas import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot
from flashrsi.core.snapshot import discard_runtime_files


class ADAS(Algorithm):
    """Meta Agent Search: grow an archive of scored agent designs.

    The archive is the method. A proposal is written against every design the
    search has measured, so the meta agent can tell an idea that has been tried
    from one that has not, and a low score is information rather than a dead
    end.

    Admission is by score, which is what separates this from AHE: both hand the
    harness to a coding agent as a directory, but AHE keeps every well-formed
    edit and holds it to its own predictions, while here an edit is measured on
    dev and only the best design in the archive is promoted at the end.
    """

    name = "adas"
    description = "Search agent designs by growing an archive of scored ones."
    # Every component the target declares: a new design may need a file that
    # does not exist yet, and only the target knows how it lays them out.
    evolve_scope = {"*"}
    _defaults = {
        "max_steps": 10,
        # Upstream reflects twice on a fresh design before measuring it.
        "num_reflections": 2,
        "max_evidence_chars": 30000,
    }
    _ranges = {
        "max_steps": (int, 1),
        "num_reflections": (int, 0),
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        dev = self.benchmark.dev
        current = self.target.snapshot
        seed = await self._evaluate(current, dev, label="seed")
        # An archive row is what the meta agent reads: a design's own account
        # of itself and what it scored. The leading design is tracked beside
        # the archive, because only a Snapshot can be checked out or promoted.
        archive = [
            {"account": "the harness this run started from", "score": seed.score}
        ]
        best, best_score = current, seed.score
        self._declined: list[dict] = []

        for step in range(self.config.max_steps):
            candidate, account = await self._propose(archive, best, step)
            if candidate is None:
                continue
            score = (await self._evaluate(candidate, dev, label="candidate")).score
            # Every measured design enters the archive, improvement or not:
            # the next proposal is written against the whole history, so one
            # that scored worse still says this direction was tried, and what
            # it cost.
            archive.append({"account": account, "score": score})
            self._record(
                "decision",
                {
                    "step": step,
                    "kind": "admit",
                    "parent_id": best.id,
                    "candidate_id": candidate.id,
                    # Whether this design becomes the next parent, and the one
                    # this run would promote if it stopped here.
                    "accepted": score > best_score,
                    "parent_score": best_score,
                    "candidate_score": score,
                    "account": account,
                },
            )
            # A tie does not move the incumbent: an equal score is no evidence.
            if score > best_score:
                best, best_score = candidate, score
        return self._promote(
            current, best, archive=archive, declined_designs=self._declined
        )

    async def _propose(self, archive, parent: Snapshot, step: int):
        """Let the meta agent edit a checkout into its next design.

        Reflection happens inside the same checkout, because upstream reflects
        on a design before it is measured: the agent revises the files it just
        wrote, and only the description it ends with is carried into the
        archive.
        """
        rules = self._evidence([dict(self.target.component_rules)])
        working = self._run_dir / "working" if self._run_dir else None
        with parent.check_out(working_dir=working) as directory:
            account = _replies.tagged(
                await self.worker.edit(
                    directory,
                    prompts.PROPOSE.format(
                        archive=self._evidence(archive), rules=rules
                    ),
                ),
                "DESIGN",
            ).strip()
            for _ in range(self.config.num_reflections):
                account = _replies.tagged(
                    await self.worker.edit(
                        directory,
                        prompts.REFLECT.format(rules=rules, account=account),
                    ),
                    "DESIGN",
                ).strip()
            # The meta agent ran its own tools in there, so it also left what
            # those tools produce; commit refuses those paths outright.
            discard_runtime_files(directory)
            try:
                candidate = self._commit(parent, directory)
            except ValueError as error:
                return self._decline(parent, step, f"rejected design: {error}")
            # What the edit really did, as the Snapshot's own comparison rather
            # than the agent's account of it. A commit always mints a new id,
            # because the parent is part of the hash, so an untouched checkout
            # is recognized by its files and not by its identity.
            if not parent.changed_paths(candidate):
                return self._decline(parent, step, "the harness was left unchanged")
        return candidate, account

    def _decline(self, parent: Snapshot, step: int, problem: str):
        """Cost one step rather than the experiment.

        Nothing is measured, so nothing enters the archive: an archive row is a
        design and a score, and there is no design here. Unlike AHE, the reason
        is not fed back into the next prompt -- this meta agent reasons about
        scored designs, and the component rules it broke were already in the
        prompt that broke them. The whole list goes into the promotion record,
        so a reader of the run can see what the search spent its steps on.
        """
        self._declined.append({"step": step, "problem": problem})
        self._record(
            "decision",
            {
                "step": step,
                "kind": "decline",
                "parent_id": parent.id,
                "problem": problem,
                "accepted": False,
            },
        )
        return None, None

    def _check(self) -> None:
        # Designs are admitted on a held-out score, so dev is what the search
        # reads; train is unused. See docs/adas.md.
        if not self.benchmark.dev:
            raise ValueError(
                "ADAS admits designs on a held-out score and requires a "
                "non-empty dev split"
            )
        # The meta agent works in a checkout, which a text Worker cannot do.
        # This is the configured role, not the harness behind it, so the check
        # holds for every Agent Worker without naming any of them.
        if self.worker.config.get("type", "agent") != "agent":
            raise ValueError(
                "ADAS designs agents by editing the harness directory and "
                "needs a Worker that can edit files; set algorithm.worker.type "
                "to 'agent'"
            )


__all__ = ["ADAS"]
