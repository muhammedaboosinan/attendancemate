"""
Main application entry point.
Starts the Telegram bot with polling and initializes all components.
"""
import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from bot.config import Config
from bot.sheets import SheetsManager
from bot.handlers import BotHandlers
from bot.scheduler import ReminderScheduler

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for the bot."""
    try:
        Config.validate()
        logger.info("Configuration validated")

        logger.info("Connecting to Google Sheets...")
        sheets = SheetsManager()
        sheets.initialize_sheets()
        logger.info("Google Sheets initialized")

        application = Application.builder().token(Config.BOT_TOKEN).build()
        handlers = BotHandlers(sheets)

        application.add_handler(CommandHandler("start", handlers.start_command))
        application.add_handler(CommandHandler("help", handlers.help_command))
        application.add_handler(CommandHandler("dashboard", handlers.dashboard_command))
        application.add_handler(CommandHandler("today", handlers.today_command))
        application.add_handler(CommandHandler("mark", handlers.mark_command))
        application.add_handler(CommandHandler("nclass", handlers.nclass_command))
        application.add_handler(CommandHandler("timetable", handlers.timetable_command))
        application.add_handler(CommandHandler("menu", handlers.start_command))
        application.add_handler(CommandHandler("stop", handlers.remove_menu_command))
        application.add_handler(CallbackQueryHandler(handlers.button_callback, pattern=".*"))

        chat_id = int(Config.TELEGRAM_CHAT_ID) if Config.TELEGRAM_CHAT_ID else None
        scheduler = None
        if chat_id:
            bot_instance = application.bot
            scheduler = ReminderScheduler(bot_instance, sheets, chat_id)

            async def post_init(application):
                await scheduler.start()
                logger.info("Bot started and scheduler running")

            application.post_init = post_init
        else:
            logger.warning("TELEGRAM_CHAT_ID not set. Reminders will not be sent.")

        logger.info("Starting bot with polling...")
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print(f"❌ Configuration error: {e}")
        print("Please check your token.env file and credentials.json")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
        print(f"❌ Failed to start bot: {e}")


if __name__ == "__main__":
    main()
