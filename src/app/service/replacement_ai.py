"""Local multimodal Hermes review for fresh seller replacement candidates."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import settings


def _evidence_content(value: Any) -> dict[str, Any] | None:
    name = Path(str(value or '')).name
    if not name:
        return None
    root = Path(settings.evidence_dir).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or 'image/png'
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}}


def _json_object(value: str) -> dict[str, Any]:
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('Hermes replacement response has no JSON object')
    result = json.loads(value[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError('Hermes replacement response is not an object')
    return result


class HermesReplacementMatcher:
    def __init__(self, base_url: str = 'http://127.0.0.1:18080') -> None:
        self.base_url = base_url.rstrip('/')

    def __call__(self, listing, source, reference: dict, candidate: dict, inspection: dict) -> dict:
        old_image = _evidence_content(reference.get('evidence'))
        candidate_image = _evidence_content(inspection.get('evidence'))
        if old_image is None or candidate_image is None:
            return {
                'verdict': 'insufficient_evidence',
                'confidence': 0.0,
                'reason': 'Нет двух сохранённых снимков для визуального сравнения',
            }
        facts = {
            'expected': {
                'brand': listing.brand,
                'model': listing.model,
                'generation': listing.generation,
                'year': listing.year,
                'price': listing.price_hint,
                'vin': listing.vin,
                'notes': listing.notes,
                'old_url': reference.get('url'),
                'old_card': reference.get('card'),
            },
            'candidate': {
                'source': source.value,
                'url': candidate.get('url'),
                'title': candidate.get('title'),
                'price': candidate.get('price'),
                'card_text': candidate.get('card_text'),
                'direct_card': inspection.get('card'),
            },
        }
        prompt = (
            'Ты Hermes. Определи, является ли новая карточка перевыкладкой именно того же '
            'автомобиля, что на старой карточке. Первое изображение — старая карточка, второе — '
            'кандидат. Сравни автомобиль, цвет, кузов, отделку, заметные детали, год, цену и текст. '
            'Одинаковая модель или стоковые фото сами по себе не доказывают совпадение. При '
            'противоречии или недостатке индивидуальных признаков верни uncertain. Верни только JSON: '
            '{"verdict":"same|different|uncertain","confidence":0.0,'
            '"matching_signals":["до 4 фактов"],"conflicts":["до 4 фактов"],'
            '"reason":"до 300 символов"}. ДАННЫЕ:\n'
            + json.dumps(facts, ensure_ascii=False, separators=(',', ':'))
        )
        content = [
            {'type': 'text', 'text': prompt + '\nИЗОБРАЖЕНИЕ 1: старая карточка'},
            old_image,
            {'type': 'text', 'text': 'ИЗОБРАЖЕНИЕ 2: свежий кандидат из каталога продавца'},
            candidate_image,
        ]
        try:
            with httpx.Client(timeout=180) as client:
                models = client.get(f'{self.base_url}/v1/models').raise_for_status().json()
                model = models['data'][0]['id']
                response = client.post(
                    f'{self.base_url}/v1/chat/completions',
                    json={
                        'model': model,
                        'messages': [{'role': 'user', 'content': content}],
                        'temperature': 0,
                        'max_tokens': 384,
                        'stream': False,
                    },
                ).raise_for_status().json()
            result = _json_object(response['choices'][0]['message']['content'])
            verdict = str(result.get('verdict') or 'uncertain').lower()
            if verdict not in {'same', 'different', 'uncertain'}:
                verdict = 'uncertain'
            confidence = max(0.0, min(1.0, float(result.get('confidence') or 0)))
            return {
                'verdict': verdict,
                'confidence': confidence,
                'matching_signals': list(result.get('matching_signals') or [])[:4],
                'conflicts': list(result.get('conflicts') or [])[:4],
                'reason': str(result.get('reason') or '')[:300],
                'engine': 'hermes-light-multimodal',
            }
        except Exception as exc:
            return {
                'verdict': 'technical_failure',
                'confidence': 0.0,
                'reason': f'{type(exc).__name__}: {exc}'[:300],
            }


def configured_replacement_matcher():
    if os.environ.get('A1_REPLACEMENT_AI', '').strip().lower() not in {'1', 'true', 'yes'}:
        return None
    return HermesReplacementMatcher(os.environ.get('A1_LOCAL_AI_URL', 'http://127.0.0.1:18080'))
