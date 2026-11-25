"""Telegram-бот ежедневных задач + приложение «Мои задачи» внутри Telegram.

Бот и приложение работают с одними данными: задача, написанная боту сообщением,
сразу видна в приложении, и наоборот.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import date, datetime, time as dtime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiohttp import web
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import tasks as T
from .config import Settings
from .formatting import evening_text, human_date, parse_tasks, stats_text, task_list_keyboard, task_list_text
from . import voice as V
from .store import Store
from .web import make_app

log = logging.getLogger(__name__)

HELP = """<b>📝 Ежедневные задачи</b>

Просто напишите задачу сообщением — она добавится на сегодня. Несколько строк = несколько задач.
{voice_line}Под списком нажимайте на задачу, чтобы отметить ✅ выполненной (или снять отметку).
{app_line}
🔁 Ежемесячные задачи: в приложении галочка «Каждый месяц» или голосом «каждый месяц 5-го оплатить интернет». После отметки задача появится снова в то же число следующего месяца.

Каждое утро в {morning} присылаю список на день, вечером в {evening} — итоги. Невыполненные задачи остаются в списке, пока их не отметите (помечены ↪️).

/today — задачи на сегодня
/tomorrow текст — добавить задачу на завтра
/done номер — отметить выполненной
/del номер — удалить задачу
/stats — статистика за неделю
/app — открыть приложение
/reminders — включить/выключить напоминания
/help — эта справка"""

COMMANDS = [
    ("today", "Задачи на сегодня"),
    ("app", "Открыть приложение"),
    ("tomorrow", "Добавить задачу на завтра"),
    ("done", "Отметить выполненной (номер)"),
    ("del", "Удалить задачу (номер)"),
    ("stats", "Статистика за неделю"),
    ("reminders", "Вкл/выкл напоминания"),
    ("help", "Справка"),
]


def _parse_time(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


class TasksBot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.tz = ZoneInfo(settings.timezone)
        self.store = Store(settings.db_path)
        self.web_runner: web.AppRunner | None = None

    # --- время ---
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    # --- доступ ---
    def allowed(self, update: Update) -> bool:
        if not self.s.allowed_chat_ids:
            return True
        chat = update.effective_chat
        return bool(chat and chat.id in self.s.allowed_chat_ids)

    async def guard(self, update: Update) -> int | None:
        if not self.allowed(update):
            if update.callback_query:
                await update.callback_query.answer("Доступ закрыт", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    f"Доступ закрыт. ID этого чата: {update.effective_chat.id}"
                )
            return None
        chat_id = update.effective_chat.id
        self.store.register_chat(chat_id)
        return chat_id

    # --- список ---
    def render_today(self, chat_id: int, mode: str = "done"):
        state, _ = self.store.get(chat_id)
        items = T.today_tasks(state, self.today())
        habits = T.daily_habits(state, self.today())
        text = task_list_text(items, self.today(), habits=habits)
        return text, task_list_keyboard(items, mode, self.s.webapp_url), items

    async def send_list(self, update: Update, chat_id: int, prefix: str = "") -> None:
        text, kb, _ = self.render_today(chat_id)
        if prefix:
            text = prefix + "\n\n" + text
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    def help_text(self) -> str:
        app_line = (
            "\n📱 Полное приложение (календарь, привычки, питание) — кнопка «Задачи» рядом с полем ввода или /app.\n"
            if self.s.webapp_url else ""
        )
        voice_line = (
            "🎙 Можно и голосом: «завтра позвонить маме и купить хлеб» — станет двумя задачами на завтра.\n"
            if V.stt_config() else ""
        )
        return HELP.format(
            morning=self.s.morning_time, evening=self.s.evening_time, app_line=app_line, voice_line=voice_line
        )

    # --- команды ---
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        await update.effective_message.reply_text(self.help_text(), parse_mode=ParseMode.HTML)
        await self.send_list(update, chat_id)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.guard(update) is None:
            return
        await update.effective_message.reply_text(self.help_text(), parse_mode=ParseMode.HTML)

    async def cmd_app(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.guard(update) is None:
            return
        if not self.s.webapp_url:
            await update.effective_message.reply_text(
                "Приложение ещё не подключено: на сервере нужен публичный адрес (Railway → Settings → Networking → Generate Domain)."
            )
            return
        await update.effective_message.reply_text(
            "Календарь, привычки, питание и все задачи — в приложении:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📱 Открыть приложение", web_app=WebAppInfo(self.s.webapp_url))]]
            ),
        )

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        await self.send_list(update, chat_id)

    async def cmd_tomorrow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        raw = update.effective_message.text.partition(" ")[2]
        tomorrow = self.today() + timedelta(days=1)
        texts = parse_tasks(raw)
        with self.store.edit(chat_id) as state:
            if texts:
                T.add(state, texts, tomorrow)
            items = T.day_tasks(state, tomorrow)
        prefix = f"Добавлено на завтра: {len(texts)}\n\n" if texts else (
            "Чтобы добавить на завтра: /tomorrow текст задачи\n\n"
        )
        await update.effective_message.reply_text(
            prefix + task_list_text(items, tomorrow, title="Задачи на завтра,"), parse_mode=ParseMode.HTML
        )

    async def _by_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
        state, _ = self.store.get(chat_id)
        items = T.today_tasks(state, self.today())
        picked = []
        for arg in context.args:
            arg = arg.strip(",")
            if arg.isdigit() and 1 <= int(arg) <= len(items):
                picked.append(items[int(arg) - 1])
        if not picked:
            await update.effective_message.reply_text("Укажите номер задачи из списка /today, например: /done 2")
        return picked

    async def cmd_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        picked = await self._by_number(update, context, chat_id)
        if not picked:
            return
        with self.store.edit(chat_id) as state:
            for t in picked:
                T.set_done(state, t.id, True, self.today())
        await self.send_list(update, chat_id, prefix=f"✅ Отмечено: {len(picked)}")

    async def cmd_del(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        picked = await self._by_number(update, context, chat_id)
        if not picked:
            return
        with self.store.edit(chat_id) as state:
            for t in picked:
                T.delete(state, t.id)
        await self.send_list(update, chat_id, prefix=f"🗑 Удалено: {len(picked)}")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        state, _ = self.store.get(chat_id)
        items = T.today_tasks(state, self.today())
        text = stats_text(T.done_stats(state, self.today()), sum(t.done for t in items), len(items))
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        on = not self.store.reminders_on(chat_id)
        self.store.set_reminders(chat_id, on)
        await update.effective_message.reply_text(
            f"🔔 Напоминания включены: утром в {self.s.morning_time}, вечером в {self.s.evening_time}."
            if on else "🔕 Напоминания выключены. Включить снова: /reminders"
        )

    # --- текст = новые задачи ---
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        texts = parse_tasks(update.effective_message.text or "")
        if not texts:
            return
        with self.store.edit(chat_id) as state:
            T.add(state, texts, self.today())
        word = "Задача добавлена" if len(texts) == 1 else f"Добавлено задач: {len(texts)}"
        await self.send_list(update, chat_id, prefix=f"➕ {word}")

    # --- голосовые ---
    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        msg = update.effective_message
        cfg = V.stt_config()
        if not cfg:
            await msg.reply_text(
                "Голосовые пока не подключены: на сервере нужен ключ для распознавания речи "
                "(OPENAI_API_KEY или GROQ_API_KEY)."
            )
            return
        media = msg.voice or msg.audio
        if media.duration and media.duration > 300:
            await msg.reply_text("Голосовое длиннее 5 минут — разбейте, пожалуйста, на части.")
            return
        await context.bot.send_chat_action(chat_id, "typing")
        try:
            file = await media.get_file()
            audio = bytes(await file.download_as_bytearray())
            text = await V.transcribe(audio, "voice.ogg", cfg)
        except V.TranscriptionError as e:
            await msg.reply_text(f"Не получилось распознать голосовое: {e}. Попробуйте ещё раз или напишите текстом.")
            return
        except Exception:
            log.exception("Ошибка при обработке голосового")
            await msg.reply_text("Не получилось обработать голосовое. Попробуйте ещё раз или напишите текстом.")
            return
        items = await V.extract_tasks(text, self.today())
        quote = text if len(text) <= 500 else text[:500] + "…"
        if not items:
            await msg.reply_text(f"🎙 «{quote}»\n\nЗадач в сообщении не нашёл.")
            return
        with self.store.edit(chat_id) as state:
            ids = T.add_items(state, [(t, d) for t, d, _ in items], [m for _, _, m in items])
        token = secrets.token_urlsafe(6)
        context.bot_data.setdefault("undo", {})[token] = (chat_id, ids)
        lines = [f"🎙 «{escape(quote)}»", "", f"➕ Добавлено задач: {len(items)}"]
        for t, d, monthly in items:
            if monthly:
                lines.append(f"🔁 {escape(t)} — каждый месяц, {d.day}-го")
            elif d > self.today():
                lines.append(f"📅 {escape(t)} — {escape(human_date(d))}")
        text_list, kb, _ = self.render_today(chat_id)
        rows = [[InlineKeyboardButton("↩️ Отменить", callback_data=f"undo:{token}")]]
        rows += list(kb.inline_keyboard) if kb else []
        await msg.reply_text(
            "\n".join(lines) + "\n\n" + text_list,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # --- кнопки ---
    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if (chat_id := await self.guard(update)) is None:
            return
        action, _, arg = (q.data or "").partition(":")
        mode, note = "done", None
        if action in ("tg", "del", "tmr"):
            with self.store.edit(chat_id) as state:
                if action == "tg":
                    T.toggle(state, arg, self.today())
                elif action == "del":
                    T.delete(state, arg)
                    mode, note = "edit", "Удалено"
                else:
                    T.move(state, arg, self.today() + timedelta(days=1))
                    mode, note = "edit", "Перенесено на завтра"
        elif action == "mode":
            mode = arg
        elif action == "undo":
            saved = context.bot_data.get("undo", {}).pop(arg, None)
            if saved and saved[0] == chat_id:
                with self.store.edit(chat_id) as state:
                    for task_id in saved[1]:
                        T.delete(state, task_id)
                note = "Отменено"
            else:
                note = "Уже нельзя отменить"
        await q.answer(note)
        text, kb, _ = self.render_today(chat_id, mode)
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise

    # --- напоминания ---
    def _reminder_targets(self) -> list[int]:
        return [c for c in self.store.reminder_chats()
                if not self.s.allowed_chat_ids or c in self.s.allowed_chat_ids]

    async def job_morning(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        for chat_id in self._reminder_targets():
            text, kb, items = self.render_today(chat_id)
            moved = sum(t.moved for t in items)
            prefix = "☀️ <b>Доброе утро!</b>"
            if moved:
                prefix += f"\nПеренесено с прошлых дней: {moved}"
            if not items:
                prefix += "\nНапишите, что планируете сделать сегодня."
            try:
                await context.bot.send_message(
                    chat_id, prefix + "\n\n" + text, parse_mode=ParseMode.HTML, reply_markup=kb
                )
            except Exception:
                log.exception("Не удалось отправить утреннее напоминание в %s", chat_id)

    async def job_evening(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = self.today()
        for chat_id in self._reminder_targets():
            state, _ = self.store.get(chat_id)
            items = T.today_tasks(state, today)
            habits = T.daily_habits(state, today)
            if not items and not habits:
                continue
            try:
                await context.bot.send_message(
                    chat_id, evening_text(items, today, habits), parse_mode=ParseMode.HTML
                )
            except Exception:
                log.exception("Не удалось отправить итоги дня в %s", chat_id)

    # --- запуск ---
    async def post_init(self, app: Application) -> None:
        try:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        except Exception:
            log.exception("Не удалось установить меню команд")
        runner = web.AppRunner(make_app(self.store, self.s.token, self.s.allowed_chat_ids, self.today))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.s.web_port).start()
        self.web_runner = runner
        log.warning("Веб-приложение слушает порт %s", self.s.web_port)
        stt = V.stt_config()
        log.warning(
            "Голосовые: %s; разбор задач Claude: %s",
            f"{stt.provider} ({stt.model})" if stt else "выключены (нет OPENAI_API_KEY / GROQ_API_KEY)",
            "включён" if os.environ.get("ANTHROPIC_API_KEY") else "выключен",
        )
        if self.s.webapp_url:
            try:
                await app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp("Задачи", WebAppInfo(self.s.webapp_url))
                )
                log.warning("Приложение: %s", self.s.webapp_url)
            except Exception:
                log.exception("Не удалось установить кнопку приложения")
        else:
            log.warning("Публичный адрес не задан — кнопки приложения в боте не будет.")

    async def post_shutdown(self, app: Application) -> None:
        if self.web_runner:
            await self.web_runner.cleanup()

    def build(self) -> Application:
        app = (
            Application.builder().token(self.s.token)
            .post_init(self.post_init).post_shutdown(self.post_shutdown).build()
        )
        for name, fn in [
            ("start", self.cmd_start), ("help", self.cmd_help), ("today", self.cmd_today),
            ("app", self.cmd_app), ("tomorrow", self.cmd_tomorrow), ("done", self.cmd_done),
            ("del", self.cmd_del), ("stats", self.cmd_stats), ("reminders", self.cmd_reminders),
        ]:
            app.add_handler(CommandHandler(name, fn))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        app.job_queue.run_daily(self.job_morning, _parse_time(self.s.morning_time).replace(tzinfo=self.tz))
        app.job_queue.run_daily(self.job_evening, _parse_time(self.s.evening_time).replace(tzinfo=self.tz))
        return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx пишет в лог адрес каждого запроса, а в нём есть токен бота — скрываем.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.token:
        raise SystemExit("Не задан TASKS_BOT_TOKEN в .env (токен от @BotFather).")
    print("✅ Бот задач запущен.", flush=True)
    TasksBot(settings).build().run_polling(allowed_updates=Update.ALL_TYPES)
