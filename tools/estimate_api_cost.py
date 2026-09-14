"""Offline budget calculator. Reads public rates only; never calls an LLM API."""
from __future__ import annotations

import argparse
import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def estimate(config: dict, vehicles: int, images: int, runs: int, image_tokens: int | None = None) -> list[dict]:
    if vehicles < 1 or images < 0 or runs < 1 or (image_tokens is not None and image_tokens < 0):
        raise ValueError('vehicles/runs must be positive, images/image tokens nonnegative')
    a = config['assumptions']
    visual = a['image_tokens_per_image'] if image_tokens is None else image_tokens
    input_tokens = math.ceil((vehicles * a['data_input_per_vehicle']
                             + images * (a['vision_text_input_per_image'] + visual)
                             + a['global_input_per_cycle']) * a['retry_overhead_factor'])
    output_tokens = math.ceil((vehicles * a['data_output_per_vehicle']
                              + images * a['vision_output_per_image']
                              + a['global_output_per_cycle']) * a['retry_overhead_factor'])
    result = []
    for provider in config['providers']:
        cost = (input_tokens * Decimal(str(provider['input']))
                + output_tokens * Decimal(str(provider['output']))) / Decimal(1_000_000)
        monthly = cost * runs * a['days_per_month']
        result.append({'provider': provider['name'], 'currency': provider['currency'],
                       'input_per_cycle': input_tokens, 'output_per_cycle': output_tokens,
                       'cycle': float(cost.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)),
                       'month': float(monthly.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                       'source': provider['source']})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vehicles', type=int, default=40)
    parser.add_argument('--images', type=int, default=80)
    parser.add_argument('--runs-per-day', type=int, default=1)
    parser.add_argument('--image-tokens', type=int)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config/costs/api_prices_2026-09-14.json').read_text(encoding='utf-8'))
    try:
        rows = estimate(config, args.vehicles, args.images, args.runs_per_day, args.image_tokens)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f'Estimated usage, rates checked {config["checked_at"]}; no API requests.')
    print(f'Input/cycle={rows[0]["input_per_cycle"]}, output/cycle={rows[0]["output_per_cycle"]}')
    print('| Provider | Currency | Per cycle | Per 30 days |')
    print('| --- | --- | ---: | ---: |')
    for row in rows:
        print(f'| {row["provider"]} | {row["currency"]} | {row["cycle"]:.4f} | {row["month"]:.2f} |')


if __name__ == '__main__':
    main()
