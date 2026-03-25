"""
Instagram post downloader using yt-dlp.
Handles both images and videos from public Instagram posts.
Requires Instagram session cookies for posts (images/carousels).
"""
import os
import re
import tempfile
import subprocess
import json
import logging
import yt_dlp  # type: ignore[import-untyped]

# Fix for PermissionError on Windows Python 3.12 SSL keylogging
if 'SSLKEYLOGFILE' not in os.environ:
    os.environ['SSLKEYLOGFILE'] = os.path.join(tempfile.gettempdir(), 'yt_dlp_ssl.log')

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
        r'(https?://)?(www\.)?instagram\.com/(reel|reels|tv|p)/[\w-]+/?',
        r'(https?://)?(www\.)?instagr\.am/(reel|reels|tv|p)/[\w-]+/?',
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
    ydl_opts['impersonate'] = 'chrome'  # Use curl-cffi impersonation
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
    ydl_opts['impersonate'] = 'chrome'
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



def download_instagram_post_cobalt(url: str, download_dir: str) -> MediaResult:
    import time

    # List of public Cobalt mirrors to try
    mirrors = [
        "https://co.wuk.sh/api/json",
        "https://cobalt-api.hyper.lol/api/json",
        "https://api.clxxped.lol/api/json",
        "https://cobalt.api.vve.best/api/json",
        "https://api.cobalt.best/api/json",
        "https://cobalt.api.timelessnesses.me/api/json",
        "https://api-dl.cgm.rs/api/json",
        "https://cobalt.synzr.space/api/json",
        "https://api.co.rooot.gay/api/json",
    ]

    for api_url in mirrors:
        try:
            payload = json.dumps({"url": url})
            res = subprocess.run([
                'curl.exe', '-s', '-X', 'POST', api_url,
                '-H', 'Accept: application/json',
                '-H', 'Content-Type: application/json',
                '-d', payload
            ], capture_output=True, text=True, timeout=10)

            if res.returncode != 0 or not res.stdout:
                continue

            data = json.loads(res.stdout)
            if data.get('status') == 'error':
                continue

            media_url = ""
            if data.get('status') in ['stream', 'redirect']:
                media_url = data.get('url')
            elif data.get('status') == 'picker':
                picker_items = data.get('picker', [])
                if picker_items:
                    media_url = picker_items[0].get('url')

            if not media_url:
                continue

            ext = '.mp4' if '.mp4' in media_url else '.jpg'
            file_path = os.path.join(download_dir, f"ig_post_{int(time.time())}{ext}")

            # Download the actual media
            res_dl = subprocess.run(['curl.exe', '-s', '-L', '-o', file_path, media_url], timeout=60)
            if res_dl.returncode != 0 or not os.path.exists(file_path):
                continue

            return MediaResult(
                post_url=url,
                platform='instagram',
                media_type='video' if ext == '.mp4' else 'photo',
                file_path=file_path,
                file_size_bytes=os.path.getsize(file_path),
                error=None
            )
        except Exception:
            continue

    return MediaResult(post_url=url, platform='instagram', error="All Cobalt mirrors failed to process this post.")


def download_instagram_post_gallery_dl(url: str, download_dir: str, cookies_path: Optional[str] = None) -> MediaResult:
    """Tertiary fallback using gallery-dl."""
    logger = logging.getLogger(__name__)
    logger.info(f"Attempting gallery-dl for: {url}")
    
    # gallery-dl -d download_dir --cookies cookies_path url
    # Note: gallery-dl might create subdirectories, so we'll walk the temp dir
    cmd = [os.path.join(".venv", "Scripts", "gallery-dl.exe"), "--dest", download_dir]
    if cookies_path:
        cmd.extend(["--cookies", cookies_path])
    cmd.append(url)
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        # Scan for ANY media file downloaded
        for root, _, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(('.mp4', '.mkv', '.mov', '.jpg', '.jpeg', '.png', '.webp')):
                    file_path = os.path.join(root, file)
                    file_size = os.path.getsize(file_path)
                    ext = os.path.splitext(file)[1].lower()
                    return MediaResult(
                        post_url=url,
                        platform='instagram',
                        media_type='video' if ext in ('.mp4', '.mkv', '.mov') else 'photo',
                        file_path=file_path,
                        file_size_bytes=file_size,
                        error=None
                    )
    except Exception as e:
        logger.error(f"gallery-dl failed: {e}")
        
    return MediaResult(post_url=url, platform='instagram', error="gallery-dl fallback failed")


