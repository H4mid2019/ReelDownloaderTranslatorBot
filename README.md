# 📸 Multipurpose Downloader & Translator Bot

A powerful Telegram bot that downloads media from **Instagram**, **X/Twitter**, and **YouTube**, transcribes videos, translates content automatically, and can produce detailed AI briefs with sentiment analysis. It also includes a specialized monitor for **Truth Social** alerts.

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
- **Detailed Brief:** Use `/db <url>` for a transcript + AI summary, or `/dbs <url>` to also get sentiment analysis.

### Bot Commands

| Command | Description |
| --- | --- |
| `/start` | Show the main command menu |
| `/help` | Show detailed help (lists every command) |
| `/d <url>` | Manual download (if auto-detect fails) |
| `/dl <url>` | Manual download using Local AI Fallback |
| `/db <url>` | Detailed brief — transcript + summary + highlights + takeaways |
| `/dbs <url>` | Detailed brief **plus** visual / vocal / text sentiment analysis |
| `/chatid` | Get the current chat ID |
| `/clearcache` | *(Admin only)* Clear the AI response cache |
| `/report <range>` | *(Admin only)* Per-method download stats (e.g. `1d`, `12h`, `1m`=month) |
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
- **Detailed Brief (`/db`) & Sentiment (`/dbs`):**
  - Uploads the video directly to **Gemini** for native video understanding.
  - **Hybrid language output:** the transcript stays **verbatim in the spoken language**, while the summary, key highlights, and takeaways are written in your configured `RESPONSE_LANGUAGE` (Persian by default). Set `RESPONSE_LANGUAGE=en` for English.
  - `/dbs` adds a **sentiment** section — observed facial cues, vocal tone, and text sentiment (also in `RESPONSE_LANGUAGE`). Uses a stronger model (`GOOGLE_AI_MODEL_SENTIMENT`, default `gemini-2.5-pro`).
  - Results are cached to avoid redundant API calls.
  - Supported platforms: Instagram, X/Twitter, and **YouTube** videos.
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

### Expiry alerts & automated refresh

Set `ADMIN_CHAT_ID` in `.env` (falls back to `TRUTH_ALERT_CHAT_ID` if not set). An hourly cron (`cookie_health.py`) probes each cookie and sends a Telegram notification when a session's state changes (expired / checkpoint / rate-limited / recovered).

**Automated refresh (optional):** if you provide login credentials, the health check can auto-renew the primary cookie when it expires — no manual export needed. It drives a stealth headless browser ([CloakBrowser](https://github.com/CloakHQ/CloakBrowser), via Docker) that logs in, handles TOTP 2FA, and writes a fresh cookie.

```env
IG_USERNAME=your_account
IG_PASSWORD=your_password
IG_TOTP_SECRET=base32seed          # from the authenticator-app setup
AUTO_REFRESH_COOKIES=true          # default; set false to disable
```

> The **first** automated login from a new server/fingerprint triggers a one-time Instagram "Was this you?" checkpoint — approve it once in the Instagram app, after which logins from that machine are trusted. Manual run: `./refresh_cookies_run.sh`.

> **Note:** `INSTAGRAM_COOKIES_FROM_BROWSER` (auto-extract from Chrome/Firefox) is available but **only works on desktop machines with a GUI browser installed** — not on headless servers (Ubuntu ARM, Oracle Cloud, VPS, etc.).

## 🌐 Residential IP (advanced, optional)

Instagram blocks most datacenter IPs. If you run on a VPS and have downloads failing, you can route the relevant traffic through a **residential IP** (e.g. your home connection) so requests look like a normal user. This project supports a WireGuard tunnel + Docker `wg_net` setup where self-hosted **Cobalt** and the download tools exit via a home router. Full setup and troubleshooting are documented in [wireguard_router_fix.md](wireguard_router_fix.md) and [CONTEXT.md](CONTEXT.md). With a residential IP, Cobalt downloads public Instagram content **without cookies at all**.

⚠️ **OPEN-SOURCE** - Non-commercial use only. See LICENSE.
