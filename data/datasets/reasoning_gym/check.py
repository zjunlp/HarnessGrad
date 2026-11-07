"""Use the QA check's score and evidence with the managed exit contract."""
import os
from pathlib import Path
import runpy
import tempfile


if __name__ == "__main__":
    # Scientific libraries must not read caches or configuration left by the actor.
    with tempfile.TemporaryDirectory(prefix="hg-reasoning-gym-check-", dir="/tmp") as directory:
        os.environ.update(HOME=directory, XDG_CACHE_HOME=directory,
                          XDG_CONFIG_HOME=directory, MPLCONFIGDIR=directory)
        try:
            runpy.run_path(str(Path(__file__).with_name("check.py")), run_name="__main__")
        except SystemExit as result:
            # The QA check exits 1 for measured zero and partial credit. The bound
            # verifier reads the reward file and requires exit 0 for any measurement.
            if result.code != 1:
                raise
