"""
Setup script for Attendance Bot.
Helps initialize the project and verify configuration.
"""
import os
import sys
from pathlib import Path


def check_python_version():
    """Check if Python version is 3.11 or higher."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 11):
        print(f"❌ Python 3.11+ required. Current version: {version.major}.{version.minor}.{version.micro}")
        return False
    print(f"✅ Python version: {version.major}.{version.minor}.{version.micro}")
    return True


def check_dependencies():
    """Check if required packages are installed."""
    required = {
        'telegram': 'python-telegram-bot',
        'gspread': 'gspread',
        'google.oauth2': 'google-auth',
        'dotenv': 'python-dotenv'
    }
    
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
            print(f"✅ {package} installed")
        except ImportError:
            print(f"❌ {package} not installed")
            missing.append(package)
    
    if missing:
        print(f"\n⚠️  Missing packages: {', '.join(missing)}")
        print(f"Install with: pip install -r requirements.txt")
        return False
    return True


def check_env_file():
    """Check if token.env exists and has required values."""
    env_path = Path(__file__).parent / "token.env"
    
    if not env_path.exists():
        print(f"❌ token.env not found")
        print(f"   Copy token.env.example to token.env and configure it")
        return False
    
    print(f"✅ token.env exists")
    
    # Check for required values
    with open(env_path, 'r') as f:
        content = f.read()
    
    if 'BOT_TOKEN=' not in content or 'your_bot_token_here' in content:
        print(f"❌ BOT_TOKEN not configured in token.env")
        return False
    
    print(f"✅ BOT_TOKEN configured")
    
    if 'TELEGRAM_CHAT_ID=' not in content or 'your_chat_id_here' in content:
        print(f"⚠️  TELEGRAM_CHAT_ID not configured (reminders won't work)")
    else:
        print(f"✅ TELEGRAM_CHAT_ID configured")
    
    return True


def check_credentials():
    """Check if Google credentials file exists."""
    creds_path = Path(__file__).parent / "credentials.json"
    
    if not creds_path.exists():
        print(f"❌ credentials.json not found")
        print(f"   Download from Google Cloud Console and save as credentials.json")
        return False
    
    print(f"✅ credentials.json exists")
    
    # Basic validation
    try:
        import json
        with open(creds_path, 'r') as f:
            data = json.load(f)
        
        if 'type' not in data or data['type'] != 'service_account':
            print(f"❌ credentials.json is not a service account key")
            return False
        
        if 'client_email' not in data:
            print(f"❌ credentials.json missing client_email")
            return False
        
        print(f"✅ Service account: {data['client_email']}")
        return True
    except json.JSONDecodeError:
        print(f"❌ credentials.json is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ Error reading credentials.json: {e}")
        return False


def check_directories():
    """Create required directories."""
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    print(f"✅ Logs directory ready: {logs_dir}")


def main():
    """Run setup checks."""
    print("=" * 60)
    print("Attendance Bot - Setup Checker")
    print("=" * 60)
    print()
    
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Environment File", check_env_file),
        ("Google Credentials", check_credentials),
    ]
    
    results = []
    for name, check_func in checks:
        print(f"\n📋 Checking {name}...")
        result = check_func()
        results.append((name, result))
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    all_passed = True
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
        if not result:
            all_passed = False
    
    check_directories()
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All checks passed! You can run the bot with: python -m bot.main")
    else:
        print("❌ Some checks failed. Please fix the issues above before running.")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())