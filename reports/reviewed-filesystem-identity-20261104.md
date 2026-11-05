# Complementary reviewed source filesystem identity

Independent registered tree `/tmp/hg-reviewed-filesystem-identity-20261104`, branch
`codex/reviewed-filesystem-identity-20261104`, exact base
`1b34f5eb78d9acf0af4e2fa468aeaf1cb4e874d2` (the reviewed-source bytecode-strip
follow-up). Main, the author's prepared-source tree and other review trees were
not edited. This increment does not change the existing path+content submission
SHA256 contract or historical receipts.

## Why a complementary identity is needed

Synthetic reproductions showed equal submission SHA256 with changed behavior:
chmod644→755 changes an executable tool from PermissionError to successful
execution; a regular Python helper replaced by an in-tree same-content symlink
changes is_symlink() fromFalse toTrue; adding an empty directory changes is_dir()
fromFalse toTrue. Git tree identity distinguishes executable bits and symlinks,
but ignores empty directories. Its recorded hash also came after model/task
configuration and was not the reviewer's prelaunch expectation.

## Deterministic filesystem contract

`tools.validate_harness.filesystem_identity` computes a separate SHA256 of
canonical JSON (sorted keys, compact separators, ensure_asciiTrue). Its payload
contains schema `harnessgrad-owned-source-filesystem-v1` and records ordered by
relative POSIX path UTF-8 bytes. It includes the root (path"."), every ordinary
directory including empty ones, each regular file and each safe symlink. Records
bind type and all POSIX rwx bits; symlink records also bind their normalized
root-relative direct target. File contents remain bound by the unchanged
submission SHA, so the two identities must be used together.

The source-side contract describes the actual normalized owned snapshot:
absolute direct in-tree links compare equal to their relative snapshot rewrite.
Original raw readlink text is not claimed unchanged. Link chains, targets through
symlink parents, escape/broken/cyclic links, special files and special permission
bits (setuid/setgid/sticky) refuse. Actual resolution must equal the normalized
lexical target; this rejects alias/../safe rather than silently erasing an alias
component with normpath. This deliberately conservative direct-target policy may
refuse source trees that the legacy interface accepts.

Exclusions exactly match bytecode stripping: all __pycache__ entries and leaf
*.pyc files/file aliases are omitted; meaningful *.pyc directories and directory
aliases remain. Included aliases into stripped cache paths refuse. Removing an
owned cache alias does not remove its meaningful target. No original source is
modified. This is a normalized source metadata fingerprint, not a claim about
raw-source hardlink relations, timestamps, ownership, ACL/xattrs, external runtime
or overlay closure, host/GPU contents, immutable storage or complete isolation.

## Binding and checks

The new opt-in CLI flag `--expected-harness-filesystem-sha256` requires
`--expected-harness-sha256` and its explicit source path. A strict source path
must be canonical, without a root alias. Before describe_model or task loading,
the driver checks original metadata, takes its private owned copy, strips ignored
bytecode, and checks both source bytes and filesystem identity. It rechecks the
original metadata after copying. ExitStack retains and cleans the private source
for the full driver lifetime.

The late owned staging copy is stripped and checked again before staging. After
stage_workspace, both the owned snapshot and the actual staged work's source
metadata are checked. Staged projection excludes only root .git/.gitignore/.state
entries that were absent from the reviewed snapshot and were newly created by
Git/platform staging. Source-provided same-name entries remain bound. Their
known platform content normalization is not redefined by this metadata contract.
The original and first private source are also rechecked, and run_meta records
harness_source.filesystem separately from submission_sha256 and Git harness_sha.
These are cooperative owned-copy/recheck boundaries, not an atomic hostile-
filesystem or same-UID process attestation.

Launcher harness_inputs includes the filesystem identity while checking source
stability. Prepared selection/current identity, execution_environment and command
construction all bind/recheck it. Formal launcher main always supplies bindings
and passes both expected digests to the driver. The legacy driver without the
new filesystem flag and the bindings-less command helper retain their prior
behavior. Existing sandbox, native context/compaction and permission policies
are unchanged.

## Public layered runtime commitment and recovery

Raw runtime environment values can contain proxy URL credentials and are never
copied into launch.json by this change. The new reviewed runtime hash algorithm is:

```
base_runtime_sha256 = digest(private_runtime_without_harness_filesystem)
public_commitment = {
  schema: "harnessgrad-runtime-source-filesystem-v1",
  base_runtime_sha256,
  filesystem: {schema, sha256, entries}
}
expected.runtime_sha256 = digest(public_commitment)
```

Launch records only that non-secret commitment, source/fs expected command flags
and the existing reviewed measurement identity. It does not serialize the raw
runtime or environment. This changes runtime identity: freshly reviewed
selections are required; it does not claim the older runtime hash algorithm
identifies the same experiment.

Recovery validates exact commitment/schema/digest/count shapes, recomputes its
hash against expected.runtime_sha256, requires exactly one matching source SHA
and filesystem SHA command argument, and compares the committed filesystem
record to run_meta.harness_source.filesystem. Missing fields, old hash algorithms,
duplicate flags, malformed records or mismatches remain unverified. Coordinated
command/metadata/filesystem tampering still fails the reviewed runtime hash.
Even a matching launch measurement identity is not promoted before all checks;
missing-run early returns stay unverified. Old and incomplete attempts retain
raw audit evidence but do not become comparable coverage or automatic resumes.
The synthetic two-interruption continuation fixture now records this full new
commitment/command/metadata contract.

## Validation and limits

```
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
 -p no:cacheprovider \
 tests/quality/test_reviewed_filesystem_identity.py \
 tests/quality/test_appworld_prepared_source.py \
 tests/quality/test_appworld_baseline_launcher.py \
 tests/quality/test_harness_path.py tests/quality/test_harness_path_eval.py \
 tests/quality/test_appworld_selection_prep.py \
 --basetemp=/tmp/hg-reviewed-filesystem-final-layered-20261104
```

**236 passed in63.03s**. Coverage includes actual harmless synthetic chmod/link/
empty-directory behavior; pre-model/pre-task guards; transient source metadata
changes restored after copying; late copy, snapshot and staged-work tampering;
normalized absolute/relative links; meaningful *.pyc directory compatibility;
bytecode-stripped strict driver success; unsafe links/permissions; launcher
runtime binding; public commitment privacy using credential-bearing synthetic
proxy URLs; thirteen recovery corruption/missing-field cases; and incomplete
legacy evidence. Existing mock/demo files controls and recovery interruption
controls are synthetic, not benchmark execution. No real model, native external
harness, AppWorld service, official scorer, dataset material/answers or network
request was used. `git diff --check` passes.

Scope boundaries remain explicit: source filesystem binding does not close the
separate driver dotenv reintroduction issue. The new public commitment itself
contains no raw runtime values; this is not a blanket privacy claim about other
preexisting platform config/egress traces. No experiment is authorized or resumed.
