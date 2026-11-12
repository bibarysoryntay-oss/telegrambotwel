"""Telegram-бот: ежедневная подборка, кнопки «Написать сценарий» / «Сохранить идею» / «Неинтересно»."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import time as dtime
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .analyzer import Analyzer
from .config import Settings, load_business_profile, load_seed_accounts
from .db import DB
from .formatting import format_item, format_saved, split_message
from .pipeline import DigestResult, build_digest, check_access

log = logging.getLogger(__name__)

HELP = """<b>Welcome Travel · разведка идей в Instagram</b>

Каждый день в {time} ({tz}) присылаю 3–5 публикаций, которые сработали заметно лучше обычного для своего аккаунта, с разбором и идеей для Welcome Travel. Одна и та же публикация не присылается дважды.

/digest — собрать подборку сейчас
/check — проверить доступ к аккаунтам и какие метрики реально доступны
/accounts — список аккаунтов
/add username [ниша] [apify] — добавить аккаунт
/remove username — убрать аккаунт
/ideas — сохранённые идеи
/script номер — сценарий по примеру
/cancel — отменить ввод"""


def item_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Написать сценарий", callback_data=f"scr:{item_id}")],
        [
            InlineKeyboardButton("💾 Сохранить идею", callback_data=f"save:{item_id}"),
            InlineKeyboardButton("👎 Неинтересно", callback_data=f"nope:{item_id}"),
        ],
    ])


def lang_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 Русский", callback_data=f"lang:ru:{item_id}"),
        InlineKeyboardButton("🇰🇿 Қазақша", callback_data=f"lang:kk:{item_id}"),
    ]])


class ScoutBot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path)
        self.db.seed_accounts(load_seed_accounts())
        self.analyzer = Analyzer(settings.claude_model, load_business_profile())
        self.digest_lock = asyncio.Lock()

    # --- доступ ---
    def allowed(self, update: Update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.id in self.s.allowed_chat_ids)

    async def deny(self, update: Update) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Доступ закрыт. ID этого чата: {update.effective_chat.id}\n"
                "Добавьте его в TELEGRAM_ALLOWED_CHAT_IDS в .env и перезапустите бота."
            )

    # --- команды ---
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        await update.message.reply_text(
            HELP.format(time=self.s.digest_time, tz=self.s.timezone), parse_mode=ParseMode.HTML
        )

    async def cmd_accounts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        rows = self.db.active_accounts()
        if not rows:
            return await update.message.reply_text("Аккаунтов нет. Добавьте: /add username ниша")
        lines = ["<b>Отслеживаемые аккаунты</b>"]
        for r in rows:
            status = "⚠️ " + escape(r["last_error"]) if r["last_error"] else ("✅" if r["last_fetch"] else "ещё не проверялся")
            lines.append(
                f"• @{escape(r['username'])} — {escape(r['niche'] or 'ниша не указана')}, "
                f"источник: {r['source'] or self.s.default_source}. {status}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    async def cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        if not ctx.args:
            return await update.message.reply_text("Формат: /add username [ниша] [apify]")
        args = list(ctx.args)
        username = args.pop(0).lstrip("@").lower().strip("/").split("/")[-1]
        source = ""
        if args and args[-1].lower() in ("apify", "graph"):
            source = args.pop().lower()
        self.db.add_account(username, " ".join(args), source)
        await update.message.reply_text(f"Добавил @{username}. Проверить доступ: /check")

    async def cmd_remove(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        if not ctx.args:
            return await update.message.reply_text("Формат: /remove username")
        username = ctx.args[0].lstrip("@").lower()
        ok = self.db.remove_account(username)
        await update.message.reply_text(f"Убрал @{username}." if ok else f"@{username} не найден.")

    async def cmd_check(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        await update.message.reply_text("Проверяю доступ к аккаунтам…")
        reports = await asyncio.to_thread(check_access, self.db, self.s)
        if not reports:
            return await update.message.reply_text("Аккаунтов нет. Добавьте: /add username ниша")
        out = []
        for r in reports:
            mark = "✅" if r.ok else "❌"
            out.append(f"{mark} <b>@{escape(r.username)}</b> · {r.source}\n{escape(r.message)}")
        for chunk in split_message("\n\n".join(out)):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    async def cmd_digest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        await update.message.reply_text("Собираю подборку, это займёт несколько минут…")
        await self.run_digest(ctx, [update.effective_chat.id])

    async def cmd_ideas(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        text = format_saved(self.db.items_with_feedback("saved", 20))
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    async def cmd_script(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self.deny(update)
        if not ctx.args or not ctx.args[0].isdigit() or not self.db.item(int(ctx.args[0])):
            return await update.message.reply_text("Формат: /script номер (номера — в /ideas)")
        await update.message.reply_text("На каком языке сценарий?", reply_markup=lang_keyboard(int(ctx.args[0])))

    async def cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        ctx.user_data.pop("await_tour", None)
        await update.message.reply_text("Отменено.")

    # --- подборка ---
    async def run_digest(self, ctx: ContextTypes.DEFAULT_TYPE, chat_ids) -> None:
        if self.digest_lock.locked():
            for cid in chat_ids:
                await ctx.bot.send_message(cid, "Подборка уже собирается, дождитесь результата.")
            return
        async with self.digest_lock:
            try:
                res: DigestResult = await asyncio.to_thread(build_digest, self.db, self.s, self.analyzer)
            except Exception as e:
                log.exception("digest failed")
                for cid in chat_ids:
                    await ctx.bot.send_message(cid, f"Не удалось собрать подборку: {type(e).__name__}: {e}")
                return
        for cid in chat_ids:
            await self.send_digest(ctx, cid, res)

    async def send_digest(self, ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, res: DigestResult) -> None:
        if res.entries:
            await ctx.bot.send_message(chat_id, f"☀️ Подборка дня: {len(res.entries)} пример(а/ов)")
        for e in res.entries:
            text = format_item(e.candidate, e.analysis, e.basis, e.niche, e.unavailable, e.growth)
            chunks = split_message(text)
            for i, chunk in enumerate(chunks):
                last = i == len(chunks) - 1
                await ctx.bot.send_message(
                    chat_id, chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=not last,
                    reply_markup=item_keyboard(e.item_id) if last else None,
                )
        summary = [
            f"Проверено аккаунтов: {res.accounts_checked}. Новых кандидатов: {res.candidates_found}.",
        ]
        if not res.entries:
            summary.insert(0, "Сегодня подходящих примеров не нашлось.")
        if res.skipped_low_fit:
            summary.append(f"Отсеяно как плохо адаптируемые для турагентства: {res.skipped_low_fit}.")
        if res.errors:
            summary.append("Проблемы с данными:\n" + "\n".join(f"• {x}" for x in res.errors[:10]))
        await ctx.bot.send_message(chat_id, "\n".join(summary), disable_web_page_preview=True)

    async def daily_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.run_digest(ctx, list(self.s.allowed_chat_ids))

    # --- кнопки ---
    async def on_button(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not self.allowed(update):
            return await q.answer("Доступ закрыт")
        parts = q.data.split(":")
        action = parts[0]
        if action in ("save", "nope"):
            item_id = int(parts[1])
            self.db.set_feedback(item_id, "saved" if action == "save" else "not_interested")
            await q.answer("Сохранено в /ideas" if action == "save" else "Учту: похожее будет реже")
            label = "💾 Идея сохранена" if action == "save" else "👎 Отмечено как неинтересное"
            await q.edit_message_reply_markup(InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Написать сценарий", callback_data=f"scr:{item_id}")],
                [InlineKeyboardButton(label, callback_data="noop")],
            ]))
        elif action == "scr":
            await q.answer()
            await q.message.reply_text("На каком языке сценарий?", reply_markup=lang_keyboard(int(parts[1])))
        elif action == "lang":
            lang, item_id = parts[1], int(parts[2])
            ctx.user_data["await_tour"] = (item_id, lang)
            await q.answer()
            await q.message.reply_text(
                "Опишите тур одним сообщением: направление, отель, даты/длительность, что включено, "
                "цена, для кого, особенности. Чем точнее — тем лучше сценарий.\n/cancel — отменить"
            )
        else:
            await q.answer()

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        pending = ctx.user_data.pop("await_tour", None)
        if not pending:
            return await update.message.reply_text("Команды: /help")
        item_id, lang = pending
        item = self.db.item(item_id)
        post = self.db.post(item["post_id"]) if item else None
        if not post:
            return await update.message.reply_text("Пример не найден.")
        tour = update.message.text
        await update.message.reply_text("Пишу сценарий…")
        try:
            text = await asyncio.to_thread(self.analyzer.write_script, post, json.loads(item["analysis"]), tour, lang)
        except Exception as e:
            log.exception("script failed")
            return await update.message.reply_text(f"Не удалось написать сценарий: {type(e).__name__}: {e}")
        self.db.save_script(item_id, lang, tour, text)
        if lang == "kk":
            text += "\n\n(Казахский текст сгенерирован моделью — перед публикацией проверьте с носителем языка.)"
        for chunk in split_message(text):
            await update.message.reply_text(chunk)

    # --- запуск ---
    def build(self) -> Application:
        app = Application.builder().token(self.s.telegram_token).build()
        app.add_handler(CommandHandler(["start", "help"], self.cmd_start))
        app.add_handler(CommandHandler("accounts", self.cmd_accounts))
        app.add_handler(CommandHandler("add", self.cmd_add))
        app.add_handler(CommandHandler("remove", self.cmd_remove))
        app.add_handler(CommandHandler("check", self.cmd_check))
        app.add_handler(CommandHandler("digest", self.cmd_digest))
        app.add_handler(CommandHandler("ideas", self.cmd_ideas))
        app.add_handler(CommandHandler("script", self.cmd_script))
        app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

        hh, mm = (int(x) for x in self.s.digest_time.split(":"))
        app.job_queue.run_daily(self.daily_job, dtime(hh, mm, tzinfo=ZoneInfo(self.s.timezone)), name="daily_digest")
        return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.telegram_token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN в .env")
    if not settings.allowed_chat_ids:
        log.warning("TELEGRAM_ALLOWED_CHAT_IDS пуст: бот только сообщит ID чата в ответ на /start.")
    ScoutBot(settings).build().run_polling()


if __name__ == "__main__":
    main()
