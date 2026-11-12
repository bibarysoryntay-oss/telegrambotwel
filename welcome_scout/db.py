"""SQLite-хранилище: аккаунты, публикации, снимки метрик, отправленные примеры, идеи и сценарии."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import AccountConfig
from .models import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username TEXT PRIMARY KEY,
    niche TEXT DEFAULT '',
    source TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    followers INTEGER,
    last_fetch TEXT,
    last_error TEXT,
    unavailable TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    permalink TEXT,
    ts TEXT NOT NULL,
    kind TEXT,
    caption TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    video_url TEXT,
    thumbnail_url TEXT,
    image_urls TEXT DEFAULT '[]',
    source TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS posts_user ON posts(username);
CREATE TABLE IF NOT EXISTS snapshots (
    post_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER
);
CREATE INDEX IF NOT EXISTS snapshots_post ON snapshots(post_id);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT UNIQUE NOT NULL,
    signal TEXT,
    ratio REAL,
    metric TEXT,
    baseline REAL,
    analysis TEXT,
    status TEXT,
    feedback TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    language TEXT,
    tour TEXT,
    text TEXT,
    created_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # --- аккаунты ---
    def seed_accounts(self, accounts: list[AccountConfig]) -> None:
        with self.conn() as c:
            for a in accounts:
                c.execute(
                    "INSERT OR IGNORE INTO accounts(username, niche, source) VALUES (?,?,?)",
                    (a.username, a.niche, a.source),
                )

    def add_account(self, username: str, niche: str = "", source: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO accounts(username, niche, source, active) VALUES (?,?,?,1) "
                "ON CONFLICT(username) DO UPDATE SET active=1, niche=COALESCE(NULLIF(excluded.niche,''), niche), "
                "source=COALESCE(NULLIF(excluded.source,''), source)",
                (username, niche, source),
            )

    def remove_account(self, username: str) -> bool:
        with self.conn() as c:
            return c.execute("UPDATE accounts SET active=0 WHERE username=?", (username,)).rowcount > 0

    def active_accounts(self) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM accounts WHERE active=1 ORDER BY username").fetchall()

    def account(self, username: str) -> sqlite3.Row | None:
        with self.conn() as c:
            return c.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()

    def mark_fetch(self, username: str, followers: int | None, error: str | None, unavailable: list[str] | None) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE accounts SET last_fetch=?, last_error=?, followers=COALESCE(?, followers), "
                "unavailable=COALESCE(?, unavailable) WHERE username=?",
                (now_iso(), error, followers, json.dumps(unavailable, ensure_ascii=False) if unavailable is not None else None, username),
            )

    # --- публикации ---
    def upsert_posts(self, posts: list[Post]) -> None:
        ts = now_iso()
        with self.conn() as c:
            for p in posts:
                c.execute(
                    """INSERT INTO posts(id, username, permalink, ts, kind, caption, views, likes, comments,
                           video_url, thumbnail_url, image_urls, source, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET views=excluded.views, likes=excluded.likes,
                           comments=excluded.comments, caption=excluded.caption, video_url=excluded.video_url,
                           thumbnail_url=excluded.thumbnail_url, image_urls=excluded.image_urls, last_seen=excluded.last_seen""",
                    (p.id, p.username, p.permalink, p.timestamp.isoformat(), p.kind, p.caption, p.views, p.likes,
                     p.comments, p.video_url, p.thumbnail_url, json.dumps(p.image_urls), p.source, ts, ts),
                )
                c.execute(
                    "INSERT INTO snapshots(post_id, fetched_at, views, likes, comments) VALUES (?,?,?,?,?)",
                    (p.id, ts, p.views, p.likes, p.comments),
                )

    def posts_for(self, username: str) -> list[Post]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM posts WHERE username=? ORDER BY ts DESC", (username,)).fetchall()
        return [row_to_post(r) for r in rows]

    def post(self, post_id: str) -> Post | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return row_to_post(r) if r else None

    def previous_snapshot(self, post_id: str) -> sqlite3.Row | None:
        """Предпоследний снимок метрик — чтобы показать прирост с прошлой проверки."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM snapshots WHERE post_id=? ORDER BY fetched_at DESC LIMIT 2", (post_id,)
            ).fetchall()
        return rows[1] if len(rows) == 2 else None

    # --- отобранные примеры ---
    def seen_post_ids(self) -> set[str]:
        with self.conn() as c:
            return {r[0] for r in c.execute("SELECT post_id FROM items")}

    def save_item(self, post_id: str, signal: str, ratio: float, metric: str, baseline: float,
                  analysis: dict | None, status: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO items(post_id, signal, ratio, metric, baseline, analysis, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (post_id, signal, ratio, metric, baseline, json.dumps(analysis, ensure_ascii=False) if analysis else None,
                 status, now_iso()),
            )
            return cur.lastrowid

    def item(self, item_id: int) -> sqlite3.Row | None:
        with self.conn() as c:
            return c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def set_feedback(self, item_id: int, feedback: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE items SET feedback=? WHERE id=?", (feedback, item_id))

    def items_with_feedback(self, feedback: str, limit: int = 20) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                "SELECT i.*, p.username, p.permalink FROM items i JOIN posts p ON p.id=i.post_id "
                "WHERE i.feedback=? ORDER BY i.id DESC LIMIT ?",
                (feedback, limit),
            ).fetchall()

    def save_script(self, item_id: int, language: str, tour: str, text: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO scripts(item_id, language, tour, text, created_at) VALUES (?,?,?,?,?)",
                (item_id, language, tour, text, now_iso()),
            )


def row_to_post(r: sqlite3.Row) -> Post:
    return Post(
        id=r["id"], username=r["username"], permalink=r["permalink"] or "",
        timestamp=datetime.fromisoformat(r["ts"]), kind=r["kind"] or "image", caption=r["caption"] or "",
        views=r["views"], likes=r["likes"], comments=r["comments"], video_url=r["video_url"],
        thumbnail_url=r["thumbnail_url"], image_urls=json.loads(r["image_urls"] or "[]"), source=r["source"] or "",
    )
