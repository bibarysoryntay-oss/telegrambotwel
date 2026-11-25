"""Тексты и клавиатуры бота задач."""

from __future__ import annotations

from datetime import date
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .db import Task

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
SHORT_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MAX_TASK_LEN = 200


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


def parse_tasks(text: str) -> list[str]:
    """Каждая непустая строка — отдельная задача. Маркеры списков в начале строки убираются."""
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•*–—▫️☐").strip()
        # «1. купить», «2) позвонить»
        head, sep, rest = line.partition(" ")
        if sep and head.rstrip(".)").isdigit() and head[-1] in ".)":
            line = rest.strip()
        if line:
            out.append(line[:MAX_TASK_LEN])
    return out


def progress_bar(done: int, total: int, width: int = 10) -> str:
    if total == 0:
        return ""
    filled = round(width * done / total)
    return "▓" * filled + "░" * (width - filled) + f" {done}/{total}"


def task_list_text(tasks: list[Task], day: date, title: str = "Задачи на") -> str:
    header = f"<b>{title} {escape(human_date(day))}</b>"
    if not tasks:
        return header + "\n\nПока пусто. Просто напишите задачу сообщением — каждая строка станет отдельной задачей."
    lines = [header, ""]
    for i, t in enumerate(tasks, 1):
        mark = "✅" if t.done else "⬜"
        text = f"<s>{escape(t.text)}</s>" if t.done else escape(t.text)
        moved = " ↪️" if t.moved and not t.done else ""
        lines.append(f"{mark} {i}. {text}{moved}")
    done = sum(t.done for t in tasks)
    lines += ["", progress_bar(done, len(tasks))]
    if done == len(tasks):
        lines.append("🎉 Всё выполнено!")
    return "\n".join(lines)


def _short(text: str, n: int = 28) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def task_list_keyboard(tasks: list[Task], mode: str = "done") -> InlineKeyboardMarkup | None:
    """mode: done — отмечать выполнение, edit — удалять/переносить на завтра."""
    rows: list[list[InlineKeyboardButton]] = []
    for i, t in enumerate(tasks, 1):
        if mode == "edit":
            rows.append([
                InlineKeyboardButton(f"🗑 {i}. {_short(t.text, 20)}", callback_data=f"del:{t.id}"),
                InlineKeyboardButton("➡️ завтра", callback_data=f"tmr:{t.id}"),
            ])
        else:
            mark = "✅" if t.done else "⬜"
            rows.append([InlineKeyboardButton(f"{mark} {i}. {_short(t.text)}", callback_data=f"tg:{t.id}")])
    if mode == "edit":
        rows.append([InlineKeyboardButton("✔️ Готово", callback_data="mode:done")])
    elif tasks:
        rows.append([
            InlineKeyboardButton("✏️ Изменить", callback_data="mode:edit"),
            InlineKeyboardButton("🔄 Обновить", callback_data="mode:done"),
        ])
    return InlineKeyboardMarkup(rows) if rows else None


def evening_text(tasks: list[Task], day: date) -> str:
    done = [t for t in tasks if t.done]
    left = [t for t in tasks if not t.done]
    lines = [f"<b>🌙 Итоги дня · {escape(human_date(day))}</b>", ""]
    if not tasks:
        return lines[0] + "\n\nСегодня задач не было. Завтра утром пришлю напоминание."
    lines.append(f"Выполнено: {progress_bar(len(done), len(tasks))}")
    if left:
        lines += ["", "Не успели (перенесу на завтра):"]
        lines += [f"⬜ {escape(t.text)}" for t in left]
    else:
        lines += ["", "🎉 Все задачи выполнены. Отличный день!"]
    return "\n".join(lines)


def stats_text(stats: list[tuple[date, int]], today_done: int, today_total: int) -> str:
    peak = max((n for _, n in stats), default=0) or 1
    lines = ["<b>📊 Выполнено за 7 дней</b>", ""]
    for d, n in stats:
        bar = "▇" * round(8 * n / peak) if n else "·"
        lines.append(f"<code>{SHORT_WD[d.weekday()]} {d.day:02d}.{d.month:02d}</code> {bar} {n}")
    total = sum(n for _, n in stats)
    lines += ["", f"Всего: {total}, в среднем {total / len(stats):.1f} в день"]
    if today_total:
        lines.append(f"Сегодня: {today_done} из {today_total}")
    return "\n".join(lines)
