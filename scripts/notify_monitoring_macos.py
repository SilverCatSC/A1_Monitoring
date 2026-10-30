"""Observe one host run and emit local notifications only; never drive Chrome."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

from app.service.operator_notice import OperatorNoticeTracker
from app.service.scan_progress import read_scan_progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--started-at', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    # This observer must never keep the scanner's inherited kernel lock alive
    # after the runner exits, including after an uncatchable parent termination.
    raw_fd = os.environ.get('A1_MONITORING_HOST_LOCK_FD', '')
    if raw_fd.isdecimal() and int(raw_fd) >= 3:
        try:
            os.close(int(raw_fd))
        except OSError:
            pass
    if sys.platform != 'darwin':
        return
    tracker = OperatorNoticeTracker(datetime.fromisoformat(args.started_at.replace('Z', '+00:00')))
    while args.once or os.getppid() == args.parent_pid:
        progress = read_scan_progress(args.evidence_dir)
        terminal = progress.get('status') in {'completed', 'partial', 'failed'}
        notice = tracker.update(progress) if args.once == terminal else None
        if notice:
            title, message = notice
            # Pass only the fixed application text as argv, not AppleScript
            # interpolation; never evaluate marketplace content as source.
            try:
                result = subprocess.run([
                    '/usr/bin/osascript', '-e',
                    'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run',
                    title, message,
                ], capture_output=True, timeout=5, check=False)
                if result.returncode:
                    print('OPERATOR_NOTICE unavailable', flush=True)
            except (OSError, subprocess.TimeoutExpired):
                print('OPERATOR_NOTICE unavailable', flush=True)
        if args.once:
            return
        time.sleep(2)


if __name__ == '__main__':
    main()
