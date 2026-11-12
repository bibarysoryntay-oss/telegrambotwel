from datetime import datetime, timedelta, timezone

from welcome_scout.models import Post
from welcome_scout.scoring import find_candidates, metric_of, pick_diverse

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def reel(i, views, hours_ago, user="acc", likes=100):
    return Post(id=f"{user}{i}", username=user, permalink=f"https://x/{user}{i}",
                timestamp=NOW - timedelta(hours=hours_ago), kind="reel", views=views, likes=likes, comments=5)


def baseline(user="acc", n=10, views=5000):
    return [reel(100 + i, views + i * 10, 72 + i * 24, user) for i in range(n)]


def test_outlier_detected_against_account_median():
    posts = baseline() + [reel(1, 50000, 96)]
    cands = find_candidates(posts, now=NOW)
    assert [c.post.id for c in cands] == ["acc1"]
    c = cands[0]
    assert c.signal == "outlier" and c.metric == "views"
    assert 9 < c.ratio < 11


def test_normal_post_not_selected():
    posts = baseline() + [reel(1, 9000, 96)]
    assert find_candidates(posts, now=NOW) == []


def test_rising_fresh_post():
    posts = baseline() + [reel(1, 9000, 10)]
    cands = find_candidates(posts, now=NOW)
    assert len(cands) == 1 and cands[0].signal == "rising"


def test_insufficient_baseline_skipped():
    posts = baseline(n=3) + [reel(1, 50000, 96)]
    assert find_candidates(posts, now=NOW) == []


def test_small_absolute_numbers_ignored():
    posts = baseline(views=100) + [reel(1, 900, 96)]
    assert find_candidates(posts, now=NOW) == []


def test_hidden_likes_falls_back_to_comments_and_never_mixes_metrics():
    p = Post(id="x", username="a", permalink="", timestamp=NOW, kind="image", likes=None, comments=40)
    assert metric_of(p) == ("feed", "comments", 40)
    v = Post(id="y", username="a", permalink="", timestamp=NOW, kind="reel", views=None, likes=10, comments=1)
    assert metric_of(v) == ("video", "likes+comments", 11)
    assert metric_of(Post(id="z", username="a", permalink="", timestamp=NOW, kind="image")) is None


def test_old_posts_outside_lookback_ignored():
    posts = baseline(n=40) + [reel(1, 50000, 24 * 60)]
    assert find_candidates(posts, now=NOW, lookback_days=30) == []


def test_pick_diverse_limits_per_account():
    posts = baseline("a") + [reel(i, 50000 + i, 96, "a") for i in range(1, 4)] + baseline("b") + [reel(9, 40000, 96, "b")]
    cands = sorted(find_candidates([p for p in posts if p.username == "a"], now=NOW)
                   + find_candidates([p for p in posts if p.username == "b"], now=NOW),
                   key=lambda c: c.ratio, reverse=True)
    picked = pick_diverse(cands, limit=5, per_account=2)
    assert [c.post.username for c in picked].count("a") == 2
    assert any(c.post.username == "b" for c in picked)
