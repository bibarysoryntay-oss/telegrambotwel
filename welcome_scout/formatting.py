"""Тексты сообщений Telegram (HTML)."""

from __future__ import annotations

import json
from html import escape

from .scoring import METRIC_LABELS, Candidate

TG_LIMIT = 4000

KIND_LABELS = {"reel": "Reels", "video": "Видео", "image": "Фото", "carousel": "Карусель"}


def fmt_num(n) -> str:
    return "нет данных" if n is None else f"{int(n):,}".replace(",", " ")


def fmt_age(hours: float) -> str:
    if hours < 48:
        return f"{hours:.0f} ч назад"
    return f"{hours / 24:.0f} дн. назад"


def metric_line(label: str, value, hidden_hint: str = "") -> str:
    if value is None:
        return f"{label}: <i>нет данных{hidden_hint}</i>"
    return f"{label}: {fmt_num(value)}"


def format_item(cand: Candidate, analysis: dict, basis: str, niche: str, unavailable: list[str],
                growth: str | None = None) -> str:
    p = cand.post
    head = (
        f"🔥 <b>Выброс ×{cand.ratio:.1f}</b>" if cand.signal == "outlier"
        else f"🚀 <b>Быстрый рост ×{cand.ratio:.1f}</b> (публикация ещё свежая)"
    )
    lines = [
        f"{head} · @{escape(p.username)}" + (f" ({escape(niche)})" if niche else ""),
        f"{KIND_LABELS.get(p.kind, p.kind)} · {fmt_age(cand.age_hours)}",
        "",
        f"📊 {METRIC_LABELS[cand.metric]}: <b>{fmt_num(cand.value)}</b> "
        f"(обычно ~{fmt_num(round(cand.baseline))}, медиана {cand.baseline_n} публикаций)",
    ]
    if p.is_video:
        lines.append(metric_line("👁 Просмотры", p.views, " — источник не отдал"))
    lines.append(metric_line("❤️ Лайки", p.likes, " — скрыты владельцем или недоступны"))
    lines.append(metric_line("💬 Комментарии", p.comments))
    if growth:
        lines.append(f"📈 {escape(growth)}")
    if unavailable:
        lines.append(f"<i>Недоступно через источник: {escape(', '.join(unavailable))}</i>")

    idea = analysis.get("idea", {})
    shots = "\n".join(f"  {i}. {escape(s)}" for i, s in enumerate(idea.get("shots", []), 1))
    hyps = "\n".join(f"• {escape(h)}" for h in analysis.get("hypotheses", []))
    lines += [
        "",
        f"🧩 <b>Формат:</b> {escape(analysis.get('format', ''))}",
        f"🎬 <b>Начало:</b> {escape(analysis.get('hook', ''))}",
        f"📖 <b>Сюжет:</b> {escape(analysis.get('plot', ''))}",
        f"🎨 <b>Подача:</b> {escape(analysis.get('delivery', ''))}",
        "",
        "💭 <b>Гипотезы, почему сработало</b> (предположения, не факты):",
        hyps,
        "",
        f"✈️ <b>Идея для Welcome Travel: {escape(idea.get('title', ''))}</b>",
        escape(idea.get("concept", "")),
        f"<b>Хук:</b> «{escape(idea.get('hook_line', ''))}»",
        "<b>Кадры:</b>",
        shots,
        f"<b>CTA:</b> {escape(idea.get('cta', ''))}",
        "",
        f"<i>ℹ️ Основа разбора: {escape(basis)}</i>",
        f'🔗 <a href="{escape(p.permalink)}">Открыть публикацию</a>',
    ]
    return "\n".join(lines)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режем по строкам, чтобы не ломать HTML-теги внутри строки."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def format_saved(rows) -> str:
    if not rows:
        return "Сохранённых идей пока нет."
    out = ["💾 <b>Сохранённые идеи</b>"]
    for r in rows:
        a = json.loads(r["analysis"] or "{}")
        idea = a.get("idea", {})
        out.append(
            f"\n<b>#{r['id']} {escape(idea.get('title', ''))}</b> — по мотивам "
            f'<a href="{escape(r["permalink"])}">@{escape(r["username"])}</a>\n{escape(idea.get("concept", ""))}'
        )
    out.append("\nЧтобы получить сценарий: /script &lt;номер&gt;")
    return "\n".join(out)
