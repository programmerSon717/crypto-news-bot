"""발행 이력 저장소. URL(또는 소스별 고유 ID) 기준으로 중복 발행을 막는다."""
import hashlib
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 같은 기사인데 URL 만 달라 보이게 만드는 추적 파라미터.
# 예: 블록미디어는 RSS 링크에 ?utm_source=general&utm_medium=rss 를 붙인다.
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid)$")


def normalize_url(url: str) -> str:
    """같은 기사면 같은 문자열이 되도록 URL 을 정리한다.

    한 매체를 여러 피드로 등록하면(일반 피드 + 규제 피드 등) 같은 기사가
    두 번 들어온다. 중복제거 키는 '소스이름+고유값' 해시라 소스 이름이 다르면
    같은 기사도 다른 키가 되어 두 번 발행된다. 그걸 막으려고 URL 을 정규화해
    소스와 무관한 두 번째 잣대로 쓴다.

    스킴(http/https)과 www, 끝 슬래시, 추적 파라미터, 프래그먼트를 떼어낸다.
    기사 식별에 쓰이는 일반 쿼리(?id=123 등)는 남긴다.
    """
    if not url:
        return ""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not _TRACKING.match(k)])
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("", host, path, query, "")).lstrip("/") or url.strip().lower()


def _as_float(v) -> float | None:
    """origin_at 은 ALTER TABLE 로 붙인 TEXT 컬럼이라 문자열로 돌아온다."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
            # extra_ids: 한 글이 사진+본문 두 메시지로 나갈 때 나머지 id(쉼표 구분)
            # 재정렬(--resort)에 필요한 것들:
            #   photo_file_id — 사진을 다시 올릴 때 재업로드 없이 그대로 재사용
            #   origin_at     — 기사/트윗의 원래 게시 시각. 정렬 기준(발행 시각이 아님)
            # mirror_ids: 같은 글을 다른 탭에도 올렸을 때 그쪽 message_id(쉼표 구분).
            # extra_ids 와 섞으면 안 된다 — extra_ids 는 '같은 탭의 딸린 메시지'라
            # --resort 가 한 탭으로 다시 몰아넣는다. 미러는 다른 탭에 있어야 한다.
            for col in ("category", "lede", "text", "extra_ids",
                        "photo_file_id", "origin_at", "mirror_ids"):
                if col not in cols:
                    c.execute(f"ALTER TABLE published ADD COLUMN {col} TEXT")
            # 소스 이름과 무관하게 '이 기사를 이미 봤는가'를 판정하는 색인.
            # seen 은 소스이름+고유값 해시라 같은 기사가 다른 피드로 들어오면 못 잡는다.
            c.execute(
                """CREATE TABLE IF NOT EXISTS seen_urls (
                    url_key TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    first_seen REAL
                )"""
            )
            # 이미 발행된 글들을 색인에 한 번 채워 넣는다(표가 비었을 때만).
            # 이게 없으면 도입 직후 과거 기사가 다시 발행될 수 있다.
            if not c.execute("SELECT 1 FROM seen_urls LIMIT 1").fetchone():
                rows = c.execute(
                    "SELECT source_url, headline FROM published WHERE source_url IS NOT NULL"
                ).fetchall()
                now = time.time()
                c.executemany(
                    "INSERT OR IGNORE INTO seen_urls (url_key, source, title, first_seen)"
                    " VALUES (?,?,?,?)",
                    [(normalize_url(u), "backfill", t or "", now)
                     for u, t in rows if normalize_url(u)],
                )
                if rows:
                    print(f"[store] 기존 발행 {len(rows)}건을 URL 색인에 등록")

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

    def is_url_seen(self, url: str) -> bool:
        """소스 이름과 무관하게, 이 기사를 이미 처리했는가."""
        k = normalize_url(url)
        if not k:
            return False
        with self._conn() as c:
            return c.execute("SELECT 1 FROM seen_urls WHERE url_key=?", (k,)).fetchone() is not None

    def mark_url_seen(self, url: str, source: str, title: str):
        k = normalize_url(url)
        if not k:
            return
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO seen_urls (url_key, source, title, first_seen)"
                " VALUES (?,?,?,?)",
                (k, source, title, time.time()),
            )

    def mark_seen(self, key: str, source: str, title: str):
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO seen (key, source, title, published_at) VALUES (?,?,?,?)",
                (key, source, title, time.time()),
            )

    def record_published(self, key: str, message_id: int, thread_id: int | None,
                         source_url: str, headline: str,
                         category: str = "", lede: str = "", text: str = "",
                         extra_ids: list | None = None, photo_file_id: str = "",
                         origin_at: float | None = None,
                         mirror_ids: list | None = None):
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO published
                   (key, message_id, thread_id, source_url, headline,
                    published_at, category, lede, text, extra_ids,
                    photo_file_id, origin_at, mirror_ids)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, message_id, thread_id, source_url, headline, time.time(),
                 category, lede, text, ",".join(str(i) for i in (extra_ids or [])),
                 photo_file_id, origin_at,
                 ",".join(str(i) for i in (mirror_ids or []))),
            )

    def all_published(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT key, message_id, thread_id, category, headline, lede, text,
                          extra_ids, photo_file_id, origin_at, mirror_ids
                   FROM published ORDER BY published_at"""
            ).fetchall()
        return [
            {"key": r[0], "message_id": r[1], "thread_id": r[2], "category": r[3] or "",
             "headline": r[4] or "", "lede": r[5] or "", "text": r[6] or "",
             "extra_ids": [int(x) for x in (r[7] or "").split(",") if x.strip()],
             "photo_file_id": r[8] or "", "origin_at": _as_float(r[9]),
             "mirror_ids": [int(x) for x in (r[10] or "").split(",") if x.strip()]}
            for r in rows
        ]

    def update_published_ids(self, key: str, message_id: int, extra_ids: list):
        """재정렬로 메시지를 다시 올린 뒤 새 id 로 갱신한다."""
        with self._conn() as c:
            c.execute(
                "UPDATE published SET message_id=?, extra_ids=? WHERE key=?",
                (message_id, ",".join(str(i) for i in extra_ids), key),
            )

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

    def drop_published(self, key: str):
        """발행 기록만 지운다. seen 은 남겨 다시 수집되지 않게 한다."""
        with self._conn() as c:
            c.execute("DELETE FROM published WHERE key=?", (key,))

    def forget(self, key: str):
        """재발행할 수 있도록 이력에서 지운다."""
        with self._conn() as c:
            c.execute("DELETE FROM seen WHERE key=?", (key,))
            c.execute("DELETE FROM published WHERE key=?", (key,))
