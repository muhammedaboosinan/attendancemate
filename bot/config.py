"""
Configuration management for the attendance bot.
Loads settings from environment variables and .env files.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
env_path = Path(__file__).parent.parent / "token.env"
if env_path.exists():
    load_dotenv(env_path)

class Config:
    """Application configuration."""
    
    # Bot configuration
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # Google Sheets configuration
    GOOGLE_CREDENTIALS_PATH: str = os.getenv(
        "GOOGLE_CREDENTIALS_PATH",
        str(Path(__file__).parent.parent / "credentials.json")
    )
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    
    # Telegram configuration
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Scheduler configuration
    CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "1"))
    REMINDER_RETRY_MINUTES: int = int(os.getenv("REMINDER_RETRY_MINUTES", "5"))
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_FILE: Path = LOG_DIR / "bot.log"
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN not found in environment variables")
        if not Path(cls.GOOGLE_CREDENTIALS_PATH).exists():
            raise ValueError(f"Google credentials file not found: {cls.GOOGLE_CREDENTIALS_PATH}")
        return True

# Ensure log directory exists
Config.LOG_DIR.mkdir(exist_ok=True)