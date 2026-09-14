import pytest


def test_container_scheduler_is_explicitly_retired():
    import app.scheduler as scheduler_module

    with pytest.raises(RuntimeError, match='container scheduler is not accepted'):
        scheduler_module.start_scheduler()
