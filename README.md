# 📸 Instagram Downloader Bot

Telegram bot that downloads Instagram posts and transcribes videos. Supports private chats and groups.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
python bot.py
```

## Commands

- `/start` - Start bot
- `/help` - Help info
- `/download <url>` - Download Instagram post

## Features

- Download images & videos from public Instagram posts
- Auto-transcribe videos (Groq Whisper)
- Bulgarian → English translation
- Works in private chat and groups
- Videos >50MB: transcript only

## API Keys Needed

- **Telegram**: [@BotFather](https://t.me/BotFather)
- **Groq**: [console.groq.com](https://console.groq.com)

⚠️ **OPEN-SOURCE** - Non-commercial use only. See LICENSE.
