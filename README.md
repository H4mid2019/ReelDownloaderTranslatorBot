# 📸 Instagram Downloader Bot

Telegram bot that downloads Instagram posts and transcribes videos with automatic translation to English.

## Quick Setup

```bash
pip install -r requirements.txt
sudo apt install ffmpeg  # Required for audio extraction
cp .env.example .env
# Edit .env with your API keys
python bot.py
```

## Commands

- `/start` - Start bot
- `/help` - Help info
- `/d <url>` - Download Instagram post

## Features

- Download images & videos from public Instagram posts
- Auto-transcribe videos (Groq Whisper)
- Auto-detect video language
- **Translate all non-English videos to English** (except Persian)
- Works in private chat and groups
- Videos >50MB: split into parts and sent automatically
- Large videos: audio is compressed automatically

## API Keys Needed

- **Telegram**: [@BotFather](https://t.me/BotFather)
- **Groq**: [console.groq.com](https://console.groq.com)

## Instagram Authentication

**Image posts/carousels require Instagram cookies.**

1. Login to Instagram in Chrome
2. Install "EditThisCookie" extension
3. Export cookies in Netscape format
4. Save as `instagram_cookies.txt` in bot directory
5. Set `INSTAGRAM_COOKIES_FILE=instagram_cookies.txt` in `.env`

**Reels and videos** work without authentication.

## Model Configuration

```env
TRANSCRIPTION_MODEL=whisper-large-v3
TRANSLATION_MODEL="meta-llama/llama-4-scout-17b-16e-instruct"
```

⚠️ **OPEN-SOURCE** - Non-commercial use only. See LICENSE.
