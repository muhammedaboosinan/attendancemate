"""Button-first Telegram screens for the attendance bot."""
from datetime import date, datetime, timedelta
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.sheets import SheetsManager
from bot.ai import GeminiAssistant

logger = logging.getLogger(__name__)
SUBJECTS = ["ECO", "MINOR 1", "MINOR 2", "MDC", "AEC-LE", "AEC-EL"]


def button(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def menu_keyboard():
    return ReplyKeyboardMarkup(
        [["Dashboard", "Today"], ["Subjects", "Reports"], ["Manage", "Refresh"]],
        resize_keyboard=True,
        is_persistent=True,
    )


class BotHandlers:
    def __init__(self, sheets: SheetsManager):
        self.sheets = sheets
        self.ai = GeminiAssistant(sheets)

    async def _show(self, update, text, markup=None):
        if update.callback_query:
            inline_markup = markup if isinstance(markup, InlineKeyboardMarkup) else None
            await update.callback_query.edit_message_text(text, reply_markup=inline_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    def _back(target="main"):
        return [button("Back", f"screen:{target}")]

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await self._show(update, "*Attendance Mate*\n\nTrack your classes in a few taps.", menu_keyboard())

    async def main_menu_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        destinations = {
            "Dashboard": "dashboard",
            "Today": "today",
            "Subjects": "subjects",
            "Reports": "reports",
            "Manage": "manage",
            "Refresh": "dashboard",
        }
        screen = destinations.get((update.message.text or "").strip())
        if screen:
            if update.message.text.strip() == "Refresh":
                self.sheets.clear_cache()
            await self.render_screen(update, context, screen)
        else:
            prompt = update.message.text or ""
            chat_id = update.effective_chat.id
            previous_turns = self.sheets.get_recent_conversation(chat_id)
            if self._asks_for_last_prompt(prompt):
                last_prompt = next((turn["text"] for turn in reversed(previous_turns) if turn["role"] == "user"), None)
                response_text = f"Your last prompt was:\n\n{last_prompt}" if last_prompt else "I do not have an earlier prompt saved yet."
                self.sheets.add_conversation_turn(chat_id, "user", prompt)
                self.sheets.add_conversation_turn(chat_id, "assistant", response_text)
                await update.message.reply_text(response_text)
                return
            self.sheets.add_conversation_turn(chat_id, "user", prompt)
            result = await self.ai.ask(prompt, previous_turns)
            if result.get("pending"):
                context.user_data["pending_ai_action"] = result["pending"]
                response_text = f"{result['text']}\n\n{self._action_summary(result['pending'])}"
                await update.message.reply_text(response_text, reply_markup=InlineKeyboardMarkup([[button("Confirm", "ai:confirm"), button("Cancel", "ai:cancel")]]))
            else:
                response_text = result["text"]
                await update.message.reply_text(response_text)
            self.sheets.add_conversation_turn(chat_id, "assistant", response_text)

    @staticmethod
    def _asks_for_last_prompt(prompt: str) -> bool:
        text = prompt.lower()
        return any(phrase in text for phrase in ("last prompt", "last message", "what did i say", "what was i asking"))

    @staticmethod
    def _action_summary(action):
        name = action.get("name")
        args = action.get("args", {})
        if name == "add_day_holidays":
            return f"Mark {args.get('start')} to {args.get('end')} as holidays?"
        if name == "add_period_exception":
            return f"Mark period {args.get('period')} on {args.get('date')} as no class?"
        if name == "edit_timetable":
            return f"Change timetable {args.get('day')} period {args.get('period')}?"
        return "Apply this Google Sheet change?"

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show(update, "*How to use*\n\nUse the buttons to view attendance, mark Today, and manage your timetable.\n\nOnly /start, /help, and /about are needed.", InlineKeyboardMarkup([self._back()]))

    async def about_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show(update, "*Attendance Mate*\n\nPersonal attendance tracker\nVersion 2.0", InlineKeyboardMarkup([self._back()]))

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        try:
            if data == "screen:main":
                await self.start_command(update, context)
            elif data.startswith("screen:"):
                await self.render_screen(update, context, data.split(":", 1)[1])
            elif data.startswith("subject:"):
                await self.subject_page(update, data.split(":", 1)[1])
            elif data.startswith("mark:"):
                await self.mark_page(update, context, data.split(":", 1)[1])
            elif data.startswith("save:"):
                await self.save_status(update, data.split(":", 1)[1])
            elif data.startswith("exception:"):
                await self.exception_action(update, data.split(":", 1)[1])
            elif data.startswith("report:"):
                await self.report_page(update, data.split(":", 1)[1])
            elif data.startswith("setting:"):
                await self.setting_action(update, data.split(":", 1)[1])
            elif data.startswith("remind_later:"):
                await self.remind_later(update, data.split(":", 1)[1])
            elif data.startswith("subject_history:"):
                subject = data.split(":", 1)[1]
                await self.subject_history_page(update, subject)
            elif data.startswith("subject_edit:"):
                await self.subject_edit_page(update, data.split(":", 1)[1])
            elif data == "ai:confirm":
                await self.ai_confirm(update, context)
            elif data == "ai:cancel":
                context.user_data.pop("pending_ai_action", None)
                await query.edit_message_text("Cancelled.")
            else:
                await self._show(update, "That action is no longer available.", InlineKeyboardMarkup([self._back()]))
        except Exception:
            logger.exception("Callback failed: %s", data)
            await self._show(update, "Could not load that screen. Please refresh.", InlineKeyboardMarkup([self._back()]))

    async def ai_confirm(self, update, context):
        action = context.user_data.pop("pending_ai_action", None)
        if not action:
            await update.callback_query.edit_message_text("That request has expired. Please ask again.")
            return
        if action.get("prompt"):
            self.ai.remember_action(action["prompt"], action)
        result = await __import__("asyncio").to_thread(self.ai.execute, action)
        await update.callback_query.edit_message_text(result)
        if update.effective_chat:
            self.sheets.add_conversation_turn(update.effective_chat.id, "assistant", result)

    async def render_screen(self, update, context, screen):
        if screen == "main":
            await self.start_command(update, context)
        elif screen == "dashboard":
            await self.dashboard_page(update)
        elif screen == "today":
            await self.today_page(update)
        elif screen == "subjects":
            await self.subjects_page(update)
        elif screen == "reports":
            await self._show(update, "*Reports*\n\nChoose a summary.", InlineKeyboardMarkup([
                [button("Weekly", "report:weekly"), button("Monthly", "report:monthly")],
                [button("Overall", "report:overall")], self._back()
            ]))
        elif screen == "manage":
            await self._show(update, "*Manage*\n\nTimetable, exceptions, settings, and export.", InlineKeyboardMarkup([
                [button("Timetable", "screen:timetable"), button("Exceptions", "screen:exceptions")],
                [button("Settings", "screen:settings"), button("Export", "screen:export")], self._back()
            ]))
        elif screen == "timetable":
            await self.timetable_page(update)
        elif screen == "exceptions":
            await self.exceptions_page(update)
        elif screen == "settings":
            await self.settings_page(update)
        elif screen == "export":
            await self._show(update, "*Export*\n\nYour attendance is available in the `Attendance_Log` sheet. You can export it as CSV from Google Sheets.", InlineKeyboardMarkup([self._back("manage")]))
        elif screen == "help":
            await self.help_command(update, context)
        elif screen == "about":
            await self.about_command(update, context)

    async def dashboard_page(self, update):
        stats = self.sheets.get_attendance_stats()
        pct = self.sheets.calculate_percentage(stats)
        threshold = float(self.sheets.get_setting("threshold", "75") or 75)
        margin = self.sheets.can_miss_before_threshold(stats, threshold)
        status = "Safe" if pct >= threshold else "At Risk"
        if threshold <= pct < threshold + 5:
            status = "Warning"
        miss_text = "No recorded classes yet" if margin == -1 else f"You can still miss {margin} more class{'es' if margin != 1 else ''}"
        lines = [f"*Dashboard*\n\nOverall: *{pct:.1f}%*\nStatus: *{status}*", miss_text]
        for subject in SUBJECTS:
            item = stats["by_subject"].get(subject, {"present": 0, "absent": 0})
            lines.append(f"{subject}: {self.sheets.calculate_subject_percentage(item):.1f}%")
        lines.append(f"\nUpdated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        await self._show(update, "\n".join(lines), InlineKeyboardMarkup([
            [button("Refresh", "screen:dashboard"), button("Subjects", "screen:subjects")],
            [button("Today", "screen:today")], self._back()
        ]))

    async def today_page(self, update):
        today = date.today()
        day = today.strftime("%A")
        timetable = self.sheets.get_timetable().get(day, {})
        if self.sheets.is_exception(today):
            text = f"*Today, {day}*\n\nNo class. This day is marked as a holiday."
            rows = [[button("Manage exceptions", "screen:exceptions")]]
        elif not timetable:
            text, rows = f"*Today, {day}*\n\nNo classes scheduled.", []
        else:
            lines, rows = [f"*Today, {day}*\n"], []
            for period in sorted(timetable, key=lambda value: int(value)):
                subject = self.sheets.normalize_subject(timetable[period])
                entry = self.sheets.get_attendance_entry(today, period)
                state = entry["status"].replace("_", " ").title() if entry else ("Cancelled" if self.sheets.is_exception(today, period) else "Upcoming")
                lines.append(f"Period {period} - {subject}: *{state}*")
                rows.append([
                    button(f"Mark {period}", f"mark:{today.isoformat()}:{period}"),
                    button("No class", f"save:no_class:{today.isoformat()}:{period}")
                ])
            text = "\n".join(lines)
            rows.append([button("Cancel today", "exception:add_day")])
        rows.append(self._back())
        await self._show(update, text, InlineKeyboardMarkup(rows))

    async def mark_page(self, update, context, value):
        date_str, period = value.split(":", 1)
        target = date.fromisoformat(date_str)
        raw = self.sheets.get_class_for_period(target.strftime("%A"), period)
        if not raw:
            await self._show(update, "Class not found.", InlineKeyboardMarkup([self._back("today")]))
            return
        context.user_data.update({"selected_date": date_str, "selected_period": period})
        current = self.sheets.get_attendance_entry(target, period)
        current_text = f"\nCurrent: {current['status'].title()}" if current else ""
        await self._show(update, f"*{self.sheets.normalize_subject(raw)}* - Period {period}\n{date_str}{current_text}\n\nWhat happened?", InlineKeyboardMarkup([
            [button("Present", f"save:present:{date_str}:{period}"), button("Absent", f"save:absent:{date_str}:{period}")],
            [button("No Class", f"save:no_class:{date_str}:{period}")],
            [button("Back", "screen:today")]
        ]))

    async def save_status(self, update, value):
        status, date_str, period = value.split(":", 2)
        target = date.fromisoformat(date_str)
        raw = self.sheets.get_class_for_period(target.strftime("%A"), period)
        if not raw or not self.sheets.log_attendance(target, target.strftime("%A"), period, raw, status):
            await self._show(update, "Could not save attendance.", InlineKeyboardMarkup([self._back("today")]))
            return
        scheduler = getattr(self, "scheduler", None)
        if scheduler:
            scheduler.answered_reminders.add(f"{date_str}:{period}")
        label = {"present": "Attendance saved", "absent": "Absent saved", "no_class": "No class marked"}[status]
        logger.info("Attendance saved: date=%s period=%s subject=%s status=%s", date_str, period, self.sheets.normalize_subject(raw), status)
        await self._show(update, f"*{label}*\n\n{self.sheets.normalize_subject(raw)} - Period {period}", InlineKeyboardMarkup([
            [button("Edit", f"mark:{date_str}:{period}"), button("Today", "screen:today")],
            [button("Dashboard", "screen:dashboard")], self._back()
        ]))

    async def remind_later(self, update, value):
        date_str, period = value.split(":", 1)
        target = date.fromisoformat(date_str)
        raw = self.sheets.get_class_for_period(target.strftime("%A"), period)
        scheduler = getattr(self, "scheduler", None)
        if scheduler and raw:
            await scheduler.handle_remind_later(period, raw, target)
        await self._show(update, "I will remind you once more shortly.", InlineKeyboardMarkup([self._back("today")]))

    async def subjects_page(self, update):
        await self._show(update, "*Subjects*\n\nChoose a subject.", InlineKeyboardMarkup([
            [button(subject, f"subject:{subject}") for subject in SUBJECTS[:2]],
            [button(subject, f"subject:{subject}") for subject in SUBJECTS[2:4]],
            [button(subject, f"subject:{subject}") for subject in SUBJECTS[4:]], self._back()
        ]))

    async def subject_page(self, update, subject):
        stats = self.sheets.get_attendance_stats()["by_subject"].get(subject, {"present": 0, "absent": 0, "no_class": 0, "total": 0})
        pct = self.sheets.calculate_subject_percentage(stats)
        margin = self.sheets.can_miss_before_threshold(stats, float(self.sheets.get_setting("threshold", "75") or 75))
        margin_text = "No recorded classes yet" if margin == -1 else f"Can still miss: {margin}"
        text = f"*{subject}*\n\nAttendance: *{pct:.1f}%*\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats.get('no_class', 0)}\nClasses held: {stats['present'] + stats['absent']}\n{margin_text}"
        await self._show(update, text, InlineKeyboardMarkup([
            [button("History", f"subject_history:{subject}"), button("Edit entries", f"subject_edit:{subject}")],
            self._back("subjects")
        ]))

    async def subject_history_page(self, update, subject):
        history = self.sheets.get_subject_history(subject)
        lines = [f"*{subject} history*"]
        lines.extend(f"{item['date']} · Period {item['period']} · {item['status'].replace('_', ' ').title()}" for item in history)
        if not history:
            lines.append("\nNo entries yet.")
        await self._show(update, "\n".join(lines), InlineKeyboardMarkup([
            [button("Back to subject", f"subject:{subject}")], self._back("subjects")
        ]))

    async def subject_edit_page(self, update, subject):
        history = self.sheets.get_subject_history(subject)
        rows = [[button(f"{item['date']} P{item['period']}", f"mark:{item['date']}:{item['period']}")] for item in history]
        if not rows:
            rows.append([button("No entries to edit", f"subject:{subject}")])
        rows.append([button("Back to subject", f"subject:{subject}")])
        await self._show(update, f"*Edit {subject}*\n\nChoose an entry.", InlineKeyboardMarkup(rows))

    async def report_page(self, update, period):
        today = date.today()
        start = today - (timedelta(days=6) if period == "weekly" else timedelta(days=29) if period == "monthly" else timedelta(days=3650))
        stats = self.sheets.get_attendance_stats(start, today)
        pct = self.sheets.calculate_percentage(stats)
        label = {"weekly": "This week", "monthly": "This month", "overall": "Overall"}[period]
        await self._show(update, f"*{label}*\n\nAttendance: *{pct:.1f}%*\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats['no_class']}", InlineKeyboardMarkup([[button("Reports", "screen:reports")], self._back()]))

    async def timetable_page(self, update):
        timetable = self.sheets.get_timetable()
        lines = ["*Timetable*"]
        for day in sorted(timetable):
            lines.append(f"\n*{day}*")
            for period in sorted(timetable[day], key=lambda value: int(value)):
                lines.append(f"{period}. {self.sheets.normalize_subject(timetable[day][period])}")
        await self._show(update, "\n".join(lines) if len(lines) > 1 else "*Timetable*\n\nNo timetable found.", InlineKeyboardMarkup([self._back("manage")]))

    async def exceptions_page(self, update):
        entries = self.sheets.get_days_with_exceptions()
        text = "*Exceptions*\n\n" + ("\n".join(entries) if entries else "No active exceptions.")
        await self._show(update, text, InlineKeyboardMarkup([
            [button("Add Day Holiday", "exception:add_day"), button("Add Period No Class", "exception:add_period")],
            [button("Remove Today", "exception:remove_today"), button("Remove Period", "exception:remove_period")],
            self._back("manage")
        ]))

    async def exception_action(self, update, value):
        today = date.today()
        if value == "add_day":
            self.sheets.add_exception(today, "day", reason="Holiday")
            await self._show(update, "Holiday added for today.", InlineKeyboardMarkup([self._back("exceptions")]))
        elif value == "add_period":
            periods = self.sheets.get_timetable().get(today.strftime("%A"), {})
            rows = [[button(f"Period {p}", f"exception:add_period:{today.isoformat()}:{p}")] for p in sorted(periods, key=lambda x: int(x))]
            await self._show(update, "Choose a period for no class:", InlineKeyboardMarkup(rows + [self._back("exceptions")]))
        elif value.startswith("add_period:"):
            _, date_str, period = value.split(":")
            self.sheets.add_exception(date.fromisoformat(date_str), "period", period=period, reason="No class")
            await self._show(update, "No class marked.", InlineKeyboardMarkup([self._back("exceptions")]))
        elif value == "remove_today":
            removed = self.sheets.deactivate_exception(today, "day")
            await self._show(update, "Exception removed." if removed else "No day exception found.", InlineKeyboardMarkup([self._back("exceptions")]))
        elif value == "remove_period":
            periods = [row[2] for row in self.sheets.get_exceptions_for_date(today) if len(row) >= 3 and row[1] == "period"]
            rows = [[button(f"Remove period {period}", f"exception:remove_period:{today.isoformat()}:{period}")] for period in periods]
            await self._show(update, "Choose a period exception:", InlineKeyboardMarkup(rows + [self._back("exceptions")]))
        elif value.startswith("remove_period:"):
            _, date_str, period = value.split(":")
            removed = self.sheets.deactivate_exception(date.fromisoformat(date_str), "period", period)
            await self._show(update, "Exception removed." if removed else "No period exception found.", InlineKeyboardMarkup([self._back("exceptions")]))

    async def settings_page(self, update):
        reminders = self.sheets.get_setting("reminders", "enabled")
        delay = self.sheets.get_setting("reminder_delay", "5")
        threshold = self.sheets.get_setting("threshold", "75")
        text = f"*Settings*\n\nReminders: {reminders}\nDelay: {delay} min\nWarning threshold: {threshold}%"
        await self._show(update, text, InlineKeyboardMarkup([
            [button("Toggle reminders", "setting:toggle"), button("Change delay", "setting:delay")],
            [button("Change threshold", "setting:threshold")], self._back("manage")
        ]))

    async def setting_action(self, update, value):
        if value == "toggle":
            current = self.sheets.get_setting("reminders", "enabled").lower()
            self.sheets.set_setting("reminders", "disabled" if current == "enabled" else "enabled")
        elif value == "delay":
            current = self.sheets.get_setting("reminder_delay", "5")
            self.sheets.set_setting("reminder_delay", "10" if current == "5" else "5")
        elif value == "threshold":
            current = self.sheets.get_setting("threshold", "75")
            self.sheets.set_setting("threshold", "80" if current == "75" else "75")
        await self.settings_page(update)
