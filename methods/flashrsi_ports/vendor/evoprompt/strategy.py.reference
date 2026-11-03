"""EvoPrompt: evolve a population of prompts, by GA or by DE.

    _initialize  the target's own instruction, then paraphrases of it, all scored
    _ga / _de    one generation, repeated max_steps times
    _promote     adopt the earliest best member seen across generations, once

Fitness is always a dev score: `_member` is the only place a prompt becomes a
candidate Snapshot, and it evaluates every one it builds.

Upstream: https://github.com/microsoft/EvoPrompt (ICLR 2024, arXiv:2309.08532)
Commit: 2e235c9410e38d531dabfae7fdd9cbded1577373
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from flashrsi.algorithms import _replies
from flashrsi.algorithms.evoprompt import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot

#: The smallest population each variant can run: GA draws two parents per
#: child, DE a target plus three distinct donors.
_MINIMUM = {"ga": 2, "de": 4}


@dataclass(frozen=True, slots=True)
class Member:
    """One population member and the dev fitness that admitted it.

    The text is carried beside the snapshot because every operator reads it: a
    crossover prompt quotes two members verbatim, and top-k survivor selection
    deduplicates on the text rather than on the snapshot.
    """

    snapshot: Snapshot
    text: str
    score: float
    #: True when an operator returned nothing usable and this slot kept its
    #: parent's files and fitness. Recorded so a reader of the decision log can
    #: tell that from a candidate that genuinely scored the same.
    carried: bool = False


class EvoPrompt(Algorithm):
    """GA or DE population evolution over one instruction file.

    The two variants differ in *when* a member is displaced. GA builds a whole
    child population and then decides which of parents and children survive, so
    a good parent can be lost to a better child from another lineage. DE keeps
    the population positionally: a trial competes only against the target it was
    built from, and every other member is untouched.

    Neither variant moves the current version during the search: the population
    carries every member's fitness throughout. Final selection remembers the
    earliest global best even when GA's standard policy replaces its generation.
    """

    name = "evoprompt"
    description = "Evolve a population of prompts by GA or DE."
    evolve_scope = {"prompt"}
    observation_scope = {"prompt"}
    _defaults = {
        # Published EvoPrompt runs 10 generations over a population of 10.
        "variant": "ga",
        "population_size": 10,
        "max_steps": 10,
        # GA only; DE draws its donors uniformly, as upstream does.
        "selection": "wheel",
        "survivor_selection": "topk",
        # Upstream's DE uses the global-best base vector unless --donor_random.
        "donor_random": False,
        "seed": 42,
    }
    _ranges = {
        "variant": ("ga", "de"),
        # The floor is per variant; _check raises that to DE's four donors.
        "population_size": (int, 2),
        "max_steps": (int, 1),
        "selection": ("wheel", "random", "tournament"),
        "survivor_selection": ("topk", "standard"),
        "donor_random": bool,
        "seed": int,
    }

    async def evolve(self) -> Snapshot:
        current = self.target.snapshot
        await self._initialize(current)
        best = max(self._population, key=lambda member: member.score)

        generation = self._ga if self.config.variant == "ga" else self._de
        for step in range(self.config.max_steps):
            await generation(random.Random(self.config.seed + step), step)
            candidate = max(self._population, key=lambda member: member.score)
            # Standard GA may discard a better parent from the live population;
            # population replacement must not erase the final dev incumbent.
            if candidate.score > best.score:
                best = candidate

        return self._promote(
            current,
            best.snapshot,
            path=self._path,
            score=best.score,
            baseline_score=self._baseline,
            population=[_row(member) for member in self._population],
        )

    async def _initialize(self, seed: Snapshot) -> None:
        """Seed the population: the target's own instruction, then paraphrases.

        The starting snapshot is already a member, so it is evaluated as it is
        rather than patched to its own text -- which would propose a candidate
        that changes nothing.
        """
        text = self._read_text(seed).get(self._path, "")
        results = await self._evaluate(seed, self.benchmark.dev, label="baseline")
        self._population = [Member(seed, text, results.score)]
        self._baseline = self._population[0].score

        while len(self._population) < self.config.population_size:
            reply = await self.worker.ask(
                prompts.INITIALIZE.format(path=self._path, seed=text),
            )
            self._population.append(
                await self._member(
                    self._population[0], _replies.tagged(reply, "prompt"), label="seed"
                )
            )

    async def _ga(self, rng: random.Random, step: int) -> None:
        """Build a full child population, then decide which members survive.

        ``wheel`` resamples the population fitness-proportionately first, so the
        same member can legitimately be drawn as both parents of a child; the
        other policies draw from the population itself. Survivor selection is
        then the whole decision: a child not selected is simply never adopted.
        """
        pool = tuple(self._population)
        if self.config.selection == "wheel":
            # Roulette wheel: sampling with replacement, weighted by fitness.
            # Uniform when every fitness is zero -- the ordinary state of a
            # fresh population, which random.choices would otherwise reject.
            weights = [member.score for member in pool]
            pool = tuple(
                rng.choices(
                    pool,
                    weights=weights if sum(weights) > 0 else None,
                    k=self.config.population_size,
                )
            )

        children = []
        for _ in range(self.config.population_size):
            if self.config.selection == "tournament":
                # A binary tournament per parent, which is upstream's ``tour``:
                # sample two, keep the fitter.
                first, second = (
                    max(rng.sample(pool, 2), key=lambda member: member.score)
                    for _ in range(2)
                )
            else:
                first, second = rng.sample(pool, 2)
            reply = await self.worker.ask(
                prompts.GA.format(
                    path=self._path, prompt1=first.text, prompt2=second.text
                )
            )
            children.append(
                await self._member(
                    first, _replies.tagged(reply, "prompt"), label="child"
                )
            )

        size = self.config.population_size
        survivors = (
            tuple(children)
            if self.config.survivor_selection == "standard"
            # Survivors are chosen from the real population, not the resampled
            # pool: a wheel draw may hold several copies of one member.
            else _top_k(tuple(self._population) + tuple(children), size)
        )
        self._adopt(step, survivors, children)

    async def _de(self, rng: random.Random, step: int) -> None:
        """One trial per target, accepted by strict pairwise replacement."""
        current = tuple(self._population)
        base = max(current, key=lambda member: member.score)

        survivors, trials = [], []
        for position, target in enumerate(current):
            # Donors are drawn by position, which is what DE means: upstream
            # walks the population's indices and a trial competes with the
            # member at its own slot. Filtering by identity instead would also
            # couple this loop to _member's internals.
            others = [item for index, item in enumerate(current) if index != position]
            first, second, third = rng.sample(others, 3)
            reply = await self.worker.ask(
                prompts.DE.format(
                    path=self._path,
                    prompt0=target.text,
                    prompt1=first.text,
                    prompt2=second.text,
                    prompt3=(third if self.config.donor_random else base).text,
                )
            )
            trial = await self._member(
                target, _replies.tagged(reply, "prompt"), label="trial"
            )
            trials.append(trial)
            # A trial competes only with the target it was built from, so a
            # losing one leaves the rest of the population where it was.
            survivors.append(trial if trial.score > target.score else target)
        self._adopt(step, tuple(survivors), trials)

    # -- the pieces a generation is built from -----------------------------

    async def _member(self, parent: Member, text: str, *, label: str) -> Member:
        """Commit one prompt as a candidate and score it on dev.

        Every fitness in the population comes from here. An operator that
        returned nothing usable, or the parent's own text back, keeps the
        parent's snapshot and fitness: re-scoring the same files would spend a
        dev pass to learn nothing. It still becomes its own record, so a
        population slot never aliases another slot's member -- top-k pads from
        the ranked pool by excluding the entries it already took, and an alias
        would remove the fitter member's every copy at once.
        """
        if not text or _text_key(text) == _text_key(parent.text):
            return Member(parent.snapshot, parent.text, parent.score, carried=True)
        candidate = self._candidate(parent.snapshot, {self._path: text})
        results = await self._evaluate(candidate, self.benchmark.dev, label=label)
        return Member(candidate, text, results.score)

    def _adopt(
        self, step: int, survivors: tuple[Member, ...], candidates: list[Member]
    ) -> None:
        """Adopt a generation's survivors and record what it displaced."""
        self._record(
            "decision",
            {
                "kind": "generation",
                "variant": self.config.variant,
                "step": step,
                "candidates": [_row(member) for member in candidates],
                "population": [_row(member) for member in survivors],
            },
        )
        self._population = list(survivors)

    def _normalize(self) -> None:
        # Upstream's CLI calls tournament selection ``tour``.
        if self.config.selection == "tour":
            self.config.selection = "tournament"

    def _check(self) -> None:
        if not self.benchmark.dev:
            raise ValueError("EvoPrompt requires a non-empty dev split")
        self._path = self._instruction_path()
        if self.config.population_size < _MINIMUM[self.config.variant]:
            raise ValueError(
                f"EvoPrompt {self.config.variant} needs algorithm.population_size "
                f">= {_MINIMUM[self.config.variant]}"
            )


