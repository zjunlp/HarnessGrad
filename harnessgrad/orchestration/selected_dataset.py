"""Selected dataset planning before namespace claim or workspace mutation.

The caller holds the provider session for the entire run. These operations read
catalogue/declarations, resolve selected identities and report refusals; they do
not acquire a session, mutate CLI/environment state, stage sources or run actors.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Protocol

from eval.ckpt.git_state import manifest
from eval.policy import diagnostic_policy
from eval.run import metrics
from eval.run.metrics import select_task_set
from harnessgrad import config as platform_config
from harnessgrad import dataset_runtime_identity, readiness, scoring_identity, verification_context
from harnessgrad.environments import _check_env_kinds, _narrow_to_run, _required_env_kinds, _resolve_envs


class DatasetSource(Protocol):
    """The registry reads made within the caller's already-held provider session."""
    def load_split(self, name: str): ...
    def oracle_source(self, name: str): ...
    def load_tasks(self, name: str, *, task_ids: list[str]): ...
    def load_envs(self, name: str, *, task_ids: list[str]): ...


class Reporter(Protocol):
    def note(self, message: str, *, level: str = "info"): ...


@dataclass(frozen=True)
class SelectionOptions:
    dataset: str
    split: str | None
    side: str
    tasks: str | None
    sampling: str
    run_id: str | None
    from_run: str | None
    sandbox: bool
    check_net: bool


@dataclass(frozen=True)
class DatasetPartition:
    tasks: list[dict]
    scorable: dict
    split: dict | None
    source: str | None
    protocol: dict | None
    oracle_source: str | None
    oracle_coverage: object


@dataclass(frozen=True)
class TaskSelection:
    tasks: list[dict]
    ids: list[str]
    eligible_ids: list[str]


@dataclass(frozen=True)
class SelectedRunPlan:
    partition: DatasetPartition
    selection: TaskSelection
    setups: dict
    verifiers: dict
    envs: dict
    scoring_identity: dict | None
    dataset_runtime_identity: dict | None
    verification_context: dict


class SelectedRunRefused(Exception):
    """An existing CLI refusal, distinct from legacy loader errors/cancellation."""
    def __init__(self, message: str, *, channel="stderr", level="error", stdout=None):
        super().__init__(message)
        self.channel, self.level, self.stdout = channel, level, stdout

    def report(self, reporter: Reporter):
        if self.stdout is not None:
            print(self.stdout)
        if self.channel == "console":
            reporter.note(str(self), level=self.level)
        else:
            print(str(self), file=sys.stderr)


def resolve_catalog_partition(options: SelectionOptions, source: DatasetSource,
                              runs_root: Path, scenario: dict | None,
                              reporter: Reporter) -> DatasetPartition:
    try:
        tasks, scorable, split = source.load_split(options.dataset)
        oracle_source, oracle_coverage = source.oracle_source(options.dataset)
    except (OSError, ValueError, ImportError) as exc:
        if scenario is None:
            raise
        raise SelectedRunRefused(
            f'refusing --scenario: dataset {options.dataset!r} could not be loaded: {exc}') from exc
    from data.scaffolds.custom_split import resolve as resolve_split
    try:
        split, split_source, protocol = resolve_split(
            options.dataset, tasks, split, options.split, side=options.side)
    except (OSError, ValueError, ImportError) as exc:
        raise SelectedRunRefused(f"--split {options.split}: {exc}", channel="console") from exc
    if protocol is not None:
        reused = protocol['official_eval_reused_for_train']
        origin = 'unknown' if reused is None else str(reused)
        reporter.note(f"custom held-out partition: {len(split['train'])} train / "
                      f"{len(split['eval'])} eval; official evaluation tasks used for study: "
                      f"{origin}. This is not an official benchmark split/leaderboard score.", level="info")
    if options.side == 'eval' and options.from_run:
        from data.scaffolds.custom_split import check_eval_pair
        try:
            check_eval_pair(runs_root / options.from_run, options.dataset, split, protocol)
        except (OSError, ValueError) as exc:
            raise SelectedRunRefused(f'refusing custom split evaluation: {exc}', channel="console") from exc
    return DatasetPartition(tasks, scorable, split, split_source, protocol, oracle_source, oracle_coverage)


def select_run_tasks(options: SelectionOptions, partition: DatasetPartition,
                     policy_evidence: dict | None) -> TaskSelection:
    side_ids = (list(partition.split[options.side]) if partition.split
                else [task["task_id"] for task in partition.tasks])
    if options.tasks:
        asked = [task.strip() for task in options.tasks.split(",") if task.strip()]
        unknown = sorted(set(asked) - set(side_ids))
        if unknown:
            raise SelectedRunRefused(
                f"--tasks names {unknown}, which are not on the {options.side} side of "
                f"{options.dataset!r}. That side has {sorted(side_ids)}")
        side_ids = [task for task in side_ids if task in set(asked)]
    eligible_ids = list(side_ids)
    if options.sampling == "diagnostic":
        side_ids = metrics.diagnostic_task_set(side_ids)
    if options.sampling == "pilot_10pct":
        try:
            diagnostic_policy.require_review(policy_evidence, context=policy_evidence["context"],
                                             eligible=eligible_ids, run_id=options.run_id)
        except ValueError as exc:
            raise SelectedRunRefused(f"refusing staged policy: {exc}") from exc
        side_ids = metrics.pilot_task_set(side_ids)
    if options.sampling.startswith("random_"):
        side_ids = select_task_set({}, options.sampling, within=side_ids)
    tasks = [task for task in partition.tasks if task["task_id"] in set(side_ids)]
    if not tasks:
        raise SelectedRunRefused(f"dataset {options.dataset!r} has no tasks on side {options.side!r}")
    return TaskSelection(tasks, side_ids, eligible_ids)


