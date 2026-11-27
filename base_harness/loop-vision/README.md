# Loop Vision

Loop Vision is a visual starting Harness derived from Loop 0.3.0. Select it with
`--harness loop-vision` for sandboxed native `files` tasks. It runs one shell
command per tool action and gives the model its result.

## Image attachments

Add an `images` array to a shell action to attach files from the task working
directory to the next model request. The command can create the files in the
same action.

When the action omits `images`, Loop Vision can also read image paths from a
successful command's standard output. The output must be a single JSON object
with an `images` mapping from labels to relative file paths. An explicit array
takes priority. Use `"images": []` to skip attachments for an action.

Use relative paths to regular PNG or JPEG files. An action can attach up to three
images of up to 1 MiB each. The Harness reports each unreadable attachment in the
tool result, and a failed shell command skips its attachments. Use a fresh image
directory when the command might leave files from an earlier observation in
place. The next request includes the shell's textual result, image labels, and
inline image content. At most one set of images stays in the model's history.
An image-set replacement leaves the earlier textual result and file labels in
the history. When the history is shortened, retain the current result in the
same way as ordinary Loop text results. Trace entries record the attachment
paths, byte counts, digests and failures.

## Runtime

Use CPython 3.12, uv and Bubblewrap. Prepare the actor dependencies with

```sh
python tools/configure_harness.py --harness base_harness/loop-vision --fetch
```

For a live run, configure `HG_AGENT_BACKEND=openai`, `HG_AGENT_BASE_URL`,
`HG_AGENT_MODEL` and `HG_AGENT_API_KEY` for a Chat Completions endpoint that
accepts inline image inputs. For tasks with `network: none`, the native model route
requires a plain HTTP base URL reachable by the host. The actor uses the same
pinned OpenAI SDK as Loop.

The default `HG_AGENT_MAX_STEPS` is 32 Harness turns, including retries. The mock
backend supports the ordinary Loop plumbing control. Use a scripted mock HTTP
endpoint to verify image transport and a pixel-dependent test to check whether
the chosen model can interpret the image.

## Example with RoboCasa365

This shell action uses the benchmark's client to save the current three camera
views. The client reports their paths in its JSON output, and Loop Vision
attaches the files to the next model request. See the [RoboCasa365 adapter
documentation](../../data/datasets/robocasa365/README.md) for setup and controls.

```json
{
  "tool": "bash",
  "command": "python3 robocasa365_client.py observe --image-dir views"
}
```
