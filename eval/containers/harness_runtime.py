"""A harness's own Python dependencies, provided from **outside** the task's image.

The problem this solves
-----------------------
A harness is arbitrary code the platform did not write, and it is written in Python
often enough that the platform invokes `python <entry>` by contract. Its dependencies
therefore have to be importable by whatever interpreter runs it -- and the two obvious
answers both fail somewhere:

* **the image's interpreter**, which §2.5.3 prefers, because "`bash`, the file editor
  and the interpreter are the image's". Measured on Terminal-Bench: 60 of the 86 local
  task images ship their own `/usr/local/bin/python3` and **none of them has `openai`**,
  so `loop` died on its first model call with `ModuleNotFoundError`, exit code 1, and a
  0-byte trace.
* **the platform's interpreter**, mounted in, which works but throws away §2.5.3 and
  weakens the one test that catches "the platform quietly ran it on the host".

And there is no third place: the harness tree is read-only during a run, so an install
has nowhere to write (§2.5.6).

What this module does instead
-----------------------------
It keeps the **image's** interpreter and adds a read-only directory to the harness
process's `sys.path`. The directory holds only the harness's declared dependencies,
resolved **for that interpreter's Python version**, and it is built once per
`(install contents, Python minor version)` -- not per task, and not per image.

The version key is not optional. Measured: a `3.13` overlay imported cleanly on four
different 3.13 task images and failed on four different 3.12 images with
`ModuleNotFoundError`, which is the loud failure we want. Sharing one overlay across
versions would be silent corruption; `jiter` and `pydantic_core` ship compiled
extensions and the ABI is per-version.

Why this and not baking into derived images
-------------------------------------------
Baking is `O(#task images)` per harness -- 89 derived images for Terminal-Bench -- so
anyone arriving with their own harness pays that before their first run. This is
`O(#Python versions in the dataset)`, which is 5 here (3.13 x41, 3.12 x13, 3.11 x3,
3.10 x1, 3.9 x2, no interpreter x26) and does not move when the task set grows.

What it costs, stated rather than hidden
----------------------------------------
`PYTHONPATH` is **inherited by the harness's subprocesses**, so a `python3 foo.py` the
harness runs also sees these packages. Measured harmless on Terminal-Bench -- the
closure overlaps no task's installs and no task's tests or solution imports any of
them -- but it is a mechanism, not a proof, so `record()` puts the fact on every curve
point. And the overlay is a **bind mount**, which `docker commit` does not include:
measured, the mount target exists in the snapshot and is empty, so the graded container
never sees these packages.

Lookup only
-----------
Nothing here builds or pulls. §2.5.5 puts both behind a human at import time, and this
module is on the run path. Building is `tools/configure_harness.py`; if no overlay
exists this returns `None` and the run proceeds exactly as it did before, which is why
installing this feature changes nothing for anyone who does not use it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import eval.containers.container as container
from harnessgrad.config import current as current_config

#: `install` names a pip requirements file or a shell script (§2.5.6). Only the former
#: can become a directory of importable packages; a `.sh` is arbitrary code and is
#: baked into an image at import time instead, so it is refused here rather than
#: guessed at.
PIP_SUFFIXES = (".txt",)

#: In-process cache: one probe per image per run, not one per task.
_VERSION_CACHE: dict[str, str | None] = {}

#: Files type/size guard: a `requirements.txt` this large is not one.
MAX_INSTALL_BYTES = 64 * 1024


def overlays_root() -> Path:
    """Where overlays live. Deliberately outside the platform tree.

    `eval/run/integrity.py` hashes `tools`, `eval`, `data`, `methods`, `docs` and the rest
    around every method call and every evaluation; generated artifacts must not land in
    a path that a run is checking for tampering. It is also outside any work root, so
    it survives the per-task `rmtree` and can be shared by every run on the machine.
    """
    configured = current_config().runtime.overlay_root
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) \
        / "harnessgrad" / "overlays"


def install_spec(repo: Path) -> tuple[str, str] | None:
    """`(sha256 of the install file, its name)`, or None if there is not one.

    Keyed on the file's **contents** rather than the harness's name or sha, so two
    harnesses that declare the same dependencies share one overlay, and editing a pin
    produces a different key instead of silently reusing a directory built for the old
    pins.
    """
    manifest_path = Path(repo) / "harness.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    name = manifest.get("install")
    if not name:
        return None
    path = Path(repo) / name
    if not path.is_file() or path.stat().st_size > MAX_INSTALL_BYTES:
        return None
    if not str(name).endswith(PIP_SUFFIXES):
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, str(name)


def _cached_version(key: str, probe) -> str | None:
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    try:
        value = probe()
    except Exception:                                          # noqa: BLE001
        value = None
    _VERSION_CACHE[key] = value
    return value


def interpreter_version(python: str, *, image: str | None = None,
                        image_digest: str | None = None) -> str | None:
    """The `X.Y` the harness's interpreter reports, or None if it cannot be asked.

    Cached twice: in this process, and -- when the interpreter is an image's -- on disk
    under the overlay root keyed by the image digest, because the probe starts a
    container and a 52-task run over 89 images would otherwise pay for it every round.
    The on-disk cache is why the second run over a dataset costs nothing.
    """
    expr = "import sys;print('%d.%d' % sys.version_info[:2])"
    if image is not None:
        disk = None
        if image_digest:
            disk = overlays_root() / "interpreters" / f"{image_digest.replace(':', '_')}.txt"
            try:
                found = disk.read_text(encoding="utf-8").strip()
                if found:
                    return found
            except OSError:
                pass

        def probe_image() -> str | None:
            proc = subprocess.run(
                ["docker", "run", "--rm", "--network", "none", "--entrypoint", python,
                 image, "-c", expr],
                capture_output=True, text=True, errors="replace", timeout=180)
            out = proc.stdout.strip()
            return out.splitlines()[-1].strip() if out else None

        value = _cached_version(f"image:{image_digest or image}", probe_image)
        if value and disk is not None:
            try:
                disk.parent.mkdir(parents=True, exist_ok=True)
                disk.write_text(value + "\n", encoding="utf-8")
            except OSError:
                pass
        return value

    def probe_host() -> str | None:
        proc = subprocess.run([python, "-c", expr], capture_output=True, text=True,
                              timeout=120)
        out = proc.stdout.strip()
        return out.splitlines()[-1].strip() if out else None

    return _cached_version(f"host:{python}", probe_host)


def overlay_path(install: tuple[str, str], version: str) -> Path:
    return overlays_root() / install[0][:16] / version


def _read_provenance(path: Path) -> dict:
    try:
        return json.loads((path / "PROVENANCE.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def lookup(repo: Path, python: str, *, image: str | None = None,
           image_digest: str | None = None) -> dict | None:
    """The overlay for this harness on this interpreter, or None.

    Returning None is the normal case and it is not an error: it means nobody has run
    `tools/configure_harness.py` for this harness, and the run then behaves exactly as
    it did before this feature existed.

    **The probe is skipped unless an overlay exists for this install at all.** A user
    who never configures a harness pays nothing -- not even the version probe, which
    would otherwise be one extra container per image on every run.
    """
    install = install_spec(repo)
    if install is None:
        return None
    root = overlays_root() / install[0][:16]
    try:
        # A *finished* overlay, not merely a directory: an interrupted build leaves the
        # directory behind, and treating that as "configured" would cost a container
        # start per image to look up something that is not there.
        if not root.is_dir() or not any((p / "PROVENANCE.json").is_file()
                                        for p in root.iterdir()):
            return None
    except OSError:
        return None

    version = interpreter_version(python, image=image, image_digest=image_digest)
    if not version:
        return None
    path = overlay_path(install, version)
    if not (path / "PROVENANCE.json").is_file():
        return None
    return {"path": path, "python": version, "install": install[1],
            "install_sha256": install[0], "provenance": _read_provenance(path)}


def env_for(overlay: dict | None) -> dict:
    """The environment a harness needs so its own interpreter finds the overlay.

    Prepended to an existing `PYTHONPATH` rather than replacing it, because a harness
    that sets its own is not the platform's to overwrite.
    """
    if not overlay:
        return {}
    existing = os.environ.get("PYTHONPATH") or ""
    parts = [str(overlay["path"])] + ([existing] if existing else [])
    return {"PYTHONPATH": os.pathsep.join(parts)}


def extra_paths(overlay: dict | None) -> tuple[Path, ...]:
    """The read-only bind the sandbox and the container both need.

    One function for both chains because they disagree about almost everything else --
    `files` runs on the host inside a bwrap namespace, `exec` runs inside an image -- but
    they agree on this: a directory the harness may read and must not write.
    """
    return (Path(overlay["path"]),) if overlay else ()


def record(overlay: dict | None, *, platform_interpreter: bool = False) -> dict:
    """The `env.harness_runtime` block for a curve point.

    Exactly three shapes, and the third is the one that used to be invisible:

    * `{"source": "overlay", ...}` -- the image's interpreter ran the harness and its
      dependencies came from a directory outside the task's environment.
    * `{"source": "platform", ...}` -- the harness ran on an interpreter the **platform**
      provided, which is §2.5.9's case for an image that has no interpreter of its own.
      That interpreter's packages are what supply the dependencies, so this is the one
      situation where a missing overlay is not a problem at all.
    * `{"source": "declared", "reason": ...}` -- the harness declares dependencies, none
      were provided, and the interpreter is the **image's**. There is no fallback here:
      the platform's interpreter is deliberately not mounted when the image has one, so
      a missing overlay on such an image means the harness cannot import what it needs.
      `loop` on Terminal-Bench was this shape, and the only thing the record said was
      `score 0.000`.

    Hence `platform_interpreter` rather than a vague `fell_back`: the distinction is not
    "did we fall back" but "whose interpreter is running", and only the caller knows.
    """
    if overlay:
        prov = overlay.get("provenance") or {}
        return {"source": "overlay", "python": overlay["python"],
                "install": overlay["install"], "install_sha256": overlay["install_sha256"],
                "path": str(overlay["path"]),
                "built_from_image": prov.get("built_from_image"),
                "packages": prov.get("packages") or {}}
    if platform_interpreter:
        return {"source": "platform", "python": None,
                "note": "the platform's interpreter supplies the dependencies"}
    return {"source": "declared", "python": None,
            "reason": "the harness declares dependencies and no overlay is configured "
                      "for this interpreter's Python version",
            "remedy": "python3 tools/configure_harness.py --harness <dir>"}


def main() -> int:                                             # pragma: no cover
    """Print what a harness resolves to here. Diagnostic, not on the run path."""
    if len(sys.argv) < 2:
        print(__doc__.splitlines()[0])
        return 2
    repo = Path(sys.argv[1]).expanduser().resolve()
    install = install_spec(repo)
    print(f"install: {install[1] if install else '(none declared)'}")
    if install:
        print(f"sha256:  {install[0]}")
        root = overlays_root() / install[0][:16]
        versions = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        print(f"overlays: {', '.join(versions) or '(none built)'}  under {root}")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
