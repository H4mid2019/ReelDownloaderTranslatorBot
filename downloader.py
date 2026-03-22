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
from config import INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, INSTAGRAM_COOKIES_FILE


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


def get_yt_dlp_options(download_dir: str) -> dict:
    """
    Get yt-dlp options with proper Instagram authentication.
    Uses cookies file if available, otherwise tries browser cookies.
    """
    ydl_opts = {
        'outtmpl': os.path.join(download_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'extractor_retries': 3,
        'fragment_retries': 3,
        # Browser simulation headers
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Upgrade-Insecure-Requests': '1',
        },
    }
    
    # Try to use cookies file first
    if INSTAGRAM_COOKIES_FILE and os.path.exists(INSTAGRAM_COOKIES_FILE):
        ydl_opts['cookiefile'] = INSTAGRAM_COOKIES_FILE
        return ydl_opts
    
    # Try browser cookies (Chrome)
    ydl_opts['cookiesfrombrowser'] = ('chrome', None, None, None)
    
    return ydl_opts


def get_mobile_headers_options(download_dir: str) -> dict:
    """Get options with mobile user-agent (sometimes works better)."""
    return {
        'outtmpl': os.path.join(download_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }


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


def download_instagram_post(url: str) -> MediaResult:
    """
    Download an Instagram post (image or video).
    
    For posts with images/carousels, Instagram requires authentication.
    Recommended: Create an Instagram cookies file from your browser.
    
    To create cookies file:
    1. Login to Instagram in Chrome
    2. Install "EditThisCookie" extension
    3. Export cookies in Netscape format
    4. Save as instagram_cookies.txt
    5. Set INSTAGRAM_COOKIES_FILE=instagram_cookies.txt in .env
    """
    normalized_url = normalize_instagram_url(url)
    download_dir = tempfile.mkdtemp(prefix="insta_")
    
    # Method 1: Try with cookies file or browser cookies
    ydl_opts = get_yt_dlp_options(download_dir)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if info:
                return process_info_result(info, url, download_dir)
    except Exception as e:
        error_msg = str(e)
        # Continue to next method if this one fails
        if not ('login' in error_msg.lower() or 'sign' in error_msg.lower()):
            pass  # Log for debugging
    
    # Method 2: Try with mobile user-agent
    ydl_opts = get_mobile_headers_options(download_dir)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if info:
                return process_info_result(info, url, download_dir)
    except Exception as e:
        error_msg = str(e)
        # Check if it's a login required error
        if 'login' in error_msg.lower() or 'sign in' in error_msg.lower():
            return MediaResult(
                post_url=url,
                media_type='unknown',
                file_path='',
                file_size_bytes=0,
                error="Instagram requires authentication for this post.\n\n"
                      "To download image posts, you need Instagram cookies:\n"
                      "1. Login to Instagram in Chrome\n"
                      "2. Use EditThisCookie extension\n"
                      "3. Export cookies in Netscape format\n"
                      "4. Save as instagram_cookies.txt\n"
                      "5. Set INSTAGRAM_COOKIES_FILE=instagram_cookies.txt in .env"
            )
        else:
            return MediaResult(
                post_url=url,
                media_type='unknown',
                file_path='',
                file_size_bytes=0,
                error=f"Download failed: {error_msg}"
            )
    
    return MediaResult(
        post_url=url,
        media_type='unknown',
        file_path='',
        file_size_bytes=0,
        error="Unable to download. Instagram may require authentication."
    )
