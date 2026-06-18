"""
Language detection and translation using Groq LLM API (primary, /d command).
Google AI Studio OpenAI-compatible API (fallback, /dl command).
Handles translation of any non-English language to English.
"""

import groq
import openai
from config import (
    GROQ_API_KEY,
    TRANSLATION_MODEL,
    GEMINI_API_KEY,
    GOOGLE_AI_MODEL,
)

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from cache import AICache


class Translator:
    """Handles language detection and translation using Groq LLM."""

    # English language codes
    ENGLISH_CODES = {"en", "eng", "english"}

    def __init__(self):
        self.client = groq.Groq(api_key=GROQ_API_KEY)
        self.model = TRANSLATION_MODEL
        # Google AI Studio client (OpenAI-compatible endpoint)
        self._google_client = openai.OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=GEMINI_API_KEY or "unset",
        )

    def detect_language(self, text: str, use_local_ai: bool = False) -> dict:
        """
        Detect the language of the given text.
        Uses fast local 'langdetect' library (0.01 seconds) instead of a slow LLM call.

        Args:
            text: The text to analyze
            use_local_ai: Ignored (now always local and instant)

        Returns:
            dict with 'language' (ISO code), 'language_name', 'confidence', 'error'
        """
        if not text or not text.strip():
            return {
                "language": None,
                "language_name": None,
                "confidence": 0,
                "error": "No text provided for language detection",
            }

        # Check for Cyrillic characters (likely Bulgarian, Russian, Ukrainian, etc.)
        cyrillic_pattern = any("\u0400" <= c <= "\u04ff" for c in text)

        try:
            import langdetect  # type: ignore[import-not-found]

            # Predict language probabilities
            langs = langdetect.detect_langs(text)
            if not langs:
                raise ValueError("No languages detected")

            best_match = langs[0]
            lang_code = best_match.lang
            confidence = best_match.prob

            # Map standard codes to readable names (Whisper standard maps mostly)
            LANGUAGE_NAMES = {
                "bg": "Bulgarian",
                "en": "English",
                "es": "Spanish",
                "fr": "French",
                "de": "German",
                "it": "Italian",
                "pt": "Portuguese",
                "ru": "Russian",
                "uk": "Ukrainian",
                "pl": "Polish",
                "tr": "Turkish",
                "ar": "Arabic",
                "zh-cn": "Chinese",
                "zh-tw": "Chinese",
                "ko": "Korean",
                "hi": "Hindi",
                "fa": "Persian",
                "ja": "Japanese",
            }

            lang_name = LANGUAGE_NAMES.get(lang_code, lang_code.title())

            return {
                "language": lang_code,
                "language_name": lang_name,
                "confidence": confidence,
                "error": None,
            }

        except ImportError:
            return {
                "language": None,
                "language_name": None,
                "confidence": 0,
                "error": "langdetect library is missing. Run pip install langdetect.",
            }
        except Exception as e:
            if cyrillic_pattern:
                return {
                    "language": "ru",
                    "language_name": "Russian",
                    "confidence": 0.5,
                    "error": None,
                }
            return {
                "language": "other",
                "language_name": "Other",
                "confidence": 0,
                "error": f"Language detection error: {str(e)}",
            }

    def _translate_with_google(self, text: str, source_language: str) -> dict:
        """
        Translate text to English using Google AI Studio (OpenAI-compatible endpoint).
        Used by /dl command when use_local_ai=True.
        """
        if not GEMINI_API_KEY:
            return {"translation": "", "error": "GEMINI_API_KEY not configured in .env"}

        prompt = f"""Translate the following text to English.
Source language: {source_language}

Maintain the meaning, tone, and style of the original.
If there are names, keep them as is.
If there are phrases that don't translate directly, provide a natural English equivalent.

Text to translate:
\"\"\"{text}\"\"\"

Provide ONLY the English translation, nothing else."""

        try:
            response = self._google_client.chat.completions.create(
                model=GOOGLE_AI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a professional translator. Translate {source_language} to English accurately.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            translation = response.choices[0].message.content.strip()
            return {"translation": translation, "error": None}
        except Exception as e:
            return {
                "translation": "",
                "error": f"Google AI translation error: {str(e)}",
            }

    def translate_to_english(
        self,
        text: str,
        source_language: str = "unknown",
        use_local_ai: bool = False,
        ai_cache: Optional["AICache"] = None,
    ) -> dict:
        """
        Translate text to English.

        Args:
            text: Text to translate
            source_language: Name of the source language (for better translation)
            use_local_ai: If True, use Google AI Studio instead of Groq
            ai_cache: Optional AICache instance for caching results

        Returns:
            dict with 'translation', 'error'
        """
        if not text or not text.strip():
            return {"translation": "", "error": "No text provided for translation"}

        # ── Cache check ────────────────────────────────────────────────────────
        cache_key = None
        if ai_cache is not None:
            from cache import make_text_hash

            text_hash = make_text_hash(text)
            cache_key = f"translation:{text_hash}:{source_language.lower()}"
            cached = ai_cache.get(cache_key)
            if cached is not None:
                import logging

                logging.getLogger(__name__).info(
                    f"Cache HIT (translation): {cache_key}"
                )
                return cached

        prompt = f"""Translate the following text to English.
Source language: {source_language}

Maintain the meaning, tone, and style of the original.
If there are names, keep them as is.
If there are phrases that don't translate directly, provide a natural English equivalent.

Text to translate:
\"\"\"{text}\"\"\"

Provide ONLY the English translation, nothing else."""

        try:
            if use_local_ai:
                # /dl command — use Google AI Studio
                result = self._translate_with_google(text, source_language)
            else:
                # /d command — use Groq LLM (unchanged)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": f"You are a professional translator. Translate {source_language} to English accurately.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                )
                translation = response.choices[0].message.content.strip()
                result = {"translation": translation, "error": None}

            # ── Store in cache ────────────────────────────────────────────────
            if (
                ai_cache is not None
                and cache_key
                and result.get("translation")
                and not result.get("error")
            ):
                ai_cache.set(cache_key, result)

            return result

        except groq.RateLimitError:
            return {
                "translation": "",
                "error": "Rate limit exceeded. Please try again later.",
            }
        except Exception as e:
            return {"translation": "", "error": f"Translation error: {str(e)}"}

    def process_transcript(
        self,
        transcript: str,
        hint_language: Optional[str] = None,
        use_local_ai: bool = False,
        ai_cache: Optional["AICache"] = None,
    ) -> dict:
        """
        Process a transcript: detect language and translate to English if needed.

        Args:
            transcript: The transcribed text
            hint_language: Optional language code from Whisper
            use_local_ai: If True, use Google AI Studio for translation
            ai_cache: Optional AICache instance for caching results

        Returns:
            dict with all relevant information
        """
        result = {
            "original_transcript": transcript,
            "detected_language": None,
            "detected_language_name": None,
            "is_english": False,
            "is_persian": False,
            "english_translation": None,
            "error": None,
        }

        if not transcript or not transcript.strip():
            result["error"] = "Empty transcript"
            return result

        # Use hint from Whisper if available
        if hint_language:
            result["detected_language"] = hint_language.lower()
            result["detected_language_name"] = hint_language.title()
            result["is_english"] = hint_language.lower() in self.ENGLISH_CODES
            result["is_persian"] = hint_language.lower() in (
                "fa",
                "fas",
                "per",
                "persian",
            )
        else:
            # Detect language
            detection = self.detect_language(transcript, use_local_ai)
            result["detected_language"] = detection["language"]
            result["detected_language_name"] = detection["language_name"]
            result["is_english"] = detection["language"] in self.ENGLISH_CODES
            result["is_persian"] = detection["language"] == "fa"
            if detection["error"]:
                result["error"] = detection["error"]

        # If not English and not Persian, translate to English
        if not result["is_english"] and not result["is_persian"]:
            translation_result = self.translate_to_english(
                transcript,
                str(result["detected_language_name"] or "unknown"),
                use_local_ai,
                ai_cache=ai_cache,
            )
            result["english_translation"] = translation_result["translation"]
            if translation_result["error"]:
                result["error"] = translation_result["error"]
        else:
            # English - no translation needed
            result["english_translation"] = None

        return result


def detect_and_translate(
    transcript: str, hint_language: Optional[str] = None, use_local_ai: bool = False
) -> dict:
    """
    Quick helper function for detecting language and translating.

    Args:
        transcript: The text to process
        hint_language: Optional language hint
        use_local_ai: Whether to use local AI fallback logic

    Returns:
        dict with processing results
    """
    translator = Translator()
    return translator.process_transcript(transcript, hint_language, use_local_ai)


def generate_hashtags(
    text: str,
    target_language: str = "fa",
    count: int = 4,
) -> list[str]:
    """Generate concise hashtags for a piece of text via Groq Llama.

    Used to make Telegram messages searchable. Returns a list of hashtag
    strings (each starts with '#'). Empty list on any failure — never raises.
    """
    import json as _json
    import logging
    import re

    log = logging.getLogger(__name__)
    text = (text or "").strip()
    if not text or not GROQ_API_KEY:
        return []

    snippet = text[:2000]
    lang_name = {
        "fa": "Persian", "en": "English", "ar": "Arabic", "tr": "Turkish",
        "de": "German", "fr": "French", "es": "Spanish",
    }.get(target_language.lower(), target_language)

    prompt = (
        f"Generate exactly {count} concise, relevant hashtags in {lang_name} "
        "for the following content. The hashtags will be used in a Telegram "
        "channel to make content searchable.\n\n"
        "Rules:\n"
        f"- Each hashtag is one token written in {lang_name} script "
        "(use underscores for multi-word concepts, no spaces).\n"
        "- Start each with '#'.\n"
        "- Focus on key topics, named entities, themes — NOT generic words.\n"
        "- Return ONLY a JSON array of strings. No prose, no markdown.\n\n"
        f"Content:\n{snippet}"
    )

    try:
        client = groq.Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=TRANSLATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("hashtag generation failed: %s", exc)
        return []

    # Accept either a bare array or an object with a tags-like key.
    try:
        data = _json.loads(raw)
        if isinstance(data, dict):
            for key in ("hashtags", "tags", "items", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                # Take the first list value if any.
                list_vals = [v for v in data.values() if isinstance(v, list)]
                data = list_vals[0] if list_vals else []
        if not isinstance(data, list):
            return []
    except Exception:
        # Last-ditch: extract anything that looks like a hashtag from raw text.
        return list(dict.fromkeys(re.findall(r"#\S+", raw)))[:count]

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        tag = tag.replace(" ", "_")
        if tag not in seen:
            seen.add(tag)
            cleaned.append(tag)
        if len(cleaned) >= count:
            break
    return cleaned
