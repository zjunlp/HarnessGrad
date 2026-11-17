# ChessBench provider identity before path migration

`before.json` is the scoring identity calculated from repository source at main
revision `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6`, before moving the dataset
provider. It contains source-file digests and metric metadata, not benchmark
examples, model output, scores or a run receipt. The source paths and bytes used
for this identity were unchanged in the refactor before the move.

The migration test checks that this valid historical identity is not silently
accepted as the new identity. It does not change historical results or prove a
real benchmark run. Update or replace this fixture only with a separately
reviewed provenance rationale.
