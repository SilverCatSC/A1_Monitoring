import threading
from types import SimpleNamespace

import pytest

from app.service.locks import operation_lock


@pytest.mark.parametrize('acquired,body_fails', [(True, False), (True, True), (False, False)])
def test_postgres_lock_uses_one_dedicated_connection_and_releases(acquired, body_fails):
    calls = []
    class Connection:
        def __enter__(self):
            calls.append('connect')
            return self

        def __exit__(self, *_):
            calls.append('close')

        def execute(self, query, params):
            calls.append(str(query))
            assert params == {'key': 123}
            return SimpleNamespace(scalar=lambda: acquired)

    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=Connection))
    def operation():
        with operation_lock(db, 123, threading.Lock(), ValueError, 'busy'):
            calls.append('body')
            if body_fails:
                raise RuntimeError('rollback')
    if not acquired or body_fails:
        with pytest.raises(ValueError if not acquired else RuntimeError):
            operation()
    else:
        operation()
    assert calls[0] == 'connect' and calls[-1] == 'close'
    assert calls.count('SELECT pg_advisory_unlock(:key)') == int(acquired)
    assert calls.count('body') == int(acquired)
