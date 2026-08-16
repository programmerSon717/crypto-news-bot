"""발행 이력 저장소. URL(또는 소스별 고유 ID) 기준으로 중복 발행을 막는다."""
import hashlib
import sqlite3
import time
from contextlib import contextmanager


class Store:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS seen (
                    key TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    published_at REAL
                )"""
            )

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def make_key(source: str, unique: str) -> str:
        return hashlib.sha256(f"{source}::{unique}".encode()).hexdigest()

    def is_seen(self, key: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone()
            return row is not None

    def mark_seen(self, key: str, source: str, title: str):
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO seen (key, source, title, published_at) VALUES (?,?,?,?)",
                (key, source, title, time.time()),
            )
