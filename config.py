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

# Output language for YouTube summaries (ISO 639-1 code, e.g. "fa", "en", "de")
RESPONSE_LANGUAGE = os.getenv("RESPONSE_LANGUAGE", "fa")

# Truth Social configuration
TRUTH_ALERT_CHAT_ID = os.getenv("TRUTH_ALERT_CHAT_ID")
TRUTH_RSS_URL = os.getenv("TRUTH_RSS_URL", "https://trumpstruth.org/feed")
# Translation model for Truth Social alerts — uses GEMINI_API_KEY via
# Google AI Studio's OpenAI-compatible endpoint (same as translator.py).
TRUTH_TRANSLATION_MODEL = os.getenv("TRUTH_TRANSLATION_MODEL", "gemini-2.5-flash-lite")

# Instagram session cookies (recommended for downloading posts)
# Get cookies from browser and save as Netscape format (.txt)
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE")

# Multiple cookie files for rotation (comma-separated paths).
# Export a full Netscape cookie file from each browser/profile, upload all to the server.
# The bot rotates to the next file automatically when the current one expires.
# Example: INSTAGRAM_COOKIES_FILES=cookies1.txt,cookies2.txt,cookies3.txt
# Falls back to INSTAGRAM_COOKIES_FILE if not set.
_raw_cookie_files = os.getenv("INSTAGRAM_COOKIES_FILES", "")
INSTAGRAM_COOKIES_FILES: list[str] = [
    s.strip() for s in _raw_cookie_files.split(",") if s.strip()
]
if not INSTAGRAM_COOKIES_FILES and INSTAGRAM_COOKIES_FILE:
    INSTAGRAM_COOKIES_FILES = [INSTAGRAM_COOKIES_FILE]

# Instagram session ID (alternative to full cookies file — easier to obtain)
# Get from browser DevTools > Application > Cookies > instagram.com > sessionid
INSTAGRAM_SESSION_ID = os.getenv("INSTAGRAM_SESSION_ID")

# Multiple session IDs for rotation (comma-separated).
# The bot tries each in order; when one expires it automatically switches to the next.
# Example: INSTAGRAM_SESSION_IDS=id1,id2,id3
# If only INSTAGRAM_SESSION_ID is set, it is used as the single entry.
_raw_session_ids = os.getenv("INSTAGRAM_SESSION_IDS", "")
INSTAGRAM_SESSION_IDS: list[str] = [
    s.strip() for s in _raw_session_ids.split(",") if s.strip()
]
if not INSTAGRAM_SESSION_IDS and INSTAGRAM_SESSION_ID:
    INSTAGRAM_SESSION_IDS = [INSTAGRAM_SESSION_ID]

# Auto-extract cookies directly from a browser (no manual export needed).
# Set to: "chrome", "firefox", "edge", "safari", "chromium", or "brave".
# ONLY works on machines with a GUI browser installed and Instagram logged in.
# NOT suitable for headless servers (Ubuntu ARM, Oracle Cloud, etc.).
INSTAGRAM_COOKIES_FROM_BROWSER = os.getenv("INSTAGRAM_COOKIES_FROM_BROWSER")

# Legacy credentials (broken with current yt-dlp Instagram extractor — use cookies instead)
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")

# Admin Telegram chat ID for system alerts (e.g. expired Instagram cookies).
# Falls back to TRUTH_ALERT_CHAT_ID if not set.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID") or os.getenv("TRUTH_ALERT_CHAT_ID")

# ── Phase 1: Cobalt self-hosted ───────────────────────────────────────────────
# Local Cobalt instance URL. When set, tried FIRST — no cookies needed for public content.
# Deploy: docker run -d --name cobalt --restart unless-stopped \
#   -p 127.0.0.1:9000:9000 -e API_URL=http://localhost:9000 ghcr.io/imputnet/cobalt:10
# Example: COBALT_LOCAL_URL=http://localhost:9000
COBALT_LOCAL_URL = os.getenv("COBALT_LOCAL_URL", "")

# ── Phase 2: Instaloader session file ────────────────────────────────────────
# Session files are far longer-lived than Netscape cookie files (~weeks vs ~hours).
# IMPORTANT: Generate the session ON THE SERVER so Instagram binds it to the server IP:
#   .venv/bin/instaloader --login <dummy_username>
# Use a DEDICATED DUMMY ACCOUNT — never your main Instagram account.
INSTALOADER_SESSION_USER = os.getenv("INSTALOADER_SESSION_USER", "")
# Optional: override session file path. Default: ~/.config/instaloader/session-{user}
INSTALOADER_SESSION_FILE = os.getenv("INSTALOADER_SESSION_FILE", "")

# ── Phase 3: HikerAPI (paid fallback) ────────────────────────────────────────
# Commercial REST API backed by instagrapi with residential proxies. ~$0.001/request.
# https://hikerapi.com — used after instaloader, before gallery-dl.
HIKERAPI_KEY = os.getenv("HIKERAPI_KEY", "")

# ── WireGuard proxy (routes yt-dlp/gallery-dl/instaloader through home IP) ───
# HTTP proxy container on wg_net — traffic exits via home residential IP.
# Empty string disables proxy usage.
RESIDENTIAL_PROXY = os.getenv("RESIDENTIAL_PROXY", "http://127.0.0.1:3128")

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
