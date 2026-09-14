"""One-time, recoverable documentation relocation (no runtime/data files)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'(!?\[[^\]]*\]\()([^\s)]+)(\))')


def main() -> None:
    names = ['README.md', 'BIBLE.md', 'INSTRUCTION.md', 'CONCEPT.md']
    historical = ['QA_0_7.md', 'QA_0_8.md', 'SCRIPT_COMPARISON_2026-09-07.md',
                  'LIVE_RUN_REVIEW_2026-09-09.md', 'MSI_AGENT_PUBLICATION_PLAN.md']
    mapping = {ROOT / n: ROOT / 'docs/archive/2026-09-10' / n for n in names}
    mapping.update({ROOT / 'docs' / n: ROOT / 'docs/archive' / n for n in historical})
    if any(target.exists() for target in mapping.values()):
        raise SystemExit('Archive already exists; no changes made.')
    documents = [*ROOT.glob('*.md'), *(ROOT / 'docs').rglob('*.md')]
    texts = {p: p.read_text(encoding='utf-8') for p in documents}
    for source, destination in mapping.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
    for old_path, text in texts.items():
        new_path = mapping.get(old_path, old_path)

        def relocate(match: re.Match, old_path: Path = old_path, new_path: Path = new_path) -> str:
            value = match[2]
            if value.startswith(('#', '/', '<')) or '://' in value or value.startswith('mailto:'):
                return match[0]
            path, separator, anchor = value.partition('#')
            old_target = (old_path.parent / unquote(path)).resolve()
            target = mapping.get(old_target, old_target)
            relative = os.path.relpath(target, new_path.parent)
            return match[1] + relative + (separator + anchor if separator else '') + match[3]

        new_path.write_text(LINK.sub(relocate, text), encoding='utf-8')
    for name in ('infrastructure', 'monitoring', 'services', 'web'):
        path = ROOT / name
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    print(f'DOCUMENTATION_REORGANIZED moved={len(mapping)} empty_scaffolds_removed=4')


if __name__ == '__main__':
    main()
