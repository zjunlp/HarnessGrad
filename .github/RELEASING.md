# Repository and Release Guide

This guide prepares maintainers for a release; it does not create a tag,
publish a package, deploy a site or claim that a release has been validated.

## 🏷️ Issue labels

The repository already has labels such as `bug`, `enhancement`,
`documentation`, `good first issue` and `help wanted`. The issue forms apply
`bug` or `enhancement`; maintainers can add an `area:` label to show which
workflow is affected.

After the [label workflow](workflows/labels.yml) is present on the repository's
default branch, a maintainer can run it manually from GitHub Actions. It creates
only missing `area:` labels; it does not rename, recolor or delete existing
labels. The workflow needs permission to manage repository issues and labels.
The form's area selection is information for triage; it does not automatically
apply an `area:` label.

## ✅ Before publishing a release

1. Choose the intended commit and version, and ensure the package version,
   installation instructions and release notes match what will be published.
2. Review the actual CI results and run the checks appropriate to the release.
   The pull-request Quality workflow includes a gating job and an advisory report;
   it is not proof that every runtime, model or benchmark was tested. Use the
   [test guide](../docs/test-suite.md) and record any unverified configurations.
3. Confirm dataset and scorer provenance and preserve existing experiment
   receipts. Do not include credentials, private task data or local run state.
4. Prepare release notes describing user-visible changes, compatibility,
   installation and known limitations. Review the notes and artifacts before
   creating a tag or publishing a GitHub Release.

## 🌐 Website deployments

The existing [Deploy site workflow](workflows/deploy.yml) builds the `website`
branch and publishes GitHub Pages when its configured triggers run or a maintainer
dispatches it. Review the workflow and site changes before any manual deployment;
deployment history is not a substitute for a tested release.
