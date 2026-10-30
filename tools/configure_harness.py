#!/usr/bin/env python3
"""Build a harness's dependency overlay, once, for each Python version it will meet.

    python3 tools/configure_harness.py --harness base_harness/loop          # the `files` half
    python3 tools/configure_harness.py --harness base_harness/loop --dataset terminal_bench
    python3 tools/configure_harness.py --harness base_harness/loop --python 3.13 \
        --builder-image IMAGE
    python3 tools/configure_harness.py --harness base_harness/loop --list

No source flag means **this host**: the interpreter a `files` run launches. A version that is
not this host's needs `--builder-image`, because the key is the version and a guessed key is
worse than no overlay -- it imports cleanly until it does not.

This is the "configure once" half of `eval/containers/harness_runtime.py`: that module only looks
overlays up, because §2.5.5 puts anything that pulls or builds behind a human at import
time rather than on a run's path. Both halves exist so that a harness can be run against
a benchmark whose images already exist, **without rebuilding any of them**.

Why an overlay and not a derived image
--------------------------------------
`import_env.py --harness` bakes the dependencies into an image, which is `O(#task
images)` per harness -- 89 derived images for Terminal-Bench. Anyone arriving with their
own harness pays that before their first run. An overlay is `O(#Python versions)`, which
is a property of the *datasets*, not of the harnesses: measured 5 for Terminal-Bench
(3.13 x41, 3.12 x13, 3.11 x3, 3.10 x1, 3.9 x2, and 26 images with no interpreter at all,
which need nothing because the platform already provides one).

So the cost of a new harness does not move when the task set grows, which is the
property that makes "bring your own harness" true rather than aspirational.

How it is built
---------------
With the **task image's own** `pip`, into a directory outside every image:

    docker run --rm -v <overlay>:/out --entrypoint <the image's python> <task image> \\
        -m pip install --no-cache-dir --target /out -r <requirements>

Using a task image as the builder is deliberate and it is not a shortcut. It means the
wheels are resolved for *that* interpreter, on *that* platform tag, so a `3.13` overlay
cannot silently be a `3.12` one -- and it needs no base image to be pullable, which
matters on a host whose registry access is a flaky mirror.

Verification is by import, not by exit code: every installed distribution's recorded
top-level modules are imported with the target interpreter and the overlay on
`sys.path`. That is the check that catches an ABI mismatch, which is the failure this
whole design turns on (`pydantic_core` and `jiter` ship compiled extensions).

Host overlays -- the `files` half
---------------------------------
A `files` run has no image. `eval/run/runner.py:611` launches the bare name `python`, so
the harness's imports resolve against **that** interpreter's own site-packages:
unpinned, undeclared and unrecorded. Measured here: `base_harness/loop` declares
`openai>=1.0` and runs on ALFWorld only because the host's anaconda happens to have
`openai` -- nothing in the record says so.

The same key is built with the host's own `pip` instead of a container:

    <python> -m pip install --no-cache-dir --target <overlay> -r <requirements>

and the probe is **hermetic**: `sys.path` is set to the overlay plus the interpreter's
own stdlib (`python -S`), with no site-packages at all. Without that the host copy would
satisfy every import and a broken overlay would be reported good -- the same false pass
the image probe exists to prevent.

What a host overlay does **not** do is exclude the host's site-packages from the run.
`PYTHONPATH` shadows a module of the same name; a dependency the requirements do not
name still comes from the host. That is strictly better than all of them coming from the
host unpinned, and it is recorded rather than assumed: `PROVENANCE.json` carries
`host_site_packages_visible: true` and `env.harness_runtime` repeats it on every point.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval.containers.container as container                              # noqa: E402
import eval.containers.harness_runtime as runtime                          # noqa: E402


def _has_pip(image: str, python: str) -> bool:
    """Whether this image can build an overlay at all.

    The overlay is built with the task image's own `pip`, because that is what makes the
    wheels match its interpreter exactly and what avoids needing a base image to be
    pullable. Measured: `alexgshaw/qemu-startup` ships a Python **without pip**, so the
    build cannot happen there and the harness on that image has no source of its
    dependencies at all -- the platform's interpreter is deliberately not mounted when
    the image has one.
    """
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", python,
             image, "-m", "pip", "--version"],
            capture_output=True, text=True, errors="replace", timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _python_versions_from_dataset(name: str, *, limit: int = 0) -> dict[str, list[str]]:
    """`{"3.13": [image, ...]}` for the images a dataset declares.

    One representative image per version is enough to build from, so the rest are
    recorded only to be reported -- a reader wants to know how many tasks a version
    covers before paying for it.
    """
    import data.scaffolds.registry as datasets

    envs = datasets.load_envs(name)
    images: dict[str, list[str]] = {}
    for spec in (envs or {}).values():
        if (spec or {}).get("kind") != "exec" or not spec.get("image"):
            continue
        image = spec["image"]
        if image in [i for v in images.values() for i in v]:
            continue
        try:
            pinned = container.resolve_digest(image)
        except container.ContainerUnavailable:
            continue                       # not local; `--list` reports these separately
        python = container.image_python(pinned)
        if not python:
            continue                       # no interpreter: the platform's covers it
        version = runtime.interpreter_version(python, image=pinned)
        if version:
            images.setdefault(version, []).append(image)
    _ = limit                      # kept for callers that want the full list
    return images


def _top_levels(overlay: Path) -> dict[str, list[str]]:
    """What actually landed, and the modules each distribution provides.

    Read from the wheel's `RECORD`, not from `top_level.txt`. Measured on a real
    `openai` install: **3 of 14 distributions write `top_level.txt`** (the setuptools
    ones), so probing from it would have imported `anyio`, `h11` and `sniffio` and
    declared the overlay good -- skipping `pydantic_core` and `jiter`, which are the
    compiled extensions and therefore the only ones that can fail on an ABI mismatch.
    `RECORD` is mandatory in a wheel, so it names every module for every build backend.

    A distribution that installs only console scripts (`truststore`, say) legitimately
    has no importable module and is recorded with an empty list rather than guessed at.
    """
    found: dict[str, list[str]] = {}
    for meta in sorted(overlay.glob("*.dist-info")):
        # Keyed by the **distribution name**, with the version in the value. The
        # directory is `<name>-<version>.dist-info` and pip normalizes `-` to `_` in the
        # name, so the version is whatever follows the last `-`; keeping it in the key as
        # well would make a name lookup depend on a version the reader already has.
        stem = meta.name[: -len(".dist-info")]
        name, _, version = stem.rpartition("-")
        name = name or stem
        version = version or "?"
        try:
            for line in (meta / "METADATA").read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass

        tops: list[str] = []
        try:
            record = (meta / "RECORD").read_text(encoding="utf-8", errors="replace")
        except OSError:
            record = ""
        for line in record.splitlines():
            rel = line.split(",", 1)[0].strip()
            if not rel or rel.startswith("../") or rel.startswith("/"):
                continue
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts:
                continue
            head = parts[0]
            if head.endswith((".dist-info", ".data")) or head == "__pycache__":
                continue
            if len(parts) == 1:
                if head.endswith(".py") and head != "__init__.py":
                    tops.append(head[:-3])
            elif head.isidentifier():
                tops.append(head)
        found[name] = {"version": version, "top_level": sorted(set(tops))}
    return found


def _clear(overlay: Path, image: str, python: str) -> None:
    """Empty an overlay directory, whoever wrote it.

    Built as the invoking user, so the ordinary path is a plain `rmtree`. The fallback
    exists because an earlier version of this tool built as root, and a root-owned tree
    under a user's own cache directory cannot be deleted by them -- measured: that is
    exactly how this function came to exist. Rather than telling the user to run `sudo
    rm -rf` in their own cache, the container that can write there does the removal.
    """
    import shutil
    try:
        for stale in overlay.iterdir():
            shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
        return
    except PermissionError:
        pass
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{overlay}:/out", "--entrypoint", "sh",
         image, "-c", "rm -rf /out/* /out/.[!.]* 2>/dev/null; true"],
        capture_output=True, text=True, errors="replace", timeout=300)


def build_one(harness: Path, install: tuple[str, str], version: str,
              image: str) -> dict:
    """Build and verify one overlay. Returns the provenance that gets written."""
    pinned = container.resolve_digest(image)
    python = container.image_python(pinned)
    if not python:
        raise SystemExit(f"{image} has no interpreter of its own; nothing to build for")
    found = runtime.interpreter_version(python, image=pinned)
    if found != version:
        raise SystemExit(
            f"{image} reports Python {found}, not {version} -- the overlay's key would "
            "be wrong, and a wrong key is worse than no overlay because it imports "
            "cleanly until it does not")

    overlay = runtime.overlay_path(install, version)
    overlay.mkdir(parents=True, exist_ok=True)
    # A rebuild **replaces**: `pip --target` does not remove files from the previous
    # resolution, so a pin removed from `requirements.txt` would otherwise keep its old
    # version in the overlay and the provenance would describe a tree that is not there.
    _clear(overlay, pinned, python)

    req = Path(harness) / install[1]
    started = time.time()
    # Built as the invoking user, not as root. The overlay is a host directory that
    # gets replaced on a rebuild, and a root-owned tree cannot be deleted by the person
    # who ran the command -- measured: an earlier version left one that had to be
    # removed from inside a container. `HOME` is redirected because the build user need
    # not have one in the image.
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network", "bridge",
         "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
         "-v", f"{overlay}:/out",
         "-v", f"{req}:/requirements.txt:ro",
         "--entrypoint", python, pinned,
         "-m", "pip", "install", "--no-cache-dir", "--target", "/out",
         "-r", "/requirements.txt"],
        capture_output=True, text=True, errors="replace", timeout=1800)
    if proc.returncode != 0:
        # Leave nothing behind. An empty directory would read as "built" to `--list` and
        # would make `lookup` probe an interpreter for an overlay that is not there.
        shutil.rmtree(overlay, ignore_errors=True)
        raise SystemExit(f"pip failed for {version}:\n{proc.stdout[-1500:]}\n"
                         f"{proc.stderr[-1500:]}")

    packages = _top_levels(overlay)
    provenance = {
        "harness": Path(harness).name,
        "install": install[1], "install_sha256": install[0],
        "python": version, "built_from_image": image, "built_from_digest": pinned,
        "interpreter": python,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 1),
        "packages": packages,
    }
    (overlay / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=1, sort_keys=True), encoding="utf-8")
    return provenance


def verify_one(harness: Path, install: tuple[str, str], version: str,
               image: str) -> tuple[bool, str]:
    """Import every recorded top-level module with the target interpreter.

    This is the step that makes the overlay trustworthy rather than merely present. A
    directory of wheels resolved for the wrong ABI imports as far as `importlib` is
    concerned and then fails on the first extension module it touches; importing the
    modules is what catches it.
    """
    overlay = runtime.overlay_path(install, version)
    prov = json.loads((overlay / "PROVENANCE.json").read_text(encoding="utf-8"))
    pinned = container.resolve_digest(image)
    python = container.image_python(pinned)
    modules = sorted({m for p in prov["packages"].values() for m in p.get("top_level") or []})
    if not modules:
        return True, f"{version}: nothing recorded a top_level.txt; not import-probed"
    script = ("import importlib,sys\n"
              "bad=[]\n"
              f"for m in {modules!r}:\n"
              "    try: importlib.import_module(m)\n"
              "    except BaseException as e: bad.append(f'{m}: {type(e).__name__}: {e}')\n"
              "print('\\n'.join(bad))\n")
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{overlay}:/overlay:ro", "-e", "PYTHONPATH=/overlay",
         "--entrypoint", python, pinned, "-c", script],
        capture_output=True, text=True, errors="replace", timeout=300)
    bad = proc.stdout.strip()
    if proc.returncode != 0 or bad:
        return False, f"{version}: {bad or proc.stderr.strip()[-500:]}"
    return True, f"{version}: {len(modules)} module(s) import with {python}"


# --------------------------------------------------------------- host overlays ---
#
# The `files` half. Nothing below starts a container: a `files` run has no image to build
# from, and the interpreter that will run the harness is already on this machine.


#: A `files` run launches the bare name `python` (`eval/run/runner.py:611`), so an overlay
#: for a `files` harness has to be keyed on what **that name resolves to**. Probing
#: `sys.executable` instead would build for a different interpreter on any host where the
#: two disagree, and a wrong key is worse than no overlay: it imports cleanly until it
#: does not.
DEFAULT_HOST_INTERPRETER = "python"


def _host_interpreter(name: str) -> Path | None:
    """The interpreter a `files` run would launch, resolved the way the run resolves it.

    **Deliberately not `resolve()`d.** A venv's `bin/python` is a symlink to the base
    interpreter, and the venv's own site-packages come from `pyvenv.cfg` beside the
    symlink, not beside its target -- so following the link would build the overlay with
    a *different* interpreter than the one the harness will run on, and the pip it uses
    would be the base one. `which` already returns an absolute path on any sane `PATH`;
    `.absolute()` only covers the case where some entry is relative.
    """
    found = shutil.which(name)
    return Path(found).absolute() if found else None


def _host_has_pip(python: Path) -> bool:
    try:
        proc = subprocess.run([str(python), "-m", "pip", "--version"],
                              capture_output=True, text=True, errors="replace",
                              timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _stdlib_paths(python: Path) -> list[str]:
    """`sys.path` with **no** site-packages: what `python -S` leaves, as data.

    Asked of the interpreter rather than computed from `sysconfig`, because a Python
    under a non-standard prefix (measured here: anaconda on `/mnt/8t`) spells its
    `lib-dynload` and its stdlib zip in a way only it knows. Entries that do not exist
    are dropped, and so is `''`: `-c` puts the cwd first, and a probe that can see the
    cwd can import whatever happens to be sitting there.
    """
    proc = subprocess.run(
        [str(python), "-S", "-c",
         "import json,sys;print(json.dumps([p for p in sys.path if p]))"],
        capture_output=True, text=True, errors="replace", timeout=120)
    try:
        paths = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return []
    return [p for p in paths if Path(p).is_dir() or p.endswith(".zip")]


def build_one_host(harness: Path, install: tuple[str, str], version: str,
                   python: Path) -> dict:
    """Build one host overlay with the host's own pip. Returns the provenance."""
    found = runtime.interpreter_version(str(python))
    if found != version:
        raise SystemExit(
            f"{python} reports Python {found}, not {version} -- the overlay's key would "
            "be wrong, and a wrong key is worse than no overlay because it imports "
            "cleanly until it does not")

    overlay = runtime.overlay_path(install, version)
    overlay.mkdir(parents=True, exist_ok=True)
    # Same reason as the image path: `pip --target` does not remove files left by an
    # earlier resolution, so a pin dropped from `requirements.txt` would otherwise keep
    # its old version and the provenance would describe a tree that is not there. Built
    # as the invoking user, so a plain removal is enough -- no container fallback.
    for stale in overlay.iterdir():
        shutil.rmtree(stale) if stale.is_dir() else stale.unlink()

    req = Path(harness) / install[1]
    started = time.time()
    # `--no-input`: an index that stops to ask a question would hang a command the user
    # expects to finish. The proxy is the caller's business and is inherited from the
    # environment, exactly as it is for the container build.
    proc = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-cache-dir", "--no-input",
         "--target", str(overlay), "-r", str(req)],
        capture_output=True, text=True, errors="replace", timeout=1800)
    if proc.returncode != 0:
        # Leave nothing behind: an empty directory reads as "built" to `--list`, and
        # `lookup` would then probe an interpreter for an overlay that is not there.
        shutil.rmtree(overlay, ignore_errors=True)
        raise SystemExit(f"pip failed for {version}:\n{proc.stdout[-1500:]}\n"
                         f"{proc.stderr[-1500:]}")

    packages = _top_levels(overlay)
    provenance = {
        "harness": Path(harness).name,
        "install": install[1], "install_sha256": install[0],
        "python": version,
        # No image was involved, and saying so is what lets `record()` name the one
        # difference that matters: these packages **shadow** the host's, they do not
        # exclude them. A dependency the requirements do not name still comes from the
        # host, and a reader of the curve is entitled to know that without reading this
        # file.
        "built_from": "host", "built_from_image": None,
        "interpreter": str(python),
        "host_site_packages_visible": True,
        "platform": sys.platform,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 1),
        "packages": packages,
    }
    (overlay / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=1, sort_keys=True), encoding="utf-8")
    return provenance


