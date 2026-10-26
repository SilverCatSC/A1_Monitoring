from app.service.completion import summarize_cycle_completion


def test_successful_search_cannot_hide_cards_excluded_by_unverified_catalogue():
    result = summarize_cycle_completion(
        {'technical_errors': 0, 'found': 17, 'review_required': 11,
         'dealer_catalogue_failures': [{'source': 'avito', 'error': 'HTTP 403'}]},
        {'incomplete': 0, 'technical_errors': 0, 'skipped_source_unavailable': 28,
         'skipped_catalogue_incomplete': 10, 'skipped_unresolved': 0},
    )
    assert result['status'] == 'partial'
    assert result['direct_cards_incomplete'] == result['direct_cards_unchecked'] == 38
    assert result['direct_cards_technical_errors'] == 0
    assert result['technical_errors'] == 1
    assert result['search_observations'] == {
        'found': 17, 'review_required': 11, 'missed_confirmed': 0, 'missed_uncertain': 0,
    }
    assert 'direct_cards_incomplete' in result['partial_reasons']


def test_eligible_incomplete_and_unresolved_cards_are_counted_once():
    result = summarize_cycle_completion({}, {
        'incomplete': 2, 'technical_errors': 2, 'skipped_unresolved': 3,
        'skipped_source_unavailable': 4, 'skipped_catalogue_incomplete': 5,
        'skipped_not_in_catalogue': 6,
    })
    assert result['direct_cards_unchecked'] == 12
    assert result['direct_cards_incomplete'] == 14
    assert result['technical_errors'] == 2
    assert result['status'] == 'partial'


def test_confirmed_inactive_cards_do_not_make_completed_cycle_partial():
    result = summarize_cycle_completion({'inactive_placements': 2}, {
        'incomplete': 0, 'skipped_not_in_catalogue': 2, 'skipped_unresolved': 0,
        'skipped_source_unavailable': 0, 'skipped_catalogue_incomplete': 0,
    })
    assert result['status'] == 'completed'
    assert result['direct_cards_incomplete'] == result['direct_cards_unchecked'] == 0
