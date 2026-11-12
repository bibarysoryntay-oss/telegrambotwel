from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Post:
    """Публикация. None в метрике = данные недоступны (скрыты владельцем или не отдаются источником)."""

    id: str
    username: str
    permalink: str
    timestamp: datetime
    kind: str  # "reel" | "video" | "image" | "carousel"
    caption: str = ""
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def is_video(self) -> bool:
        return self.kind in ("reel", "video")

    def age_hours(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.timestamp).total_seconds() / 3600


@dataclass
class AccountSnapshot:
    username: str
    followers: int | None
    posts: list[Post]
    source: str
    unavailable: list[str] = field(default_factory=list)  # что этот источник не отдаёт в принципе


class SourceError(Exception):
    """Ошибка получения данных; сообщение показывается пользователю как есть."""
