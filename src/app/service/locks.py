from contextlib import contextmanager

from sqlalchemy import text


@contextmanager
def operation_lock(db, key, local_lock, busy_error, message):
    """Keep session-level PG locks on one connection, even when ORM commits."""
    engine = getattr(db, 'bind', None)
    if engine is not None and engine.dialect.name == 'postgresql':
        with engine.connect() as connection:
            if not connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar():
                raise busy_error(message)
            try:
                yield
            finally:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
    else:
        if not local_lock.acquire(blocking=False):
            raise busy_error(message)
        try:
            yield
        finally:
            local_lock.release()
