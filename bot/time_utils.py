"""Application clock helpers using the configured local timezone."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.config import Config


def now() -> datetime:
    return datetime.now(ZoneInfo(Config.TIMEZONE))


def today() -> date:
    return now().date()


def tomorrow() -> date:
    return today() + timedelta(days=1)


def date_context() -> str:
    current = today()
    next_day = tomorrow()
    return (
        f"Timezone: {Config.TIMEZONE}\n"
        f"Today: {current.isoformat()} ({current.strftime('%A')})\n"
        f"Tomorrow: {next_day.isoformat()} ({next_day.strftime('%A')})"
    )
