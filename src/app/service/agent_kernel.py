"""Durable, single-consumer task queue. Payloads are data, never shell commands."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

KINDS = frozenset({'quality', 'maintenance'})


class AgentQueue:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL,
            owner TEXT, lease_until REAL, result TEXT)''')
        self.db.commit()

    def close(self):
        self.db.close()

    def enqueue(self, kind: str, payload: dict, dedupe: str) -> None:
        if kind not in KINDS:
            raise ValueError('unsupported task kind')
        self.db.execute('INSERT OR IGNORE INTO jobs(id,dedupe,kind,payload,available) VALUES (?,?,?,?,?)',
                        (str(uuid.uuid4()), dedupe, kind, json.dumps(payload), time.time()))
        self.db.commit()

    def claim(self, owner: str, lease_seconds: int = 3600):
        now = time.time()
        try:
            self.db.execute('BEGIN IMMEDIATE')
            # A crashed attempt is visible and retryable, never silently successful.
            self.db.execute("UPDATE jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END, "
                            "owner=NULL, result='worker lease expired' WHERE state='running' AND lease_until<?", (now,))
            if self.db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone():
                self.db.commit()
                return None
            row = self.db.execute("SELECT * FROM jobs WHERE state='queued' AND available<=? ORDER BY available,id LIMIT 1", (now,)).fetchone()
            if row is None:
                self.db.commit()
                return None
            self.db.execute("UPDATE jobs SET state='running', owner=?, attempts=attempts+1, lease_until=? WHERE id=?",
                            (owner, now + lease_seconds, row['id']))
            self.db.commit()
            return {**dict(row), 'payload': json.loads(row['payload']), 'attempts': row['attempts'] + 1}
        except Exception:
            self.db.rollback()
            raise

    def finish(self, job_id: str, owner: str, success: bool, result: str):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['owner'] != owner or row['state'] != 'running':
            raise ValueError('task is no longer owned by this worker')
        state = 'done' if success else 'failed' if row['attempts'] >= 3 else 'queued'
        self.db.execute('UPDATE jobs SET state=?, result=?, available=?, owner=NULL, lease_until=NULL WHERE id=?',
                        (state, result[-8000:], time.time() + min(3600, 60 * 2 ** row['attempts']), job_id))
        self.db.commit()

    def status(self):
        return [dict(row) for row in self.db.execute('SELECT id,kind,state,attempts,result FROM jobs ORDER BY available')]
