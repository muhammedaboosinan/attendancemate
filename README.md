# Attendance Bot

A Telegram bot for tracking class attendance with Google Sheets integration.

## Features

- 📊 Dashboard with overall and subject-wise attendance percentages
- ⏰ Automatic reminders after each class period
- ✅ Track Present/Absent/No Class with inline buttons
- 📚 Subject-wise attendance tracking
- ⏱️ Period-wise attendance tracking
- ⚠️ 75% threshold warnings
- 🎉 Holiday and exception handling
- 📝 Manual corrections and edits

## Project Structure

```
Attendance/
├── bot/
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── sheets.py          # Google Sheets integration
│   ├── handlers.py        # Telegram bot handlers
│   ├── scheduler.py       # Reminder scheduler
│   └── main.py            # Main application entry point
├── requirements.txt
├── token.env              # Bot token and settings
├── credentials.json       # Google Sheets service account
└── README.md
```

## Quick Start

### 1. Prerequisites

- Python 3.11 or higher
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Google Cloud service account with Google Sheets API enabled

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy the example env file and configure it:

```bash
copy token.env.example token.env
```

Edit `token.env` with your values:

```env
BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRstUvWxYz1234567890
TELEGRAM_CHAT_ID=123456789
GOOGLE_SHEET_ID=
CHECK_INTERVAL_MINUTES=1
REMINDER_RETRY_MINUTES=5
```

**To get your Telegram Bot Token:**
1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow instructions
3. Copy the token (format: `1234567890:ABCdef...`)

**To get your Telegram Chat ID:**
1. Start a chat with your bot
2. Send any message
3. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Find your numeric chat ID in the `"chat":{"id":123456789,...}` section

