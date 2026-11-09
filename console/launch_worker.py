"""Detach one driver while retaining its real exit code across Console restarts."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time

if __package__:
    from .assistant_drivers import redact_secrets
else:
    from assistant_drivers import redact_secrets

MAX_LINE = 65536


def main(argv):
    if len(argv) < 4 or argv[2] != '--' or not re.fullmatch(r'console-[A-Za-z0-9._-]{1,180}', argv[1]):
        return 2
    directory, run_id, command = argv[0], argv[1], argv[3:]
    started = time.time()
    secret_parts = [part for name, value in os.environ.items()
                    if any(marker in name.upper() for marker in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))
                    for part in value.splitlines() if part]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert process.stdout is not None
        while True:
            line = process.stdout.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE and not line.endswith(b'\n'):
                # Do not expose partial secrets in a split or arbitrarily long line.
                while line and not line.endswith(b'\n'):
                    line = process.stdout.readline(MAX_LINE + 1)
                print('[oversized log line omitted]', flush=True)
                continue
            text = line.decode('utf-8', errors='replace')
            # API credentials supplied for this launch may not be saved in the Console.
            print(redact_secrets(text, os.environ, secrets=secret_parts, scrub_urls=True), end='', flush=True)
        process.stdout.close()
        exit_code = process.wait()
    except OSError as error:
        print(f'driver could not start: {error}', file=sys.stderr, flush=True)
        exit_code = 127
    receipt = {'schema': 1, 'run_id': run_id, 'exit_code': exit_code,
               'started_at': started, 'finished_at': time.time()}
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = '.exit-' + secrets.token_hex(16)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(receipt, out)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, run_id + '.exit.json', src_dir_fd=root, dst_dir_fd=root)
    finally:
        try:
            os.unlink(temp, dir_fd=root)
        except FileNotFoundError:
            pass
        os.close(root)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
