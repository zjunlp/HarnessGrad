# Fixed public HF dependencies

This optional component supplies the public Outlines task1706 baseline dependencies
without changing its tests, model loader or metric. Preparation pins `erwanf/gpt2-mini`
to `f12cc7ee54aa4d3e6366597f72ff7acb39b0ab3a` and the TinyMistral Q4_K_M GGUF to
`M4-ai/TinyMistral-248M-v2-Instruct-GGUF@114104e33976dc4e499866d2b5951c8558fa8d14`.
It captures public model/tree responses and HEAD identities, verifies each Git/LFS
hash and byte count, and writes a manifest only after all required files succeed.
No token or model inference is used. Transport failures have at most three download
attempts; incomplete assets cannot enter the build context.

`prepare.py --root /external/fresh-assets` fetches the dependencies.
`build.py --assets /external/fresh-assets --output /external/fresh-build
--base-image sha256:... --python /absolute/real/python` stages a mirror layer on a
pinned prepared runtime. Build that context with `DOCKER_BUILDKIT=0 docker build --network=none --pull=false`;
this uses the already-local base image without registry resolution.
The dataset runtime must explicitly select `/opt/hg-hf/launcher.py` as its interpreter
and retain the build identity; the system interpreter is unchanged.

At the trusted container idle launch, the wrapper starts one root-owned loopback
server, verifies readiness against PID/start time/UID and a root-owned identity,
then executes the real interpreter for trusted helper operations. Actor/scorer
programs retain inherited standard streams under a small outer wrapper, which checks
service health again when the real interpreter exits. Service loss returns exit 78;
the existing worker-group policy treats nonzero worker exits as unmeasured. Private launch state stays in a root-only
directory. The existing side-effect-free image interpreter-version probe is also supported.
Nested wrappers reuse the same service; failed services are never restarted.
Trusted platform cleanup helpers remain usable after a service failure. Root service
processes die with the container. Workers and the verifier each start a fresh container;
no worker cache is imported by the verifier.

Assets under `/opt/hg-hf` are read-only. Client writes stay under the admitted
`/workspace/.hf-cache`; Docker remains `network=none`. `HF_ENDPOINT` is loopback,
`HF_HUB_OFFLINE=0` permits only that local HTTP access, and implicit authentication
and telemetry are disabled. The server has no upstream transport; unknown routes,
revisions and files return 404. `main` is explicitly frozen to the pinned revision.

Integration must check `launcher.py --hf-mirror-health` before and after scoring,
validate the expected manifest identity, and treat service failures as unmeasured.
This component alone does not certify an official task or complete benchmark run.
The optional `HG_COOPERBENCH_HF_TEST_IMAGE=sha256:...` test exercises tiny assets in
real observed containers, including actor tampering, service loss and cleanup.
