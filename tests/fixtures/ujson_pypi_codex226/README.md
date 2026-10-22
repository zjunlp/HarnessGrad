# ujson PyPI package evidence — Codex 226

Public package code/metadata only. The exact 53,686-byte wheel was obtained from
the URL and SHA-256 in official PyPI 5.10.0 JSON. It is retained under `artifacts/`
for reproducible, offline, non-executing provenance tests.

- `pypi-release.json`: original HTTPS response, including all release metadata.
- `acquisition.json`: acquisition time/headers and exact artifact entry.
- `provenance.json`: target, wheel/member identities, notices, ELF requirements,
  explicit remaining closure flags and scope.
- `LICENSE.txt`: verbatim complete combined wheel license; notice completeness
  for bundled double_conversion code remains unverified.
- `elf-*.txt`: static GNU readelf 2.38 output with `LC_ALL=C`.
- `requirements-codex226.txt`: exact artifact hash lock.
- `offline-install-report.json`: fresh local-venv pip report.
- `offline-install-initial-*.json`: retained first installation before the
  fresh replay additionally disabled all pip configuration files.
- `offline-install-receipt.json`: original non-RECORD installed files byte-match
  the wheel; native extension never imported. Historical evidence, not a live
  claim about a retained venv or an attested supported image.

The native wheel is **not imported by tests**. No benchmark dataset/model or
runtime image is included. See the [full report and installation
procedure](../../../docs/task-226-ujson-wheel-report.md) and
[focused tests](../../quality/test_ujson_pypi_codex226.py).
