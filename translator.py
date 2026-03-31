"""
Language detection and translation using Groq LLM API (primary, /d command).
Google AI Studio OpenAI-compatible API (fallback, /dl command).
Handles translation of any non-English language to English.
"""
import groq
import openai
from config import (
    GROQ_API_KEY, TRANSLATION_MODEL,
    GEMINI_API_KEY, GOOGLE_AI_MODEL,
)

from typing import Optional


class Translator:
    """Handles language detection and translation using Groq LLM."""
    
    # English language codes
    ENGLISH_CODES = {'en', 'eng', 'english'}
    
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
                'language': None,
                'language_name': None,
                'confidence': 0,
                'error': "No text provided for language detection"
            }
        
        # Check for Cyrillic characters (likely Bulgarian, Russian, Ukrainian, etc.)
        cyrillic_pattern = any('\u0400' <= c <= '\u04FF' for c in text)
        
        try:
            import langdetect
            
            # Predict language probabilities
            langs = langdetect.detect_langs(text)
            if not langs:
                raise ValueError("No languages detected")
                
            best_match = langs[0]
            lang_code = best_match.lang
            confidence = best_match.prob
            
            # Map standard codes to readable names (Whisper standard maps mostly)
            LANGUAGE_NAMES = {
                'bg': 'Bulgarian', 'en': 'English', 'es': 'Spanish', 'fr': 'French',
                'de': 'German', 'it': 'Italian', 'pt': 'Portuguese', 'ru': 'Russian',
                'uk': 'Ukrainian', 'pl': 'Polish', 'tr': 'Turkish', 'ar': 'Arabic',
                'zh-cn': 'Chinese', 'zh-tw': 'Chinese', 'ko': 'Korean', 'hi': 'Hindi', 
                'fa': 'Persian', 'ja': 'Japanese'
            }
            
            lang_name = LANGUAGE_NAMES.get(lang_code, lang_code.title())
            
            return {
                'language': lang_code,
                'language_name': lang_name,
                'confidence': confidence,
                'error': None
            }
                
        except ImportError:
            return {
                'language': None,
                'language_name': None,
                'confidence': 0,
                'error': "langdetect library is missing. Run pip install langdetect."
            }
        except Exception as e:
            if cyrillic_pattern:
                return {'language': 'ru', 'language_name': 'Russian', 'confidence': 0.5, 'error': None}
            return {
                'language': 'other',
                'language_name': 'Other',
                'confidence': 0,
                'error': f"Language detection error: {str(e)}"
            }
    
    def _translate_with_google(self, text: str, source_language: str) -> dict:
        """
        Translate text to English using Google AI Studio (OpenAI-compatible endpoint).
        Used by /dl command when use_local_ai=True.
        """
        if not GEMINI_API_KEY:
            return {
                'translation': '',
                'error': 'GEMINI_API_KEY not configured in .env'
            }

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
                    {"role": "system", "content": f"You are a professional translator. Translate {source_language} to English accurately."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            translation = response.choices[0].message.content.strip()
            return {
                'translation': translation,
                'error': None
            }
        except Exception as e:
            return {
                'translation': '',
                'error': f"Google AI translation error: {str(e)}"
            }

    def translate_to_english(self, text: str, source_language: str = "unknown", use_local_ai: bool = False) -> dict:
        """
        Translate text to English.
        
        Args:
            text: Text to translate
            source_language: Name of the source language (for better translation)
        
        Returns:
            dict with 'translation', 'error'
        """
        if not text or not text.strip():
            return {
                'translation': '',
                'error': 'No text provided for translation'
            }
        
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
                return self._translate_with_google(text, source_language)
            else:
                # /d command — use Groq LLM (unchanged)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": f"You are a professional translator. Translate {source_language} to English accurately."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=2000
                )
            
            translation = response.choices[0].message.content.strip()
            
            return {
                'translation': translation,
                'error': None
            }
            
        except groq.RateLimitError:
            return {
                'translation': '',
                'error': "Rate limit exceeded. Please try again later."
            }
        except Exception as e:
            return {
                'translation': '',
                'error': f"Translation error: {str(e)}"
            }
    
    def process_transcript(self, transcript: str, hint_language: Optional[str] = None, use_local_ai: bool = False) -> dict:
        """
        Process a transcript: detect language and translate to English if needed.
        
        Args:
            transcript: The transcribed text
            hint_language: Optional language code from Whisper
        
        Returns:
            dict with all relevant information
        """
        result = {
            'original_transcript': transcript,
            'detected_language': None,
            'detected_language_name': None,
            'is_english': False,
            'is_persian': False,
            'english_translation': None,
            'error': None
        }
        
        if not transcript or not transcript.strip():
            result['error'] = 'Empty transcript'
            return result
        
        # Use hint from Whisper if available
        if hint_language:
            result['detected_language'] = hint_language.lower()
            result['detected_language_name'] = hint_language.title()
            result['is_english'] = hint_language.lower() in self.ENGLISH_CODES
            result['is_persian'] = hint_language.lower() in ('fa', 'fas', 'per', 'persian')
        else:
            # Detect language
            detection = self.detect_language(transcript, use_local_ai)
            result['detected_language'] = detection['language']
            result['detected_language_name'] = detection['language_name']
            result['is_english'] = detection['language'] in self.ENGLISH_CODES
            result['is_persian'] = detection['language'] == 'fa'
            if detection['error']:
                result['error'] = detection['error']
        
        # If not English and not Persian, translate to English
        if not result['is_english'] and not result['is_persian']:
            translation_result = self.translate_to_english(
                transcript, 
                str(result['detected_language_name'] or "unknown"),
                use_local_ai
            )
            result['english_translation'] = translation_result['translation']
            if translation_result['error']:
                result['error'] = translation_result['error']
        else:
            # English - no translation needed
            result['english_translation'] = None
        
        return result


def detect_and_translate(transcript: str, hint_language: Optional[str] = None, use_local_ai: bool = False) -> dict:
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
