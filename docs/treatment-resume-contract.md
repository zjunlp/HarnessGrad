# Optional declared treatment contract

Pass `--treatment-manifest /absolute/manifest.json` on the original run and each
resume to bind an external method wrapper, its declared dependencies and settings.
This generic interface does not inspect or certify any external launcher.

```json
{
  "schema": "harnessgrad.treatment-manifest.v1",
  "files": {
    "wrapper": "/absolute/method_wrapper.py",
    "helper": "/absolute/helper.py",
    "profile": "/absolute/provider-profile.json"
  },
  "configuration": {"treatment": "operator-selected-v1"},
  "environment": ["CUSTOM_WRAPPER_PROFILE", "CUSTOM_WRAPPER_SETTINGS"]
}
```

`wrapper` is required; add every file the operator intends to bind. All declared
files must be regular files at absolute paths, with no symlink components. Capture
uses no-follow opens, SHA-256, executable-mode commitments and checks for replacement
or mutation during reading. The manifest is versioned; duplicate or unsupported
fields refuse. Settings, declared environment values, effective platform settings,
method argv, budget-profile identity and resolved improver/model/provider/profile
identity are hashed. The treatment record contains commitments, not copied source,
credentials, argv values or complete configuration objects.

Resume checks declared files/configuration before local resolution, then checks the
resolved treatment before any optional provider tool-call probe. A changed or missing
contract refuses before actor or method dispatch. `--resume-across-platforms` waives
only its existing platform check and never waives treatment mismatch. Continuation
round count and output/report controls are excluded so a run can extend its budget;
treatment settings, task/trial configuration, model/provider and method argv stay fixed.
Original treatment metadata and already-written round artifacts remain intact.

Coverage is exactly the operator-declared files and environment names plus the stated
platform/resolver identities. This is not automatic discovery or proof of complete
transitive dependencies: declare profile/config files and custom environment variables
used by the wrapper. Equal hashes establish equality of captured inputs, not authority
or correctness of a provider or wrapper. Legacy runs without a treatment contract keep
their existing resume behavior; a contract cannot be retroactively attached to claim
that earlier uncommitted treatment was verified.
