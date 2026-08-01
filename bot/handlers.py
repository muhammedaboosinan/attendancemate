from datetime import date
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from bot.config import Config
from bot.sheets import SheetsManager
from bot.calendar import build_calendar, parse_calendar_selection
import logging

logger = logging.getLogger(__name__)

user_states = {}


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📊 Dashboard", "📅 Today"],
            ["✅ Mark Att.", "🚫 No Class Days"],
            ["❓ Help"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


class BotHandlers:
    def __init__(self, sheets: SheetsManager):
        self.sheets = sheets

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        logger.info(f"User {user_id} started the bot")
        text = (
            "👋 *Welcome to Attendance Bot!*\n\n"
            "I'll help you track your class attendance and remind you to mark it.\n\n"
            "Use the menu below or type a command."
        )
        markup = main_menu_keyboard()
        if update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "❓ *Commands*\n\n"
            "/start - Welcome + main menu\n"
            "/dashboard - Attendance stats\n"
            "/today - Today's schedule\n"
            "/mark - Mark attendance for a date/period\n"
            "/nclass - Mark no-class day/period\n"
            "/timetable - View timetable\n"
            "/help - This message\n"
            "/menu - Show main menu\n"
            "/stop - Hide menu\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def dashboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.sheets.get_attendance_stats()
        total_scheduled = self.sheets.get_total_scheduled_classes()
        classes_taken = self.sheets.get_classes_taken()
        overall_pct = self.sheets.calculate_percentage(stats)

        text = "📊 *Attendance Dashboard*\n\n"
        text += f"📋 *Total Scheduled:* {total_scheduled}\n"
        text += f"📅 *Classes Taken:* {classes_taken}\n"
        text += f"✅ *Present:* {stats['present']}\n"
        text += f"❌ *Absent:* {stats['absent']}\n"
        text += f"➖ *No Class:* {stats['no_class']}\n\n"
        text += f"📈 *Overall:* {overall_pct:.1f}%\n\n"

        if stats["by_subject"]:
            text += "📚 *Subject-wise:*\n"
            for subject, subj_stats in sorted(stats["by_subject"].items()):
                subj_pct = self.sheets.calculate_subject_percentage(subj_stats)
                text += f"  • {subject}: {subj_pct:.1f}% ({subj_stats['present']} present, {subj_stats['absent']} absent)\n"

        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        today = date.today()
        day_name = today.strftime("%A")
        text, keyboard = self._build_today_text(day_name, today)
        markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        if update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    async def timetable_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        timetable = self.sheets.get_timetable()
        if not timetable:
            await update.message.reply_text("No timetable found.")
            return
        lines = ["📚 *Timetable*"]
        for day in sorted(timetable.keys()):
            lines.append(f"\n*{day}*")
            for period in sorted(timetable[day].keys(), key=lambda x: int(x)):
                lines.append(f"Period {period}: {timetable[day][period]}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def mark_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["flow"] = "mark"
        calendar_markup = build_calendar(date.today())
        if update.message:
            await update.message.reply_text("📅 Select date:", reply_markup=calendar_markup)
        else:
            await update.callback_query.edit_message_text("📅 Select date:", reply_markup=calendar_markup)

    async def nclass_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["flow"] = "nclass"
        calendar_markup = build_calendar(date.today())
        if update.message:
            await update.message.reply_text("📅 Select date:", reply_markup=calendar_markup)
        else:
            await update.callback_query.edit_message_text("📅 Select date:", reply_markup=calendar_markup)

    async def remove_menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Menu hidden.", reply_markup=ReplyKeyboardRemove())

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "back_main":
            await self.start_command(update, context)
            return

        if data.startswith("calendar:"):
            await self.handle_calendar(query, context, data)
            return

        if data.startswith("calendar_nav:"):
            await self.handle_calendar_nav(query, context, data)
            return

        if data.startswith("mark_period_") or data.startswith("mark_status_"):
            await self.handle_mark_flow(query, context, data)
            return

        if data.startswith("nclass_"):
            await self.handle_nclass_flow(query, context, data)
            return

        if data == "today":
            await self.today_command(update, context)
            return

        if data == "dashboard":
            await self.dashboard_command(update, context)
            return

        await query.edit_message_text("❌ Unknown action. Please try again.")

    async def handle_calendar(self, query, context: ContextTypes.DEFAULT_TYPE, data: str):
        selected_date, _ = parse_calendar_selection(data)
        if not selected_date:
            return
        flow = context.user_data.get("flow")
        if flow == "mark":
            await self._show_periods_for_date(query, selected_date, context)
        elif flow == "nclass":
            keyboard = [
                [
                    InlineKeyboardButton("🗓️ Entire Day", callback_data=f"nclass_day:{selected_date.isoformat()}"),
                    InlineKeyboardButton("⏰ Specific Period", callback_data=f"nclass_periods:{selected_date.isoformat()}"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]
            await query.edit_message_text("Mark entire day as no-class, or a specific period?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("Use /mark or /nclass to start a flow.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")]]))

    async def handle_calendar_nav(self, query, context: ContextTypes.DEFAULT_TYPE, data: str):
        _, offset = parse_calendar_selection(data)
        if offset is None:
            return
        current_calendar = query.reply_markup
        try:
            header_text = current_calendar.inline_keyboard[0][1].text
        except Exception:
            header_text = ""
        from datetime import date as dtdate
        try:
            current_date = dtdate.strptime(header_text, "%B %Y").replace(day=1)
        except Exception:
            current_date = date.today()
        month = current_date.month - 1 + offset
        year = current_date.year + month // 12
        month = month % 12 + 1
        day = min(current_date.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        new_date = date(year, month, day)
        await query.edit_message_reply_markup(reply_markup=build_calendar(new_date))

    async def _show_periods_for_date(self, query, target_date: date, context: ContextTypes.DEFAULT_TYPE):
        day_name = target_date.strftime("%A")
        timetable = self.sheets.get_timetable()
        periods = timetable.get(day_name, {})
        if not periods:
            await query.edit_message_text("No classes scheduled for this day.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
            return
        if self.sheets.is_exception(target_date):
            await query.edit_message_text("🎉 This day is marked as a holiday.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
            return
        keyboard = []
        for period_num in sorted(periods.keys(), key=lambda x: int(x)):
            if self.sheets.is_exception(target_date, period_num):
                continue
            subject = periods[period_num]
            keyboard.append([InlineKeyboardButton(f"Period {period_num}: {subject}", callback_data=f"mark_period_{period_num}:{target_date.isoformat()}")])
        if not keyboard:
            await query.edit_message_text("All periods are cancelled for this date.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
            return
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
        await query.edit_message_text(f"Select period for *{day_name}, {target_date.isoformat()}*:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_mark_flow(self, query, context: ContextTypes.DEFAULT_TYPE, data: str):
        if data.startswith("mark_period_"):
            rest = data[len("mark_period_"):]
            period, date_str = rest.split(":", 1)
            target_date = date.fromisoformat(date_str)
            day_name = target_date.strftime("%A")
            raw_subject = self.sheets.get_class_for_period(day_name, period)
            if not raw_subject:
                await query.edit_message_text("Class not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]))
                return
            keyboard = [
                [
                    InlineKeyboardButton("✅ Present", callback_data=f"mark_status_present:{period}:{date_str}"),
                    InlineKeyboardButton("❌ Absent", callback_data=f"mark_status_absent:{period}:{date_str}"),
                    InlineKeyboardButton("➖ No Class", callback_data=f"mark_status_no_class:{period}:{date_str}"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]
            text = f"Mark *{raw_subject}* — Period {period} on {date_str}"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return

        if data.startswith("mark_status_"):
            rest = data[len("mark_status_"):]
            status, period, date_str = rest.split(":", 2)
            target_date = date.fromisoformat(date_str)
            day_name = target_date.strftime("%A")
            raw_subject = self.sheets.get_class_for_period(day_name, period)
            if not raw_subject:
                await query.edit_message_text("Class not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]))
                return
            success = self.sheets.log_attendance(target_date, day_name, period, raw_subject, status)
            if success:
                status_emoji = {"present": "✅", "absent": "❌", "no_class": "➖"}
                text = f"{status_emoji.get(status, '✓')} *Attendance Marked*\n\n📚 {raw_subject}\n⏰ Period {period}\n📅 {date_str}\nStatus: {status.title()}"
            else:
                text = "❌ Failed to mark attendance."
            keyboard = [
                [InlineKeyboardButton("📅 Today", callback_data="today")],
                [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")],
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return

    async def handle_nclass_flow(self, query, context: ContextTypes.DEFAULT_TYPE, data: str):
        if data.startswith("nclass_day:"):
            date_str = data.split(":", 1)[1]
            target_date = date.fromisoformat(date_str)
            self.sheets.add_exception(target_date, "day", reason="Holiday")
            await query.edit_message_text(f"✅ {date_str} marked as holiday.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
            return
        if data.startswith("nclass_periods:"):
            date_str = data.split(":", 1)[1]
            target_date = date.fromisoformat(date_str)
            day_name = target_date.strftime("%A")
            timetable = self.sheets.get_timetable()
            periods = timetable.get(day_name, {})
            if self.sheets.is_exception(target_date):
                await query.edit_message_text("This date is already a holiday.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
                return
            keyboard = []
            for period_num in sorted(periods.keys(), key=lambda x: int(x)):
                if self.sheets.is_exception(target_date, period_num):
                    continue
                subject = periods[period_num]
                keyboard.append([InlineKeyboardButton(f"Period {period_num}: {subject}", callback_data=f"nclass_period:{period_num}:{date_str}")])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
            await query.edit_message_text(f"Select period for *{date_str}*:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return
        if data.startswith("nclass_period:"):
            _, period, date_str = data.split(":", 2)
            target_date = date.fromisoformat(date_str)
            self.sheets.add_exception(target_date, "period", period=period, reason="Cancelled")
            await query.edit_message_text(f"✅ Period {period} on {date_str} marked as cancelled.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")]]), parse_mode=ParseMode.MARKDOWN)
            return

    def _build_today_text(self, day_name: str, today: date):
        if self.sheets.is_exception(today):
            return f"📅 *Today ({day_name}) - {today}*\n\n🎉 Holiday! No classes today.", []
        timetable = self.sheets.get_timetable()
        periods = timetable.get(day_name, {})
        if not periods:
            return f"📅 *Today ({day_name}) - {today}*\n\nNo classes scheduled for today.", []
        text = f"📅 *Today ({day_name}) - {today}*\n\n"
        keyboard = []
        for period_num in sorted(periods.keys(), key=lambda x: int(x)):
            subject = periods[period_num]
            if self.sheets.is_exception(today, period_num):
                text += f"⏰ Period {period_num}: {subject} - *Cancelled*\n"
            else:
                text += f"⏰ Period {period_num}: {subject}\n"
                keyboard.append([InlineKeyboardButton(f"Period {period_num}", callback_data=f"mark_period_{period_num}:{today.isoformat()}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
        return text, keyboard
