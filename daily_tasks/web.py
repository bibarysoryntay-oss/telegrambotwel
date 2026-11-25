"""Веб-сервер для Telegram Mini App: отдаёт приложение и хранит его данные.

Вход — через Telegram: приложение присылает `initData`, подписанную токеном бота,
поэтому каждый видит только свои данные, без паролей.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from .store import Conflict, Store

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
MAX_AGE = 30 * 24 * 3600  # initData старше 30 дней не принимаем


def check_init_data(init_data: str, token: str, now: float | None = None) -> int | None:
    """Проверяет подпись Telegram и возвращает id пользователя (или None)."""
    if not init_data or not token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        return None
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    def sign(items: dict[str, str]) -> str:
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(items.items()))
        return hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()

    variants = [pairs]
    if "signature" in pairs:  # разные версии Telegram считают по-разному
        variants.append({k: v for k, v in pairs.items() if k != "signature"})
    if not any(hmac.compare_digest(sign(v), received) for v in variants):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
        user_id = int(user["id"])
    except (ValueError, KeyError, TypeError):
        return None
    if (now or time.time()) - auth_date > MAX_AGE:
        return None
    return user_id


def make_app(store: Store, token: str, allowed_ids: tuple[int, ...] = ()) -> web.Application:
    def auth(request: web.Request) -> int:
        user_id = check_init_data(request.headers.get("X-Init-Data", ""), token)
        if user_id is None:
            raise web.HTTPUnauthorized(text="Откройте приложение через бота в Telegram.")
        if allowed_ids and user_id not in allowed_ids:
            raise web.HTTPForbidden(text="Доступ закрыт.")
        return user_id

    async def index(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def get_state(request: web.Request) -> web.Response:
        user_id = auth(request)
        raw, version = store.get_raw(user_id)
        if raw is None:
            state, _ = store.get(user_id)  # перенос задач из первой версии бота, если были
            raw = json.dumps(state, ensure_ascii=False) if state["tasks"] else None
        return web.json_response({"data": raw, "version": version})

    async def put_state(request: web.Request) -> web.Response:
        user_id = auth(request)
        try:
            body = await request.json()
            version = store.put_raw(user_id, body["data"], int(body["version"]))
        except Conflict as c:
            return web.json_response({"data": c.data, "version": c.version}, status=409)
        except (ValueError, KeyError, TypeError) as e:
            raise web.HTTPBadRequest(text=f"Неверные данные: {e}")
        return web.json_response({"version": version})

    async def sync_js(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "sync.js", headers={"Cache-Control": "no-cache"})

    async def health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/sync.js", sync_js),
        web.get("/health", health),
        web.get("/api/state", get_state),
        web.put("/api/state", put_state),
    ])
    return app
