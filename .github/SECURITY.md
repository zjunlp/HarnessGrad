# Security Policy

## 🔒 Report a vulnerability

Please do not post vulnerability details, credentials or private evaluation
data in a public issue or pull request. If GitHub's private vulnerability
reporting is enabled for this repository, use
[Report a vulnerability](https://github.com/zjunlp/HarnessGrad/security/advisories/new).

If that option is unavailable, contact a project maintainer through an existing
private channel. If you do not have one, open a public issue asking for a private
contact **without including vulnerability details or secrets**.

A useful private report includes the affected revision, operating system, a
minimal reproduction and the potential impact. Remove API keys, tokens, `.env`
contents, private task answers and sensitive logs before sharing material.

## 📦 Scope

HarnessGrad runs agent Harnesses and evaluation tools with user-provided models,
files and environments. Reports about unintended access across a declared
isolation boundary, credential disclosure, or unsafe handling of untrusted task
and tool output are particularly useful. The project currently maintains the
default branch; release-specific support commitments should be stated with a
published release.
