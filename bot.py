"""
Telegram bot for downloading Instagram posts with transcription and translation.
Supports both images and videos from public Instagram posts.
"""
import logging
import os
import re
import subprocess
import tempfile
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, MAX_VIDEO_SIZE_MB, MAX_VIDEO_SIZE_BYTES
from downloader import download_video, detect_platform
from transcriber import Transcriber
from translator import Translator
from typing import Optional

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# Disclaimer text
DISCLAIMER = """
⚠️ **DISCLAIMER** - This bot is **OPEN-SOURCE** software.
• Free for personal/non-commercial use only
• Commercial use is strictly prohibited
• Use at your own risk
"""

# Chunk size for splitting large videos (45MB to stay under 50MB Telegram limit)
VIDEO_CHUNK_SIZE_BYTES = 45 * 1024 * 1024


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not update.message:
        return
    await update.message.reply_text(
        "👋 Welcome to Video Downloader Bot!\n\n"
        "I can download videos from Instagram Reels/TV, X/Twitter "
        "and transcribe/translate them.\n\n"
        "📝 Commands:\n"
        "/d <video_url> - Download and process\n"
        "/help - Show help\n\n"
        "Supported:\n"
        "• Instagram: /reel/, /reels/, /tv/ (no /p/ posts)\n"
        "• X/Twitter: x.com/username/status/ID\n\n"
        + DISCLAIMER
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not update.message:
        return
    await update.message.reply_text(
        "📖 How to use:\n\n"
        "1️⃣ `/d <video_url>`\n\n"
        "✅ **Supported platforms:**\n"
        "• Instagram Reels/TV (no /p/ posts)\n"
        "• X/Twitter status videos\n\n"
        "2️⃣ **Videos >50MB** auto-split\n\n"
        "3️⃣ **Transcription/Translation:**\n"
        "• Persian: video only\n"
        "• English: video + transcript\n"
        "• Other: video + transcript + English translation\n\n"
        "⚠️ Public videos only\n\n"
        + DISCLAIMER
    )


def split_video(video_path: str, chunk_size_bytes: int = VIDEO_CHUNK_SIZE_BYTES) -> list:
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
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ],
            capture_output=True, text=True, timeout=60
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
        chunk_path = os.path.join(chunk_dir, f"chunk_{i+1:03d}.mp4")

        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', video_path,
            '-t', str(chunk_duration),
            '-c', 'copy',  # No re-encoding for speed
            '-avoid_negative_ts', '1',
            '-y',
            chunk_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                chunk_paths.append(chunk_path)
        except Exception as e:
            logger.error(f"Failed to create chunk {i+1}: {e}")

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
    status_msg=None
) -> bool:
    """Send video or split chunks. Remuxes for Telegram compatibility if needed."""
    # Remux for Telegram (stream copy + faststart)
    remuxed_path = video_path
    if file_size_bytes > 30 * 1024 * 1024:  # Remux large videos
        remuxed_path = video_path + '.telegram.mp4'
        cmd = [
            'ffmpeg', '-i', video_path,
            '-c', 'copy',
            '-movflags', '+faststart', '-y', remuxed_path
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
            trunc_orig = post_caption[:half-3] + "..." if len(post_caption) > half else post_caption
            trunc_trans = translated_caption[:max_len-len(trunc_orig)-3] + "..." if len(translated_caption) > (max_len-len(trunc_orig)) else translated_caption
            caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
        elif post_caption:
            caption = f"{post_caption[:1024-len(footer)-3]}{footer}"
        else:
            caption = footer.strip()
            
        if not update.message:
            return False
        await update.message.reply_video(
            video=open(video_path, 'rb'),
            caption=caption
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
            chunk_size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            footer = f"\n\n🎬 Video ({platform}) — Part {idx}/{total_parts}\n📏 Part: {chunk_size_mb:.2f} MB\n🔊 Language: {lang_name}"
            if post_caption and translated_caption:
                sep = "\n\n🌐 **Translation:**\n"
                max_len = 1024 - len(footer) - len(sep)
                half = max_len // 2
                trunc_orig = post_caption[:half-3] + "..." if len(post_caption) > half else post_caption
                trunc_trans = translated_caption[:max_len-len(trunc_orig)-3] + "..." if len(translated_caption) > (max_len-len(trunc_orig)) else translated_caption
                caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
            elif post_caption:
                caption = f"{post_caption[:1024-len(footer)-3]}{footer}"
            else:
                caption = footer.strip()
                
            if not update.message:
                continue
            await update.message.reply_video(
                video=open(chunk_path, 'rb'),
                caption=caption
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
            "Example: /d https://www.instagram.com/reel/ABC123/\n\n"
            + DISCLAIMER
        )
        return

    url = ' '.join(context.args)
    await process_url(update, context, url)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular text messages to auto-detect supported URLs."""
    if not update.message:
        return
        
    text = update.message.text or update.message.caption or ""
    
    # Check if there are any supported URLs in the text
    urls = re.findall(r'(https?://[^\s]+)', text)
    
    for url in urls:
        if detect_platform(url):
            await process_url(update, context, url)
            return


async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """
    Process a detected or provided URL.
    Downloads Instagram post or Tweet, sends video first, then transcribes and translates.
    """
    if not update.message or not update.message.from_user:
        return
    user = update.message.from_user
    logger.info(f"User {user.first_name} ({user.id}) triggered processing for {url}")

    # Detect platform
    platform = detect_platform(url)
    if not platform:
        await update.message.reply_text(
            "❌ Unsupported URL.\n\n"
            "Supported:\n"
            "• Instagram Reels/TV: instagram.com/reel/...\n"
            "• X/Twitter: x.com/user/status/ID\n\n"
            + DISCLAIMER
        )
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
        text_to_translate = result.caption if result.media_type != 'text' else result.tweet_text
        if text_to_translate and text_to_translate.strip():
            try:
                if status_msg:
                    await status_msg.edit_text("🌐 Checking language & translating text...")
                trans = Translator()
                t_res = trans.process_transcript(text_to_translate[:1000]) # Use first 1000 chars for lang detection + trans
                if t_res.get('english_translation') and not t_res.get('error'):
                    translated_caption = t_res['english_translation']
            except Exception as e:
                logger.warning(f"Text translation failed: {e}")

        # ── Handle PHOTO ───────────────────────────────────────────────────────
        if result.media_type == 'photo':
            if status_msg:
                await status_msg.edit_text("📤 Sending photo...")
            
            footer = f"\n\n📷 Photo ({platform})"
            if result.caption and translated_caption:
                sep = "\n\n🌐 **Translation:**\n"
                max_len = 1024 - len(footer) - len(sep)
                half = max_len // 2
                trunc_orig = result.caption[:half-3] + "..." if len(result.caption) > half else result.caption
                trunc_trans = translated_caption[:max_len-len(trunc_orig)-3] + "..." if len(translated_caption) > (max_len-len(trunc_orig)) else translated_caption
                caption = f"{trunc_orig}{sep}{trunc_trans}{footer}"
            elif result.caption:
                caption = f"{result.caption[:1024-len(footer)-3]}{footer}"
            else:
                caption = footer.strip()
            
            # Send photo safely
            try:
                if update.message:
                    await update.message.reply_photo(
                        photo=open(result.file_path, 'rb'),
                        caption=caption
                    )
                await status_msg.edit_text("✅ Photo sent successfully!")
            except Exception as e:
                await status_msg.edit_text(f"⚠️ Failed to send photo: {e}")
            finally:
                cleanup_file(result.file_path)
            return

        # ── Handle TEXT ONLY ───────────────────────────────────────────────────
        if result.media_type == 'text':
            text = result.tweet_text or "No text available."
            if translated_caption:
                msg_text = f"📄 **Twitter Text:**\n\n{text}\n\n🌐 **Translation:**\n{translated_caption}"
            else:
                msg_text = f"📄 **Twitter Text:**\n\n{text}"
            
            for i in range(0, len(msg_text), 4000):
                if update.message:
                    await update.message.reply_text(msg_text[i:i+4000])
                    
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            return

        file_size_mb = result.file_size_bytes / (1024 * 1024)

        # ── Handle VIDEO ───────────────────────────────────────────────────────
        if result.media_type == 'video':

            # ── STEP 1: Transcribe first (just to detect language) ─────────────
            # We need language detection before sending so we can caption correctly.
            # For Persian, we skip transcription entirely AFTER detecting language.
            await status_msg.edit_text(
                f"📹 Video downloaded ({file_size_mb:.2f} MB)\n"
                "🔍 Detecting language..."
            )

            transcriber = Transcriber()
            transcript_result = transcriber.transcribe_video(result.file_path)

            if transcript_result['error'] and not transcript_result.get('skipped'):
                # Transcription/detection actually failed (not just skipped for Persian)
                # Still try to send the video
                await status_msg.edit_text(
                    f"⚠️ Language detection failed: {transcript_result['error']}\n"
                    "📤 Sending video anyway..."
                )
                await send_video_or_chunks(
                    update, result.file_path, result.file_size_bytes, file_size_mb,
                    "Unknown", result.platform, post_caption=result.caption, translated_caption=translated_caption, status_msg=None
                )
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                cleanup_file(result.file_path)
                return

            detected_lang = transcript_result.get('detected_language')
            detected_lang_name = transcript_result.get('language_name', 'Unknown')
            is_skipped = transcript_result.get('skipped', False)  # True for Persian
            auto_detected = transcript_result.get('auto_detected', True)

            # ── STEP 2: Send video (always, for any language) ──────────────────
            await status_msg.edit_text(
                f"🔍 Detected language: **{detected_lang_name}**\n"
                "📤 Sending video..."
            )

            await send_video_or_chunks(
                update, result.file_path, result.file_size_bytes, file_size_mb,
                detected_lang_name, result.platform, result.caption, translated_caption, status_msg
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
                        await status_msg.edit_text(
                            f"🔍 **Detected Language:** {detected_lang_name}\n\n"
                            "Persian language doesn't need transcription or translation.\n\n"
                        )
                    except Exception:
                        if update.message:
                            await update.message.reply_text(
                                f"🔍 **Detected Language:** {detected_lang_name} (Persian)\n\n"
                                "No transcription/translation needed."
                            )
                cleanup_file(result.file_path)
                return

            # ── STEP 4: Handle no speech detected ─────────────────────────────
            transcript = transcript_result.get('text', '')
            if not transcript or not transcript.strip():
                if status_msg:
                    try:
                        await status_msg.edit_text("⚠️ No speech detected in this video.")
                    except Exception:
                        if update.message:
                            await update.message.reply_text("⚠️ No speech detected in this video.")
                cleanup_file(result.file_path)
                return

            # ── STEP 5: Translate if needed ────────────────────────────────────
            await status_msg.edit_text("🌐 Translating transcript...")

            translator = Translator()
            processed = translator.process_transcript(transcript, hint_language=detected_lang)

            # ── STEP 6: Build and send response message ────────────────────────
            detection_note = "(auto-detected)" if auto_detected else "(user specified)"
            response_parts = []
            response_parts.append(f"🔍 **Detected Language:** {detected_lang_name} {detection_note}")
            response_parts.append("")
            response_parts.append("📝 **Transcript:**")
            response_parts.append(processed['original_transcript'])

            if processed['is_english']:
                response_parts.append("\n✅ Language is English — no translation needed")
            elif processed.get('english_translation'):
                trans_text = str(processed['english_translation'])
                response_parts.append("\n🌐 **English Translation:**")
                response_parts.append(trans_text[:850])
            else:
                response_parts.append("\n⚠️ Translation not available")

            if processed.get('error'):
                response_parts.append(f"\n⚠️ Note: {processed['error']}")

            response_text = "\n".join(response_parts)

            # Split message if too long (Telegram ~4096 char limit)
            if len(response_text) > 4000:
                if status_msg:
                    try:
                        await status_msg.edit_text(
                            f"🔍 **Detected Language:** {detected_lang_name} {detection_note}\n\n"
                            "📝 **Transcript:**\n" + processed['original_transcript'][:3500]
                        )
                    except Exception:
                        if update.message:
                            await update.message.reply_text(
                                f"🔍 **Detected Language:** {detected_lang_name} {detection_note}\n\n"
                                "📝 **Transcript:**\n" + processed['original_transcript'][:3500]
                            )

                remaining = processed['original_transcript'][3500:]
                if remaining:
                    for i in range(0, len(remaining), 4000):
                        if update.message:
                            await update.message.reply_text(remaining[i:i+4000])

                if not processed['is_english'] and processed.get('english_translation'):
                    trans_text = "🌐 **English Translation:**\n" + processed['english_translation']
                    for i in range(0, len(trans_text), 4000):
                        if update.message:
                            await update.message.reply_text(trans_text[i:i+4000])
            else:
                if status_msg:
                    try:
                        await status_msg.edit_text(response_text)
                    except Exception:
                        if update.message:
                            await update.message.reply_text(response_text)

            cleanup_file(result.file_path)
            return

        # Unknown media type
        await status_msg.edit_text("❌ Unsupported media type or post format.")
        cleanup_file(result.file_path)

    except Exception as e:
        logger.error(f"Error processing download: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ An error occurred: {str(e)}\n"
            "Please try again later."
        )


def _check_yt_dlp_version():
    """Log the installed yt-dlp version as a sanity check at startup."""
    try:
        import yt_dlp  # type: ignore[import-untyped]
        version = getattr(yt_dlp, '__version__', 'unknown')
        logger.info(f"yt-dlp version: {version}  (run 'pip install -U yt-dlp' to update)")
    except Exception:
        logger.warning("Could not determine yt-dlp version")


def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment or .env file!")
        return

    _check_yt_dlp_version()
    logger.info("Starting Instagram Downloader Bot...")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("d", download_command))

    application.add_handler(
        MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_text_message)
    )

    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
