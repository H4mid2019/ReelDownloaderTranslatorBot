"""
Telegram bot for downloading Instagram posts with transcription and translation.
Supports both images and videos from public Instagram posts.
"""

import logging
import os
import re
import subprocess
import tempfile
import asyncio
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    MAX_VIDEO_SIZE_MB,
    MAX_VIDEO_SIZE_BYTES,
    LOG_LEVEL,
    USE_LOCAL_AI,
)
from config import ENABLE_AI_CACHE, CACHE_TTL_DAYS, CACHE_DB_PATH
from cache import AICache, extract_post_id
from downloader import download_video, detect_platform, download_youtube_audio
from transcriber import Transcriber
from translator import Translator
from truth_monitor import monitor_loop
from youtube_summarizer import YouTubeSummarizer
from summarizer import Summarizer
from typing import Optional

# ── Global AI cache (None when ENABLE_AI_CACHE=false) ───────────────────────
_cache: Optional[AICache] = (
    AICache(CACHE_DB_PATH, CACHE_TTL_DAYS) if ENABLE_AI_CACHE else None
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)


# Disclaimer text
DISCLAIMER = """
⚠️ **DISCLAIMER** - This bot is **OPEN-SOURCE** software.
• Free for personal/non-commercial use only
• Commercial use is strictly prohibited
• Use at your own risk
"""

# Chunk size for splitting large videos (30MB target provides 20MB buffer for keyframe bleeding)
VIDEO_CHUNK_SIZE_BYTES = 30 * 1024 * 1024


async def post_init(application: Application):
    """Start background tasks after bot initialization."""
    # Purge expired cache entries at startup
    if _cache:
        removed = _cache.purge_expired()
        if removed:
            logger.info(f"AI cache: purged {removed} expired entries at startup")
        stats = _cache.stats()
        logger.info(
            f"AI cache ready — valid={stats['valid']} entries, "
            f"total_hits={stats['total_hits']}, db={CACHE_DB_PATH}"
        )
    task = asyncio.create_task(monitor_loop(application))
    application.bot_data["truth_monitor_task"] = task


