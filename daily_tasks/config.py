"""Настройки бота задач из .env (общий файл с основным ботом)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _ids(raw: str) -> tuple[int, ...]:
    return tuple(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Settings:
    token: str = field(default_factory=lambda: _env("TASKS_BOT_TOKEN"))
    # Пусто — ботом может пользоваться любой (у каждого свой список задач).
    allowed_chat_ids: tuple[int, ...] = field(default_factory=lambda: _ids(_env("TASKS_ALLOWED_CHAT_IDS")))
    timezone: str = field(default_factory=lambda: _env("TASKS_TIMEZONE", _env("TIMEZONE", "Asia/Almaty")))
    morning_time: str = field(default_factory=lambda: _env("TASKS_MORNING_TIME", "09:00"))
    evening_time: str = field(default_factory=lambda: _env("TASKS_EVENING_TIME", "21:00"))
    db_path: str = field(default_factory=lambda: _env("TASKS_DB_PATH", "data/tasks.db"))
    # Приложение в Telegram (Mini App). Railway сам задаёт PORT и RAILWAY_PUBLIC_DOMAIN.
    web_port: int = field(default_factory=lambda: int(_env("PORT", _env("TASKS_WEB_PORT", "8080"))))
    webapp_url: str = field(default_factory=lambda: _env("TASKS_WEBAPP_URL") or (
        f"https://{_env('RAILWAY_PUBLIC_DOMAIN')}/" if _env("RAILWAY_PUBLIC_DOMAIN") else ""
    ))
