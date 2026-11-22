# Resuming interrupted runs

`--resume --run-id R` continues an existing run only after checking its recorded
identity. Keep the original run directory and use the same dataset, task selection,
model route, seed, scorer and runtime. A change in platform identity requires an
explicit `--resume-across-platforms REASON`; that reason does not waive the other
compatibility checks. See [treatment-resume-contract.md](treatment-resume-contract.md).

A run namespace admits one cooperating driver at a time. A second driver for the
same run ID is refused while the first is active. This lock coordinates drivers
that implement it; older drivers and unrelated writers do not participate. Before
resuming an old run, establish that its previous driver and task processes have
actually stopped. Do not delete or move evidence to bypass a refusal.

## Training

Training resumes from the last published round. The total `--rounds` target is
not added to the existing round count. H0 stays the original baseline, and saved
history, traces and verdicts are restored when available. Missing evidence is not
invented. Resume records the platform seam and the round from which it continued.

The private method-submission journal is an experimental opt-in:
`HG_EXPERIMENTAL_ROUND_JOURNAL=1`. It saves a candidate before evaluation so a
subsequent resume can restore it without repeating the method call. Incomplete
rounds with existing sample evidence are refused until the evidence is reconciled.
The current CLI does not automatically fill missing training samples in place.

For an explicitly reviewed complementary measurement, experimental
`--resume-supplement-run RUN_ID` reconciles completed task records with the saved
candidate. `--resume-batch-supplement ROUND=RUN_ID[,ROUND=RUN_ID]` can identify
later saved candidates in the same batch. Admission checks task coverage,
identity, snapshot and receipts; it refuses overlapping, missing or incompatible
evidence. The supplement retains its own run and trial identity. Interrupted
process integrity and any unobserved attempts remain unknown; reported costs are
lower bounds when their coverage is incomplete. Journals and raw traces may contain
private task material and require review before publication.

## Evaluation

Evaluation resumption uses the existing progress and trial evidence to avoid
repeating completed work where the saved pool, source, scorer and runtime remain
compatible. Missing or incompatible evidence is reported or refused rather than
invented. A resumed score and an independently repeated measurement are distinct
observations; keep their provenance when comparing or exporting results.
