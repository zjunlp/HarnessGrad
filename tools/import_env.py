#!/usr/bin/env python3
"""Import a task environment, once, and pin it to a content address.

INTERFACE.md §2.5.5: *"It will not build a task's image during a run."* Building and
pulling are supply-chain events that execute third-party content with the daemon's
privileges, before any sandbox exists -- so they belong to a human at import time, not
to a dataset that a method may influence, and not to a run that a method can trigger.
This is the only tool in the repository that does either, and it is deliberately not
imported by the platform.

Three ways to import, all ending in the same output:

    # an image that already exists -- ensures it is local, then pins it
    python tools/import_env.py --image python:3.11-slim

    # a task's own image
    python tools/import_env.py --dockerfile tasks/foo/Dockerfile

    # a harness's dependencies, baked into the environment (§2.5.6 rule 2)
    python tools/import_env.py --image python:3.11-slim --harness base_harness/cli_agent

The output is the pinned reference and a `DEFAULT_ENV` block to paste into a dataset.
What it does **not** do is edit a dataset for you: rewriting a source file from a tool
is how a dataset ends up describing an image nobody chose.

## Why `--harness` lives here

`install` has been declared, validated and inert since the manifest gained the field,
because the harness tree is read-only by design and an install has nowhere to write.
The decision (§2.5.6) is that it goes **into the environment, at import time** -- the
per-harness half of the same one-time build as a task's Dockerfile:

    FROM <base>
    COPY . /opt/harnessgrad-install/
    RUN <the harness's own install command>

so `install` is the only place a harness's dependencies come from, and they are baked
into the image rather than installed into the host's interpreter. On the host they
cannot be installed at all: that would mutate a Python environment the platform does
not own, make two harnesses with conflicting pins both broken, and put a fact in the
run that no curve point records.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval.containers.container as container


def _run(argv: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(argv[:3])} failed:\n"
                         f"{(proc.stderr or proc.stdout).strip()[-1500:]}")
    return proc


def ensure_local(image: str, *, allow_pull: bool,
                 mirrors: list[str] | None = None) -> None:
    """Make `image` present on the daemon, pulling only if the caller allowed it.

    `--no-pull` exists because "import" on a machine with a slow or absent registry
    should fail and say so rather than hang, and because a run must never do this:
    see `container.resolve_digest`, which refuses in exactly this situation.

    **Mirrors are an import-time concern and only an import-time one.** On a host that
    cannot reach Docker Hub, a registry mirror is how an image arrives at all — and it
    arrives under the mirror's own reference, `<mirror>/<image>`, which is *not* the
    reference the benchmark publishes. So the image is pulled through the mirror and
    **re-tagged to its canonical name**, and the dataset goes on naming
    `alexgshaw/whatever:tag`. Without the re-tag, a dataset would have to know which
    mirror a particular machine used, and the digest on every curve point would carry a
    mirror's name into the record.

    Measured on this host: two of the three configured mirrors truncate large layers
    (`short read: ... unexpected EOF`) while the third serves them, so trying more than
    one is not belt-and-braces, it is the difference between an image and no image.
    """
    # `container.docker`, not `_run`: `_run` raises on a non-zero exit, so asking it
    # "is this image here?" raised for the answer "no" and the pull below was dead code.
    # Measured: `import_env.py --image <not-local>` failed with
    # "docker image inspect failed" instead of fetching anything. The happy path was
    # tested with an image that was already local, which is why it looked fine.
    if container.docker("image", "inspect", image, timeout=120).returncode == 0:
        return
    if not allow_pull:
        raise SystemExit(
            f"{image!r} is not local and --no-pull was given. Drop --no-pull to fetch "
            f"it, or import it by another route (docker load, docker build).")

    # Mirrors **first** when they are given. `--mirror` is a statement that this host
    # cannot reach the registry directly, so trying the canonical name first makes every
    # image pay a full connection timeout before the attempt that was going to work --
    # measured: a 68-image loop sat on its first image with nothing logged, because each
    # one was waiting out a doomed pull. With no mirror given the canonical name is tried
    # alone and this ordering is invisible.
    attempts = ([f"{m.rstrip('/')}/{image}" for m in (mirrors or [])] + [image])
    # Per-attempt, and it matters more than it looks: a bulk import over a mirror that
    # serves some images and hangs on others stalls the whole queue behind one bad
    # image. Measured: a 67-image loop spent 29 minutes on its *first* image, trying all
    # three mirrors, with nothing logged and no bytes on disk. A timeout turns "one
    # image blocks the run" into "one image is skipped and reported".
    pull_timeout = int(os.environ.get("HG_PULL_TIMEOUT_S") or 900)
    problems = []
    for ref in attempts:
        print(f"pulling {ref} ...", file=sys.stderr)
        proc = container.docker("pull", ref, timeout=pull_timeout)
        if proc.returncode != 0:
            problems.append(f"{ref}: {proc.stderr.strip().splitlines()[-1][:160]}")
            continue
        if ref != image:
            tagged = container.docker("tag", ref, image, timeout=120)
            if tagged.returncode != 0:
                problems.append(f"tag {ref} -> {image}: "
                                f"{tagged.stderr.strip()[:160]}")
                continue
            print(f"re-tagged {ref} as {image}", file=sys.stderr)
        return
    raise SystemExit(f"could not fetch {image!r}:\n  " + "\n  ".join(problems))


def install_command(install_file: str) -> str:
    """How to run a harness's declared dependency file, from inside the image.

    Two shapes, and anything else is refused rather than guessed at: a build step is
    arbitrary code, and a tool that invents a command for an unrecognised file is a
    tool that runs something nobody wrote.
    """
    name = Path(install_file).name
    if name.endswith(".txt"):
        return f"RUN python -m pip install --no-cache-dir -r /opt/harnessgrad-install/{name}"
    if name.endswith(".sh"):
        return f"RUN sh /opt/harnessgrad-install/{name}"
    raise SystemExit(
        f"install file {install_file!r} is neither a requirements `.txt` nor a `.sh`, "
        f"and this tool will not guess a command for it. Add the case deliberately if "
        f"the project needs one.")


def derived_dockerfile(base: str, harness_dir: Path, install_file: str) -> str:
    return (f"FROM {base}\n"
            f"# The harness's own dependencies, baked into the environment at import\n"
            f"# time (INTERFACE.md §2.5.6). The harness tree is read-only during a run,\n"
            f"# so this is the only moment an install can happen at all.\n"
            f"COPY . /opt/harnessgrad-install/\n"
            f"{install_command(install_file)}\n")


def harness_install(harness_dir: Path) -> str | None:
    """The `install` a harness declares, checked the way `validate_harness` checks it."""
    manifest = harness_dir / "harness.json"
    if not manifest.is_file():
        raise SystemExit(f"{harness_dir} has no harness.json")
    declared = json.loads(manifest.read_text()).get("install")
    if not declared:
        return None
    if not (harness_dir / declared).is_file():
        raise SystemExit(f"harness.json declares install {declared!r}, which is not "
                         f"under {harness_dir}")
    return declared


def build(context: Path, dockerfile_text: str, tag: str) -> str:
    """Build a context and return the image id, which is a content hash.

    `--iidfile` rather than parsing stdout: the id is the thing that goes on the
    record, and reading it out of human-facing build output is how a record acquires
    a value that was never the image's identity. A locally built image has no registry
    digest, so the id *is* the content address -- and it is what `resolve_digest`
    falls back to.
    """
    with tempfile.TemporaryDirectory(prefix="hg-import-") as tmp:
        (Path(tmp) / "Dockerfile").write_text(dockerfile_text)
        iid = Path(tmp) / "iid"
        _run(["docker", "build", "--file", str(Path(tmp) / "Dockerfile"),
              "--iidfile", str(iid), "--tag", tag, str(context)])
        image_id = iid.read_text().strip()
    if not container.looks_pinned(image_id):
        raise SystemExit(f"the build reported {image_id!r}, which is not a digest")
    return image_id


def import_from_dockerfile(dockerfile: Path, tag: str) -> str:
    """Build a task's own Dockerfile, with the Dockerfile's directory as context."""
    if not dockerfile.is_file():
        raise SystemExit(f"no such Dockerfile: {dockerfile}")
    with tempfile.TemporaryDirectory(prefix="hg-import-") as tmp:
        context = Path(tmp) / "context"
        # The context is a copy, so a build cannot write into the dataset's tree and
        # cannot accidentally ship the harness's scratch state if one is pointed here.
        shutil.copytree(dockerfile.parent, context,
                        ignore=shutil.ignore_patterns(".git", ".state", "__pycache__"))
        (context / "Dockerfile").write_text(dockerfile.read_text())
        return build(context, dockerfile.read_text(), tag)


def import_with_harness(base: str, harness_dir: Path, install_file: str,
                        tag: str) -> str:
    context = Path(tempfile.mkdtemp(prefix="hg-import-ctx-"))
    try:
        shutil.copytree(harness_dir, context,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", ".state", "__pycache__"))
        return build(context, derived_dockerfile(base, harness_dir, install_file), tag)
    finally:
        shutil.rmtree(context, ignore_errors=True)


def dataset_images(name: str) -> list[tuple[str, str]]:
    """`[(task_id, image)]` for every `exec` task a dataset declares, deduplicated.

    A dataset could not be configured in one command before this: `--image` takes one
    image at a time, so Terminal-Bench meant lifting 89 tags out of `task.toml` by hand
    and looping. The enumeration already existed -- the dataset loader answers it -- it
    was just not reachable from the tool that does the pulling.
    """
    import data.scaffolds.registry as datasets

    envs = datasets.load_envs(name)
    out: dict[str, str] = {}
    for tid, spec in sorted((envs or {}).items()):
        if (spec or {}).get("kind") == "exec" and spec.get("image"):
            out.setdefault(spec["image"], tid)
    return [(tid, image) for image, tid in out.items()]


def import_dataset(name: str, *, mirrors: list[str], allow_pull: bool,
                   json_out: bool) -> int:
    """Ensure every image a dataset's tasks need is local. Skips the ones that are.

    Not parallel: a host whose registry access is a flaky mirror fails *harder* under
    concurrency, and the failure this has to survive is a large blob timing out
    partway. Measured: three Terminal-Bench images could not be fetched here at all.
    """
    if container.available() is None:
        raise SystemExit("no docker daemon is reachable")
    wanted = dataset_images(name)
    if not wanted:
        print(f"dataset {name!r} declares no `exec` tasks; nothing to import")
        return 0

    local, pulled, failed = [], [], []
    for tid, image in wanted:
        try:
            container.resolve_digest(image)
            local.append((tid, image))
            print(f"  present  {tid:34s} {image}")
            continue
        except container.ContainerUnavailable:
            pass
        print(f"  pulling  {tid:34s} {image}", flush=True)
        try:
            ensure_local(image, allow_pull=allow_pull, mirrors=mirrors)
            container.resolve_digest(image)
            pulled.append((tid, image))
            print(f"  pulled   {tid:34s} {image}")
        except (SystemExit, Exception) as exc:                  # noqa: BLE001
            reason = str(exc).strip().splitlines()[-1][:200] if str(exc).strip() else \
                type(exc).__name__
            failed.append({"task_id": tid, "image": image, "why": reason})
            print(f"  FAILED   {tid:34s} {image}\n           {reason}", file=sys.stderr)

    if json_out:
        print(json.dumps({"dataset": name, "total": len(wanted),
                          "present": [t for t, _ in local],
                          "pulled": [t for t, _ in pulled],
                          "failed": failed}, indent=1))
    print()
    print(f"{name}: {len(wanted)} image(s) -- {len(local)} already local, "
          f"{len(pulled)} pulled, {len(failed)} failed")
    if failed:
        print("A task whose image cannot be fetched is one this host cannot measure. "
              "The driver refuses to start such a run and names the task, and the "
              "console marks it unrunnable with this reason.", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="an image that exists (or can be pulled)")
    src.add_argument("--dockerfile", help="a task's Dockerfile, built once, here")
    src.add_argument("--dataset", help="every `exec` image a dataset's tasks need, "
                                       "skipping the ones already local")
    ap.add_argument("--harness", help="with --image: bake this harness's `install` "
                                      "into a derived image")
    ap.add_argument("--tag", default=None,
                    help="tag for a built image (default: harnessgrad-import:<time>)")
    ap.add_argument("--no-pull", action="store_true",
                    help="refuse to fetch a missing --image instead of pulling it")
    ap.add_argument("--mirror", action="append", default=[], metavar="PREFIX",
                    help="a registry mirror to try when the image is not local, e.g. "
                         "docker.1panel.live. The image is pulled as <PREFIX>/<image> "
                         "and re-tagged to its canonical name, so the dataset keeps "
                         "naming the reference the benchmark publishes and the digest "
                         "on a curve point carries no mirror's name. Repeatable.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.dataset:
        if args.harness:
            raise SystemExit("--harness derives an image; use --image with it, or run "
                             "tools/configure_harness.py for a whole dataset")
        return import_dataset(args.dataset, mirrors=args.mirror,
                              allow_pull=not args.no_pull, json_out=args.json)

    if container.available() is None:
        raise SystemExit("no docker daemon is reachable")

    tag = args.tag or "harnessgrad-import:latest"

    if args.harness:
        if not args.image:
            raise SystemExit("--harness goes with --image: it derives from a base image")
        harness_dir = Path(args.harness).resolve()
        install_file = harness_install(harness_dir)
        if install_file is None:
            # Not an error: a self-contained harness needs nothing, and the useful
            # answer is the base image pinned, so the caller gets a working command.
            print(f"harness {harness_dir.name!r} declares no `install`; importing the "
                  f"base image unchanged", file=sys.stderr)
            ensure_local(args.image, allow_pull=not args.no_pull,
                         mirrors=args.mirror)
            pinned = container.resolve_digest(args.image)
        else:
            ensure_local(args.image, allow_pull=not args.no_pull,
                         mirrors=args.mirror)
            base = container.resolve_digest(args.image)
            print(f"building a derived image: {args.image} + {install_file}",
                  file=sys.stderr)
            pinned = import_with_harness(base, harness_dir, install_file, tag)
    elif args.dockerfile:
        print(f"building {args.dockerfile} ...", file=sys.stderr)
        pinned = import_from_dockerfile(Path(args.dockerfile).resolve(), tag)
    else:
        ensure_local(args.image, allow_pull=not args.no_pull, mirrors=args.mirror)
        pinned = container.resolve_digest(args.image)

    digest = container.digest_of(pinned)
    if args.json:
        print(json.dumps({"pinned": pinned, "image_digest": digest,
                          "declared": pinned}, indent=2))
        return 0

    print()
    print(f"pinned:       {pinned}")
    print(f"image_digest: {digest}")
    print()
    print("Paste this into the dataset's DEFAULT_ENV (or ENV[<task>]):")
    print()
    print('    DEFAULT_ENV = {"kind": "exec",')
    print(f'                   "image": "{pinned}",')
    print('                   "workdir": "/app",')
    print('                   "network": "<the task\'s own -- do not assume none>",')
    print('                   "placement": "inside"}')
    print()
    print("`network` is the task's, not the image's: measured, all 89 Terminal-Bench")
    print("tasks declare `bridge`, and a task with network is a different task from one")
    print("without (§2.5.4). Copying a `none` from a template would change what the")
    print("benchmark measures.")
    print()
    print("Naming a tag also works -- the driver resolves it before the run starts and")
    print("refuses to start if it cannot, so no `exec` curve is ever recorded without a")
    print("digest on it. The pinned form is what makes the record independent of this")
    print("machine's daemon.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
