"""Cache pinned native benchmark trees directly from their upstream Git repos."""

import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from filelock import FileLock
from huggingface_hub import constants


def _download_git(url, revision, paths):
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Native upstream revision must be an immutable Git commit")
    name = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    cache = Path(constants.HF_HOME) / "flashrsi" / "upstream" / name
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / revision
    full_checkout = paths == ["."]

    def git(root, *args):
        result = subprocess.run(
            [
                "git",
                *(["-c", "core.longpaths=true"] if os.name == "nt" else []),
                "-C",
                str(root),
                *args,
            ],
            capture_output=True,
            timeout=600,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise ValueError(
                f"Cannot load pinned upstream {name}; check Git/network access"
            )
        return result.stdout.decode("utf-8").strip()

    with FileLock(str(destination) + ".lock"):
        if not destination.exists():
            with TemporaryDirectory(prefix=".checkout-", dir=cache) as temporary:
                root = Path(temporary) / "repo"
                root.mkdir()
                git(root, "init", "--quiet")
                git(root, "config", "core.autocrlf", "false")
                git(root, "remote", "add", "origin", url)
                if not full_checkout:
                    git(root, "sparse-checkout", "init", "--cone")
                    git(root, "sparse-checkout", "set", *paths)
                git(root, "fetch", "--depth=1", "origin", revision)
                git(root, "checkout", "--quiet", "--detach", "FETCH_HEAD")
                root.rename(destination)
        if git(destination, "rev-parse", "HEAD") != revision or git(
            destination, "status", "--porcelain", "--untracked-files=all", "--", *paths
        ):
            raise ValueError(f"Pinned upstream {name} cache changed; use a fresh cache")
        if full_checkout:
            # Cone mode's '.' means root files only. Expand old sparse caches
            # after checking they are unchanged; never reset a dirty checkout.
            git(destination, "sparse-checkout", "disable")
    return destination
