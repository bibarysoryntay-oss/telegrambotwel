"""Настройки из переменных окружения (.env) и файлов accounts.yaml / business_profile.md."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env, чтобы не тянуть лишнюю зависимость."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class AccountConfig:
    username: str
    niche: str = ""
    source: str = ""  # "" -> DEFAULT_SOURCE


@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    allowed_chat_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _env("TELEGRAM_ALLOWED_CHAT_IDS").replace(" ", "").split(",") if x
        )
    )
    digest_time: str = field(default_factory=lambda: _env("DIGEST_TIME", "10:00"))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Asia/Almaty"))

    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))

    default_source: str = field(default_factory=lambda: _env("DEFAULT_SOURCE", "graph"))
    ig_user_id: str = field(default_factory=lambda: _env("IG_USER_ID"))
    ig_access_token: str = field(default_factory=lambda: _env("IG_ACCESS_TOKEN"))
    graph_api_version: str = field(default_factory=lambda: _env("GRAPH_API_VERSION", "v24.0"))
    apify_token: str = field(default_factory=lambda: _env("APIFY_TOKEN"))
    apify_actor: str = field(default_factory=lambda: _env("APIFY_ACTOR", "apify~instagram-scraper"))

    digest_size: int = field(default_factory=lambda: int(_env("DIGEST_SIZE", "5")))
    outlier_min_ratio: float = field(default_factory=lambda: float(_env("OUTLIER_MIN_RATIO", "3.0")))
    rising_min_ratio: float = field(default_factory=lambda: float(_env("RISING_MIN_RATIO", "1.5")))
    rising_max_age_hours: float = field(default_factory=lambda: float(_env("RISING_MAX_AGE_HOURS", "72")))
    mature_min_age_hours: float = field(default_factory=lambda: float(_env("MATURE_MIN_AGE_HOURS", "48")))
    lookback_days: int = field(default_factory=lambda: int(_env("LOOKBACK_DAYS", "30")))
    posts_per_account: int = field(default_factory=lambda: int(_env("POSTS_PER_ACCOUNT", "30")))
    min_baseline_posts: int = field(default_factory=lambda: int(_env("MIN_BASELINE_POSTS", "6")))
    max_analyses_per_run: int = field(default_factory=lambda: int(_env("MAX_ANALYSES_PER_RUN", "8")))
    db_path: Path = field(default_factory=lambda: ROOT / _env("DB_PATH", "data/scout.db"))


def load_seed_accounts(path: Path | None = None) -> list[AccountConfig]:
    path = path or ROOT / "accounts.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = []
    for item in data.get("accounts", []) or []:
        if isinstance(item, str):
            item = {"username": item}
        username = str(item.get("username", "")).strip().lstrip("@").lower()
        if username:
            result.append(
                AccountConfig(username=username, niche=item.get("niche", "") or "", source=item.get("source", "") or "")
            )
    return result


def load_business_profile() -> str:
    path = ROOT / "business_profile.md"
    return path.read_text(encoding="utf-8") if path.exists() else "Welcome Travel — туристическое агентство."
