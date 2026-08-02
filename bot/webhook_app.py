"""
Flask webhook wrapper for Telegram bot deployment on Render.
This file handles webhook mode for production deployment.
"""
import os
import logging
import threading
import asyncio
import atexit
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from bot.config import Config
from bot.sheets import SheetsManager
from bot.handlers import BotHandlers
from bot.scheduler import ReminderScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Global variables for bot components
application = None
scheduler = None
bot_loop = None

def init_bot():
    """Initialize the bot and scheduler."""
    global application, scheduler, bot_loop
    
    try:
        Config.validate()
        logger.info("Configuration validated")

        logger.info("Connecting to Google Sheets...")
        sheets = SheetsManager()
        sheets.initialize_sheets()
        logger.info("Google Sheets initialized")

        # Create event loop for bot operations
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        # Create Telegram application
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Initialize the application (required for webhook mode)
        bot_loop.run_until_complete(application.initialize())
        logger.info("Application initialized")
        
        handlers = BotHandlers(sheets)

        # Add handlers
        application.add_handler(CommandHandler("start", handlers.start_command))
        application.add_handler(CommandHandler("help", handlers.help_command))
        application.add_handler(CommandHandler("about", handlers.about_command))
        application.add_handler(CommandHandler("reset", handlers.reset_command))
        application.add_handler(CommandHandler("undo", handlers.undo_command))
        application.add_handler(CommandHandler("patterns", handlers.patterns_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.main_menu_message))
        application.add_handler(CallbackQueryHandler(handlers.button_callback, pattern=".*"))

        # Initialize scheduler if chat ID is configured
        chat_id = int(Config.TELEGRAM_CHAT_ID) if Config.TELEGRAM_CHAT_ID else None
        if chat_id:
            bot_instance = application.bot
            scheduler = ReminderScheduler(bot_instance, sheets, chat_id)
            application.bot_data["scheduler"] = scheduler
            handlers.scheduler = scheduler
            
            # Start scheduler in background thread
            def run_scheduler():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(scheduler.start())
                except Exception as e:
                    logger.error(f"Scheduler error: {e}")
                finally:
                    loop.close()
            
            scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
            scheduler_thread.start()
            logger.info("Scheduler started in background thread")
        else:
            logger.warning("TELEGRAM_CHAT_ID not set. Reminders will not be sent.")

        logger.info("Bot initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}", exc_info=True)
        return False

def cleanup_scheduler():
    """Clean up scheduler on shutdown."""
    global scheduler
    if scheduler:
        logger.info("Cleaning up scheduler...")
        try:
            scheduler.stop_sync()
        except Exception as e:
            logger.warning(f"Error during scheduler cleanup: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook updates from Telegram."""
    if application is None:
        return {"error": "Bot not initialized"}, 500
    
    try:
        # Get update from request
        update = Update.de_json(request.get_json(force=True), application.bot)
        
        # Process update in bot's event loop
        if bot_loop:
            bot_loop.run_until_complete(application.process_update(update))
        else:
            # Fallback: create temporary event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application.process_update(update))
            loop.close()
        
        return {"status": "ok"}, 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return {"error": str(e)}, 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Render."""
    return {"status": "healthy", "bot_initialized": application is not None}, 200

@app.route('/')
def index():
    """Root endpoint."""
    return {"status": "Telegram Attendance Bot is running", "mode": "webhook"}, 200

# Initialize bot on module import
init_bot()

# Register cleanup function with error handling after initialization
try:
    atexit.register(cleanup_scheduler)
except Exception as e:
    logger.warning(f"Could not register cleanup function: {e}")