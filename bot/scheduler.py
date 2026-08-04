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
from bot.time_utils import now, today as local_today
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
        self.answered_reminders = set()
        self.queued_retries = set()
        self.retry_queue = asyncio.Queue()
        self._tasks = []
        self._loop = None
        self._last_check_date = None
    
    async def start(self):
        """Start the scheduler."""
        self.running = True
        self._loop = asyncio.get_running_loop()
        logger.info("Reminder scheduler started")
        
        # Start the check loop
        self._tasks = [
            asyncio.create_task(self._check_loop()),
            asyncio.create_task(self._retry_loop()),
        ]
        
    async def stop(self):
        """Stop the scheduler gracefully."""
        self.running = False
        logger.info("Stopping reminder scheduler...")
        
        # Cancel all pending tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to finish with a timeout
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for scheduler tasks to stop")
                # Force-cancel any remaining tasks to avoid "Task was destroyed" warnings
                for task in self._tasks:
                    if not task.done():
                        task.cancel()
                # Give cancelled tasks a final chance to process CancelledError
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.warning(f"Error while stopping scheduler tasks: {e}")
        
        self._tasks.clear()
        logger.info("Reminder scheduler stopped")
    
    def stop_sync(self):
        """Synchronous stop method for non-async contexts."""
        self.running = False
        logger.info("Stopping reminder scheduler (sync)...")
        
        # Cancel tasks without awaiting (best effort for shutdown)
        for task in self._tasks:
            if not task.done():
                try:
                    task.cancel()
                except Exception:
                    pass  # Ignore errors during shutdown
        
        # Give the event loop a chance to process cancellations
        if self._loop and self._loop.is_running():
            try:
                # Schedule the stop coroutine on the loop
                future = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
                future.result(timeout=5.0)
            except Exception as e:
                logger.warning(f"Error during sync stop: {e}")
        
        self._tasks.clear()
        logger.info("Reminder scheduler stopped (sync)")
    
    async def _stop_async(self):
        """Internal async stop helper for sync stop."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for scheduler tasks to stop")
            except Exception:
                pass
        self._tasks.clear()
    
    async def _check_loop(self):
        """Main loop that checks for period endings."""
        while self.running:
            try:
                await self._check_periods()
                # Use a shorter sleep and check running flag to respond faster to cancellation
                for _ in range(60 * Config.CHECK_INTERVAL_MINUTES):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("Check loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in check loop: {e}")
                await asyncio.sleep(5)
    
    async def _retry_loop(self):
        """Retry loop for 'Remind Me Later' requests."""
        while self.running:
            try:
                if not self.retry_queue.empty():
                    reminder_data = await self.retry_queue.get()
                    # Use shorter sleeps to respond faster to cancellation
                    for _ in range(60 * Config.REMINDER_RETRY_MINUTES):
                        if not self.running:
                            break
                        await asyncio.sleep(1)
                    await self._send_reminder(
                        reminder_data["period"],
                        reminder_data["subject"],
                        reminder_data["date"]
                    )
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("Retry loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in retry loop: {e}")
                await asyncio.sleep(1)
    
    async def _check_periods(self):
        """Check if any period has ended and send reminders."""
        if self.sheets.get_setting("reminders", "enabled").lower() == "disabled":
            return
        today = local_today()
        day_name = today.strftime("%A")
        
        # Clear stale reminders when the date changes
        if self._last_check_date != today:
            self.sent_reminders.clear()
            self.answered_reminders.clear()
            self.queued_retries.clear()
            self._last_check_date = today
            logger.info(f"Date changed to {today}, cleared reminder sets")
        
        # Check if today is an exception (holiday)
        if self.sheets.is_exception(today):
            logger.info(f"Today ({today}) is a holiday, skipping reminders")
            return
        
        # Get current time
        current_time = now().time()
        
        # Get timetable
        timetable = self.sheets.get_timetable()
        logger.info("Today is " + day_name + ", timetable keys: " + str(list(timetable.keys())))
        if day_name not in timetable:
            logger.info("Day " + day_name + " not found in timetable")
            return
        
        # Legacy fallback for timetable rows without Start/End values.
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

            if self.sheets.get_attendance_entry(today, period_num):
                self.answered_reminders.add(f"{today.isoformat()}:{period_num}")
                continue
            
            configured_times = self.sheets.get_period_times(day_name, period_num)
            if configured_times and configured_times[1]:
                try:
                    end_time = time.fromisoformat(configured_times[1])
                except ValueError:
                    logger.warning("Invalid end time for %s period %s: %s", day_name, period_num, configured_times[1])
                    continue
            elif period_num in time_slots:
                _, end_time = time_slots[period_num]
            else:
                logger.warning("No end time configured for %s period %s", day_name, period_num)
                continue
                
            # Send once after the configured class end, even if the bot restarted late.
            if current_time >= end_time:
                    
                reminder_key = f"{today.isoformat()}:{period_num}"
                if reminder_key not in self.sent_reminders:
                    sent = await self._send_reminder(period_num, subject, today)
                    if sent:
                        self.sent_reminders.add(reminder_key)
    
    async def _send_reminder(self, period: str, subject: str, check_date: date):
        """Send a reminder message."""
        try:
            reminder_key = f"{check_date.isoformat()}:{period}"
            if reminder_key in self.answered_reminders:
                return
            text = "Class ended.\n\n"
            text += f"📚 {subject}\n"
            text += f"Period {period} has just finished.\n\n"
            text += f"Did you attend?"
            
            keyboard = [
                [
                    InlineKeyboardButton("Present", callback_data=f"save:present:{check_date.isoformat()}:{period}"),
                    InlineKeyboardButton("Absent", callback_data=f"save:absent:{check_date.isoformat()}:{period}")
                ],
                [
                    InlineKeyboardButton("No Class", callback_data=f"save:no_class:{check_date.isoformat()}:{period}"),
                    InlineKeyboardButton("Remind Later", callback_data=f"remind_later:{check_date.isoformat()}:{period}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup
            )
            
            logger.info(f"Reminder sent for period {period}: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send reminder for period {period}: {e}")
            return False
    
    async def handle_remind_later(self, period: str, subject: str, check_date: date):
        """Handle 'Remind Me Later' action."""
        retry_key = f"{check_date.isoformat()}:{period}"
        if retry_key in self.queued_retries or retry_key in self.answered_reminders:
            return
        self.queued_retries.add(retry_key)
        await self.retry_queue.put({
            "period": period,
            "subject": subject,
            "date": check_date
        })
        logger.info(f"Reminder for period {period} queued for later")
    
    def clear_sent_reminders(self):
        """Clear the sent reminders set (useful for testing or day changes)."""
        self.sent_reminders.clear()
        self.answered_reminders.clear()
        self.queued_retries.clear()
        logger.info("Cleared sent reminders")