def _text_key(text: str) -> str:
    """Compare line endings consistently without rewriting snapshot contents."""
    return text.replace("\r\n", "\n").rstrip("\n")


def _top_k(pool: tuple[Member, ...], size: int) -> tuple[Member, ...]:
    """The best distinct prompts in ``pool``, padded back to a fixed size.

    Deduplicating on the text is what makes this top-k rather than a plain sort:
    a generation whose mutations collapsed onto one prompt would otherwise fill
    the whole population with copies of it. The padding is equally load-bearing
    -- the next generation still needs ``size`` members to draw parents from, so
    once the distinct prompts run out the best duplicates come back in.

    Members are tracked by position because two slots can legitimately hold
    equal records: a carried parent compares equal to the member it was carried
    from, and dropping one as a duplicate would lose a slot the next generation
    needs.
    """
    fittest: dict[str, int] = {}
    for index, member in enumerate(pool):
        key = _text_key(member.text)
        holder = fittest.get(key)
        if holder is None or member.score > pool[holder].score:
            fittest[key] = index
    ranked = sorted(range(len(pool)), key=lambda index: pool[index].score, reverse=True)

    # One slot per distinct prompt, fittest first, then the best entries left
    # over -- duplicates included -- to pad back to a full population.
    survivors = sorted(
        fittest.values(), key=lambda index: pool[index].score, reverse=True
    )[:size]
    remaining = [index for index in ranked if index not in survivors]
    survivors += remaining[: size - len(survivors)]
    while len(survivors) < size:
        survivors.append(ranked[len(survivors) % len(ranked)])
    return tuple(pool[index] for index in survivors)


def _row(member: Member) -> dict:
    row = {
        "snapshot_id": member.snapshot.id,
        "text": member.text,
        "score": member.score,
    }
    # Only marked when true, so an ordinary candidate's row stays as it was.
    if member.carried:
        row["carried"] = True
    return row


__all__ = ["EvoPrompt"]
