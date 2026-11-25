from datetime import date, datetime

from daily_tasks.db import TaskDB
from daily_tasks.formatting import parse_tasks, progress_bar, task_list_keyboard, task_list_text

NOW = datetime(2026, 11, 25, 10, 0)
TODAY = date(2026, 11, 25)


def test_parse_tasks_lines_and_markers():
    assert parse_tasks("Купить хлеб\n\n- позвонить маме\n2) отчёт\n3. спорт") == [
        "Купить хлеб", "позвонить маме", "отчёт", "спорт",
    ]
    assert parse_tasks("   ") == []
    assert parse_tasks("2026 год итоги") == ["2026 год итоги"]


def test_add_toggle_delete():
    db = TaskDB(":memory:")
    ids = db.add(1, ["a", "b"], TODAY, NOW)
    db.add(2, ["чужая"], TODAY, NOW)
    assert [t.text for t in db.for_day(1, TODAY)] == ["a", "b"]
    db.toggle(1, ids[0], NOW)
    assert db.get(1, ids[0]).done
    db.toggle(1, ids[0], NOW)
    assert not db.get(1, ids[0]).done
    assert not db.delete(2, ids[1])  # чужую задачу удалить нельзя
    assert db.delete(1, ids[1])
    assert len(db.for_day(1, TODAY)) == 1


def test_roll_over_moves_only_unfinished():
    db = TaskDB(":memory:")
    yesterday = date(2026, 11, 24)
    a, b = db.add(1, ["сделано", "не сделано"], yesterday, NOW)
    db.set_done(1, a, True, NOW)
    assert db.roll_over(1, TODAY) == 1
    today = db.for_day(1, TODAY)
    assert [t.text for t in today] == ["не сделано"] and today[0].moved == 1
    assert [t.text for t in db.for_day(1, yesterday)] == ["сделано"]


def test_stats_and_clear():
    db = TaskDB(":memory:")
    a, _ = db.add(1, ["x", "y"], TODAY, NOW)
    db.set_done(1, a, True, NOW)
    stats = db.stats(1, TODAY)
    assert len(stats) == 7 and stats[-1] == (TODAY, 1)
    assert db.clear_done(1, TODAY) == 1


def test_formatting():
    db = TaskDB(":memory:")
    a, _ = db.add(1, ["<b>тег</b>", "y"], TODAY, NOW)
    db.set_done(1, a, True, NOW)
    tasks = db.for_day(1, TODAY)
    text = task_list_text(tasks, TODAY)
    assert "&lt;b&gt;" in text and "1/2" in text and "25 ноября" in text
    kb = task_list_keyboard(tasks)
    assert kb.inline_keyboard[0][0].callback_data == f"tg:{a}"
    assert progress_bar(0, 0) == ""
