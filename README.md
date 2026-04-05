# 📸 Multipurpose Downloader & Translator Bot

A powerful Telegram bot that downloads media from **Instagram** and **X/Twitter**, transcribes videos, and translates content automatically. It also includes a specialized monitor for **Truth Social** alerts.

## 🚀 Quick Setup

```bash
pip install -r requirements.txt
# Ensure ffmpeg is installed on your system
# Linux: sudo apt install ffmpeg
# Windows: Download from gyan.dev and add to PATH
cp .env.example .env
# Edit .env with your API keys (Telegram, Groq, etc.)
python bot.py
```

## 🎮 How to Use

Simply send or forward any supported link to the bot. It will automatically detect the platform and process the content.

- **Automated:** No commands needed! Just paste a link.
- **Manual:** Use `/d <url>` if auto-detection doesn't trigger.
- **Detailed Brief:** Use `/db <url>` to get a full transcript + AI summary in the video's source language.

### Bot Commands

| Command | Description |
| --- | --- |
| `/start` | Show the main command menu |
| `/help` | Show detailed help |
| `/d <url>` | Manual download (if auto-detect fails) |
| `/dl <url>` | Manual download using Local AI Fallback |
| `/db <url>` | Detailed source-language transcript + summary brief |
| `/chatid` | Get the current chat ID |
| `/clearcache` | Clear the AI response cache |
| `/setcookie <id>` | *(Admin only)* Update Instagram session ID without SSH or restart |

## ✨ Features

- **Instagram Downloader:**
  - Supports Reels, TV, and **Posts (/p/)**.
  - Downloads single images, videos, and **Carousels (Galleries)**.
  - Automatically attaches the original caption and its English translation.
- **X/Twitter Downloader:**
  - Downloads status videos.
  - Supports text-only posts with automatic translation.
- **AI Transcription & Translation:**
  - Uses **Groq Whisper** for lightning-fast transcription.
  - Auto-detects video language.
  - **Translates all non-English videos to English** (Except Persian).
- **Detailed Brief (`/db`):**
  - Uploads the video directly to **Gemini** for native video understanding.
  - Returns a verbatim transcript, summary, key highlights, and takeaways — all in the **source language** of the video (including Persian/Farsi, Arabic, etc.).
  - Results are cached to avoid redundant API calls.
  - Supported platforms: Instagram video posts, X/Twitter video posts.
- **YouTube Summarizer:**
  - Paste any YouTube link and the bot fetches metadata and generates an AI summary via Gemini.
- **Truth Social Monitor:**
  - Background task that polls Donald Trump's Truths via RSS.
  - Uses AI (Llama 3) to filter for posts related to **Iran**.
  - Sends instant alerts to a configured Telegram chat.
- **Large Video Handling:**
  - Videos >50MB are automatically split into parts to stay within Telegram limits.
  - Automatic remuxing for maximum compatibility with Telegram's video player.

## 🛡️ Local AI Fallback (Optional)

If you are hitting Groq's Free Tier rate limits, you can easily host your own local AI endpoints for free using Docker. The following setup runs extremely well even on CPU-only ARM servers (e.g. Free Tier Oracle Cloud instances).

1. Create a new directory on your server (e.g. `ai-fallback`) and create a `docker-compose.yml` file with this configuration to spin up both Faster-Whisper (Port 8000) and Ollama for LLM fallback (Port 11434):

```yaml
services:
  whisper-stt:
    image: fedirz/faster-whisper-server:latest-cpu
    container_name: local-stt
    ports:
      - "8000:8000"
    environment:
      - WHISPER__MODEL=base
    restart: unless-stopped

  local-llm:
    image: ollama/ollama:latest
    container_name: local-llm
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped

volumes:
  ollama_data:
```

1. Start the services and tell Ollama to pull the `qwen2.5:3b` model:

```bash
docker compose up -d
docker exec -it local-llm ollama pull qwen2.5:3b
```

1. Update your bot logic to point the OpenAI clients to `http://localhost:8000/v1` for audio transcriptions and `http://localhost:11434/v1` for text translations!

## 🔑 API Keys & Config

- **Telegram Bot Token**: Get from [@BotFather](https://t.me/BotFather)
- **Groq API Key**: Get from [console.groq.com](https://console.groq.com)
- **Truth Social Configuration**:
  - `TRUTH_ALERT_CHAT_ID`: The ID of the group/user where alerts should be sent.
  - `TRUTH_RSS_URL`: The RSS feed to monitor (default provided).

## 🍪 Instagram Authentication

Instagram requires valid session cookies to download posts. The bot supports multiple methods with automatic rotation — when one session expires, it silently switches to the next.

### Method 1 — Single cookie file *(simplest)*

1. Log into Instagram in Chrome
2. Install the "EditThisCookie" extension
3. Export cookies in Netscape format → save as `instagram_cookies.txt`
4. Upload the file to your server
5. Set in `.env`:

   ```env
   INSTAGRAM_COOKIES_FILE=instagram_cookies.txt
   ```

### Method 2 — Multiple cookie files with rotation *(recommended for servers)*

Export a full Netscape cookie file from each browser/profile (Chrome, Firefox, Chrome Profile 2) — all can use the **same Instagram account**. Each browser gets its own independent session, multiplying your uptime before any manual refresh is needed.

```env
INSTAGRAM_COOKIES_FILES=cookies1.txt,cookies2.txt,cookies3.txt
```

When one file's session expires the bot automatically rotates to the next. A Telegram alert is sent to `ADMIN_CHAT_ID` when the entire pool is exhausted.

### Method 3 — Session ID rotation *(lightweight alternative)*

Copy just the `sessionid` value from browser DevTools (Application → Cookies → `sessionid`) for each session:

```env
INSTAGRAM_SESSION_IDS=id1,id2,id3
```

### Refreshing cookies without SSH — `/setcookie`

Once the bot is running, you can push a fresh session ID to it directly from Telegram:

```text
/setcookie <your_new_sessionid>
```

The bot will:

- Delete your message immediately (to avoid leaking the token in chat history)
- Update the active session in memory
- Persist the new ID to `.env` so it survives restarts
- Confirm with a health check result

This command is restricted to `ADMIN_CHAT_ID`. Get your chat ID via `/chatid` in a private chat with the bot.

### Expiry alerts

Set `ADMIN_CHAT_ID` in `.env` (falls back to `TRUTH_ALERT_CHAT_ID` if not set). The bot checks cookie health every 6 hours and sends a Telegram notification when the session pool is expired, before users start seeing errors.

```env
ADMIN_CHAT_ID=your_personal_chat_id
```

> **Note:** `INSTAGRAM_COOKIES_FROM_BROWSER` (auto-extract from Chrome/Firefox) is available but **only works on desktop machines with a GUI browser installed** — not on headless servers (Ubuntu ARM, Oracle Cloud, VPS, etc.).

⚠️ **OPEN-SOURCE** - Non-commercial use only. See LICENSE.
