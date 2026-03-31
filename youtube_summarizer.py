"""
YouTube metadata and transcript extraction.
Uses youtube-transcript-api for transcript extraction and YouTube oEmbed API for metadata.
"""

import logging
import re
import urllib.request
import json
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

logger = logging.getLogger(__name__)


class YouTubeSummarizer:
    """Extract metadata and transcript from YouTube videos."""

    # Non-speech patterns to filter from transcripts
    NON_SPEECH_PATTERNS = [
        r"\[.*?\]",  # [Music], [Applause], [Laughter]
        r"\(.*?\)",  # (Applause), (laughter)
        r"^\s*♪+\s*$",  # Musical notes
        r"^\s*🔔\s*$",  # Bell emoji
        r"^\s*Music\s*$",  # Music labels
        r"\s{3,}",  # Multiple spaces
    ]

    def get_metadata(self, url: str) -> dict:
        """
        Get video metadata using YouTube oEmbed API (no auth required).

        Returns:
            dict with: video_id, title, thumbnail, url, author_name
        """
        try:
            video_id = self._extract_video_id(url)

            # Use oEmbed API - free, no auth required
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            req = urllib.request.Request(
                oembed_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)",
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

            return {
                "video_id": video_id,
                "title": data.get("title", "Unknown Video"),
                "author_name": data.get("author_name", "Unknown"),
                "thumbnail": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                "url": url,
                "error": None,
            }

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"error": "This video is not available or has been removed"}
            elif e.code == 403:
                return {"error": "This video is private or restricted"}
            return {"error": f"Failed to get video metadata: HTTP {e.code}"}
        except Exception as e:
            return {"error": f"Failed to get video metadata: {str(e)}"}

    def extract_transcript(self, url: str) -> dict:
        """
        Extract transcript using youtube-transcript-api.

        Returns:
            dict with: text, language, is_auto_generated, duration_seconds, error
        """
        try:
            # Extract video ID from URL
            video_id = self._extract_video_id(url)

            # Try to get manually created transcript first
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # Priority: manually created > auto-generated (English) > any auto-generated
            transcript = None
            is_auto = False
            language_code = "en"

            try:
                # Try English first
                transcript = transcript_list.find_transcript(["en"])
                is_auto = transcript.is_generated
                language_code = transcript.language_code
            except NoTranscriptFound:
                try:
                    # Try any manually created transcript (not auto-generated)
                    for t in transcript_list:
                        if not t.is_generated:
                            transcript = t
                            is_auto = False
                            language_code = t.language_code
                            break
                except Exception:
                    pass

            # Fallback to any auto-generated
            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript(["en"])
                    is_auto = True
                    language_code = "en"
                except NoTranscriptFound:
                    # Try any available transcript
                    try:
                        transcript = transcript_list.find_transcript(
                            [t.language_code for t in transcript_list]
                        )
                        is_auto = transcript.is_generated
                        language_code = transcript.language_code
                    except NoTranscriptFound:
                        return {
                            "text": "",
                            "language": "unknown",
                            "is_auto_generated": False,
                            "duration_seconds": 0,
                            "error": "This video doesn't have subtitles or transcripts available",
                        }

            # Fetch transcript data
            transcript_data = transcript.fetch()

            # Calculate duration from transcript entries
            duration_seconds = 0
            if transcript_data:
                last_entry = transcript_data[-1]
                duration_seconds = last_entry.start + last_entry.duration

            # Combine into single text
            lines = []
            for entry in transcript_data:
                text = entry.text.strip()
                if text:
                    lines.append(text)

            full_text = " ".join(lines)

            # Detect language code
            detected_lang = language_code.split("-")[0] if "-" in language_code else language_code

            return {
                "text": full_text,
                "language": detected_lang,
                "is_auto_generated": is_auto,
                "duration_seconds": duration_seconds,
                "error": None,
            }

        except VideoUnavailable:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "duration_seconds": 0,
                "error": "This video is not available or has been removed",
            }
        except TranscriptsDisabled:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "duration_seconds": 0,
                "error": "This video has disabled subtitles/transcripts",
            }
        except Exception as e:
            error_str = str(e).lower()
            if "sign in" in error_str or "bot" in error_str:
                return {
                    "text": "",
                    "language": "unknown",
                    "is_auto_generated": False,
                    "duration_seconds": 0,
                    "error": "YouTube is blocking requests. Please try again later.",
                }
            logger.error(f"Transcript extraction error: {e}")
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "duration_seconds": 0,
                "error": f"Failed to extract transcript: {str(e)}",
            }

    def clean_transcript(self, transcript: str) -> str:
        """
        Remove non-speech elements from transcript.
        Handles: [Music], [Applause], (laughter), timestamps, filler words.

        Returns cleaned transcript.
        """
        if not transcript:
            return ""

        text = transcript

        # Step 1: Remove bracketed content
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(r"\(.*?\)", "", text)

        # Step 2: Remove lines that are mostly music/sounds
        lines = text.split("\n")
        cleaned_lines = []
        for line in lines:
            stripped = line.strip().lower()
            # Skip lines that are mostly music/sound markers
            if any(kw in stripped for kw in ["music", "♪", "♫", "🔔"]):
                continue
            if len(stripped) < 3:  # Skip very short lines
                continue
            cleaned_lines.append(line)

        text = " ".join(cleaned_lines)

        # Step 3: Collapse excessive whitespace
        text = re.sub(r"\s+", " ", text)

        # Step 4: Add basic punctuation where missing (simple heuristic)
        text = re.sub(r"(\w)$", r"\1.", text)

        return text.strip()

    def detect_transcript_quality(
        self, transcript: str, is_auto: bool
    ) -> tuple[str, str]:
        """
        Assess transcript quality.

        Returns:
            tuple of (quality: str, note: str)
            quality: "excellent" | "good" | "fair" | "poor" | "very_poor"
        """
        word_count = len(transcript.split())

        # Very short = very poor
        if word_count < 50:
            return "very_poor", "Very limited transcript available"

        # Check for common auto-caption issues
        if is_auto:
            # Auto-captions often have no punctuation
            has_punctuation = any(c in transcript for c in ".!?,")
            if not has_punctuation:
                return "poor", "Auto-captions may have errors"
            if word_count < 200:
                return "fair", "Fair quality transcript"

        if word_count > 500:
            return "excellent", "High quality transcript"
        elif word_count > 200:
            return "good", "Good quality transcript"
        else:
            return "fair", "Fair quality transcript"

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from various YouTube URL formats."""
        patterns = [
            r"(?:youtube\.com/watch\?v=)([\w-]+)",
            r"(?:youtu\.be/)([\w-]+)",
            r"(?:youtube\.com/shorts/)([\w-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        raise ValueError(f"Could not extract video ID from URL: {url}")

    def format_duration(self, seconds: int) -> str:
        """Format seconds as HH:MM:SS or MM:SS."""
        if seconds <= 0:
            return "N/A"

        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
