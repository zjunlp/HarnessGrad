# Verified shared-H0 subset views

`--h0-from RUN` retains its equal-cohort contract. To derive a smaller cohort from
an original measured donor, request `--h0-from RUN --h0-subset-view --sampling all
--tasks ID1,ID2,...`. Fix this task list before inspecting candidate results; this
option does not select tasks or define an experimental cohort-selection algorithm.
This option is available on the train side. The resolved current task list must be a nonempty proper subset of the donor's
recorded H0 cohort. The requested trial count must match the original measurement.

The view requires the original platform-measured donor, `h0.json`, matching source,
agent endpoint/model/backend, scorer, actor/runtime and content-bound verification
inputs. Original identity envelopes are validated before projecting task identities.
Every selected task needs an actual score, native verdict and trace; multiple trials
also need their original trial scores, verdicts and traces. Missing evidence is a
refusal, never a zero or a reconstructed receipt. Specialized `repeated_trials`
protocol reports are currently refused; ordinary `--trials N` evidence is supported.

The recipient publishes selected scores and canonical aggregates, selected trace,
verdict, harness-output and trial channels, plus `h0_subset_view` provenance containing
the source cohort, selected cohort and SHA-256 commitments to original source files.
The source run is read without modification. These commitments identify bytes; they
do not authenticate a measurement or justify editing source receipts.

`cost` and `cumulative` remain the referenced original donor measurement's historical
cost, explicitly marked `original_donor_measurement_not_prorated`. They are not an
estimate of subset execution cost. `current_run_cost` separately records zero new H0
execution. Unknown historical usage stays unknown. Normal continuation uses the
selected cohort; normal resume freezes that recorded cohort and does not accept
`--h0-subset-view` again. A derived point cannot serve as an original donor.
