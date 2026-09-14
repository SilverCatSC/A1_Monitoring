"""Non-forgeable-by-configuration checks for the MacBook host child process.

The code does not attempt to defend against an owner deliberately writing a
new native process. It does prevent an ordinary container, API request or
direct CLI configuration from claiming it is the interactive host runner.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HOST_RUNNER_CONTEXT_ENV = 'A1_MONITORING_HOST_RUNNER_CONTEXT'
HOST_LOCK_HELD_ENV = 'A1_MONITORING_HOST_LOCK_HELD'
HOST_LOCK_FD_ENV = 'A1_MONITORING_HOST_LOCK_FD'
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCK_HELPER = PROJECT_ROOT / 'scripts' / 'with_monitoring_host_lock_macos.py'
LOCK_PATH = PROJECT_ROOT / 'artifacts' / '.monitoring_host_runner_macos.lock'


class HostRunnerContextError(RuntimeError):
    """The caller is not the locked child of the macOS host runner."""


def require_verified_macos_host_runner_context() -> None:
    """Require the runner marker and its inherited, kernel-locked descriptor."""
    if sys.platform != 'darwin':
        raise HostRunnerContextError('MacBook interactive host runner is required')
    if os.environ.get(HOST_RUNNER_CONTEXT_ENV) != '1':
        raise HostRunnerContextError('MacBook interactive host runner is required')
    if os.environ.get(HOST_LOCK_HELD_ENV) != '1':
        raise HostRunnerContextError('verified MacBook host lock is required')
    raw_fd = os.environ.get(HOST_LOCK_FD_ENV, '')
    if not raw_fd.isdecimal() or int(raw_fd) < 3:
        raise HostRunnerContextError('verified MacBook host lock is required')
    lock_fd = int(raw_fd)
    try:
        verification = subprocess.run(
            [
                sys.executable,
                str(LOCK_HELPER),
                '--lock-path',
                str(LOCK_PATH),
                '--verify-inherited-fd',
                str(lock_fd),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(lock_fd,),
        )
    except OSError as exc:
        raise HostRunnerContextError('verified MacBook host lock is required') from exc
    if verification.returncode != 0:
        raise HostRunnerContextError('verified MacBook host lock is required')
