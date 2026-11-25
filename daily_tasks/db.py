"""Хранилище задач в SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    day TEXT NOT NULL,              -- на какой день задача (YYYY-MM-DD)
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at TEXT,
    moved INTEGER NOT NULL DEFAULT 0 -- сколько раз переносилась с прошлых дней
);
CREATE INDEX IF NOT EXISTS idx_tasks_chat_day ON tasks(chat_id, day);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    reminders INTEGER NOT NULL DEFAULT 1
);
"""


@dataclass(frozen=True)
class Task:
    id: int
    text: str
    day: str
    done: bool
    moved: int


class TaskDB:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- чаты ---
    def register_chat(self, chat_id: int) -> None:
        self.conn.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (chat_id,))
        self.conn.commit()

    def set_reminders(self, chat_id: int, on: bool) -> None:
        self.register_chat(chat_id)
        self.conn.execute("UPDATE chats SET reminders=? WHERE chat_id=?", (int(on), chat_id))
        self.conn.commit()

    def reminders_on(self, chat_id: int) -> bool:
        row = self.conn.execute("SELECT reminders FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(row["reminders"]) if row else True

    def reminder_chats(self) -> list[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM chats WHERE reminders=1")]

    # --- задачи ---
    def add(self, chat_id: int, texts: list[str], day: date, now: datetime) -> list[int]:
        ids = []
        for text in texts:
            cur = self.conn.execute(
                "INSERT INTO tasks(chat_id, text, day, created_at) VALUES (?,?,?,?)",
                (chat_id, text, day.isoformat(), now.isoformat(timespec="seconds")),
            )
            ids.append(cur.lastrowid)
        self.conn.commit()
        return ids

    def roll_over(self, chat_id: int, today: date) -> int:
        """Переносит невыполненные задачи прошлых дней на сегодня. Возвращает их число."""
        cur = self.conn.execute(
            "UPDATE tasks SET day=?, moved=moved+1 WHERE chat_id=? AND done=0 AND day<?",
            (today.isoformat(), chat_id, today.isoformat()),
        )
        self.conn.commit()
        return cur.rowcount

    def for_day(self, chat_id: int, day: date) -> list[Task]:
        rows = self.conn.execute(
            "SELECT id, text, day, done, moved FROM tasks WHERE chat_id=? AND day=? ORDER BY id",
            (chat_id, day.isoformat()),
        )
        return [Task(r["id"], r["text"], r["day"], bool(r["done"]), r["moved"]) for r in rows]

    def get(self, chat_id: int, task_id: int) -> Task | None:
        r = self.conn.execute(
            "SELECT id, text, day, done, moved FROM tasks WHERE chat_id=? AND id=?", (chat_id, task_id)
        ).fetchone()
        return Task(r["id"], r["text"], r["day"], bool(r["done"]), r["moved"]) if r else None

    def set_done(self, chat_id: int, task_id: int, done: bool, now: datetime) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET done=?, done_at=? WHERE chat_id=? AND id=?",
            (int(done), now.isoformat(timespec="seconds") if done else None, chat_id, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def toggle(self, chat_id: int, task_id: int, now: datetime) -> bool:
        task = self.get(chat_id, task_id)
        if not task:
            return False
        return self.set_done(chat_id, task_id, not task.done, now)

    def delete(self, chat_id: int, task_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM tasks WHERE chat_id=? AND id=?", (chat_id, task_id))
        self.conn.commit()
        return cur.rowcount > 0

    def move(self, chat_id: int, task_id: int, day: date) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET day=? WHERE chat_id=? AND id=? AND done=0", (day.isoformat(), chat_id, task_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def clear_done(self, chat_id: int, day: date) -> int:
        cur = self.conn.execute(
            "DELETE FROM tasks WHERE chat_id=? AND day=? AND done=1", (chat_id, day.isoformat())
        )
        self.conn.commit()
        return cur.rowcount

    def stats(self, chat_id: int, today: date, days: int = 7) -> list[tuple[date, int]]:
        """[(день, сколько задач выполнено в этот день)] за последние `days` дней, от старых к новым."""
        start = today - timedelta(days=days - 1)
        rows = self.conn.execute(
            "SELECT substr(done_at, 1, 10) AS d, COUNT(*) AS n FROM tasks "
            "WHERE chat_id=? AND done=1 AND substr(done_at, 1, 10) BETWEEN ? AND ? GROUP BY d",
            (chat_id, start.isoformat(), today.isoformat()),
        )
        by_day = {r["d"]: r["n"] for r in rows}
        return [(start + timedelta(days=i), by_day.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]
