#!/usr/bin/env python3
"""Hold the macOS host-runner lock across exec without a stale PID file.

This wrapper deliberately has one job: acquire a non-blocking kernel fcntl
lock and exec the interactive shell runner with that file descriptor inherited.
The operating system releases the lock when the last holder exits, including
after an unexpected process death. It never deletes or guesses about a stale
lock based on a PID or timestamp.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import sys
from pathlib import Path


def parse_args() -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--lock-path', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        print('HOST_RUNNER_LOCK_FAILED reason=command_required', file=sys.stderr)
        raise SystemExit(64)
    return Path(args.lock_path), command


def main() -> int:
    lock_path, command = parse_args()
    try:
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
    except OSError:
        print('HOST_RUNNER_LOCK_FAILED reason=lock_path_unavailable', file=sys.stderr)
        return 1

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                print('HOST_RUNNER_SKIPPED_ACTIVE')
                return 0
            print('HOST_RUNNER_LOCK_FAILED reason=kernel_lock_unavailable', file=sys.stderr)
            return 1

        # Python opens descriptors as close-on-exec by default. Clear that bit
        # so the shell runner itself owns the same kernel lock after exec.
        os.set_inheritable(fd, True)
        environment = os.environ.copy()
        environment['A1_MONITORING_HOST_LOCK_HELD'] = '1'
        environment['A1_MONITORING_HOST_LOCK_FD'] = str(fd)
        os.execvpe(command[0], command, environment)
    except OSError:
        print('HOST_RUNNER_LOCK_FAILED reason=runner_exec_failed', file=sys.stderr)
        return 1
    finally:
        # Reached only when exec fails or a handled error occurs. Successful
        # exec hands the inherited descriptor to the shell runner instead.
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
