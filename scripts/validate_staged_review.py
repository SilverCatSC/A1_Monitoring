#!/usr/bin/env python3
"""Validate staged AI coverage and exact finding provenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_ai_review import validate_review  # noqa: E402


def validate_staged(data: object, expected_cycle_id: str | None = None) -> None:
    if not isinstance(data, dict) or data.get('mode') != 'sequential_multimodal':
        raise ValueError('invalid staged review mode')
    coverage = data.get('coverage')
    reports = data.get('unit_reports')
    failures = data.get('failures')
    final = data.get('hermes_review')
    if not isinstance(coverage, dict) or not isinstance(reports, list) or not isinstance(failures, list):
        raise ValueError('staged review sections are missing')
    if expected_cycle_id is not None and data.get('cycle_id') != expected_cycle_id:
        raise ValueError('staged review does not belong to the requested cycle_id')
    if coverage.get('units_completed') != len(reports):
        raise ValueError('unit coverage does not match unit_reports')
    vision_reports = [
        vision
        for report in reports
        if isinstance(report, dict)
        for vision in report.get('vision', [])
        if isinstance(vision, dict)
    ]
    if coverage.get('vision_expected') != len(vision_reports):
        raise ValueError('vision coverage does not match unit_reports')
    if coverage.get('technical_failures') != len(failures):
        raise ValueError('technical failure count does not match failures')

    allowed_keys = {
        str(report.get('vehicle_key'))
        for report in reports
        if isinstance(report, dict) and report.get('vehicle_key')
    }
    validate_review(final, allowed_vehicle_keys=allowed_keys)
    evidenced = set()
    for report in reports:
        if not isinstance(report, dict):
            continue
        vehicle_key = report.get('vehicle_key')
        blocks = [report.get('data'), *report.get('vision', [])]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for finding in block.get('findings', []):
                if isinstance(finding, dict):
                    evidenced.add((
                        finding.get('severity'),
                        finding.get('source'),
                        vehicle_key,
                        finding.get('evidence'),
                        finding.get('recommendation'),
                    ))
    for issue in final.get('issues', []):
        signature = (
            issue.get('severity'),
            issue.get('source'),
            issue.get('vehicle_key'),
            issue.get('evidence'),
            issue.get('recommendation'),
        )
        if issue.get('source') != 'system' and signature not in evidenced:
            raise ValueError('final issue has no exact staged finding provenance')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('review', type=Path)
    parser.add_argument('--cycle-id', default=None)
    args = parser.parse_args()
    data: Any = json.loads(args.review.read_text(encoding='utf-8'))
    validate_staged(data, expected_cycle_id=args.cycle_id)
    print(f'STAGED_REVIEW_VALID file={args.review}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
