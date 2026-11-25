"""Голосовые сообщения → задачи.

1. Речь в текст: OpenAI или Groq (оба понимают русский и казахский). Claude
   аудио не распознаёт, поэтому для этого шага нужен один из этих ключей.
2. Текст в задачи: если задан ANTHROPIC_API_KEY, Claude делит фразу на отдельные
   задачи и понимает даты («завтра», «в пятницу»). Без ключа — вся фраза
   становится задачами по строкам/«;».
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

STT_PROVIDERS = {
    # provider: (адрес, модель по умолчанию, переменная с ключом)
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo", "GROQ_API_KEY"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "gpt-4o-mini-transcribe", "OPENAI_API_KEY"),
}
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class TranscriptionError(Exception):
    pass


@dataclass(frozen=True)
class STTConfig:
    url: str
    model: str
    key: str
    provider: str


def stt_config() -> STTConfig | None:
    """Первый провайдер, для которого задан ключ (или явно выбранный в TASKS_STT_PROVIDER)."""
    wanted = os.environ.get("TASKS_STT_PROVIDER", "").strip().lower()
    order = [wanted] if wanted in STT_PROVIDERS else list(STT_PROVIDERS)
    for name in order:
        url, model, key_var = STT_PROVIDERS[name]
        key = os.environ.get(key_var, "").strip()
        if key:
            return STTConfig(url, os.environ.get("TASKS_STT_MODEL", "").strip() or model, key, name)
    return None


async def transcribe(audio: bytes, filename: str, cfg: STTConfig) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            cfg.url,
            headers={"Authorization": f"Bearer {cfg.key}"},
            data={"model": cfg.model},
            files={"file": (filename, audio, "audio/ogg")},
        )
    if r.status_code != 200:
        log.warning("Распознавание речи: %s %s", r.status_code, r.text[:300])
        raise TranscriptionError(f"сервис распознавания ответил {r.status_code}")
    text = (r.json().get("text") or "").strip()
    if not text:
        raise TranscriptionError("в сообщении не удалось разобрать речь")
    return text


# --- текст → задачи ---
class ParsedTask(BaseModel):
    text: str
    date: str  # YYYY-MM-DD


class ParsedTasks(BaseModel):
    tasks: list[ParsedTask]


SYSTEM = """Ты разбираешь надиктованное сообщение для списка дел.
Раздели его на отдельные короткие задачи. Сохрани язык и формулировки автора (русский, казахский или смесь), убери слова-паразиты и служебные фразы вроде «добавь задачу», «напомни».
Для каждой задачи укажи дату в формате YYYY-MM-DD: сегодня, если день не назван; иначе день, который назвал автор («завтра», «в пятницу», «25-го»). Дата не может быть раньше сегодняшней.
Если в сообщении нет ни одной задачи, верни пустой список."""


def simple_split(text: str) -> list[str]:
    parts = [p.strip(" .,-—") for chunk in text.splitlines() for p in chunk.split(";")]
    return [p[:300] for p in parts if p]


def _clamp(value: str, today: date) -> date:
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return today
    return d if today <= d <= today + timedelta(days=366) else today


async def extract_tasks(text: str, today: date) -> list[tuple[str, date]]:
    """[(задача, дата)]. С Claude — умный разбор, без него — простое деление."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return [(t, today) for t in simple_split(text)]
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.environ.get("TASKS_CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
    try:
        response = await client.messages.parse(
            model=model,
            max_tokens=2000,
            output_config={"effort": "low"},  # простая задача разбора
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Сегодня {today.isoformat()}, {WEEKDAYS[today.weekday()]}.\n\nСообщение:\n{text}",
            }],
            output_format=ParsedTasks,
        )
    except anthropic.APIError:
        log.exception("Claude не смог разобрать задачи — делю текст сам")
        return [(t, today) for t in simple_split(text)]
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return [(t, today) for t in simple_split(text)]
    out = [(t.text.strip()[:300], _clamp(t.date, today)) for t in response.parsed_output.tasks if t.text.strip()]
    return out
