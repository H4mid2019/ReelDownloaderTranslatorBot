"""
LLM-based text summarization for YouTube transcripts.
Uses Google Gemini (AI Studio) for generating summaries.
"""

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

                # Format is expected to be direct markdown text now based on updated prompt
                return {
                    "summary_text": content,
                    "source_language": source_language,
                    "error": None,
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
        # Truncate transcript if too long
        max_chars = 30000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

        return f"""
You are an expert content analyst. Your task is to analyze the provided YouTube video transcript and extract the most valuable information.
Video Title: "{title}"

CRITICAL INSTRUCTIONS:
1. ONLY use the information provided in the <transcript> tags below. Do not hallucinate or add outside knowledge.
2. You MUST format your response exactly according to the <output_template> provided.

<output_template>
### 📝 Brief Summary
[Write 2-3 sentences summarizing the core topic.]

### 💡 Key Highlights
* **[Concept]**: [Brief explanation]
(Provide exactly 5 highlights)

### 🚀 Actionable Takeaways  
* [Actionable step 1]
* [Actionable step 2]
* [Actionable step 3]
</output_template>

<transcript>
{transcript}
</transcript>
"""

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

        return f"""
You are an expert content analyst. Your task is to analyze the provided YouTube video transcript and extract the most valuable information.
Video Title: "{title}"

IMPORTANT: The transcript is in {lang_name}. Please:
- Analyze the content accurately in the original language
- Translate key terms, names, and important phrases to English in your summary
- Ensure the brief and takeaways are written in English

CRITICAL INSTRUCTIONS:
1. ONLY use the information provided in the <transcript> tags below. Do not hallucinate or add outside knowledge.
2. You MUST format your response exactly according to the <output_template> provided.

<output_template>
### 📝 Brief Summary
[Write 2-3 sentences summarizing the core topic.]

### 💡 Key Highlights
* **[Concept]**: [Brief explanation]
(Provide exactly 5 highlights)

### 🚀 Actionable Takeaways  
* [Actionable step 1]
* [Actionable step 2]
* [Actionable step 3]
</output_template>

<transcript>
{transcript}
</transcript>
"""
