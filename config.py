"""
Load environment variables from .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Google AI Studio (Gemini) — used by /dl command fallback
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_AI_MODEL = os.getenv("GOOGLE_AI_MODEL", "gemini-2.5-flash-lite")

# Truth Social configuration
TRUTH_ALERT_CHAT_ID = os.getenv("TRUTH_ALERT_CHAT_ID")
TRUTH_RSS_URL = os.getenv("TRUTH_RSS_URL", "https://trumpstruth.org/feed")

# Instagram session cookies (recommended for downloading posts)
# Get cookies from browser and save as Netscape format (.txt)
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE")

# Instagram session ID (alternative to full cookies file — easier to obtain)
# Get from browser DevTools > Application > Cookies > instagram.com > sessionid
INSTAGRAM_SESSION_ID = os.getenv("INSTAGRAM_SESSION_ID")

# Legacy credentials (broken with current yt-dlp Instagram extractor — use cookies instead)
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")

# Model configuration (can be changed via .env)
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "whisper-large-v3")
TRANSLATION_MODEL = os.getenv("TRANSLATION_MODEL", "llama-3.3-70b-versatile")

# Google AI Fallback flag — enables the /dl command (was previously local AI)
USE_LOCAL_AI = os.getenv("USE_LOCAL_AI", "False").lower() in ("true", "1", "yes")

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Size limits
MAX_VIDEO_SIZE_MB = 45
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

# AI result cache — stores transcripts & translations in SQLite to avoid duplicate API calls
ENABLE_AI_CACHE = os.getenv("ENABLE_AI_CACHE", "true").lower() in ("true", "1", "yes")
CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "30"))
CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", "./ai_cache.db")