**To get Google Sheet ID (optional):**
1. Create a Google Sheet manually at [sheets.google.com](https://sheets.google.com)
2. Share it with your service account email (from `credentials.json`)
3. Copy the ID from URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`
4. Leave `GOOGLE_SHEET_ID` empty if you want the bot to create one automatically

### 4. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select existing one
3. Enable **Google Sheets API** and **Google Drive API**
4. Create a Service Account and download JSON credentials
5. Save the JSON file as `credentials.json` in the project root
6. Share your Google Sheet with the service account email (if using existing sheet)

### 4. Google Sheets Setup

The bot will automatically create the required sheets on first run:
- `Timetable` - Your class schedule
- `Attendance_Log` - Attendance records
- `Subject_Map` - Subject name normalization
- `Exceptions` - Holidays and cancelled classes
- `Settings` - Bot configuration

Make sure your `credentials.json` service account has edit access to the Google Sheet.

### 5. Initialize Google Sheets

The bot will automatically create and initialize all required sheets on first run:
- `Timetable` - Your class schedule (pre-filled with sample data)
- `Attendance_Log` - Attendance records
- `Subject_Map` - Subject name normalization rules
- `Exceptions` - Holidays and cancelled classes
- `Settings` - Bot configuration

### 6. Run the Bot

```bash
python -m bot.main
```

Or from the project root:

```bash
python bot/main.py
```

You should see:
```
INFO - Configuration validated
INFO - Connecting to Google Sheets...
INFO - Google Sheets initialized
INFO - Starting bot with polling...
```

### 7. Test the Bot

1. Open Telegram and search for your bot
2. Send `/start`
3. You should see the main menu with buttons
4. Click **📊 Dashboard** to see stats (initially empty)
5. Click **📅 Today** to see today's classes
6. Click any period to mark attendance

## Usage

### Main Menu

After starting the bot, use these buttons:

- **📊 Dashboard** - View overall attendance stats
- **📅 Today** - See today's classes and mark attendance
- **📚 Subjects** - View subject-wise attendance
- **⏰ Periods** - View period-wise attendance
- **⚙️ Exceptions** - Manage holidays
- **🔧 Settings** - Bot configuration

### Marking Attendance

When a class ends, the bot sends a reminder with buttons:
- ✅ Present
- ❌ Absent
- ➖ No Class
- ⏰ Remind Later

### Google Sheets Structure

**Timetable Tab:**
| Day | Period | Subject | Start | End |
|-----|--------|---------|-------|-----|
| Monday | 1 | MINOR 1 | 09:30 | 10:30 |
| ... | ... | ... | ... | ... |

**Exceptions Tab:**
| Date | Scope | Period | Reason | Active |
|------|-------|--------|--------|--------|
| 2024-01-15 | day | | Holiday | TRUE |

Set `Active` to `FALSE` to disable an exception.

**Settings Tab:**
| Key | Value |
|-----|-------|
| threshold | 75 |

## Attendance Rules

- ECO, ECO (VNV), and ECO (BKU) are treated as one subject: `ECO`
- No Class entries don't count as missed attendance
- Attendance % = (Present / (Present + Absent)) × 100
- 75% threshold warning shown when approaching minimum attendance

## Reminder Schedule

The bot checks every minute for ended periods and sends reminders:
- Period 1: 10:30-10:35
- Period 2: 11:30-11:35
- Period 3: 12:30-12:35
- Period 4: 14:30-14:35
- Period 5: 15:30-15:35

Skip reminders for holidays and cancelled periods (configured in Exceptions tab).

## Editing the Timetable

You can edit the Timetable tab directly in Google Sheets:
1. Open your Google Sheet
2. Go to Timetable tab
3. Edit Day, Period, Subject, Start, End columns
4. Changes are reflected immediately in the bot

## Troubleshooting

**Bot not starting:**
- Check that `token.env` exists and has BOT_TOKEN
- Verify `credentials.json` exists and is valid
- Ensure internet connection is available

**Sheets not updating:**
- Verify service account has edit access to the sheet
- Check Google Sheets API is enabled in Google Cloud Console
- Review logs in `logs/bot.log`

**Reminders not sending:**
- Ensure bot is running continuously
- Check that TELEGRAM_CHAT_ID is set correctly
- Verify period times in Timetable tab match your schedule

## Deployment to Render

When ready to deploy:

1. Push code to GitHub
2. Create a new Web Service on Render
3. Set environment variables in Render dashboard
4. Upload `credentials.json` as a Render Secret File
5. Use webhook mode instead of polling (update main.py)

## Testing

### Local Testing Checklist

- [ ] Bot starts without errors
- [ ] `/start` shows main menu
- [ ] Dashboard shows empty stats initially
- [ ] Today shows your timetable
- [ ] Marking attendance updates Google Sheets
- [ ] Dashboard reflects attendance changes
- [ ] Subject stats calculate correctly
- [ ] Reminders send at period end times
- [ ] Exceptions prevent reminders

### Manual Testing Steps

1. **Test basic navigation:**
   - Start bot with `/start`
   - Click each menu button
   - Verify all screens load

2. **Test attendance marking:**
   - Go to Today → Select a period
   - Click Present/Absent/No Class
   - Verify confirmation message
   - Check Google Sheets for new entry

3. **Test dashboard:**
   - Mark attendance for multiple periods
   - Check Dashboard shows correct percentages
   - Verify subject-wise stats

4. **Test exceptions:**
   - Add a holiday in Exceptions tab
   - Verify bot shows "Holiday" status
   - Verify no reminders sent

5. **Test timetable editing:**
   - Edit Timetable tab in Google Sheets
   - Verify bot reflects changes immediately

## Project Structure

```
Attendance/
├── bot/
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── sheets.py          # Google Sheets integration
│   ├── handlers.py        # Telegram bot handlers
│   ├── scheduler.py       # Reminder scheduler
│   └── main.py            # Main application entry point
├── requirements.txt
├── token.env              # Bot token and settings (create from token.env.example)
├── token.env.example      # Example environment file
├── credentials.json       # Google Sheets service account (from Google Cloud)
├── logs/
│   └── bot.log            # Application logs (auto-created)
└── README.md
```

## Troubleshooting

**Bot not starting:**
- Check that `token.env` exists and has BOT_TOKEN
- Verify `credentials.json` exists and is valid
- Ensure internet connection is available
- Check `logs/bot.log` for errors

**Sheets not updating:**
- Verify service account has edit access to the sheet
- Check Google Sheets API is enabled in Google Cloud Console
- Ensure sheet ID is correct if using existing sheet
- Review logs in `logs/bot.log`

**Reminders not sending:**
- Ensure bot is running continuously
- Check that TELEGRAM_CHAT_ID is set correctly in `token.env`
- Verify period times in Timetable tab match your schedule
- Check bot has permission to send messages

**Import errors:**
- Run from project root: `python -m bot.main`
- Ensure all dependencies installed: `pip install -r requirements.txt`
- Check Python version: `python --version` (should be 3.11+)

## Deployment to Render

When ready to deploy to Render:

1. Push code to GitHub
2. Create new Web Service on [Render](https://render.com)
3. Set environment variables in Render dashboard:
   - `BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_CREDENTIALS_PATH` (path to uploaded credentials file)
4. Upload `credentials.json` as Render Secret File
5. Use webhook mode instead of polling (modify `main.py`)

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `BOT_TOKEN` | (required) | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | (required for reminders) | Your Telegram chat ID |
| `GOOGLE_SHEET_ID` | (auto-create) | Google Sheet ID or empty to create new |
| `CHECK_INTERVAL_MINUTES` | 1 | How often to check for ended periods |
| `REMINDER_RETRY_MINUTES` | 5 | Delay for "Remind Me Later" feature |

## Logs

Logs are stored in `logs/bot.log` with timestamps and log levels.

## License

MIT

## Support

For issues or questions:
1. Check `logs/bot.log` for error messages
2. Review Google Cloud Console for API errors
3. Verify all configuration in `token.env`
