"""Сбор данных, отбор кандидатов, анализ — без привязки к Telegram (можно запускать из CLI)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .analyzer import Analyzer
from .config import Settings
from .db import DB
from .media import collect_visuals
from .models import SourceError
from .scoring import Candidate, find_candidates, pick_diverse
from .sources import get_source

log = logging.getLogger(__name__)

MIN_ADAPTABILITY = 3


@dataclass
class DigestEntry:
    item_id: int
    candidate: Candidate
    analysis: dict
    basis: str
    niche: str
    unavailable: list[str]
    growth: str | None


@dataclass
class DigestResult:
    entries: list[DigestEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accounts_checked: int = 0
    candidates_found: int = 0
    skipped_low_fit: int = 0
    analysis_failures: int = 0


def refresh(db: DB, settings: Settings) -> list[str]:
    """Обновляет публикации всех активных аккаунтов. Возвращает список ошибок."""
    errors = []
    for acc in db.active_accounts():
        username = acc["username"]
        try:
            source = get_source(acc["source"], settings)
            snap = source.fetch(username, settings.posts_per_account)
        except SourceError as e:
            db.mark_fetch(username, None, str(e), None)
            errors.append(str(e))
            continue
        except Exception as e:  # сеть и прочее — не валим всю подборку
            log.exception("fetch failed for %s", username)
            db.mark_fetch(username, None, f"{type(e).__name__}: {e}", None)
            errors.append(f"@{username}: {type(e).__name__}: {e}")
            continue
        db.upsert_posts(snap.posts)
        db.mark_fetch(username, snap.followers, None, snap.unavailable)
    return errors


def growth_note(db: DB, cand: Candidate) -> str | None:
    prev = db.previous_snapshot(cand.post.id)
    if not prev:
        return None
    field_name = {"views": "views", "likes+comments": "likes", "comments": "comments"}[cand.metric]
    before = prev[field_name]
    now_val = getattr(cand.post, field_name)
    if before is None or now_val is None:
        return None
    hours = (datetime.now(timezone.utc) - datetime.fromisoformat(prev["fetched_at"])).total_seconds() / 3600
    label = {"views": "просмотров", "likes": "лайков", "comments": "комментариев"}[field_name]
    return f"С прошлой проверки (~{hours:.0f} ч назад): +{now_val - before:,} {label}".replace(",", " ")


def all_candidates(db: DB, settings: Settings) -> list[Candidate]:
    seen = db.seen_post_ids()
    cands: list[Candidate] = []
    for acc in db.active_accounts():
        found = find_candidates(
            db.posts_for(acc["username"]),
            outlier_min_ratio=settings.outlier_min_ratio,
            rising_min_ratio=settings.rising_min_ratio,
            rising_max_age_hours=settings.rising_max_age_hours,
            mature_min_age_hours=settings.mature_min_age_hours,
            lookback_days=settings.lookback_days,
            min_baseline_posts=settings.min_baseline_posts,
        )
        cands += [c for c in found if c.post.id not in seen]
    cands.sort(key=lambda c: c.ratio, reverse=True)
    return cands


def build_digest(db: DB, settings: Settings, analyzer: Analyzer) -> DigestResult:
    res = DigestResult()
    res.errors = refresh(db, settings)
    res.accounts_checked = len(db.active_accounts())
    cands = all_candidates(db, settings)
    res.candidates_found = len(cands)

    liked = [json.loads(r["analysis"] or "{}").get("format", "") for r in db.items_with_feedback("saved", 10)]
    disliked = [json.loads(r["analysis"] or "{}").get("format", "") for r in db.items_with_feedback("not_interested", 10)]

    for cand in pick_diverse(cands, settings.max_analyses_per_run):
        if len(res.entries) >= settings.digest_size:
            break
        acc = db.account(cand.post.username)
        niche = acc["niche"] if acc else ""
        visuals = collect_visuals(cand.post)
        try:
            analysis = analyzer.analyze(cand, visuals, niche, acc["followers"] if acc else None,
                                        [x for x in liked if x], [x for x in disliked if x])
        except Exception as e:
            log.exception("analysis failed for %s", cand.post.id)
            res.analysis_failures += 1
            res.errors.append(f"Не удалось разобрать {cand.post.permalink}: {type(e).__name__}")
            continue  # не помечаем как просмотренную — попробуем в следующий раз
        if int(analysis.get("adaptability", 0)) < MIN_ADAPTABILITY:
            db.save_item(cand.post.id, cand.signal, cand.ratio, cand.metric, cand.baseline, analysis, "skipped")
            res.skipped_low_fit += 1
            continue
        item_id = db.save_item(cand.post.id, cand.signal, cand.ratio, cand.metric, cand.baseline, analysis, "sent")
        res.entries.append(DigestEntry(
            item_id, cand, analysis, visuals.basis, niche,
            json.loads(acc["unavailable"] or "[]") if acc else [], growth_note(db, cand),
        ))
    return res


@dataclass
class AccessReport:
    username: str
    source: str
    ok: bool
    message: str


def check_access(db: DB, settings: Settings, limit: int = 12) -> list[AccessReport]:
    """Проверяет каждый аккаунт и честно показывает, какие метрики реально доступны."""
    reports = []
    for acc in db.active_accounts():
        username = acc["username"]
        src_name = acc["source"] or settings.default_source
        try:
            snap = get_source(src_name, settings).fetch(username, limit)
        except SourceError as e:
            reports.append(AccessReport(username, src_name, False, str(e)))
            continue
        except Exception as e:
            reports.append(AccessReport(username, src_name, False, f"{type(e).__name__}: {e}"))
            continue
        db.upsert_posts(snap.posts)
        db.mark_fetch(username, snap.followers, None, snap.unavailable)
        posts = snap.posts
        videos = [p for p in posts if p.is_video]
        lines = [
            f"публикаций получено: {len(posts)} (видео/Reels: {len(videos)})",
            f"подписчики: {'нет данных' if snap.followers is None else snap.followers}",
            f"просмотры роликов: {sum(p.views is not None for p in videos)}/{len(videos)}",
            f"лайки: {sum(p.likes is not None for p in posts)}/{len(posts)}"
            + (" (часть скрыта владельцем)" if any(p.likes is None for p in posts) else ""),
            f"комментарии: {sum(p.comments is not None for p in posts)}/{len(posts)}",
            f"ссылка на видео для разбора кадров: {sum(bool(p.video_url) for p in videos)}/{len(videos)}",
            f"недоступно в принципе: {', '.join(snap.unavailable)}",
        ]
        reports.append(AccessReport(username, src_name, True, "\n".join(lines)))
    return reports
