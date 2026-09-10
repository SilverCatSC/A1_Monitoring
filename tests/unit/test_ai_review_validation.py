import json

import pytest

from scripts.validate_ai_review import parse_review

VALID = {
    "verdict": "ok",
    "summary": "No aggregate contradictions.",
    "issues": [],
}


def test_valid_review_is_accepted() -> None:
    assert parse_review(json.dumps(VALID)) == VALID


def test_markdown_fence_is_removed() -> None:
    assert parse_review(f"```json\n{json.dumps(VALID)}\n```") == VALID


def test_missing_trailing_brace_is_narrowly_repaired() -> None:
    payload = json.dumps(VALID)
    assert parse_review(payload[:-1]) == VALID


def test_non_trailing_json_damage_is_rejected() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_review('{"verdict" "ok", "summary": "x", "issues": []}')


def test_issue_schema_is_enforced() -> None:
    invalid = VALID | {
        "issues": [
            {
                "severity": "urgent",
                "source": "system",
                "vehicle_key": None,
                "vin": None,
                "evidence": "x",
                "recommendation": "y",
            }
        ]
    }
    with pytest.raises(ValueError, match="severity"):
        parse_review(json.dumps(invalid))


def test_marketplace_issue_requires_an_evidenced_vehicle_key() -> None:
    invalid = VALID | {
        "issues": [
            {
                "severity": "medium",
                "source": "avito",
                "vehicle_key": None,
                "vin": None,
                "evidence": "Card price differs.",
                "recommendation": "Check the card.",
            }
        ]
    }
    with pytest.raises(ValueError, match='vehicle'):
        parse_review(json.dumps(invalid))


def test_issue_vehicle_key_must_belong_to_packet() -> None:
    issue = {
        "severity": "medium",
        "source": "auto_ru",
        "vehicle_key": "vin:W1VVNLSZXS4493307",
        "vin": "W1VVNLSZXS4493307",
        "evidence": "The saved card has a different price.",
        "recommendation": "Check the price in the card.",
    }
    with pytest.raises(ValueError, match='absent'):
        parse_review(json.dumps(VALID | {'issues': [issue]}), allowed_vehicle_keys=set())


def test_summary_cannot_name_a_vin_without_a_structured_issue() -> None:
    invalid = VALID | {'summary': 'Проверить W1VVNLSZXS4493307.'}

    with pytest.raises(ValueError, match='summary names a VIN'):
        parse_review(json.dumps(invalid))


def test_head_table_audit_is_a_supported_evidence_source() -> None:
    review = VALID | {
        'issues': [{
            'severity': 'medium',
            'source': 'head_table_audit',
            'vehicle_key': 'a1_site:/cars-for-sale/v-vip_11_07.html',
            'vin': None,
            'evidence': 'The active record is not mapped to the catalogue.',
            'recommendation': 'Check the expected publication.',
        }]
    }

    assert parse_review(json.dumps(review)) == review


def test_text_limits_are_enforced() -> None:
    with pytest.raises(ValueError, match="summary"):
        parse_review(json.dumps(VALID | {"summary": "x" * 301}))
