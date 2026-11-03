"""A bounded shell call in the native MiniAgent's task environment."""

import asyncio
import json
import math
import os
import signal
from pathlib import Path

_OUTPUT_LIMIT = 32 * 1024


async def call(args, ctx) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    timeout = args.get("timeout", 60)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    cwd = args.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd must be a non-empty path")
    directory = (ctx.working_dir / Path(cwd)).resolve(strict=True)

    loop = asyncio.get_running_loop()
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    output = bytearray()
    truncated = False

    def read_available():
        nonlocal truncated
        # One bounded read per callback lets deadlines run even for commands
        # producing output continuously. Discard overflow without buffering it.
        try:
            chunk = os.read(read_fd, 64 * 1024)
        except BlockingIOError:
            return
        if not chunk:
            loop.remove_reader(read_fd)
            return
        remaining = _OUTPUT_LIMIT - len(output)
        output.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining

    process = None
    spawning = None
    completed = False
    timed_out = False
    try:
        loop.add_reader(read_fd, read_available)
        # Own the pipe separately: Process.wait() must observe the shell's exit,
        # not wait for EOF from a background service that inherited stdout.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                command,
                cwd=directory,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=write_fd,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        )
        # Cancellation may arrive after the OS process exists but before its
        # handle is returned. Retain acquisition so finally can stop its group.
        process = await asyncio.shield(spawning)
        os.close(write_fd)
        write_fd = None
        await asyncio.wait_for(process.wait(), timeout=timeout)
        completed = True
    except TimeoutError:
        timed_out = True
    finally:
        try:
            if process is None and spawning is not None:
                process = await spawning
            if process is not None:
                # Successful commands may leave task services running. Their
                # lifetime belongs to the caller or the surrounding environment.
                if not completed:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await process.wait()
        finally:
            loop.remove_reader(read_fd)
            # Capture any final pending bytes, then close our pipe even if a
            # detached child still holds stdout. No EOF wait or transport leaks.
            read_available()
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    return json.dumps(
        {
            "exit_code": process.returncode,
            "output": output.decode("utf-8", errors="replace"),
            "timed_out": timed_out,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