def verify_one_host(harness: Path, install: tuple[str, str], version: str,
                    python: Path) -> tuple[bool, str]:
    """Import every recorded module with the overlay and the stdlib, and nothing else.

    Hermetic on purpose. The host already has most of these packages installed, so the
    probe the image path can afford -- the interpreter's normal `sys.path` with the
    overlay prepended -- would let the host's copy answer for a module the overlay got
    wrong, and the whole point of the check is the ABI mismatch that only shows up when
    the extension module is actually loaded.

    It also reports **where** each module came from, not just that it imported. A name
    that resolves inside the overlay is a dependency this overlay pins; one that resolves
    anywhere else is not, and that distinction is the difference between "the harness's
    dependencies are pinned" and "the harness's dependencies are pinned *except*".
    """
    overlay = runtime.overlay_path(install, version)
    prov = json.loads((overlay / "PROVENANCE.json").read_text(encoding="utf-8"))
    modules = sorted({m for p in prov["packages"].values() for m in p.get("top_level") or []})
    if not modules:
        return True, f"{version}: no distribution recorded an importable module; not probed"
    stdlib = _stdlib_paths(python)
    if not stdlib:
        return False, (f"{version}: could not read {python}'s stdlib paths, so a hermetic "
                       f"probe is impossible; refusing to report a check that did not run")
    script = (
        "import importlib, json, sys\n"
        f"sys.path[:] = [{str(overlay)!r}] + {stdlib!r}\n"
        "out = {}\n"
        f"for name in {modules!r}:\n"
        "    try:\n"
        "        mod = importlib.import_module(name)\n"
        "    except BaseException as exc:\n"
        "        out[name] = {'error': f'{type(exc).__name__}: {exc}'}\n"
        "        continue\n"
        "    where = getattr(mod, '__file__', None)\n"
        "    if where is None:\n"
        "        where = next(iter(list(getattr(mod, '__path__', []) or [])), '')\n"
        "    out[name] = {'where': where}\n"
        "print('HGPROBE' + json.dumps(out))\n")
    proc = subprocess.run([str(python), "-c", script], capture_output=True, text=True,
                          errors="replace", timeout=300, cwd=str(overlay.parent))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("HGPROBE")), "")
    try:
        found = json.loads(line[len("HGPROBE"):])
    except json.JSONDecodeError:
        return False, (f"{version}: the probe produced no result: "
                       f"{(proc.stderr or proc.stdout).strip()[-500:]}")
    bad: list[str] = []
    for name, got in sorted(found.items()):
        if "error" in got:
            bad.append(f"{name}: {got['error']}")
            continue
        where = got.get("where") or ""
        if not where or not Path(where).resolve().is_relative_to(overlay.resolve()):
            bad.append(f"{name} resolved to {where or '(nowhere)'}, not the overlay")
    if bad:
        return False, f"{version}: " + " | ".join(bad[:6])
    return True, (f"{version}: {len(modules)} module(s) import from the overlay with "
                  f"{python}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", required=True, help="the harness directory")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--dataset", help="build for every Python version this dataset's "
                                      "images use")
    src.add_argument("--python", action="append", default=[], metavar="X.Y",
                     help="build for this version (repeatable)")
    ap.add_argument("--builder-image", help="build against this image's interpreter "
                                           "instead of the host's")
    ap.add_argument("--host-interpreter", default=DEFAULT_HOST_INTERPRETER, metavar="PATH",
                    help=f"the interpreter a `files` run launches (default: the bare name "
                         f"{DEFAULT_HOST_INTERPRETER!r}, because that is the name "
                         f"eval/run/runner.py resolves)")
    ap.add_argument("--list", action="store_true", help="report, build nothing")
    ap.add_argument("--force", action="store_true", help="rebuild an existing overlay")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    harness = Path(args.harness).expanduser().resolve()
    install = runtime.install_spec(harness)
    if install is None:
        manifest = harness / "harness.json"
        why = ("no harness.json" if not manifest.is_file()
               else "its `install` is absent, or is not a pip requirements file")
        print(f"{harness.name}: nothing to configure -- {why}.\n"
              "A `.sh` install is arbitrary code and is baked into an image at import "
              "time instead (§2.5.6); see tools/import_env.py --harness.", file=sys.stderr)
        return 1

    print(f"harness   {harness.name}")
    print(f"install   {install[1]}  sha256 {install[0][:16]}")
    print(f"overlays  {runtime.overlays_root()}")

    targets: dict[str, str | None] = {}     # version -> builder image; None = this host
    host: Path | None = None                # the interpreter a `files` run would launch
    if args.list and not (args.dataset or args.python):
        # `--list` on its own is a question about this machine, not a build request:
        # report what is already built rather than demanding a builder image.
        root = runtime.overlays_root() / install[0][:16]
        built = sorted(p.name for p in root.iterdir()
                       if (p / "PROVENANCE.json").is_file()) if root.is_dir() else []
        if not built:
            print("  (nothing built yet)")
        for version in built:
            path = root / version
            prov = json.loads((path / "PROVENANCE.json").read_text(encoding="utf-8")) \
                if (path / "PROVENANCE.json").is_file() else {}
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            print(f"  {version:6s} {size / 1e6:6.1f} MB  built "
                  f"{prov.get('built_at', '?')} from "
                  f"{str(prov.get('built_from_image', '?')).split('/')[-1]}"
                  f"  ({len(prov.get('packages') or {})} distributions)")
        return 0 if built else 1
    if args.dataset:
        by_version = _python_versions_from_dataset(args.dataset)
        for version, images in sorted(by_version.items()):
            # Every image of the version is a candidate, because they do not agree
            # about whether they can build: one may ship a Python without pip, and the
            # next one may be fine.
            builder = next((i for i in images if _has_pip(i, container.image_python(
                container.resolve_digest(i)))), None)
            if builder is None:
                print(f"  {version:6s} SKIPPED: none of its {len(images)} image(s) can "
                      f"build (no pip); tasks on this version keep the image's "
                      f"interpreter and will be recorded as `declared`")
                continue
            targets[version] = builder
            print(f"  {version:6s} {len(images)} image(s), building from "
                  f"{builder.split('/')[-1]}")
    elif args.python:
        for version in args.python:
            if args.builder_image:
                targets[version] = args.builder_image
                continue
            # No image named, so the host is the builder -- but only for its own
            # version, because the key *is* the version and a guessed key is worse than
            # no overlay: it imports cleanly until it does not.
            candidate = _host_interpreter(args.host_interpreter)
            found = runtime.interpreter_version(str(candidate)) if candidate else None
            if candidate is None or found != version:
                print(f"--python {version} needs --builder-image: "
                      f"{args.host_interpreter!r} resolves to "
                      f"{candidate or '(nothing on PATH)'}"
                      + (f", which is Python {found}" if found else ""),
                      file=sys.stderr)
                return 2
            host = candidate
            targets[version] = None
    else:
        # The host's own interpreter is the default, because it is the one a `files` run
        # launches and the one someone iterating on a harness actually has. An image can
        # still be named, which is what an `exec` overlay needs.
        host = _host_interpreter(args.host_interpreter)
        if host is None:
            print(f"{args.host_interpreter!r} is not on PATH; pass --host-interpreter",
                  file=sys.stderr)
            return 2
        version = runtime.interpreter_version(str(host))
        if not version:
            print(f"could not determine the version of {host}", file=sys.stderr)
            return 2
        image = args.builder_image or os.environ.get("HG_OVERLAY_BUILDER_IMAGE")
        if image:
            targets[version] = image
            print(f"  {version:6s} building with {str(image).split('/')[-1]}")
        else:
            if not _host_has_pip(host):
                print(f"{host} has no pip, so nothing can be installed for it; a `files` "
                      f"harness on this host keeps whatever site-packages it already "
                      f"has", file=sys.stderr)
                return 2
            targets[version] = None
            print(f"  {version:6s} host interpreter {host}")

    if args.list:
        for version in sorted(targets):
            path = runtime.overlay_path(install, version)
            state = "built" if (path / "PROVENANCE.json").is_file() else "missing"
            print(f"  {version:6s} {state:8s} {path}")
        return 0

    results, failed = [], []
    for version, builder in sorted(targets.items()):
        path = runtime.overlay_path(install, version)
        # `None` is "build it here, with the host's pip". Checked rather than assumed,
        # because the key of a host overlay and the interpreter that built it have to be
        # the same thing or the whole key is a lie.
        on_host = builder is None
        if on_host and host is None:
            raise SystemExit(f"{version}: no host interpreter to build with")
        if (path / "PROVENANCE.json").is_file() and not args.force:
            ok, note = (verify_one_host(harness, install, version, host) if on_host
                        else verify_one(harness, install, version, str(builder)))
            print(f"  {version:6s} present, {note}")
            (results if ok else failed).append(
                {"python": version, "reused": True,
                 "built_from": "host" if on_host else "image"})
            continue
        try:
            prov = (build_one_host(harness, install, version, host) if on_host
                    else build_one(harness, install, version, str(builder)))
        except SystemExit as exc:
            print(f"  {version:6s} BUILD FAILED: {exc}", file=sys.stderr)
            failed.append({"python": version, "error": str(exc)[:300]})
            continue
        ok, note = (verify_one_host(harness, install, version, host) if on_host
                    else verify_one(harness, install, version, str(builder)))
        print(f"  {version:6s} built in {prov['seconds']}s, {note}")
        (results if ok else failed).append({"python": version, "provenance": prov})

    print()
    print(f"configured {len(results)} version(s)"
          + (f", {len(failed)} failed" if failed else ""))
    if failed:
        print("A version that failed keeps whatever interpreter the run provides -- the "
              "image's for `exec`, the host's for `files` -- and `env.harness_runtime` "
              "records which.", file=sys.stderr)
    if args.json:
        print(json.dumps({"ok": results, "failed": failed}, indent=1))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
