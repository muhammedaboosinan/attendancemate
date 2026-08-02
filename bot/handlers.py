"""Button-first Telegram screens for the attendance bot."""
from datetime import date, datetime, timedelta
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.sheets import SheetsManager
from bot.ai import GeminiAssistant
from bot.time_utils import now, today as local_today

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
        help_text = """*How to use*

*Basic Commands:*
/start - Start the bot
/help - Show this help message
/about - About the bot
/reset - Clear AI conversation memory
/undo - Undo last AI action (within 5 minutes)
/patterns - Show your saved prompt patterns

*Attendance:*
Use the menu buttons to mark attendance, view stats, and manage your timetable.

*AI Assistant:*
Send natural language messages to interact with the AI:
• "What's my attendance in ECO?"
• "Add holiday for tomorrow"
• "Mark period 3 as no class today"

The AI learns from your confirmed actions and creates reusable patterns.
"""
        await self._show(update, help_text, InlineKeyboardMarkup([self._back()]))

    async def about_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show(update, "*Attendance Mate*\n\nPersonal attendance tracker\nVersion 2.0", InlineKeyboardMarkup([self._back()]))

    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        deleted = self.sheets.clear_conversation_memory(chat_id)
        if deleted:
            await update.message.reply_text("✅ AI memory cleared successfully.")
        else:
            await update.message.reply_text("No AI memory to clear.")

    async def undo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        undo_data = self.ai.get_undo_action(chat_id)
        if undo_data:
            try:
                result = await __import__("asyncio").to_thread(self.ai.execute, undo_data, chat_id)
                await update.message.reply_text(f"✅ Undo successful: {result}")
            except Exception as e:
                await update.message.reply_text(f"❌ Undo failed: {str(e)}")
        else:
            await update.message.reply_text("No recent action to undo or undo time has expired (5 minutes).")

    async def patterns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show saved prompt patterns to the user."""
        patterns = self.sheets._get_worksheet("Prompt_Patterns")
        if not patterns:
            await update.message.reply_text("No saved patterns found.")
            return
        
        rows = self.sheets._read_values("Prompt_Patterns", patterns)
        if len(rows) <= 1:
            await update.message.reply_text("No saved patterns found.")
            return
        
        import json
        pattern_list = []
        for row in rows[1:]:
            if len(row) >= 3 and row[2].lower() == "true":
                try:
                    pattern_data = json.loads(row[1])
                    if isinstance(pattern_data, dict) and "template" in pattern_data:
                        template = pattern_data["template"]
                        variables = pattern_data.get("variables", {})
                        action = pattern_data.get("action", {}).get("name", "unknown")
                        var_info = f" (vars: {', '.join(variables.keys())})" if variables else ""
                        pattern_list.append(f"• {template}\n  → {action}{var_info}")
                    else:
                        pattern_list.append(f"• {row[0]}")
                except:
                    pattern_list.append(f"• {row[0]}")
        
        if pattern_list:
            response = "Your saved patterns:\n\n" + "\n".join(pattern_list[:10])  # Limit to 10 patterns
            if len(pattern_list) > 10:
                response += f"\n\n... and {len(pattern_list) - 10} more"
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("No active patterns found.")

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
            elif data == "ai:save_pattern":
                await self.ai_save_pattern(update, context)
            elif data == "ai:pattern_skip":
                await self.ai_pattern_skip(update, context)
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
        
        # Strip context from prompt before saving
        original_prompt = action.get("prompt", "")
        clean_prompt = self.ai._strip_context_from_prompt(original_prompt)
        action["prompt"] = clean_prompt
        
        # Execute the action first
        chat_id = update.effective_chat.id if update.effective_chat else None
        result = await __import__("asyncio").to_thread(self.ai.execute, action, chat_id)
        
        # Store action for pattern saving
        context.user_data["last_ai_action"] = action
        context.user_data["last_ai_result"] = result
        
        # Ask if user wants to save pattern
        if clean_prompt:
            suggested_prompt = self.ai.suggest_pattern_refinement(clean_prompt, action)
            action_desc = self.ai._describe_action(action)
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Save Pattern", callback_data="ai:save_pattern"),
                    InlineKeyboardButton("❌ Skip", callback_data="ai:pattern_skip")
                ],
                [self._back()]
            ])
            
            message = f"✅ {result}\n\n"
            message += f"💡 Do you want to save this as a pattern?\n\n"
            message += f"**Suggested prompt:** {suggested_prompt}\n"
            message += f"**Action:** {action_desc}\n\n"
            message += "This will let you trigger this action with similar phrases in the future."
            
            await update.callback_query.edit_message_text(message, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await update.callback_query.edit_message_text(result)
        if update.effective_chat:
            self.sheets.add_conversation_turn(update.effective_chat.id, "assistant", result)
    
    async def ai_save_pattern(self, update, context):
        """Save the last AI action as a pattern."""
        action = context.user_data.pop("last_ai_action", None)
        result = context.user_data.pop("last_ai_result", None)
        
        if not action:
            await update.callback_query.edit_message_text("Action expired. Please ask again.")
            return
        
        original_prompt = action.get("prompt", "")
        suggested_prompt = self.ai.suggest_pattern_refinement(original_prompt, action)
        
        # Save with the suggested prompt
        self.ai.remember_action(suggested_prompt, action)
        
        await update.callback_query.edit_message_text(
            f"✅ Pattern saved!\n\nNext time you can say \"{suggested_prompt}\" to trigger this action."
        )
    
    async def ai_pattern_skip(self, update, context):
        """Skip saving the pattern."""
        result = context.user_data.pop("last_ai_result", None)
        context.user_data.pop("last_ai_action", None)
        
        await update.callback_query.edit_message_text(
            f"✅ {result}\n\nPattern not saved. You can manually save patterns later using /patterns."
        )

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
        lines.append(f"\nUpdated: {now().strftime('%Y-%m-%d %H:%M')}")
        await self._show(update, "\n".join(lines), InlineKeyboardMarkup([
            [button("Refresh", "screen:dashboard"), button("Subjects", "screen:subjects")],
            [button("Today", "screen:today")], self._back()
        ]))

    async def today_page(self, update):
        today = local_today()
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
        today = local_today()
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
        today = local_today()
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
