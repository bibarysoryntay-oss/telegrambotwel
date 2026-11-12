"""Официальный Instagram Graph API — Business Discovery.

Позволяет из вашего бизнес-аккаунта Instagram читать публичные данные ДРУГИХ
бизнес- и авторских аккаунтов. Бесплатно. Личные (не профессиональные) аккаунты недоступны.

Доступно: подпись, тип, дата, ссылка, лайки (если не скрыты владельцем), комментарии,
просмотры Reels (view_count), ссылка на видео/обложку (кроме части роликов с лицензионной музыкой),
число подписчиков аккаунта.
Недоступно: сохранения, репосты, охват, удержание, демография, история роста.
"""

from __future__ import annotations

from datetime import datetime

import requests

from ..models import AccountSnapshot, Post, SourceError

UNAVAILABLE = ["сохранения", "репосты", "охват", "удержание зрителей"]

MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,permalink,timestamp,"
    "like_count,comments_count,view_count,media_url,thumbnail_url,"
    "children{media_type,media_url,thumbnail_url}"
)


class GraphApiSource:
    name = "graph"
    label = "Instagram Graph API (официальный, бесплатно)"

    def __init__(self, ig_user_id: str, access_token: str, api_version: str = "v24.0", timeout: int = 30):
        if not ig_user_id or not access_token:
            raise SourceError("Не заданы IG_USER_ID и IG_ACCESS_TOKEN для Instagram Graph API.")
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.base = f"https://graph.facebook.com/{api_version}"
        self.timeout = timeout

    def _query(self, username: str, limit: int, after: str | None, media_fields: str) -> dict:
        media_part = f"media.limit({limit})" + (f".after({after})" if after else "")
        fields = f"business_discovery.username({username}){{username,followers_count,media_count,{media_part}{{{media_fields}}}}}"
        resp = requests.get(
            f"{self.base}/{self.ig_user_id}",
            params={"fields": fields, "access_token": self.token},
            timeout=self.timeout,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "error" in data:
            err = data.get("error", {})
            raise SourceError(_explain_error(username, err, resp.status_code))
        return data["business_discovery"]

    def fetch(self, username: str, limit: int = 30) -> AccountSnapshot:
        media_fields = MEDIA_FIELDS
        posts: list[Post] = []
        after = None
        followers = None
        while len(posts) < limit:
            page_size = min(50, limit - len(posts))
            try:
                bd = self._query(username, page_size, after, media_fields)
            except SourceError as e:
                # На случай старой версии API без view_count — пробуем без него, но честно отмечаем.
                if "view_count" in str(e) and "view_count" in media_fields:
                    media_fields = media_fields.replace("view_count,", "")
                    continue
                raise
            followers = bd.get("followers_count")
            media = bd.get("media", {})
            for m in media.get("data", []):
                posts.append(_to_post(username, m))
            after = media.get("paging", {}).get("cursors", {}).get("after")
            if not after or not media.get("data"):
                break
        unavailable = list(UNAVAILABLE)
        if "view_count" not in media_fields:
            unavailable.insert(0, "просмотры")
        return AccountSnapshot(username=username, followers=followers, posts=posts, source=self.name, unavailable=unavailable)


def _to_post(username: str, m: dict) -> Post:
    media_type = m.get("media_type", "")
    product = m.get("media_product_type", "")
    if product == "REELS":
        kind = "reel"
    elif media_type == "VIDEO":
        kind = "video"
    elif media_type == "CAROUSEL_ALBUM":
        kind = "carousel"
    else:
        kind = "image"

    image_urls: list[str] = []
    children = (m.get("children") or {}).get("data", [])
    for ch in children:
        url = ch.get("thumbnail_url") if ch.get("media_type") == "VIDEO" else ch.get("media_url")
        if url:
            image_urls.append(url)
    if not image_urls and kind == "image" and m.get("media_url"):
        image_urls.append(m["media_url"])

    return Post(
        id=f"ig_{m['id']}",
        username=username,
        permalink=m.get("permalink", ""),
        timestamp=datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z"),
        kind=kind,
        caption=m.get("caption") or "",
        views=m.get("view_count"),
        likes=m.get("like_count"),  # отсутствует, если владелец скрыл лайки
        comments=m.get("comments_count"),
        video_url=m.get("media_url") if media_type == "VIDEO" else None,
        thumbnail_url=m.get("thumbnail_url") or (image_urls[0] if image_urls else None),
        image_urls=image_urls,
        source="graph",
    )


def _explain_error(username: str, err: dict, status: int) -> str:
    code = err.get("code")
    sub = err.get("error_subcode")
    msg = err.get("message", f"HTTP {status}")
    if sub == 2207013 or "Cannot find User" in msg:
        return (
            f"@{username}: аккаунт не найден через Graph API. Скорее всего, это личный (не бизнес/автор) аккаунт "
            f"или логин указан неверно. Для такого аккаунта используйте source: apify."
        )
    if code == 190:
        return f"@{username}: токен Instagram недействителен или истёк — обновите IG_ACCESS_TOKEN. ({msg})"
    if code in (4, 17, 32, 613):
        return f"@{username}: превышен лимит запросов Graph API, повторите позже. ({msg})"
    if code in (10, 200):
        return f"@{username}: у токена нет нужных разрешений (instagram_basic, pages_read_engagement). ({msg})"
    return f"@{username}: ошибка Graph API: {msg}"
