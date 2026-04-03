"""
YouTube metadata extraction and native Gemini API video summarization.
Uses YouTube oEmbed API for metadata and Gemini API with native URL ingestion for summarization.
No local video/audio downloads required.
"""

import logging
import re
import urllib.request
import urllib.parse
import json
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class YouTubeSummarizer:
    """Extract metadata from YouTube videos."""

    def get_metadata(self, url: str) -> dict:
        """
        Get video metadata using YouTube oEmbed API (no auth required).

        Returns:
            dict with: video_id, title, thumbnail, url, author_name, error
        """
        try:
            video_id = self._extract_video_id(url)

            # Use oEmbed API - free, no auth required
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            req = urllib.request.Request(
                oembed_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)",
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

            return {
                "video_id": video_id,
                "title": data.get("title", "Unknown Video"),
                "author_name": data.get("author_name", "Unknown"),
                "thumbnail": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                "url": url,
                "error": None,
            }

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"error": "این ویدیو در دسترس نیست یا حذف شده است"}
            elif e.code == 403:
                return {"error": "این ویدیو خصوصی یا محدود شده است"}
            return {"error": f"دریافت اطلاعات ویدیو ناموفق بود: HTTP {e.code}"}
        except Exception as e:
            return {"error": f"دریافت اطلاعات ویدیو ناموفق بود: {str(e)}"}

    def format_duration(self, seconds: int) -> str:
        """Format seconds as HH:MM:SS or MM:SS."""
        if seconds <= 0:
            return "N/A"

        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from various YouTube URL formats."""
        patterns = [
            r"(?:youtube\.com/watch\?v=)([\w-]+)",
            r"(?:youtu\.be/)([\w-]+)",
            r"(?:youtube\.com/shorts/)([\w-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        raise ValueError(f"Could not extract video ID from URL: {url}")


def sanitize_youtube_url(url: str) -> str:
    """
    Sanitize YouTube URL by:
    1. Converting youtu.be/ID to youtube.com/watch?v=ID
    2. Stripping tracking parameters (?si=, &t=, &feature=, etc.)
    3. Removing fragments and ensuring HTTPS

    Args:
        url: Raw YouTube URL

    Returns:
        Sanitized YouTube URL ready for Gemini API consumption
    """
    if not url:
        raise ValueError("URL cannot be empty")

    # Ensure HTTPS
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

    # Parse URL
    parsed = urllib.parse.urlparse(url)

    # Convert youtu.be to youtube.com/watch format
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/").split("?")[0].split("#")[0]
        if not video_id:
            raise ValueError("Invalid youtu.be URL: no video ID")
        url = f"https://www.youtube.com/watch?v={video_id}"
        parsed = urllib.parse.urlparse(url)

    # Extract video ID and rebuild URL with only essential params
    try:
        video_id = YouTubeSummarizer()._extract_video_id(url)
    except ValueError:
        raise ValueError(f"Could not extract video ID from URL: {url}")

    # Detect URL type (watch vs shorts)
    if "youtube.com/shorts/" in url:
        clean_url = f"https://www.youtube.com/shorts/{video_id}"
    elif "youtube.com/watch" in url or "youtu.be" in url:
        clean_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        raise ValueError(f"Unsupported YouTube URL format: {url}")

    logger.debug(f"Sanitized URL: {url} → {clean_url}")
    return clean_url


def _build_summary_prompt(video_title: str, language: str = "fa") -> str:
    """
    Build the prompt for Gemini to summarize the YouTube video.

    Args:
        video_title: Title of the YouTube video
        language: ISO 639-1 language code for the response (default: "fa" for Persian)

    Returns:
        Prompt string for the Gemini API
    """
    if language == "fa":
        return f"""تمام پاسخ خود را به زبان فارسی بنویس. تمام عنوان‌ها، توضیحات و نکات باید به فارسی باشند.

تو یک تحلیلگر محتوای حرفه‌ای هستی. این ویدیوی یوتیوب را تحلیل کن و یک خلاصه جامع ارائه بده.

عنوان ویدیو: "{video_title}"

لطفاً پاسخ خود را دقیقاً در قالب زیر بنویس:

