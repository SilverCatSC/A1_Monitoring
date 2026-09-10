from app.service.scan_progress import ScanProgressTracker, read_scan_progress


def test_progress_tracker_persists_filter_page_and_completion(tmp_path, capsys):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'source_refresh_started'})
    tracker(
        {
            'event': 'source_refresh_finished',
            'rows_total': 130,
            'rows_valid': 106,
            'rows_invalid': 24,
        }
    )
    tracker({'event': 'cycle_started', 'total_filters': 2})
    tracker(
        {
            'event': 'filter_started',
            'source': 'auto_ru',
            'filter_name': 'V-Class',
            'source_index': 1,
            'source_total': 1,
            'overall_index': 1,
            'expected': 3,
        }
    )
    tracker(
        {
            'event': 'page_wait',
            'source': 'auto_ru',
            'page': 1,
            'pages_total': 3,
            'wait_seconds': 8.5,
        }
    )
    tracker(
        {
            'event': 'filter_retry',
            'source': 'auto_ru',
            'filter_name': 'V-Class',
            'retry_seconds': 5,
        }
    )
    tracker(
        {
            'event': 'page_finished',
            'source': 'auto_ru',
            'page': 1,
            'pages_total': 3,
            'cards': 22,
            'target_cards': 2,
            'state': 'results',
        }
    )
    tracker(
        {
            'event': 'filter_finished',
            'source': 'auto_ru',
            'filter_name': 'V-Class',
            'overall_index': 1,
            'status': 'ok',
            'found': 2,
            'expected': 3,
        }
    )
    payload = read_scan_progress(str(tmp_path))
    assert payload['status'] == 'running'
    assert payload['completed_filters'] == 1
    assert payload['current']['filter_name'] == 'V-Class'
    assert payload['current']['page'] == 1
    output = capsys.readouterr().out
    assert 'РЕЕСТР · обновлён · валидных: 106 из 130' in output
    assert 'щадящая пауза 8.5 с · перед страницей 1/3' in output
    assert 'вкладка Chrome была закрыта · повтор фильтра через 5 с' in output
    assert 'страница 1/3 · карточек: 22' in output

    summary = {'technical_errors': 0, 'found': 2}
    tracker({'event': 'cycle_finished', 'summary': summary})
    tracker({'event': 'direct_cards_started', 'total': 2})
    tracker({
        'event': 'direct_card_finished',
        'source': 'avito',
        'card_index': 1,
        'card_total': 2,
        'vehicle': 'Mercedes-Benz VLE',
        'status_code': 'active',
    })
    assert read_scan_progress(str(tmp_path))['direct_cards']['checked'] == 1
    tracker({'event': 'direct_cards_finished', 'summary': {'active': 2, 'total': 2}})
    payload = read_scan_progress(str(tmp_path))
    assert payload['status'] == 'completed'
    assert payload['completed_filters'] == 2
    assert payload['summary'] == summary


def test_missing_progress_file_is_idle(tmp_path):
    assert read_scan_progress(str(tmp_path)) == {'status': 'idle', 'events': []}
