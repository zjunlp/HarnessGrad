"""Platform-specific handle operations for the Codex surface reader."""
from __future__ import annotations

import os

if os.name == 'nt':
    from harnessgrad.personal.adapters.windows_fs import (
        close_directory, directory_names, open_directory, open_file, stat_directory, stat_file,
    )

    def available() -> bool:
        return True
else:
    close_directory = os.close
    stat_directory = stat_file = os.fstat

    def available() -> bool:
        return (all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK'))
                and os.open in os.supports_dir_fd and os.scandir in os.supports_fd)

    def open_directory(name: str, parent: int | None = None) -> int:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=parent)

    def open_file(name: str, parent: int) -> int:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)

    def directory_names(handle: int):
        with os.scandir(handle) as entries:
            yield from (row.name for row in entries)