### 📝 خلاصه کوتاه
[۲ تا ۳ جمله که موضوع اصلی و پیام محوری ویدیو را خلاصه می‌کند.]

### 💡 نکات کلیدی
* **[مفهوم/موضوع]**: [توضیح کوتاه]
* **[مفهوم/موضوع]**: [توضیح کوتاه]
* **[مفهوم/موضوع]**: [توضیح کوتاه]
* **[مفهوم/موضوع]**: [توضیح کوتاه]
* **[مفهوم/موضوع]**: [توضیح کوتاه]

### 🚀 درس‌های کاربردی
* [اقدام یا یادگیری کلیدی ۱]
* [اقدام یا یادگیری کلیدی ۲]
* [اقدام یا یادگیری کلیدی ۳]

مختصر، دقیق باش و فقط به اطلاعاتی که واقعاً در ویدیو وجود دارد اشاره کن."""

    # Default English prompt
    return f"""You are an expert content analyst. Analyze this YouTube video and provide a comprehensive summary.

Video Title: "{video_title}"

Please provide your response in the following format:

### 📝 Brief Summary
[Write 2-3 sentences summarizing the core topic and main message.]

### 💡 Key Highlights
* **[Concept/Topic]**: [Brief explanation]
* **[Concept/Topic]**: [Brief explanation]
* **[Concept/Topic]**: [Brief explanation]
* **[Concept/Topic]**: [Brief explanation]
* **[Concept/Topic]**: [Brief explanation]

### 🚀 Actionable Takeaways
* [Action or key learning 1]
* [Action or key learning 2]
* [Action or key learning 3]

Be concise, accurate, and only reference information that is actually present in the video."""


def summarize_youtube_video(youtube_url: str, user_prompt: str) -> str:
    """
    Summarize a YouTube video using Gemini API's native URL ingestion.
    Does NOT download the video locally.

    Args:
        youtube_url: Sanitized YouTube URL (use sanitize_youtube_url first)
        user_prompt: The summarization prompt

    Returns:
        Summary text or error message string
    """
    try:
        client = genai.Client()  # Automatically picks up GEMINI_API_KEY from env

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=types.Content(
                parts=[
                    # Native Ingestion: passing the URL directly to Gemini
                    types.Part(file_data=types.FileData(file_uri=youtube_url)),
                    types.Part(text=user_prompt),
                ]
            ),
        )

        return response.text or ""

    except Exception as e:
        return _handle_gemini_error(e)


def _handle_gemini_error(e: Exception) -> str:
    """
    Map Gemini API errors to user-friendly messages.

    Handles:
    - Private/unlisted videos
    - Videos exceeding context length
    - Age-restricted or safety-blocked content
    - Generic errors

    Args:
        e: The exception from Gemini API

    Returns:
        User-friendly error message
    """
    error_str = str(e).lower()

    # Private/Unlisted Videos
    if any(
        phrase in error_str
        for phrase in ["private", "permission", "access denied", "403", "forbidden"]
    ):
        return "❌ فقط ویدیوهای عمومی یوتیوب قابل پردازش هستند. لطفاً تنظیمات حریم خصوصی ویدیو را بررسی کنید."

    # Context Limit Exceeded (video too long)
    if any(
        phrase in error_str
        for phrase in [
            "context_length_exceeded",
            "too long",
            "token limit",
            "max tokens",
            "resource_exhausted",
        ]
    ):
        return "⚠️ این ویدیو برای پردازش بسیار طولانی است. لطفاً ویدیوی کوتاه‌تری ارسال کنید."

    # Age-Restricted or Safety-Blocked Content
    if any(
        phrase in error_str
        for phrase in ["safety", "age", "restricted", "finishreason", "blocked"]
    ):
        return "⚠️ این ویدیو دارای محدودیت سنی است یا محتوای قابل پردازش ندارد."

    # Video unavailable (404, deleted, etc.)
    if any(phrase in error_str for phrase in ["404", "not found", "unavailable"]):
        return "❌ این ویدیو در دسترس نیست یا حذف شده است."

    # Generic fallback
    logger.error(f"Gemini API error: {e}", exc_info=True)
    return "❌ خلاصه‌سازی این ویدیو ناموفق بود. لطفاً دوباره تلاش کنید."
