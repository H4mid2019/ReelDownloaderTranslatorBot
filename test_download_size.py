#!/usr/bin/env python3
"""
Verification test for ReelDownloaderTranslatorBot download size fix.
Tests the specific reel that was failing (14s Instagram reel).
Expected: ~1MB file, not 38MB chunks.
"""

import os
from downloader import download_video

REEL_URL = "https://www.instagram.com/reel/DVY8pcmDDci/?igsh=b21qY2Vhdm4zYmU4"
MAX_EXPECTED_MB = 10.0  # 14s reel at 720p should be well under 10MB
TELEGRAM_LIMIT_MB = 45.0

print("🧪 Testing download size fix...")

result = download_video(REEL_URL)

if result.error:
    print(f"❌ Download failed: {result.error}")
    exit(1)

if not result.file_path or not os.path.exists(result.file_path):
    print("❌ No file downloaded")
    exit(1)

actual_size_bytes = os.path.getsize(result.file_path)
actual_mb = actual_size_bytes / (1024 * 1024)
print(f"✅ Downloaded: {os.path.basename(result.file_path)}")
print(f"📏 Size: {actual_mb:.2f} MB")
print(
    f"⏱️ Duration: {result.duration_seconds:.1f}s"
    if result.duration_seconds
    else "⏱️ Duration: N/A"
)

assert actual_mb <= MAX_EXPECTED_MB, (
    f"❌ File too large! {actual_mb:.2f} MB > {MAX_EXPECTED_MB} MB. "
    "Format selector not working — check yt-dlp logs."
)

assert actual_mb <= TELEGRAM_LIMIT_MB, (
    f"⚠️ File would require chunking: {actual_mb:.2f} MB > {TELEGRAM_LIMIT_MB} MB"
)

print(
    f"✅ PASS: File size {actual_mb:.2f} MB is optimal (under {MAX_EXPECTED_MB}MB limit)."
)
print("✅ No chunking needed for Telegram (under 45MB).")

# Cleanup
os.remove(result.file_path)
print("🧹 Cleaned up test file.")
