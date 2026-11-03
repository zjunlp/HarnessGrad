# Independent re-review of the four onboarding fixes

Reviewed branch `codex/fix-onboarding-first-four-20261103` against base
`0a2551b0`. This pass covers the earlier material findings, their fixes,
and the regression cases for those fixes.

## Closed findings

- The driver resolves omitted improvement rounds before check-only readiness.
  Both staged sampling policies default to zero. The editor check follows the
  `HG_IMPROVER` command override, as do the editor and CLI launcher. The new
  tests exercise a command with arguments and a missing executable.
- The shared dotenv reader preserves the first effective assignment to a key.
  The driver still gives an existing process variable precedence. The template
  test restores the entire environment on exit, including keys it introduced.
- H0 preparation includes sampling policy in cache identity and checks the
  recorded policy. A cached `random_1` run cannot satisfy an `all` request.
  The tool resolves the run store in the caller's directory before passing it
  to the child driver. Lookup, verification and the printed hint use that path.
- Direct H0 reuse checks the donor's recorded endpoint, backend and model,
  the measured curve identity and the platform measurement marker. A missing
  key is distinguishable from a known null model on the mock backend.
- Checkpoint provenance distinguishes official eval-only measurements and
  training-side diagnostics. The training text describes feedback eligibility,
  including for a zero-round H0 that never called a method.
- Matrix and summary arithmetic exclude numeric placeholders from explicitly
  unmeasured rounds. Per-round coverage and rejection reasons survive export.
  The regression cases distinguish a rejected zero from a measured zero and
  exercise a valid score after a rejected round.

## Historical donors and installer verification

The initial follow-up implementation still accepted an intermediate historical
donor whose baseline came from another endpoint. A focused Python probe wrote
two synthetic run metadata files under a temporary directory. The original
named endpoint A, the intermediate run named endpoint B, and its curve point
carried `shared_from` pointing to A. With matching model and backend,
`harnessgrad.h0_identity.agent_problems` returned an empty list for a recipient
at B. The recipient loads this shape through the driver's existing curve-only
donor path. The final guard rejects points carrying `shared_from` and directs
the user to the original donor. A second focused probe returned that refusal.
The dedicated regression uses an intermediate run whose own metadata matches
the recipient. Direct donor verification still follows its existing path.

The final attributes rule applies `text eol=lf` to `install.sh`. Git reported
`i/lf w/lf`, and a direct byte count found 289 LF terminators and zero CRLF
pairs. WSL `bash -n install.sh` exited 0. The installer content diff is
confined to printing the quoted method command.

**Verdict.** All material findings in the scoped re-review are closed.

This reviewer inspected the implementation and the related regression tests
and ran the synthetic endpoint probe. Wider suite results belong to the parent
and implementer receipts. This review used no model calls or benchmark data.
