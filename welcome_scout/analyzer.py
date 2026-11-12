"""Разбор публикации и генерация сценария через Claude API."""

from __future__ import annotations

import base64
import json
import logging

import anthropic

from .media import Visuals
from .models import Post
from .scoring import METRIC_LABELS, Candidate

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format", "hook", "plot", "delivery", "hypotheses", "idea", "adaptability", "adaptability_reason"],
    "properties": {
        "format": {"type": "string", "description": "Короткое название формата, напр. «POV-история», «до/после», «топ-3»"},
        "hook": {"type": "string", "description": "Что происходит в первые 1–3 секунды: кадр, текст на экране, действие. Только наблюдаемое."},
        "plot": {"type": "string", "description": "Сюжет/структура ролика или поста в 2–4 предложениях"},
        "delivery": {"type": "string", "description": "Подача: съёмка, монтаж, текст на экране, тон подписи, CTA. Только наблюдаемое."},
        "hypotheses": {
            "type": "array", "items": {"type": "string"},
            "description": "Гипотезы, почему публикация сработала. Это предположения, не факты.",
        },
        "idea": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "concept", "hook_line", "shots", "cta"],
            "properties": {
                "title": {"type": "string"},
                "concept": {"type": "string", "description": "Как применить приём для Welcome Travel: тур/отель/направление, 2–4 предложения"},
                "hook_line": {"type": "string", "description": "Готовая фраза/текст на экране для первых секунд"},
                "shots": {"type": "array", "items": {"type": "string"}, "description": "3–6 кадров/сцен"},
                "cta": {"type": "string"},
            },
        },
        "adaptability": {"type": "integer", "description": "1–5: насколько реально и уместно адаптировать для турагентства"},
        "adaptability_reason": {"type": "string"},
    },
}

SYSTEM_ANALYSIS = """Ты — контент-стратег туристического агентства Welcome Travel (Казахстан). \
Тебе показывают публикацию из Instagram другого бизнеса (не обязательно туристического), которая сработала \
заметно лучше обычного для этого аккаунта. Задача — понять приём и перенести его на туры, отели и отдых.

Правила:
- Пиши по-русски, коротко и конкретно.
- В полях hook, plot, delivery описывай только то, что видно на кадрах и в подписи. Если чего-то не видно \
(например, звук, речь, музыка, середина ролика), прямо скажи, что это не видно — не выдумывай.
- Причины успеха давай только как гипотезы. Учитывай, что всплеск может объясняться внешними факторами \
(реклама, коллаборация, тренд), которых мы не видим.
- Идея должна быть реализуемой силами небольшого агентства и опираться на профиль бизнеса ниже. \
Переносить нужно приём (структуру, хук, подачу), а не копировать чужой контент.

Профиль бизнеса:
{profile}"""


def _media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _fmt(n) -> str:
    return "нет данных" if n is None else f"{n:,}".replace(",", " ")


def _text_of(response) -> str:
    if response.stop_reason == "refusal":
        raise RuntimeError("Модель отказалась обрабатывать этот запрос.")
    return "".join(b.text for b in response.content if b.type == "text").strip()


class Analyzer:
    def __init__(self, model: str, business_profile: str):
        self.client = anthropic.Anthropic()
        self.model = model
        self.profile = business_profile

    def _create(self, **kwargs):
        # Серверный fallback: если модель отклонит запрос, API сам повторит его на запасной модели.
        return self.client.beta.messages.create(
            model=self.model, betas=[FALLBACK_BETA], fallbacks="default", **kwargs
        )

    def analyze(self, cand: Candidate, visuals: Visuals, niche: str, followers: int | None,
                liked: list[str], disliked: list[str]) -> dict:
        p = cand.post
        facts = [
            f"Аккаунт: @{p.username} (ниша: {niche or 'не указана'}, подписчиков: {_fmt(followers)})",
            f"Тип: {p.kind}. Опубликовано {cand.age_hours:.0f} ч назад.",
            f"Просмотры: {_fmt(p.views)}; лайки: {_fmt(p.likes)}; комментарии: {_fmt(p.comments)}",
            f"Сравнение: {METRIC_LABELS[cand.metric]} = {_fmt(cand.value)}, медиана аккаунта по "
            f"{cand.baseline_n} похожим публикациям = {_fmt(round(cand.baseline))}, т.е. ×{cand.ratio:.1f}.",
            f"Что доступно для анализа: {visuals.basis}",
            f"Подпись к публикации:\n{p.caption[:2000] or '(без подписи)'}",
        ]
        if liked:
            facts.append("Пользователю раньше нравились такие идеи: " + "; ".join(liked[:8]))
        if disliked:
            facts.append("Пользователю были неинтересны: " + "; ".join(disliked[:8]))

        content: list[dict] = []
        for label, data in visuals.images:
            content.append({"type": "text", "text": label})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": _media_type(data), "data": base64.standard_b64encode(data).decode()},
            })
        content.append({"type": "text", "text": "\n".join(facts) + "\n\nСделай разбор по схеме."})

        response = self._create(
            max_tokens=16000,
            system=SYSTEM_ANALYSIS.format(profile=self.profile),
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        return json.loads(_text_of(response))

    def write_script(self, post: Post, analysis: dict, tour: str, language: str) -> str:
        lang = "казахском" if language == "kk" else "русском"
        prompt = f"""Напиши сценарий короткого вертикального ролика (Reels, 15–40 секунд) для Instagram Welcome Travel на {lang} языке.

Тур/предложение, о котором ролик:
{tour}

Приём, который берём за основу (из публикации @{post.username}, {post.permalink}):
- Формат: {analysis.get('format')}
- Хук оригинала: {analysis.get('hook')}
- Сюжет: {analysis.get('plot')}
- Подача: {analysis.get('delivery')}
- Наша идея адаптации: {json.dumps(analysis.get('idea', {}), ensure_ascii=False)}

Структура ответа:
1. Название ролика.
2. Хук (0–3 с): что в кадре + текст на экране + первая фраза.
3. Раскадровка по секундам: кадр, действие, текст на экране, закадровый текст.
4. Подпись к публикации с CTA и 3–5 хэштегами.
5. Что нужно отснять (список).

Не придумывай факты о туре, которых нет во вводных (цены, даты, услуги) — оставляй [вставить ...].
Пиши обычным текстом без Markdown-разметки."""
        response = self._create(
            max_tokens=16000,
            system=SYSTEM_ANALYSIS.format(profile=self.profile),
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
        return _text_of(response)
