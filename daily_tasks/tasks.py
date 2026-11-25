"""Работа с задачами в формате веб-приложения (те же правила, что в static/index.html).

Задача: {id, text, date, done, doneDay, created}. Невыполненная задача остаётся
в списке «сегодня», пока её не отметят (переносится сама, дата не меняется).
"""

from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class Task:
    id: str
    text: str
    done: bool
    moved: bool  # перенесена с прошлых дней


def uid() -> str:
    n = int(time.time() * 1000)
    digits = string.digits + string.ascii_lowercase
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out + "".join(random.choices(digits, k=5))


def _find(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return next((t for t in state["tasks"] if t.get("id") == task_id), None)


def today_tasks(state: dict[str, Any], today: date) -> list[Task]:
    """Как во вкладке «Сегодня»: невыполненные (включая перенесённые), затем сделанные сегодня."""
    t = today.isoformat()
    open_ = sorted(
        (x for x in state["tasks"] if not x.get("done") and x.get("date", t) <= t),
        key=lambda x: (x.get("date", t), x.get("created", 0)),
    )
    done = [x for x in state["tasks"] if x.get("done") and x.get("doneDay") == t]
    return [Task(x["id"], x["text"], bool(x.get("done")), x.get("date", t) < t) for x in open_ + done]


def day_tasks(state: dict[str, Any], day: date) -> list[Task]:
    """Задачи, назначенные на будущий день."""
    d = day.isoformat()
    items = sorted(
        (x for x in state["tasks"] if not x.get("done") and x.get("date") == d),
        key=lambda x: x.get("created", 0),
    )
    return [Task(x["id"], x["text"], False, False) for x in items]


def add(state: dict[str, Any], texts: list[str], day: date) -> list[str]:
    return add_items(state, [(t, day) for t in texts])


def add_items(state: dict[str, Any], items: list[tuple[str, date]]) -> list[str]:
    """Добавляет задачи [(текст, дата)] и возвращает их id."""
    now = int(time.time() * 1000)
    ids = []
    for i, (text, day) in enumerate(items):
        task_id = uid() + str(i)
        state["tasks"].append({
            "id": task_id,
            "text": text[:300],
            "date": day.isoformat(),
            "done": False,
            "doneDay": None,
            "created": now + i,
        })
        ids.append(task_id)
    return ids


def set_done(state: dict[str, Any], task_id: str, done: bool, today: date) -> bool:
    t = _find(state, task_id)
    if not t:
        return False
    t["done"] = done
    t["doneDay"] = today.isoformat() if done else None
    return True


def toggle(state: dict[str, Any], task_id: str, today: date) -> bool:
    t = _find(state, task_id)
    return bool(t) and set_done(state, task_id, not t.get("done"), today)


def delete(state: dict[str, Any], task_id: str) -> bool:
    before = len(state["tasks"])
    state["tasks"] = [x for x in state["tasks"] if x.get("id") != task_id]
    return len(state["tasks"]) < before


def move(state: dict[str, Any], task_id: str, day: date) -> bool:
    t = _find(state, task_id)
    if not t or t.get("done"):
        return False
    t["date"] = day.isoformat()
    return True


def done_stats(state: dict[str, Any], today: date, days: int = 7) -> list[tuple[date, int]]:
    start = today - timedelta(days=days - 1)
    counts: dict[str, int] = {}
    for x in state["tasks"]:
        if x.get("done") and x.get("doneDay"):
            counts[x["doneDay"]] = counts.get(x["doneDay"], 0) + 1
    return [(start + timedelta(days=i), counts.get((start + timedelta(days=i)).isoformat(), 0))
            for i in range(days)]


def daily_habits(state: dict[str, Any], today: date) -> list[tuple[str, bool]]:
    """Ежедневные привычки на сегодня: [(название, отмечена)]."""
    t = today.isoformat()
    log = state.get("log", {}).get(t, {})
    out = []
    for h in state.get("habits", []):
        if h.get("kind") != "daily" or h.get("start", t) > t or (h.get("stopped") and h["stopped"] <= t):
            continue
        out.append((h.get("name", ""), bool(log.get(h.get("id")))))
    return out
