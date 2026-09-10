#!/usr/bin/env python3
"""Validate and narrowly normalize a local Hermes monitoring review."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

VERDICTS = {"ok", "review_required", "technical_failure"}
SEVERITIES = {"low", "medium", "high"}
SOURCES = {"auto_ru", "avito", "a1_site", "head_table_audit", "system"}
VIN_PATTERN = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')
VIN_TOKEN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _close_trailing_containers(text: str) -> str:
    """Repair only valid JSON prefixes missing closing braces/brackets at EOF."""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "}]":
            if not stack or stack.pop() != char:
                return text
    if in_string:
        return text
    return text + "".join(reversed(stack))


def parse_review(text: str, allowed_vehicle_keys: set[str] | None = None) -> dict[str, Any]:
    normalized = _strip_markdown_fence(text)
    try:
        data = json.loads(normalized)
    except json.JSONDecodeError as error:
        if error.pos < len(normalized) - 1:
            raise
        data = json.loads(_close_trailing_containers(normalized))
    validate_review(data, allowed_vehicle_keys=allowed_vehicle_keys)
    return data


def validate_review(data: object, allowed_vehicle_keys: set[str] | None = None) -> None:
    if not isinstance(data, dict):
        raise ValueError("review must be a JSON object")
    if data.get("verdict") not in VERDICTS:
        raise ValueError("invalid verdict")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise ValueError("summary must be a non-empty string")
    if len(data["summary"]) > 300:
        raise ValueError("summary must not exceed 300 characters")
    issues = data.get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    if len(issues) > 5:
        raise ValueError("issues must not contain more than 5 entries")
    reported_vins = {
        issue.get('vin')
        for issue in issues
        if isinstance(issue, dict) and isinstance(issue.get('vin'), str)
    }
    summary_vins = set(VIN_TOKEN_PATTERN.findall(data['summary'].upper()))
    if not summary_vins.issubset(reported_vins):
        raise ValueError('summary names a VIN that is absent from structured issues')
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError("each issue must be an object")
        if issue.get("severity") not in SEVERITIES:
            raise ValueError("invalid issue severity")
        if issue.get("source") not in SOURCES:
            raise ValueError("invalid issue source")
        if 'vehicle_key' not in issue:
            raise ValueError('issue vehicle_key is required')
        vehicle_key = issue.get('vehicle_key')
        if vehicle_key is not None:
            if not isinstance(vehicle_key, str) or not vehicle_key.strip():
                raise ValueError('invalid issue vehicle_key')
            if allowed_vehicle_keys is not None and vehicle_key not in allowed_vehicle_keys:
                raise ValueError('issue vehicle_key is absent from the evidence packet')
        elif issue.get('source') != 'system':
            raise ValueError('marketplace issue must name an evidenced vehicle')
        if 'vin' not in issue:
            raise ValueError('issue vin is required')
        vin = issue.get('vin')
        if vin is not None:
            if not isinstance(vin, str) or not VIN_PATTERN.fullmatch(vin):
                raise ValueError('invalid issue vin')
        for key in ("evidence", "recommendation"):
            if not isinstance(issue.get(key), str) or not issue[key].strip():
                raise ValueError(f"issue {key} must be a non-empty string")
            if len(issue[key]) > 300:
                raise ValueError(f"issue {key} must not exceed 300 characters")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path)
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument('--allowed-vehicles-from', type=Path)
    args = parser.parse_args()
    allowed_vehicle_keys = None
    if args.allowed_vehicles_from:
        packet = json.loads(args.allowed_vehicles_from.read_text(encoding='utf-8'))
        audit = packet.get('head_table_audit', {})
        company_site_audit = packet.get('company_site_audit', {})
        allowed_vehicle_keys = {
            str(row['vehicle_key'])
            for row in [
                *audit.get('issues', []),
                *audit.get('content_samples', []),
                *company_site_audit.get('issues', []),
            ]
            if isinstance(row, dict) and row.get('vehicle_key')
        }
    review = parse_review(
        args.review.read_text(encoding="utf-8"), allowed_vehicle_keys=allowed_vehicle_keys
    )
    if args.normalize:
        args.review.write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
