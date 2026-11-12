"""Сторонний парсер Apify (по умолчанию актор apify/instagram-scraper).

Работает и для аккаунтов, недоступных через Graph API (личные аккаунты с открытым профилем),
и обычно отдаёт число воспроизведений Reels. Платно: оплата за каждую полученную публикацию
(ориентир — несколько долларов за 1000 публикаций, точную цену смотрите на странице актора).

Ограничения: это неофициальный сбор публичных данных — Instagram может менять сайт, и парсер
временно перестаёт работать или отдаёт неполные данные; это не одобрено правилами Instagram.
Ролики, опубликованные только во вкладке Reels (не в сетке), могут не попасть в выдачу.
"""

from __future__ import annotations

from datetime import datetime

import requests

from ..models import AccountSnapshot, Post, SourceError

UNAVAILABLE = ["сохранения", "репосты", "охват", "удержание зрителей", "подписчики (не запрашиваются для экономии)"]


class ApifySource:
    name = "apify"
    label = "Apify (сторонний парсер, платно)"

    def __init__(self, token: str, actor: str = "apify~instagram-scraper", timeout: int = 300):
        if not token:
            raise SourceError("Не задан APIFY_TOKEN.")
        self.token = token
        self.actor = actor
        self.timeout = timeout

    def fetch(self, username: str, limit: int = 30) -> AccountSnapshot:
        url = f"https://api.apify.com/v2/acts/{self.actor}/run-sync-get-dataset-items"
        payload = {
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsType": "posts",
            "resultsLimit": limit,
            "addParentData": False,
        }
        try:
            resp = requests.post(url, params={"token": self.token}, json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise SourceError(f"@{username}: Apify недоступен: {e}") from e
        if resp.status_code >= 400:
            raise SourceError(f"@{username}: ошибка Apify HTTP {resp.status_code}: {resp.text[:300]}")
        items = resp.json()
        posts = []
        for it in items:
            if it.get("error"):
                raise SourceError(f"@{username}: Apify: {it.get('errorDescription') or it.get('error')}")
            post = _to_post(username, it)
            if post:
                posts.append(post)
        return AccountSnapshot(username=username, followers=None, posts=posts, source=self.name, unavailable=list(UNAVAILABLE))


def _num(value) -> int | None:
    """Apify отдаёт -1 или пропускает поле, если значение скрыто."""
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _to_post(username: str, it: dict) -> Post | None:
    short = it.get("shortCode") or it.get("shortcode")
    ts = it.get("timestamp")
    if not short or not ts:
        return None
    typ = (it.get("type") or "").lower()
    product = (it.get("productType") or "").lower()
    if product == "clips":
        kind = "reel"
    elif typ == "video":
        kind = "video"
    elif typ == "sidecar":
        kind = "carousel"
    else:
        kind = "image"
    images = [u for u in (it.get("images") or []) if isinstance(u, str)]
    if not images and it.get("displayUrl"):
        images = [it["displayUrl"]]
    return Post(
        id=f"ig_sc_{short}",
        username=username,
        permalink=it.get("url") or f"https://www.instagram.com/p/{short}/",
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        kind=kind,
        caption=it.get("caption") or "",
        views=_num(it.get("videoPlayCount")) or _num(it.get("videoViewCount")),
        likes=_num(it.get("likesCount")),
        comments=_num(it.get("commentsCount")),
        video_url=it.get("videoUrl"),
        thumbnail_url=it.get("displayUrl"),
        image_urls=images,
        source="apify",
    )
