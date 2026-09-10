#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def lock_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (ROOT / 'config' / 'ai-tools.lock').read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--image', default='')
    parser.add_argument('--reasoning', default='low')
    args = parser.parse_args()
    lock = lock_values()
    install = ROOT / 'artifacts' / 'hermes_agent'
    runner = install / 'venv' / 'Scripts' / 'python.exe'
    env = os.environ.copy()
    env.update({
        'HERMES_HOME': str(ROOT / 'artifacts' / 'hermes_home'),
        'HERMES_VISION_MAX_CONCURRENCY': '1',
        'NO_PROXY': '127.0.0.1,localhost',
        'no_proxy': '127.0.0.1,localhost',
    })
    command = [str(runner), str(install / 'hermes'), 'chat', '--query-file', args.prompt,
               '--oneshot', '-Q', '--provider', 'custom:a1-local', '--model', lock['AI_MODEL_FILE'],
               '--reasoning', args.reasoning, '--max-turns', '1', '--run-budget', '900',
               '--ignore-rules', '--source', 'tool']
    if args.image:
        command.extend(['--image', args.image])
    with Path(args.output).open('w', encoding='utf-8') as output:
        return subprocess.run(command, env=env, stdout=output, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
