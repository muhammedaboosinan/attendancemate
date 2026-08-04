"""
Flask webhook wrapper for Telegram bot deployment on Render.
This file handles webhook mode for production deployment.
"""
import os
import logging
import threading
import asyncio
import atexit
import signal
import sys
import json
from flask import Flask, request, jsonify
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
scheduler_thread = None
scheduler_loop = None
initialized = False
init_lock = threading.Lock()

@app.route("/", methods=["GET", "HEAD"])
def index():
    """Simple health endpoint for hosting platform health checks."""
    return "AttendanceMate is running", 200

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "bot_initialized": initialized}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive Telegram webhook updates."""
    global application, bot_loop
    
    if not initialized:
        # Try to initialize if not yet done
        with init_lock:
            if not initialized:
                _start_app_once()
    
    if not application or not bot_loop:
        return jsonify({"ok": False, "error": "Bot not initialized"}), 500
    
    try:
        # Parse the update
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, application.bot)
        
        # Process the update on the bot's event loop
        future = asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            bot_loop
        )
        # Wait for processing with a timeout
        future.result(timeout=30)
        
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500

def init_bot():
    """Initialize the bot and scheduler."""
    global application, scheduler, bot_loop, scheduler_thread, scheduler_loop
    
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

        # Create Telegram application - disable updater to prevent getUpdates polling in webhook mode
        application = Application.builder().token(Config.BOT_TOKEN).updater(None).build()
        
        # Initialize and start the application (required for webhook mode)
        bot_loop.run_until_complete(application.initialize())
        bot_loop.run_until_complete(application.start())
        logger.info("Application initialized and started")
        
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
                global scheduler_loop
                scheduler_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(scheduler_loop)
                try:
                    scheduler_loop.run_until_complete(scheduler.start())
                    scheduler_loop.run_forever()
                except asyncio.CancelledError:
                    logger.info("Scheduler loop cancelled")
                except Exception as e:
                    logger.error(f"Scheduler error: {e}")
                finally:
                    # Cancel any remaining tasks
                    try:
                        pending = [t for t in asyncio.all_tasks(scheduler_loop) if not t.done()]
                        for task in pending:
                            task.cancel()
                        if pending:
                            scheduler_loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                    except Exception:
                        pass
                    scheduler_loop.close()
                    logger.info("Scheduler event loop closed")
            
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
    global scheduler, scheduler_loop, scheduler_thread
    if scheduler:
        try:
            logger.info("Cleaning up scheduler...")
            scheduler.running = False
            
            # Stop the scheduler loop gracefully
            if scheduler_loop and scheduler_loop.is_running():
                try:
                    # Schedule stop on the scheduler's event loop
                    future = asyncio.run_coroutine_threadsafe(
                        scheduler.stop(), scheduler_loop
                    )
                    future.result(timeout=5.0)
                except Exception as e:
                    logger.warning(f"Error stopping scheduler: {e}")
                
                # Stop the event loop
                try:
                    scheduler_loop.call_soon_threadsafe(scheduler_loop.stop)
                except Exception as e:
                    logger.warning(f"Error stopping scheduler loop: {e}")
            
            # Wait for the thread to finish
            if scheduler_thread and scheduler_thread.is_alive():
                scheduler_thread.join(timeout=5.0)
                if scheduler_thread.is_alive():
                    logger.warning("Scheduler thread did not stop within timeout")
            
            scheduler = None
            scheduler_loop = None
            scheduler_thread = None
            logger.info("Scheduler cleanup complete")
        except Exception as e:
            logger.warning(f"Error during scheduler cleanup: {e}")

def shutdown_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global application, bot_loop
    logger.info(f"Received signal {signum}, shutting down...")
    cleanup_scheduler()
    # Stop the application
    if application and bot_loop:
        try:
            logger.info("Stopping application...")
            future = asyncio.run_coroutine_threadsafe(application.stop(), bot_loop)
            future.result(timeout=5.0)
            logger.info("Application stopped")
        except Exception as e:
            logger.warning(f"Error stopping application: {e}")
    # Exit gracefully
    try:
        sys.exit(0)
    except SystemExit:
        # Ensure process exits
        os._exit(0)

def _start_app_once():
    """Initialize the bot when the first request arrives (worker-safe)."""
    global initialized
    success = init_bot()
    if success:
        initialized = True

    # Register cleanup function in worker process
    try:
        atexit.register(cleanup_scheduler)
        logger.info("Cleanup function registered")
    except Exception as e:
        logger.warning(f"Could not register cleanup function: {e}")

    # Register signal handlers for graceful shutdown in this process
    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)
        logger.info("Signal handlers registered")
    except Exception as e:
        logger.warning(f"Could not register signal handlers: {e}")

    return success


@app.before_request
def ensure_started():
    """Before handling any request, ensure the bot is initialized once."""
    global initialized
    if initialized:
        return
    try:
        with init_lock:
            if not initialized:
                _start_app_once()
    except Exception as e:
        logger.error(f"Failed to start app in before_request: {e}")
        # Don't raise; allow request to continue (will likely 500)
        initialized = False