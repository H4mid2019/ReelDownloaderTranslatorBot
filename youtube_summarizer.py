"""
YouTube metadata and transcript extraction.
Uses youtube-transcript-api for transcript extraction and yt-dlp for metadata.
"""

import logging
import re
import yt_dlp  # type: ignore[import-untyped]


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
        Get video metadata using yt-dlp (info extraction only, no download).

        Returns:
            dict with: video_id, title, description, duration, duration_formatted,
                       thumbnail, url
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return {"error": "Failed to extract video information"}

            video_id = info.get("id", "")
            duration = info.get("duration") or 0

            # Format duration as HH:MM:SS or MM:SS
            if duration:
                hours, remainder = divmod(int(duration), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    duration_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                else:
                    duration_formatted = f"{minutes:02d}:{seconds:02d}"
            else:
                duration_formatted = "N/A"

            return {
                "video_id": video_id,
                "title": info.get("title", "Unknown Video"),
                "description": info.get("description", ""),
                "duration": duration,
                "duration_formatted": duration_formatted,
                "thumbnail": info.get("thumbnail", ""),
                "url": url,
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
                "error": None,
            }

        except Exception as e:
            error_msg = str(e).lower()
            if "private" in error_msg:
                return {"error": "This video is private"}
            if "age" in error_msg or "age-restricted" in error_msg:
                return {"error": "This video is age-restricted"}
            if "region" in error_msg or "blocked" in error_msg:
                return {"error": "This video is not available in your region"}
            return {"error": f"Failed to get video metadata: {str(e)}"}

    def extract_transcript(self, url: str) -> dict:
        """
        Extract transcript using youtube-transcript-api.

        Returns:
            dict with: text, language, is_auto_generated, error
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import (
                TranscriptsDisabled,
                NoTranscriptFound,
                VideoUnavailable,
            )

            # Extract video ID from URL
            video_id = self._extract_video_id(url)

            # Try to get manually created transcript first
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # Priority: manually created > auto-generated (English) > any auto-generated
            try:
                # Try English first
                transcript = transcript_list.find_transcript(["en"])
                is_auto = transcript.is_generated
                language_code = transcript.language_code
            except NoTranscriptFound:
                try:
                    # Try any manually created transcript
                    transcript = transcript_list.find_transcript(
                        [t.language_code for t in transcript_list]
                    )
                    is_auto = transcript.is_generated
                    language_code = transcript.language_code
                except NoTranscriptFound:
                    # Fallback to auto-generated
                    try:
                        transcript = transcript_list.find_generated_transcript(
                            ["en"]
                        )
                        is_auto = True
                        language_code = "en"
                    except NoTranscriptFound:
                        return {
                            "text": "",
                            "language": "unknown",
                            "is_auto_generated": False,
                            "error": "This video doesn't have subtitles or transcripts available",
                        }

            # Fetch transcript data
            transcript_data = transcript.fetch()

            # Combine into single text, preserving paragraph breaks
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
                "error": None,
            }

        except ImportError:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "error": "youtube-transcript-api not installed. Run: pip install youtube-transcript-api",
            }
        except VideoUnavailable:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "error": "This video is not available or has been removed",
            }
        except TranscriptsDisabled:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "error": "This video has disabled subtitles/transcripts",
            }
        except NoTranscriptFound:
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
                "error": "This video doesn't have subtitles or transcripts available",
            }
        except Exception as e:
            logger.error(f"Transcript extraction error: {e}")
            return {
                "text": "",
                "language": "unknown",
                "is_auto_generated": False,
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

        import re

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
        # Add period if line ends with word and next starts with capital
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