def download_video(url: str) -> MediaResult:
    """
    Download video from Instagram Reels/TV, X/Twitter, or YouTube Shorts.
    Supports multi-layered fallbacks for better reliability.
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
            error="❌ Unsupported Instagram URL format."
        )
    
    target_url = normalize_instagram_url(url) if platform == 'instagram' else url
    download_dir = tempfile.mkdtemp(prefix=f"{platform}_")
    cookies_path = _resolve_cookies_file() if platform == 'instagram' else None
    logger = logging.getLogger(__name__)

    # Use multi-layered fallback for Instagram /p/ posts (which yt-dlp blocks)
    if platform == 'instagram' and '/p/' in url:
        res = download_instagram_post_cobalt(url, download_dir)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res
        # Fallback to gallery-dl if Cobalt fails for /p/
        res = download_instagram_post_gallery_dl(url, download_dir, cookies_path)
        _cleanup_temp_cookie(cookies_path)
        return res
    
    # Standard download chain (yt-dlp -> Fallbacks)
    last_error = "Unknown error"
    
    # Method 1: yt-dlp Desktop
    ydl_opts = get_yt_dlp_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: # type: ignore[arg-type]
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = info.get('description', '')[:4000] if platform == 'twitter' else None # type: ignore[index]
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir, platform, tweet_text) # type: ignore[arg-type]
    except Exception as e:
        last_error = str(e)
        logger.warning(f"yt-dlp desktop failed: {last_error}")
    
    # Method 2: yt-dlp Mobile
    ydl_opts = get_mobile_headers_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: # type: ignore[arg-type]
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = info.get('description', '')[:4000] if platform == 'twitter' else None # type: ignore[index]
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(info, url, download_dir, platform, tweet_text) # type: ignore[arg-type]
    except Exception as e:
        last_error = str(e)
        logger.warning(f"yt-dlp mobile failed: {last_error}")

    # Method 3: Instagram-specific fallbacks (Cobalt -> gallery-dl)
    if platform == 'instagram':
        logger.info("yt-dlp failed for Instagram, trying Cobalt fallback...")
        res = download_instagram_post_cobalt(url, download_dir)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res
        
        logger.info("Cobalt failed, trying gallery-dl fallback...")
        res = download_instagram_post_gallery_dl(url, download_dir, cookies_path)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res
        
        last_error = res.error or last_error

    # Method 4: Twitter-specific fallback (vxtwitter)
    if platform == 'twitter':
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            api_url = f"https://api.vxtwitter.com{parsed.path}"
            res_vx = subprocess.run(['curl', '-s', api_url], capture_output=True, text=True, timeout=10)
            if res_vx.returncode == 0:
                data = json.loads(res_vx.stdout)
                text = data.get('text')
                if text:
                    _cleanup_temp_cookie(cookies_path)
                    return MediaResult(
                        post_url=url,
                        platform=platform,
                        media_type='text',
                        tweet_text=text[:4000]
                    )
        except Exception as e:
            logger.debug(f"vxtwitter fallback failed: {e}")

    # Final error handling
    _cleanup_temp_cookie(cookies_path)
    
    if 'unable to extract video url' in last_error.lower():
        return MediaResult(
            post_url=url,
            platform=platform,
            error=(
                "Could not extract video. Platform might be blocking us.\n\n"
                "Fixes:\n"
                "1. `pip install -U yt-dlp`\n"
                "2. Provide fresh cookies in `INSTAGRAM_COOKIES_FILE`."
            )
        )
    
    auth_needed = (
        'login' in last_error.lower() or 'sign in' in last_error.lower() or 
        'rate-limit' in last_error.lower() or 'not available' in last_error.lower()
    )
    if auth_needed and platform == 'instagram':
        return MediaResult(
            post_url=url,
            platform=platform,
            error=(
                "Instagram requires authentication.\n"
                "Check your INSTAGRAM_SESSION_ID or provide a full cookie file."
            )
        )
    
    return MediaResult(
        post_url=url,
        platform=platform,
        error=f"Download failed after trying all methods. Error: {last_error}"
    )
    return MediaResult(
        post_url=url,
        platform=platform,
        error="Unable to download video."
    )
