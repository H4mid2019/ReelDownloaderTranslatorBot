"""
Load environment variables from .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

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
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")


# Model configuration (can be changed via .env)
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "whisper-large-v3")
TRANSLATION_MODEL = os.getenv("TRANSLATION_MODEL", "llama-3.3-70b-versatile")

# Local AI Fallback configurations
USE_LOCAL_AI = os.getenv("USE_LOCAL_AI", "False").lower() in ("true", "1", "yes")
LOCAL_STT_URL = os.getenv("LOCAL_STT_URL", "http://localhost:8000/v1")
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
LOCAL_STT_MODEL = os.getenv("LOCAL_STT_MODEL", "base")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:3b")

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Size limits
MAX_VIDEO_SIZE_MB = 45
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024
