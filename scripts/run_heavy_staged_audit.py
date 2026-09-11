#!/usr/bin/env python3
"""Run the Windows heavy-model fallback for a validated staged review."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ai_work_units import _extract_json  # noqa: E402
from scripts.validate_staged_review import validate_staged  # noqa: E402


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    verdicts = Counter()
    for report in data.get('unit_reports', []):
        if not isinstance(report, dict):
            continue
        unit_data = report.get('data')
        if isinstance(unit_data, dict):
            verdicts[f"data:{unit_data.get('verdict', 'unknown')}"] += 1
        for vision in report.get('vision', []):
            if isinstance(vision, dict):
                verdicts[f"vision:{vision.get('verdict', 'unknown')}"] += 1
    failure_counts = Counter(
        str(item.get('stage') or 'unknown')
        for item in data.get('failures', [])
        if isinstance(item, dict)
    )
    return {
        'coverage': data.get('coverage', {}),
        'verdict_counts': dict(verdicts),
        'failure_counts': dict(failure_counts),
        'review': data.get('hermes_review', {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('review', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--reuse-raw', type=Path)
    args = parser.parse_args()
    data = json.loads(args.review.read_text(encoding='utf-8'))
    validate_staged(data)
    compact = _compact(data)
    prompt = '''Ты — независимый тяжёлый аудитор A1 Monitoring. Проверь только компактный
валидированный отчёт ниже: покрытие, технические сбои, непротиворечивость итогового verdict
и достаточность доказательств. Partial и technical_failure не доказывают отсутствие объявления.
Не предлагай автоматическое изменение данных или кода. Верни только JSON:
{"verdict":"pass|review_required|technical_failure","score":0.0,"summary":"до 500 символов","concerns":["до 5 пунктов"]}
ОТЧЁТ:\n''' + json.dumps(compact, ensure_ascii=False, separators=(',', ':'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = args.output.with_suffix('.prompt.txt')
    raw_path = args.reuse_raw or args.output.with_suffix('.raw.txt')
    prompt_path.write_text(prompt, encoding='utf-8')
    env = os.environ.copy()
    env['A1_AI_PROFILE'] = 'heavy'
    helper = ROOT / 'scripts' / 'hermes_monitoring_oneshot_windows.cmd'
    if not args.reuse_raw:
        result = subprocess.run(
            [str(helper), '--prompt', str(prompt_path), '--output', str(raw_path), '--reasoning', 'low'],
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f'Heavy local audit exited with {result.returncode}')
    report = _extract_json(raw_path.read_text(encoding='utf-8'))
    if report.get('verdict') not in {'pass', 'review_required', 'technical_failure'}:
        raise ValueError('Heavy local audit returned an invalid verdict')
    report['engine'] = 'qwen3.5-9b-windows-fallback'
    report['validated_input'] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'HEAVY_STAGED_AUDIT_READY {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
