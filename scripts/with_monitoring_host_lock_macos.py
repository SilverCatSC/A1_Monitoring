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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--lock-path', required=True)
    parser.add_argument(
        '--verify-inherited-fd',
        type=int,
        help='Validate/acquire the lock on one already inherited descriptor.',
    )
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.verify_inherited_fd is not None:
        if args.command:
            print('HOST_RUNNER_LOCK_FAILED reason=verification_does_not_accept_command', file=sys.stderr)
            raise SystemExit(64)
        return args
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        print('HOST_RUNNER_LOCK_FAILED reason=command_required', file=sys.stderr)
        raise SystemExit(64)
    args.command = command
    return args


def verify_inherited_lock(lock_path: Path, fd: int) -> int:
    """Verify that ``fd`` names this runner's lock and holds/acquires it.

    The shell runner must never trust an ambient environment variable as proof
    of a lock.  A genuine helper-exec descriptor references the same open file
    description and passes this check.  A forged descriptor either refers to a
    different object or cannot acquire the lock while another runner owns it.
    """
    if fd < 3:
        print('HOST_RUNNER_LOCK_FAILED reason=inherited_fd_invalid', file=sys.stderr)
        return 1
    try:
        expected = lock_path.stat()
        actual = os.fstat(fd)
    except OSError:
        print('HOST_RUNNER_LOCK_FAILED reason=inherited_fd_unavailable', file=sys.stderr)
        return 1
    if (
        not os.path.isfile(lock_path)
        or actual.st_dev != expected.st_dev
        or actual.st_ino != expected.st_ino
    ):
        print('HOST_RUNNER_LOCK_FAILED reason=inherited_fd_mismatch', file=sys.stderr)
        return 1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            print('HOST_RUNNER_LOCK_FAILED reason=inherited_fd_not_locked', file=sys.stderr)
            return 1
        print('HOST_RUNNER_LOCK_FAILED reason=kernel_lock_unavailable', file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = parse_args()
    lock_path = Path(args.lock_path)
    if args.verify_inherited_fd is not None:
        return verify_inherited_lock(lock_path, args.verify_inherited_fd)
    command = args.command
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
