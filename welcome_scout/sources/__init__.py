from __future__ import annotations

from ..config import Settings
from ..models import SourceError
from .apify import ApifySource
from .graph_api import GraphApiSource


def get_source(name: str, settings: Settings):
    name = (name or settings.default_source).lower()
    if name == "graph":
        return GraphApiSource(settings.ig_user_id, settings.ig_access_token, settings.graph_api_version)
    if name == "apify":
        return ApifySource(settings.apify_token, settings.apify_actor)
    raise SourceError(f"Неизвестный источник данных: {name}. Допустимо: graph, apify.")


__all__ = ["get_source", "GraphApiSource", "ApifySource"]
