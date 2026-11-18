"""Removed entrypoint guard: use :mod:`harnessgrad.experiment.splits`."""

if __name__ == "__main__":
    raise SystemExit('harnessgrad.splits was removed; use python -m harnessgrad.experiment.splits or harnessgrad split')
raise ImportError('harnessgrad.splits was removed; import harnessgrad.experiment.splits instead')
