#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]


def lock_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (ROOT / 'config' / 'ai-tools.lock').read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def _message_content(prompt: str, image_path: str) -> str | list[dict[str, Any]]:
    if not image_path:
        return prompt
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f'Image is missing: {path}')
    mime = mimetypes.guess_type(path.name)[0] or 'image/png'
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        output_format = 'PNG' if mime == 'image/png' else 'JPEG'
        image.save(buffer, format=output_format, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}},
    ]


def _extract_content(response: dict[str, Any]) -> str:
    choices = response.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError('Local model response has no choices')
    message = choices[0].get('message')
    if not isinstance(message, dict):
        raise ValueError('Local model response has no message')
    content = message.get('content') or message.get('reasoning_content')
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text = '\n'.join(
            str(item.get('text') or '') for item in content if isinstance(item, dict)
        ).strip()
        if text:
            return text
    raise ValueError('Local model response has no text content')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--image', default='')
    parser.add_argument('--reasoning', default='low')
    args = parser.parse_args()
    lock = lock_values()
    profile = os.environ.get('A1_AI_PROFILE', 'light').strip().lower()
    model_key = 'AI_HEAVY_MODEL_FILE' if profile == 'heavy' else 'AI_MODEL_FILE'
    max_tokens_key = 'AI_HEAVY_MODEL_MAX_OUTPUT_TOKENS' if profile == 'heavy' else 'AI_MODEL_MAX_OUTPUT_TOKENS'
    model = lock[model_key]
    prompt = Path(args.prompt).read_text(encoding='utf-8')
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': _message_content(prompt, args.image)}],
        'temperature': 0,
        'max_tokens': int(lock[max_tokens_key]),
        'stream': False,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{lock['AI_MODEL_PORT']}/v1/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    output = Path(args.output)
    try:
        with urllib.request.urlopen(request, timeout=900) as result:
            response = json.loads(result.read().decode('utf-8'))
        output.write_text(_extract_content(response) + '\n', encoding='utf-8')
        return 0
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        message = f'LOCAL_MODEL_REQUEST_FAILED: {exc}'
        output.write_text(message + '\n', encoding='utf-8')
        print(message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
