"""
Instagram post downloader using yt-dlp, instaloader, gallery-dl and Cobalt.
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
import sys
import shutil
from typing import Optional, List

# Fix for PermissionError on Windows Python 3.12 SSL keylogging
if "SSLKEYLOGFILE" not in os.environ:
    os.environ["SSLKEYLOGFILE"] = os.path.join(tempfile.gettempdir(), "yt_dlp_ssl.log")

from dataclasses import dataclass, field
import time
from config import INSTAGRAM_COOKIES_FILE, INSTAGRAM_SESSION_ID, INSTAGRAM_USERNAME

try:
    import instaloader  # type: ignore[import-untyped]

    _INSTALOADER_AVAILABLE = True
except ImportError:
    _INSTALOADER_AVAILABLE = False


@dataclass
class MediaResult:
    """Result of downloading a video post."""

    post_url: str
    media_type: str = "unknown"
    file_path: str = ""
    file_paths: List[str] = field(default_factory=list)
    file_size_bytes: int = 0
    duration_seconds: Optional[float] = None
    caption: Optional[str] = None
    platform: str = "unknown"
    tweet_text: Optional[str] = None
    error: Optional[str] = None


def is_instagram_video_url(url: str) -> bool:
    """Check if URL is Instagram video (reel/tv only, no /p/ posts)."""
    patterns = [
        r"(https?://)?(www\.)?instagram\.com/(reel|reels|tv|p)/[\w-]+/?",
        r"(https?://)?(www\.)?instagr\.am/(reel|reels|tv|p)/[\w-]+/?",
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)


def is_twitter_url(url: str) -> bool:
    """Check if URL is Twitter/X video post."""
    patterns = [
        r"(https?://)?(www\.)?(twitter\.com|x\.com)/[\w-]+/status/[\d-]+",
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)


def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video."""
    patterns = [
        r"(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+",
        r"(https?://)?(www\.)?youtu\.be/[\w-]+",
        r"(https?://)?(www\.)?youtube\.com/shorts/[\w-]+",
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)


def detect_platform(url: str) -> Optional[str]:
    """Detect platform: 'instagram', 'twitter', 'youtube', or None."""
    if is_instagram_video_url(url):
        return "instagram"
    if is_twitter_url(url):
        return "twitter"
    if is_youtube_url(url):
        return "youtube"
    return None


def normalize_instagram_url(url: str) -> str:
    """Convert various Instagram URL formats to the standard format."""
    if not url.startswith("http"):
        url = "https://" + url
    url = url.replace("instagr.am", "instagram.com")
    # Remove query params that might interfere
    if "?" in url:
        base_url = url.split("?")[0]
        return base_url.rstrip("/")
    return url.rstrip("/")


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
        mode="w", suffix="_insta_cookies.txt", delete=False
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
        "outtmpl": os.path.join(download_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "extractor_retries": 3,
        "fragment_retries": 3,
        # Cap at Instagram Reel standard (720x1280 portrait or 1280p landscape equiv) to prevent huge files
        # Portrait videos have height=1280, width=720; landscape height<=720
        "format": "bestvideo[height<=1280][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1280]+bestaudio/best[height<=1280]/best",
    }


def get_yt_dlp_options(download_dir: str, cookies_path: Optional[str] = None) -> dict:
    """
    Get yt-dlp options with proper Instagram authentication.
    Priority: cookies file > session ID > unauthenticated.
    Note: username/password login is broken in current yt-dlp Instagram extractor.
    """
    ydl_opts = _base_ydl_opts(download_dir)
    ydl_opts["impersonate"] = "chrome"  # Use curl-cffi impersonation
    ydl_opts["http_headers"] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
    return ydl_opts


def get_mobile_headers_options(
    download_dir: str, cookies_path: Optional[str] = None
) -> dict:
    """Get options with mobile user-agent (sometimes works better for Reels)."""
    ydl_opts = _base_ydl_opts(download_dir)
    ydl_opts["impersonate"] = "chrome"
    ydl_opts["http_headers"] = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
    return ydl_opts


