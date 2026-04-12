"""
Truth Social monitor to fetch Donald Trump's posts and use Groq AI
to determine if they relate to Iran, sending an alert if they do.
"""

import asyncio
import logging
import os
import re

try:
    import feedparser  # type: ignore[import-not-found]

    _FEEDPARSER_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency may be absent in CI
    feedparser = None  # type: ignore[assignment]
    _FEEDPARSER_AVAILABLE = False

import groq
from openai import AsyncOpenAI
from telegram import InputMediaPhoto, InputMediaVideo

from config import (
    GROQ_API_KEY,
    OPENROUTER_API_KEY,
    TRUTH_ALERT_CHAT_ID,
    TRUTH_RSS_URL,
    TRUTH_TRANSLATION_MODEL,
)

logger = logging.getLogger(__name__)

LAST_POST_FILE = "last_truth_id.txt"


def _extract_media_urls(entry) -> list[dict]:  # type: ignore[type-arg]
    """Extract image/video URLs from an RSS entry.

    Checks (in priority order):
    1. media:content tags  (feedparser key: media_content)
    2. media:thumbnail tags (feedparser key: media_thumbnail)
    3. enclosure tags
    4. <img>/<video> src attributes embedded in the HTML description

    Returns a list of {"type": "image"|"video", "url": str} dicts.
    Zero network calls — all data is already in memory from feedparser.
    """
    items: list[dict] = []  # type: ignore[type-arg]

    for m in getattr(entry, "media_content", []):
        url = m.get("url", "")
        if url:
            kind = "video" if m.get("medium") == "video" else "image"
            items.append({"type": kind, "url": url})

    for m in getattr(entry, "media_thumbnail", []):
        url = m.get("url", "")
        if url and not any(i["url"] == url for i in items):
            items.append({"type": "image", "url": url})

    for enc in getattr(entry, "enclosures", []):
        url = enc.get("url", "")
        mime = enc.get("type", "")
        if url and not any(i["url"] == url for i in items):
            kind = "video" if "video" in mime else "image"
            items.append({"type": kind, "url": url})

    if not items:
        description = entry.get("description", "") or entry.get("summary", "")
        for url in re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']', description, re.IGNORECASE
        ):
            items.append({"type": "image", "url": url})
        for url in re.findall(
            r'<video[^>]+src=["\']([^"\']+)["\']', description, re.IGNORECASE
        ):
            items.append({"type": "video", "url": url})

    return items


