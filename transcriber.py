"""
Audio transcription using Groq Whisper API.
Extracts speech from video files and returns text transcript.
Auto-detects language automatically if not specified.
Excludes Persian/Farsi from transcription.
Handles large files by extracting audio first.
"""
import os
import subprocess
import tempfile
import groq
from config import GROQ_API_KEY, TRANSCRIPTION_MODEL
from typing import Optional


class Transcriber:
    """Handles audio transcription using Groq Whisper API."""
    
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
    
    def transcribe_audio(self, audio_file_path: str, force_language: Optional[str] = None) -> dict:
        """
        Transcribe an audio/video file using Groq Whisper.
        
        Args:
            audio_file_path: Path to the audio/video file
            force_language: Optional language code to force
        
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
        
        # Check file size
        file_size = os.path.getsize(audio_file_path)
        
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
                # Build transcription parameters
                params = {
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
    
    def transcribe_video(self, video_file_path: str, force_language: Optional[str] = None) -> dict:
        """
        Convenience method for transcribing video files.
        Extracts audio first if video is too large.
        """
        return self.transcribe_audio(video_file_path, force_language)


def transcribe_file(file_path: str, force_language: Optional[str] = None) -> dict:
    """Quick helper function for transcribing a file."""
    transcriber = Transcriber()
    return transcriber.transcribe_video(file_path, force_language)
