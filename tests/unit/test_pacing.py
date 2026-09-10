from app.scraper.pacing import choose_pause


def test_choose_pause_respects_bounds_and_disabled_mode():
    assert choose_pause(0, 0) == 0
    assert choose_pause(7, 3) == 7
    for _ in range(20):
        assert 2 <= choose_pause(2, 5) <= 5
