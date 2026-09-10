from __future__ import annotations

import json

from app.service.company_site_report import company_site_audit_context


def test_company_site_report_projects_bounded_safe_data(tmp_path) -> None:
    audit = tmp_path / 'latest.json'
    audit.write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-09-09T18:38:09+00:00',
        'source': {'final_url': 'https://a1auto.ru/cars-for-sale/'},
        'summary': {
            'active_marketing_records': 32,
            'catalogue_candidates': 100,
            'published_catalogue_candidates': 29,
            'issues_total': 1,
            'issues_by_severity': {'high': 0, 'medium': 1, 'low': 0},
        },
        'issues': [{'code': 'active_site_link_missing', 'severity': 'medium', 'vin': None, 'head_row': 12}],
        'limitations': ['Publication is not search visibility.'],
    }), encoding='utf-8')

    context = company_site_audit_context(tmp_path)

    assert context['state'] == 'ready'
    assert context['summary']['active_marketing_records'] == 32
    assert context['issues'][0]['code'] == 'active_site_link_missing'
    assert context['generated_at'].year == 2026


def test_company_site_report_treats_broken_artifact_as_non_result(tmp_path) -> None:
    (tmp_path / 'latest.json').write_text('{', encoding='utf-8')

    context = company_site_audit_context(tmp_path)

    assert context['state'] == 'invalid'
    assert context['summary'] is None


def test_company_site_report_does_not_expose_unsafe_artifact_links(tmp_path) -> None:
    (tmp_path / 'latest.json').write_text(json.dumps({
        'schema_version': 1,
        'source': {'final_url': 'javascript://a1auto.ru/cars-for-sale/'},
        'summary': {'issues_by_severity': {}},
        'issues': [{'direct_url': 'javascript://a1auto.ru/cars-for-sale/x.html'}],
    }), encoding='utf-8')

    context = company_site_audit_context(tmp_path)

    assert context['source_url'] == 'https://a1auto.ru/cars-for-sale/'
    assert context['issues'][0]['direct_url'] is None