async def post_stop(application: Application):
    """Clean up background tasks before bot shutdown."""
    task = application.bot_data.get("truth_monitor_task")
    if task and not task.done():
        task.cancel()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not update.message:
        return
    await update.message.reply_text(
        "👋 Welcome to Video Downloader Bot!\n\n"
        "I can download media from **Instagram (Posts, Reels, TV)** and **X/Twitter (Videos)**, "
        "and generate AI summaries for **YouTube videos**.\n\n"
        "🚀 **How to use:**\n"
        "Just send or forward any supported link! I'll detect it and start processing automatically.\n\n"
        "📁 **Features:**\n"
        "• **Download:** Support for Instagram posts, reels, carousels and Twitter videos.\n"
        "• **Transcription:** Automatically transcribes speech from videos.\n"
        "• **Translation:** Translates non-English/non-Persian speech to English.\n"
        "• **YouTube Summaries:** Get AI-powered summaries with key highlights, takeaways, and brief overviews.\n"
        "• **Captions:** Instagram post captions are attached to the media with their translation.\n"
        "• **Truth Monitor:** Background tracking of Trump's Truth Social for Iran-related posts.\n\n"
        "📝 **Commands:**\n"
        "/chatid - Get the current chat ID\n"
        "/help - Show detailed help\n"
        "/d <url> - Manual download (if auto-detect fails)\n"
        "/dl <url> - Manual download using Local AI Fallback\n\n" + DISCLAIMER
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not update.message:
        return
    await update.message.reply_text(
        "📖 **How to use:**\n\n"
        "1️⃣ **Send a Link:** Simply paste an Instagram or X/Twitter link. I will detect it automatically.\n"
        "2️⃣ **Wait for Download:** I'll download the media (Photo, Video, or Gallery).\n"
        "3️⃣ **Transcription & Translation:**\n"
        "   • **Persian:** No translation needed.\n"
        "   • **English:** Transcript provided.\n"
        "   • **Other Languages:** Transcript + English translation provided.\n\n"
        "✅ **Supported Platforms:**\n"
        "• **Instagram:** Reels, TV, and Posts (/p/ posts are now supported!)\n"
        "• **X/Twitter:** Status videos and text-only posts.\n"
        "• **Truth Social:** Automated monitoring for specific alerts.\n\n"
        "💡 **Pro Tip:** Videos over 50MB are automatically split into smaller parts for Telegram compatibility.\n\n"
        + DISCLAIMER
    )


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /chatid command to easily get the current group or user chat ID."""
    if not update.message:
        return
    chat = update.message.chat
    chat_type = chat.type
    chat_title = chat.title or "Private Chat"
    await update.message.reply_text(
        f"📝 **Chat Information**\n"
        f"• **ID:** `{chat.id}`\n"
        f"• **Type:** {chat_type}\n"
        f"• **Title:** {chat_title}\n\n"
        f"You can copy the ID above and paste it into your configuration."
    )


async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clearcache command — wipes all AI cache entries."""
    if not update.message:
        return
    if not _cache:
        await update.message.reply_text(
            "ℹ️ AI cache is **disabled** (`ENABLE_AI_CACHE=false` in config)."
        )
        return

    stats_before = _cache.stats()
    removed = _cache.clear_all()
    await update.message.reply_text(
        f"🗑️ **AI Cache Cleared**\n"
        f"• Removed **{removed}** entries\n"
        f"• Previous total hits: **{stats_before['total_hits']}**\n\n"
        f"Cache will rebuild automatically as new content is processed."
    )


def split_video(
    video_path: str, chunk_size_bytes: int = VIDEO_CHUNK_SIZE_BYTES
) -> list:
    """
    Split a video file into chunks of approximately chunk_size_bytes.
    Returns a list of file paths for the chunks.
    Uses ffmpeg to split by time segments proportional to chunk size.
    """
    file_size = os.path.getsize(video_path)
    if file_size <= chunk_size_bytes:
        return [video_path]

    # Get video duration using ffprobe
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        total_duration = float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get video duration: {e}")
        return [video_path]  # Return original if we can't split

    # Calculate number of chunks needed
    num_chunks = int(file_size / chunk_size_bytes) + 1
    chunk_duration = total_duration / num_chunks

    chunk_dir = tempfile.mkdtemp(prefix="video_chunks_")
    chunk_paths = []

    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_path = os.path.join(chunk_dir, f"chunk_{i + 1:03d}.mp4")

        cmd = [
            "ffmpeg",
            "-ss",
            str(start_time),
            "-i",
            video_path,
            "-t",
            str(chunk_duration),
            "-c",
            "copy",  # No re-encoding for speed
            "-avoid_negative_ts",
            "1",
            "-y",
            chunk_path,
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                chunk_paths.append(chunk_path)
        except Exception as e:
            logger.error(f"Failed to create chunk {i + 1}: {e}")

    return chunk_paths if chunk_paths else [video_path]


def cleanup_file(file_path: str):
    """Safely delete a downloaded file."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup file {file_path}: {e}")


def cleanup_chunks(chunk_paths: list, original_path: str):
    """Clean up chunk files and their directory (if different from original)."""
    for chunk in chunk_paths:
        if chunk != original_path:
            cleanup_file(chunk)
            # Try to remove the chunk directory
            try:
                chunk_dir = str(os.path.dirname(chunk))
                if os.path.isdir(chunk_dir) and not os.listdir(chunk_dir):
                    os.rmdir(chunk_dir)
            except Exception:
                pass


async def send_video_or_chunks(
    update: Update,
    video_path: str,
    file_size_bytes: int,
    file_size_mb: float,
    lang_name: str,
    platform: str,
    post_caption: Optional[str] = None,
    translated_caption: Optional[str] = None,
    status_msg=None,
) -> bool:
    """Send video or split chunks. Remuxes for Telegram compatibility if needed."""
    # Remux for Telegram (stream copy + faststart)
    remuxed_path = video_path
    if file_size_bytes > 30 * 1024 * 1024:  # Remux large videos
        remuxed_path = video_path + ".telegram.mp4"
        cmd = [
            "ffmpeg",
            "-i",
            video_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            remuxed_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            if os.path.exists(remuxed_path):
                video_path = remuxed_path
        except Exception as e:
            logger.warning(f"Remux failed: {e}")

    # Always check ACTUAL on-disk size before deciding single vs split (prevents 413 errors)
    actual_size_bytes = os.path.getsize(video_path)
    actual_size_mb = actual_size_bytes / (1024 * 1024)

    if actual_size_bytes <= MAX_VIDEO_SIZE_BYTES:
        if status_msg:
            await status_msg.edit_text("📤 Sending video...")

        footer = f"\n\n🎬 Video ({platform})\n📏 Size: {actual_size_mb:.2f} MB\n🔊 Language: {lang_name}"
        if post_caption and translated_caption:
            sep = "\n\n🌐 **Translation:**\n"
            max_len = 1024 - len(footer) - len(sep)
            half = max_len // 2
            trunc_orig = (
                post_caption[: half - 3] + "..."
                if len(post_caption) > half
                else post_caption
            )
            trunc_trans = (
                translated_caption[: max_len - len(trunc_orig) - 3] + "..."
                if len(translated_caption) > (max_len - len(trunc_orig))
                else translated_caption
            )
            caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
        elif post_caption:
            caption = f"{post_caption[: 1024 - len(footer) - 3]}{footer}"
        else:
            caption = footer.strip()

        if not update.message:
            return False
        await update.message.reply_video(
            video=open(video_path, "rb"),
            caption=caption,
            read_timeout=120,
            write_timeout=120,
        )
        return True
    else:
        if status_msg:
            await status_msg.edit_text(
                f"📹 Video is {actual_size_mb:.2f} MB — splitting into parts..."
            )
        chunk_paths = split_video(video_path)

        if len(chunk_paths) == 1 and chunk_paths[0] == video_path:
            if status_msg:
                await status_msg.edit_text(
                    f"⚠️ Video ({actual_size_mb:.2f} MB) exceeds {MAX_VIDEO_SIZE_MB}MB limit and could not be split."
                )
            return False

        total_parts = len(chunk_paths)
        for idx, chunk_path in enumerate(chunk_paths, 1):
            chunk_size_bytes = os.path.getsize(chunk_path)
            chunk_size_mb = chunk_size_bytes / (1024 * 1024)

            # Failsafe against Telegram's hard 50MB limit
            if chunk_size_mb > 49.5:
                if status_msg:
                    await status_msg.reply_text(
                        f"⚠️ **Skipped Part {idx}/{total_parts}:**\nThis segment is {chunk_size_mb:.1f} MB, which randomly exceeded Telegram's 50MB hard limit due to the video's extreme keyframe distribution."
                    )
                continue
            footer = f"\n\n🎬 Video ({platform}) — Part {idx}/{total_parts}\n📏 Part: {chunk_size_mb:.2f} MB\n🔊 Language: {lang_name}"
            if post_caption and translated_caption:
                sep = "\n\n🌐 **Translation:**\n"
                max_len = 1024 - len(footer) - len(sep)
                half = max_len // 2
                trunc_orig = (
                    post_caption[: half - 3] + "..."
                    if len(post_caption) > half
                    else post_caption
                )
                trunc_trans = (
                    translated_caption[: max_len - len(trunc_orig) - 3] + "..."
                    if len(translated_caption) > (max_len - len(trunc_orig))
                    else translated_caption
                )
                caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
            elif post_caption:
                caption = f"{post_caption[: 1024 - len(footer) - 3]}{footer}"
            else:
                caption = footer.strip()

            if not update.message:
                continue
            await update.message.reply_video(
                video=open(chunk_path, "rb"),
                caption=caption,
                read_timeout=120,
                write_timeout=120,
            )

        cleanup_chunks(chunk_paths, video_path)
        if remuxed_path != video_path and os.path.exists(remuxed_path):
            os.remove(remuxed_path)
        if status_msg:
            await status_msg.delete()
        return True


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main command handler for /download."""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a URL.\n"
            "Usage: /d <url>\n\n"
            "Example: /d https://www.instagram.com/reel/ABC123/\n\n" + DISCLAIMER
        )
        return

    url = " ".join(context.args)
    await process_url(update, context, url)


async def download_local_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler for /dl to test Local AI fallback."""
    if not update.message:
        return
    if not USE_LOCAL_AI:
        await update.message.reply_text(
            "⚠️ **Local AI** fallback is disabled in the bot's configuration."
        )
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a URL.\n"
            "Usage: /dl <url>\n\n"
            "Example: /dl https://www.instagram.com/reel/ABC123/\n\n" + DISCLAIMER
        )
        return

    url = " ".join(context.args)
    await process_url(update, context, url, use_local_ai=True)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular text messages to auto-detect supported URLs."""
    if not update.message:
        return

    text = update.message.text or update.message.caption or ""

    # Check if there are any supported URLs in the text
    urls = re.findall(r"(https?://[^\s]+)", text)

    for url in urls:
        if detect_platform(url):
            await process_url(update, context, url)
            return


# ── YouTube URL handling ───────────────────────────────────────────────────────

# In-process lock to prevent concurrent extraction of same video
_processing_videos: dict[str, asyncio.Task] = {}


async def process_youtube_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_msg,
    use_local_ai: bool = False,
):
    """Handle YouTube URL - extract transcript and generate summary."""
    summarizer = YouTubeSummarizer()

    # 1. Get metadata
    await status_msg.edit_text("📹 Fetching video metadata...")
    meta = summarizer.get_metadata(url)

    if meta.get("error"):
        await status_msg.edit_text(f"❌ {meta['error']}")
        return

    video_id = meta.get("video_id")

    # 2. Check if already being processed (soft lock)
    cache_key = f"summary:youtube:{video_id}"

    # Check cache first (fast path)
    if _cache:
        cached_summary = _cache.get(cache_key)
        if cached_summary:
            await send_youtube_summary(update, meta, cached_summary, status_msg)
            return

        # Check if another coroutine is processing this video
        if video_id in _processing_videos:
            await status_msg.edit_text(
                "⏳ This video is being processed by another request. Please wait..."
            )
            # Wait for the other task
            try:
                await asyncio.wait_for(_processing_videos[video_id], timeout=120)
            except asyncio.TimeoutError:
                pass
            # Try cache again
            cached_summary = _cache.get(cache_key)
            if cached_summary:
                await send_youtube_summary(update, meta, cached_summary, status_msg)
                return

    # 3. Extract transcript
    await status_msg.edit_text("📝 Extracting transcript...")
    transcript_result = summarizer.extract_transcript(url)

    if transcript_result.get("error"):
        await status_msg.edit_text(f"⚠️ {transcript_result['error']}\n\n🔄 Falling back to audio extraction...")
        
        dl_result = download_youtube_audio(url, max_duration_sec=1800)
        
        if dl_result.error:
            await status_msg.edit_text(f"❌ Audio fallback failed: {dl_result.error}")
            return
            
        audio_path = dl_result.file_path
        await status_msg.edit_text("🎤 Audio extracted. Transcribing...")
        
        transcriber = Transcriber()
        audio_trans_result = transcriber.transcribe_audio(audio_path, use_local_ai=use_local_ai)
        cleanup_file(audio_path)
        
        if audio_trans_result.get("error") and not audio_trans_result.get("skipped"):
            await status_msg.edit_text(f"❌ Transcription failed: {audio_trans_result['error']}")
            return
            
        raw_transcript = audio_trans_result["text"]
        is_auto_caption = audio_trans_result.get("auto_detected", True)
        transcript_result["language"] = audio_trans_result.get("detected_language", "unknown")
    else:
        raw_transcript = transcript_result["text"]
        is_auto_caption = transcript_result.get("is_auto_generated", False)

    if not raw_transcript or not raw_transcript.strip():
        await status_msg.edit_text(
            "❌ This video doesn't have enough speech content to summarize."
        )
        return

    # 4. Clean transcript
    clean_transcript = summarizer.clean_transcript(raw_transcript)
    transcript_quality, quality_note = summarizer.detect_transcript_quality(
        clean_transcript, is_auto_caption
    )

    # 5. Generate summary
    await status_msg.edit_text("🤖 Generating AI summary...")
    ai_summarizer = Summarizer()
    summary_result = ai_summarizer.generate_summary(
        clean_transcript,
        meta["title"],
        source_language=transcript_result.get("language", "unknown"),
    )

    if summary_result.get("error"):
        await status_msg.edit_text(
            f"❌ Summary generation failed: {summary_result['error']}"
        )
        return

    # 6. Add metadata to result
    summary_result["transcript_quality"] = transcript_quality
    summary_result["is_auto_caption"] = is_auto_caption

    # 7. Cache results
    if _cache:
        _cache.set(cache_key, summary_result)

    # 8. Send response
    await send_youtube_summary(update, meta, summary_result, status_msg)

    # 9. Cleanup lock
    if video_id in _processing_videos:
        del _processing_videos[video_id]


