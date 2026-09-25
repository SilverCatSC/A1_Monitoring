from app.service.scan_progress import ScanProgressTracker, read_scan_progress


def test_progress_tracker_persists_filter_page_and_completion(tmp_path, capsys):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'cycle_registered', 'cycle_id': 'cycle-1'})
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
    assert payload['cycle_id'] == 'cycle-1'
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
    direct_summary = {
        'active': 2,
        'total': 2,
        'incomplete': 0,
        'technical_errors': 0,
    }
    tracker({'event': 'direct_cards_finished', 'summary': direct_summary})
    tracker({'event': 'cycle_completed', 'summary': {'status': 'completed'}})
    payload = read_scan_progress(str(tmp_path))
    assert payload['status'] == 'completed'
    assert payload['completed_filters'] == 2
    assert payload['summary'] == {'status': 'completed'}


def test_progress_tracker_marks_direct_card_failures_as_partial(tmp_path):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'cycle_started', 'total_filters': 1})
    tracker({'event': 'cycle_finished', 'summary': {'technical_errors': 0}})
    tracker(
        {
            'event': 'direct_cards_finished',
            'summary': {
                'blocked': 2,
                'total': 2,
                'incomplete': 2,
                'technical_errors': 2,
            },
        }
    )
    tracker(
        {
            'event': 'cycle_completed',
            'summary': {
                'status': 'partial',
                'technical_errors': 2,
                'direct_cards_technical_errors': 2,
                'direct_cards_incomplete': 2,
            },
        }
    )

    payload = read_scan_progress(str(tmp_path))
    assert payload['status'] == 'partial'
    assert payload['summary']['direct_cards_technical_errors'] == 2


def test_captcha_wait_is_visible_and_returns_to_prior_stage(tmp_path, capsys):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'dealer_preflight_started'})
    tracker({'event': 'captcha_operator_required', 'source': 'auto_ru',
             'url': 'https://auto.ru/cars/', 'wait_seconds': 180})
    waiting = read_scan_progress(str(tmp_path))
    assert waiting['status'] == 'waiting_captcha'
    assert waiting['current']['event'] == 'captcha_operator_required'
    assert 'пройдите проверку в открытом Chrome' in capsys.readouterr().out

    tracker({'event': 'captcha_operator_resolved', 'source': 'auto_ru',
             'url': 'https://auto.ru/cars/'})
    assert read_scan_progress(str(tmp_path))['status'] == 'reconciling'


def test_missing_progress_file_is_idle(tmp_path):
    assert read_scan_progress(str(tmp_path)) == {'status': 'idle', 'events': []}
