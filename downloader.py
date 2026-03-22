"""
Instagram post downloader using yt-dlp.
Handles both images and videos from public Instagram posts.
"""
import os
import re
import tempfile
import yt_dlp
from dataclasses import dataclass
from typing import List, Optional
from config import INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD


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
    # Convert instagr.am to instagram.com
    url = url.replace('instagr.am', 'instagram.com')
    return url


def download_instagram_post(url: str) -> MediaResult:
    """
    Download a public Instagram post (image or video).
    
    Returns a MediaResult with the downloaded file path and metadata.
    """
    normalized_url = normalize_instagram_url(url)
    
    # Create a temp directory for downloads
    download_dir = tempfile.mkdtemp(prefix="insta_")
    
    ydl_opts = {
        'outtmpl': os.path.join(download_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    # Optionally add login credentials if provided
    if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
        ydl_opts['http_headers'] = {
            'Cookie': f' sessionid={INSTAGRAM_USERNAME}'
        }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            
            if info is None:
                return MediaResult(
                    post_url=url,
                    media_type='unknown',
                    file_path='',
                    file_size_bytes=0,
                    error="Failed to extract post information"
                )
            
            # Determine media type
            if info.get('duration'):
                media_type = 'video'
            elif 'image' in str(info.get('format', '')).lower() or info.get('thumbnail'):
                media_type = 'image'
            else:
                media_type = info.get('extractor_key', 'unknown').lower()
                if 'video' not in media_type and 'image' not in media_type:
                    # Check for video formats
                    formats = info.get('formats', [])
                    for fmt in formats:
                        if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                            media_type = 'video'
                            break
                    else:
                        media_type = 'image'
            
            # Find the downloaded file
            file_path = ''
            file_size = 0
            
            # Check for single file (image or video)
            if 'filepath' in info:
                file_path = info['filepath']
            elif 'download' in info and 'filepath' in info['download']:
                file_path = info['download']['filepath']
            else:
                # Look for downloaded files in the directory
                for f in os.listdir(download_dir):
                    file_path = os.path.join(download_dir, f)
                    if os.path.isfile(file_path):
                        break
            
            if file_path and os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
            else:
                return MediaResult(
                    post_url=url,
                    media_type='unknown',
                    file_path='',
                    file_size_bytes=0,
                    error="Downloaded file not found"
                )
            
            # Handle albums/carousel (multiple images/videos)
            entries = info.get('entries', [])
            if entries and len(entries) > 1:
                # This is an album - return first item as representative
                all_files = []
                for entry in entries:
                    if entry and 'filepath' in entry:
                        all_files.append(entry['filepath'])
                
                if all_files:
                    file_path = all_files[0]
                    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            
            return MediaResult(
                post_url=url,
                media_type=media_type,
                file_path=file_path,
                file_size_bytes=file_size,
                duration_seconds=info.get('duration'),
                caption=info.get('description') or info.get('title', ''),
                error=None
            )
            
    except yt_dlp.utils.DownloadError as e:
        return MediaResult(
            post_url=url,
            media_type='unknown',
            file_path='',
            file_size_bytes=0,
            error=f"Download error: {str(e)}"
        )
    except Exception as e:
        return MediaResult(
            post_url=url,
            media_type='unknown',
            file_path='',
            file_size_bytes=0,
            error=f"Unexpected error: {str(e)}"
        )
    finally:
        # Clean up temp directory (optional - keep files for debugging)
        # import shutil
        # shutil.rmtree(download_dir, ignore_errors=True)
        pass
