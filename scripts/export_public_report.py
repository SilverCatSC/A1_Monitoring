"""Export read-only dashboard pages into a self-contained GitHub Pages tree."""
from __future__ import annotations

import argparse
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROUTES = {
    # The sales report is the primary publication artifact. Export it before
    # long history pages so its direct-card proof cannot be crowded out by the
    # global evidence cap.
    'listings/index.html': '/api/v1/dashboard/listings',
    'index.html': '/api/v1/dashboard',
    'placements/index.html': '/api/v1/dashboard/placements',
    'analytics/index.html': '/api/v1/dashboard/analytics',
    'history/index.html': '/api/v1/dashboard/history',
    'feedback/index.html': '/api/v1/dashboard/feedback',
    'activity/index.html': '/api/v1/dashboard/activity',
}
API_DASHBOARD_RE = re.compile(
    r'/api/v1/dashboard(?:/listings/[^"\'?#/]+|/(?:placements|analytics|history|listings|feedback|activity))?'
    r'(?:\?[^"\']*)?'
)
EVIDENCE_RE = re.compile(r'/api/v1/observations/([^/"\']+)/evidence/(\d+)')
DIRECT_EVIDENCE_RE = re.compile(r'/api/v1/reconciliations/([^/"\']+)/evidence')


def _fetch(base_url: str, path: str) -> bytes:
    request = Request(urljoin(base_url.rstrip('/') + '/', path.lstrip('/')), headers={'User-Agent': 'A1-report-export/1'})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status} for {path}')
        return response.read()


def _route_for(value: str) -> str:
    match = re.match(r'/api/v1/dashboard/listings/([^/?#]+)', value)
    if match:
        return f'listings/{match.group(1)}/index.html'
    path = value.split('?', 1)[0].rstrip('/')
    return {
        '/api/v1/dashboard': 'index.html',
        '/api/v1/dashboard/placements': 'placements/index.html',
        '/api/v1/dashboard/analytics': 'analytics/index.html',
        '/api/v1/dashboard/history': 'history/index.html',
        '/api/v1/dashboard/listings': 'listings/index.html',
        '/api/v1/dashboard/feedback': 'feedback/index.html',
        '/api/v1/dashboard/activity': 'activity/index.html',
    }.get(path, '#')


def main() -> int:
    parser = argparse.ArgumentParser(description='Export the local A1 dashboard as static HTML.')
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--out', default='public')
    parser.add_argument('--max-evidence', type=int, default=250)
    args = parser.parse_args()

    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    static_source = Path(__file__).resolve().parents[1] / 'src' / 'app' / 'static'
    static_target = output / 'static'
    if static_target.exists():
        shutil.rmtree(static_target)
    shutil.copytree(static_source, static_target)

    exported: dict[str, str] = {}
    listing_ids: set[str] = set()
    for target, path in ROUTES.items():
        body = _fetch(args.base_url, path).decode('utf-8')
        listing_ids.update(re.findall(r'/api/v1/dashboard/listings/([^/?#"\']+)', body))
        exported[target] = body

    for listing_id in sorted(listing_ids):
        target = f'listings/{listing_id}/index.html'
        try:
            exported[target] = _fetch(args.base_url, f'/api/v1/dashboard/listings/{listing_id}').decode('utf-8')
        except Exception as exc:
            print(f'EXPORT_LISTING_SKIPPED {listing_id}: {exc}')

    evidence_count = 0
    for target, body in list(exported.items()):
        depth = len(Path(target).parts) - 1
        prefix = '../' * depth
        body = re.sub(r'(?P<quote>["\'])/static/', r'\g<quote>' + prefix + 'static/', body)

        def evidence(match: re.Match[str], prefix: str = prefix) -> str:
            nonlocal evidence_count
            if evidence_count >= args.max_evidence:
                return '#'
            observation_id, page = match.group(1), match.group(2)
            relative = f'{prefix}evidence/{observation_id}-{page}.png'
            destination = output / 'evidence' / f'{observation_id}-{page}.png'
            if not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(_fetch(args.base_url, match.group(0)))
                    evidence_count += 1
                except Exception:
                    return '#'
            return relative

        body = EVIDENCE_RE.sub(evidence, body)

        def direct_evidence(match: re.Match[str], prefix: str = prefix) -> str:
            nonlocal evidence_count
            if evidence_count >= args.max_evidence:
                return '#'
            reconciliation_id = match.group(1)
            relative = f'{prefix}evidence/direct-{reconciliation_id}.png'
            destination = output / 'evidence' / f'direct-{reconciliation_id}.png'
            if not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(_fetch(args.base_url, match.group(0)))
                    evidence_count += 1
                except Exception:
                    return '#'
            return relative

        body = DIRECT_EVIDENCE_RE.sub(direct_evidence, body)

        def dashboard(match: re.Match[str], target: str = target) -> str:
            route = _route_for(match.group(0))
            if route == '#':
                return '#'
            source_dir = posixpath.dirname(target) or '.'
            return posixpath.relpath(route, source_dir)

        body = API_DASHBOARD_RE.sub(dashboard, body)
        body = re.sub(r'<script\b[^>]*>.*?</script>', '', body, flags=re.IGNORECASE | re.DOTALL)
        body = body.replace('</main>', '<div class="notice">Публичная копия отчёта доступна только для просмотра. Замечания передаются через Битрикс.</div></main>')
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding='utf-8')

    print(f'PUBLIC_EXPORT_OK pages={len(exported)} evidence={evidence_count} out={output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