async def _send_persian_translation(application, chat_id: str, text: str) -> None:
    """Translate *text* to Persian via OpenRouter and send as a separate message.

    Runs as a fire-and-forget asyncio task so it never delays the main alert.
    Silently skips if OPENROUTER_API_KEY is not configured.
    """
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    try:
        resp = await client.chat.completions.create(
            model=TRUTH_TRANSLATION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate the following text to Persian (Farsi). "
                        "Return only the translation, no explanations:\n\n"
                        f"{text}"
                    ),
                }
            ],
            max_tokens=1000,
            temperature=0.3,
        )
        persian = resp.choices[0].message.content
        if persian:
            persian = persian.strip()
        if persian:
            await application.bot.send_message(
                chat_id=chat_id,
                text=f"🇮🇷 **ترجمه فارسی:**\n\n{persian}",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.warning(f"Persian translation failed: {e}")


class TruthMonitor:
    def __init__(self):
        self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
        self.rss_url = TRUTH_RSS_URL or "https://trumpstruth.org/feed"
        self.chat_id = TRUTH_ALERT_CHAT_ID
        self.model = "llama-3.3-70b-versatile"

    def _get_last_processed_id(self) -> str:
        if os.path.exists(LAST_POST_FILE):
            with open(LAST_POST_FILE, "r") as f:
                return f.read().strip()
        return ""

    def _save_last_processed_id(self, post_id: str):
        with open(LAST_POST_FILE, "w") as f:
            f.write(post_id)

    async def is_related_to_iran(self, text: str) -> bool:
        if not text or not text.strip():
            return False

        prompt = f"""Read the following post and determine if it mentions or relates to Iran.
Answer ONLY with exactly 'YES' or 'NO'. Nothing else.

Post:
"{text}"
"""
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a specialized AI designed to filter content.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=10,
            )
            result = response.choices[0].message.content.strip().upper()
            return "YES" in result
        except groq.RateLimitError:
            logger.warning("Groq rate limit exceeded while checking Truth Social post.")
            return False
        except Exception as e:
            logger.error(f"Error checking if post is related to Iran: {e}")
            return False

    async def check_feed(self, application) -> None:
        if not _FEEDPARSER_AVAILABLE:
            logger.warning(
                "feedparser is not installed; Truth Social monitoring is disabled."
            )
            return

        if not self.chat_id:
            # We don't want to log this spammy warning every 5 mins.
            # Only checking once and failing silently.
            return

        try:
            feed = await asyncio.to_thread(feedparser.parse, self.rss_url)

            if not feed.entries:
                return

            latest_post = feed.entries[0]
            # Use link or ID
            post_id = latest_post.id if hasattr(latest_post, "id") else latest_post.link

            last_id = self._get_last_processed_id()

            if post_id and post_id != last_id:
                logger.info(f"New Truth Social post detected: {post_id}")

                content = (
                    latest_post.get("description", "")
                    or latest_post.get("summary", "")
                    or latest_post.get("title", "")
                )

                is_target = await self.is_related_to_iran(content)

                if is_target:
                    logger.info("Post relates to Iran! Sending alert...")

                    clean_content = (
                        re.sub(r"<[^>]+>", " ", content).replace("&nbsp;", " ").strip()
                    )

                    msg = "🚨 **TRUTH ALERTS** 🚨\n\n"
                    msg += "**New post from @realDonaldTrump relates to Iran:**\n\n"
                    msg += f"_{clean_content}_\n\n"
                    msg += f"🔗 [View Post]({latest_post.link})"

                    # Extract media URLs synchronously from in-memory RSS data
                    media = _extract_media_urls(latest_post)

                    try:
                        await self._send_alert(application, msg, media)
                    except Exception as e:
                        logger.error(f"Failed to send Telegram alert: {e}")

                    # Fire-and-forget Persian translation — never delays the main alert
                    if OPENROUTER_API_KEY:
                        asyncio.create_task(
                            _send_persian_translation(
                                application, self.chat_id, clean_content
                            )
                        )

                # Save it so we don't process it again
                self._save_last_processed_id(post_id)

        except Exception as e:
            logger.error(f"Error checking Truth Social feed: {e}")

    async def _send_alert(self, application, msg: str, media: list) -> None:  # type: ignore[type-arg]
        """Send the alert message, attaching media when present.

        - No media      → send_message (identical to previous behaviour)
        - Single image  → send_photo with msg as caption (if ≤1024 chars)
        - Single video  → send_video with msg as caption (if ≤1024 chars)
        - Multiple      → send_message for text, then send_media_group
        - Caption overflow → send_message first, then media without caption
        """
        bot = application.bot
        chat_id = self.chat_id

        # Telegram caption limit is 1024 characters
        caption = msg if len(msg) <= 1024 else None

        if not media:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
            return

        if len(media) == 1:
            item = media[0]
            if caption:
                if item["type"] == "video":
                    await bot.send_video(
                        chat_id=chat_id,
                        video=item["url"],
                        caption=caption,
                        parse_mode="Markdown",
                    )
                else:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=item["url"],
                        caption=caption,
                        parse_mode="Markdown",
                    )
            else:
                # Text too long for a caption — send text first, then media bare
                await bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                if item["type"] == "video":
                    await bot.send_video(chat_id=chat_id, video=item["url"])
                else:
                    await bot.send_photo(chat_id=chat_id, photo=item["url"])
            return

        # Multiple media items
        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        media_group = [
            InputMediaVideo(m["url"])
            if m["type"] == "video"
            else InputMediaPhoto(m["url"])
            for m in media[:10]  # Telegram media group limit is 10
        ]
        await bot.send_media_group(chat_id=chat_id, media=media_group)


async def monitor_loop(application):
    """Background task to poll Truth Social periodically."""
    if not _FEEDPARSER_AVAILABLE:
        logger.warning("Truth Social monitor disabled because feedparser is missing.")
        return

    monitor = TruthMonitor()
    logger.info("Started Truth Social monitor loop.")

    # Wait a few seconds before first check to let bot initialize completely
    await asyncio.sleep(5)

    while True:
        try:
            await monitor.check_feed(application)
        except asyncio.CancelledError:
            logger.info("Truth Social monitor cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in monitor loop: {e}")

        # Wait 5 minutes (300 seconds) before checking again
        await asyncio.sleep(300)
