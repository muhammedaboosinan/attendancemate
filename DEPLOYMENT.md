# Render Deployment Guide

## Quick Setup

### 1. Repository Setup
- Ensure all changes are committed to GitHub
- Verify these files exist in root:
  - `requirements.txt`
  - `Procfile` 
  - `runtime.txt`
  - `bot/webhook_app.py`

### 2. Render Configuration

**Environment Variables (add in Render dashboard):**
```
BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
GOOGLE_SHEET_ID=your_sheet_id_here
```

**Secret Files:**
- Upload `credentials.json` as Secret File
- Path: `credentials.json`

**Optional Variables:**
```
CHECK_INTERVAL_MINUTES=1
REMINDER_RETRY_MINUTES=5
TIMEZONE=Asia/Kolkata
AI_MODEL=gemini-2.5-flash
API_KEY_1=your_gemini_key_1
API_KEY_2=your_gemini_key_2
```

### 3. Webhook Setup

After deployment, set the webhook:

```bash
# Replace with your bot token and Render URL
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-app-name.onrender.com/webhook"}'
```

### 4. Verify Deployment

Check health endpoint:
```
https://your-app-name.onrender.com/health
```

Should return: `{"status": "healthy", "bot_initialized": true}`

### 5. Test Bot

Send `/start` to your bot on Telegram and verify it responds.

## Undo Feature

The bot now supports undoing the last action with confirmation:

1. Use `/undo` command
2. The bot shows what action will be undone
3. Click **CONFIRM** to undo or **CANCEL** to cancel

Undo works for:
- Adding/removing exceptions (holidays)
- Logging attendance (present/absent/no_class)
- Deleting attendance entries
- Changing settings

Undo entries expire after 30 minutes.

## Important Notes

- **Local Development**: Use `python -m bot.main` for polling mode
- **Production**: Render uses webhook mode via `bot/webhook_app.py`
- **Credentials**: Never commit `credentials.json` or `token.env`
- **AI Features**: Add `API_KEY_*` variables for Gemini AI
- **Reminders**: Require `TELEGRAM_CHAT_ID` to work properly