# JobBench renderer dependencies

This component profile supplies the isolated Python dependencies for the pinned
JobBench author's data fetch and rendering code. It is not a published dataset
or a scoring environment; platform Python does not import its SDKs.

Use the explicit preparation and read-only check commands in the
[JobBench preparation guide](../jobbench/README.md). Rendering records this
artifact's identity together with the pinned renderer sources. The execution
and judge configuration for a generated task are separate requirements.
