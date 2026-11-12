from datetime import datetime, timedelta, timezone

from welcome_scout.db import DB
from welcome_scout.formatting import format_item, split_message
from welcome_scout.models import Post
from welcome_scout.pipeline import all_candidates
from welcome_scout.scoring import Candidate
from welcome_scout.sources.apify import _to_post as apify_post
from welcome_scout.sources.graph_api import _to_post as graph_post


def test_graph_post_mapping_hidden_likes():
    p = graph_post("acc", {
        "id": "1", "media_type": "VIDEO", "media_product_type": "REELS", "permalink": "https://ig/p/1",
        "timestamp": "2026-11-10T08:00:00+0000", "comments_count": 12, "view_count": 51000,
        "media_url": "https://cdn/v.mp4", "thumbnail_url": "https://cdn/t.jpg", "caption": "hi",
    })
    assert p.kind == "reel" and p.views == 51000 and p.likes is None and p.comments == 12
    assert p.video_url == "https://cdn/v.mp4"


def test_apify_post_mapping_negative_means_hidden():
    p = apify_post("acc", {
        "shortCode": "abc", "timestamp": "2026-11-10T08:00:00.000Z", "type": "Video", "productType": "clips",
        "likesCount": -1, "commentsCount": 3, "videoPlayCount": 12000, "videoUrl": "https://v", "displayUrl": "https://d",
    })
    assert p.kind == "reel" and p.likes is None and p.views == 12000


def test_format_shows_missing_data_and_hypothesis_label():
    post = Post(id="1", username="flowers", permalink="https://ig/p/1", timestamp=datetime.now(timezone.utc),
                kind="reel", views=50000, likes=None, comments=40)
    cand = Candidate(post, "outlier", "views", 50000, 5000, 10, 10.0, 72)
    analysis = {"format": "до/после", "hook": "h", "plot": "p", "delivery": "d", "hypotheses": ["<гипотеза>"],
                "idea": {"title": "t", "concept": "c", "hook_line": "hl", "shots": ["s1"], "cta": "cta"},
                "adaptability": 4, "adaptability_reason": "r"}
    text = format_item(cand, analysis, "кадры", "цветы", ["сохранения"], None)
    assert "Выброс ×10.0" in text
    assert "Лайки: <i>нет данных" in text
    assert "предположения, не факты" in text
    assert "&lt;гипотеза&gt;" in text
    assert "Недоступно через источник: сохранения" in text


def test_split_message():
    parts = split_message("a\n" * 5000, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("a") == 5000


def test_sent_posts_are_not_repeated(tmp_path):
    db = DB(tmp_path / "t.db")
    db.add_account("acc")
    now = datetime.now(timezone.utc)
    posts = [Post(id=f"b{i}", username="acc", permalink="", timestamp=now - timedelta(days=3 + i), kind="reel",
                  views=5000, likes=10, comments=1) for i in range(8)]
    posts.append(Post(id="hit", username="acc", permalink="", timestamp=now - timedelta(days=3), kind="reel",
                      views=60000, likes=10, comments=1))
    db.upsert_posts(posts)

    from welcome_scout.config import Settings
    s = Settings()
    assert [c.post.id for c in all_candidates(db, s)] == ["hit"]
    db.save_item("hit", "outlier", 12.0, "views", 5000, {"format": "x"}, "sent")
    assert all_candidates(db, s) == []
