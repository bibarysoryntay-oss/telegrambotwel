import hashlib
import hmac
import json
import sqlite3
import time
from datetime import date
from urllib.parse import urlencode

import pytest

from daily_tasks import tasks as T
from daily_tasks.formatting import parse_tasks, progress_bar, task_list_keyboard, task_list_text
from daily_tasks.store import Conflict, Store
from daily_tasks.web import check_init_data

TODAY = date(2026, 11, 25)
YESTERDAY = date(2026, 11, 24)
TOKEN = "123:abc"


def sign_init_data(user_id: int, token: str = TOKEN, auth_date: int | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps({"id": user_id}), "query_id": "q"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_parse_tasks_lines_and_markers():
    assert parse_tasks("Купить хлеб\n\n- позвонить маме\n2) отчёт\n3. спорт") == [
        "Купить хлеб", "позвонить маме", "отчёт", "спорт",
    ]
    assert parse_tasks("2026 год итоги") == ["2026 год итоги"]


def test_tasks_carry_over_without_changing_date():
    state = {"tasks": []}
    T.add(state, ["вчерашняя"], YESTERDAY)
    T.add(state, ["сегодняшняя"], TODAY)
    items = T.today_tasks(state, TODAY)
    assert [t.text for t in items] == ["вчерашняя", "сегодняшняя"]
    assert items[0].moved and not items[1].moved
    assert state["tasks"][0]["date"] == YESTERDAY.isoformat()  # как в приложении: дата не трогается


def test_toggle_delete_move():
    state = {"tasks": []}
    T.add(state, ["a", "b"], TODAY)
    a, b = (t.id for t in T.today_tasks(state, TODAY))
    T.toggle(state, a, TODAY)
    assert state["tasks"][0]["done"] and state["tasks"][0]["doneDay"] == TODAY.isoformat()
    assert [t.done for t in T.today_tasks(state, TODAY)] == [False, True]  # сделанные — в конце
    T.toggle(state, a, TODAY)
    assert state["tasks"][0]["doneDay"] is None
    assert T.move(state, b, date(2026, 11, 26))
    assert [t.text for t in T.day_tasks(state, date(2026, 11, 26))] == ["b"]
    assert T.delete(state, a) and not T.delete(state, "нет")


def test_daily_habits_and_stats():
    state = {"tasks": [], "habits": [
        {"id": "h1", "name": "Duolingo", "kind": "daily", "start": "2026-11-01", "stopped": None},
        {"id": "h2", "name": "Спорт", "kind": "weekly", "start": "2026-11-01", "stopped": None},
        {"id": "h3", "name": "Старая", "kind": "daily", "start": "2026-11-01", "stopped": "2026-11-20"},
    ], "log": {"2026-11-25": {"h1": True}}}
    assert T.daily_habits(state, TODAY) == [("Duolingo", True)]
    T.add(state, ["x"], TODAY)
    T.set_done(state, state["tasks"][0]["id"], True, TODAY)
    assert T.done_stats(state, TODAY)[-1] == (TODAY, 1)


def test_store_versions_and_conflict():
    s = Store(":memory:")
    assert s.get_raw(1) == (None, 0)
    v1 = s.put_raw(1, json.dumps({"tasks": []}), 0)
    with s.edit(1) as state:  # бот добавляет задачу
        T.add(state, ["из бота"], TODAY)
    with pytest.raises(Conflict) as e:  # приложение со старой версией
        s.put_raw(1, json.dumps({"tasks": []}), v1)
    assert "из бота" in e.value.data and e.value.version == v1 + 1
    assert s.put_raw(1, e.value.data, e.value.version) == v1 + 2
    with pytest.raises(ValueError):
        s.put_raw(1, json.dumps({"no": "tasks"}), v1 + 2)
    assert s.get_raw(2) == (None, 0)  # у другого пользователя своё


def test_store_imports_first_version_tasks(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, chat_id INT, text TEXT, day TEXT, done INT, "
                 "created_at TEXT, done_at TEXT, moved INT DEFAULT 0)")
    conn.execute("INSERT INTO tasks VALUES (1, 7, 'старая', '2026-11-24', 0, '2026-11-24T10:00:00', NULL, 0)")
    conn.execute("INSERT INTO tasks VALUES (2, 7, 'сделано', '2026-11-24', 1, '2026-11-24T10:00:00', '2026-11-24T12:00:00', 0)")
    conn.commit()
    state, version = Store(db).get(7)
    assert version == 0 and [t["text"] for t in state["tasks"]] == ["старая", "сделано"]
    assert state["tasks"][1]["doneDay"] == "2026-11-24"


def test_init_data_check():
    assert check_init_data(sign_init_data(42), TOKEN) == 42
    assert check_init_data(sign_init_data(42), "999:other") is None  # чужой токен
    assert check_init_data(sign_init_data(42).replace("42", "43"), TOKEN) is None  # подмена id
    assert check_init_data(sign_init_data(42, auth_date=int(time.time()) - 40 * 86400), TOKEN) is None
    assert check_init_data("", TOKEN) is None


def test_formatting():
    state = {"tasks": []}
    T.add(state, ["<b>тег</b>", "y"], TODAY)
    T.set_done(state, state["tasks"][0]["id"], True, TODAY)
    items = T.today_tasks(state, TODAY)
    text = task_list_text(items, TODAY, habits=[("Duolingo", False)])
    assert "&lt;b&gt;" in text and "1/2" in text and "25 ноября" in text and "Duolingo" in text
    kb = task_list_keyboard(items, webapp_url="https://example.com/")
    assert kb.inline_keyboard[0][0].callback_data == f"tg:{items[0].id}"
    assert kb.inline_keyboard[-1][0].web_app.url == "https://example.com/"
    assert progress_bar(0, 0) == ""


def test_voice_fallback_without_claude(monkeypatch):
    import asyncio

    from daily_tasks import voice as V

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    items = asyncio.run(V.extract_tasks("купить хлеб; позвонить маме", TODAY))
    assert items == [("купить хлеб", TODAY), ("позвонить маме", TODAY)]
    assert V._clamp("2026-11-20", TODAY) == TODAY and V._clamp("мусор", TODAY) == TODAY
    assert V._clamp("2026-11-27", TODAY) == date(2026, 11, 27)


def test_stt_config(monkeypatch):
    from daily_tasks import voice as V

    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "TASKS_STT_PROVIDER", "TASKS_STT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert V.stt_config() is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert V.stt_config().provider == "openai"
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    assert V.stt_config().provider == "groq"
    monkeypatch.setenv("TASKS_STT_PROVIDER", "openai")
    assert V.stt_config().provider == "openai"
