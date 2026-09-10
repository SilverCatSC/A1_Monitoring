#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / 'artifacts' / 'hermes_agent'


def main() -> int:
    home = ROOT / 'artifacts' / 'hermes_maintenance_home'
    if '--max-turns' in sys.argv[1:]:
        home = ROOT / 'artifacts' / 'hermes_ouroboros_llm_home'
    env = os.environ.copy()
    env.update({
        'HERMES_HOME': str(home),
        'OUROBOROS_TELEMETRY': '0',
        'DO_NOT_TRACK': '1',
        'NO_PROXY': '127.0.0.1,localhost',
        'no_proxy': '127.0.0.1,localhost',
    })
    return subprocess.run(
        [str(INSTALL / 'venv' / 'Scripts' / 'python.exe'), str(INSTALL / 'hermes'), *sys.argv[1:]],
        env=env,
        check=False,
    ).returncode


if __name__ == '__main__':
    raise SystemExit(main())
