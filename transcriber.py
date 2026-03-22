"""
Audio transcription using Groq Whisper API.
Extracts speech from video files and returns text transcript.
"""
import os
import groq
from config import GROQ_API_KEY, TRANSCRIPTION_MODEL


class Transcriber:
    """Handles audio transcription using Groq Whisper API."""
    
    def __init__(self):
        self.client = groq.Groq(api_key=GROQ_API_KEY)
        self.model = TRANSCRIPTION_MODEL
    
    def transcribe_audio(self, audio_file_path: str, language: str = None) -> dict:
        """
        Transcribe an audio/video file using Groq Whisper.
        
        Args:
            audio_file_path: Path to the audio/video file
            language: Optional language hint (e.g., 'bg' for Bulgarian, 'en' for English)
        
        Returns:
            dict with 'text' (transcript) and 'language' (detected/used language)
        """
        if not os.path.exists(audio_file_path):
            return {
                'text': '',
                'language': None,
                'error': f"File not found: {audio_file_path}"
            }
        
        # Validate file extension
        valid_extensions = ['mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm', 'ogg', 'flac']
        ext = audio_file_path.rsplit('.', 1)[-1].lower() if '.' in audio_file_path else ''
        
        if ext not in valid_extensions:
            return {
                'text': '',
                'language': None,
                'error': f"Unsupported audio format: .{ext}. Supported: {', '.join(valid_extensions)}"
            }
        
        try:
            with open(audio_file_path, 'rb') as audio_file:
                # Build transcription parameters
                params = {
                    'model': self.model,
                    'response_format': 'verbose_json',
                }
                
                # Add language hint if provided
                if language:
                    params['language'] = language
                
                # Perform transcription
                response = self.client.audio.transcriptions.create(
                    file=(os.path.basename(audio_file_path), audio_file),
                    **params
                )
                
                # Extract text and detected language
                transcript = response.text.strip() if hasattr(response, 'text') else str(response).strip()
                detected_lang = None
                
                # Try to get language from response
                if hasattr(response, 'language'):
                    detected_lang = response.language
                elif language:
                    detected_lang = language
                
                return {
                    'text': transcript,
                    'language': detected_lang,
                    'error': None
                }
                
        except groq.BadRequestError as e:
            return {
                'text': '',
                'language': None,
                'error': f"Bad request: {str(e)}"
            }
        except groq.RateLimitError:
            return {
                'text': '',
                'language': None,
                'error': "Rate limit exceeded. Please try again later."
            }
        except Exception as e:
            return {
                'text': '',
                'language': None,
                'error': f"Transcription error: {str(e)}"
            }
    
    def transcribe_video(self, video_file_path: str, language: str = None) -> dict:
        """
        Convenience method for transcribing video files.
        Works the same as transcribe_audio for supported formats.
        """
        return self.transcribe_audio(video_file_path, language)


def transcribe_file(file_path: str, language: str = None) -> dict:
    """
    Quick helper function for transcribing a file.
    
    Args:
        file_path: Path to the audio/video file
        language: Optional language hint
    
    Returns:
        dict with 'text', 'language', and 'error' keys
    """
    transcriber = Transcriber()
    return transcriber.transcribe_video(file_path, language)