def process_info_result(
    info: dict,
    original_url: str,
    download_dir: str,
    platform: str,
    tweet_text: Optional[str] = None,
) -> MediaResult:
    """Process yt-dlp info result and return MediaResult."""

    if info is None:
        return MediaResult(
            post_url=original_url,
            platform=platform,
            error="Failed to extract post information",
        )

    # Assume video (no image support)
    media_type = "video"

    # Find downloaded files
    file_paths: List[str] = []
    file_size: int = 0

    if os.path.exists(download_dir):
        for f in os.listdir(download_dir):
            potential_path = os.path.join(download_dir, f)
            if os.path.isfile(potential_path) and not f.endswith(".json"):
                file_paths.append(potential_path)
                file_size += os.path.getsize(potential_path)  # type: ignore

    # Sort files by name to ensure consistent ordering if downloading carousels
    file_paths.sort()

    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"Downloaded {platform}: {len(file_paths)} files, "
        f"total_size={file_size / (1024 * 1024):.2f}MB, "
        f"duration={info.get('duration', 'N/A')}s, "
        f"format_id={info.get('format_id', 'N/A')}"
    )

    if not file_paths:
        if platform == "twitter" and tweet_text:
            return MediaResult(
                post_url=original_url,
                platform=platform,
                media_type="text",
                tweet_text=tweet_text,
                error=None,
            )
        return MediaResult(
            post_url=original_url,
            platform=platform,
            media_type="unknown",
            error="Downloaded file not found",
        )

    caption = info.get("description") or info.get("title", "")

    return MediaResult(
        post_url=original_url,
        platform=platform,
        media_type=media_type,
        file_path=file_paths[0] if file_paths else "",
        file_paths=file_paths,
        file_size_bytes=file_size,
        duration_seconds=info.get("duration"),
        caption=caption,
        tweet_text=tweet_text,
        error=None,
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
            curl_cmd = "curl.exe" if os.name == "nt" else "curl"
            res = subprocess.run(
                [
                    curl_cmd,
                    "-s",
                    "-X",
                    "POST",
                    api_url,
                    "-H",
                    "Accept: application/json",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if res.returncode != 0 or not res.stdout:
                continue

            data = json.loads(res.stdout)
            if data.get("status") == "error":
                continue

            picker_items = data.get("picker", [])
            file_paths = []
            file_size_bytes = 0

            # If picker exists, it's a carousel. Otherwise single media.
            if data.get("status") in ["stream", "redirect"] and data.get("url"):
                urls_to_download = [data.get("url")]
            elif data.get("status") == "picker" and picker_items:
                urls_to_download = [
                    item.get("url") for item in picker_items if item.get("url")
                ]
            else:
                continue

            for idx, media_url in enumerate(urls_to_download):
                ext = ".mp4" if ".mp4" in media_url else ".jpg"
                file_path = os.path.join(
                    download_dir, f"ig_post_{int(time.time())}_{idx}{ext}"
                )

                # Download the actual media
                res_dl = subprocess.run(
                    [curl_cmd, "-s", "-L", "-o", file_path, media_url], timeout=60
                )
                if res_dl.returncode == 0 and os.path.exists(file_path):
                    file_paths.append(file_path)
                    file_size_bytes += os.path.getsize(file_path)  # type: ignore

            if not file_paths:
                continue

            caption = data.get("text") or data.get("caption")
            if not caption and data.get("status") == "picker" and picker_items:
                caption = picker_items[0].get("text") or picker_items[0].get("caption")

            return MediaResult(
                post_url=url,
                platform="instagram",
                media_type="gallery"
                if len(file_paths) > 1
                else ("video" if file_paths[0].endswith(".mp4") else "photo"),
                file_path=file_paths[0],
                file_paths=file_paths,
                file_size_bytes=file_size_bytes,
                caption=caption,
                error=None,
            )
        except Exception:
            continue

    return MediaResult(
        post_url=url,
        platform="instagram",
        error="All Cobalt mirrors failed to process this post.",
    )


def download_instagram_post_gallery_dl(
    url: str, download_dir: str, cookies_path: Optional[str] = None
) -> MediaResult:
    """Tertiary fallback using gallery-dl."""
    logger = logging.getLogger(__name__)
    logger.info(f"Attempting gallery-dl for: {url}")

    # Cross-platform detection of gallery-dl binary
    gallery_dl_bin = shutil.which("gallery-dl")
    if not gallery_dl_bin:
        # Fallback: look in the same directory as the current python executable
        python_dir = os.path.dirname(sys.executable)
        for name in ["gallery-dl", "gallery-dl.exe"]:
            p = os.path.join(python_dir, name)
            if os.path.exists(p):
                gallery_dl_bin = p
                break

    if not gallery_dl_bin:
        gallery_dl_bin = "gallery-dl"  # Last resort, attempt to use system PATH

    # Build gallery-dl command.
    # --write-metadata: creates .json files with caption/metadata
    # Note: --no-config is NOT supported on older gallery-dl versions; omit it.
    cmd = [gallery_dl_bin, "--dest", download_dir, "--write-metadata"]
    if cookies_path:
        cmd.extend(["--cookies", cookies_path])
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=60
        )
        logger.debug(f"gallery-dl stdout: {result.stdout[:500]}")

        # Scan for ANY media file downloaded
        captured_caption = None

        # Look for metadata first
        for root, _, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(".json"):
                    try:
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            # gallery-dl instagram metadata structure: meta[0]['content'] or similar
                            # It's usually a list or dict depending on version/extractor
                            if isinstance(meta, list) and len(meta) > 0:
                                captured_caption = (
                                    meta[0].get("content")
                                    or meta[0].get("description")
                                    or meta[0].get("caption")
                                )
                            elif isinstance(meta, dict):
                                captured_caption = (
                                    meta.get("content")
                                    or meta.get("description")
                                    or meta.get("caption")
                                )
                    except Exception:
                        pass

        file_paths = []
        file_size_bytes = 0
        has_video = False

        for root, _, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(
                    (".mp4", ".mkv", ".mov", ".jpg", ".jpeg", ".png", ".webp")
                ):
                    file_path = os.path.join(root, file)
                    file_paths.append(file_path)
                    file_size_bytes += os.path.getsize(file_path)  # type: ignore
                    if file.lower().endswith((".mp4", ".mkv", ".mov")):
                        has_video = True

        if file_paths:
            file_paths.sort()
            return MediaResult(
                post_url=url,
                platform="instagram",
                media_type="gallery"
                if len(file_paths) > 1
                else ("video" if has_video else "photo"),
                file_path=file_paths[0],
                file_paths=file_paths,
                file_size_bytes=file_size_bytes,
                caption=captured_caption,
                error=None,
            )
    except subprocess.CalledProcessError as e:
        stderr_snippet = (e.stderr or "")[:300]
        logger.error(f"gallery-dl failed (exit {e.returncode}): {stderr_snippet}")
    except Exception as e:
        logger.error(f"gallery-dl exception: {e}")

    return MediaResult(
        post_url=url, platform="instagram", error="gallery-dl fallback failed"
    )


def download_instagram_post_instaloader(url: str, download_dir: str) -> MediaResult:
    """
    Fallback using instaloader Python library — authenticates directly with
    INSTAGRAM_SESSION_ID without relying on the CLI pipeline that gallery-dl uses.
    Works by injecting the session cookie directly into instaloader's request session.
    """
    if not _INSTALOADER_AVAILABLE:
        return MediaResult(
            post_url=url, platform="instagram", error="instaloader not installed"
        )
    has_cookies_file = bool(
        INSTAGRAM_COOKIES_FILE and os.path.exists(INSTAGRAM_COOKIES_FILE)
    )
    if not INSTAGRAM_SESSION_ID and not has_cookies_file:
        return MediaResult(
            post_url=url,
            platform="instagram",
            error="INSTAGRAM_SESSION_ID or INSTAGRAM_COOKIES_FILE not configured",
        )

    logger = logging.getLogger(__name__)
    logger.info(f"Attempting instaloader for: {url}")

    import contextlib
    import io

    try:
        # Suppress instaloader's own stdout chatter (e.g. "403 Forbidden [retrying]")
        _suppress = open(os.devnull, "w") if hasattr(os, "devnull") else io.StringIO()
        with contextlib.redirect_stdout(_suppress):
            # Extract shortcode from URL  (e.g. DWiiZ4bN52Y)
            m = re.search(r"/(reel|reels|p|tv)/([\w-]+)", url)
            if not m:
                return MediaResult(
                    post_url=url,
                    platform="instagram",
                    error="Cannot parse shortcode from URL",
                )
            shortcode = m.group(2)

            # Build an Instaloader instance and inject our session cookies
            L = instaloader.Instaloader(
                download_videos=True,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                quiet=True,
            )
            import requests  # instaloader uses requests under the hood

            expiry = int(time.time()) + 60 * 60 * 24 * 365

            # Prefer full cookies file (all cookies = fewer 403s from Instagram)
            cookies_file = (
                INSTAGRAM_COOKIES_FILE
                if (INSTAGRAM_COOKIES_FILE and os.path.exists(INSTAGRAM_COOKIES_FILE))
                else None
            )
            if cookies_file:
                from http.cookiejar import MozillaCookieJar

                jar = MozillaCookieJar(cookies_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                for c in jar:
                    L.context._session.cookies.set(
                        c.name, c.value, domain=c.domain, path=c.path
                    )  # type: ignore[attr-defined]
                logger.debug(
                    f"instaloader: loaded {len(list(jar))} cookies from {cookies_file}"
                )
            else:
                # Fallback: inject session ID cookie only
                if not INSTAGRAM_SESSION_ID:
                    return MediaResult(
                        post_url=url,
                        platform="instagram",
                        error="No cookies configured (set INSTAGRAM_COOKIES_FILE or INSTAGRAM_SESSION_ID)",
                    )
                cookie = requests.cookies.create_cookie(
                    name="sessionid",
                    value=INSTAGRAM_SESSION_ID,
                    domain=".instagram.com",
                    path="/",
                    secure=True,
                    expires=expiry,
                )
                L.context._session.cookies.set_cookie(cookie)  # type: ignore[attr-defined]

            # Signal to instaloader that we are logged in so it skips anonymous-only paths
            L.context.username = INSTAGRAM_USERNAME or "user"  # type: ignore[attr-defined]

            post = instaloader.Post.from_shortcode(L.context, shortcode)
            caption = post.caption or ""

            file_paths: List[str] = []
            file_size_bytes = 0

            if post.is_video:
                import urllib.request

                video_url = post.video_url
                out_path = os.path.join(download_dir, f"{shortcode}.mp4")
                req = urllib.request.Request(
                    video_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                        "Referer": "https://www.instagram.com/",
                    },
                )
                with (
                    urllib.request.urlopen(req, timeout=60) as resp,
                    open(out_path, "wb") as f,
                ):
                    f.write(resp.read())
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    file_paths.append(out_path)
                    file_size_bytes = os.path.getsize(out_path)
            else:
                # Image or sidecar (carousel)
                if post.typename == "GraphSidecar":
                    for i, node in enumerate(post.get_sidecar_nodes()):
                        import urllib.request

                        img_url = node.video_url if node.is_video else node.display_url
                        ext = ".mp4" if node.is_video else ".jpg"
                        out_path = os.path.join(download_dir, f"{shortcode}_{i}{ext}")
                        req = urllib.request.Request(
                            img_url,
                            headers={
                                "User-Agent": "Mozilla/5.0",
                                "Referer": "https://www.instagram.com/",
                            },
                        )
                        with (
                            urllib.request.urlopen(req, timeout=60) as resp,
                            open(out_path, "wb") as f,
                        ):
                            f.write(resp.read())
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                            file_paths.append(out_path)
                            file_size_bytes += os.path.getsize(out_path)
                else:
                    import urllib.request

                    img_url = post.url
                    out_path = os.path.join(download_dir, f"{shortcode}.jpg")
                    req = urllib.request.Request(
                        img_url,
                        headers={
                            "User-Agent": "Mozilla/5.0",
                            "Referer": "https://www.instagram.com/",
                        },
                    )
                    with (
                        urllib.request.urlopen(req, timeout=60) as resp,
                        open(out_path, "wb") as f,
                    ):
                        f.write(resp.read())
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        file_paths.append(out_path)
                        file_size_bytes = os.path.getsize(out_path)

            if not file_paths:
                return MediaResult(
                    post_url=url,
                    platform="instagram",
                    error="instaloader: no files downloaded",
                )

            file_paths.sort()
            has_video = any(p.endswith(".mp4") for p in file_paths)
            return MediaResult(
                post_url=url,
                platform="instagram",
                media_type="gallery"
                if len(file_paths) > 1
                else ("video" if has_video else "photo"),
                file_path=file_paths[0],
                file_paths=file_paths,
                file_size_bytes=file_size_bytes,
                caption=caption,
                error=None,
            )
    except Exception as e:
        logger.error(f"instaloader failed: {e}")
        return MediaResult(
            post_url=url, platform="instagram", error=f"instaloader failed: {e}"
        )


def download_video(url: str) -> MediaResult:
    """
    Download video from Instagram Reels/TV, X/Twitter, or YouTube Shorts.
    Supports multi-layered fallbacks for better reliability.
    """
    platform = detect_platform(url)
    if not platform:
        return MediaResult(
            post_url=url,
            platform="unknown",
            error="Unsupported platform. Supported: Instagram Reels/TV, X/Twitter videos.",
        )

    if platform == "instagram" and not is_instagram_video_url(url):
        return MediaResult(
            post_url=url,
            platform=platform,
            error="❌ Unsupported Instagram URL format.",
        )

    target_url = normalize_instagram_url(url) if platform == "instagram" else url
    download_dir = tempfile.mkdtemp(prefix=f"{platform}_")
    cookies_path = _resolve_cookies_file() if platform == "instagram" else None
    logger = logging.getLogger(__name__)

    # Use multi-layered fallback for Instagram /p/ posts (which yt-dlp blocks)
    if platform == "instagram" and "/p/" in url:
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
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = (
                    info.get("description", "")[:4000]
                    if platform == "twitter"
                    else None
                )  # type: ignore[index]
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(
                    info, url, download_dir, platform, tweet_text
                )  # type: ignore[arg-type]
    except Exception as e:
        last_error = str(e)
        logger.warning(f"yt-dlp desktop failed: {last_error}")

    # Method 2: yt-dlp Mobile
    ydl_opts = get_mobile_headers_options(download_dir, cookies_path)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(target_url, download=True)
            if info:
                tweet_text = (
                    info.get("description", "")[:4000]
                    if platform == "twitter"
                    else None
                )  # type: ignore[index]
                _cleanup_temp_cookie(cookies_path)
                return process_info_result(
                    info, url, download_dir, platform, tweet_text
                )  # type: ignore[arg-type]
    except Exception as e:
        last_error = str(e)
        logger.warning(f"yt-dlp mobile failed: {last_error}")

    # Method 3: Instagram-specific fallbacks (Cobalt -> instaloader -> gallery-dl)
    if platform == "instagram":
        logger.info("yt-dlp failed for Instagram, trying Cobalt fallback...")
        res = download_instagram_post_cobalt(url, download_dir)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res

        logger.info("Cobalt failed, trying instaloader fallback...")
        res = download_instagram_post_instaloader(url, download_dir)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res
        logger.warning(f"instaloader failed: {res.error}")

        logger.info("instaloader failed, trying gallery-dl fallback...")
        res = download_instagram_post_gallery_dl(url, download_dir, cookies_path)
        if not res.error:
            _cleanup_temp_cookie(cookies_path)
            return res

        last_error = res.error or last_error

    # Method 4: Twitter-specific fallback (vxtwitter)
    if platform == "twitter":
        try:
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            api_url = f"https://api.vxtwitter.com{parsed.path}"
            curl_cmd = "curl.exe" if os.name == "nt" else "curl"
            res_vx = subprocess.run(
                [curl_cmd, "-s", api_url], capture_output=True, text=True, timeout=10
            )
            if res_vx.returncode == 0:
                data = json.loads(res_vx.stdout)
                text = data.get("text")
                media_extended = data.get("media_extended", [])

                if media_extended:
                    file_paths = []
                    file_size_bytes = 0
                    has_video = False

                    import time

                    for idx, media in enumerate(media_extended):
                        media_url = media.get("url")
                        media_type_str = media.get("type")
                        if not media_url:
                            continue

                        ext = ".mp4" if media_type_str in ("video", "gif") else ".jpg"
                        file_path = os.path.join(
                            download_dir, f"tw_media_{int(time.time())}_{idx}{ext}"
                        )

                        res_dl = subprocess.run(
                            [curl_cmd, "-s", "-L", "-o", file_path, media_url],
                            timeout=60,
                        )
                        if res_dl.returncode == 0 and os.path.exists(file_path):
                            file_paths.append(file_path)
                            file_size_bytes += os.path.getsize(file_path)
                            if ext == ".mp4":
                                has_video = True

                    if file_paths:
                        _cleanup_temp_cookie(cookies_path)
                        return MediaResult(
                            post_url=url,
                            platform=platform,
                            media_type="gallery"
                            if len(file_paths) > 1
                            else ("video" if has_video else "photo"),
                            file_path=file_paths[0],
                            file_paths=file_paths,
                            file_size_bytes=file_size_bytes,
                            caption=text,
                            tweet_text=text,
                            error=None,
                        )

                if text:
                    _cleanup_temp_cookie(cookies_path)
                    return MediaResult(
                        post_url=url,
                        platform=platform,
                        media_type="text",
                        tweet_text=text[:4000],
                    )
        except Exception as e:
            logger.debug(f"vxtwitter fallback failed: {e}")

    # Final error handling
    _cleanup_temp_cookie(cookies_path)

    if "unable to extract video url" in last_error.lower():
        return MediaResult(
            post_url=url,
            platform=platform,
            error=(
                "Could not extract video. Platform might be blocking us.\n\n"
                "Fixes:\n"
                "1. `pip install -U yt-dlp`\n"
                "2. Provide fresh cookies in `INSTAGRAM_COOKIES_FILE`."
            ),
        )

    auth_needed = (
        "login" in last_error.lower()
        or "sign in" in last_error.lower()
        or "rate-limit" in last_error.lower()
        or "not available" in last_error.lower()
    )
    if auth_needed and platform == "instagram":
        return MediaResult(
            post_url=url,
            platform=platform,
            error=(
                "Instagram requires authentication.\n"
                "Check your INSTAGRAM_SESSION_ID or provide a full cookie file."
            ),
        )

    return MediaResult(
        post_url=url,
        platform=platform,
        error=f"Download failed after trying all methods. Error: {last_error}",
    )
