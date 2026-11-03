# First four onboarding fixes — 2026-11-03

Base: 0a2551b028b5d6e527199458c8ce358a74c7d608.
Parent branch: codex/fix-onboarding-first-four-20261103.

## Conflict scan

| Tasks | Shared boundary | Ruling |
| --- | --- | --- |
| 1 / 2 | README method guide link | Parent adds the link after the method commit; child does not edit README. |
| 1 / 3 | Mock/real model examples and preflight | Real settings must pass the same selected-method checks; no endpoint calls in preflight. |
| 2 / 3 | External method entrypoint/trajectory | Task 2 owns validator and protocol; parent owns harnessgrad/methods.py and driver admission. |
| 3 / 4 | driver consumes recorded H0 | Results task does not modify driver; effective identities come from existing config helpers. |
| 1 | Documentation and executable config | Reuse experiments resolver; no new dependency. |
| 2 | Validator and engine | Missing trajectory fails; legal no-change behavior must match engine. |
| 3 | Readiness and execution | Validate before H0; preserve external/self-contained methods. |
| 4 | Records and exports | Historical missing identity stays unknown; planned/scored coverage stays explicit. |

Implementation and verification results will be appended below.
