# Customization proposals

The customization command generates an **unevaluated candidate** from text and feedback that you explicitly supply. It combines existing proposal stages according to the provided surfaces: skill text uses the SkillOpt fast stage, and a separate instruction uses ACE reflection and curation. It does not run tasks, score a candidate, modify your harness or publish anything.

## Inspect a Harness

If the Harness has a `harness.json` manifest, inspect its declared components before selecting files:

```bash
harnessgrad customize inspect --root /path/to/harness
```

The command prints the declared identity, environment kinds and component-to-file map as JSON. It reads only the manifest, does not check whether listed files exist, and does not run the Harness or call a model. An empty component map means none were declared; it does not mean the Harness has no components. A manifest declaration is not evidence of runtime behavior or permission to modify a file. The root must be trusted, and the manifest must be bounded, valid JSON without links.

## Input

Create a JSON request containing at least one feedback item and one surface. Feedback must be categorized by the caller as `failure` or `success`; the command does not turn it into a numeric score. A request can include `skills`, `instruction`, or both.

```json
{
  "feedback": [{"id": "review-1", "kind": "failure", "text": "The task ended without running the project tests."}],
  "skills": {"skills/main.md": "Implement the requested change.\n"},
  "instruction": {"path": "prompt.md", "text": "Complete the requested task.\n"}
}
```

Skill keys must be relative Markdown paths under `skills/`. An instruction must be a separate relative path. Paths must use canonical POSIX spelling: empty, `.` and `..` segments, backslashes and colons are rejected for both input and candidate paths. Maximum request size is 2 MiB; proposal JSON is limited to 8 MiB so it can be read by review and patch. Duplicate JSON keys are rejected.

## Collect selected files

If the text already exists in a harness directory, `collect` reads only the files you name and prints the same request JSON. The feedback file is a JSON array, such as the `feedback` array above. This step is local and does not call a model.

```bash
harnessgrad customize collect --root /path/to/harness \
  --skill skills/main.md --instruction prompt.md \
  --feedback /path/to/feedback.json > /path/to/request.json
```

Repeat `--skill` for multiple files, or omit either `--skill` or `--instruction` when it is not needed. The selected root must be trusted; selected paths must be relative, regular UTF-8 files without symlinks or hard links. Limits are 64 files, 256 KiB per file and 1 MiB in total; the combined JSON must also fit the 2 MiB request limit. Changes to a selected file while it is read are rejected, but several files are not read as one atomic snapshot. Protect the generated request as you would the source and feedback it contains.

## Generate a candidate

Configure an OpenAI-compatible Chat Completions endpoint and run the command. The selected input text and feedback are sent to this endpoint; use only material you intend to share with it.

```bash
export HG_METHOD_BASE_URL='YOUR_EDITOR_ENDPOINT_URL'
export HG_METHOD_MODEL='YOUR_EDITOR_MODEL_NAME'
export HG_METHOD_API_KEY='YOUR_EDITOR_API_KEY'
harnessgrad customize propose --request /path/to/request.json --max-calls 8
```

The command prints JSON containing the candidate files, a unified diff, feedback IDs, stage provenance, an input digest and observed model usage. `status: unevaluated` means no task or correctness test has been run. Redirect stdout to a file if you want to retain the proposal; review the diff before manually applying it. The proposal command reads the explicitly selected request file and does not overwrite it or the source harness.

To also turn failure feedback into reviewable memory records and topic skills, add `--with-memory` and save the output:

```bash
harnessgrad customize propose --request /path/to/request.json --with-memory --max-calls 8 > /path/to/proposal.json
```

This option requires selected skill text and at least one caller-labeled failure. It adds reflection and skill compilation after the existing proposal stages; it does not ask you to select an RSI method. Multiple failures or compilation steps can require more logical calls. `memories` contains model-generated, unverified records tied to the supplied feedback IDs; these records are printed with the candidate, not saved into a personal memory store. The same endpoint receives the selected feedback and skill text. Review and test the result before applying it.

To add a failure-focused rewrite of the curated instruction, pass `--refine-instruction`. It requires an instruction and at least one failure item, and adds one logical model call. It can be combined with `--with-memory` and is included in the same `--max-calls` limit. The rewrite receives the selected failures and curated instruction and may replace the entire instruction, so inspect the diff for lost guidance before testing or applying it. The default proposal is unchanged when the flag is absent.

```bash
harnessgrad customize propose --request /path/to/request.json --refine-instruction --max-calls 8 > /path/to/proposal.json
```

`--max-calls` limits logical model calls, not HTTP attempts: the shared transport can retry a call. The usage report records observed attempts and per-response token usage, keeping unavailable token counts as `null`. A limit, malformed input, oversized proposal, model error or invalid response ends the command without returning a partial candidate; stderr includes the observed usage so far. The model must return a JSON object for each stage.

This interface does not automatically collect documents, create an environment, run validation or deploy a harness. It exposes the first reusable proposal path; the experiment workflow and its scoring protocol remain separate.

## Review consistency offline

Save the proposal output and check it against the original request without calling a model:

```bash
harnessgrad customize review --request /path/to/request.json \
  --proposal /path/to/proposal.json --root /path/to/harness
```

The command checks the input digest, selected surfaces, feedback IDs, stages and diff. When memory records are present, it also checks their structure and selected failure IDs; it does not establish that their content is true. With `--root`, it also compares the selected original files to the request under the same file-count and byte limits as `collect`; without it, `source_check` is `not_checked`. It does not check the rest of the harness or whether new candidate paths already exist. The digest is not a signature: anyone able to change the request and proposal can recompute it. A consistent proposal is still unevaluated and requires human review and appropriate testing. No files are applied or modified.

To inspect which declared Harness components the changed paths touch, add `--components` together with `--root`:

```bash
harnessgrad customize review --request /path/to/request.json \
  --proposal /path/to/proposal.json --root /path/to/harness --components
```

This reads the root's bounded, non-linked `harness.json` and returns aggregate and per-path attribution from its component declarations. Paths without an exact declaration remain `unattributed`. This describes files touched, not observed component behavior, permissions or improvement. The manifest and selected files are read separately, not as an atomic snapshot.

## Export a patch

After reviewing a saved proposal, export its unified diff without modifying the Harness:

```bash
harnessgrad customize patch --request /path/to/request.json \
  --proposal /path/to/proposal.json --root /path/to/harness > /path/to/candidate.patch
# Use a separate copy of the Harness to try the candidate.
git -C /path/to/harness-copy apply --check /path/to/candidate.patch
git -C /path/to/harness-copy apply /path/to/candidate.patch
```

The copy must contain the same selected source files; `git apply --check` checks that the patch fits the copy. The export rechecks the selected source files and refuses new candidate paths that already exist or traverse symlinks. It also refuses new empty files, control characters in changed paths and text line formats that the generated unified diff cannot reliably represent. The root and its ancestors must be trusted. Checks are not an atomic snapshot and do not reserve paths; inspect the patch and recheck immediately before any manual application. Exporting or applying a patch does not evaluate the candidate.
