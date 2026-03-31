"""
Audio transcription using Groq Whisper API (primary, /d command).
Google Gemini multimodal API (fallback, /dl command) — handles STT + language
detection + translation in a single API call.

Excludes Persian/Farsi from transcription.
Handles large files by extracting audio first.
"""
import json
import os
import subprocess
import tempfile
import groq
from config import (
    GROQ_API_KEY, TRANSCRIPTION_MODEL,
    GEMINI_API_KEY, GOOGLE_AI_MODEL,
)
from typing import Optional

try:
    from google import genai as google_genai
    from google.genai import types as google_types
    _GOOGLE_AI_AVAILABLE = True
except ImportError:
    _GOOGLE_AI_AVAILABLE = False


class Transcriber:
    """Handles audio transcription using Groq Whisper API (primary) or
    Google Gemini (fallback via /dl command)."""

    # Language name mapping for display
    LANGUAGE_NAMES = {
        'bg': 'Bulgarian',
        'en': 'English',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'uk': 'Ukrainian',
        'pl': 'Polish',
        'tr': 'Turkish',
        'ar': 'Arabic',
        'zh': 'Chinese',
        'ja': 'Japanese',
        'ko': 'Korean',
        'hi': 'Hindi',
        'fa': 'Persian',
        'other': 'Unknown'
    }

    # Languages to EXCLUDE from transcription
    EXCLUDED_LANGUAGES = {'fa', 'persian', 'farsi'}

    # Groq file size limit (25MB)
    MAX_FILE_SIZE = 25 * 1024 * 1024

    def __init__(self):
        self.client = groq.Groq(api_key=GROQ_API_KEY)
        self.model = TRANSCRIPTION_MODEL

    def extract_audio(self, video_path: str) -> str:
        """
        Extract audio from video file and compress it.
        Returns path to the audio file.
        """
        audio_dir = tempfile.mkdtemp(prefix="audio_")
        audio_path = os.path.join(audio_dir, "audio.mp3")

        # Extract audio with compression (96kbps mp3 - good quality, small size)
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn',  # No video
            '-acodec', 'libmp3lame',
            '-ab', '96k',
            '-ar', '16000',  # 16kHz sample rate (good for speech)
            '-y',  # Overwrite
            audio_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return audio_path
        except subprocess.TimeoutExpired:
            raise Exception("Audio extraction timed out")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to extract audio: {e.stderr.decode() if e.stderr else str(e)}")
        except FileNotFoundError:
            raise Exception("ffmpeg not found. Please install ffmpeg: sudo apt install ffmpeg")

    # ── Google Gemini (combined STT + translation) ─────────────────────────────

    def _transcribe_with_google(self, audio_path: str) -> dict:
        """
        Upload audio to Google Gemini and get transcript + translation in ONE call.

        Returns the standard transcription dict PLUS:
          - 'google_translation': str | None  (English translation, or None if already English/Persian)
          - 'google_translation_handled': True  (signals that translation is already done)
        """
        if not _GOOGLE_AI_AVAILABLE:
            return {
                'text': '', 'detected_language': None, 'language_name': None,
                'skipped': False, 'auto_detected': True,
                'google_translation': None, 'google_translation_handled': False,
                'error': 'google-genai library not installed. Run: pip install google-genai'
            }

        if not GEMINI_API_KEY:
            return {
                'text': '', 'detected_language': None, 'language_name': None,
                'skipped': False, 'auto_detected': True,
                'google_translation': None, 'google_translation_handled': False,
                'error': 'GEMINI_API_KEY not configured in .env'
            }

        client = google_genai.Client(api_key=GEMINI_API_KEY)
        uploaded_file = None

        try:
            # Determine MIME type from extension
            ext = audio_path.rsplit('.', 1)[-1].lower() if '.' in audio_path else 'mp3'
            mime_map = {
                'mp3': 'audio/mpeg', 'mp4': 'video/mp4', 'wav': 'audio/wav',
                'm4a': 'audio/mp4', 'webm': 'audio/webm', 'ogg': 'audio/ogg',
                'flac': 'audio/flac', 'aac': 'audio/aac', 'mpeg': 'audio/mpeg',
                'mpga': 'audio/mpeg',
            }
            mime_type = mime_map.get(ext, 'audio/mpeg')

            # Upload audio file to Google File API
            uploaded_file = client.files.upload(
                file=audio_path,
                config=google_types.UploadFileConfig(mime_type=mime_type)
            )

            # Wait for file processing (usually instant for audio)
            import time
            max_wait = 30
            waited = 0
            while waited < max_wait:
                state = str(getattr(uploaded_file, 'state', '')).upper()
                if 'PROCESSING' not in state:
                    break
                time.sleep(2)
                waited += 2
                uploaded_file = client.files.get(name=uploaded_file.name)

            # Single prompt: detect language + transcribe + translate (all in one)
            prompt = (
                "Listen to this audio carefully and return a JSON object with EXACTLY these fields:\n"
                "{\n"
                '  "language_code": "ISO 639-1 code (e.g. en, fa, ar, es, ru, bg, tr)",\n'
                '  "language_name": "Full language name in English",\n'
                '  "transcript": "Full verbatim transcript of the speech in the original language",\n'
                '  "translation": "Complete English translation of the transcript. '
                'If the speech is already in English, copy the transcript here as-is. '
                'If the speech is in Persian/Farsi, set this to an empty string."\n'
                "}\n"
                "Rules:\n"
                "- ALWAYS populate the translation field (never return null)\n"
                "- If speech is Persian/Farsi: set language_code to \"fa\", set transcript to \"\", set translation to \"\"\n"
                "- If speech is English: set translation to be the same as the transcript\n"
                "- For all other languages: provide a full, natural English translation\n"
                "- Return ONLY the JSON object. No markdown fences, no extra text."
            )

            response = client.models.generate_content(
                model=GOOGLE_AI_MODEL,
                contents=[uploaded_file, prompt],
                config=google_types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.1,
                )
            )

            data = json.loads(response.text)
            lang_code = (data.get('language_code') or '').lower().strip()
            lang_name = (
                data.get('language_name')
                or self.LANGUAGE_NAMES.get(lang_code, lang_code.title())
            )
            transcript = (data.get('transcript') or '').strip()
            raw_translation = (data.get('translation') or '').strip()

            is_excluded = lang_code in self.EXCLUDED_LANGUAGES
            is_english = lang_code in ('en', 'eng', 'english')

            # Determine final translation value:
            # - English: no translation needed (translation == transcript)
            # - Persian: skip entirely
            # - Other: use what Gemini returned; if empty, mark as needing fallback
            if is_excluded or is_english:
                translation = None
            else:
                translation = raw_translation if raw_translation else None

            return {
                'text': transcript,
                'detected_language': lang_code,
                'language_name': lang_name,
                'skipped': is_excluded,
                'auto_detected': True,
                'google_translation': translation,
                'google_translation_handled': True,
                'error': (
                    None if not is_excluded
                    else "Persian language is not supported for transcription"
                )
            }

        except Exception as e:
            return {
                'text': '', 'detected_language': None, 'language_name': None,
                'skipped': False, 'auto_detected': True,
                'google_translation': None, 'google_translation_handled': False,
                'error': f"Google AI transcription error: {str(e)}"
            }
        finally:
            # Always clean up the uploaded file from Google's servers
            if uploaded_file:
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

    # ── Main transcription entry point ─────────────────────────────────────────

    def transcribe_audio(self, audio_file_path: str, force_language: Optional[str] = None, use_local_ai: bool = False) -> dict:
        """
        Transcribe an audio/video file.

        - use_local_ai=False (default): uses Groq Whisper (/d command)
        - use_local_ai=True: uses Google Gemini (/dl command)
          Returns extra keys: 'google_translation', 'google_translation_handled'

        Args:
            audio_file_path: Path to the audio/video file
            force_language:  Optional language code to force (Groq path only)
            use_local_ai:    If True, use Google Gemini instead of Groq

        Returns:
            dict with transcription results
        """
        if not os.path.exists(audio_file_path):
            return {
                'text': '',
                'detected_language': None,
                'language_name': None,
                'skipped': False,
                'error': f"File not found: {audio_file_path}"
            }

        file_size = os.path.getsize(audio_file_path)

        # ── Google Gemini path (/dl command) ───────────────────────────────────
        if use_local_ai:
            # Extract & compress audio first (reduces upload size / latency)
            temp_audio_path = None
            audio_to_transcribe = audio_file_path

            if file_size > self.MAX_FILE_SIZE:
                try:
                    temp_audio_path = self.extract_audio(audio_file_path)
                    audio_to_transcribe = temp_audio_path
                except Exception as e:
                    return {
                        'text': '', 'detected_language': None, 'language_name': None,
                        'skipped': False, 'auto_detected': True,
                        'google_translation': None, 'google_translation_handled': False,
                        'error': f"Failed to process large file: {str(e)}"
                    }

            try:
                return self._transcribe_with_google(audio_to_transcribe)
            finally:
                if temp_audio_path and os.path.exists(temp_audio_path):
                    try:
                        os.remove(temp_audio_path)
                        os.rmdir(os.path.dirname(temp_audio_path))
                    except Exception:
                        pass

        # ── Groq Whisper path (/d command, untouched) ──────────────────────────
        # Validate file extension
        valid_extensions = ['mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm', 'ogg', 'flac']
        ext = audio_file_path.rsplit('.', 1)[-1].lower() if '.' in audio_file_path else ''

        if ext not in valid_extensions and not ext:
            return {
                'text': '',
                'detected_language': None,
                'language_name': None,
                'skipped': False,
                'error': f"Unsupported audio format: .{ext if ext else 'unknown'}. Supported: {', '.join(valid_extensions)}"
            }

        # If file is too large, extract and compress audio
        audio_to_transcribe = audio_file_path
        temp_audio_path = None

        if file_size > self.MAX_FILE_SIZE:
            try:
                temp_audio_path = self.extract_audio(audio_file_path)
                audio_to_transcribe = temp_audio_path
                file_size = os.path.getsize(audio_to_transcribe)
            except Exception as e:
                return {
                    'text': '',
                    'detected_language': None,
                    'language_name': None,
                    'skipped': False,
                    'error': f"Failed to process large file: {str(e)}"
                }

        try:
            with open(audio_to_transcribe, 'rb') as audio_file:
                # Build transcription parameters for Groq
                params: dict = {
                    'model': self.model,
                    'response_format': 'verbose_json',
                }

                if force_language:
                    params['language'] = force_language

                # Perform transcription
                response = self.client.audio.transcriptions.create(
                    file=(os.path.basename(audio_to_transcribe), audio_file),
                    **params
                )

                # Extract transcript
                transcript = response.text.strip() if hasattr(response, 'text') else str(response).strip()

                # Get detected language
                detected_lang = None
                if hasattr(response, 'language') and response.language:
                    detected_lang = response.language
                elif force_language:
                    detected_lang = force_language

                # Get language name
                lang_name = self.LANGUAGE_NAMES.get(detected_lang, detected_lang) if detected_lang else 'Unknown'

                # Check if excluded
                is_excluded = detected_lang and detected_lang.lower() in self.EXCLUDED_LANGUAGES

                return {
                    'text': transcript if not is_excluded else '',
                    'detected_language': detected_lang,
                    'language_name': lang_name,
                    'skipped': is_excluded,
                    'auto_detected': not bool(force_language),
                    'error': None if not is_excluded else "Persian language is not supported for transcription"
                }

        except groq.BadRequestError as e:
            error_msg = str(e)
            if 'too_large' in error_msg.lower() or '413' in error_msg:
                return {
                    'text': '',
                    'detected_language': None,
                    'language_name': None,
                    'skipped': False,
                    'error': "Audio file is too large for transcription. Please use a shorter video."
                }
            return {
                'text': '',
                'detected_language': None,
                'language_name': None,
                'skipped': False,
                'error': f"Bad request: {str(e)}"
            }
        except groq.RateLimitError:
            return {
                'text': '',
                'detected_language': None,
                'language_name': None,
                'skipped': False,
                'error': "Rate limit exceeded. Please try again later."
            }
        except Exception as e:
            return {
                'text': '',
                'detected_language': None,
                'language_name': None,
                'skipped': False,
                'error': f"Transcription error: {str(e)}"
            }
        finally:
            # Cleanup temp audio
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    os.rmdir(os.path.dirname(temp_audio_path))
                except Exception:
                    pass

    def transcribe_video(self, video_file_path: str, force_language: Optional[str] = None, use_local_ai: bool = False) -> dict:
        """
        Convenience method for transcribing video files.
        Extracts audio first if video is too large.
        """
        return self.transcribe_audio(video_file_path, force_language, use_local_ai)


def transcribe_file(file_path: str, force_language: Optional[str] = None, use_local_ai: bool = False) -> dict:
    """Quick helper function for transcribing a file."""
    transcriber = Transcriber()
    return transcriber.transcribe_video(file_path, force_language, use_local_ai)
