"""
Scheduler for sending attendance reminders.
Checks timetable and sends reminders after each period ends.
"""
from datetime import datetime, date, time, timedelta
from typing import Optional
import asyncio
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from bot.config import Config
from bot.sheets import SheetsManager
import logging

logger = logging.getLogger(__name__)

class ReminderScheduler:
    """Manages periodic checks and sends reminders."""
    
    def __init__(self, bot: Bot, sheets: SheetsManager, chat_id: int):
        self.bot = bot
        self.sheets = sheets
        self.chat_id = chat_id
        self.running = False
        self.sent_reminders = set()  # Track sent reminders to avoid duplicates
        self.retry_queue = asyncio.Queue()
    
    async def start(self):
        """Start the scheduler."""
        self.running = True
        logger.info("Reminder scheduler started")
        
        # Start the check loop
        asyncio.create_task(self._check_loop())
        
        # Start the retry loop
        asyncio.create_task(self._retry_loop())
    
    async def stop(self):
        """Stop the scheduler."""
        self.running = False
        logger.info("Reminder scheduler stopped")
    
    async def _check_loop(self):
        """Main loop that checks for period endings."""
        while self.running:
            try:
                await self._check_periods()
                await asyncio.sleep(60 * Config.CHECK_INTERVAL_MINUTES)
            except Exception as e:
                logger.error(f"Error in check loop: {e}")
                await asyncio.sleep(60)
    
    async def _retry_loop(self):
        """Retry loop for 'Remind Me Later' requests."""
        while self.running:
            try:
                if not self.retry_queue.empty():
                    reminder_data = await self.retry_queue.get()
                    await asyncio.sleep(60 * Config.REMINDER_RETRY_MINUTES)
                    await self._send_reminder(
                        reminder_data["period"],
                        reminder_data["subject"],
                        reminder_data["date"]
                    )
                else:
                    await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error in retry loop: {e}")
                await asyncio.sleep(10)
    
    async def _check_periods(self):
        """Check if any period has ended and send reminders."""
        today = date.today()
        day_name = today.strftime("%A")
        
        # Check if today is an exception (holiday)
        if self.sheets.is_exception(today):
            logger.info(f"Today ({today}) is a holiday, skipping reminders")
            return
        
        # Get current time
        now = datetime.now()
        current_time = now.time()
        
        # Get timetable
        timetable = self.sheets.get_timetable()
        if day_name not in timetable:
            return
        
        # Time slots definition
        time_slots = {
            "1": (time(9, 30), time(10, 30)),
            "2": (time(10, 30), time(11, 30)),
            "3": (time(11, 30), time(12, 30)),
            "4": (time(13, 30), time(14, 30)),
            "5": (time(14, 30), time(15, 30)),
        }
        
        periods = timetable[day_name]
        
        for period_num in periods:
            subject = periods[period_num]
            
            # Skip if period is an exception
            if self.sheets.is_exception(today, period_num):
                continue
            
            # Check if period has ended
            if period_num in time_slots:
                _, end_time = time_slots[period_num]
                
                # If current time is past the end time (within last 2 minutes or up to 5 minutes after)
                if (current_time >= end_time and 
                    current_time <= (datetime.combine(date.today(), end_time) + timedelta(minutes=5)).time()):
                    
                    # Check if we already sent a reminder for this period today
                    reminder_key = f"{today}_{period_num}"
                    if reminder_key not in self.sent_reminders:
                        await self._send_reminder(period_num, subject, today)
                        self.sent_reminders.add(reminder_key)
    
    async def _send_reminder(self, period: str, subject: str, check_date: date):
        """Send a reminder message."""
        try:
            text = f"⏰ *Class Ended!*\n\n"
            text += f"📚 {subject}\n"
            text += f"Period {period} has just finished.\n\n"
            text += f"Did you attend?"
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Present", callback_data=f"mark_{period}_present"),
                    InlineKeyboardButton("❌ Absent", callback_data=f"mark_{period}_absent")
                ],
                [
                    InlineKeyboardButton("➖ No Class", callback_data=f"mark_{period}_no_class"),
                    InlineKeyboardButton("⏰ Remind Later", callback_data=f"remind_later_{period}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Reminder sent for period {period}: {subject}")
            
        except Exception as e:
            logger.error(f"Failed to send reminder for period {period}: {e}")
    
    async def handle_remind_later(self, period: str, subject: str):
        """Handle 'Remind Me Later' action."""
        today = date.today()
        await self.retry_queue.put({
            "period": period,
            "subject": subject,
            "date": today
        })
        logger.info(f"Reminder for period {period} queued for later")
    
    def clear_sent_reminders(self):
        """Clear the sent reminders set (useful for testing or day changes)."""
        self.sent_reminders.clear()
        logger.info("Cleared sent reminders")