async def send_youtube_summary(
    update: Update, meta: dict, summary: dict, status_msg
):
    """Format and send YouTube summary to user."""
    title = meta.get("title", "Unknown Video")
    duration = meta.get("duration_formatted", "N/A")
    url = meta.get("url", "")

    summary_text = summary.get("summary_text", "")
    source_lang = summary.get("source_language", "en")
    transcript_quality = summary.get("transcript_quality", "unknown")

    # Quality indicator
    quality_emoji = {
        "excellent": "✅",
        "good": "👍",
        "fair": "⚠️",
        "poor": "⚠️",
        "very_poor": "❌",
    }

    response = f"🎬 **{title}**\n"
    response += f"⏱️ Duration: {duration}\n"
    response += f"🔗 [Watch on YouTube]({url})\n"

    if source_lang != "en":
        lang_names = {
            "ja": "Japanese",
            "ko": "Korean",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
            "pt": "Portuguese",
            "ru": "Russian",
            "zh": "Chinese",
            "ar": "Arabic",
            "hi": "Hindi",
            "it": "Italian",
            "tr": "Turkish",
            "nl": "Dutch",
            "pl": "Polish",
            "vi": "Vietnamese",
            "th": "Thai",
            "id": "Indonesian",
        }
        lang_name = lang_names.get(source_lang, source_lang.upper())
        response += f"🌐 Source: {lang_name}\n"

    quality_icon = quality_emoji.get(transcript_quality, "❓")
    response += f"{quality_icon} {transcript_quality.replace('_', ' ').title()}\n"

    response += "\n━━━━━━━━━━━━━━━━━━━━\n\n"
    response += summary_text
    response += "\n\n━━━━━━━━━━━━━━━━━━━━"

    # Delete status message
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # Split message if too long (Telegram ~4096 char limit)
    if len(response) > 4000:
        # Send in parts (chunking by paragraph if possible)
        chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for i, chunk in enumerate(chunks):
            await update.message.reply_text(
                chunk, disable_web_page_preview=(i == 0)
            )
    else:
        await update.message.reply_text(response, disable_web_page_preview=True)


