"""Голосовые сообщения → задачи.

1. Речь в текст: OpenAI или Groq (оба понимают русский и казахский). Claude
   аудио не распознаёт, поэтому для этого шага нужен один из этих ключей.
2. Текст в задачи: если задан ANTHROPIC_API_KEY, Claude делит фразу на отдельные
   задачи и понимает даты («завтра», «в пятницу»). Без ключа — вся фраза
   становится задачами по строкам/«;».
"""

from __future__ import annotations

import logging
import calendar
import os
import re
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


async def transcribe(audio: bytes, filename: str, cfg: STTConfig, mime: str = "audio/ogg") -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            cfg.url,
            headers={"Authorization": f"Bearer {cfg.key}"},
            data={"model": cfg.model},
            files={"file": (filename, audio, mime)},
        )
    if r.status_code != 200:
        log.warning("Распознавание речи: %s %s", r.status_code, r.text[:300])
        raise TranscriptionError(f"сервис распознавания ответил {r.status_code}")
    text = (r.json().get("text") or "").strip()
    if not text:
        raise TranscriptionError("в сообщении не удалось разобрать речь")
    return text


def filename_for(mime: str) -> str:
    """Имя файла с правильным расширением — по нему сервис понимает формат."""
    mime = (mime or "").lower()
    for key, ext in (("webm", "webm"), ("mp4", "mp4"), ("m4a", "m4a"), ("aac", "m4a"),
                     ("mpeg", "mp3"), ("wav", "wav"), ("ogg", "ogg")):
        if key in mime:
            return f"voice.{ext}"
    return "voice.webm"


# --- текст → задачи ---
class ParsedTask(BaseModel):
    text: str
    date: str  # YYYY-MM-DD
    monthly: bool  # повторять каждый месяц в это число


class ParsedTasks(BaseModel):
    tasks: list[ParsedTask]


SYSTEM = """Ты разбираешь надиктованное сообщение для списка дел.
Раздели его на отдельные короткие задачи. Сохрани язык и формулировки автора (русский, казахский или смесь), убери слова-паразиты и служебные фразы вроде «добавь задачу», «напомни».
Для каждой задачи укажи дату в формате YYYY-MM-DD: сегодня, если день не назван; иначе день, который назвал автор («завтра», «в пятницу», «25-го»). Дата не может быть раньше сегодняшней.
Если автор говорит, что задачу надо делать каждый месяц («каждый месяц», «ежемесячно», «ай сайын», «каждое 5-е число»), поставь monthly = true и дату ближайшего такого дня (сегодня или позже); иначе monthly = false.
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


MONTHLY_WORDS = re.compile(r"(каждый месяц|ежемесячн|ай сайын|әр ай)[\s,.:—-]*", re.I)


DAY_OF_MONTH = re.compile(r"\b(\d{1,2})[\s-]*(?:го|числа|-?е)\b[\s,.:—-]*", re.I)


def _nearest_day(day: int, today: date) -> date:
    """Ближайшее N-е число, начиная с сегодняшнего дня."""
    from .tasks import next_monthly

    if 1 <= day <= 31:
        this = today.replace(day=min(day, calendar.monthrange(today.year, today.month)[1]))
        return this if this >= today else next_monthly(this, day)
    return today


def simple_items(text: str, today: date) -> list[tuple[str, date, bool]]:
    """Без Claude: делим по строкам и «;». «Каждый месяц 5-го …» — ежемесячная на 5-е число."""
    out = []
    for part in simple_split(text):
        monthly = bool(MONTHLY_WORDS.search(part))
        clean, day = part, today
        if monthly:
            clean = MONTHLY_WORDS.sub("", clean)
            m = DAY_OF_MONTH.search(clean)
            if m:
                day = _nearest_day(int(m.group(1)), today)
                clean = DAY_OF_MONTH.sub("", clean, count=1)
            clean = clean.strip(" .,-—")
        if clean:
            out.append((clean[:1].upper() + clean[1:], day, monthly))
    return out


async def extract_tasks(text: str, today: date) -> list[tuple[str, date, bool]]:
    """[(задача, дата, ежемесячная)]. С Claude — умный разбор, без него — простое деление."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return simple_items(text, today)
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
        return simple_items(text, today)
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return simple_items(text, today)
    return [(t.text.strip()[:300], _clamp(t.date, today), t.monthly)
            for t in response.parsed_output.tasks if t.text.strip()]
