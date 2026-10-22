"""Check private host paths against the actor's final ordered bwrap mounts."""
import os
from pathlib import Path


def reject_visible(plan, private_paths):
    """Reject exposure through bind sources, including aliases and projections.

    Evaluate mount destinations in order: a later parent mount hides an earlier
    bind, but a later bind can expose the same source at another destination.
    The input is the platform-generated mount plan, before env/argv are appended.
    """
    mounts = []
    i = 0
    while i < len(plan):
        flag = plan[i]
        if flag in ('--bind', '--ro-bind', '--dev-bind', '--bind-try', '--ro-bind-try'):
            source = Path(plan[i + 1]).resolve()
            target = Path(os.path.abspath(plan[i + 2]))
            mounts.append((source, target))
            i += 3
        elif flag in ('--tmpfs', '--proc', '--dev'):
            mounts.append((None, Path(os.path.abspath(plan[i + 1]))))
            i += 2
        else:
            i += 1
    for private in private_paths:
        private = Path(private).resolve()
        for index, (source, target) in enumerate(mounts):
            if source is None:
                continue
            if private.is_relative_to(source):
                exposed = target / private.relative_to(source)
            elif source.is_relative_to(private):
                exposed = target
            else:
                continue
            if any(exposed.is_relative_to(later_target) for _, later_target in mounts[index + 1:]):
                continue
            raise ValueError(f'trial_evidence archive {private} is visible through sandbox mount {target}')