async def process_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    use_local_ai: bool = False,
):
    """
    Process a detected or provided URL.
    Downloads Instagram/Twitter posts, generates YouTube summaries.
    """
    if not update.message or not update.message.from_user:
        return
    user = update.message.from_user
    chat = update.message.chat
    logger.info(
        f"User {user.first_name} ({user.id}) in Chat {chat.title or chat.type} ({chat.id}) triggered processing for {url}"
    )

    # Detect platform
    platform = detect_platform(url)
    if not platform:
        await update.message.reply_text(
            "❌ Unsupported URL.\n\n"
            "Supported:\n"
            "• Instagram Reels/TV: instagram.com/reel/...\n"
            "• X/Twitter: x.com/user/status/ID\n"
            "• YouTube: youtube.com/watch?v=...\n\n" + DISCLAIMER
        )
        return

    if platform == "youtube":
        status_msg = await update.message.reply_text("⏳ Processing YouTube video...")
        await process_youtube_url(update, context, url, status_msg, use_local_ai)
        return

    # Send initial processing message
    status_msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")

    try:
        # Download the video
        result = download_video(url)

        if result.error:
            await status_msg.edit_text(f"❌ Download failed: {result.error}")
            return

            # Translate post text if necessary
        translated_caption = None
        text_to_translate = (
            result.caption if result.media_type != "text" else result.tweet_text
        )
        if text_to_translate and text_to_translate.strip():
            try:
                if status_msg:
                    await status_msg.edit_text(
                        "🌐 Checking language & translating text..."
                    )
                trans = Translator()
                t_res = trans.process_transcript(
                    text_to_translate[:1000], use_local_ai=use_local_ai, ai_cache=_cache
                )
                logger.info(
                    f"Caption lang detection: is_english={t_res.get('is_english')}, has_translation={bool(t_res.get('english_translation'))}, error={t_res.get('error')}"
                )
                # Accept translation even if there was a minor detection error
                if t_res.get("english_translation"):
                    translated_caption = t_res["english_translation"]
                elif t_res.get("error"):
                    logger.warning(
                        f"Caption translation skipped due to error: {t_res.get('error')}"
                    )
            except Exception as e:
                logger.warning(f"Text translation failed: {e}")

        # ── Handle GALLERY (Carousel) ─────────────────────────────────────────
        if result.media_type == "gallery" or len(result.file_paths) > 1:
            if status_msg:
                await status_msg.edit_text("📤 Sending gallery (carousel)...")

            footer = f"\n\n🖼️ Gallery ({platform})\n📏 Total Size: {result.file_size_bytes / (1024 * 1024):.2f} MB"
            if result.caption and translated_caption:
                sep = "\n\n🌐 **Translation:**\n"
                max_len = 1024 - len(footer) - len(sep)
                half = max_len // 2
                trunc_orig = (
                    result.caption[: half - 3] + "..."
                    if len(result.caption) > half
                    else result.caption
                )
                trunc_trans = (
                    translated_caption[: max_len - len(trunc_orig) - 3] + "..."
                    if len(translated_caption) > (max_len - len(trunc_orig))
                    else translated_caption
                )
                caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
            elif result.caption:
                caption = f"{result.caption[: 1024 - len(footer) - 3]}{footer}"
            else:
                caption = footer.strip()

            from typing import List, Union

            media_groups: List[List[Union[InputMediaPhoto, InputMediaVideo]]] = []
            current_group: List[Union[InputMediaPhoto, InputMediaVideo]] = []
            open_files = []  # Keep track to close them later

            try:
                for idx, file_path in enumerate(result.file_paths):
                    ext = os.path.splitext(file_path)[1].lower()
                    is_video = ext in (".mp4", ".mkv", ".mov")

                    item_caption = caption if idx == 0 else ""

                    f = open(file_path, "rb")
                    open_files.append(f)

                    if is_video:
                        current_group.append(
                            InputMediaVideo(media=f, caption=item_caption)
                        )
                    else:
                        current_group.append(
                            InputMediaPhoto(media=f, caption=item_caption)
                        )

                    if len(current_group) == 10:
                        media_groups.append(current_group)
                        current_group = []

                if current_group:
                    media_groups.append(current_group)

                if update.message:
                    for i, group in enumerate(media_groups):
                        if i > 0 and status_msg:
                            try:
                                await status_msg.edit_text(
                                    f"📤 Sending gallery part {i + 1}/{len(media_groups)}..."
                                )
                            except Exception:
                                pass
                        await update.message.reply_media_group(
                            media=group, read_timeout=120, write_timeout=120
                        )

                if status_msg:
                    try:
                        await status_msg.edit_text("✅ Gallery sent successfully!")
                    except Exception as e:
                        logger.warning(f"Failed to edit success message: {e}")
            except Exception as e:
                logger.error(f"Failed to send media group: {e}", exc_info=True)
                if status_msg:
                    try:
                        await status_msg.edit_text(f"⚠️ Failed to send gallery: {e}")
                    except Exception:
                        pass
            finally:
                for f in open_files:
                    f.close()
                for fp in result.file_paths:
                    cleanup_file(fp)
            return

        # ── Handle PHOTO ───────────────────────────────────────────────────────
        if result.media_type == "photo":
            if status_msg:
                await status_msg.edit_text("📤 Sending photo...")

            footer = f"\n\n📷 Photo ({platform})"
            if result.caption and translated_caption:
                sep = "\n\n🌐 **Translation:**\n"
                max_len = 1024 - len(footer) - len(sep)
                half = max_len // 2
                trunc_orig = (
                    result.caption[: half - 3] + "..."
                    if len(result.caption) > half
                    else result.caption
                )
                trunc_trans = (
                    translated_caption[: max_len - len(trunc_orig) - 3] + "..."
                    if len(translated_caption) > (max_len - len(trunc_orig))
                    else translated_caption
                )
                caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
            elif result.caption:
                caption = f"{result.caption[: 1024 - len(footer) - 3]}{footer}"
            else:
                caption = footer.strip()

            # Send photo safely
            try:
                if update.message:
                    await update.message.reply_photo(
                        photo=open(result.file_path, "rb"),
                        caption=caption,
                        read_timeout=60,
                        write_timeout=60,
                    )
                if status_msg:
                    try:
                        await status_msg.edit_text("✅ Photo sent successfully!")
                    except Exception:
                        pass
            except Exception as e:
                if status_msg:
                    try:
                        await status_msg.edit_text(f"⚠️ Failed to send photo: {e}")
                    except Exception:
                        pass
            finally:
                cleanup_file(result.file_path)
            return

        # ── Handle TEXT ONLY ───────────────────────────────────────────────────
        if result.media_type == "text":
            text = result.tweet_text or "No text available."
            if translated_caption:
                msg_text = f"📄 **Twitter Text:**\n\n{text}\n\n🌐 **Translation:**\n{translated_caption}"
            else:
                msg_text = f"📄 **Twitter Text:**\n\n{text}"

            for i in range(0, len(msg_text), 4000):
                if update.message:
                    await update.message.reply_text(msg_text[i : i + 4000])

            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            return

        file_size_mb = result.file_size_bytes / (1024 * 1024)

        # ── Handle VIDEO ───────────────────────────────────────────────────────
        if result.media_type == "video":
            # ── STEP 1: Check transcript cache first ──────────────────────────
            post_id_tuple = extract_post_id(url)
            transcript_result = None

            if _cache and post_id_tuple:
                t_cache_key = f"transcript:{post_id_tuple[0]}:{post_id_tuple[1]}"
                cached_transcript = _cache.get(t_cache_key)
                if cached_transcript is not None:
                    logger.info(f"Cache HIT (transcript): {t_cache_key}")
                    transcript_result = cached_transcript
                    await status_msg.edit_text(
                        f"📹 Video downloaded ({file_size_mb:.2f} MB)\n"
                        "⚡ Using cached transcript..."
                    )

            if transcript_result is None:
                # ── STEP 1b: Transcribe via API ─────────────────────────────
                await status_msg.edit_text(
                    f"📹 Video downloaded ({file_size_mb:.2f} MB)\n"
                    "🔍 Detecting language..."
                )

                transcriber = Transcriber()
                transcript_result = transcriber.transcribe_video(
                    result.file_path, use_local_ai=use_local_ai
                )

                # Store in cache if successful (no error, not a skipped Persian)
                if _cache and post_id_tuple and not transcript_result.get("error"):
                    t_cache_key = f"transcript:{post_id_tuple[0]}:{post_id_tuple[1]}"
                    _cache.set(t_cache_key, transcript_result)
                    logger.info(f"Cache SET (transcript): {t_cache_key}")

            if transcript_result["error"] and not transcript_result.get("skipped"):
                # Transcription/detection actually failed (not just skipped for Persian)
                # Still try to send the video
                await status_msg.edit_text(
                    f"⚠️ Language detection failed: {transcript_result['error']}\n"
                    "📤 Sending video anyway..."
                )
                await send_video_or_chunks(
                    update,
                    result.file_path,
                    result.file_size_bytes,
                    file_size_mb,
                    "Unknown",
                    result.platform,
                    post_caption=result.caption,
                    translated_caption=translated_caption,
                    status_msg=None,
                )
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                cleanup_file(result.file_path)
                return

            detected_lang = transcript_result.get("detected_language")
            detected_lang_name = transcript_result.get("language_name", "Unknown")
            is_skipped = transcript_result.get("skipped", False)  # True for Persian
            auto_detected = transcript_result.get("auto_detected", True)

            # ── STEP 2: Send video (always, for any language) ──────────────────
            await status_msg.edit_text(
                f"🔍 Detected language: **{detected_lang_name}**\n📤 Sending video..."
            )

            await send_video_or_chunks(
                update,
                result.file_path,
                result.file_size_bytes,
                file_size_mb,
                detected_lang_name,
                result.platform,
                result.caption,
                translated_caption,
                status_msg,
            )

            # Handle status message safely after potential deletion in send_video_or_chunks
            if status_msg:
                try:
                    await status_msg.edit_text("✅ Video sent successfully!")
                except Exception:
                    pass  # Message was deleted or invalid

            # ── STEP 3: Handle Persian — no transcription/translation needed ───
            if is_skipped:
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                if update.message:
                    await update.message.reply_text(
                        f"🔍 **Detected Language:** {detected_lang_name} (Persian)\n\n"
                        "No transcription/translation needed."
                    )
                cleanup_file(result.file_path)
                return

            # ── STEP 4: Handle no speech detected ─────────────────────────────
            transcript = transcript_result.get("text", "")
            if not transcript or not transcript.strip():
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                if update.message:
                    await update.message.reply_text(
                        "⚠️ No speech detected in this video."
                    )
                cleanup_file(result.file_path)
                return

            # ── STEP 5: Build transcript result ────────────────────────────────
            # If /dl was used, Gemini already transcribed + translated in one call.
            # Reuse that result instead of making a second API call.
            if use_local_ai and transcript_result.get("google_translation_handled"):
                if status_msg:
                    try:
                        await status_msg.edit_text(
                            "✅ Gemini processed transcript & translation!"
                        )
                    except Exception:
                        pass
                google_trans = transcript_result.get(
                    "google_translation"
                )  # None if English
                is_english = (detected_lang or "").lower() in Translator.ENGLISH_CODES
                is_persian = (detected_lang or "").lower() == "fa"

                # Safety fallback: if Gemini didn't return a translation for a
                # non-English, non-Persian language, call Google AI separately
                if (
                    not is_english
                    and not is_persian
                    and not google_trans
                    and transcript
                ):
                    logger.warning(
                        f"Gemini returned no translation for {detected_lang_name}, calling translator as fallback"
                    )
                    if status_msg:
                        try:
                            await status_msg.edit_text(
                                "🌐 Fetching translation (Google AI)..."
                            )
                        except Exception:
                            pass
                    fallback_translator = Translator()
                    fb = fallback_translator.translate_to_english(
                        transcript, detected_lang_name or "unknown", use_local_ai=True
                    )
                    google_trans = fb.get("translation") or None

                processed = {
                    "original_transcript": transcript,
                    "detected_language": detected_lang,
                    "detected_language_name": detected_lang_name,
                    "is_english": is_english,
                    "is_persian": is_persian,
                    "english_translation": google_trans,
                    "error": None,
                }
            else:
                # ── STEP 5 (fallback): Translate with Groq if needed ───────────────
                if status_msg:
                    try:
                        await status_msg.edit_text("🌐 Translating transcript...")
                    except Exception:
                        pass

                translator = Translator()
                processed = translator.process_transcript(
                    transcript,
                    hint_language=detected_lang,
                    use_local_ai=use_local_ai,
                    ai_cache=_cache,
                )

            # ── STEP 6: Build and send response message ────────────────────────
            detection_note = "(auto-detected)" if auto_detected else "(user specified)"
            response_parts = []
            response_parts.append(
                f"🔍 **Detected Language:** {detected_lang_name} {detection_note}"
            )
            response_parts.append("")
            response_parts.append("📝 **Transcript:**")
            response_parts.append(processed["original_transcript"])

            if processed["is_english"]:
                response_parts.append(
                    "\n✅ Language is English — no translation needed"
                )
            elif processed.get("english_translation"):
                trans_text = str(processed["english_translation"])
                response_parts.append("\n🌐 **English Translation:**")
                response_parts.append(trans_text)
            else:
                response_parts.append("\n⚠️ Translation not available")

            if processed.get("error"):
                response_parts.append(f"\n⚠️ Note: {processed['error']}")

            response_text = "\n".join(response_parts)

            # Split message if too long (Telegram ~4096 char limit)
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            if len(response_text) > 4000:
                if update.message:
                    await update.message.reply_text(
                        f"🔍 **Detected Language:** {detected_lang_name} {detection_note}\n\n"
                        "📝 **Transcript:**\n" + processed["original_transcript"][:3500]
                    )

                remaining = processed["original_transcript"][3500:]
                if remaining:
                    for i in range(0, len(remaining), 4000):
                        if update.message:
                            await update.message.reply_text(remaining[i : i + 4000])

                if not processed["is_english"] and processed.get("english_translation"):
                    trans_text = (
                        "🌐 **English Translation:**\n"
                        + processed["english_translation"]
                    )
                    for i in range(0, len(trans_text), 4000):
                        if update.message:
                            await update.message.reply_text(trans_text[i : i + 4000])
            else:
                if update.message:
                    await update.message.reply_text(response_text)

            cleanup_file(result.file_path)
            return

        # Unknown media type
        await status_msg.edit_text("❌ Unsupported media type or post format.")
        cleanup_file(result.file_path)

    except Exception as e:
        logger.error(f"Error processing download: {e}", exc_info=True)
        try:
            if status_msg:
                await status_msg.edit_text(
                    f"❌ An error occurred: {str(e)}\nPlease try again later."
                )
        except Exception:
            try:
                if update.message:
                    await update.message.reply_text(
                        f"❌ An error occurred: {str(e)}\nPlease try again later."
                    )
            except Exception:
                pass


def _check_yt_dlp_version():
    """Log the installed yt-dlp version as a sanity check at startup."""
    try:
        import yt_dlp  # type: ignore[import-untyped]

        version = getattr(yt_dlp, "__version__", "unknown")
        logger.info(
            f"yt-dlp version: {version}  (run 'pip install -U yt-dlp' to update)"
        )
    except Exception:
        logger.warning("Could not determine yt-dlp version")


def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment or .env file!")
        return

    _check_yt_dlp_version()
    logger.info("Starting Instagram Downloader Bot...")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("chatid", chatid_command))
    application.add_handler(CommandHandler("clearcache", clearcache_command))
    application.add_handler(CommandHandler("d", download_command))
    application.add_handler(CommandHandler("dl", download_local_command))

    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_text_message
        )
    )

    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
