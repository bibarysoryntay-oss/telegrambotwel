"""Проверка без Telegram и Claude: какие данные реально приходят по аккаунтам и какие публикации выделяются.

Запуск:
    python scripts/check_access.py              # проверить доступ и метрики по accounts.yaml
    python scripts/check_access.py --candidates # плюс показать найденные «выбросы» (без анализа, бесплатно)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from welcome_scout.config import Settings, load_seed_accounts  # noqa: E402
from welcome_scout.db import DB  # noqa: E402
from welcome_scout.pipeline import all_candidates, check_access  # noqa: E402
from welcome_scout.scoring import METRIC_LABELS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", action="store_true", help="показать кандидатов на разбор")
    ap.add_argument("--limit", type=int, default=None, help="сколько публикаций брать на аккаунт")
    args = ap.parse_args()

    settings = Settings()
    db = DB(settings.db_path)
    db.seed_accounts(load_seed_accounts())

    for r in check_access(db, settings, args.limit or settings.posts_per_account):
        print(f"\n{'OK ' if r.ok else 'ERR'} @{r.username} [{r.source}]")
        for line in r.message.splitlines():
            print("    " + line)

    if args.candidates:
        print("\nКандидаты (ещё не отправленные):")
        cands = all_candidates(db, settings)
        if not cands:
            print("    нет — либо ничего не выделяется, либо мало публикаций для базы сравнения")
        for c in cands:
            print(f"    {c.signal:7} x{c.ratio:5.1f}  @{c.post.username:20} {METRIC_LABELS[c.metric]}={c.value} "
                  f"(медиана {c.baseline:.0f} по {c.baseline_n})  {c.post.permalink}")


if __name__ == "__main__":
    main()
