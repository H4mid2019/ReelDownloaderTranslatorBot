"""
Instagram post downloader using yt-dlp.
Handles both images and videos from public Instagram posts.
Requires Instagram session cookies for posts (images/carousels).
"""
import os
import re
import tempfile
import yt_dlp
from dataclasses import dataclass
from typing import Optional
import time
from config import INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, INSTAGRAM_COOKIES_FILE, INSTAGRAM_SESSION_ID


@dataclass
class MediaResult:
    """Result of downloading an Instagram post."""
    post_url: str
    media_type: str  # 'video' or 'image'
    file_path: str
    file_size_bytes: int
    duration_seconds: Optional[float] = None
    caption: Optional[str] = None
    error: Optional[str] = None


def is_instagram_url(url: str) -> bool:
    """Check if the URL is a valid Instagram URL."""
    patterns = [
        r'(https?://)?(www\.)?instagram\.com/(p|reel|reels|tv|stories)/[\w-]+/?',
        r'(https?://)?(www\.)?instagr\.am/[\w-]+/?',
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)


def normalize_instagram_url(url: str) -> str:
    """Convert various Instagram URL formats to the standard format."""
    if not url.startswith('http'):
        url = 'https://' + url
    url = url.replace('instagr.am', 'instagram.com')
    # Remove query params that might interfere
    if '?' in url:
        base_url = url.split('?')[0]
        return base_url.rstrip('/')
    return url.rstrip('/')


def _write_session_cookie_file() -> Optional[str]:
    """
    Write a minimal Netscape-format cookies file from INSTAGRAM_SESSION_ID.
    Returns the path to the temp file, or None if session ID is not configured.
    The caller is responsible for deleting the file when done.
    """
    if not INSTAGRAM_SESSION_ID:
        return None
    # Netscape cookie file format:
    # domain  include_subdomains  path  secure  expiry  name  value
    expiry = int(time.time()) + 60 * 60 * 24 * 365  # 1 year from now
    lines = [
        "# Netscape HTTP Cookie File\n",
        f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\tsessionid\t{INSTAGRAM_SESSION_ID}\n",
    ]
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='_insta_cookies.txt', delete=False
    )
    tmp.writelines(lines)
    tmp.close()
    return tmp.name


def _resolve_cookies_file() -> Optional[str]:
    """
    Return the best available cookies file path, or None.
    Priority: configured file > session-ID-generated temp file.
    Caller must NOT delete a user-configured file; temp files must be cleaned up.
    """
    if INSTAGRAM_COOKIES_FILE and os.path.exists(INSTAGRAM_COOKIES_FILE):
        return INSTAGRAM_COOKIES_FILE
    return _write_session_cookie_file()


def _base_ydl_opts(download_dir: str) -> dict:
    """Return base yt-dlp options shared across all methods."""
    return {
        'outtmpl': os.path.join(download_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'extractor_retries': 3,
        'fragment_retries': 3,
    }


def get_yt_dlp_options(download_dir: str, cookies_path: Optional[str] = None) -> dict:
    """
    Get yt-dlp options with proper Instagram authentication.
    Priority: cookies file > session ID > unauthenticated.
    Note: username/password login is broken in current yt-dlp Instagram extractor.
    """
    ydl_opts = _base_ydl_opts(download_dir)
    ydl_opts['http_headers'] = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'Upgrade-Insecure-Requests': '1',
    }
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path
    return ydl_opts


def get_mobile_headers_options(download_dir: str, cookies_path: Optional[str] = None) -> dict:
    """Get options with mobile user-agent (sometimes works better for Reels)."""
    ydl_opts = _base_ydl_opts(download_dir)
    ydl_opts['http_headers'] = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                      'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                      'Version/17.0 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path
    return ydl_opts


