"""Поиск публикаций, которые выделяются относительно обычных результатов САМОГО аккаунта.

Логика:
- Ролики сравниваются с роликами по просмотрам; фото/карусели — с фото/каруселями по лайкам+комментариям
  (если лайки скрыты — только по комментариям). Разные метрики между собой не смешиваются.
- База сравнения — медиана последних «созревших» публикаций того же типа (старше MATURE_MIN_AGE_HOURS),
  медиана устойчива к единичным вирусным роликам.
- «Выброс» (outlier): созревшая публикация, у которой метрика ≥ OUTLIER_MIN_RATIO × медиана.
- «Быстрый рост» (rising): свежая публикация (младше RISING_MAX_AGE_HOURS), которая УЖЕ набрала
  ≥ RISING_MIN_RATIO × медианы итоговых результатов аккаунта.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Post

# Минимальные абсолютные значения, чтобы «3 лайка против 1» не считались успехом.
MIN_ABSOLUTE = {"views": 1000, "likes+comments": 50, "comments": 10}
BASELINE_WINDOW = 30  # сколько последних публикаций брать в базу

METRIC_LABELS = {"views": "просмотры", "likes+comments": "лайки + комментарии", "comments": "комментарии"}


@dataclass
class Candidate:
    post: Post
    signal: str  # "outlier" | "rising"
    metric: str
    value: int
    baseline: float
    baseline_n: int
    ratio: float
    age_hours: float


def metric_of(post: Post) -> tuple[str, str, int] | None:
    """(группа, метрика, значение) или None, если сравнить не по чему."""
    if post.is_video:
        if post.views is not None:
            return "video", "views", post.views
        group = "video"
    else:
        group = "feed"
    if post.likes is not None:
        return group, "likes+comments", post.likes + (post.comments or 0)
    if post.comments is not None:
        return group, "comments", post.comments
    return None


def find_candidates(
    posts: list[Post],
    *,
    outlier_min_ratio: float = 3.0,
    rising_min_ratio: float = 1.5,
    rising_max_age_hours: float = 72,
    mature_min_age_hours: float = 48,
    lookback_days: int = 30,
    min_baseline_posts: int = 6,
    now: datetime | None = None,
) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    measured = [(p, m) for p in posts if (m := metric_of(p))]
    result: list[Candidate] = []

    for post, (group, metric, value) in measured:
        age = post.age_hours(now)
        if age > lookback_days * 24 or value < MIN_ABSOLUTE[metric]:
            continue
        peers = sorted(
            (
                (p, m)
                for p, m in measured
                if p.id != post.id and m[0] == group and m[1] == metric and p.age_hours(now) >= mature_min_age_hours
            ),
            key=lambda x: x[0].timestamp,
            reverse=True,
        )[:BASELINE_WINDOW]
        if len(peers) < min_baseline_posts:
            continue
        baseline = statistics.median(m[2] for _, m in peers) or 1
        ratio = value / baseline

        if age >= mature_min_age_hours and ratio >= outlier_min_ratio:
            signal = "outlier"
        elif age < rising_max_age_hours and ratio >= rising_min_ratio:
            signal = "rising"
        else:
            continue
        result.append(Candidate(post, signal, metric, value, baseline, len(peers), ratio, age))

    result.sort(key=lambda c: c.ratio, reverse=True)
    return result


def pick_diverse(candidates: list[Candidate], limit: int, per_account: int = 2) -> list[Candidate]:
    """Не больше per_account примеров от одного аккаунта, чтобы подборка была разнообразной."""
    picked, counts = [], {}
    for c in candidates:
        if counts.get(c.post.username, 0) >= per_account:
            continue
        picked.append(c)
        counts[c.post.username] = counts.get(c.post.username, 0) + 1
        if len(picked) >= limit:
            break
    return picked
