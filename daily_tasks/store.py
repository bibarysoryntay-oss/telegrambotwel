"""Хранилище данных приложения «Мои задачи»: один JSON на пользователя.

Формат JSON совпадает с тем, что использует веб-приложение (static/index.html):
{tasks: [...], habits: [...], log: {...}, history: {...}, food: {...}, ...}.
Бот и приложение работают с одними и теми же данными.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    reminders INTEGER NOT NULL DEFAULT 1
);
"""

MAX_STATE_BYTES = 2_000_000


def empty_state() -> dict[str, Any]:
    return {"tasks": [], "habits": [], "log": {}, "history": {}, "food": {}, "frozenUntil": None}


class Conflict(Exception):
    """Данные уже изменились (на другом устройстве или через бота)."""

    def __init__(self, data: str, version: int):
        self.data, self.version = data, version


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- чаты и напоминания ---
    def register_chat(self, chat_id: int) -> None:
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (chat_id,))
            self.conn.commit()

    def set_reminders(self, chat_id: int, on: bool) -> None:
        self.register_chat(chat_id)
        with self.lock:
            self.conn.execute("UPDATE chats SET reminders=? WHERE chat_id=?", (int(on), chat_id))
            self.conn.commit()

    def reminders_on(self, chat_id: int) -> bool:
        row = self.conn.execute("SELECT reminders FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(row["reminders"]) if row else True

    def reminder_chats(self) -> list[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM chats WHERE reminders=1")]

    # --- данные приложения ---
    def get_raw(self, user_id: int) -> tuple[str | None, int]:
        """(JSON или None, если данных ещё нет; версия)."""
        row = self.conn.execute(
            "SELECT data, version FROM app_state WHERE user_id=?", (user_id,)
        ).fetchone()
        return (row["data"], row["version"]) if row else (None, 0)

    def get(self, user_id: int) -> tuple[dict[str, Any], int]:
        raw, version = self.get_raw(user_id)
        if raw is None:
            return self._import_legacy(user_id) or empty_state(), 0
        state = json.loads(raw)
        for key, default in empty_state().items():
            state.setdefault(key, default)
        return state, version

    def put_raw(self, user_id: int, data: str, base_version: int) -> int:
        """Сохраняет JSON от приложения. Если base_version устарела — Conflict."""
        if len(data.encode()) > MAX_STATE_BYTES:
            raise ValueError("слишком большие данные")
        parsed = json.loads(data)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tasks"), list):
            raise ValueError("неверный формат данных")
        with self.lock:
            raw, version = self.get_raw(user_id)
            if version != base_version:
                raise Conflict(raw or json.dumps(empty_state()), version)
            return self._write(user_id, data, version)

    @contextmanager
    def edit(self, user_id: int):
        """Изменение данных из бота: `with store.edit(uid) as state: ...`."""
        with self.lock:
            state, version = self.get(user_id)
            yield state
            self._write(user_id, json.dumps(state, ensure_ascii=False), version)

    def _write(self, user_id: int, data: str, version: int) -> int:
        new_version = version + 1
        self.conn.execute(
            "INSERT INTO app_state(user_id, data, version, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, version=excluded.version, "
            "updated_at=excluded.updated_at",
            (user_id, data, new_version, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return new_version

    def _import_legacy(self, user_id: int) -> dict[str, Any] | None:
        """Задачи из первой версии бота (таблица tasks) переносим в новый формат."""
        try:
            rows = self.conn.execute(
                "SELECT id, text, day, done, created_at, done_at FROM tasks WHERE chat_id=? ORDER BY id",
                (user_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        if not rows:
            return None
        state = empty_state()
        for r in rows:
            created = datetime.fromisoformat(r["created_at"])
            state["tasks"].append({
                "id": f"old{r['id']}",
                "text": r["text"],
                "date": r["day"],
                "done": bool(r["done"]),
                "doneDay": (r["done_at"] or r["day"])[:10] if r["done"] else None,
                "created": int(created.timestamp() * 1000),
            })
        return state
