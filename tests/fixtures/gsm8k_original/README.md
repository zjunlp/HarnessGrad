# Original GSM8K integer-only extractor

`integer_extractor.json` captures `REF_FLEXIBLE`, last-match selection and comma/dollar
stripping from `tests/quality/test_gsm8k_official_scorer.py` at immutable repository
commit `f7ab62143108e120914b5ead34ed339d0c785ee9`, immediately before the decimal change
in `d10b387cc476f4d4ebf04b5a8451453969592db5`.

The source file SHA-256 is
`feb85c6646873e13e6a84feab4322b186e45e07c400151518d00f9e0e3765dcc`.
The fixture SHA-256 is
`45c73aeb817878b5ba65709f45766f21eb7899182df4eba9a8101e9169d8276b`;
the focused test pins these bytes and the regex independently of the adapter.

Preserve this fixture unchanged. A new extractor needs a new identity and fixture.
The original file attributed the regex to lm-evaluation-harness without an immutable
upstream revision. That attribution remains unverified; this snapshot establishes
the inherited repository contract only. It does not certify an official scorer or
copy the old file's incorrect claim about upstream float comparison.

This is a scorer fixture with no benchmark rows, model outputs or run receipts.
