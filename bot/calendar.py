from datetime import date, timedelta
from calendar import monthcalendar
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.time_utils import today as local_today

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_calendar(current_date: date) -> InlineKeyboardMarkup:
    year = current_date.year
    month = current_date.month
    today = local_today()

    keyboard = []

    keyboard.append([
        InlineKeyboardButton("← Previous", callback_data=f"calendar_nav:-1"),
        InlineKeyboardButton(f"{current_date.strftime('%B %Y')}", callback_data="calendar_ignore"),
        InlineKeyboardButton("Next →", callback_data=f"calendar_nav:1"),
    ])

    keyboard.append([
        InlineKeyboardButton(day, callback_data="calendar_ignore") for day in WEEKDAY_NAMES
    ])

    cal = monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="calendar_ignore"))
            else:
                cell_date = date(year, month, day)
                label = str(day)
                if cell_date == today:
                    label = f"•{day}•"
                row.append(
                    InlineKeyboardButton(label, callback_data=f"calendar:{cell_date.isoformat()}")
                )
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


def parse_calendar_selection(callback_data: str):
    if callback_data.startswith("calendar:"):
        date_str = callback_data.split(":", 1)[1]
        return date.fromisoformat(date_str), None
    if callback_data.startswith("calendar_nav:"):
        offset = int(callback_data.split(":", 1)[1])
        return None, offset
    return None, None
