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
- **Truth Social Monitor:**
    - Background task that polls Donald Trump's Truths via RSS.
    - Uses AI (Llama 3) to filter for posts related to **Iran**.
    - Sends instant alerts to a configured Telegram chat.
- **Large Video Handling:**
    - Videos >50MB are automatically split into parts to stay within Telegram limits.
    - Automatic remuxing for maximum compatibility with Telegram's video player.

## 🛡️ Local AI Fallback (Optional)

If you are hitting Groq's Free Tier rate limits, you can easily host your own local AI endpoints for free using Docker. The following setup runs extremely well even on CPU-only ARM servers (e.g. Free Tier Oracle Cloud instances).

1. Clone the high-speed Parakeet speech-to-text API:
```bash
git clone https://github.com/groxaxo/parakeet-tdt-0.6b-v3-fastapi-openai.git
cd parakeet-tdt-0.6b-v3-fastapi-openai
```

2. Replace its `docker-compose.yml` with this combined configuration which spins up both Parakeet (Port 8000) and Ollama for LLM fallback (Port 11434):
```yaml
version: '3.8'

services:
  parakeet-cpu:
    build:
      context: .
    container_name: parakeet-stt
    ports:
      - "8000:8000"
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

3. Start the services and tell Ollama to pull the `qwen2.5:3b` model:
```bash
docker compose up -d
docker exec -it local-llm ollama pull qwen2.5:3b
```

4. Update your bot logic to point the OpenAI clients to `http://localhost:8000/v1` for audio transcriptions and `http://localhost:11434/v1` for text translations!

## 🔑 API Keys & Config

- **Telegram Bot Token**: Get from [@BotFather](https://t.me/BotFather)
- **Groq API Key**: Get from [console.groq.com](https://console.groq.com)
- **Truth Social Configuration**:
    - `TRUTH_ALERT_CHAT_ID`: The ID of the group/user where alerts should be sent.
    - `TRUTH_RSS_URL`: The RSS feed to monitor (default provided).

## 🍪 Instagram Authentication

**Image posts/carousels require cookies to bypass some restrictions.**

1. Login to Instagram in Chrome
2. Install "EditThisCookie" extension
3. Export cookies in Netscape format
4. Save as `instagram_cookies.txt` in the bot directory
5. Set `INSTAGRAM_COOKIES_FILE=instagram_cookies.txt` in `.env`

⚠️ **OPEN-SOURCE** - Non-commercial use only. See LICENSE.
