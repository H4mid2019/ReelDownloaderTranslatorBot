"""
LLM-based text summarization for YouTube transcripts.
Uses Google Gemini (AI Studio) for generating summaries.
"""

import json
import logging
import openai

from config import GEMINI_API_KEY, YOUTUBE_SUMMARY_MODEL

logger = logging.getLogger(__name__)


class Summarizer:
    """LLM-based text summarization using Google Gemini."""

    def __init__(self):
        self.client = openai.OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=GEMINI_API_KEY or "unset",
        )
        self.model = YOUTUBE_SUMMARY_MODEL

    def detect_language(self, text: str) -> str:
        """
        Detect transcript language using langdetect.

        Returns:
            ISO 639-1 language code (e.g., "en", "ja", "ko")
        """
        if not text or not text.strip():
            return "unknown"

        try:
            import langdetect

            langs = langdetect.detect_langs(text)
            if not langs:
                return "unknown"

            best_match = langs[0]
            lang_code = best_match.lang

            # Normalize to 2-letter ISO code
            # langdetect returns codes like "zh-cn" -> "zh"
            if "-" in lang_code:
                lang_code = lang_code.split("-")[0]

            return lang_code

        except ImportError:
            logger.warning("langdetect not installed, defaulting to English")
            return "en"
        except Exception as e:
            logger.warning(f"Language detection failed: {e}")
            return "unknown"

    def generate_summary(
        self, transcript: str, video_title: str, source_language: str = "unknown"
    ) -> dict:
        """
        Generate highlights, takeaway, and brief using Gemini.

        Args:
            transcript: The cleaned transcript text
            video_title: Title of the YouTube video
            source_language: ISO language code of transcript

        Returns:
            dict with: brief, highlights, takeaway, source_language, error
        """
        if not transcript or not transcript.strip():
            return {
                "brief": "",
                "highlights": [],
                "takeaway": "",
                "source_language": source_language,
                "error": "Empty transcript provided",
            }

        # Detect source language if not provided or unknown
        if not source_language or source_language == "unknown":
            source_language = self.detect_language(transcript)

        # Build prompt based on source language
        if source_language != "en":
            prompt = self._build_multilingual_prompt(transcript, video_title, source_language)
        else:
            prompt = self._build_english_prompt(transcript, video_title)

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=4000,
                )

                content = response.choices[0].message.content.strip()

                # Parse JSON response
                # Handle potential markdown code blocks
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    content = content.rsplit("```", 1)[0]
                    content = content.strip()

                result = json.loads(content)

                return {
                    "brief": result.get("brief", ""),
                    "highlights": result.get("highlights", []),
                    "takeaway": result.get("takeaway", ""),
                    "source_language": source_language,
                    "error": None,
                }

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return {
                        "brief": "",
                        "highlights": [],
                        "takeaway": "",
                        "source_language": source_language,
                        "error": f"Failed to parse AI response: {str(e)}",
                    }
            except Exception as e:
                error_msg = str(e).lower()
                if "rate" in error_msg or "429" in error_msg:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay * (2 ** attempt))
                        continue
                return {
                    "brief": "",
                    "highlights": [],
                    "takeaway": "",
                    "source_language": source_language,
                    "error": f"Summary generation failed: {str(e)}",
                }

        return {
            "brief": "",
            "highlights": [],
            "takeaway": "",
            "source_language": source_language,
            "error": "Max retries exceeded",
        }

    def _build_english_prompt(self, transcript: str, title: str) -> str:
        """Build prompt for English transcripts."""
        # Truncate transcript if too long (Gemini token limits)
        max_chars = 30000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

        return f"""You are an AI assistant that analyzes video transcripts and provides concise summaries.

Video Title: "{title}"

Given the following transcript, provide:

1. BRIEF SUMMARY (2-3 paragraphs):
   A concise overview of the video's content and main topics.

2. KEY HIGHLIGHTS (5-7 bullet points):
   The most important points, insights, or memorable moments.

3. TAKEAWAY (1-2 sentences):
   The main lesson, conclusion, or value the viewer should remember.

Transcript:
{transcript}

Respond in the following JSON format only (no other text):
{{
  "brief": "...",
  "highlights": ["...", "..."],
  "takeaway": "..."
}}"""

    def _build_multilingual_prompt(
        self, transcript: str, title: str, lang: str
    ) -> str:
        """Build prompt for non-English transcripts."""
        # Language names for display
        lang_names = {
            "ja": "Japanese",
            "ko": "Korean",
            "zh": "Chinese",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
            "pt": "Portuguese",
            "ru": "Russian",
            "ar": "Arabic",
            "hi": "Hindi",
            "it": "Italian",
            "nl": "Dutch",
            "pl": "Polish",
            "tr": "Turkish",
            "vi": "Vietnamese",
            "th": "Thai",
            "id": "Indonesian",
            "ms": "Malay",
        }
        lang_name = lang_names.get(lang, lang.upper())

        # Truncate transcript if too long
        max_chars = 30000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

        return f"""You are an AI assistant that analyzes video transcripts and provides concise summaries.

Video Title: "{title}"
Source Language: {lang_name}

IMPORTANT: The transcript is in {lang_name}. Please:
- Analyze the content accurately in the original language
- Translate key terms, names, and important phrases to English in your summary
- Ensure the brief and takeaways are written in English

Given the following transcript (in {lang_name}), provide:

1. BRIEF SUMMARY (2-3 paragraphs in English):
   A concise overview of the video's content and main topics.

2. KEY HIGHLIGHTS (5-7 bullet points in English):
   The most important points, insights, or memorable moments.

3. TAKEAWAY (1-2 sentences in English):
   The main lesson, conclusion, or value the viewer should remember.

Transcript ({lang_name}):
{transcript}

Respond in the following JSON format only (all text in English):
{{
  "brief": "...",
  "highlights": ["...", "..."],
  "takeaway": "..."
}}"""
