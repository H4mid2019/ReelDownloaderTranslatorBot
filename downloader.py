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
from config import INSTAGRAM_COOKIES_FILE, INSTAGRAM_SESSION_ID


@dataclass
class MediaResult:
    """Result of downloading a video post."""
    post_url: str
    media_type: str = 'unknown'
    file_path: str = ''
    file_size_bytes: int = 0
    duration_seconds: Optional[float] = None
    caption: Optional[str] = None
    platform: str = 'unknown'
    tweet_text: Optional[str] = None
    error: Optional[str] = None


def is_instagram_video_url(url: str) -> bool:
    """Check if URL is Instagram video (reel/tv only, no /p/ posts)."""
    patterns = [
        r'(https?://)?(www\.)?instagram\.com/(reel|reels|tv)/[\w-]+/?',
        r'(https?://)?(www\.)?instagr\.am/(reel|reels|tv)/[\w-]+/?',
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)


def is_twitter_url(url: str) -> bool:
    """Check if URL is Twitter/X video post."""
    patterns = [
        r'(https?://)?(www\.)?(twitter\.com|x\.com)/[\w-]+/status/[\d-]+',
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)




def detect_platform(url: str) -> Optional[str]:
    """Detect platform: 'instagram', 'twitter', or None."""
    if is_instagram_video_url(url):
        return 'instagram'
    if is_twitter_url(url):
        return 'twitter'
    return None


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
        # Cap at Instagram Reel standard (720x1280 portrait or 1280p landscape equiv) to prevent huge files
        # Portrait videos have height=1280, width=720; landscape height<=720
        'format': 'bestvideo[height<=1280][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1280]+bestaudio/best[height<=1280]/best',
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


def process_info_result(info: dict, original_url: str, download_dir: str, platform: str, tweet_text: Optional[str] = None) -> MediaResult:
    """Process yt-dlp info result and return MediaResult."""
    
    if info is None:
        return MediaResult(
            post_url=original_url,
            platform=platform,
            error="Failed to extract post information"
        )
    
    # Assume video (no image support)
    media_type = 'video'
    
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

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Downloaded {platform}: {os.path.basename(file_path) if file_path else 'NO_FILE'}, "
                f"size={file_size/(1024*1024):.2f}MB, "
                f"duration={info.get('duration', 'N/A')}s, "
                f"height={info.get('height', 'N/A')}p, "
                f"format_id={info.get('format_id', 'N/A')}")
    
    if not file_path:
        if platform == 'twitter' and tweet_text:
            return MediaResult(
                post_url=original_url,
                platform=platform,
                media_type='text',
                tweet_text=tweet_text,
                error=None
            )
        return MediaResult(
            post_url=original_url,
            platform=platform,
            media_type='unknown',
            error="Downloaded file not found"
        )
    
    caption = info.get('description') or info.get('title', '')
    
    return MediaResult(
        post_url=original_url,
        platform=platform,
        media_type=media_type,
        file_path=file_path,
        file_size_bytes=file_size,
        duration_seconds=info.get('duration'),
        caption=caption,
        tweet_text=tweet_text,
        error=None
    )


def _cleanup_temp_cookie(path: Optional[str]):
    """Delete a temp cookies file if it was auto-generated (not user-configured)."""
    if path and path != INSTAGRAM_COOKIES_FILE:
        try:
            os.remove(path)
        except Exception:
            pass


def download_video(url: str) -> MediaResult:
    """
    Download video from Instagram Reels/TV, X/Twitter, or YouTube Shorts.
    
    Rejects Instagram /p/ posts (images/carousels).
    """
    platform = detect_platform(url)
    if not platform:
        return MediaResult(
            post_url=url,
            platform='unknown',
            error="Unsupported platform. Supported: Instagram Reels/TV, X/Twitter videos."
        )
    
    if platform == 'instagram' and not is_instagram_video_url(url):
        return MediaResult(
            post_url=url,
            platform=platform,
            error="❌ Instagram posts (/p/) not supported. Only Reels/TV videos."
        )
    
    target_url = normalize_instagram_url(url) if platform == 'instagram' else url
    download_dir = tempfile.mkdtemp(prefix=f"{platform}_")
    
    cookies_path = _resolve_cookies_file() if platform == 'instagram' else None
    
    # Method 1: Desktop user-agent
    ydl_opts = get_yt_dlp_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = info.get('description', '')[:400] if platform == 'twitter' else None
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir, platform, tweet_text)
    except Exception:
        pass  # Try mobile
    
    # Method 2: Mobile user-agent
    ydl_opts = get_mobile_headers_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = info.get('description', '')[:400] if platform == 'twitter' else None
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir, platform, tweet_text)
    except Exception as e:
        if platform == 'twitter':
            try:
                # Try getting info without downloading, which doesn't error out on text-only tweets
                with yt_dlp.YoutubeDL(get_yt_dlp_options(download_dir, cookies_path)) as ydl:
                    info = ydl.extract_info(target_url, download=False)
                    if info and info.get('description'):
                        return process_info_result(info, url, download_dir, platform, info.get('description')[:400])
            except Exception:
                pass
        _cleanup_temp_cookie(cookies_path)
        error_msg = str(e)
        
        if 'unable to extract video url' in error_msg.lower():
            return MediaResult(
                post_url=url,
                platform=platform,
                error=(
                    "yt-dlp could not extract the video URL.\n\n"
                    "Fix: `pip install -U yt-dlp` then restart bot.\n\n"
                    "For Instagram, try full cookies file (INSTAGRAM_COOKIES_FILE)."
                )
            )
        
        auth_needed = (
            'login' in error_msg.lower() or 'sign in' in error_msg.lower() or 
            'rate-limit' in error_msg.lower() or 'not available' in error_msg.lower()
        )
        if auth_needed and platform == 'instagram':
            return MediaResult(
                post_url=url,
                platform=platform,
                error=(
                    "Instagram requires authentication.\n\n"
                    "1. instagram.com → F12 → Application → Cookies → instagram.com → sessionid value\n"
                    "2. .env: INSTAGRAM_SESSION_ID=<value>\n"
                    "3. Restart bot"
                )
            )
        
        return MediaResult(
            post_url=url,
            platform=platform,
            error=f"Download failed: {error_msg}"
        )
    
    _cleanup_temp_cookie(cookies_path)
    return MediaResult(
        post_url=url,
        platform=platform,
        error="Unable to download video."
    )
