from app.service.completion import summarize_cycle_completion


def test_completed_requires_successful_search_and_direct_cards():
    assert summarize_cycle_completion(
        {'technical_errors': 0, 'links_need_review': 0},
        {'technical_errors': 0, 'incomplete': 0},
    )['status'] == 'completed'


def test_blocked_direct_cards_make_an_otherwise_clean_search_partial():
    result = summarize_cycle_completion(
        {'technical_errors': 0, 'links_need_review': 0},
        {'technical_errors': 2, 'incomplete': 2},
    )

    assert result['status'] == 'partial'
    assert result['technical_errors'] == 2
    assert result['partial_reasons'] == ['direct_card_technical_errors']
