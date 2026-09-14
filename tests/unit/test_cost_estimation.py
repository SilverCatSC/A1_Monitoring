import json
from pathlib import Path

import pytest

from tools.estimate_api_cost import estimate

PRICES = Path(__file__).resolve().parents[2] / 'config/costs/api_prices_2026-09-14.json'


@pytest.fixture
def prices():
    return json.loads(PRICES.read_text(encoding='utf-8'))


def test_full_daily_budget_and_currency_rounding(prices):
    rows = estimate(prices, vehicles=40, images=80, runs=1)
    assert rows[0]['input_per_cycle'] == 425_000
    assert rows[0]['output_per_cycle'] == 47_500
    assert rows[0]['month'] == 3269.30
    assert rows[2]['month'] == 15.98  # Exact unrounded month is USD 15.975.
    assert rows[3]['month'] == 19.88


def test_frequency_uses_unrounded_cycle_and_image_sensitivity(prices):
    four = estimate(prices, vehicles=40, images=80, runs=4)
    assert four[0]['month'] == 13077.18
    cheaper_images = estimate(prices, vehicles=40, images=80, runs=1, image_tokens=500)
    assert cheaper_images[0]['input_per_cycle'] == 275_000
    assert cheaper_images[0]['output_per_cycle'] == 47_500
    assert cheaper_images[0]['month'] < four[0]['month'] / 4


def test_text_only_still_includes_global_review(prices):
    row = estimate(prices, vehicles=1, images=0, runs=1)[0]
    assert row['input_per_cycle'] == 27_500
    assert row['output_per_cycle'] == 3000


@pytest.mark.parametrize('vehicles,images,runs,image_tokens', [
    (0, 1, 1, None), (1, -1, 1, None), (1, 1, 0, None), (1, 1, 1, -1),
])
def test_invalid_usage_rejected(prices, vehicles, images, runs, image_tokens):
    with pytest.raises(ValueError):
        estimate(prices, vehicles, images, runs, image_tokens)
