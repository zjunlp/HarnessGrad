# ujson PyPI package evidence — Codex 226

This fixture records acquisition and offline installation of the 53,686-byte
wheel identified by the URL and SHA-256 in the official PyPI 5.10.0 metadata.

- `pypi-release.json`: original HTTPS response, including all release metadata.
- `acquisition.json`: acquisition time/headers and exact artifact entry.
- `provenance.json`: target, wheel/member identities, notices, ELF requirements,
  explicit remaining closure flags and scope.
- `LICENSE.txt`: verbatim complete combined wheel license; notice completeness
  for bundled double_conversion code remains unverified.
- `elf-*.txt`: static GNU readelf 2.38 output with `LC_ALL=C`.
- `requirements-codex226.txt`: exact artifact hash lock.
- `offline-install-initial-report.json` and `offline-install-report.json` preserve
  the pip reports from the two runs. Their bytes and SHA-256 hashes match.
- `offline-install-initial-receipt.json` records the first installation.
- `offline-install-receipt.json` records the replay with `PIP_CONFIG_FILE=/dev/null`.

Both receipts record that the installed files outside `RECORD` matched the wheel.
The native extension was never imported during these installations. See the
[withdrawal record](../../../registry/withdrawn-requirements.md) for the limits
of the wheel's source and build provenance.
