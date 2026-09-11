import time

import pytest

from app.service.agent_kernel import AgentQueue


def test_queue_survives_restart_and_deduplicates(tmp_path):
    path = tmp_path / 'queue.db'
    first = AgentQueue(path)
    first.enqueue('quality', {'revision': 'a'}, 'quality:a')
    first.close()
    second = AgentQueue(path)
    try:
        second.enqueue('quality', {'revision': 'a'}, 'quality:a')
        assert len(second.status()) == 1
        job = second.claim('worker')
        assert job['payload']['revision'] == 'a'
        second.finish(job['id'], 'worker', True, 'passed')
        assert second.claim('worker') is None
    finally:
        second.close()


def test_single_worker_lease_and_expired_recovery(tmp_path):
    queue = AgentQueue(tmp_path / 'queue.db')
    try:
        queue.enqueue('quality', {}, 'one')
        queue.enqueue('maintenance', {}, 'two')
        job = queue.claim('one')
        assert queue.claim('two') is None
        with pytest.raises(ValueError):
            queue.finish(job['id'], 'two', True, 'wrong owner')
        queue.db.execute('UPDATE jobs SET lease_until=? WHERE id=?', (time.time() - 1, job['id']))
        queue.db.commit()
        recovered = queue.claim('two')
        assert recovered['id'] == job['id']
        assert recovered['attempts'] == 2
    finally:
        queue.close()


def test_arbitrary_command_task_is_rejected(tmp_path):
    queue = AgentQueue(tmp_path / 'queue.db')
    try:
        with pytest.raises(ValueError):
            queue.enqueue('shell', {'command': 'anything'}, 'bad')
    finally:
        queue.close()