def load_selected_declarations(options: SelectionOptions, source: DatasetSource,
                               selection: TaskSelection, scenario: dict | None):
    try:
        _, setups, verifiers = source.load_tasks(options.dataset, task_ids=selection.ids)
        envs = source.load_envs(options.dataset, task_ids=selection.ids)
    except (OSError, ValueError, ImportError) as exc:
        if scenario is None:
            raise
        raise SelectedRunRefused(
            f'refusing --scenario: dataset {options.dataset!r} declarations could not be loaded: {exc}') from exc
    return _narrow_to_run(setups, verifiers, envs, selection.ids)


def check_scenario_runtime(spec: dict, model: dict, man: dict, envs: dict) -> None:
    """Actual capabilities, not a configured field's claim of enforcement."""
    wanted = spec['model']['backend']
    if wanted == 'mock' and model['backend'] != 'mock':
        raise ValueError('scenario model.backend=mock conflicts with the actual agent backend')
    if wanted == 'live' and (not model.get('live') or not platform_config.current().agent.base_url):
        raise ValueError('scenario model.backend=live requires a configured model endpoint and credentials')
    problem = _check_env_kinds(man, set(spec['env_kinds']) | _required_env_kinds(envs))
    if problem:
        raise ValueError(f'scenario environment requirements: {problem}')
    if spec.get('limits'):
        fields = ', '.join(sorted(spec['limits']))
        raise ValueError(f'scenario limits ({fields}) cannot yet be enforced by this CLI')


def _selected_model_admission(options, model, scenario, harness_source, setups, verifiers, envs):
    checks = readiness.selected_model_checks(options, verifiers=verifiers, setups=setups)
    if any(check.required and not check.ok for check in checks):
        raise SelectedRunRefused(
            'refusing to run: selected judge/environment configuration failed before H0',
            stdout=readiness.render(checks))
    if scenario is not None:
        try:
            check_scenario_runtime(scenario, model, manifest(harness_source), envs)
        except ValueError as exc:
            raise SelectedRunRefused(f'refusing --scenario: {exc}') from exc


def _selected_identities(options, selection, setups, verifiers, envs):
    try:
        scoring = scoring_identity.resolve(options.dataset)
    except (OSError, TypeError, ValueError) as exc:
        raise SelectedRunRefused(f"invalid scoring profile: {exc}", channel="console") from exc
    try:
        runtime = dataset_runtime_identity.capture(options.dataset, selection.ids, setups, verifiers, envs)
    except (OSError, TypeError, ValueError) as exc:
        raise SelectedRunRefused(f"invalid dataset runtime identity: {exc}", channel="console") from exc
    return scoring, runtime


def _selected_environment_context(options, partition, selection, harness_source, setups, verifiers, envs):
    try:
        problem = _check_env_kinds(manifest(harness_source), _required_env_kinds(envs))
        if problem:
            raise SelectedRunRefused(f"refusing to run: {problem}", channel="console")
        envs, problem = _resolve_envs(envs)
        if problem:
            raise SelectedRunRefused(f"refusing to run: {problem}", channel="console")
        context = verification_context.capture(selection.tasks, partition.scorable, setups,
                                               verifiers, envs, sandbox=options.sandbox)
    except (OSError, TypeError, ValueError) as exc:
        raise SelectedRunRefused(f"invalid verification context: {exc}", channel="console") from exc
    return envs, context


def admit_selected_run(options, partition, selection, *, model, scenario, harness_source,
                       setups, verifiers, envs) -> SelectedRunPlan:
    _selected_model_admission(options, model, scenario, harness_source, setups, verifiers, envs)
    scoring, runtime = _selected_identities(options, selection, setups, verifiers, envs)
    envs, context = _selected_environment_context(
        options, partition, selection, harness_source, setups, verifiers, envs)
    return SelectedRunPlan(partition, selection, setups, verifiers, envs, scoring, runtime, context)


def build_selected_run_plan(options: SelectionOptions, source: DatasetSource, *, runs_root: Path,
                            scenario: dict | None, model: dict, harness_source: Path,
                            policy_evidence: dict | None, reporter: Reporter) -> SelectedRunPlan:
    partition = resolve_catalog_partition(options, source, runs_root, scenario, reporter)
    selection = select_run_tasks(options, partition, policy_evidence)
    setups, verifiers, envs = load_selected_declarations(options, source, selection, scenario)
    return admit_selected_run(options, partition, selection, model=model, scenario=scenario,
                              harness_source=harness_source, setups=setups, verifiers=verifiers, envs=envs)