def process_info_result(info: dict, original_url: str, download_dir: str) -> MediaResult:
    """Process yt-dlp info result and return MediaResult."""
    
    if info is None:
        return MediaResult(
            post_url=original_url,
            media_type='unknown',
            file_path='',
            file_size_bytes=0,
            error="Failed to extract post information"
        )
    
    # Determine media type
    media_type = 'image'
    if info.get('duration'):
        media_type = 'video'
    elif info.get('format') and 'video' in str(info.get('format', '')).lower():
        media_type = 'video'
    else:
        formats = info.get('formats', [])
        for fmt in formats:
            if fmt.get('vcodec') and fmt.get('vcodec') != 'none':
                media_type = 'video'
                break
    
    # Find the downloaded file
    file_path = ''
    file_size = 0
    
    if os.path.exists(download_dir):
        for f in os.listdir(download_dir):
            potential_path = os.path.join(download_dir, f)
            if os.path.isfile(potential_path):
                file_path = potential_path
                file_size = os.path.getsize(potential_path)
                break
    
    if not file_path:
        return MediaResult(
            post_url=original_url,
            media_type='unknown',
            file_path='',
            file_size_bytes=0,
            error="Downloaded file not found"
        )
    
    return MediaResult(
        post_url=original_url,
        media_type=media_type,
        file_path=file_path,
        file_size_bytes=file_size,
        duration_seconds=info.get('duration'),
        caption=info.get('description') or info.get('title', ''),
        error=None
    )


def _cleanup_temp_cookie(path: Optional[str]):
    """Delete a temp cookies file if it was auto-generated (not user-configured)."""
    if path and path != INSTAGRAM_COOKIES_FILE:
        try:
            os.remove(path)
        except Exception:
            pass


def download_instagram_post(url: str) -> MediaResult:
    """
    Download an Instagram post (image or video).

    Authentication priority (set in .env):
    1. INSTAGRAM_COOKIES_FILE — full Netscape cookies file (most reliable)
    2. INSTAGRAM_SESSION_ID  — single session cookie value (easy to obtain)
    3. Unauthenticated        — only works for some public Reels

    How to get your session ID:
    1. Open instagram.com in Chrome and log in
    2. Press F12 → Application → Cookies → https://www.instagram.com
    3. Find the "sessionid" cookie and copy its Value
    4. Set INSTAGRAM_SESSION_ID=<value> in .env
    """
    normalized_url = normalize_instagram_url(url)
    download_dir = tempfile.mkdtemp(prefix="insta_")

    # Resolve once so both methods reuse the same (possibly temp) file
    cookies_path = _resolve_cookies_file()

    # Method 1: Desktop Chrome user-agent
    ydl_opts = get_yt_dlp_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if info:
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir)
    except Exception as e:
        pass  # Try next method

    # Method 2: Mobile Safari user-agent
    ydl_opts = get_mobile_headers_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if info:
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir)
    except Exception as e:
        _cleanup_temp_cookie(cookies_path)
        error_msg = str(e)
        auth_needed = (
            'login' in error_msg.lower()
            or 'sign in' in error_msg.lower()
            or 'rate-limit' in error_msg.lower()
            or 'not available' in error_msg.lower()
        )
        if auth_needed:
            return MediaResult(
                post_url=url,
                media_type='unknown',
                file_path='',
                file_size_bytes=0,
                error=(
                    "Instagram requires authentication.\n\n"
                    "Easiest fix — set your session ID in .env:\n"
                    "1. Open instagram.com in Chrome and log in\n"
                    "2. Press F12 → Application → Cookies → instagram.com\n"
                    "3. Find 'sessionid' cookie — copy its Value\n"
                    "4. Add to .env:  INSTAGRAM_SESSION_ID=<value>\n"
                    "5. Restart the bot"
                )
            )
        return MediaResult(
            post_url=url,
            media_type='unknown',
            file_path='',
            file_size_bytes=0,
            error=f"Download failed: {error_msg}"
        )

    _cleanup_temp_cookie(cookies_path)
    return MediaResult(
        post_url=url,
        media_type='unknown',
        file_path='',
        file_size_bytes=0,
        error="Unable to download. Instagram may require authentication."
    )
