"""
Standalone runner for generate_video_brief on a single Instagram reel.
Uses the exact same functions as the bot (downloader + video_brief).

Usage:
    python run_video_brief.py
"""

import io
import logging
import os
import shutil
import sys

from downloader import download_video
from video_brief import build_video_brief_messages, generate_video_brief

# Force UTF-8 output so Persian/Arabic characters don't crash the Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

REEL_URL = "https://www.instagram.com/reel/DWr2rSejcBD/?igsh=MjVndzYxb3NjcTJp"


def main() -> None:
    """Download the reel and generate a detailed video brief."""
    logger.info("Downloading: %s", REEL_URL)
    result = download_video(REEL_URL)

    if result.error:
        logger.error("Download failed: %s", result.error)
        sys.exit(1)

    logger.info(
        "Downloaded: %s  (%.2f MB)",
        result.file_path,
        result.file_size_bytes / (1024 * 1024),
    )

    try:
        model_name = os.getenv("GOOGLE_AI_MODEL", "gemini-2.5-flash-lite")
        logger.info("Generating video brief (model=%s) ...", model_name)
        brief = generate_video_brief(
            video_path=result.file_path,
            caption_context=result.caption,
            platform="instagram",
        )

        messages = build_video_brief_messages(
            brief=brief,
            post_url=REEL_URL,
            platform="instagram",
        )

        print("\n" + "=" * 60)
        for i, msg in enumerate(messages, 1):
            print(f"\n--- Message {i}/{len(messages)} ---\n")
            print(msg)
        print("\n" + "=" * 60)

        if brief.get("error"):
            logger.error("Brief returned error: %s", brief["error"])
            sys.exit(1)

    finally:
        download_dir = os.path.dirname(result.file_path)
        if download_dir and os.path.isdir(download_dir):
            shutil.rmtree(download_dir, ignore_errors=True)
            logger.info("Cleaned up temp dir: %s", download_dir)


if __name__ == "__main__":
    main()
