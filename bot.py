"""
Telegram bot for downloading Instagram posts with transcription and translation.
Supports both images and videos from public Instagram posts.
"""
import asyncio
import logging
import os
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from config import TELEGRAM_BOT_TOKEN, MAX_VIDEO_SIZE_MB, MAX_VIDEO_SIZE_BYTES
from downloader import download_instagram_post, is_instagram_url, normalize_instagram_url
from transcriber import Transcriber
from translator import Translator

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
• View source: [GitHub/Repository]
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to Instagram Downloader Bot!\n\n"
        "I can download Instagram posts (images & videos) and transcribe them.\n\n"
        "📝 Commands:\n"
        "/download <instagram_url> - Download and process a post\n"
        "/help - Show help message\n\n"
        "Supported URL formats:\n"
        "• https://www.instagram.com/p/POST_ID/\n"
        "• https://www.instagram.com/reel/REEL_ID/\n"
        "• https://www.instagram.com/tv/VIDEO_ID/\n\n"
        + DISCLAIMER
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(
        "📖 How to use this bot:\n\n"
        "1️⃣ Send an Instagram URL with /download\n"
        "   Example: /download https://www.instagram.com/p/ABC123/\n\n"
        "2️⃣ For videos:\n"
        "   • Videos ≤ 50MB: I'll send the video + transcript\n"
        "   • Videos > 50MB: I'll send only the transcript\n\n"
        "3️⃣ Transcription:\n"
        "   • English videos: Full transcript\n"
        "   • Bulgarian videos: Transcript + English translation\n\n"
        "⚠️ Note: Only public Instagram posts are supported.\n\n"
        + DISCLAIMER
    )


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main command handler for /download.
    Downloads Instagram post, processes video, sends results.
    Works in both private chats and group chats.
    """
    user = update.message.from_user
    logger.info(f"User {user.first_name} ({user.id}) triggered /download")
    
    # Extract URL from command
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide an Instagram URL.\n"
            "Usage: /download <instagram_url>\n\n"
            "Example: /download https://www.instagram.com/p/ABC123/\n\n"
            + DISCLAIMER
        )
        return
    
    url = ' '.join(context.args)
    
    # Validate URL
    if not is_instagram_url(url):
        await update.message.reply_text(
            "❌ Invalid Instagram URL.\n\n"
            "Supported formats:\n"
            "• https://www.instagram.com/p/POST_ID/\n"
            "• https://www.instagram.com/reel/REEL_ID/\n"
            "• https://www.instagram.com/tv/VIDEO_ID/\n\n"
            + DISCLAIMER
        )
        return
    
    # Send initial processing message
    status_msg = await update.message.reply_text("⏳ Downloading Instagram post...")
    
    try:
        # Download the post
        result = download_instagram_post(url)
        
        if result.error:
            await status_msg.edit_text(f"❌ Download failed: {result.error}")
            return
        
        file_size_mb = result.file_size_bytes / (1024 * 1024)
        
        # Handle IMAGE
        if result.media_type == 'image':
            await status_msg.edit_text("📷 Image downloaded! Sending...")
            
            await update.message.reply_photo(
                photo=open(result.file_path, 'rb'),
                caption=f"🖼️ Instagram Image\n📏 Size: {file_size_mb:.2f} MB",
            )
            
            await status_msg.delete()
            cleanup_file(result.file_path)
            return
        
        # Handle VIDEO
        if result.media_type == 'video':
            # Check file size
            if result.file_size_bytes > MAX_VIDEO_SIZE_BYTES:
                await status_msg.edit_text(
                    f"📹 Video downloaded ({file_size_mb:.2f} MB)\n"
                    f"⚠️ Video exceeds {MAX_VIDEO_SIZE_MB}MB limit.\n"
                    "🔄 Processing transcript only..."
                )
                video_too_large = True
            else:
                await status_msg.edit_text(
                    f"📹 Video downloaded ({file_size_mb:.2f} MB)\n"
                    "🔄 Transcribing audio..."
                )
                video_too_large = False
            
            # Transcribe video
            transcriber = Transcriber()
            transcript_result = transcriber.transcribe_video(result.file_path)
            
            if transcript_result['error']:
                await status_msg.edit_text(
                    f"❌ Transcription failed: {transcript_result['error']}"
                )
                cleanup_file(result.file_path)
                return
            
            transcript = transcript_result['text']
            
            if not transcript or not transcript.strip():
                await status_msg.edit_text(
                    "⚠️ No speech detected in this video."
                )
                if not video_too_large:
                    await update.message.reply_video(
                        video=open(result.file_path, 'rb'),
                        caption="🎬 Video (no speech detected)"
                    )
                cleanup_file(result.file_path)
                return
            
            # Detect language and translate
            translator = Translator()
            processed = translator.process_transcript(
                transcript, 
                hint_language=transcript_result.get('language')
            )
            
            # Build response message
            response_parts = []
            
            # Always show original transcript
            response_parts.append("📝 **Transcript:**")
            response_parts.append(processed['original_transcript'])
            
            # Add translation if Bulgarian
            if processed['is_bulgarian']:
                response_parts.append("\n🌐 **English Translation:**")
                response_parts.append(processed['english_translation'])
            elif processed['is_english']:
                response_parts.append("\n🌐 Language: English (no translation needed)")
            else:
                response_parts.append("\n🌐 Language: Unknown")
            
            # Show detected language info
            if processed.get('error'):
                response_parts.append(f"\n⚠️ Note: {processed['error']}")
            
            response_text = "\n".join(response_parts)
            
            # Split message if too long (Telegram limit ~4096 chars)
            if len(response_text) > 4000:
                # Send transcript first
                await status_msg.edit_text(
                    "📝 **Transcript:**\n" + processed['original_transcript'][:4000]
                )
                # Send rest
                remaining = response_text[4000:]
                for i in range(0, len(remaining), 4000):
                    await update.message.reply_text(remaining[i:i+4000])
                
                # Send translation if Bulgarian
                if processed['is_bulgarian'] and processed['english_translation']:
                    trans_parts = ["🌐 **English Translation:**"]
                    trans_parts.append(processed['english_translation'])
                    trans_text = "\n".join(trans_parts)
                    
                    for i in range(0, len(trans_text), 4000):
                        await update.message.reply_text(trans_text[i:i+4000])
            else:
                await status_msg.edit_text(response_text)
            
            # Send video if within size limit
            if not video_too_large:
                await update.message.reply_video(
                    video=open(result.file_path, 'rb'),
                    caption=f"🎬 Instagram Video\n📏 Size: {file_size_mb:.2f} MB"
                )
            else:
                await update.message.reply_text(
                    f"⚠️ Video file ({file_size_mb:.2f} MB) exceeds {MAX_VIDEO_SIZE_MB}MB Telegram limit.\n"
                    "Transcript and translation are provided above."
                )
            
            cleanup_file(result.file_path)
            return
        
        # Unknown media type
        await status_msg.edit_text(
            "❌ Unsupported media type or post format."
        )
        cleanup_file(result.file_path)
        
    except Exception as e:
        logger.error(f"Error processing download: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ An error occurred: {str(e)}\n"
            "Please try again later."
        )


def cleanup_file(file_path: str):
    """Safely delete a downloaded file."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup file {file_path}: {e}")


def main():
    """Start the bot."""
    # Validate configuration
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment or .env file!")
        return
    
    logger.info("Starting Instagram Downloader Bot...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("download", download_command))
    
    # Handle any other messages (not commands)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, 
                      lambda u, c: u.message.reply_text(
                          "Send /download <instagram_url> to download a post.\n"
                          "Or use /help for more information.\n\n"
                          + DISCLAIMER
                      ))
    )
    
    # Start the bot
    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
