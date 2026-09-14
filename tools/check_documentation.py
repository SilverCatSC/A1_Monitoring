"""Check local inline Markdown file links without opening external resources."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)')
FENCES = re.compile(r'^\s*(`{3,}|~{3,})')


def main() -> int:
    documents = sorted([*ROOT.glob('*.md'), *(ROOT / 'docs').rglob('*.md')])
    broken = []
    checked = 0
    for document in documents:
        fence = ''
        for number, line in enumerate(document.read_text(encoding='utf-8').splitlines(), 1):
            match = FENCES.match(line)
            if match:
                marker = match[1][0]
                fence = '' if fence == marker else marker if not fence else fence
                continue
            if fence:
                continue
            for target in LINK.findall(line):
                target = target.strip('<>')
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                path = document.parent / unquote(parsed.path)
                checked += 1
                if not path.exists():
                    broken.append(f'{document.relative_to(ROOT)}:{number}: {target}')
    for item in broken:
        print(item)
    print(f'DOC_LINKS documents={len(documents)} checked={checked} broken={len(broken)} '
          '(inline file targets only; external URLs and anchors not validated)')
    return bool(broken)


if __name__ == '__main__':
    raise SystemExit(main())
