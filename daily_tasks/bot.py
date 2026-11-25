"""Telegram-бот ежедневных задач: пишете задачи сообщением, отмечаете кнопками."""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
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

from .config import Settings
from .db import TaskDB
from .formatting import (
    evening_text,
    parse_tasks,
    stats_text,
    task_list_keyboard,
    task_list_text,
)

log = logging.getLogger(__name__)

HELP = """<b>📝 Ежедневные задачи</b>

Просто напишите задачу сообщением — она добавится на сегодня. Несколько строк = несколько задач.
Под списком нажимайте на задачу, чтобы отметить ✅ выполненной (или снять отметку).

Каждое утро в {morning} присылаю список на день, вечером в {evening} — итоги. Невыполненные задачи автоматически переносятся на следующий день (помечены ↪️).

/today — задачи на сегодня
/tomorrow текст — добавить задачу на завтра
/done номер — отметить выполненной
/del номер — удалить задачу
/clear — убрать выполненные из сегодняшнего списка
/stats — статистика за неделю
/reminders — включить/выключить напоминания
/help — эта справка"""


def _parse_time(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


class TasksBot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.tz = ZoneInfo(settings.timezone)
        self.db = TaskDB(settings.db_path)

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
        """Возвращает chat_id, если доступ есть; иначе отвечает отказом."""
        if not self.allowed(update):
            if update.callback_query:
                await update.callback_query.answer("Доступ закрыт", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    f"Доступ закрыт. ID этого чата: {update.effective_chat.id}"
                )
            return None
        chat_id = update.effective_chat.id
        self.db.register_chat(chat_id)
        self.db.roll_over(chat_id, self.today())
        return chat_id

    # --- отправка списка ---
    async def send_list(self, update: Update, chat_id: int, prefix: str = "") -> None:
        tasks = self.db.for_day(chat_id, self.today())
        text = task_list_text(tasks, self.today())
        if prefix:
            text = prefix + "\n\n" + text
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=task_list_keyboard(tasks)
        )

    # --- команды ---
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        await update.effective_message.reply_text(
            HELP.format(morning=self.s.morning_time, evening=self.s.evening_time), parse_mode=ParseMode.HTML
        )
        await self.send_list(update, chat_id)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.guard(update) is None:
            return
        await update.effective_message.reply_text(
            HELP.format(morning=self.s.morning_time, evening=self.s.evening_time), parse_mode=ParseMode.HTML
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
        if texts:
            self.db.add(chat_id, texts, tomorrow, self.now())
        tasks = self.db.for_day(chat_id, tomorrow)
        prefix = f"Добавлено на завтра: {len(texts)}\n\n" if texts else (
            "Чтобы добавить на завтра: /tomorrow текст задачи\n\n"
        )
        await update.effective_message.reply_text(
            prefix + task_list_text(tasks, tomorrow, title="Задачи на завтра,"), parse_mode=ParseMode.HTML
        )

    async def _by_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
        tasks = self.db.for_day(chat_id, self.today())
        nums = []
        for arg in context.args:
            if not arg.strip(",").isdigit():
                continue
            n = int(arg.strip(","))
            if 1 <= n <= len(tasks):
                nums.append(n)
        if not nums:
            await update.effective_message.reply_text("Укажите номер задачи из списка /today, например: /done 2")
        return [tasks[n - 1] for n in nums]

    async def cmd_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        picked = await self._by_number(update, context, chat_id)
        if not picked:
            return
        for t in picked:
            self.db.set_done(chat_id, t.id, True, self.now())
        await self.send_list(update, chat_id, prefix=f"✅ Отмечено: {len(picked)}")

    async def cmd_del(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        picked = await self._by_number(update, context, chat_id)
        if not picked:
            return
        for t in picked:
            self.db.delete(chat_id, t.id)
        await self.send_list(update, chat_id, prefix=f"🗑 Удалено: {len(picked)}")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        n = self.db.clear_done(chat_id, self.today())
        await self.send_list(update, chat_id, prefix=f"Убрано выполненных: {n}")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        tasks = self.db.for_day(chat_id, self.today())
        text = stats_text(self.db.stats(chat_id, self.today()), sum(t.done for t in tasks), len(tasks))
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (chat_id := await self.guard(update)) is None:
            return
        on = not self.db.reminders_on(chat_id)
        self.db.set_reminders(chat_id, on)
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
        self.db.add(chat_id, texts, self.today(), self.now())
        word = "Задача добавлена" if len(texts) == 1 else f"Добавлено задач: {len(texts)}"
        await self.send_list(update, chat_id, prefix=f"➕ {word}")

    # --- кнопки ---
    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if (chat_id := await self.guard(update)) is None:
            return
        action, _, arg = (q.data or "").partition(":")
        mode = "done"
        note = None
        if action == "tg":
            self.db.toggle(chat_id, int(arg), self.now())
        elif action == "del":
            self.db.delete(chat_id, int(arg))
            mode, note = "edit", "Удалено"
        elif action == "tmr":
            self.db.move(chat_id, int(arg), self.today() + timedelta(days=1))
            mode, note = "edit", "Перенесено на завтра"
        elif action == "mode":
            mode = arg
        await q.answer(note)
        tasks = self.db.for_day(chat_id, self.today())
        try:
            await q.edit_message_text(
                task_list_text(tasks, self.today()),
                parse_mode=ParseMode.HTML,
                reply_markup=task_list_keyboard(tasks, mode),
            )
        except BadRequest as e:  # «message is not modified» — список не изменился
            if "not modified" not in str(e).lower():
                raise

    # --- напоминания ---
    async def job_morning(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = self.today()
        for chat_id in self.db.reminder_chats():
            if self.s.allowed_chat_ids and chat_id not in self.s.allowed_chat_ids:
                continue
            moved = self.db.roll_over(chat_id, today)
            tasks = self.db.for_day(chat_id, today)
            prefix = "☀️ <b>Доброе утро!</b>"
            if moved:
                prefix += f"\nПеренесено со вчера: {moved}"
            if not tasks:
                prefix += "\nНапишите, что планируете сделать сегодня."
            try:
                await context.bot.send_message(
                    chat_id,
                    prefix + "\n\n" + task_list_text(tasks, today),
                    parse_mode=ParseMode.HTML,
                    reply_markup=task_list_keyboard(tasks),
                )
            except Exception:
                log.exception("Не удалось отправить утреннее напоминание в %s", chat_id)

    async def job_evening(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = self.today()
        for chat_id in self.db.reminder_chats():
            if self.s.allowed_chat_ids and chat_id not in self.s.allowed_chat_ids:
                continue
            tasks = self.db.for_day(chat_id, today)
            if not tasks:
                continue
            try:
                await context.bot.send_message(chat_id, evening_text(tasks, today), parse_mode=ParseMode.HTML)
            except Exception:
                log.exception("Не удалось отправить итоги дня в %s", chat_id)

    # --- сборка ---
    def build(self) -> Application:
        app = Application.builder().token(self.s.token).build()
        for name, fn in [
            ("start", self.cmd_start), ("help", self.cmd_help), ("today", self.cmd_today),
            ("tomorrow", self.cmd_tomorrow), ("done", self.cmd_done), ("del", self.cmd_del),
            ("clear", self.cmd_clear), ("stats", self.cmd_stats), ("reminders", self.cmd_reminders),
        ]:
            app.add_handler(CommandHandler(name, fn))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.job_queue.run_daily(self.job_morning, _parse_time(self.s.morning_time).replace(tzinfo=self.tz))
        app.job_queue.run_daily(self.job_evening, _parse_time(self.s.evening_time).replace(tzinfo=self.tz))
        return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    settings = Settings()
    if not settings.token:
        raise SystemExit("Не задан TASKS_BOT_TOKEN в .env (токен от @BotFather).")
    TasksBot(settings).build().run_polling(allowed_updates=Update.ALL_TYPES)
