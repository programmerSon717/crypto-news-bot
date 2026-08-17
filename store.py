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
            # 발행한 메시지의 id. 나중에 형식을 고쳐 수정(editMessageText)하거나
            # 잘못 나간 글을 지우려면 id가 있어야 한다. 없으면 손댈 방법이 없다.
            c.execute(
                """CREATE TABLE IF NOT EXISTS published (
                    key TEXT PRIMARY KEY,
                    message_id INTEGER,
                    thread_id INTEGER,
                    source_url TEXT,
                    headline TEXT,
                    published_at REAL
                )"""
            )
            # 시간별 다이제스트를 만들려면 헤드라인만으로는 부족해 분류·요약문도 남긴다.
            # 이미 만들어진 DB에도 적용되도록 없을 때만 컬럼을 추가한다.
            cols = {r[1] for r in c.execute("PRAGMA table_info(published)")}
            # text 는 발행 원문(HTML). 나중에 다른 탭으로 옮길 때 그대로 다시 쓸 수 있다.
            for col in ("category", "lede", "text"):
                if col not in cols:
                    c.execute(f"ALTER TABLE published ADD COLUMN {col} TEXT")
            # 다이제스트 중복 발행 방지용 — 어느 구간까지 요약했는지 기록
            c.execute(
                """CREATE TABLE IF NOT EXISTS digest_log (
                    scope TEXT,
                    window_end REAL,
                    message_id INTEGER,
                    PRIMARY KEY (scope, window_end)
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

    def record_published(self, key: str, message_id: int, thread_id: int | None,
                         source_url: str, headline: str,
                         category: str = "", lede: str = "", text: str = ""):
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO published
                   (key, message_id, thread_id, source_url, headline,
                    published_at, category, lede, text)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (key, message_id, thread_id, source_url, headline, time.time(),
                 category, lede, text),
            )

    def all_published(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT key, message_id, thread_id, category, headline, lede, text
                   FROM published ORDER BY published_at"""
            ).fetchall()
        return [
            {"key": r[0], "message_id": r[1], "thread_id": r[2], "category": r[3] or "",
             "headline": r[4] or "", "lede": r[5] or "", "text": r[6] or ""}
            for r in rows
        ]

    def update_published_location(self, key: str, message_id: int,
                                  thread_id: int | None, category: str):
        with self._conn() as c:
            c.execute(
                "UPDATE published SET message_id=?, thread_id=?, category=? WHERE key=?",
                (message_id, thread_id, category, key),
            )

    def published_between(self, start: float, end: float) -> list[dict]:
        """구간 안에 발행된 글 목록(오래된 순). 다이제스트 재료."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT category, headline, lede, source_url, message_id, published_at
                   FROM published
                   WHERE published_at >= ? AND published_at < ?
                   ORDER BY published_at""",
                (start, end),
            ).fetchall()
        return [
            {"category": r[0] or "이슈", "headline": r[1], "lede": r[2] or "",
             "source_url": r[3] or "", "message_id": r[4], "published_at": r[5]}
            for r in rows
        ]

    def digest_done(self, scope: str, window_end: float) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM digest_log WHERE scope=? AND window_end=?",
                (scope, window_end),
            ).fetchone()
            return row is not None

    def record_digest(self, scope: str, window_end: float, message_id: int):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO digest_log (scope, window_end, message_id) VALUES (?,?,?)",
                (scope, window_end, message_id),
            )

    def forget(self, key: str):
        """재발행할 수 있도록 이력에서 지운다."""
        with self._conn() as c:
            c.execute("DELETE FROM seen WHERE key=?", (key,))
            c.execute("DELETE FROM published WHERE key=?", (key,))
